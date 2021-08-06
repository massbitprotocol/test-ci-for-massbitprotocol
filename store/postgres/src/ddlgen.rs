use std::error::Error;
use std::convert::From;
use std::{env, fs};
use graph::prelude::{Schema, SubgraphDeploymentId};

//use graph_store_postgres::command_support::{Namespace, Catalog, Layout};
//use store_postgres::{catalog::{Catalog}, primary::Namespace, relational::{Layout} };
//use super::command_support::{Layout, Namespace, Catalog};
use lazy_static::lazy_static;
use clap::ArgMatches;
use serde_yaml::{Value, Mapping};
use crate::relational::{self, Layout};
use crate::primary::Namespace;
use crate::catalog::Catalog;
//use crate::metrics_registry::*;
use chrono::{DateTime, Utc};
use diesel::{PgConnection, Connection};
use diesel_migrations;
use std::path::{PathBuf};
use serde_json;
use std::fs::File;
use std::io::Read;
use inflector::Inflector;


lazy_static! {
    static ref THINGS_SUBGRAPH_ID: SubgraphDeploymentId = SubgraphDeploymentId::new("subgraphId").unwrap();
    static ref DATABASE_CONNECTION_STRING: String = env::var("DATABASE_CONNECTION_STRING")
        .unwrap_or(String::from("postgres://graph-node:let-me-in@localhost"));
}
//const MIGRATION_PATH: &str = r#"./migrations"#;

pub fn run(matches: &ArgMatches) -> Result<(), Box<dyn Error>> {
    let config_path = matches.value_of("config").unwrap_or("project.yaml");
    let def_catalog = r#"graph-node"#;
    let mut contents = String::new();
    match File::open(config_path) {
        Ok(mut file) => {
            match file.read_to_string(&mut contents) {
                Ok(_) => {}
                Err(_) => {
                    log::warn!("Cannot read config file {}", config_path);
                }
            }
        }
        Err(_) => {
            log::warn!("Config file {} not found", config_path);
        }
    };
    let mut catalog = String::from(def_catalog);
    if contents.len() > 0 {
        let manifest: serde_yaml::Value = serde_yaml::from_str(contents.as_str())?;
        match manifest.get("database") {
            None => {
                log::warn!("Database configuration not found, use default value: {}", &"graph-node".to_owned());
            }
            Some(val) => {
                match val.get("catalog") {
                    None => {
                        log::warn!("Catalog not found, use default value: {}", &"graph-node".to_owned());
                    }
                    Some(value) => {
                        match value.as_str() {
                            None => {
                                log::warn!("Config value for catalog is invalidd, use default value: {}", &"graph-node".to_owned());
                            }
                            Some(str) => {
                                catalog = String::from(str);
                            }
                        }
                    }
                }
            }
        }
    };

    //input schema path
    let schema_path = matches.value_of("schema").unwrap_or("schema.graphql");
    let session = matches.value_of("hash").unwrap_or("");
    let output = matches.value_of("output").unwrap_or("./migrations");

    let raw_schema = fs::read_to_string(schema_path)?;
    let now: String = Utc::now().format("%Y-%m-%d-%H%M%S").to_string();
    //include session hash in output dir
    let out_dir = format!("{}/{}_{}", output, now.as_str(), session);

    match generate_ddl(raw_schema.as_str(), catalog.as_str(), out_dir.as_str()) {
        Ok(_) => {
            let url = format!("{}/{}",DATABASE_CONNECTION_STRING.as_str(), catalog);
            let path = PathBuf::from(out_dir.as_str());
            run_migrations(path, url.as_str());
            Ok(())
        }
        Err(err) => Err(err)
    }
}
///
/// Run diesel migrations
///
fn run_migrations(path: PathBuf, db_url : &str) -> Result<(), Box<dyn Error>>{
    log::info!("Migration path: {:?}", &path);
    match diesel_migrations::migration_from(path) {
        Ok(migration) => {
            let list_migrations = vec![migration];
            let connection = PgConnection::establish(&db_url).expect(&format!(
                "Error connecting to {}",
                *DATABASE_CONNECTION_STRING
            ));
            diesel_migrations::run_migrations(&connection, list_migrations, &mut std::io::stdout());
        }
        Err(err) => {
            println!("{:?}", err);
        }
    };
    Ok(())
}
///
/// Parse input schema to pure pgsql sql for creating tables in database.
/// Input: graphql schema, namespace in database
/// Output: 3 files on disk: up.sql, down.sql, hasura_queries.json
///

