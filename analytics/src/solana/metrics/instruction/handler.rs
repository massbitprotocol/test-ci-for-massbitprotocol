use super::common::PARSABLE_PROGRAM_IDS;
use crate::create_columns;
use crate::postgres_queries::UpsertConflictFragment;
use crate::relational::{Column, ColumnType, Table};
use crate::solana::handler::SolanaHandler;
use crate::solana::metrics::instruction::common::InstructionKey;
use crate::solana::metrics::instruction::raw_instruction::create_unparsed_instruction;
use crate::solana::metrics::instruction::spltoken_instruction::create_spltoken_entity;
use crate::solana::metrics::instruction::system_instruction::create_system_entity;
use crate::solana::metrics::instruction::vote_instruction::create_vote_entity;
use crate::storage_adapter::StorageAdapter;
use massbit::prelude::Entity;
use massbit_chain_solana::data_type::Pubkey;
use massbit_common::NetworkType;
use solana_sdk::transaction::Transaction;
use solana_transaction_status::parse_instruction::{ParsableProgram, ParsedInstruction};
use solana_transaction_status::{parse_instruction, EncodedConfirmedBlock};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

pub struct SolanaInstructionHandler {
    pub network: Option<NetworkType>,
    pub storage_adapter: Arc<dyn StorageAdapter>,
}

impl SolanaInstructionHandler {
    pub fn new(network: &Option<NetworkType>, storage_adapter: Arc<dyn StorageAdapter>) -> Self {
        SolanaInstructionHandler {
            network: network.clone(),
            storage_adapter,
        }
    }
}

impl SolanaHandler for SolanaInstructionHandler {
    fn handle_block(
        &self,
        block_slot: u64,
        block: Arc<EncodedConfirmedBlock>,
    ) -> Result<(), anyhow::Error> {
        //log::info!("Handle block instructions");
        let mut parsed_entities: HashMap<InstructionKey, Vec<Entity>> = HashMap::default();
        let mut unparsed_entities = Vec::default();
        let start = Instant::now();
        let mut total_instruction = 0;
        for (tx_index, tran) in block.transactions.iter().enumerate() {
            if let Some(transaction) = tran.transaction.decode() {
                total_instruction = total_instruction + transaction.message.instructions.len();
                let entities =
                    create_instructions(block_slot, block.clone(), &transaction, tx_index as i32);
                parsed_entities.extend(entities.0);
                unparsed_entities.extend(entities.1);
            }
            //create_inner_instructions(&block.block, tran);
        }

        let mut program_entities = Vec::default();
        parsed_entities.into_iter().for_each(|(key, entities)| {
            //Store program info from InstructionKey
            program_entities.push(key.create_program_entity());
            let adapter = self.storage_adapter.clone();
            tokio::spawn(async move {
                match key.create_table() {
                    Some(table) => {
                        adapter.upsert(&table, &entities, &None);
                    }
                    None => {}
                }
            });
        });
        if program_entities.len() > 0 {
            log::info!("Store instruction programs info");
            let prog_columns = create_columns!(
                "program_id" => ColumnType::String,
                "program_name" => ColumnType::String,
                "type" => ColumnType::String
            );
            let table = Table::new("solana_programs", prog_columns);
            let conflict_frag = Some(UpsertConflictFragment::new("solana_programs_type_uindex"));
            self.storage_adapter
                .upsert(&table, &program_entities, &conflict_frag);
        }
        log::info!(
            "Parsing {} instructions in {:?}",
            total_instruction,
            start.elapsed()
        );
        Ok(())
        //Don't store unpased instruction due to huge amount of data
        // if unparsed_entities.len() > 0 {
        //     let columns = create_columns!(
        //         "block_slot" => ColumnType::BigInt,
        //         "tx_index" => ColumnType::Int,
        //         "block_time" => ColumnType::BigInt,
        //         //Index of instruction in transaction
        //         "inst_index" => ColumnType::Int,
        //         "program_name" => ColumnType::String,
        //         "accounts" => ColumnType::TextArray,
        //         "data" => ColumnType::Bytes
        //     );
        //     let table = Table::new("solana_instructions", columns);
        //     //let table = create_unparsed_instruction_table();
        //     self.storage_adapter
        //         .upsert(&table, &unparsed_entities, &None)
        // } else {
        //     Ok(())
        // }
    }
}

///
/// For each transaction try to parse instructions and create correspond entities,
/// Unparsed instructions are converted to common entities
///
fn create_instructions(
    block_slot: u64,
    block: Arc<EncodedConfirmedBlock>,
    tran: &Transaction,
    tx_index: i32,
) -> (HashMap<InstructionKey, Vec<Entity>>, Vec<Entity>) {
    let timestamp = match block.block_time {
        None => 0_u64,
        Some(val) => val as u64,
    };
    let tx_hash = match tran.signatures.get(0) {
        Some(sig) => format!("{:?}", sig),
        None => String::from(""),
    };
    let mut unparsed_instructions = Vec::default();
    let mut parsed_instrucions: HashMap<InstructionKey, Vec<Entity>> = HashMap::default();
    for (ind, inst) in tran.message.instructions.iter().enumerate() {
        let program_key = inst.program_id(tran.message.account_keys.as_slice());
        match parse_instruction::parse(program_key, inst, tran.message.account_keys.as_slice()) {
            Ok(parsed_inst) => {
                let key = InstructionKey::from(&parsed_inst);
                if let Some(entity) = create_parsed_entity(
                    block_slot,
                    program_key,
                    tx_hash.clone(),
                    timestamp,
                    ind as i32,
                    &parsed_inst,
                ) {
                    match parsed_instrucions.get_mut(&key) {
                        None => {
                            parsed_instrucions.insert(key, vec![entity]);
                        }
                        Some(vec) => {
                            vec.push(entity);
                        }
                    };
                };
            }
            Err(_) => {
                unparsed_instructions.push(create_unparsed_instruction(
                    block_slot,
                    tx_index,
                    timestamp,
                    ind as i32,
                    program_key.to_string(),
                    tran,
                    inst,
                ));
            }
        }
    }
    (parsed_instrucions, unparsed_instructions)
}

fn create_parsed_entity(
    block_slot: u64,
    program_id: &Pubkey,
    tx_hash: String,
    block_time: u64,
    inst_order: i32,
    inst: &ParsedInstruction,
) -> Option<Entity> {
    match PARSABLE_PROGRAM_IDS.get(program_id) {
        Some(ParsableProgram::System) => {
            create_system_entity(block_slot, tx_hash, block_time, inst_order, inst)
        }
        Some(ParsableProgram::SplToken) => {
            create_spltoken_entity(block_slot, tx_hash, block_time, inst_order, inst)
        }
        Some(ParsableProgram::Vote) => {
            create_vote_entity(block_slot, tx_hash, block_time, inst_order, inst)
        }
        _ => None,
    }
}
