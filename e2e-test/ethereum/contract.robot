*** Settings ***
Library  RequestsLibrary
Library  OperatingSystem
Library  RPA.JSON
Library  DatabaseLibrary
Library  ../core-lib/request.py
Library  ../core-lib/pgconnection.py
Library  ../core-lib/example-reader.py
Library  String

*** Variables ***
${CODE_COMPILER}  http://localhost:5000
${INDEX_MANAGER}  http://localhost:3000

*** Test Cases ***
#####################
# Test-harmony WASM #
#####################
Compile and Deploy WASM Test Harmony
    # Configuration
    Connect To Database  psycopg2  graph-node  graph-node  let-me-in  localhost  5432

    # Compile request
    ${object} =  Read Wasm Example  ../../user-example/ethereum/wasm/harmony
    ${compile_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/compile/wasm
    ...  ${object}
    Should be equal  ${compile_res["status"]}  success

    # Compile status
    Wait Until Keyword Succeeds
    ...  60x
    ...  3 sec
    ...  Pooling Status
    ...  ${compile_res["payload"]}

    # Deploy
    ${json}=  Convert String to JSON  {"compilation_id": "${compile_res["payload"]}"}
    ${deploy_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/deploy/wasm
    ...  ${json}
    Should be equal  ${deploy_res["status"]}  success


############################
# Uniswap-v2 Exchange WASM #
############################
Compile and Deploy Uniswap-v2 Exchange WASM
    # Configuration
    Connect To Database  psycopg2  graph-node  graph-node  let-me-in  localhost  5432

    # Compile request
    ${object} =  Read Wasm Example  ../../user-example/ethereum/wasm/uniswap-v2-exchange
    ${compile_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/compile/wasm
    ...  ${object}
    Should be equal  ${compile_res["status"]}  success

    # Compile status
    Wait Until Keyword Succeeds
    ...  60x
    ...  3 sec
    ...  Pooling Status
    ...  ${compile_res["payload"]}

    # Deploy
    ${json}=  Convert String to JSON  {"compilation_id": "${compile_res["payload"]}"}
    ${deploy_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/deploy/wasm
    ...  ${json}
    Should be equal  ${deploy_res["status"]}  success


#################################
# Test-uniswap-v3 Exchange WASM #
#################################
Compile and Deploy WASM Test Uniswap V3 Exchange
    # Configuration
    Connect To Database  psycopg2  graph-node  graph-node  let-me-in  localhost  5432

    # Compile request
    ${object} =  Read Wasm Example  ../../user-example/ethereum/wasm/uniswap-v3-exchange
    ${compile_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/compile/wasm
    ...  ${object}
    Should be equal  ${compile_res["status"]}  success

    # Compile status
    Wait Until Keyword Succeeds
    ...  60x
    ...  3 sec
    ...  Pooling Status
    ...  ${compile_res["payload"]}

    # Deploy
    ${json}=  Convert String to JSON  {"compilation_id": "${compile_res["payload"]}"}
    ${deploy_res}=  Request.Post Request
    ...  ${CODE_COMPILER}/deploy/wasm
    ...  ${json}
    Should be equal  ${deploy_res["status"]}  success


###################
# Helper Function #
###################
*** Keywords ***
Pooling Status
    [Arguments]  ${payload}
    ${status_res} =    GET  ${CODE_COMPILER}/compile/status/${payload}  expected_status=200
    Should be equal   ${status_res.json()}[status]  success

Pooling Database Data
    [Arguments]  ${query}
    Check If Exists In Database  ${query}
