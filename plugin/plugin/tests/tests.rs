extern crate plugin;

use index_store::core::Store;
use massbit_chain_substrate::data_type::SubstrateBlock;
use node_template_runtime::{Block, DigestItem, Hash, Header};
use plugin::PluginManager;
use sp_runtime::Digest;
use std::str::FromStr;
use structmap::GenericMap;

const LIBPATH: &'static str = "../../target/debug/libtest_plugin.so";

fn make_helpers() {
    static ONCE: ::std::sync::Once = ::std::sync::Once::new();
    ONCE.call_once(|| {
        let rustc = std::env::var_os("CARGO").unwrap_or_else(|| "cargo".into());
        let mut cmd = ::std::process::Command::new(rustc);
        cmd.args(&["build", "--package", "test-plugin"]);
        assert!(cmd
            .status()
            .expect("could not compile the test helpers!")
            .success());
    });
}

fn new_substrate_block() -> SubstrateBlock {
    SubstrateBlock {
        version: String::new(),
        timestamp: 0,
        block: Block {
            header: Header {
                parent_hash: Hash::from_str(
                    "0x5611f005b55ffb1711eaf3b2f5557c788aa2e3d61b1a833f310f9c7e12a914f7",
                )
                .unwrap(),
                number: 610,
                state_root: Hash::from_str(
                    "0x173717683ea4459d15d532264aa7c51657cd65d204c033834ffa62f9ea69e78b",
                )
                .unwrap(),
                extrinsics_root: Hash::from_str(
                    "0x732ea723e3ff97289d22f2a4a52887329cd37c3b694a4d563979656d1aa6b7ee",
                )
                .unwrap(),
                digest: Digest {
                    logs: [DigestItem::ChangesTrieRoot(
                        Hash::from_str(
                            "0x173717683ea4459d15d532264aa7c51657cd65d204c033834ffa62f9ea69e78b",
                        )
                        .unwrap(),
                    )]
                    .to_vec(),
                },
            },
            extrinsics: [].to_vec(),
        },
        events: [].to_vec(),
    }
}

#[derive(Default)]
struct MockStore {}

impl Store for MockStore {
    fn save(&mut self, _entity_name: String, _data: GenericMap) {}
    fn flush(&mut self) {}
}

impl MockStore {
    fn new() -> MockStore {
        MockStore::default()
    }
}

#[ignore]
#[test]
fn test() {
    make_helpers();
    let mut store = MockStore::new();
    let block = new_substrate_block();
    unsafe {
        let mut plugins = PluginManager::new(&mut store);
        plugins.load("1234", LIBPATH).unwrap();
        assert_eq!(plugins.handle_substrate_block("1234", &block).unwrap(), ());
    }
}
