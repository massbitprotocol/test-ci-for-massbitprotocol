mod mapping;
mod models;

use massbit_chain_substrate::data_type as substrate_types;
use massbit_chain_solana::data_type as solana_types;
use plugin::core::{self, PluginRegistrar};
use index_store::core::Store;
use std::error::Error;

#[doc(hidden)]
#[no_mangle]
pub static mut STORE: Option<&mut dyn Store> = None;

plugin::export_plugin!(register);

#[allow(dead_code, improper_ctypes_definitions)]
extern "C" fn register(registrar: &mut dyn PluginRegistrar) {
    registrar.register_substrate_block_handler(Box::new(SubstrateBlockHandler));
}

#[derive(Debug, Clone, PartialEq)]
pub struct SubstrateBlockHandler;

impl core::SubstrateBlockHandler for SubstrateBlockHandler {
    fn handle_block(&self, block: &substrate_types::SubstrateBlock) -> Result<(), Box<dyn Error>> {
        mapping::handle_block(block)
    }
}
