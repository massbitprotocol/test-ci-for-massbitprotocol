# Codegen CLI

## Usage
```shell
cd cli
cargo run -- codegen -s example/schema.graphql -c example/project.yaml -o example/
```

## Input templates
`schema.graphql`
```graphql
type BlockTs @entity {
  id: ID!
  blockHeight: BigInt!
}
```
`project.yaml`
```yaml
schema:
  file: ./schema.graphql

dataSources:
  - kind: substrate
    name: Index
    network: https://data-seed-prebsc-1-s1.binance.org:8545/
    mapping:
      language: rust
      handlers:
        - handler: handleBlock
          kind: substrate/BlockHandler
        - handler: handleExtrinsic
          kind: substrate/ExtrinsicHandler
        - handler: handleEvent
          kind: substrate/EventHandler
  - kind: solana
    name: Index
    network: https://data-seed-prebsc-1-s1.binance.org:8545/
    mapping:
      language: rust
      handlers:
        - handler: handleBlock
          kind: solana/BlockHandler
        - handler: handleTransaction
          kind: solana/TransactionHandler
```