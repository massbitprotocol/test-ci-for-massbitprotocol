use diesel::{PgConnection, RunQueryDsl};
use graph::components::store::{EntityFilter, EntityOrder, EntityRange, WritableStore};
use graph::data::store::Entity;
use lazy_static::lazy_static;
use massbit_common::prelude::structmap::GenericMap;
use std::collections::HashMap;
use std::error::Error;
use std::fmt::Write;
use std::sync::{Arc, Mutex};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio;
use tokio_postgres::NoTls;

lazy_static! {
    static ref COMPONENT_NAME: String = String::from("[Index-Store]");
}

const BATCH_SIZE: usize = 1000;
const PERIOD: u128 = 500; //Period to insert in ms

type ArcVec = Arc<Mutex<Vec<GenericMap>>>;
#[derive(Clone)]
struct TableBuffer {
    data: ArcVec,
    last_store: u128,
}
impl TableBuffer {
    fn new() -> TableBuffer {
        TableBuffer {
            data: Arc::new(Mutex::new(Vec::new())),
            last_store: 0,
        }
    }
    pub fn size(&self) -> usize {
        let buffer = self.data.clone();
        let size = buffer.lock().unwrap().len();
        size
    }
    pub fn elapsed_since_last_flush(&self) -> u128 {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time before Unix epoch");
        now.as_millis() - self.last_store
    }
    fn push(&mut self, record: GenericMap) {
        let arc_buf = self.data.clone();
        let mut buffer = arc_buf.lock().unwrap();
        buffer.push(record);
    }
    pub fn move_buffer(&mut self) -> Vec<GenericMap> {
        let buffer = self.data.clone();
        let mut data = buffer.lock().unwrap();
        let mut res = Vec::with_capacity(data.len());
        res.append(&mut data); //Move records from data to res;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time before Unix epoch");
        self.last_store = now.as_millis();
        res
    }
}

pub trait QueryableStore: Sync + Send {
    fn query(
        &self,
        entity_type: String,
        filter: Option<EntityFilter>,
        order: EntityOrder,
        range: EntityRange,
    ) -> Vec<Entity>;
}
pub trait ToWritableStore {
    fn to_writable_store<'a>(self: Arc<Self>) -> Arc<dyn WritableStore + 'a>
    where
        Self: 'a;
}
pub trait IndexStore: WritableStore + QueryableStore + ToWritableStore {}

impl<T: WritableStore> ToWritableStore for T {
    fn to_writable_store<'a>(self: Arc<Self>) -> Arc<dyn WritableStore + 'a>
    where
        Self: 'a,
    {
        self
    }
}

pub trait Store: QueryableStore + Sync + Send {
    fn save(&mut self, entity_name: String, data: Entity);
    fn get(&mut self, entity_name: String, entity_id: &String) -> Option<Entity>;
    fn flush(&mut self, block_hash: &String, block_number: u64) -> Result<(), Box<dyn Error>>;
}
// impl Store for IndexStore {
//     fn save(&mut self, _entity_name: String, mut _data: GenericMap) {
//         match self.buffer.get_mut(_entity_name.as_str()) {
//             //Create buffer for first call
//             None => {
//                 let mut tab_buf = TableBuffer::new();
//                 tab_buf.push(_data);
//                 self.buffer.insert(_entity_name, tab_buf);
//             }
//             //Put data into buffer then perform flush to db if buffer size exceeds BATCH_SIZE
//             //or elapsed time from last save exceeds PERIOD
//             Some(tab_buf) => {
//                 tab_buf.push(_data);
//                 self.check_and_flush(&_entity_name);
//             }
//         }
//     }
//
//     fn get(&self, entity_name: String, entity_id: String) -> Option<GenericMap> {
//         todo!()
//     }
//
//     fn flush(&mut self, block_hash: &String, block_number: u64) -> Result<(), Box<dyn Error>> {
//         //todo!()
//         log::info!(
//             "Flush block with hash: {} and number: {}",
//             block_hash,
//             block_number
//         );
//         Ok(())
//     }
// }
/*
 * 2021-07-27
 * vuvietai: add dependent insert
 */
