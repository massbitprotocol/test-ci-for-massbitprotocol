from flask import Flask, request
from flask_cors import CORS, cross_origin
from shutil import copyfile
import urllib.parse
import random
import os
import subprocess
import threading
import ipfshttpclient
import requests
import re
from distutils.dir_util import copy_tree

################
# Config Flask #
################
app = Flask(__name__)
cors = CORS(app)


###################
# Helper function #
###################
class CargoCodegen(threading.Thread):
    """
    CargoCodgen is to create a new thread to build new code from schema.graphql & project.yml

    """

    def __init__(self, generated_folder):
        self.stdout = None
        self.stderr = None
        self.generated_folder = generated_folder
        threading.Thread.__init__(self)

    def run(self):
        try:
            # Config
            schema = os.path.join("src/schema.graphql")
            project = os.path.join("src/project.yaml")
            folder = os.path.join("src/")
            command = "$HOME/.cargo/bin/cargo run --manifest-path=../../../Cargo.toml --bin cli -- codegen -s {schema} -c {project} -o {folder} "\
                .format(schema = schema, project = project, folder = folder)
            print("Running: " + command)

            # Start
            output = subprocess.check_output([command], stderr=subprocess.STDOUT,
                                             shell=True, universal_newlines=True, cwd=self.generated_folder)
        except subprocess.CalledProcessError as exc:
            print("Codegen has failed. The result can be found in: " + self.generated_folder)
            write_to_disk(self.generated_folder + "/error-codegen.txt", exc.output)
        else:
            print("Codegen was success. The result can be found in: " + self.generated_folder)
            write_to_disk(self.generated_folder + "/success-codegen.txt", output)

def write_to_disk(file, data):
    """
    write_to_disk create and save the file to disk

    :param file: (String) path to the file + the file's name

    :param data: (String) raw data. Any data with "\ n" will be created as newline
    :return: (String) ok
    """
    f = open(file, "w+")
    f.write(data)
    return "ok"


def populate_stub(dst_dir, file_name):
    """
    populate_stub use the existing stub folder to populate the new folder with it's existing files

    :param dst_dir: (String) Path to directory we want to populate data

    :param file_name: (String) Path to the file + the file's name that we want to copy
    :return: (String) ok
    """
    print("Populating " + file_name + " from /stub")
    copyfile("./stub/" + file_name, dst_dir + "/" + file_name)


class CargoGenAndBuild(threading.Thread):
    """
    CargoBuild is class that will run `cargo build --release` in a new thread, not blocking the main thread

    """

    def __init__(self, generated_folder):
        self.stdout = None
        self.stderr = None
        self.generated_folder = generated_folder
        threading.Thread.__init__(self)

    def run(self):
        cargo_codegen = CargoCodegen(self.generated_folder)
        cargo_codegen.run()  # TODO: This still block the request

        print("Compiling...")
        try:
            # Docker container doesn't know about cargo path so we need to use $HOME
            output = subprocess.check_output(["$HOME/.cargo/bin/cargo build --release"], stderr=subprocess.STDOUT,
                                             shell=True, universal_newlines=True, cwd=self.generated_folder)
        except subprocess.CalledProcessError as exc:
            print("Compilation has failed. The result can be found in: " + self.generated_folder)
            write_to_disk(self.generated_folder + "/error.txt", exc.output)
        else:
            print("Compilation was success. The result can be found in: " + self.generated_folder)
            write_to_disk(self.generated_folder + "/success.txt", output)


@app.route("/compile", methods=['POST'])
@cross_origin()
def compile_handler():
    # Get data
    data = request.json

    # Random hash should be used as folder name for each new deployment
    hash = random.getrandbits(128)
    hash = "%032x" % hash
    generated_folder = "generated/" + hash

    # Create new folder
    os.mkdir(generated_folder)
    os.mkdir(generated_folder + "/src")

    # URL-decode the data
    mapping = urllib.parse.unquote_plus(data["mapping.rs"])
    project = urllib.parse.unquote_plus(data["project.yaml"])
    schema = urllib.parse.unquote_plus(data["schema.graphql"])

    # Populating stub data
    populate_stub(generated_folder, "Cargo.lock")
    populate_stub(generated_folder, "Cargo.toml")
    copy_tree("stub/target", generated_folder + "/target")

    # Save the formatted data from request to disk, ready for compiling
    write_to_disk(generated_folder + "/src/mapping.rs", mapping)
    write_to_disk(generated_folder + "/src/project.yaml", project)
    write_to_disk(generated_folder + "/src/schema.graphql", schema)

    # Codegen + Build
    print("Generating code + compiling for: " + hash + ". This will take a while!")
    cargo_gen_and_build = CargoGenAndBuild(generated_folder)
    cargo_gen_and_build.start()

    return {
               "status": "success",
               "payload": hash,
           }, 200


@app.route("/compile/status/<id>", methods=['GET'])
@cross_origin()
def compile_status_handler(id):
    generated_folder = "generated/" + id  # Where we'll be looking for the compilation status
    file = None
    status = None
    try:
        file = open(generated_folder + "/success.txt")
        status = "success"
    except IOError:
        print("Looking for success.txt file in " + generated_folder)

    try:
        file = open(generated_folder + "/error.txt")
        status = "error"
    except IOError:
        print("Looking for error.txt file in " + generated_folder)

    # If could not find success or error file, the compiling progress maybe is still in-progress
    if not file:
        return {
                   "status": "in-progress",
                   "payload": ""
               }, 200

    # Return compilation result to user
    print("Found " + status + ".txt file in " + generated_folder)
    payload = file.read()
    return {
               "status": status,
               "payload": payload
           }, 200


@app.route("/deploy", methods=['POST'])
@cross_origin()
def deploy_handler():
    # Get data
    data = request.json
    compilation_id = urllib.parse.unquote_plus(data["compilation_id"])

    # Get the files path from generated/hash folder
    project = os.path.join("./generated", compilation_id, "src/project.yaml")
    so = os.path.join("./generated", compilation_id, "target/release/libblock.so")
    schema = os.path.join("./generated", compilation_id, "src/schema.graphql")

    # Uploading files to IPFS
    if os.environ.get('IPFS_URL'):
        client = ipfshttpclient.connect(os.environ.get('IPFS_URL'))  # Connect with IPFS container name
    else:
        client = ipfshttpclient.connect()

    print("Uploading files to IPFS...")
    config_res = client.add(project)
    so_res = client.add(so)
    schema_res = client.add(schema)

    # Uploading to IPFS result
    print("project.yaml: " + config_res['Hash'])
    print("libblock.so: " + so_res['Hash'])
    print("schema.graphql: " + schema_res['Hash'])

    # Uploading IPFS files to Index Manager
    if os.environ.get('INDEX_MANAGER_URL'):
        index_manager_url = os.environ.get('INDEX_MANAGER_URL')  # Connection to indexer
    else:
        index_manager_url = 'http://0.0.0.0:3030'

    res = requests.post(index_manager_url,
                        json={
                            'jsonrpc': '2.0',
                            'method': 'index_deploy',
                            'params': [
                                config_res['Hash'],
                                so_res['Hash'],
                                schema_res['Hash']
                            ],
                            'id': 1,
                        })
    print(res.json())
    return {
               "status": "success",
               "payload": "",
           }, 200


@app.route('/', methods=['GET'])
@cross_origin()
def index():
    return "Code compiler server is up & running", 200


#################
# Mock Endpoint #
#################
@app.route("/mock/compile", methods=['POST'])
@cross_origin()
def mock_compile_handler():
    return {
               "status": "success",
               "payload": "mock_hash_success",
           }, 200


