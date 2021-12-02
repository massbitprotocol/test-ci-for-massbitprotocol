use crate::plugin::handler::SolanaHandler;
use crate::store::IndexStore;
use crate::types::SolanaBlock;
pub use massbit_grpc::firehose::bstream::BlockResponse;
use std::error::Error;
use std::sync::{Arc, Mutex};

pub mod handler;
pub mod proxy;

pub trait PluginRegistrar {
    fn register_solana_handler(&mut self, handler: Box<dyn SolanaHandler + Send + Sync>);
}

#[derive(Copy, Clone)]
pub struct AdapterDeclaration {
    pub register: unsafe extern "C" fn(&mut dyn PluginRegistrar),
}

// General trait for handling message,
// every adapter proxies must implement this trait
pub trait MessageHandler {
    //Return max processed block number
    fn handle_block_mapping(
        &self,
        blocks: Vec<SolanaBlock>,
        _store: Arc<Mutex<Box<dyn IndexStore>>>,
    ) -> Result<i64, Box<dyn Error>>;
    fn handle_transaction_mapping(
        &self,
        _message: &mut BlockResponse,
        _store: &mut dyn IndexStore,
    ) -> Result<(), Box<dyn Error>> {
        log::error!("Error! handle_transaction_mapping is not implemented!");
        Ok(())
    }
}

#[macro_export]
macro_rules! export_plugin {
    ($register:expr) => {
        #[doc(hidden)]
        #[no_mangle]
        pub static adapter_declaration: $crate::plugin::AdapterDeclaration =
            $crate::plugin::AdapterDeclaration {
                register: $register,
            };
    };
}
