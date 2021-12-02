use diesel::{connection::SimpleConnection, pg::PgConnection};
use diesel::{
    r2d2::{ConnectionManager, PooledConnection},
    Connection,
};
use rand::{seq::SliceRandom, thread_rng};
use std::collections::{BTreeMap, HashMap};
use std::env;
use std::ops::Deref;
use std::str::FromStr;
use std::sync::atomic::AtomicUsize;
use std::sync::Arc;

use massbit::components::store::{EntityType, StoredDynamicDataSource, BLOCK_NUMBER_MAX};
use massbit::data::indexer::schema::IndexerDeploymentEntity;
use massbit::data::query::QueryExecutionError;
use massbit::prelude::*;
use massbit::prelude::{ApiSchema, DeploymentHash, Schema, StoreError};

use crate::block_range::block_number;
use crate::connection_pool::ConnectionPool;
use crate::deployment;
use crate::dynds;
use crate::primary::Site;
use crate::relational::{Layout, LayoutCache};
use massbit::prelude::reqwest::Client;
lazy_static! {
    /// `QUERY_STATS_REFRESH_INTERVAL` is how long statistics that
    /// influence query execution are cached in memory (in seconds) before
    /// they are reloaded from the database. Defaults to 300s (5 minutes).
    static ref STATS_REFRESH_INTERVAL: Duration = {
        env::var("QUERY_STATS_REFRESH_INTERVAL")
        .ok()
        .map(|s| {
            let secs = u64::from_str(&s).unwrap_or_else(|_| {
                panic!("QUERY_STATS_REFRESH_INTERVAL must be a number, but is `{}`", s)
            });
            Duration::from_secs(secs)
        }).unwrap_or(Duration::from_secs(300))
    };

    static ref HASURA_URL: String = env::var("HASURA_URL").unwrap_or(String::from("http://localhost:8080/v1/query"));
}

/// When connected to read replicas, this allows choosing which DB server to use for an operation.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum ReplicaId {
    /// The main server has write and read access.
    Main,

    /// A read replica identified by its index.
    ReadOnly(usize),
}

/// Commonly needed information about a indexer that we cache in
/// `Store.indexer_cache`. Only immutable indexer data can be cached this
/// way as the cache lives for the lifetime of the `Store` object
#[derive(Clone)]
pub(crate) struct IndexerInfo {
    /// The schema as supplied by the user
    pub(crate) input: Arc<Schema>,
    /// The schema we derive from `input` with `graphql::schema::api::api_schema`
    pub(crate) api: Arc<ApiSchema>,
    pub(crate) description: Option<String>,
    pub(crate) repository: Option<String>,
}

pub struct StoreInner {
    logger: Logger,

    conn: ConnectionPool,
    read_only_pools: Vec<ConnectionPool>,

    /// A list of the available replicas set up such that when we run
    /// through the list once, we picked each replica according to its
    /// desired weight. Each replica can appear multiple times in the list
    replica_order: Vec<ReplicaId>,
    /// The current position in `replica_order` so we know which one to
    /// pick next
    conn_round_robin_counter: AtomicUsize,

    /// A cache for the layout metadata for indexers. The Store just
    /// hosts this because it lives long enough, but it is managed from
    /// the entities module
    pub(crate) layout_cache: LayoutCache,
}

/// Storage of the data for individual deployments. Each `DeploymentStore`
/// corresponds to one of the database shards that `IndexerStore` manages.
#[derive(Clone)]
pub struct DeploymentStore(Arc<StoreInner>);

impl CheapClone for DeploymentStore {}

