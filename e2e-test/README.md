# E2E Test for Substrate and Solana template

## Prerequisites
```
pip install robotframework-requests
pip install robotframework-databaselibrary
```
And make sure you have started all the services 

## Run a Substrate test
```
robot --variable JSON_PAYLOAD:payload/[add_payload_file_here].json --variable TABLE_NAME:[add_table_name_here] substrate.robot
```
Example
```
robot --variable JSON_PAYLOAD:payload/extrinsic.json --variable TABLE_NAME:substrate_extrinsic_test substrate.robot 
robot --variable JSON_PAYLOAD:payload/block.json --variable TABLE_NAME:substrate_block_test substrate.robot 
robot --variable JSON_PAYLOAD:payload/event.json --variable TABLE_NAME:substrate_event_test substrate.robot 
```

## Log
Open log.html in your browser