pub fn generate_ddl(raw: &str, catalog: &str, output_dir: &str) -> Result<(), Box<dyn Error>> {
    //let mut ddls : Vec<String> = Vec::new();
    //let mut table_names : Vec<String> = Vec::new();
    let schema = Schema::parse(raw, THINGS_SUBGRAPH_ID.clone())?;
    //println!("{}",schema.document.to_string());
    let catalog = Catalog::make_empty(Namespace::new(String::from(catalog))?)?;
    let mut result = Ok(());
    if let Ok(layout) = Layout::new(&schema, catalog, false) {
        let result = layout.gen_migration()?;
        fs::create_dir_all(output_dir)?;
        fs::write(format!("{}/up.sql", output_dir), result.0);
        fs::write(format!("{}/down.sql", output_dir),result.1);
        //Generate hasura request to track tables + relationships
        let mut hasura_tables : Vec<serde_json::Value> = Vec::new();
        let mut hasura_relations : Vec<serde_json::Value> = Vec::new();
        let mut hasura_down_relations : Vec<serde_json::Value> = Vec::new();
        let mut hasura_down_tables : Vec<serde_json::Value> = Vec::new();
        layout.tables.iter().for_each(|(name, table)| {
            hasura_tables.push(serde_json::json!({
                "type": "track_table",
                "args": {
                    "schema": "public",
                    "name": table.name.as_str()
                },
            }));
            hasura_down_tables.push(serde_json::json!({
                "type": "untrack_table",
                "args": {
                    "table" : {
                        "schema": "public",
                        "name": table.name.as_str()
                    },
                    "source": "default",
                    "cascade": true
                },
            }));
            /*
             * 21-07-27
             * vuviettai: hasura use create_object_relationship api to create relationship in DB
             * Migration sql already include this creation.
             */
            table.columns
                .iter()
                .filter(|col| col.is_reference())
                .for_each(|column|{
                    hasura_relations.push(serde_json::json!({
                        "type": "create_object_relationship",
                        "args": {
                            "table": table.name.as_str(),
                            "name": relational::named_type(&column.field_type),
                            "using" : {
                                "foreign_key_constraint_on" : column.name.as_str()
                            }
                        }
                    }));
                    hasura_down_relations.push(serde_json::json!({
                        "type": "drop_relationship",
                        "args": {
                            "relationship": relational::named_type(&column.field_type),
                            "source": "default",
                            "table": table.name.as_str()
                        }
                    }));
                    let ref_table = relational::named_type(&column.field_type).to_snake_case();
                    hasura_relations.push(serde_json::json!({
                        "type": "create_array_relationship",
                        "args": {
                            "name": table.name.as_str(),
                            "table": ref_table.clone(),
                            "using" : {
                                "foreign_key_constraint_on" : {
                                    "table": table.name.as_str(),
                                    "column": column.name.as_str()
                                }
                            }
                        }
                    }));
                    hasura_down_relations.push(serde_json::json!({
                        "type": "drop_relationship",
                        "args": {
                            "relationship": table.name.as_str(),
                            "source": "default",
                            "table": ref_table
                        }
                    }));
                });
        });
        hasura_tables.append(&mut hasura_relations);
        let bulk_up = serde_json::json!({
            "type": "bulk",
            "args" : hasura_tables
        });
        hasura_down_relations.append(&mut hasura_down_tables);
        if let Ok(payload) = serde_json::to_string(&bulk_up) {
            fs::write(format!("{}/hasura_queries.json", output_dir),
                      format!("{}", payload));
        }
        let bulk_down = serde_json::json!({
            "type": "bulk",
            "args" : hasura_down_relations
        });
        if let Ok(payload) = serde_json::to_string(&bulk_down) {
            fs::write(format!("{}/hasura_down.json", output_dir),
                      format!("{}", payload));
        }
    } else {
        result = Err(format!("Invalid schema").into())
    }
    result
}
