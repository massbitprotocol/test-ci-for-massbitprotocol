use bs58;
use log::warn;
use serde::{Deserialize, Serialize};
use serde_json;
use solana_transaction_status;
use solana_transaction_status::UiInstruction::{Compiled, Parsed};
use solana_transaction_status::{
    EncodedTransactionWithStatusMeta, InnerInstructions, TransactionStatusMeta,
    TransactionTokenBalance, TransactionWithStatusMeta, UiInnerInstructions,
    UiTransactionTokenBalance,
};
use std::error::Error;
use std::str::FromStr;

//***************** Solana data type *****************
// EncodedConfirmedBlock is block with vec of EncodedTransactionWithStatusMeta.
pub type SolanaEncodedBlock = ExtEncodedBlock;
pub type SolanaBlock = ExtBlock;
pub type SolanaTransaction = ExtTransaction;
// The most similar Event concept in Solana is log_messages in UiTransactionStatusMeta in EncodedTransactionWithStatusMeta
pub type SolanaLogMessages = ExtLogMessages;
pub type Pubkey = solana_program::pubkey::Pubkey;
//***************** End solana data type *****************

pub const SYSTEM_PROGRAM_ID: &str = "11111111111111111111111111111111";
pub const SYSVAR_RENT_ID: &str = "SysvarRent111111111111111111111111111111111";
pub const ASSOCIATED_TOKEN_ACCOUNT_PROGRAM_ID: &str =
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
pub const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
pub const BINARY_OPTION_PROGRAM_ID: &str = "betw959P4WToez4DkuXwNsJszqbpe3HuY56AcG5yevx";

type Number = u32;
type Date = i64;
type LogMessages = Option<Vec<String>>;
type Transaction = solana_transaction_status::TransactionWithStatusMeta;
type EncodedBlock = solana_transaction_status::EncodedConfirmedBlock;
type Block = solana_transaction_status::ConfirmedBlock;

#[derive(Clone, Debug, Serialize, Deserialize, Default)]
pub struct SolanaFilter {
    pub keys: Vec<Pubkey>,
}
impl SolanaFilter {
    pub fn new(keys: Vec<&str>) -> Self {
        SolanaFilter {
            keys: keys
                .iter()
                .map(|key| Pubkey::from_str(key).unwrap_or_default())
                .collect(),
        }
    }
    fn is_match(&self, tran: &TransactionWithStatusMeta) -> bool {
        self.keys.iter().any(|key| {
            tran.transaction
                .message
                .account_keys
                .iter()
                .any(|account_key| key == account_key)
        })
    }

    pub fn filter_block(&self, block: Block) -> Block {
        // If there are no key, then accept all transactions
        if self.keys.is_empty() {
            return block;
        }
        let mut filtered_block = block.clone();
        filtered_block.transactions = block
            .transactions
            .into_iter()
            .filter_map(|tran| {
                if self.is_match(&tran) {
                    Some(tran)
                } else {
                    None
                }
            })
            .collect();
        filtered_block
    }
}

pub fn decode(payload: &mut Vec<u8>) -> Result<Vec<SolanaBlock>, Box<dyn Error>> {
    let decode_block: Vec<SolanaBlock> = serde_json::from_slice(&payload).unwrap();
    Ok(decode_block)
}

pub fn get_list_log_messages_from_encoded_block(block: &Block) -> Vec<LogMessages> {
    block
        .transactions
        .iter()
        .map(|transaction| transaction.meta.as_ref().unwrap().log_messages.clone())
        .collect()
}

fn to_transaction_token_balance(ui_ttb: &UiTransactionTokenBalance) -> TransactionTokenBalance {
    TransactionTokenBalance {
        account_index: ui_ttb.account_index.clone(),
        mint: ui_ttb.mint.clone(),
        ui_token_amount: ui_ttb.ui_token_amount.clone(),
        owner: "".to_string(),
    }
}

fn to_ui_instructions(ui_inner_instruction: UiInnerInstructions) -> InnerInstructions {
    InnerInstructions {
        index: ui_inner_instruction.index,
        //instructions: compiled_instructions,
        instructions: ui_inner_instruction
            .instructions
            .iter()
            .filter_map(|ui_instruction| {
                match ui_instruction {
                    Compiled(ui_compiled_instruction) => {
                        Some(solana_program::instruction::CompiledInstruction {
                            program_id_index: ui_compiled_instruction.program_id_index,
                            accounts: ui_compiled_instruction.accounts.clone(),
                            data: bs58::decode(ui_compiled_instruction.data.clone())
                                .into_vec()
                                .unwrap(),
                        })
                    }
                    // Todo: need support Parsed(UiParsedInstruction)
                    Parsed(ui_parsed_instruction) => {
                        warn!(
                            "Not support ui_instruction type: {:?}",
                            ui_parsed_instruction
                        );
                        None
                    }
                }
            })
            .collect(),
    }
}