@app.route("/mock/compile/status/<id>", methods=['GET'])
@cross_origin()
def mock_compile_status_handler(id):
    return {
               "status": "success",
               "payload": "%20%20%20Compiling%20proc-macro2%20v1.0.24%0A%20%20%20Compiling%20unicode-xid%20v0.2.1%0A%20%20%20Compiling%20syn%20v1.0.64%0A%20%20%20Compiling%20libc%20v0.2.97%0A%20%20%20Compiling%20cfg-if%20v1.0.0%0A%20%20%20Compiling%20autocfg%20v1.0.1%0A%20%20%20Compiling%20serde%20v1.0.126%0A%20%20%20Compiling%20serde_derive%20v1.0.126%0A%20%20%20Compiling%20memchr%20v2.3.4%0A%20%20%20Compiling%20value-bag%20v1.0.0-alpha.6%0A%20%20%20Compiling%20log%20v0.4.14%0A%20%20%20Compiling%20typenum%20v1.13.0%0A%20%20%20Compiling%20smallvec%20v1.6.1%0A%20%20%20Compiling%20scopeguard%20v1.1.0%0A%20%20%20Compiling%20byteorder%20v1.4.3%0A%20%20%20Compiling%20getrandom%20v0.2.2%0A%20%20%20Compiling%20pin-project-lite%20v0.2.6%0A%20%20%20Compiling%20futures-core%20v0.3.13%0A%20%20%20Compiling%20ppv-lite86%20v0.2.10%0A%20%20%20Compiling%20version_check%20v0.9.3%0A%20%20%20Compiling%20getrandom%20v0.1.16%0A%20%20%20Compiling%20radium%20v0.6.2%0A%20%20%20Compiling%20proc-macro-hack%20v0.5.19%0A%20%20%20Compiling%20futures-io%20v0.3.13%0A%20%20%20Compiling%20libm%20v0.2.1%0A%20%20%20Compiling%20lazy_static%20v1.4.0%0A%20%20%20Compiling%20proc-macro-nested%20v0.1.7%0A%20%20%20Compiling%20futures-sink%20v0.3.13%0A%20%20%20Compiling%20crunchy%20v0.2.2%0A%20%20%20Compiling%20static_assertions%20v1.1.0%0A%20%20%20Compiling%20funty%20v1.1.0%0A%20%20%20Compiling%20anyhow%20v1.0.38%0A%20%20%20Compiling%20slab%20v0.4.2%0A%20%20%20Compiling%20pin-utils%20v0.1.0%0A%20%20%20Compiling%20tap%20v1.0.1%0A%20%20%20Compiling%20wyz%20v0.2.0%0A%20%20%20Compiling%20arrayvec%20v0.7.0%0A%20%20%20Compiling%20byte-slice-cast%20v1.0.0%0A%20%20%20Compiling%20futures-task%20v0.3.13%0A%20%20%20Compiling%20subtle%20v2.4.0%0A%20%20%20Compiling%20ryu%20v1.0.5%0A%20%20%20Compiling%20cfg-if%20v0.1.10%0A%20%20%20Compiling%20ahash%20v0.4.7%0A%20%20%20Compiling%20serde_json%20v1.0.64%0A%20%20%20Compiling%20regex-syntax%20v0.6.23%0A%20%20%20Compiling%20subtle%20v1.0.0%0A%20%20%20Compiling%20byte-tools%20v0.3.1%0A%20%20%20Compiling%20itoa%20v0.4.7%0A%20%20%20Compiling%20tinyvec_macros%20v0.1.0%0A%20%20%20Compiling%20rustc-hex%20v2.1.0%0A%20%20%20Compiling%20cpuid-bool%20v0.1.2%0A%20%20%20Compiling%20opaque-debug%20v0.3.0%0A%20%20%20Compiling%20opaque-debug%20v0.2.3%0A%20%20%20Compiling%20arrayvec%20v0.4.12%0A%20%20%20Compiling%20arrayref%20v0.3.6%0A%20%20%20Compiling%20fake-simd%20v0.1.2%0A%20%20%20Compiling%20hex%20v0.4.3%0A%20%20%20Compiling%20sp-std%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20zstd-safe%20v3.0.1%2Bzstd.1.4.9%0A%20%20%20Compiling%20ref-cast%20v1.0.6%0A%20%20%20Compiling%20parity-util-mem%20v0.9.0%0A%20%20%20Compiling%20signature%20v1.3.0%0A%20%20%20Compiling%20parity-wasm%20v0.41.0%0A%20%20%20Compiling%20slog%20v2.7.0%0A%20%20%20Compiling%20hash-db%20v0.15.2%0A%20%20%20Compiling%20memory_units%20v0.3.0%0A%20%20%20Compiling%20ansi_term%20v0.12.1%0A%20%20%20Compiling%20keccak%20v0.1.0%0A%20%20%20Compiling%20arrayvec%20v0.5.2%0A%20%20%20Compiling%20environmental%20v1.1.3%0A%20%20%20Compiling%20tiny-keccak%20v2.0.2%0A%20%20%20Compiling%20nodrop%20v0.1.14%0A%20%20%20Compiling%20rustc-hash%20v1.1.0%0A%20%20%20Compiling%20dyn-clone%20v1.0.4%0A%20%20%20Compiling%20constant_time_eq%20v0.1.5%0A%20%20%20Compiling%20base58%20v0.1.0%0A%20%20%20Compiling%20gimli%20v0.23.0%0A%20%20%20Compiling%20adler%20v1.0.2%0A%20%20%20Compiling%20async-trait%20v0.1.48%0A%20%20%20Compiling%20object%20v0.23.0%0A%20%20%20Compiling%20rustc-demangle%20v0.1.18%0A%20%20%20Compiling%20either%20v1.6.1%0A%20%20%20Compiling%20paste%20v1.0.5%0A%20%20%20Compiling%20bitflags%20v1.2.1%0A%20%20%20Compiling%20fnv%20v1.0.7%0A%20%20%20Compiling%20remove_dir_all%20v0.5.3%0A%20%20%20Compiling%20futures-timer%20v3.0.2%0A%20%20%20Compiling%20bytes%20v1.0.1%0A%20%20%20Compiling%20cache-padded%20v1.1.1%0A%20%20%20Compiling%20matches%20v0.1.8%0A%20%20%20Compiling%20parking%20v2.0.0%0A%20%20%20Compiling%20event-listener%20v2.5.1%0A%20%20%20Compiling%20waker-fn%20v1.1.0%0A%20%20%20Compiling%20fastrand%20v1.4.0%0A%20%20%20Compiling%20fixedbitset%20v0.2.0%0A%20%20%20Compiling%20pin-project-internal%20v0.4.27%0A%20%20%20Compiling%20unicode-segmentation%20v1.7.1%0A%20%20%20Compiling%20vec-arena%20v1.0.0%0A%20%20%20Compiling%20bytes%20v0.5.6%0A%20%20%20Compiling%20multimap%20v0.8.3%0A%20%20%20Compiling%20percent-encoding%20v2.1.0%0A%20%20%20Compiling%20pin-project-lite%20v0.1.12%0A%20%20%20Compiling%20async-task%20v4.0.3%0A%20%20%20Compiling%20unsigned-varint%20v0.5.1%0A%20%20%20Compiling%20rawpointer%20v0.2.1%0A%20%20%20Compiling%20signal-hook%20v0.3.7%0A%20%20%20Compiling%20unsigned-varint%20v0.7.0%0A%20%20%20Compiling%20bs58%20v0.4.0%0A%20%20%20Compiling%20prometheus%20v0.11.0%0A%20%20%20Compiling%20httparse%20v1.4.1%0A%20%20%20Compiling%20data-encoding%20v2.3.2%0A%20%20%20Compiling%20spin%20v0.5.2%0A%20%20%20Compiling%20atomic-waker%20v1.0.0%0A%20%20%20Compiling%20untrusted%20v0.7.1%0A%20%20%20Compiling%20asn1_der%20v0.7.4%0A%20%20%20Compiling%20void%20v1.0.2%0A%20%20%20Compiling%20try-lock%20v0.2.3%0A%20%20%20Compiling%20ucd-trie%20v0.1.3%0A%20%20%20Compiling%20tower-service%20v0.3.1%0A%20%20%20Compiling%20httpdate%20v0.3.2%0A%20%20%20Compiling%20camino%20v1.0.4%0A%20%20%20Compiling%20semver-parser%20v0.7.0%0A%20%20%20Compiling%20same-file%20v1.0.6%0A%20%20%20Compiling%20pq-sys%20v0.4.6%0A%20%20%20Compiling%20safe-mix%20v1.0.1%0A%20%20%20Compiling%20instant%20v0.1.9%0A%20%20%20Compiling%20lock_api%20v0.4.2%0A%20%20%20Compiling%20lock_api%20v0.3.4%0A%20%20%20Compiling%20futures-channel%20v0.3.13%0A%20%20%20Compiling%20tracing-core%20v0.1.17%0A%20%20%20Compiling%20sharded-slab%20v0.1.1%0A%20%20%20Compiling%20block-padding%20v0.1.5%0A%20%20%20Compiling%20num-traits%20v0.2.14%0A%20%20%20Compiling%20num-integer%20v0.1.44%0A%20%20%20Compiling%20num-bigint%20v0.2.6%0A%20%20%20Compiling%20num-rational%20v0.2.4%0A%20%20%20Compiling%20miniz_oxide%20v0.4.4%0A%20%20%20Compiling%20indexmap%20v1.6.2%0A%20%20%20Compiling%20num-complex%20v0.2.4%0A%20%20%20Compiling%20crossbeam-utils%20v0.8.3%0A%20%20%20Compiling%20atomic%20v0.5.0%0A%20%20%20Compiling%20tinyvec%20v1.1.1%0A%20%20%20Compiling%20generic-array%20v0.14.4%0A%20%20%20Compiling%20proc-macro-error-attr%20v1.0.4%0A%20%20%20Compiling%20proc-macro-error%20v1.0.4%0A%20%20%20Compiling%20hashbrown%20v0.9.1%0A%20%20%20Compiling%20trie-root%20v0.16.0%0A%20%20%20Compiling%20ed25519%20v1.0.3%0A%20%20%20Compiling%20concurrent-queue%20v1.2.2%0A%20%20%20Compiling%20itertools%20v0.9.0%0A%20%20%20Compiling%20unicode-bidi%20v0.3.4%0A%20%20%20Compiling%20async-mutex%20v1.4.0%0A%20%20%20Compiling%20async-lock%20v2.3.0%0A%20%20%20Compiling%20matrixmultiply%20v0.2.4%0A%20%20%20Compiling%20form_urlencoded%20v1.0.1%0A%20%20%20Compiling%20http%20v0.2.3%0A%20%20%20Compiling%20heck%20v0.3.2%0A%20%20%20Compiling%20pest%20v2.1.3%0A%20%20%20Compiling%20walkdir%20v2.3.2%0A%20%20%20Compiling%20semver%20v0.6.0%0A%20%20%20Compiling%20parity-wasm%20v0.32.0%0A%20%20%20Compiling%20wasmi-validation%20v0.3.0%0A%20%20%20Compiling%20async-channel%20v1.6.1%0A%20%20%20Compiling%20lru%20v0.6.5%0A%20%20%20Compiling%20uint%20v0.9.0%0A%20%20%20Compiling%20hash256-std-hasher%20v0.15.2%0A%20%20%20Compiling%20bitvec%20v0.20.2%0A%20%20%20Compiling%20build-helper%20v0.1.1%0A%20%20%20Compiling%20aho-corasick%20v0.7.15%0A%20%20%20Compiling%20futures-lite%20v1.11.3%0A%20%20%20Compiling%20tokio%20v0.2.25%0A%20%20%20Compiling%20regex-automata%20v0.1.9%0A%20%20%20Compiling%20unicode-normalization%20v0.1.17%0A%20%20%20Compiling%20quote%20v1.0.9%0A%20%20%20Compiling%20jobserver%20v0.1.22%0A%20%20%20Compiling%20atty%20v0.2.14%0A%20%20%20Compiling%20parking_lot_core%20v0.8.3%0A%20%20%20Compiling%20num_cpus%20v1.13.0%0A%20%20%20Compiling%20parking_lot_core%20v0.7.2%0A%20%20%20Compiling%20socket2%20v0.3.19%0A%20%20%20Compiling%20signal-hook-registry%20v1.3.0%0A%20%20%20Compiling%20semver-parser%20v0.10.2%0A%20%20%20Compiling%20addr2line%20v0.14.1%0A%20%20%20Compiling%20blake2-rfc%20v0.2.18%0A%20%20%20Compiling%20generic-array%20v0.12.4%0A%20%20%20Compiling%20generic-array%20v0.13.3%0A%20%20%20Compiling%20cc%20v1.0.67%0A%20%20%20Compiling%20paste-impl%20v0.1.18%0A%20%20%20Compiling%20http-body%20v0.3.1%0A%20%20%20Compiling%20rand_core%20v0.6.2%0A%20%20%20Compiling%20petgraph%20v0.5.1%0A%20%20%20Compiling%20rand_core%20v0.5.1%0A%20%20%20Compiling%20parking_lot%20v0.11.1%0A%20%20%20Compiling%20regex%20v1.4.5%0A%20%20%20Compiling%20parking_lot%20v0.10.2%0A%20%20%20Compiling%20nb-connect%20v1.0.3%0A%20%20%20Compiling%20matchers%20v0.0.1%0A%20%20%20Compiling%20backtrace%20v0.3.56%0A%20%20%20Compiling%20idna%20v0.2.2%0A%20%20%20Compiling%20integer-sqrt%20v0.1.5%0A%20%20%20Compiling%20approx%20v0.3.2%0A%20%20%20Compiling%20digest%20v0.8.1%0A%20%20%20Compiling%20crypto-mac%20v0.7.0%0A%20%20%20Compiling%20block-buffer%20v0.7.3%0A%20%20%20Compiling%20digest%20v0.9.0%0A%20%20%20Compiling%20block-buffer%20v0.9.0%0A%20%20%20Compiling%20crypto-mac%20v0.8.0%0A%20%20%20Compiling%20rand_chacha%20v0.3.0%0A%20%20%20Compiling%20rand_pcg%20v0.2.1%0A%20%20%20Compiling%20rand_chacha%20v0.2.2%0A%20%20%20Compiling%20once_cell%20v1.7.2%0A%20%20%20Compiling%20paste%20v0.1.18%0A%20%20%20Compiling%20sp-panic-handler%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20hmac%20v0.7.1%0A%20%20%20Compiling%20pbkdf2%20v0.3.0%0A%20%20%20Compiling%20sha2%20v0.8.2%0A%20%20%20Compiling%20chrono%20v0.4.19%0A%20%20%20Compiling%20sha2%20v0.9.3%0A%20%20%20Compiling%20pbkdf2%20v0.4.0%0A%20%20%20Compiling%20hmac%20v0.8.1%0A%20%20%20Compiling%20thread_local%20v1.1.3%0A%20%20%20Compiling%20blocking%20v1.0.2%0A%20%20%20Compiling%20async-executor%20v1.4.0%0A%20%20%20Compiling%20rand%20v0.7.3%0A%20%20%20Compiling%20simba%20v0.1.5%0A%20%20%20Compiling%20rand%20v0.8.3%0A%20%20%20Compiling%20url%20v2.2.1%0A%20%20%20Compiling%20hmac-drbg%20v0.2.0%0A%20%20%20Compiling%20zstd-sys%20v1.4.20%2Bzstd.1.4.9%0A%20%20%20Compiling%20ring%20v0.16.20%0A%20%20%20Compiling%20libloading%20v0.5.2%0A%20%20%20Compiling%20Inflector%20v0.11.4%0A%20%20%20Compiling%20fixed-hash%20v0.7.0%0A%20%20%20Compiling%20tempfile%20v3.2.0%0A%20%20%20Compiling%20libsecp256k1%20v0.3.5%0A%20%20%20Compiling%20twox-hash%20v1.6.0%0A%20%20%20Compiling%20rand_distr%20v0.2.2%0A%20%20%20Compiling%20statrs%20v0.12.0%0A%20%20%20Compiling%20wasmi%20v0.6.2%0A%20%20%20Compiling%20nalgebra%20v0.21.1%0A%20%20%20Compiling%20synstructure%20v0.12.4%0A%20%20%20Compiling%20ctor%20v0.1.19%0A%20%20%20Compiling%20thiserror-impl%20v1.0.24%0A%20%20%20Compiling%20futures-macro%20v0.3.13%0A%20%20%20Compiling%20zeroize_derive%20v1.0.1%0A%20%20%20Compiling%20tracing-attributes%20v0.1.15%0A%20%20%20Compiling%20impl-trait-for-tuples%20v0.2.1%0A%20%20%20Compiling%20ref-cast-impl%20v1.0.6%0A%20%20%20Compiling%20sp-debug-derive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20parity-util-mem-derive%20v0.1.0%0A%20%20%20Compiling%20dyn-clonable-impl%20v0.9.0%0A%20%20%20Compiling%20derive_more%20v0.99.11%0A%20%20%20Compiling%20frame-support-procedural-tools-derive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20prost-derive%20v0.7.0%0A%20%20%20Compiling%20pin-project-internal%20v1.0.5%0A%20%20%20Compiling%20libp2p-swarm-derive%20v0.23.0%0A%20%20%20Compiling%20diesel_derives%20v1.4.1%0A%20%20%20Compiling%20dyn-clonable%20v0.9.0%0A%20%20%20Compiling%20zeroize%20v1.2.0%0A%20%20%20Compiling%20futures-util%20v0.3.13%0A%20%20%20Compiling%20curve25519-dalek%20v3.0.2%0A%20%20%20Compiling%20merlin%20v2.0.1%0A%20%20%20Compiling%20curve25519-dalek%20v2.1.2%0A%20%20%20Compiling%20secrecy%20v0.7.0%0A%20%20%20Compiling%20thiserror%20v1.0.24%0A%20%20%20Compiling%20tracing-log%20v0.1.2%0A%20%20%20Compiling%20trie-db%20v0.22.3%0A%20%20%20Compiling%20polling%20v2.0.2%0A%20%20%20Compiling%20tokio-util%20v0.3.1%0A%20%20%20Compiling%20want%20v0.3.0%0A%20%20%20Compiling%20kv-log-macro%20v1.0.7%0A%20%20%20Compiling%20wasm-gc-api%20v0.1.11%0A%20%20%20Compiling%20tracing%20v0.1.25%0A%20%20%20Compiling%20which%20v4.0.2%0A%20%20%20Compiling%20tiny-bip39%20v0.8.0%0A%20%20%20Compiling%20pin-project%20v1.0.5%0A%20%20%20Compiling%20async-io%20v1.3.1%0A%20%20%20Compiling%20tracing-futures%20v0.2.5%0A%20%20%20Compiling%20pin-project%20v0.4.27%0A%20%20%20Compiling%20prost%20v0.7.0%0A%20%20%20Compiling%20prost-build%20v0.7.0%0A%20%20%20Compiling%20async-global-executor%20v2.0.2%0A%20%20%20Compiling%20async-process%20v1.0.2%0A%20%20%20Compiling%20async-std%20v1.9.0%0A%20%20%20Compiling%20prost-types%20v0.7.0%0A%20%20%20Compiling%20diesel%20v1.4.7%0A%20%20%20Compiling%20futures-executor%20v0.3.13%0A%20%20%20Compiling%20h2%20v0.2.7%0A%20%20%20Compiling%20futures%20v0.3.13%0A%20%20%20Compiling%20wasm-timer%20v0.2.5%0A%20%20%20Compiling%20multistream-select%20v0.10.2%0A%20%20%20Compiling%20rw-stream-sink%20v0.2.1%0A%20%20%20Compiling%20sp-utils%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p-core%20v0.28.3%0A%20%20%20Compiling%20linregress%20v0.4.0%0A%20%20%20Compiling%20zstd%20v0.6.1%2Bzstd.1.4.9%0A%20%20%20Compiling%20sp-maybe-compressed-blob%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20hyper%20v0.13.10%0A%20%20%20Compiling%20impl-serde%20v0.3.1%0A%20%20%20Compiling%20tracing-serde%20v0.1.2%0A%20%20%20Compiling%20erased-serde%20v0.3.13%0A%20%20%20Compiling%20ed25519-dalek%20v1.0.1%0A%20%20%20Compiling%20schnorrkel%20v0.9.1%0A%20%20%20Compiling%20toml%20v0.5.8%0A%20%20%20Compiling%20cargo-platform%20v0.1.1%0A%20%20%20Compiling%20semver%20v0.11.0%0A%20%20%20Compiling%20substrate-bip39%20v0.4.2%0A%20%20%20Compiling%20tracing-subscriber%20v0.2.17%0A%20%20%20Compiling%20cargo_metadata%20v0.13.1%0A%20%20%20Compiling%20proc-macro-crate%20v0.1.5%0A%20%20%20Compiling%20proc-macro-crate%20v1.0.0%0A%20%20%20Compiling%20frame-support-procedural-tools%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20parity-scale-codec-derive%20v2.1.0%0A%20%20%20Compiling%20sp-runtime-interface-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-api-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20multihash-derive%20v0.7.1%0A%20%20%20Compiling%20frame-support-procedural%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20substrate-wasm-builder%20v4.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20multihash%20v0.13.2%0A%20%20%20Compiling%20node-template-runtime%20v3.0.0%20%28https%3A//github.com/scs/substrate-api-client-test-node%3Fbranch%3Dbump-to-polkadot-v0.9.2%237d14a36d%29%0A%20%20%20Compiling%20parity-scale-codec%20v2.1.1%0A%20%20%20Compiling%20parity-multiaddr%20v0.11.2%0A%20%20%20Compiling%20substrate-prometheus-endpoint%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20impl-codec%20v0.5.0%0A%20%20%20Compiling%20sp-storage%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-tracing%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-wasm-interface%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-arithmetic%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20finality-grandpa%20v0.14.0%0A%20%20%20Compiling%20sp-version-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20primitive-types%20v0.9.0%0A%20%20%20Compiling%20sp-externalities%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-runtime-interface%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p-swarm%20v0.29.0%0A%20%20%20Compiling%20memory-db%20v0.26.0%0A%20%20%20Compiling%20kvdb%20v0.9.0%0A%20%20%20Compiling%20sp-database%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-core%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p%20v0.37.1%0A%20%20%20Compiling%20sp-trie%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-keystore%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-metadata%20v13.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-state-machine%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-io%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20index-store%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/massbit-core/index-store%29%0A%20%20%20Compiling%20sp-application-crypto%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-runtime%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-version%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-inherents%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-staking%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus-slots%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-session%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-finality-grandpa%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-offchain%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-system-rpc-runtime-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-support%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-timestamp%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-authorship%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-block-builder%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-blockchain%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus-aura%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-transaction-pool%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-system%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-benchmarking%20v3.1.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-transaction-payment%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-authorship%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-randomness-collective-flip%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-executive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-sudo%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-timestamp%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-template%20v3.0.0%20%28https%3A//github.com/scs/substrate-api-client-test-node%3Fbranch%3Dbump-to-polkadot-v0.9.2%237d14a36d%29%0A%20%20%20Compiling%20pallet-balances%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-transaction-payment-rpc-runtime-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-session%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-grandpa%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-aura%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20massbit-chain-substrate%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/massbit-core/chain/substrate%29%0A%20%20%20Compiling%20plugin%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/plugin%29%0A%20%20%20Compiling%20block%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/code-compiler/generated/b72fdaa4d4301abd55540723e56b92c6%29%0Awarning%3A%20unused%20import%3A%20%60diesel%3A%3Apg%3A%3APgConnection%60%0A%20--%3E%20src/mapping.rs%3A3%3A5%0A%20%20%7C%0A3%20%7C%20use%20diesel%3A%3Apg%3A%3APgConnection%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%20%20%7C%0A%20%20%3D%20note%3A%20%60%23%5Bwarn%28unused_imports%29%5D%60%20on%20by%20default%0A%0Awarning%3A%20unused%20import%3A%20%60diesel%3A%3Aprelude%3A%3A%2A%60%0A%20--%3E%20src/mapping.rs%3A4%3A5%0A%20%20%7C%0A4%20%7C%20use%20diesel%3A%3Aprelude%3A%3A%2A%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20unused%20import%3A%20%60std%3A%3Aenv%60%0A%20--%3E%20src/mapping.rs%3A7%3A5%0A%20%20%7C%0A7%20%7C%20use%20std%3A%3Aenv%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20unused%20imports%3A%20%60Connection%60%2C%20%60PgConnection%60%2C%20%60RunQueryDsl%60%0A%20--%3E%20src/models.rs%3A2%3A14%0A%20%20%7C%0A2%20%7C%20use%20diesel%3A%3A%7BPgConnection%2C%20Connection%2C%20RunQueryDsl%7D%3B%0A%20%20%7C%20%20%20%20%20%20%20%20%20%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20%60extern%60%20fn%20uses%20type%20%60dyn%20PluginRegistrar%60%2C%20which%20is%20not%20FFI-safe%0A%20%20--%3E%20src/lib.rs%3A13%3A35%0A%20%20%20%7C%0A13%20%7C%20extern%20%22C%22%20fn%20register%28registrar%3A%20%26mut%20dyn%20PluginRegistrar%29%20%7B%0A%20%20%20%7C%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20not%20FFI-safe%0A%20%20%20%7C%0A%20%20%20%3D%20note%3A%20%60%23%5Bwarn%28improper_ctypes_definitions%29%5D%60%20on%20by%20default%0A%20%20%20%3D%20note%3A%20trait%20objects%20have%20no%20C%20equivalent%0A%0Awarning%3A%205%20warnings%20emitted%0A%0A%20%20%20%20Finished%20release%20%5Boptimized%5D%20target%28s%29%20in%201m%2007s%0A"
           }, 200


