use crate::CheapClone;
use anyhow::Context;
use http::uri::Uri;
use rand::prelude::IteratorRandom;
use std::{collections::HashMap, fmt::Display, sync::Arc};
use tonic::{
    metadata::MetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request,
};

use super::bstream;

#[derive(Clone)]
pub struct FirehoseEndpoint {
    provider: String,
    uri: String,
    channel: Channel,
    token: Option<String>,
}

impl Display for FirehoseEndpoint {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        Display::fmt(self.uri.as_str(), f)
    }
}

impl FirehoseEndpoint {
    pub async fn new<S: AsRef<str>>(
        provider: S,
        url: S,
        token: Option<String>,
    ) -> Result<Self, anyhow::Error> {
        let uri = url
            .as_ref()
            .parse::<Uri>()
            .expect("the url should have been validated by now, so it is a valid Uri");

        let endpoint = match uri.scheme().unwrap().as_str() {
            "http" => Channel::builder(uri),
            "https" => Channel::builder(uri)
                .tls_config(ClientTlsConfig::new())
                .expect("TLS config on this host is invalid"),
            _ => panic!("invalid uri scheme for firehose endpoint"),
        };

        let uri = endpoint.uri().to_string();
        let channel = endpoint.connect().await?;

        Ok(FirehoseEndpoint {
            provider: provider.as_ref().to_string(),
            uri,
            channel,
            token,
        })
    }

    pub async fn stream_blocks(
        self: Arc<Self>,
        request: bstream::BlockRequest,
    ) -> Result<tonic::Streaming<bstream::BlockResponse>, anyhow::Error> {
        let token_metadata_opt = match self.token.clone() {
            Some(token) => Some(MetadataValue::from_str(token.as_str())?),
            None => None,
        };

        let mut client = bstream::stream_client::StreamClient::with_interceptor(
            self.channel.cheap_clone(),
            move |mut r: Request<()>| match token_metadata_opt.clone() {
                Some(t) => {
                    r.metadata_mut().insert("authorization", t.clone());
                    Ok(r)
                }
                _ => Ok(r),
            },
        );

        let response_stream = client
            .blocks(request)
            .await
            .context("unable to fetch blocks from server")?;
        let block_stream = response_stream.into_inner();

        Ok(block_stream)
    }
}

#[derive(Clone)]
pub struct FirehoseNetworkEndpoint {
    endpoint: Arc<FirehoseEndpoint>,
}

#[derive(Clone)]
pub struct FirehoseNetworkEndpoints {
    pub endpoints: Vec<FirehoseNetworkEndpoint>,
}

impl FirehoseNetworkEndpoints {
    pub fn new() -> Self {
        Self { endpoints: vec![] }
    }

    pub fn len(&self) -> usize {
        self.endpoints.len()
    }

    pub fn random(&self) -> Option<&Arc<FirehoseEndpoint>> {
        if self.endpoints.len() == 0 {
            return None;
        }

        // Select from the matching adapters randomly
        let mut rng = rand::thread_rng();
        Some(&self.endpoints.iter().choose(&mut rng).unwrap().endpoint)
    }

    pub fn remove(&mut self, provider: &str) {
        self.endpoints
            .retain(|network_endpoint| network_endpoint.endpoint.provider != provider);
    }
}

#[derive(Clone)]
pub struct FirehoseNetworks {
    pub networks: HashMap<String, FirehoseNetworkEndpoints>,
}

impl FirehoseNetworks {
    pub fn new() -> FirehoseNetworks {
        FirehoseNetworks {
            networks: HashMap::new(),
        }
    }

    pub fn insert(&mut self, name: String, endpoint: Arc<FirehoseEndpoint>) {
        let network_endpoints = self
            .networks
            .entry(name)
            .or_insert(FirehoseNetworkEndpoints { endpoints: vec![] });
        network_endpoints.endpoints.push(FirehoseNetworkEndpoint {
            endpoint: endpoint.clone(),
        });
    }

    pub fn remove(&mut self, name: &str, provider: &str) {
        if let Some(endpoints) = self.networks.get_mut(name) {
            endpoints.remove(provider);
        }
    }
}
