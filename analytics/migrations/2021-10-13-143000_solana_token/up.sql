create table solana_spl_token_initialize_mints
(
    id                  bigserial constraint solana_spl_token_initialize_mints_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    mint                varchar(88),
    decimals            smallint,
    mint_authority      varchar(88),
    rent_sysvar         varchar(88),
    freeze_authority    varchar(88)
);

create table solana_spl_token_initialize_accounts
(
    id                  bigserial constraint solana_spl_token_initialize_accounts_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    account             varchar(88),
    mint                varchar(88),
    owner               varchar(88),
    rent_sysvar         varchar(88)
);

create table solana_spl_token_initialize_account2s
(
    id                  bigserial constraint solana_spl_token_initialize_account2_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    account             varchar(88),
    mint                varchar(88),
    owner               varchar(88),
    rent_sysvar         varchar(88)
);

create table solana_spl_token_initialize_multisigs
(
    id                  bigserial constraint solana_spl_token_initialize_multisigs_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    multisig            varchar(88),
    rent_sysvar         varchar(88),
    signers             text,
    m                   smallint

);

create table solana_spl_token_transfers
(
    id                  bigserial constraint solana_spl_token_transfers_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    source              varchar(88),
    destination         varchar(88),
    amount              bigint,
    authority           varchar(88),
    multisig_authority  varchar(88),
    signers             text
);
create table solana_spl_token_transfer_checkeds
(
    id                          bigserial constraint solana_spl_token_transfer_checkeds_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    source                      varchar(88),
    mint                        varchar(88),
    destination                 varchar(88),
    token_amount                varchar(88),
    authority                   varchar(88),
    multisig_fauthority         text,
    signers                     text
);
create table solana_spl_token_approves
(
    id                  bigserial constraint solana_spl_token_approves_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    source              varchar(88),
    delegate            varchar(88),
    amount              bigint,
    owner               varchar(88),
    multisig_owner      text,
    signers             text
);


create table solana_spl_token_approve_checkeds
(
    id                          bigserial constraint solana_spl_token_approve_checkeds_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    source                      varchar(88),
    mint                        varchar(88),
    delegate                    varchar(88),
    token_amount                varchar(88),
    owner                       varchar(88),
    multisig_owner              text,
    signers                     text
);

create table solana_spl_token_revokes
(
    id                  bigserial constraint solana_spl_token_revokes_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    source              varchar(88),
    signers             text,
    owner               varchar(88),
    multisig_owner      text
);

create table solana_spl_token_set_authorities
(
    id                  bigserial constraint solana_spl_token_set_authorities_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    authority_type      varchar(88),
    new_authority       varchar(88),
    signers             text,
    authority           varchar(88),
    multisig_authority  text
);

create table solana_spl_token_mint_tos
(
    id                  bigserial constraint solana_spl_token_mint_tos_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    mint                varchar(88),
    account             varchar(88),
    amount              varchar(88),
    mint_authority      varchar(88),
    multisig_mint_authority  text,
    signers             text
);

create table solana_spl_token_min_to_checkeds
(
    id                          bigserial constraint solana_spl_token_min_to_checkeds_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    mint                        varchar(88),
    account                     varchar(88),
    token_amount                varchar(88),
    mint_authority              varchar(88),
    multisig_mint_authority     varchar(88),
    signers                     text
);

create table solana_spl_token_burns
(
    id                  bigserial constraint solana_spl_token_burns_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    account             varchar(88),
    mint                varchar(88),
    amount              varchar(88),
    signers             text,
    authority      varchar(88),
    multisig_authority  text
);

create table solana_spl_token_burn_checkeds
(
    id                          bigserial constraint solana_spl_token_burn_checkeds_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    account                     varchar(88),
    mint                        varchar(88),
    token_amount                varchar(88),
    authority              varchar(88),
    multisig_mint_authority     varchar(88),
    signers                     text
);

create table solana_spl_token_close_accounts
(
    id                  bigserial constraint solana_spl_token_close_accounts_pk primary key,
    block_slot          bigint,
    block_time          bigint,
    tx_index            int,    --Index of transaction in block
    instruction_index   int,    --Index of instruction in transaction
    account             varchar(88),
    destination         varchar(88),
    owner               varchar(88),
    signers             text,
    multisig_owner      text
);

create table solana_spl_token_freeze_accounts
(
    id                          bigserial constraint solana_spl_token_freeze_accounts_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    account                     varchar(88),
    mint                        varchar(88),
    freeze_authority            varchar(88),
    signers                     text,
    multisig_freeze_authority   text
);

create table solana_spl_token_thaw_accounts
(
    id                          bigserial constraint solana_spl_token_thaw_accounts_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    account                     varchar(88),
    mint                        varchar(88),
    freeze_authority            varchar(88),
    signers                     text,
    multisig_freeze_authority   text
);

create table solana_spl_token_sync_natives
(
    id                          bigserial constraint solana_spl_token_sync_natives_pk primary key,
    block_slot                  bigint,
    block_time                  bigint,
    tx_index                    int,    --Index of transaction in block
    instruction_index           int,    --Index of instruction in transaction
    account                     varchar(88)
);
