FROM rust
# FROM rust:alpine
COPY . .
RUN ls -ll

RUN ["chmod", "+x", "index-manager-main/index-manager-main"]
RUN ls -ll
CMD ./index-manager-main/index-manager-main