impl Deref for DeploymentStore {
    type Target = StoreInner;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl DeploymentStore {
    pub fn new(
        logger: &Logger,
        pool: ConnectionPool,
        read_only_pools: Vec<ConnectionPool>,
        mut pool_weights: Vec<usize>,
    ) -> Self {
        // Create a list of replicas with repetitions according to the weights
        // and shuffle the resulting list. Any missing weights in the list
        // default to 1
        pool_weights.resize(read_only_pools.len() + 1, 1);
        let mut replica_order: Vec<_> = pool_weights
            .iter()
            .enumerate()
            .map(|(i, weight)| {
                let replica = if i == 0 {
                    ReplicaId::Main
                } else {
                    ReplicaId::ReadOnly(i - 1)
                };
                vec![replica; *weight]
            })
            .flatten()
            .collect();
        let mut rng = thread_rng();
        replica_order.shuffle(&mut rng);

        // Create the store
        let store = StoreInner {
            logger: logger.clone(),
            conn: pool,
            read_only_pools,
            replica_order,
            conn_round_robin_counter: AtomicUsize::new(0),
            layout_cache: LayoutCache::new(*STATS_REFRESH_INTERVAL),
        };
        let store = DeploymentStore(Arc::new(store));
        store
    }

    pub(crate) async fn with_conn<T: Send + 'static>(
        &self,
        f: impl 'static
            + Send
            + FnOnce(
                &PooledConnection<ConnectionManager<PgConnection>>,
                &CancelHandle,
            ) -> Result<T, CancelableError<StoreError>>,
    ) -> Result<T, StoreError> {
        self.conn.with_conn(f).await
    }

    pub(crate) fn create_deployment(
        &self,
        schema: &Schema,
        deployment: IndexerDeploymentEntity,
        site: Arc<Site>,
        replace: bool,
    ) -> Result<(), StoreError> {
        let conn = self.get_conn()?;
        let result = conn.transaction(|| -> Result<_, StoreError> {
            let exists = deployment::exists(&conn, &site)?;

            // Create (or update) the metadata. Update only happens in tests
            if replace || !exists {
                deployment::create_deployment(&conn, &site, deployment, exists, replace)?;
            };

            // Create the schema for the indexer data
            if !exists {
                let query = format!("create schema {}", &site.namespace);
                conn.batch_execute(&query)?;
                let layout = Layout::create_relational_schema(&conn, site.clone(), schema)?;
                Ok(Some(layout))
            } else {
                Ok(None)
            }
        });

        if let Ok(Some(layout)) = result {
            let payload = layout.create_hasura_tracking();
            massbit::spawn(async move {
                Client::new().post(&*HASURA_URL).json(&payload).send().await;
            });
        }

        Ok(())
    }

    /// Return the layout for a deployment. Since constructing a `Layout`
    /// object takes a bit of computation, we cache layout objects that do
    /// not have a pending migration in the Store, i.e., for the lifetime of
    /// the Store. Layout objects with a pending migration can not be
    /// cached for longer than a transaction since they might change
    /// without us knowing
    pub(crate) fn layout(
        &self,
        conn: &PgConnection,
        site: Arc<Site>,
    ) -> Result<Arc<Layout>, StoreError> {
        self.layout_cache.get(conn, site)
    }

    /// Return the layout for a deployment. This might use a database
    /// connection for the lookup and should only be called if the caller
    /// does not have a connection currently. If it does, use `layout`
    pub(crate) fn find_layout(&self, site: Arc<Site>) -> Result<Arc<Layout>, StoreError> {
        if let Some(layout) = self.layout_cache.find(site.as_ref()) {
            return Ok(layout.clone());
        }

        let conn = self.get_conn()?;
        self.layout(&conn, site)
    }

    /// Deprecated. Use `with_conn` instead.
    fn get_conn(&self) -> Result<PooledConnection<ConnectionManager<PgConnection>>, StoreError> {
        self.conn.get_with_timeout_warning(&self.logger)
    }

    pub(crate) fn get(
        &self,
        site: Arc<Site>,
        key: &EntityKey,
    ) -> Result<Option<Entity>, QueryExecutionError> {
        let conn = self.get_conn()?;
        let layout = self.layout(&conn, site)?;

        // We should really have callers pass in a block number; but until
        // that is fully plumbed in, we just use the biggest possible block
        // number so that we will always return the latest version,
        // i.e., the one with an infinite upper bound

        layout
            .find(&conn, &key.entity_type, &key.entity_id, BLOCK_NUMBER_MAX)
            .map_err(|e| {
                QueryExecutionError::ResolveEntityError(
                    key.indexer_id.clone(),
                    key.entity_type.to_string(),
                    key.entity_id.clone(),
                    format!("Invalid entity {}", e),
                )
            })
    }

