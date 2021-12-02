use massbit::prelude::{
    anyhow::{anyhow, bail, Context, Result},
    info, Logger,
};
use massbit_store_postgres::{Shard as ShardName, PRIMARY_SHARD};

use http::{HeaderMap, Uri};
use serde::{Deserialize, Serialize};
use std::fs::read_to_string;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
use url::Url;

pub struct Opt {
    pub postgres_url: Option<String>,
    pub config: Option<String>,
    // This is only used when we construct a config purely from command
    // line options. When using a configuration file, pool sizes must be
    // set in the configuration file alone
    pub store_connection_pool_size: u32,
    pub postgres_secondary_hosts: Vec<String>,
    pub postgres_host_weights: Vec<usize>,
    pub ethereum_rpc: Vec<String>,
    pub ethereum_ws: Vec<String>,
    pub ethereum_ipc: Vec<String>,
}

impl Default for Opt {
    fn default() -> Self {
        Opt {
            postgres_url: None,
            config: None,
            store_connection_pool_size: 10,
            postgres_secondary_hosts: vec![],
            postgres_host_weights: vec![],
            ethereum_rpc: vec![],
            ethereum_ws: vec![],
            ethereum_ipc: vec![],
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Config {
    #[serde(rename = "store")]
    pub stores: BTreeMap<String, Shard>,
    pub chains: ChainSection,
}

fn validate_name(s: &str) -> Result<()> {
    if s.is_empty() {
        return Err(anyhow!("names must not be empty"));
    }
    if s.len() > 30 {
        return Err(anyhow!(
            "names can be at most 30 characters, but `{}` has {} characters",
            s,
            s.len()
        ));
    }

    if !s
        .chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        return Err(anyhow!(
            "name `{}` is invalid: names can only contain lowercase alphanumeric characters or '-'",
            s
        ));
    }
    Ok(())
}

impl Config {
    /// Check that the config is valid.
    fn validate(&mut self) -> Result<()> {
        if !self.stores.contains_key(PRIMARY_SHARD.as_str()) {
            return Err(anyhow!("missing a primary store"));
        }

        for (key, shard) in self.stores.iter_mut() {
            shard.validate(&key)?;
        }

        self.chains.validate()?;

        Ok(())
    }

    /// Load a configuration file if `opt.config` is set. If not, generate
    /// a config from the command line arguments in `opt`
    pub fn load(logger: &Logger, opt: &Opt) -> Result<Config> {
        if let Some(config) = &opt.config {
            info!(logger, "Reading configuration file `{}`", config);
            let config = read_to_string(config)?;
            let mut config: Config = toml::from_str(&config)?;
            config.validate()?;
            Ok(config)
        } else {
            info!(
                logger,
                "Generating configuration from command line arguments"
            );
            Self::from_opt(opt)
        }
    }

    fn from_opt(opt: &Opt) -> Result<Config> {
        let mut stores = BTreeMap::new();
        let chains = ChainSection::from_opt(opt)?;
        stores.insert(PRIMARY_SHARD.to_string(), Shard::from_opt(opt)?);
        Ok(Config { stores, chains })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Shard {
    pub connection: String,
    #[serde(default = "one")]
    pub weight: usize,
    #[serde(default)]
    pub pool_size: PoolSize,
    #[serde(default)]
    pub replicas: BTreeMap<String, Replica>,
}

impl Shard {
    fn validate(&mut self, name: &str) -> Result<()> {
        ShardName::new(name.to_string()).map_err(|e| anyhow!(e))?;

        self.connection = shellexpand::env(&self.connection)?.into_owned();

        if matches!(self.pool_size, PoolSize::None) {
            return Err(anyhow!("missing pool size definition for shard `{}`", name));
        }

        self.pool_size.validate(&self.connection)?;
        for (name, replica) in self.replicas.iter_mut() {
            validate_name(name).context("illegal replica name")?;
            replica.validate(&self.pool_size)?;
        }

        let no_weight =
            self.weight == 0 && self.replicas.values().all(|replica| replica.weight == 0);
        if no_weight {
            return Err(anyhow!(
                "all weights for shard `{}` are 0; \
                remove explicit weights or set at least one of them to a value bigger than 0",
                name
            ));
        }
        Ok(())
    }

    fn from_opt(opt: &Opt) -> Result<Self> {
        let postgres_url = opt
            .postgres_url
            .as_ref()
            .expect("validation checked that postgres_url is set");
        let pool_size = PoolSize::Fixed(opt.store_connection_pool_size);
        pool_size.validate(&postgres_url)?;
        let mut replicas = BTreeMap::new();
        for (i, host) in opt.postgres_secondary_hosts.iter().enumerate() {
            let replica = Replica {
                connection: replace_host(&postgres_url, &host),
                weight: opt.postgres_host_weights.get(i + 1).cloned().unwrap_or(1),
                pool_size: pool_size.clone(),
            };
            replicas.insert(format!("replica{}", i + 1), replica);
        }
        Ok(Self {
            connection: postgres_url.clone(),
            weight: opt.postgres_host_weights.get(0).cloned().unwrap_or(1),
            pool_size,
            replicas,
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(untagged)]
pub enum PoolSize {
    None,
    Fixed(u32),
}

impl Default for PoolSize {
    fn default() -> Self {
        Self::None
    }
}

impl PoolSize {
    fn validate(&self, connection: &str) -> Result<()> {
        use PoolSize::*;

        let pool_size = match self {
            None => bail!("missing pool size for {}", connection),
            Fixed(s) => *s,
        };

        if pool_size < 2 {
            Err(anyhow!(
                "connection pool size must be at least 2, but is {} for {}",
                pool_size,
                connection
            ))
        } else {
            Ok(())
        }
    }

    pub fn size(&self) -> Result<u32> {
        use PoolSize::*;
        match self {
            None => unreachable!("validation ensures we have a pool size"),
            Fixed(s) => Ok(*s),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Replica {
    pub connection: String,
    #[serde(default = "one")]
    pub weight: usize,
    #[serde(default)]
    pub pool_size: PoolSize,
}

impl Replica {
    fn validate(&mut self, pool_size: &PoolSize) -> Result<()> {
        self.connection = shellexpand::env(&self.connection)?.into_owned();
        if matches!(self.pool_size, PoolSize::None) {
            self.pool_size = pool_size.clone();
        }

        self.pool_size.validate(&self.connection)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ChainSection {
    #[serde(flatten)]
    pub chains: BTreeMap<String, Chain>,
}

impl ChainSection {
    fn validate(&mut self) -> Result<()> {
        for (_, chain) in self.chains.iter_mut() {
            chain.validate()?
        }
        Ok(())
    }

    fn from_opt(opt: &Opt) -> Result<Self> {
        let mut chains = BTreeMap::new();
        Self::parse_networks(&mut chains, Transport::Rpc, &opt.ethereum_rpc)?;
        Self::parse_networks(&mut chains, Transport::Ws, &opt.ethereum_ws)?;
        Self::parse_networks(&mut chains, Transport::Ipc, &opt.ethereum_ipc)?;
        Ok(Self { chains })
    }

    fn parse_networks(
        chains: &mut BTreeMap<String, Chain>,
        transport: Transport,
        args: &Vec<String>,
    ) -> Result<()> {
        for (nr, arg) in args.iter().enumerate() {
            if arg.starts_with("wss://")
                || arg.starts_with("http://")
                || arg.starts_with("https://")
            {
                return Err(anyhow!(
                    "Is your Ethereum node string missing a network name? \
                     Try 'mainnet:' + the Ethereum node URL."
                ));
            } else {
                // Parse string (format is "NETWORK_NAME:NETWORK_CAPABILITIES:URL" OR
                // "NETWORK_NAME::URL" which will default to NETWORK_CAPABILITIES="archive,traces")
                let colon = arg.find(':').ok_or_else(|| {
                    return anyhow!(
                        "A network name must be provided alongside the \
                         Ethereum node location. Try e.g. 'mainnet:URL'."
                    );
                })?;

                let (name, rest_with_delim) = arg.split_at(colon);
                let rest = &rest_with_delim[1..];
                if name.is_empty() {
                    return Err(anyhow!("Ethereum network name cannot be an empty string"));
                }
                if rest.is_empty() {
                    return Err(anyhow!("Ethereum node URL cannot be an empty string"));
                }

                let colon = rest.find(':').ok_or_else(|| {
                    return anyhow!(
                        "A network name must be provided alongside the \
                         Ethereum node location. Try e.g. 'mainnet:URL'."
                    );
                })?;

                let (features, url_str) = rest.split_at(colon);
                let (url, features) = if vec!["http", "https", "ws", "wss"].contains(&features) {
                    (rest, DEFAULT_PROVIDER_FEATURES.to_vec())
                } else {
                    (&url_str[1..], features.split(',').collect())
                };
                let features = features.into_iter().map(|s| s.to_string()).collect();
                let provider = Provider {
                    label: format!("{}-{}-{}", name, transport, nr),
                    details: ProviderDetails::Web3(Web3Provider {
                        transport,
                        url: url.to_string(),
                        features,
                        headers: Default::default(),
                    }),
                };
                let entry = chains
                    .entry(name.to_string())
                    .or_insert_with(|| Chain { providers: vec![] });
                entry.providers.push(provider);
            }
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Chain {
    #[serde(rename = "provider")]
    pub providers: Vec<Provider>,
}

impl Chain {
    fn validate(&mut self) -> Result<()> {
        // `Config` validates that `self.shard` references a configured shard

        for provider in self.providers.iter_mut() {
            provider.validate()?
        }
        Ok(())
    }
}

fn deserialize_http_headers<'de, D>(deserializer: D) -> Result<HeaderMap, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let kvs: BTreeMap<String, String> = Deserialize::deserialize(deserializer)?;
    Ok(btree_map_to_http_headers(kvs))
}

fn btree_map_to_http_headers(kvs: BTreeMap<String, String>) -> HeaderMap {
    let mut headers = HeaderMap::new();
    for (k, v) in kvs.into_iter() {
        headers.insert(
            k.parse::<http::header::HeaderName>()
                .expect(&format!("invalid HTTP header name: {}", k)),
            v.parse::<http::header::HeaderValue>()
                .expect(&format!("invalid HTTP header value: {}: {}", k, v)),
        );
    }
    headers
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub struct Provider {
    pub label: String,
    pub details: ProviderDetails,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ProviderDetails {
    Firehose(FirehoseProvider),
    Web3(Web3Provider),
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct FirehoseProvider {
    pub url: String,
    pub token: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub struct Web3Provider {
    #[serde(default)]
    pub transport: Transport,
    pub url: String,
    pub features: BTreeSet<String>,

    // TODO: This should be serialized.
    #[serde(
        skip_serializing,
        default,
        deserialize_with = "deserialize_http_headers"
    )]
    pub headers: HeaderMap,
}

const PROVIDER_FEATURES: [&str; 3] = ["traces", "archive", "no_eip1898"];
const DEFAULT_PROVIDER_FEATURES: [&str; 2] = ["traces", "archive"];

impl Provider {
    fn validate(&mut self) -> Result<()> {
        validate_name(&self.label).context("illegal provider name")?;

        match self.details {
            ProviderDetails::Firehose(ref firehose) => {
                // A Firehose url must be a valid Uri since gRPC library we use (Tonic)
                // works with Uri.
                firehose.url.parse::<Uri>().map_err(|e| {
                    anyhow!(
                        "the url `{}` for firehose provider {} is not a legal URI: {}",
                        firehose.url,
                        self.label,
                        e
                    )
                })?;
            }

            ProviderDetails::Web3(ref mut web3) => {
                for feature in &web3.features {
                    if !PROVIDER_FEATURES.contains(&feature.as_str()) {
                        return Err(anyhow!(
                            "illegal feature `{}` for provider {}. Features must be one of {}",
                            feature,
                            self.label,
                            PROVIDER_FEATURES.join(", ")
                        ));
                    }
                }

                web3.url = shellexpand::env(&web3.url)?.into_owned();

                let label = &self.label;
                Url::parse(&web3.url).map_err(|e| {
                    anyhow!(
                        "the url `{}` for provider {} is not a legal URL: {}",
                        web3.url,
                        label,
                        e
                    )
                })?;
            }
        }

        Ok(())
    }
}

impl<'de> Deserialize<'de> for Provider {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct ProviderVisitor;

        impl<'de> serde::de::Visitor<'de> for ProviderVisitor {
            type Value = Provider;

            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
                formatter.write_str("struct Provider")
            }

            fn visit_map<V>(self, mut map: V) -> Result<Provider, V::Error>
            where
                V: serde::de::MapAccess<'de>,
            {
                let mut label = None;
                let mut details = None;

                let mut url = None;
                let mut transport = None;
                let mut features = None;
                let mut headers = None;

                while let Some(key) = map.next_key()? {
                    match key {
                        ProviderField::Label => {
                            if label.is_some() {
                                return Err(serde::de::Error::duplicate_field("label"));
                            }
                            label = Some(map.next_value()?);
                        }
                        ProviderField::Details => {
                            if details.is_some() {
                                return Err(serde::de::Error::duplicate_field("details"));
                            }
                            details = Some(map.next_value()?);
                        }
                        ProviderField::Url => {
                            if url.is_some() {
                                return Err(serde::de::Error::duplicate_field("url"));
                            }
                            url = Some(map.next_value()?);
                        }
                        ProviderField::Transport => {
                            if transport.is_some() {
                                return Err(serde::de::Error::duplicate_field("transport"));
                            }
                            transport = Some(map.next_value()?);
                        }
                        ProviderField::Features => {
                            if features.is_some() {
                                return Err(serde::de::Error::duplicate_field("features"));
                            }
                            features = Some(map.next_value()?);
                        }
                        ProviderField::Headers => {
                            if headers.is_some() {
                                return Err(serde::de::Error::duplicate_field("headers"));
                            }

                            let raw_headers: BTreeMap<String, String> = map.next_value()?;
                            headers = Some(btree_map_to_http_headers(raw_headers));
                        }
                    }
                }

                let label = label.ok_or_else(|| serde::de::Error::missing_field("label"))?;
                let details = match details {
                    Some(v) => {
                        if url.is_some()
                            || transport.is_some()
                            || features.is_some()
                            || headers.is_some()
                        {
                            return Err(serde::de::Error::custom("when `details` field is provided, deprecated `url`, `transport`, `features` and `headers` cannot be specified"));
                        }

                        v
                    }
                    None => ProviderDetails::Web3(Web3Provider {
                        url: url.ok_or_else(|| serde::de::Error::missing_field("url"))?,
                        transport: transport.unwrap_or(Transport::Rpc),
                        features: features
                            .ok_or_else(|| serde::de::Error::missing_field("features"))?,
                        headers: headers.unwrap_or_else(|| HeaderMap::new()),
                    }),
                };

                Ok(Provider { label, details })
            }
        }

        const FIELDS: &'static [&'static str] = &[
            "label",
            "details",
            "transport",
            "url",
            "features",
            "headers",
        ];
        deserializer.deserialize_struct("Provider", FIELDS, ProviderVisitor)
    }
}

#[derive(Deserialize)]
#[serde(field_identifier, rename_all = "lowercase")]
enum ProviderField {
    Label,
    Details,

    // Deprecated fields
    Url,
    Transport,
    Features,
    Headers,
}

#[derive(Copy, Clone, Debug, Deserialize, Serialize, PartialEq)]
pub enum Transport {
    #[serde(rename = "rpc")]
    Rpc,
    #[serde(rename = "ws")]
    Ws,
    #[serde(rename = "ipc")]
    Ipc,
}

impl Default for Transport {
    fn default() -> Self {
        Self::Rpc
    }
}

impl std::fmt::Display for Transport {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        use Transport::*;

        match self {
            Rpc => write!(f, "rpc"),
            Ws => write!(f, "ws"),
            Ipc => write!(f, "ipc"),
        }
    }
}

/// Replace the host portion of `url` and return a new URL with `host`
/// as the host portion
///
/// Panics if `url` is not a valid URL (which won't happen in our case since
/// we would have paniced before getting here as `url` is the connection for
/// the primary Postgres instance)
fn replace_host(url: &str, host: &str) -> String {
    let mut url = match Url::parse(url) {
        Ok(url) => url,
        Err(_) => panic!("Invalid Postgres URL {}", url),
    };
    if let Err(e) = url.set_host(Some(host)) {
        panic!("Invalid Postgres url {}: {}", url, e.to_string());
    }
    String::from(url)
}

fn one() -> usize {
    1
}