pub fn decode_transaction(
    encode_txs: &EncodedTransactionWithStatusMeta,
) -> Option<TransactionWithStatusMeta> {
    let meta = encode_txs.meta.as_ref().unwrap();
    let decoded_transaction = encode_txs.transaction.decode()?;
    let post_token_balances: Option<Vec<TransactionTokenBalance>> = match &meta.post_token_balances
    {
        Some(post_token_balances) => Some(
            post_token_balances
                .into_iter()
                .map(|ui_ttb| to_transaction_token_balance(ui_ttb))
                .collect(),
        ),
        None => None,
    };
    let pre_token_balances: Option<Vec<TransactionTokenBalance>> = match &meta.pre_token_balances {
        Some(pre_token_balances) => Some(
            pre_token_balances
                .into_iter()
                .map(|ui_ttb| to_transaction_token_balance(ui_ttb))
                .collect(),
        ),
        None => None,
    };
    let inner_instructions: Option<Vec<InnerInstructions>> = Some(
        meta.inner_instructions
            .clone()
            .unwrap()
            .iter()
            .map(|ui_inner_instruction| to_ui_instructions(ui_inner_instruction.clone()))
            .collect(),
    );

    Some(solana_transaction_status::TransactionWithStatusMeta {
        meta: Some(TransactionStatusMeta {
            status: meta.status.clone(),
            rewards: meta.rewards.clone(),
            log_messages: meta.log_messages.clone(),
            fee: meta.fee,
            post_balances: meta.post_balances.clone(),
            pre_balances: meta.pre_balances.clone(),
            inner_instructions: inner_instructions,
            post_token_balances,
            pre_token_balances,
        }),
        transaction: decoded_transaction,
    })
}

pub fn decode_encoded_block(encoded_block: EncodedBlock) -> Block {
    Block {
        rewards: encoded_block.rewards,
        transactions: encoded_block
            .transactions
            .iter()
            .filter_map(|transaction| {
                let meta = &transaction.meta.as_ref().unwrap();
                let decoded_transaction = transaction.transaction.decode();
                let post_token_balances: Option<Vec<TransactionTokenBalance>> =
                    match &meta.post_token_balances {
                        Some(post_token_balances) => Some(
                            post_token_balances
                                .into_iter()
                                .map(|ui_ttb| to_transaction_token_balance(ui_ttb))
                                .collect(),
                        ),
                        None => None,
                    };
                let pre_token_balances: Option<Vec<TransactionTokenBalance>> =
                    match &meta.pre_token_balances {
                        Some(pre_token_balances) => Some(
                            pre_token_balances
                                .into_iter()
                                .map(|ui_ttb| to_transaction_token_balance(ui_ttb))
                                .collect(),
                        ),
                        None => None,
                    };
                let inner_instructions: Option<Vec<InnerInstructions>> = Some(
                    meta.inner_instructions
                        .clone()
                        .unwrap()
                        .iter()
                        .map(|ui_inner_instruction| {
                            to_ui_instructions(ui_inner_instruction.clone())
                        })
                        .collect(),
                );

                match decoded_transaction {
                    Some(decoded_transaction) => {
                        Some(solana_transaction_status::TransactionWithStatusMeta {
                            meta: Some(TransactionStatusMeta {
                                status: meta.status.clone(),
                                rewards: meta.rewards.clone(),
                                log_messages: meta.log_messages.clone(),
                                fee: meta.fee,
                                post_balances: meta.post_balances.clone(),
                                pre_balances: meta.pre_balances.clone(),
                                inner_instructions: inner_instructions,
                                post_token_balances,
                                pre_token_balances,
                                // EndTodo
                            }),
                            transaction: decoded_transaction,
                        })
                    }
                    None => None,
                }
            })
            .collect(),
        block_time: encoded_block.block_time,
        blockhash: encoded_block.blockhash,
        block_height: encoded_block.block_height,
        parent_slot: encoded_block.parent_slot,
        previous_blockhash: encoded_block.previous_blockhash,
    }
}

pub fn convert_solana_encoded_block_to_solana_block(
    encoded_block: SolanaEncodedBlock,
) -> SolanaBlock {
    SolanaBlock {
        version: encoded_block.version,
        timestamp: encoded_block.timestamp,
        block_number: encoded_block.block_number,
        block: decode_encoded_block(encoded_block.block),
        list_log_messages: encoded_block.list_log_messages,
    }
}

// Similar to
// https://github.com/subquery/subql/blob/93afc96d7ee0ff56d4dd62d8a145088f5bb5e3ec/packages/types/src/interfaces.ts#L18
#[derive(PartialEq, Debug, Serialize, Deserialize)]
pub struct ExtEncodedBlock {
    pub version: String,
    pub timestamp: Date,
    pub block_number: u64,
    pub block: EncodedBlock,
    pub list_log_messages: Vec<LogMessages>,
}

#[derive(PartialEq, Clone, Debug, Serialize, Deserialize)]
pub struct ExtBlock {
    pub version: String,
    pub timestamp: Date,
    pub block_number: u64,
    pub block: Block,
    pub list_log_messages: Vec<LogMessages>,
}

#[derive(PartialEq, Clone, Debug, Serialize, Deserialize)]
pub struct ExtTransaction {
    pub block_number: Number,
    pub transaction: Transaction,
    //pub block: Arc<ExtBlock>,
    pub log_messages: LogMessages,
    pub success: bool,
}

#[derive(PartialEq, Clone, Debug, Serialize, Deserialize)]
pub struct ExtLogMessages {
    pub block_number: Number,
    pub log_messages: LogMessages,
    pub transaction: Transaction,
    //pub block: Arc<ExtBlock>,
}
