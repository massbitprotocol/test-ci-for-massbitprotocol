use crate::core::{AdapterManager, BlockResponse, MessageHandler};
use crate::solana::SolanaHandler;
use index_store::Store;
use libloading::Library;
use massbit::prelude::serde_json;
use massbit_chain_solana::data_type::{decode, SolanaBlock, SolanaLogMessages, SolanaTransaction};
use std::sync::Arc;
use std::{alloc::System, collections::HashMap, error::Error, ffi::OsStr, rc::Rc};

lazy_static::lazy_static! {
    pub static ref COMPONENT_NAME: String = String::from("[Solana-Adapter]");
}
/// A proxy object which wraps a [`Handler`] and makes sure it can't outlive
/// the library it came from.
pub struct SolanaHandlerProxy {
    pub handler: Box<dyn SolanaHandler + Send + Sync>,
    _lib: Arc<Library>,
}
impl SolanaHandlerProxy {
    pub fn new(
        handler: Box<dyn SolanaHandler + Send + Sync>,
        _lib: Arc<Library>,
    ) -> SolanaHandlerProxy {
        SolanaHandlerProxy { handler, _lib }
    }
}
impl SolanaHandler for SolanaHandlerProxy {
    fn handle_block(&self, message: &SolanaBlock) -> Result<(), Box<dyn Error>> {
        self.handler.handle_block(message)
    }
    fn handle_transaction(&self, message: &SolanaTransaction) -> Result<(), Box<dyn Error>> {
        self.handler.handle_transaction(message)
    }
    fn handle_log_messages(&self, message: &SolanaLogMessages) -> Result<(), Box<dyn Error>> {
        self.handler.handle_log_messages(message)
    }
}

impl MessageHandler for SolanaHandlerProxy {
    fn handle_block_mapping(
        &self,
        data: &mut BlockResponse,
        store: &mut dyn Store,
    ) -> Result<(), Box<dyn Error>> {
        //log::info!("handle_block_mapping data: {:?}", data);
        let blocks: Vec<SolanaBlock> = decode(&mut data.payload).unwrap();
        // Todo: Rewrite the flush so it will flush after finish the array of blocks for better performance. For now, we flush after each block.
        for block in blocks {
            log::info!(
                "{} Received SOLANA BLOCK with block slot: {:?} and hash {:?}, with {} TRANSACTIONs",
                &*COMPONENT_NAME,
                &block.block_number,
                &block.block.blockhash,
                &block.block.transactions.len()
            );
            self.handler.handle_block(&block);
            let mut print_flag = true;
            for origin_transaction in block.clone().block.transactions {
                let origin_log_messages = origin_transaction.meta.clone().unwrap().log_messages;
                let transaction = SolanaTransaction {
                    block_number: ((&block).block.block_height.unwrap_or_default() as u32),
                    transaction: origin_transaction.clone(),
                    log_messages: origin_log_messages.clone(),
                    success: false,
                };
                let log_messages = SolanaLogMessages {
                    block_number: ((&block).block.block_height.unwrap_or_default() as u32),
                    log_messages: origin_log_messages.clone(),
                    transaction: origin_transaction.clone(),
                };
                if print_flag {
                    log::debug!(
                        "{} Recieved SOLANA TRANSACTION with Block number: {:?}, transaction: {:?}",
                        &*COMPONENT_NAME,
                        &transaction.block_number,
                        &transaction.transaction.transaction.signatures
                    );
                    log::debug!(
                    "{} Recieved SOLANA LOG_MESSAGES with Block number: {:?}, log_messages: {:?}",
                    &*COMPONENT_NAME,
                    &log_messages.block_number,
                    &log_messages.log_messages.clone().unwrap().get(0)
                );
                    print_flag = false;
                }
                // self.handler.handle_transaction(&transaction);
                // self.handler.handle_log_messages(&log_messages);
            }
            store.flush(&block.block.blockhash, block.block_number);
        }
        Ok(())
    }
    // fn handle_transaction_mapping(
    //     &self,
    //     transaction: &mut BlockResponse,
    //     store: &mut dyn Store,
    // ) -> Result<(), Box<dyn Error>> {
    //     let transaction: SolanaTransaction =
    //         serde_json::from_slice(&mut transaction.payload).unwrap();
    //     log::info!(
    //         "{} Received SOLANA Transaction with block slot: {:?}",
    //         &*COMPONENT_NAME,
    //         &transaction.block_number,
    //     );
    //     self.handler.handle_transaction(&transaction)
    // }
}