// #[derive(Clone)]
// pub struct IndexStore {
//     pub connection_string: String,
//     buffer: HashMap<String, TableBuffer>,
//     entity_dependencies: HashMap<String, Vec<String>>,
// }
// impl IndexStore {
//     pub async fn new(connection: &str) -> IndexStore {
//         //let dependencies = HashMap::new();
//
//         let dependencies = match get_entity_dependencies(connection, "public").await {
//             Ok(res) => res,
//             Err(err) => {
//                 log::error!(
//                     "{} Cannot load relationship from db: {:?}",
//                     &*COMPONENT_NAME,
//                     err
//                 );
//                 HashMap::new()
//             }
//         };
//
//         IndexStore {
//             connection_string: connection.to_string(),
//             buffer: HashMap::new(),
//             entity_dependencies: dependencies,
//         }
//     }
//     fn check_and_flush(&mut self, _entity_name: &String) {
//         if let Some(table_buf) = self.buffer.get(_entity_name.as_str()) {
//             let size = table_buf.size();
//             if size >= BATCH_SIZE || (table_buf.elapsed_since_last_flush() >= PERIOD && size > 0) {
//                 //Todo: move this init connection to new fn.
//                 let con = PgConnection::establish(&self.connection_string)
//                     .expect(&format!("Error connecting to {}", self.connection_string));
//                 let buffer = &mut self.buffer;
//                 //Todo: implement multiple levels of relationship (chain dependencies)
//                 let dependencies = self.entity_dependencies.get(_entity_name.as_str());
//                 match dependencies {
//                     Some(deps) => {
//                         deps.iter().for_each(|reference| {
//                             log::info!(
//                                 "{} Flush reference data into table {}",
//                                 &*COMPONENT_NAME,
//                                 reference.as_str()
//                             );
//                             if let Some(ref_buf) = buffer.get_mut(reference.as_str()) {
//                                 let buf_data = ref_buf.move_buffer();
//                                 flush_entity(reference, &buf_data, &con);
//                             }
//                         });
//                     }
//                     None => {}
//                 };
//                 if let Some(table_buf) = buffer.get_mut(_entity_name.as_str()) {
//                     let buf_data = table_buf.move_buffer();
//                     log::info!(
//                         "{} Flush data into table {}",
//                         &*COMPONENT_NAME,
//                         _entity_name.as_str()
//                     );
//                     flush_entity(_entity_name, &buf_data, &con);
//                 }
//                 /*
//                 con.transaction::<(), DieselError, _>(|| {
//                     match dependencies {
//                         Some(deps) => {
//                             deps.iter().rev().for_each(|reference|{
//                                 log::info!("Flush reference data into table {}", reference.as_str());
//                                 if let Some(ref_buf) = buffer.get_mut(reference.as_str()) {
//                                     let buf_data = ref_buf.move_buffer();
//                                     flush_entity(reference, &buf_data, &con);
//                                 }
//                             });
//                         },
//                         None => {}
//                     };
//                     if let Some(table_buf) = buffer.get_mut(_entity_name.as_str()) {
//                         let buf_data = table_buf.move_buffer();
//                         log::info!("Flush data into table {}", _entity_name.as_str());
//                         flush_entity(_entity_name, &buf_data, &con);
//                     }
//                     Ok(())
//                     // If we want to roll back the transaction, but don't have an
//                     // actual error to return, we can return `RollbackTransaction`.
//                     //Err(DieselError::RollbackTransaction)
//                 });
//                  */
//             }
//         };
//     }
// }
//fn flush_entity(table_name : &String, _buffer : &Vec<GenericMap>, conn: &TransactionalConnection<PgConnection>) -> QueryResult<usize> {
fn flush_entity(table_name: &String, _buffer: &Vec<GenericMap>, conn: &PgConnection) {
    let start = Instant::now();
    if let Some(query) = create_query(table_name, _buffer) {
        match diesel::sql_query(query.as_str()).execute(conn) {
            Ok(res) => {
                log::info!("{} Execute query with result {:?}.", &*COMPONENT_NAME, res);
            }
            Err(err) => {
                log::error!(
                    "{} Error {:?} while insert into table: {:?}.",
                    &*COMPONENT_NAME,
                    err,
                    table_name
                );
            }
        }
        log::info!(
            "{} Insert {:?} records into table {:?} in: {:?} ms.",
            &*COMPONENT_NAME,
            _buffer.len(),
            table_name,
            start.elapsed()
        );
    }
}

