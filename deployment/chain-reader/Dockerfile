FROM rust
COPY . .
RUN ls -ll
RUN ["chmod", "+x", "chain-reader/chain-reader"]
RUN ls -ll
CMD ./chain-reader/chain-reader
