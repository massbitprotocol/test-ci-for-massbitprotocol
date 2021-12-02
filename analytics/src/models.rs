use crate::postgres_queries::UpsertConflictFragment;
use crate::relational::Table;
use crate::schema::*;
use massbit::prelude::Entity;

#[derive(Debug, Clone, Insertable, Queryable)]
#[table_name = "network_states"]
pub struct NetworkState {
    pub id: i64,
    pub chain: String,
    pub network: String,
    pub got_block: i64,
}

pub struct CommandData<'a> {
    pub table: &'a Table<'a>,
    pub values: &'a Vec<Entity>,
    pub conflict_fragment: &'a Option<UpsertConflictFragment<'a>>,
}
impl<'a> CommandData<'a> {
    pub fn new(
        table: &'a Table<'a>,
        values: &'a Vec<Entity>,
        conflict_fragment: &'a Option<UpsertConflictFragment<'a>>,
    ) -> Self {
        CommandData {
            table,
            values,
            conflict_fragment,
        }
    }
}
