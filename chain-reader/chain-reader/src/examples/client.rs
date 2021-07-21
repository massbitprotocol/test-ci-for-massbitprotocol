use crate::stream_mod::{
    streamout_client::StreamoutClient, ChainType, DataType, GenericDataProto, GetBlocksRequest,
};
use massbit_chain_solana::data_type::{
    convert_solana_encoded_block_to_solana_block, decode as solana_decode,
    SolanaEncodedBlock, SolanaLogMessages, SolanaTransaction,
};
use massbit_chain_substrate::data_type::{
    SubstrateBlock, SubstrateEventRecord,
};
#[allow(unused_imports)]
use tonic::{transport::{Server, Channel}, Request, Response, Status};


pub mod stream_mod {
    tonic::include_proto!("chaindata");
}
use massbit_chain_substrate::data_type::{
    decode, get_extrinsics_from_block
};
use std::time::Instant;
use std::rc::Rc;
use std::sync::Arc;


const URL: &str = "http://127.0.0.1:50051";


pub async fn print_blocks(mut client: StreamoutClient<Channel>, chain_type: ChainType) -> Result<(), Box<dyn std::error::Error + Send + Sync + 'static>> {
    // Not use start_block_number start_block_number yet
    let get_blocks_request = GetBlocksRequest {
        start_block_number: 0,
        end_block_number: 1,
        chain_type: chain_type as i32,
    };
    let mut stream = client
        .list_blocks(Request::new(get_blocks_request))
        .await?
        .into_inner();

    while let Some(data) = stream.message().await? {
        let mut data = data as GenericDataProto;
        println!(
            "Received chain: {:?}, data block = {:?}, hash = {:?}, data type = {:?}",
            ChainType::from_i32(data.chain_type).unwrap(),
            data.block_number,
            data.block_hash,
            DataType::from_i32(data.data_type).unwrap()
        );
        match chain_type {
            ChainType::Substrate => {
                let now = Instant::now();
                match DataType::from_i32(data.data_type) {
                    Some(DataType::Block) => {
                        let block: SubstrateBlock = decode(&mut data.payload).unwrap();
                        println!("Received BLOCK: {:?}", &block.block.header.number);
                        let extrinsics = get_extrinsics_from_block(&block);
                        for extrinsic in extrinsics {
                            //println!("Recieved EXTRINSIC: {:?}", extrinsic);
                            let string_extrinsic = format!("Recieved EXTRINSIC:{:?}", extrinsic);
                            println!("{}", string_extrinsic);
                        }
                    }
                    Some(DataType::Event) => {
                        let event: Vec<SubstrateEventRecord> = decode(&mut data.payload).unwrap();
                        println!("Received Event: {:?}", event);
                    },

                    _ => {
                        println!("Not support data type: {:?}", &data.data_type);
                    }
                }
                let elapsed = now.elapsed();
                println!("Elapsed processing solana block: {:.2?}", elapsed);
            },
            ChainType::Solana => {
                let now = Instant::now();
                match DataType::from_i32(data.data_type) {
                    Some(DataType::Block) => {

                        let encoded_block: SolanaEncodedBlock = solana_decode(&mut data.payload).unwrap();
                        // Decode
                        let block = convert_solana_encoded_block_to_solana_block(encoded_block);
                        let rc_block = Arc::new(block.clone());
                        println!("Recieved SOLANA BLOCK with block height: {:?}, hash: {:?}", &rc_block.block.block_height.unwrap(), &rc_block.block.blockhash);

                        let mut print_flag = true;
                        for origin_transaction in block.clone().block.transactions {
                            let log_messages = origin_transaction.clone().meta.unwrap().log_messages.clone();
                            let transaction = SolanaTransaction {
                                block_number: ((&block).block.block_height.unwrap() as u32),
                                transaction: origin_transaction.clone(),
                                log_messages: log_messages.clone(),
                                success: false
                            };
                            let rc_transaction = Arc::new(transaction.clone());




                            let log_messages = SolanaLogMessages {
                                block_number: ((&block).block.block_height.unwrap() as u32),
                                log_messages: log_messages.clone(),
                                transaction: origin_transaction.clone(),
                            };
                            // Print first data only bc it too many.
                            if print_flag {
                                println!("Recieved SOLANA TRANSACTION with Block number: {:?}, trainsation: {:?}", &transaction.block_number, &transaction.transaction.transaction.signatures);
                                println!("Recieved SOLANA LOG_MESSAGES with Block number: {:?}, log_messages: {:?}", &log_messages.block_number, &log_messages.log_messages.unwrap().get(0));

                                print_flag = false;
                            }

                        }
                    },
                    _ => {
                        println!("Not support this type in Solana");
                    }
                }
                let elapsed = now.elapsed();
                println!("Elapsed processing solana block: {:.2?}", elapsed);
            },
            _ => {
                println!("Not support this package chain-type");
            }
        }
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync + 'static>> {

    println!("Waiting for chain-reader");

    tokio::spawn(async move {
        let client = StreamoutClient::connect(URL).await.unwrap();
        print_blocks(client, ChainType::Solana).await
    });

    let client = StreamoutClient::connect(URL).await.unwrap();
    print_blocks(client, ChainType::Substrate).await?;

    Ok(())
}
