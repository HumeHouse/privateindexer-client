import asyncio
import datetime
import json
import logging
import os
import signal
import sys
from concurrent.futures import ProcessPoolExecutor

import libtorrent as lt
import requests
from requests import Response

TORRENTS_FILE = "/app/data/torrents.json"
TORRENTS_DIR = "/app/data/torrents"

CATEGORY_PATHS = {}

INDEXER_API_URL = "https://indexer.humehouse.com"
ANNOUNCE_TRACKER_URL = "https://tracker.humehouse.com/announce"

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

# set up pretty terminal logging with custom format
formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger = logging.getLogger("privateindexer")
logger.setLevel(logging.INFO)
logger.addHandler(console_handler)
logger.propagate = False

# init the qbit web session
qbit_session = requests.Session()


# ---- Utilities ----
def qbit_login() -> bool:
    """
    Log into qBittorrent using username and password
    """
    login_data = {"username": QBIT_USERNAME, "password": QBIT_PASSWORD}
    login_response = qbit_request("post", "/auth/login", data=login_data)

    if login_response.text == "Ok.":
        return True

    raise Exception("qBitorrent credentials incorrect")


def qbit_request(method, endpoint, attempt: int = 0, **kwargs) -> Response:
    """
    Send a request to the qBittorrent API using the web session
    Will attempt to make 3 attempts to reauthenticate with API before giving up
    """
    request_url = f"http://{QBIT_HOST}/api/v2" + endpoint

    response = qbit_session.request(method, request_url, **kwargs)

    if response.status_code == 403 and attempt <= 3:
        qbit_login()
        return qbit_request(method, endpoint, attempt=attempt + 1, **kwargs)

    return response


def qbit_get_torrents() -> list:
    """
    Request a list of all torrents from the qBittorrent API
    """
    try:
        torrent_response = qbit_request("get", "/torrents/info")
        if torrent_response.status_code == 200:
            return torrent_response.json()
        else:
            logger.error(f"[QBIT] Failed to fetch torrents: {torrent_response.status_code}")
            return []
    except Exception as e:
        logger.error(f"[QBIT] Exception fetching torrents: {e}")
        return []


def qbit_add_torrent(torrent_metadata: dict, torrents_on_qbit: list = None):
    """
    Add a torrent file to qBittorrent API
    Checks if the torrent v1/v2 hash already exists on the client to prevent duplication
    Adds the tracker's announce URL with the current user's API key to the torrent's announce-list before adding
    """
    torrents_on_qbit = torrents_on_qbit or qbit_get_torrents()

    existing_v1_hashes = [t["infohash_v1"].lower() for t in torrents_on_qbit if "infohash_v1" in t]
    existing_v2_hashes = [t["infohash_v2"].lower() for t in torrents_on_qbit if "infohash_v2" in t]

    if torrent_metadata["hash_v1"].lower() in existing_v1_hashes or torrent_metadata["hash_v2"].lower() in existing_v2_hashes:
        return

    torrent_path = os.path.join(TORRENTS_DIR, f"{torrent_metadata['name']}.torrent")

    try:
        # use libtorrent to decode the bytes from file
        with open(torrent_path, "rb") as f:
            torrent_data = lt.bdecode(f.read())

        # add tracker announce URL to the list of trackers
        torrent_data[b"announce"] = f"{ANNOUNCE_TRACKER_URL}?apikey={API_KEY}".encode()
        if b"announce-list" in torrent_data:
            torrent_data[b"announce-list"] = [[torrent_data[b"announce"]]]

        # use libtorrent to reencode the file back into bytes
        modified_torrent_bytes = lt.bencode(torrent_data)

    except Exception as e:
        logger.error(f"[QBIT] Failed to add tracker URL to torrent '{torrent_metadata['name']}': {e}")
        return

    files = {"torrents": (os.path.basename(torrent_path), modified_torrent_bytes)}
    data = {"savepath": os.path.dirname(torrent_metadata["path"]), "skip_checking": "false", "paused": "false"}

    # try to send the torrent file to the qBittorrent client for seeding
    try:
        add_response = qbit_request("post", "/torrents/add", data=data, files=files)
        if add_response.status_code == 200:
            logger.info(f"[QBIT] Added torrent '{torrent_metadata['name']}' to client")
        else:
            logger.error(f"[QBIT] Failed to add torrent '{torrent_metadata['name']}' to client: {add_response.status_code}")
    except Exception as e:
        logger.error(f"[QBIT] Failed to add torrent '{torrent_metadata['name']}' to client: {e}")