///
/// Create Query with format
/// INSERT INTO {entity_name} ({field1}, {field2}, {field3}) VALUES
/// ('strval11',numberval12, numberval13),
/// ('strval21',numberval22, numberval23),
///
fn create_query(_entity_name: &str, buffer: &Vec<GenericMap>) -> Option<String> {
    let mut query = None;
    if buffer.len() > 0 {
        if let Some(_data) = buffer.get(0) {
            let fields: Vec<String> = _data.iter().map(|(k, _)| k.to_string()).collect();
            //Store vector of row's form ('strval11',numberval12, numberval13)
            let row_values: Vec<String> = buffer
                .iter()
                .map(|_data| {
                    let field_values: Vec<String> = _data
                        .iter()
                        .map(|(_, v)| {
                            let mut str_val = String::new();
                            if let Some(r) = v.bool() {
                                write!(str_val, "{}", r).unwrap();
                            } else if let Some(r) = v.f64() {
                                write!(str_val, "{}", r).unwrap();
                            } else if let Some(r) = v.i64() {
                                write!(str_val, "{}", r).unwrap();
                            } else if let Some(r) = v.u64() {
                                write!(str_val, "{}", r).unwrap();
                            } else if let Some(r) = v.string() {
                                write!(str_val, "'{}'", r).unwrap();
                            }
                            str_val
                        })
                        .collect();
                    format!("({})", field_values.join(","))
                })
                .collect();
            query = Some(format!(
                "INSERT INTO {} ({}) VALUES {};",
                _entity_name,
                fields.join(","),
                row_values.join(",")
            ));
        }
    }
    query
}

///
/// Get relationship dependencies from database
/// When flush data into one table, first check and flush data in reference table
///
//Todo: get dependencies from input schema (not from DB)
async fn get_entity_dependencies(
    connection: &str,
    schema: &str,
) -> Result<HashMap<String, Vec<String>>, Box<dyn Error>> {
    //let conn = establish_connection();
    let query = r#"
        SELECT
            pgc.conname as constraint_name,
            kcu.table_name as table_name,
            CASE WHEN (pgc.contype = 'f') THEN kcu.COLUMN_NAME ELSE ccu.COLUMN_NAME END as column_name,
            CASE WHEN (pgc.contype = 'f') THEN ccu.TABLE_NAME ELSE (null) END as reference_table,
            CASE WHEN (pgc.contype = 'f') THEN ccu.COLUMN_NAME ELSE (null) END as reference_col
        FROM
            pg_constraint AS pgc
            JOIN pg_namespace nsp ON nsp.oid = pgc.connamespace
            JOIN pg_class cls ON pgc.conrelid = cls.oid
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = pgc.conname
            LEFT JOIN information_schema.constraint_column_usage ccu ON pgc.conname = ccu.CONSTRAINT_NAME
            AND nsp.nspname = ccu.CONSTRAINT_SCHEMA
        WHERE ccu.table_schema = $1 and pgc.contype = 'f'
    "#;
    let mut dependencies: HashMap<String, Vec<String>> = HashMap::new();
    /*
     * https://docs.rs/tokio-postgres/0.7.2/tokio_postgres/
     * 2021-07-28
     * vuviettai: use tokio_postgres instead of postgres
     */

    //log::info!("Connect to ds with string {}", connection);
    // Connect to the database.
    let (client, connection) = tokio_postgres::connect(connection, NoTls).await?;

    // The connection object performs the actual communication with the database,
    // so spawn it off to run on its own.
    tokio::spawn(async move {
        if let Err(e) = connection.await {
            eprintln!("connection error: {}", e);
        }
    });

    // Now we can execute a simple statement that just returns its parameter.
    //let mut client = Client::connect(connection, NoTls)?;
    let result = &client.query(query, &[&schema]).await?;
    result.iter().for_each(|row| {
        let table_name = row.get::<usize, String>(1);
        let reference = row.get::<usize, String>(3);
        match dependencies.get_mut(table_name.as_str()) {
            None => {
                let mut vec = Vec::new();
                vec.push(reference);
                dependencies.insert(table_name, vec);
            }
            Some(vec) => {
                if !vec.contains(&reference) {
                    vec.push(reference);
                }
            }
        }
    });
    log::info!("{} Found references {:?}", &*COMPONENT_NAME, &dependencies);
    let mut chain_deps: HashMap<String, Vec<String>> = HashMap::default();
    dependencies.iter().for_each(|(key, _)| {
        let vec = create_chain_dependencies(key, &dependencies);
        chain_deps.insert(key.clone(), vec);
    });
    log::info!("{} Chain dependencies {:?}", &*COMPONENT_NAME, &chain_deps);
    Ok(chain_deps)
}
/*
 * Create chain dependencies from db relationship:
 * For example: A depends on B, B depends on C then output A depends on [C,B]
 */