    pub(crate) fn get_many(
        &self,
        site: Arc<Site>,
        ids_for_type: BTreeMap<&EntityType, Vec<&str>>,
    ) -> Result<BTreeMap<EntityType, Vec<Entity>>, StoreError> {
        if ids_for_type.is_empty() {
            return Ok(BTreeMap::new());
        }
        let conn = self.get_conn()?;
        let layout = self.layout(&conn, site)?;

        layout.find_many(&conn, ids_for_type, BLOCK_NUMBER_MAX)
    }

    pub(crate) async fn load_dynamic_data_sources(
        &self,
        id: DeploymentHash,
    ) -> Result<Vec<StoredDynamicDataSource>, StoreError> {
        self.with_conn(move |conn, _| {
            conn.transaction(|| crate::dynds::load(&conn, id.as_str()))
                .map_err(Into::into)
        })
        .await
    }

    pub(crate) fn transact_block_operations(
        &self,
        site: Arc<Site>,
        block_ptr_to: BlockPtr,
        mods: Vec<EntityModification>,
        data_sources: Vec<StoredDynamicDataSource>,
    ) -> Result<(), StoreError> {
        // All operations should apply only to data or metadata for this indexer
        if mods
            .iter()
            .map(|modification| modification.entity_key())
            .any(|key| key.indexer_id != site.deployment)
        {
            panic!(
                "transact_block_operations must affect only entities \
                 in the indexer or in the indexer of indexers"
            );
        }

        let conn = self.get_conn()?;

        conn.transaction(|| -> Result<_, StoreError> {
            // Make the changes
            let layout = self.layout(&conn, site.clone())?;
            let _ = self.apply_entity_modifications(&conn, layout.as_ref(), mods, &block_ptr_to)?;
            dynds::insert(&conn, &site.deployment, data_sources, &block_ptr_to)?;
            deployment::forward_block_ptr(&conn, &site.deployment, block_ptr_to)?;
            Ok(())
        })?;

        Ok(())
    }

    fn apply_entity_modifications(
        &self,
        conn: &PgConnection,
        layout: &Layout,
        mods: Vec<EntityModification>,
        ptr: &BlockPtr,
    ) -> Result<i32, StoreError> {
        use EntityModification::*;
        let mut count = 0;

        // Group `Insert`s and `Overwrite`s by key, and accumulate `Remove`s.
        let mut inserts = HashMap::new();
        let mut overwrites = HashMap::new();
        let mut removals = HashMap::new();
        for modification in mods.into_iter() {
            match modification {
                Insert { key, data } => {
                    inserts
                        .entry(key.entity_type.clone())
                        .or_insert_with(Vec::new)
                        .push((key, data));
                }
                Overwrite { key, data } => {
                    overwrites
                        .entry(key.entity_type.clone())
                        .or_insert_with(Vec::new)
                        .push((key, data));
                }
                Remove { key } => {
                    removals
                        .entry(key.entity_type.clone())
                        .or_insert_with(Vec::new)
                        .push(key.entity_id);
                }
            }
        }

        // Apply modification groups.
        // Inserts:
        for (entity_type, mut entities) in inserts.into_iter() {
            count += self.insert_entities(&entity_type, &mut entities, conn, layout, ptr)? as i32
        }

        Ok(count)
    }

    fn insert_entities(
        &self,
        entity_type: &EntityType,
        data: &mut [(EntityKey, Entity)],
        conn: &PgConnection,
        layout: &Layout,
        ptr: &BlockPtr,
    ) -> Result<usize, StoreError> {
        layout.insert(conn, entity_type, data, block_number(ptr))
    }

    pub(crate) fn block_ptr(&self, site: &Site) -> Result<Option<BlockPtr>, Error> {
        let conn = self.get_conn()?;
        Self::block_ptr_with_conn(&site.deployment, &conn)
    }

    fn block_ptr_with_conn(
        indexer_id: &DeploymentHash,
        conn: &PgConnection,
    ) -> Result<Option<BlockPtr>, Error> {
        Ok(deployment::block_ptr(&conn, indexer_id)?)
    }
}