async def load_torrents_threadsafe():
    """
    Reads JSON database of all actively tracked torrents on this client
    """
    async with torrents_lock:
        if not os.path.exists(TORRENTS_FILE):
            return []
        try:
            with open(TORRENTS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[TORRENTS] Failed to load torrents.json: {e}")
            return []


async def save_torrents_threadsafe(torrents):
    """
    Writes to the JSON database for torrent data, same purpose as load_torrents_threadsafe()
    """
    async with torrents_lock:
        try:
            with open(TORRENTS_FILE, "w") as f:
                json.dump(torrents, f, indent=2)
        except Exception as e:
            logger.error(f"[TORRENTS] Failed to write torrents.json: {e}")


def detect_category(file_path: str) -> int:
    """
    Tries to match the file's path with the known category directories and returns its ID
    """
    for name, cat_info in CATEGORY_PATHS.items():
        if file_path.startswith(cat_info["path"]):
            return cat_info["id"]
    return 0


def send_torrent_to_indexer(torrent_file, metadata):
    """
    Attempt to upload the torrent file along with its metadata to the PrivateIndexer server
    Will mark a file as uploaded in the database if the server API returns a 409 status code
    """
    try:
        with open(torrent_file, "rb") as f:
            # build the request with all the necessary torrent metadata required by indexer
            files = {"torrent_file": (os.path.basename(torrent_file), f, "application/x-bittorrent")}
            data = {"apikey": API_KEY, "metadata": json.dumps(
                {"name": metadata["name"], "size": metadata["size"], "category": metadata["category"], "hash_v1": metadata.get("hash_v1"),
                 "hash_v2": metadata.get("hash_v2"), "files": metadata["files"]})}

            response = requests.post(f"{INDEXER_API_URL}/create", data=data, files=files)

            # based on the response from API, we will know status of upload
            if response.status_code == 200:
                logger.info(f"[INDEXER] Successfully sent '{metadata["name"]}' to indexer")
                return True
            elif response.status_code == 409:
                logger.info(f"[INDEXER] Torrent {metadata.get('name')} already exists on indexer, marking as uploaded")
                return True
            else:
                logger.error(f"[INDEXER] Failed to send '{metadata["name"]}' to indexer, will retry later: {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"[INDEXER] Exception while sending '{metadata["name"]}' to indexer, will retry later: {e}")
        return False


def create_torrent(file_path: str):
    """
    Main synchronous routine to build and generate a complete torrent file from the media passed in as file_path
    Checks for existing torrent file in case database save operation was interrupted from a previous app run
    Will fail if v1/v2 hash checks do not succeeed
    Removes the torrent file if any failures occur so a new one can be generated
    """
    # split the extension off the filename, this will become the name of the torrent
    torrent_name, _ = os.path.splitext(os.path.basename(file_path))
    torrent_file = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")

    if not os.path.exists(torrent_file):
        # use libtorrent to initialize temporary storage, add the media, sign the torrent, set to private, and encode data to the torrent file
        logger.info(f"[TORRENT] Creating torrent for '{torrent_name}' using libtorrent")
        fs = lt.file_storage()
        fs.set_name(torrent_name)
        lt.add_files(fs, file_path)
        t = lt.create_torrent(fs)
        t.set_creator(f"PrivateIndexer Client ({TORRENT_SIGNER})")
        t.set_priv(True)
        lt.set_piece_hashes(t, os.path.dirname(file_path))
        torrent = t.generate()

        with open(torrent_file, "wb") as f:
            f.write(lt.bencode(torrent))
    else:
        logger.info(f"[TORRENT] Torrent '{torrent_name}' already exists")

    # attempt to pull the v1 and v2 hash information from the torrent file, otherwise fail and remove torrent file from disk
    try:
        info = lt.torrent_info(torrent_file)
        hashes = info.info_hashes()
        if not hashes.has_v1():
            logger.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v1 = str(hashes.v1)
        if not hashes.has_v2():
            logger.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v2 = str(hashes.v2)
    except Exception as e:
        logger.error(f"[TORRENT] Failed to read hash for '{torrent_name}', it has been removed: {e}")
        os.unlink(torrent_file)
        return None

    size = os.path.getsize(file_path)
    category_id = detect_category(file_path)

    return {"name": torrent_name, "size": size, "path": file_path, "uploaded": False, "files": 1, "category": category_id, "hash_v1": torrent_hash_v1,
            "hash_v2": torrent_hash_v2}


def create_torrent_threadsafe(file_path: str):
    """
    Wraps the create_torrent() routine in a try/accept to catch all runtime errors
    """
    try:
        return create_torrent(file_path)
    except Exception as e:
        logger.error(f"[TORRENT] Failed to create torrent for '{file_path}': {e}")
        return None


async def scan_media_library():
    """
    Main loop for scanning media libraries defined by user
    Will walk over all defined category paths, each single file gets turned into a single torrent file
    Will ignore existing and correctly uploaded torrent files
    Torrent creation is batched into a multi-threaded executor, number of threads defined by user
    Will attempt to use send_torrent_to_indexer() and qbit_get_torrents() for each torrent if conditions are met
    """
    torrents = await load_torrents_threadsafe()
    torrents_by_path = {t["path"]: t for t in torrents}

    total_files = 0
    ignored_files = 0
    created_files = 0

    loop = asyncio.get_event_loop()
    futures = []

    # loop through all files in the media directories
    for category_key, cat_info in CATEGORY_PATHS.items():
        for root, _, files in os.walk(cat_info["path"]):
            for f in files:
                total_files += 1

                file_path = os.path.join(root, f)
                extension = os.path.splitext(os.path.basename(file_path))[1].replace(".", "")
                if extension not in MOVIE_EXTENSIONS:
                    logger.info(f"[SCAN] Skipping file with {extension} extension")
                    continue

                # ignore the media file if it has already been uploaded according to the database
                if file_path in torrents_by_path and torrents_by_path[file_path].get("uploaded", False):
                    ignored_files += 1
                    continue

                # dispatch the torrent creation to the pool of worker threads
                future = loop.run_in_executor(EXECUTOR, create_torrent_threadsafe, file_path)
                futures.append(future)

    if len(futures) > 0:
        logger.info(f"[SCAN] Queued {len(futures)} torrents for creation")

    # collect the workers as they finish and process their output
    for future in asyncio.as_completed(futures):
        try:
            new_torrent = await future
            if new_torrent:
                created_files += 1
                torrent_file = os.path.join(TORRENTS_DIR, f"{new_torrent['name']}.torrent")

                # attempt to send torrent file to indexer server
                if not new_torrent["uploaded"]:
                    if send_torrent_to_indexer(torrent_file, new_torrent):
                        new_torrent["uploaded"] = True

                torrents_by_path[new_torrent["path"]] = new_torrent
                await save_torrents_threadsafe(list(torrents_by_path.values()))

                logger.info(f"[SCAN] Created or updated torrent: {new_torrent["name"]}")
        except Exception as e:
            logger.error(f"[SCAN] Error in torrent post-torrent-creation process: {e}")

    torrents = list(torrents_by_path.values())
    torrents_on_qbit = qbit_get_torrents()

    # here we check to make sure the media files for a torrent still exist on the disk, otherwise remove the torrent from the local database ONLY
    still_existing = []
    for torrent in torrents:
        if os.path.exists(torrent["path"]):
            still_existing.append(torrent)

            # attempt to add any missing files to the qBittorrent client for seeding
            qbit_add_torrent(torrent, torrents_on_qbit)
        else:
            logger.info(f"[SCAN] Media files missing for '{torrent["name"]}', removed it from database")

    await save_torrents_threadsafe(still_existing)

    removed_entries = len(torrents) - len(still_existing)

    return total_files, ignored_files, created_files, removed_entries


async def periodic_scan():
    """
    Wraps scan_media_library() asynchronously and periodically scans media libraries defined by user
    Will also attempt to resend failed uploads torrents to the PrivateIndexer server after each scan
    """
    while True:
        try:
            logger.info("[SCAN] Running media library scan")
            before = datetime.datetime.now()

            total_files, ignored_files, created_files, removed_entries = await scan_media_library()

            delta = datetime.datetime.now() - before
            logger.info(f"[SCAN] Media library scan complete ({delta}): "
                        f"total {total_files} files, {ignored_files} ignored, {created_files} created, {removed_entries} removed")

            torrents = await load_torrents_threadsafe()
            updated = False
            # attempt to resend all failed uploads to indexer server
            for torrent in torrents:
                if not torrent.get("uploaded", False):
                    torrent_file = os.path.join(TORRENTS_DIR, f"{torrent['name']}.torrent")
                    if os.path.exists(torrent_file):
                        logger.info(f"[INDEXER] Attempting to resend torrent to indexer: '{torrent['name']}'")
                        if send_torrent_to_indexer(torrent_file, torrent):
                            torrent["uploaded"] = True
                            updated = True
            if updated:
                await save_torrents_threadsafe(torrents)
        except Exception as e:
            logger.error(f"[SCAN] Error during periodic scan: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


# ---- General Setup ----
logger.info("[APP] Loading PrivateIndexer client")

# check if the torrent storage directory exists, otherwise create it
if os.path.exists(TORRENTS_DIR):
    logger.info(f"[APP] Torrents directory: {TORRENTS_DIR}")
else:
    logger.info(f"[APP] Creating torrents directory: {TORRENTS_DIR}")
    os.makedirs(TORRENTS_DIR)

# create the multi-thread executor with user-defined number of threads
SCANNER_THREADS = int(os.getenv("SCANNER_THREADS", "16"))
logger.info(f"[APP] Scanner threads: {SCANNER_THREADS}")
EXECUTOR = ProcessPoolExecutor(max_workers=SCANNER_THREADS)

SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", "5"))
logger.info(f"[APP] Scan interval: {SCAN_INTERVAL} seconds")

# make sure media directory exists and index it with ID in the category paths
MOVIE_DIR = os.getenv("MOVIE_DIR", "false")
MOVIE_EXTENSIONS = os.getenv("MOVIE_EXTENSIONS", "mp4,mkv,m4v,avi").split(",")
if MOVIE_DIR.lower() != "false":
    if not os.path.exists(MOVIE_DIR):
        logger.error(f"[APP] Movies directory doesn't exist: {MOVIE_DIR}")
        exit(1)
    logger.info(f"[APP] Using movies directory: {MOVIE_DIR}")
    CATEGORY_PATHS["movies"] = {"id": 1000, "path": MOVIE_DIR}

# try to authenticate with the API to validate the API key, otherwise fail
API_KEY = os.getenv("API_KEY")
try:
    indexer_response = requests.get(f"{INDEXER_API_URL}/user?apikey={API_KEY}")
    if indexer_response.status_code == 200:
        TORRENT_SIGNER = indexer_response.text
        logger.info(f"[APP] Logged into indexer as '{TORRENT_SIGNER}'")
    else:
        logger.error(f"[APP] API key rejected by indexer or indexer unavailable")
        exit(1)
except Exception as e:
    logger.error(f"[APP] Failed to validate API key: {e}")
    exit(1)

# try to authenticate with the qBittorrent API, otherwise fail
QBIT_HOST = os.getenv("QBIT_HOST")
QBIT_USERNAME = os.getenv("QBIT_USERNAME")
QBIT_PASSWORD = os.getenv("QBIT_PASSWORD")
try:
    qbit_login()
    logger.info(f"[APP] qBittorrent connection is working")
except Exception as e:
    logger.error(f"[APP] qBittorrent connection failed: {e}")
    exit(1)

# initialize the threadsafe file lock for the JSON database
torrents_lock = asyncio.Lock()

# start main loop if app is run from CLI
if __name__ == "__main__":
    async def main():
        logger.info("[APP] Starting PrivateIndexer scan task")

        # send the scan task to the asyncio scheduler
        scan_task = asyncio.create_task(periodic_scan())
        await asyncio.gather(scan_task)
        return None


    asyncio.run(main())