fn create_chain_dependencies(
    table_name: &String,
    dependencies: &HashMap<String, Vec<String>>,
) -> Vec<String> {
    let mut res: Vec<String> = Vec::default();
    //let mut checking: Vec<String> = Vec::default();
    if let Some(dep) = dependencies.get(table_name) {
        dep.iter().for_each(|ref_table| {
            let tmp = create_chain_dependencies(ref_table, dependencies);
            tmp.iter().for_each(|item| {
                if !res.contains(item) {
                    res.push(item.clone());
                }
            });
            res.push(ref_table.clone());
        });
    };
    res
}
/*
 * Create chain dependencies from graphql.schema with cycle dependencies detection:
 * Input empty init cycle_deps
 * For example: A depends on B, B depends on C then output A depends on [C,B]
 * If A -> B; B -> C, C -> A => detect cycle dependency
 */
/*
fn create_chain_dependencies(table_name: &String, dependencies: &HashMap<String, Vec<String>>, cycle_deps: &mut Vec<String>) -> Vec<String> {
    let mut res : Vec<String> = Vec::default();
    let mut checking: Vec<String> = Vec::default();
    if let Some(dep) = dependencies.get(table_name) {
        cycle_deps.push(table_name.clone());
        dep.iter().for_each(|ref_table|{
            if cycle_deps.contains(ref_table) {
                log::error!("{} Cycle dependency detected {}", &*COMPONENT_NAME, ref_table)
                //Todo: stop working here message user to change graphql.schema
            } else {
                let mut tmp = create_chain_dependencies(ref_table, dependencies, cycle_deps);
                tmp.iter().for_each(|item|{
                    if !res.contains(item) {
                        res.push(item.clone());
                    }
                });
            }
            res.push(ref_table.clone());
        });
    };
    res
}
 */
/*
///
/// Collect all dependencies chain start by table_name
///

fn prepare_dependency_lists<'a>(table_name: &'a String, dependencies: &'a HashMap<String, Vec<String>>) -> Vec<&'a String> {
    let mut res : Vec<&String> = Vec::new();
    match dependencies.get(table_name.as_str()) {
        None => {}
        Some(vec) => {
            for ref_table in vec.iter() {
                res.push(ref_table);
                let dep_list = prepare_dependency_lists(ref_table, dependencies);
                dep_list.iter().for_each( |val| {
                    if !res.contains(val) {
                        res.push(val.clone());
                    }
                });
            }
        }
    };
    res
}
*/
