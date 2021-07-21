# Docker file for running chain-reader
FROM ubuntu:18.04

RUN apt update
RUN apt install -y git curl

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
RUN apt install -y cmake pkg-config libssl-dev git gcc build-essential clang libclang-dev libpq-dev
RUN apt-get update
RUN apt-get install -y libssl-dev libudev-dev pkg-config zlib1g-dev llvm clang make

RUN $HOME/.cargo/bin/rustup target add wasm32-unknown-unknown --toolchain stable

COPY ./ massbitprotocol
WORKDIR "massbitprotocol"

RUN ls

EXPOSE 50051
CMD ./target/release/chain-reader