@app.route("/mock/deploy", methods=['POST'])
@cross_origin()
def mock_deploy_handler():
    return {
               "status": "success",
               "payload": "dfd8648a4a0cfc56f99d435a0e6e48c3",
           }, 200


@app.route("/mock/deploy/status/<id>", methods=['GET'])
@cross_origin()
def mock_deploy_status_handler(id):
    return {
               "status": "success",
               "payload": "%20%20%20Compiling%20proc-macro2%20v1.0.24%0A%20%20%20Compiling%20unicode-xid%20v0.2.1%0A%20%20%20Compiling%20syn%20v1.0.64%0A%20%20%20Compiling%20libc%20v0.2.97%0A%20%20%20Compiling%20cfg-if%20v1.0.0%0A%20%20%20Compiling%20autocfg%20v1.0.1%0A%20%20%20Compiling%20serde%20v1.0.126%0A%20%20%20Compiling%20serde_derive%20v1.0.126%0A%20%20%20Compiling%20memchr%20v2.3.4%0A%20%20%20Compiling%20value-bag%20v1.0.0-alpha.6%0A%20%20%20Compiling%20log%20v0.4.14%0A%20%20%20Compiling%20typenum%20v1.13.0%0A%20%20%20Compiling%20smallvec%20v1.6.1%0A%20%20%20Compiling%20scopeguard%20v1.1.0%0A%20%20%20Compiling%20byteorder%20v1.4.3%0A%20%20%20Compiling%20getrandom%20v0.2.2%0A%20%20%20Compiling%20pin-project-lite%20v0.2.6%0A%20%20%20Compiling%20futures-core%20v0.3.13%0A%20%20%20Compiling%20ppv-lite86%20v0.2.10%0A%20%20%20Compiling%20version_check%20v0.9.3%0A%20%20%20Compiling%20getrandom%20v0.1.16%0A%20%20%20Compiling%20radium%20v0.6.2%0A%20%20%20Compiling%20proc-macro-hack%20v0.5.19%0A%20%20%20Compiling%20futures-io%20v0.3.13%0A%20%20%20Compiling%20libm%20v0.2.1%0A%20%20%20Compiling%20lazy_static%20v1.4.0%0A%20%20%20Compiling%20proc-macro-nested%20v0.1.7%0A%20%20%20Compiling%20futures-sink%20v0.3.13%0A%20%20%20Compiling%20crunchy%20v0.2.2%0A%20%20%20Compiling%20static_assertions%20v1.1.0%0A%20%20%20Compiling%20funty%20v1.1.0%0A%20%20%20Compiling%20anyhow%20v1.0.38%0A%20%20%20Compiling%20slab%20v0.4.2%0A%20%20%20Compiling%20pin-utils%20v0.1.0%0A%20%20%20Compiling%20tap%20v1.0.1%0A%20%20%20Compiling%20wyz%20v0.2.0%0A%20%20%20Compiling%20arrayvec%20v0.7.0%0A%20%20%20Compiling%20byte-slice-cast%20v1.0.0%0A%20%20%20Compiling%20futures-task%20v0.3.13%0A%20%20%20Compiling%20subtle%20v2.4.0%0A%20%20%20Compiling%20ryu%20v1.0.5%0A%20%20%20Compiling%20cfg-if%20v0.1.10%0A%20%20%20Compiling%20ahash%20v0.4.7%0A%20%20%20Compiling%20serde_json%20v1.0.64%0A%20%20%20Compiling%20regex-syntax%20v0.6.23%0A%20%20%20Compiling%20subtle%20v1.0.0%0A%20%20%20Compiling%20byte-tools%20v0.3.1%0A%20%20%20Compiling%20itoa%20v0.4.7%0A%20%20%20Compiling%20tinyvec_macros%20v0.1.0%0A%20%20%20Compiling%20rustc-hex%20v2.1.0%0A%20%20%20Compiling%20cpuid-bool%20v0.1.2%0A%20%20%20Compiling%20opaque-debug%20v0.3.0%0A%20%20%20Compiling%20opaque-debug%20v0.2.3%0A%20%20%20Compiling%20arrayvec%20v0.4.12%0A%20%20%20Compiling%20arrayref%20v0.3.6%0A%20%20%20Compiling%20fake-simd%20v0.1.2%0A%20%20%20Compiling%20hex%20v0.4.3%0A%20%20%20Compiling%20sp-std%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20zstd-safe%20v3.0.1%2Bzstd.1.4.9%0A%20%20%20Compiling%20ref-cast%20v1.0.6%0A%20%20%20Compiling%20parity-util-mem%20v0.9.0%0A%20%20%20Compiling%20signature%20v1.3.0%0A%20%20%20Compiling%20parity-wasm%20v0.41.0%0A%20%20%20Compiling%20slog%20v2.7.0%0A%20%20%20Compiling%20hash-db%20v0.15.2%0A%20%20%20Compiling%20memory_units%20v0.3.0%0A%20%20%20Compiling%20ansi_term%20v0.12.1%0A%20%20%20Compiling%20keccak%20v0.1.0%0A%20%20%20Compiling%20arrayvec%20v0.5.2%0A%20%20%20Compiling%20environmental%20v1.1.3%0A%20%20%20Compiling%20tiny-keccak%20v2.0.2%0A%20%20%20Compiling%20nodrop%20v0.1.14%0A%20%20%20Compiling%20rustc-hash%20v1.1.0%0A%20%20%20Compiling%20dyn-clone%20v1.0.4%0A%20%20%20Compiling%20constant_time_eq%20v0.1.5%0A%20%20%20Compiling%20base58%20v0.1.0%0A%20%20%20Compiling%20gimli%20v0.23.0%0A%20%20%20Compiling%20adler%20v1.0.2%0A%20%20%20Compiling%20async-trait%20v0.1.48%0A%20%20%20Compiling%20object%20v0.23.0%0A%20%20%20Compiling%20rustc-demangle%20v0.1.18%0A%20%20%20Compiling%20either%20v1.6.1%0A%20%20%20Compiling%20paste%20v1.0.5%0A%20%20%20Compiling%20bitflags%20v1.2.1%0A%20%20%20Compiling%20fnv%20v1.0.7%0A%20%20%20Compiling%20remove_dir_all%20v0.5.3%0A%20%20%20Compiling%20futures-timer%20v3.0.2%0A%20%20%20Compiling%20bytes%20v1.0.1%0A%20%20%20Compiling%20cache-padded%20v1.1.1%0A%20%20%20Compiling%20matches%20v0.1.8%0A%20%20%20Compiling%20parking%20v2.0.0%0A%20%20%20Compiling%20event-listener%20v2.5.1%0A%20%20%20Compiling%20waker-fn%20v1.1.0%0A%20%20%20Compiling%20fastrand%20v1.4.0%0A%20%20%20Compiling%20fixedbitset%20v0.2.0%0A%20%20%20Compiling%20pin-project-internal%20v0.4.27%0A%20%20%20Compiling%20unicode-segmentation%20v1.7.1%0A%20%20%20Compiling%20vec-arena%20v1.0.0%0A%20%20%20Compiling%20bytes%20v0.5.6%0A%20%20%20Compiling%20multimap%20v0.8.3%0A%20%20%20Compiling%20percent-encoding%20v2.1.0%0A%20%20%20Compiling%20pin-project-lite%20v0.1.12%0A%20%20%20Compiling%20async-task%20v4.0.3%0A%20%20%20Compiling%20unsigned-varint%20v0.5.1%0A%20%20%20Compiling%20rawpointer%20v0.2.1%0A%20%20%20Compiling%20signal-hook%20v0.3.7%0A%20%20%20Compiling%20unsigned-varint%20v0.7.0%0A%20%20%20Compiling%20bs58%20v0.4.0%0A%20%20%20Compiling%20prometheus%20v0.11.0%0A%20%20%20Compiling%20httparse%20v1.4.1%0A%20%20%20Compiling%20data-encoding%20v2.3.2%0A%20%20%20Compiling%20spin%20v0.5.2%0A%20%20%20Compiling%20atomic-waker%20v1.0.0%0A%20%20%20Compiling%20untrusted%20v0.7.1%0A%20%20%20Compiling%20asn1_der%20v0.7.4%0A%20%20%20Compiling%20void%20v1.0.2%0A%20%20%20Compiling%20try-lock%20v0.2.3%0A%20%20%20Compiling%20ucd-trie%20v0.1.3%0A%20%20%20Compiling%20tower-service%20v0.3.1%0A%20%20%20Compiling%20httpdate%20v0.3.2%0A%20%20%20Compiling%20camino%20v1.0.4%0A%20%20%20Compiling%20semver-parser%20v0.7.0%0A%20%20%20Compiling%20same-file%20v1.0.6%0A%20%20%20Compiling%20pq-sys%20v0.4.6%0A%20%20%20Compiling%20safe-mix%20v1.0.1%0A%20%20%20Compiling%20instant%20v0.1.9%0A%20%20%20Compiling%20lock_api%20v0.4.2%0A%20%20%20Compiling%20lock_api%20v0.3.4%0A%20%20%20Compiling%20futures-channel%20v0.3.13%0A%20%20%20Compiling%20tracing-core%20v0.1.17%0A%20%20%20Compiling%20sharded-slab%20v0.1.1%0A%20%20%20Compiling%20block-padding%20v0.1.5%0A%20%20%20Compiling%20num-traits%20v0.2.14%0A%20%20%20Compiling%20num-integer%20v0.1.44%0A%20%20%20Compiling%20num-bigint%20v0.2.6%0A%20%20%20Compiling%20num-rational%20v0.2.4%0A%20%20%20Compiling%20miniz_oxide%20v0.4.4%0A%20%20%20Compiling%20indexmap%20v1.6.2%0A%20%20%20Compiling%20num-complex%20v0.2.4%0A%20%20%20Compiling%20crossbeam-utils%20v0.8.3%0A%20%20%20Compiling%20atomic%20v0.5.0%0A%20%20%20Compiling%20tinyvec%20v1.1.1%0A%20%20%20Compiling%20generic-array%20v0.14.4%0A%20%20%20Compiling%20proc-macro-error-attr%20v1.0.4%0A%20%20%20Compiling%20proc-macro-error%20v1.0.4%0A%20%20%20Compiling%20hashbrown%20v0.9.1%0A%20%20%20Compiling%20trie-root%20v0.16.0%0A%20%20%20Compiling%20ed25519%20v1.0.3%0A%20%20%20Compiling%20concurrent-queue%20v1.2.2%0A%20%20%20Compiling%20itertools%20v0.9.0%0A%20%20%20Compiling%20unicode-bidi%20v0.3.4%0A%20%20%20Compiling%20async-mutex%20v1.4.0%0A%20%20%20Compiling%20async-lock%20v2.3.0%0A%20%20%20Compiling%20matrixmultiply%20v0.2.4%0A%20%20%20Compiling%20form_urlencoded%20v1.0.1%0A%20%20%20Compiling%20http%20v0.2.3%0A%20%20%20Compiling%20heck%20v0.3.2%0A%20%20%20Compiling%20pest%20v2.1.3%0A%20%20%20Compiling%20walkdir%20v2.3.2%0A%20%20%20Compiling%20semver%20v0.6.0%0A%20%20%20Compiling%20parity-wasm%20v0.32.0%0A%20%20%20Compiling%20wasmi-validation%20v0.3.0%0A%20%20%20Compiling%20async-channel%20v1.6.1%0A%20%20%20Compiling%20lru%20v0.6.5%0A%20%20%20Compiling%20uint%20v0.9.0%0A%20%20%20Compiling%20hash256-std-hasher%20v0.15.2%0A%20%20%20Compiling%20bitvec%20v0.20.2%0A%20%20%20Compiling%20build-helper%20v0.1.1%0A%20%20%20Compiling%20aho-corasick%20v0.7.15%0A%20%20%20Compiling%20futures-lite%20v1.11.3%0A%20%20%20Compiling%20tokio%20v0.2.25%0A%20%20%20Compiling%20regex-automata%20v0.1.9%0A%20%20%20Compiling%20unicode-normalization%20v0.1.17%0A%20%20%20Compiling%20quote%20v1.0.9%0A%20%20%20Compiling%20jobserver%20v0.1.22%0A%20%20%20Compiling%20atty%20v0.2.14%0A%20%20%20Compiling%20parking_lot_core%20v0.8.3%0A%20%20%20Compiling%20num_cpus%20v1.13.0%0A%20%20%20Compiling%20parking_lot_core%20v0.7.2%0A%20%20%20Compiling%20socket2%20v0.3.19%0A%20%20%20Compiling%20signal-hook-registry%20v1.3.0%0A%20%20%20Compiling%20semver-parser%20v0.10.2%0A%20%20%20Compiling%20addr2line%20v0.14.1%0A%20%20%20Compiling%20blake2-rfc%20v0.2.18%0A%20%20%20Compiling%20generic-array%20v0.12.4%0A%20%20%20Compiling%20generic-array%20v0.13.3%0A%20%20%20Compiling%20cc%20v1.0.67%0A%20%20%20Compiling%20paste-impl%20v0.1.18%0A%20%20%20Compiling%20http-body%20v0.3.1%0A%20%20%20Compiling%20rand_core%20v0.6.2%0A%20%20%20Compiling%20petgraph%20v0.5.1%0A%20%20%20Compiling%20rand_core%20v0.5.1%0A%20%20%20Compiling%20parking_lot%20v0.11.1%0A%20%20%20Compiling%20regex%20v1.4.5%0A%20%20%20Compiling%20parking_lot%20v0.10.2%0A%20%20%20Compiling%20nb-connect%20v1.0.3%0A%20%20%20Compiling%20matchers%20v0.0.1%0A%20%20%20Compiling%20backtrace%20v0.3.56%0A%20%20%20Compiling%20idna%20v0.2.2%0A%20%20%20Compiling%20integer-sqrt%20v0.1.5%0A%20%20%20Compiling%20approx%20v0.3.2%0A%20%20%20Compiling%20digest%20v0.8.1%0A%20%20%20Compiling%20crypto-mac%20v0.7.0%0A%20%20%20Compiling%20block-buffer%20v0.7.3%0A%20%20%20Compiling%20digest%20v0.9.0%0A%20%20%20Compiling%20block-buffer%20v0.9.0%0A%20%20%20Compiling%20crypto-mac%20v0.8.0%0A%20%20%20Compiling%20rand_chacha%20v0.3.0%0A%20%20%20Compiling%20rand_pcg%20v0.2.1%0A%20%20%20Compiling%20rand_chacha%20v0.2.2%0A%20%20%20Compiling%20once_cell%20v1.7.2%0A%20%20%20Compiling%20paste%20v0.1.18%0A%20%20%20Compiling%20sp-panic-handler%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20hmac%20v0.7.1%0A%20%20%20Compiling%20pbkdf2%20v0.3.0%0A%20%20%20Compiling%20sha2%20v0.8.2%0A%20%20%20Compiling%20chrono%20v0.4.19%0A%20%20%20Compiling%20sha2%20v0.9.3%0A%20%20%20Compiling%20pbkdf2%20v0.4.0%0A%20%20%20Compiling%20hmac%20v0.8.1%0A%20%20%20Compiling%20thread_local%20v1.1.3%0A%20%20%20Compiling%20blocking%20v1.0.2%0A%20%20%20Compiling%20async-executor%20v1.4.0%0A%20%20%20Compiling%20rand%20v0.7.3%0A%20%20%20Compiling%20simba%20v0.1.5%0A%20%20%20Compiling%20rand%20v0.8.3%0A%20%20%20Compiling%20url%20v2.2.1%0A%20%20%20Compiling%20hmac-drbg%20v0.2.0%0A%20%20%20Compiling%20zstd-sys%20v1.4.20%2Bzstd.1.4.9%0A%20%20%20Compiling%20ring%20v0.16.20%0A%20%20%20Compiling%20libloading%20v0.5.2%0A%20%20%20Compiling%20Inflector%20v0.11.4%0A%20%20%20Compiling%20fixed-hash%20v0.7.0%0A%20%20%20Compiling%20tempfile%20v3.2.0%0A%20%20%20Compiling%20libsecp256k1%20v0.3.5%0A%20%20%20Compiling%20twox-hash%20v1.6.0%0A%20%20%20Compiling%20rand_distr%20v0.2.2%0A%20%20%20Compiling%20statrs%20v0.12.0%0A%20%20%20Compiling%20wasmi%20v0.6.2%0A%20%20%20Compiling%20nalgebra%20v0.21.1%0A%20%20%20Compiling%20synstructure%20v0.12.4%0A%20%20%20Compiling%20ctor%20v0.1.19%0A%20%20%20Compiling%20thiserror-impl%20v1.0.24%0A%20%20%20Compiling%20futures-macro%20v0.3.13%0A%20%20%20Compiling%20zeroize_derive%20v1.0.1%0A%20%20%20Compiling%20tracing-attributes%20v0.1.15%0A%20%20%20Compiling%20impl-trait-for-tuples%20v0.2.1%0A%20%20%20Compiling%20ref-cast-impl%20v1.0.6%0A%20%20%20Compiling%20sp-debug-derive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20parity-util-mem-derive%20v0.1.0%0A%20%20%20Compiling%20dyn-clonable-impl%20v0.9.0%0A%20%20%20Compiling%20derive_more%20v0.99.11%0A%20%20%20Compiling%20frame-support-procedural-tools-derive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20prost-derive%20v0.7.0%0A%20%20%20Compiling%20pin-project-internal%20v1.0.5%0A%20%20%20Compiling%20libp2p-swarm-derive%20v0.23.0%0A%20%20%20Compiling%20diesel_derives%20v1.4.1%0A%20%20%20Compiling%20dyn-clonable%20v0.9.0%0A%20%20%20Compiling%20zeroize%20v1.2.0%0A%20%20%20Compiling%20futures-util%20v0.3.13%0A%20%20%20Compiling%20curve25519-dalek%20v3.0.2%0A%20%20%20Compiling%20merlin%20v2.0.1%0A%20%20%20Compiling%20curve25519-dalek%20v2.1.2%0A%20%20%20Compiling%20secrecy%20v0.7.0%0A%20%20%20Compiling%20thiserror%20v1.0.24%0A%20%20%20Compiling%20tracing-log%20v0.1.2%0A%20%20%20Compiling%20trie-db%20v0.22.3%0A%20%20%20Compiling%20polling%20v2.0.2%0A%20%20%20Compiling%20tokio-util%20v0.3.1%0A%20%20%20Compiling%20want%20v0.3.0%0A%20%20%20Compiling%20kv-log-macro%20v1.0.7%0A%20%20%20Compiling%20wasm-gc-api%20v0.1.11%0A%20%20%20Compiling%20tracing%20v0.1.25%0A%20%20%20Compiling%20which%20v4.0.2%0A%20%20%20Compiling%20tiny-bip39%20v0.8.0%0A%20%20%20Compiling%20pin-project%20v1.0.5%0A%20%20%20Compiling%20async-io%20v1.3.1%0A%20%20%20Compiling%20tracing-futures%20v0.2.5%0A%20%20%20Compiling%20pin-project%20v0.4.27%0A%20%20%20Compiling%20prost%20v0.7.0%0A%20%20%20Compiling%20prost-build%20v0.7.0%0A%20%20%20Compiling%20async-global-executor%20v2.0.2%0A%20%20%20Compiling%20async-process%20v1.0.2%0A%20%20%20Compiling%20async-std%20v1.9.0%0A%20%20%20Compiling%20prost-types%20v0.7.0%0A%20%20%20Compiling%20diesel%20v1.4.7%0A%20%20%20Compiling%20futures-executor%20v0.3.13%0A%20%20%20Compiling%20h2%20v0.2.7%0A%20%20%20Compiling%20futures%20v0.3.13%0A%20%20%20Compiling%20wasm-timer%20v0.2.5%0A%20%20%20Compiling%20multistream-select%20v0.10.2%0A%20%20%20Compiling%20rw-stream-sink%20v0.2.1%0A%20%20%20Compiling%20sp-utils%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p-core%20v0.28.3%0A%20%20%20Compiling%20linregress%20v0.4.0%0A%20%20%20Compiling%20zstd%20v0.6.1%2Bzstd.1.4.9%0A%20%20%20Compiling%20sp-maybe-compressed-blob%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20hyper%20v0.13.10%0A%20%20%20Compiling%20impl-serde%20v0.3.1%0A%20%20%20Compiling%20tracing-serde%20v0.1.2%0A%20%20%20Compiling%20erased-serde%20v0.3.13%0A%20%20%20Compiling%20ed25519-dalek%20v1.0.1%0A%20%20%20Compiling%20schnorrkel%20v0.9.1%0A%20%20%20Compiling%20toml%20v0.5.8%0A%20%20%20Compiling%20cargo-platform%20v0.1.1%0A%20%20%20Compiling%20semver%20v0.11.0%0A%20%20%20Compiling%20substrate-bip39%20v0.4.2%0A%20%20%20Compiling%20tracing-subscriber%20v0.2.17%0A%20%20%20Compiling%20cargo_metadata%20v0.13.1%0A%20%20%20Compiling%20proc-macro-crate%20v0.1.5%0A%20%20%20Compiling%20proc-macro-crate%20v1.0.0%0A%20%20%20Compiling%20frame-support-procedural-tools%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20parity-scale-codec-derive%20v2.1.0%0A%20%20%20Compiling%20sp-runtime-interface-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-api-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20multihash-derive%20v0.7.1%0A%20%20%20Compiling%20frame-support-procedural%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20substrate-wasm-builder%20v4.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20multihash%20v0.13.2%0A%20%20%20Compiling%20node-template-runtime%20v3.0.0%20%28https%3A//github.com/scs/substrate-api-client-test-node%3Fbranch%3Dbump-to-polkadot-v0.9.2%237d14a36d%29%0A%20%20%20Compiling%20parity-scale-codec%20v2.1.1%0A%20%20%20Compiling%20parity-multiaddr%20v0.11.2%0A%20%20%20Compiling%20substrate-prometheus-endpoint%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20impl-codec%20v0.5.0%0A%20%20%20Compiling%20sp-storage%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-tracing%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-wasm-interface%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-arithmetic%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20finality-grandpa%20v0.14.0%0A%20%20%20Compiling%20sp-version-proc-macro%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20primitive-types%20v0.9.0%0A%20%20%20Compiling%20sp-externalities%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-runtime-interface%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p-swarm%20v0.29.0%0A%20%20%20Compiling%20memory-db%20v0.26.0%0A%20%20%20Compiling%20kvdb%20v0.9.0%0A%20%20%20Compiling%20sp-database%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-core%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20libp2p%20v0.37.1%0A%20%20%20Compiling%20sp-trie%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-keystore%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-metadata%20v13.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-state-machine%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-io%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20index-store%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/massbit-core/index-store%29%0A%20%20%20Compiling%20sp-application-crypto%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-runtime%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-version%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-inherents%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-staking%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus-slots%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-session%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-finality-grandpa%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-offchain%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-system-rpc-runtime-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-support%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-timestamp%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-authorship%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-block-builder%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-blockchain%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-consensus-aura%20v0.9.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20sp-transaction-pool%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-system%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-benchmarking%20v3.1.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-transaction-payment%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-authorship%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-randomness-collective-flip%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20frame-executive%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-sudo%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-timestamp%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-template%20v3.0.0%20%28https%3A//github.com/scs/substrate-api-client-test-node%3Fbranch%3Dbump-to-polkadot-v0.9.2%237d14a36d%29%0A%20%20%20Compiling%20pallet-balances%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-transaction-payment-rpc-runtime-api%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-session%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-grandpa%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20pallet-aura%20v3.0.0%20%28https%3A//github.com/paritytech/substrate.git%3Fbranch%3Dmaster%231d7f6e12%29%0A%20%20%20Compiling%20massbit-chain-substrate%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/massbit-core/chain/substrate%29%0A%20%20%20Compiling%20plugin%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/plugin%29%0A%20%20%20Compiling%20block%20v0.1.0%20%28/home/hughie/7-2021/massbitprotocol/code-compiler/generated/b72fdaa4d4301abd55540723e56b92c6%29%0Awarning%3A%20unused%20import%3A%20%60diesel%3A%3Apg%3A%3APgConnection%60%0A%20--%3E%20src/mapping.rs%3A3%3A5%0A%20%20%7C%0A3%20%7C%20use%20diesel%3A%3Apg%3A%3APgConnection%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%20%20%7C%0A%20%20%3D%20note%3A%20%60%23%5Bwarn%28unused_imports%29%5D%60%20on%20by%20default%0A%0Awarning%3A%20unused%20import%3A%20%60diesel%3A%3Aprelude%3A%3A%2A%60%0A%20--%3E%20src/mapping.rs%3A4%3A5%0A%20%20%7C%0A4%20%7C%20use%20diesel%3A%3Aprelude%3A%3A%2A%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20unused%20import%3A%20%60std%3A%3Aenv%60%0A%20--%3E%20src/mapping.rs%3A7%3A5%0A%20%20%7C%0A7%20%7C%20use%20std%3A%3Aenv%3B%0A%20%20%7C%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20unused%20imports%3A%20%60Connection%60%2C%20%60PgConnection%60%2C%20%60RunQueryDsl%60%0A%20--%3E%20src/models.rs%3A2%3A14%0A%20%20%7C%0A2%20%7C%20use%20diesel%3A%3A%7BPgConnection%2C%20Connection%2C%20RunQueryDsl%7D%3B%0A%20%20%7C%20%20%20%20%20%20%20%20%20%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%0A%0Awarning%3A%20%60extern%60%20fn%20uses%20type%20%60dyn%20PluginRegistrar%60%2C%20which%20is%20not%20FFI-safe%0A%20%20--%3E%20src/lib.rs%3A13%3A35%0A%20%20%20%7C%0A13%20%7C%20extern%20%22C%22%20fn%20register%28registrar%3A%20%26mut%20dyn%20PluginRegistrar%29%20%7B%0A%20%20%20%7C%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%20%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%5E%20not%20FFI-safe%0A%20%20%20%7C%0A%20%20%20%3D%20note%3A%20%60%23%5Bwarn%28improper_ctypes_definitions%29%5D%60%20on%20by%20default%0A%20%20%20%3D%20note%3A%20trait%20objects%20have%20no%20C%20equivalent%0A%0Awarning%3A%205%20warnings%20emitted%0A%0A%20%20%20%20Finished%20release%20%5Boptimized%5D%20target%28s%29%20in%201m%2007s%0A"
           }, 200


