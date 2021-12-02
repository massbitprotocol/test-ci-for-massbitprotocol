import errno
import random
import os
import ipfshttpclient
import yaml
from shutil import copyfile


def write_to_disk(file, data):
    """
    write_to_disk create and save the file to disk

    :param file: (String) path to the file + the file's name

    :param data: (String) raw data. Any data with "\ n" will be created as newline
    :return: (String) ok
    """
    # Auto create folder parent folder if not exists
    if not os.path.exists(os.path.dirname(file)):
        try:
            os.makedirs(os.path.dirname(file))
        except OSError as exc:  # Guard against race condition
            if exc.errno != errno.EEXIST:
                raise

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


def random_hash():
    # Random hash should be used as folder name for each new deployment
    hash = random.getrandbits(128)
    hash = "%032x" % hash
    return hash


def check_compile_status(deployment_hash):
    generated_folder = "generated/" + deployment_hash  # Where we'll be looking for the compilation status
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
    return status, payload


def get_file(path):
    """
    Look for files in a folder

    :param path: (String) Path to the directory where we want to get all the file names inside
    :return: (Array) File names
    """
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    # Remove hidden files
    files = [f for f in files if f[0] != "."]
    return files


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def get_abi_files(compilation_id):
    """
    Build a new array of abi object from the /generated/hash/abis folder

    :param compilation_id: (String) Hash Identifier of the new index. It's also the name of the folders in the generated folder
    :return: (Array) ABI file objects (name of the file, path to the file)
    """
    abi = []
    abi_path = os.path.join("./generated", compilation_id, "abis")
    if os.path.isdir(abi_path):
        for file_name in get_file(abi_path):
            abi_object = {
                "name": file_name,
                "path": os.path.join("./generated", compilation_id, "abis", file_name)
            }
            abi.append(abi_object)
    return abi


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def upload_abi_to_ipfs(client, abi):
    """
    Upload abi files to IPFS and build a new abi object for ease of access

    :param client: IPFS Client
    :param abi: ABI Objects (name, hash)
    :return: (Array) ABI file objects (name of the file, path to the file, hash of the IPFS upload result)
    """
    abi_new = []
    for abi_object in abi:
        # Upload to IPFS
        res = client.add(abi_object["path"])
        # Build a new abi object with more attribute
        abi_object["hash"] = res["Hash"]
        abi_new.append(abi_object)
    return abi


def replace_abi_v2(client, root_path, subgraph_type, subgraph_content):
    """
    Loop through abis in the dataSources, upload them to IPFS and add that IPFS hash back into the subgraph.yml file
    :param client: IPFS Client
    :param root_path: Path to the generated/hash folder
    :param subgraph_type: dataSources / templates
    :param subgraph_content: python dictionary of the subgraph.yaml
    :return: parsed subgraph_content
    """
    for i in range(len(subgraph_content[subgraph_type])):
        for j in range(len(subgraph_content[subgraph_type][i]['mapping']['abis'])):
            res = client.add(
                os.path.join(root_path, "build", subgraph_content[subgraph_type][i]['mapping']['abis'][j]['file']))[
                "Hash"]
            subgraph_content[subgraph_type][i]['mapping']['abis'][j] = {
                'name': subgraph_content[subgraph_type][i]['mapping']['abis'][j]['name'], 'file': {'/': '/ipfs/' + res}}
    return subgraph_content


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def upload_mapping_to_ipfs(client, type, root_path, subgraph_path):
    """
    Upload mapping.abi and mapping.files to IPFS and build a new mapping object for ease of access

    """
    # Load subgraph content
    stream = open(subgraph_path, 'r')
    subgraph = yaml.safe_load(stream)

    # Upload and build new mapping object
    mapping_new = []
    for i in range(len(subgraph[type])):
        mapping_res = dict()
        mapping_res["name"] = subgraph[type][i]["name"]
        mapping_res["file_hash"] = client.add(os.path.join(root_path, "build", subgraph[type][i]['mapping']['file']))[
            "Hash"]
        mapping_new.append(mapping_res)

    return mapping_new


def ipfs_client_init():
    if os.environ.get('IPFS_URL'):
        return ipfshttpclient.connect(os.environ.get('IPFS_URL'))  # Connect with IPFS container name
    else:
        return ipfshttpclient.connect()


def get_index_manager_url():
    if os.environ.get('INDEX_MANAGER_URL'):
        return os.environ.get('INDEX_MANAGER_URL')  # Connection to indexer
    else:
        return 'http://0.0.0.0:3030'


def get_index_manager_url_v2():
    if os.environ.get('INDEX_MANAGER_URL_V2'):
        return os.environ.get('INDEX_MANAGER_URL_V2')  # Connection to indexer
    else:
        return 'http://0.0.0.0:8020'

def is_template_exist(subgraph_path):
    stream = open(subgraph_path, 'r')
    subgraph = yaml.safe_load(stream)
    if 'templates' in subgraph:
        stream.close()
        return True
    return False


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def replace_abi_v1(subgraph_type, subgraph, abi_res):
    if subgraph_type in subgraph:
        for iterator in range(len(subgraph[subgraph_type])):
            for i in range(0, len(subgraph[subgraph_type][0]['mapping']['abis'])):
                file_name = os.path.basename(subgraph[subgraph_type][0]['mapping']['abis'][i]['file'])
                name = subgraph[subgraph_type][0]['mapping']['abis'][i]['name']
                for abi_object in abi_res:
                    if file_name.lower() == abi_object["name"].lower():
                        subgraph[subgraph_type][0]['mapping']['abis'][i] = {'name': name,
                                                                            'file': {
                                                                                '/': '/ipfs/' + abi_object["hash"]}}
    return subgraph


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def replace_mapping_v1(subgraph_type, subgraph, mapping_res):
    if subgraph_type in subgraph:
        for i in range(len(subgraph[subgraph_type])):
            do_replace_mapping(subgraph_type, subgraph, mapping_res, i)
    return subgraph


# To be refactored
# We'll be traversing subgraph.yaml and upload the content instead of pre-upload the content to IPFS.
def do_replace_mapping(subgraph_type, subgraph, mapping_res, iterator):
    """
    Replace mapping > file with IPFS hash
    """
    for j in range(len(mapping_res)):  # Add a new iterator to loop through mapping_res
        if subgraph[subgraph_type][iterator]['name'] == mapping_res[j]['name']:
            subgraph[subgraph_type][iterator]['mapping']['file'] = {'/': '/ipfs/' + mapping_res[j]['file_hash']}