# The logic of this API should be written in index-manager and proxy through an API gateway for user's ease of access
@app.route("/mock/indexer-list", methods=['GET'])
@cross_origin()
def mock_indexer_list_handler():
    return {
               "status": "success",
               "payload": [
                   {
                       "id": "1",
                       "network": "Substrate",
                       "name": "Uniswap V3 Official Indexer",
                       "description": "A fully decentralized protocol for automated liquidity provision on Ethereum.",
                       "repo": "https://github.com/massbitprotocol/massbitprotocol",
                       "status": "syncing"
                   },
                   {
                       "id": "2",
                       "network": "Solana",
                       "name": "Sushiswap Indexer",
                       "description": "Aims to deliver analytics & historical data for SushiSwap. Still a work in progress. Feel free to contribute! The Graph exposes a GraphQL endpoint to query the events and entities within the SushiSwap ecosystem.",
                       "repo": "https://github.com/massbitprotocol/massbitprotocol",
                       "status": "synced"
                   }
               ]
           }, 200


# The logic of this API should be written in index-manager and proxy through an API gateway for user's ease of access
@app.route("/mock/indexer-detail/<id>", methods=['GET'])
@cross_origin()
def mock_indexer_detail_handler(id):
    return {
               "status": "success",
               "payload": [
                   {
                       "id": "1",
                       "hash": "be8bf77abc1ad864d88ff3e13f225d43",
                       "timestamp": 1625806045
                   },
                   {
                       "id": "2",
                       "hash": "be8bf77abc1ad864d88ff3e13f225d43",
                       "timestamp": 1625806046
                   }
               ],
           }, 200


if __name__ == '__main__':
    # start server
    app.run(host="0.0.0.0", debug=True)
