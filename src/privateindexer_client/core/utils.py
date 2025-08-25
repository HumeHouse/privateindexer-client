import json
import os

import libtorrent as lt
import requests

from privateindexer_client.core.config import CATEGORY_PATHS, API_KEY, INDEXER_API_URL, TORRENTS_DIR
from privateindexer_client.core.logger import log


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
                log.info(f"[INDEXER] Successfully sent '{metadata["name"]}' to indexer")
                return True
            elif response.status_code == 409:
                log.info(f"[INDEXER] Torrent {metadata.get('name')} already exists on indexer, marking as uploaded")
                return True
            else:
                log.error(f"[INDEXER] Failed to send '{metadata["name"]}' to indexer, will retry later: {response.status_code}")
                return False
    except Exception as e:
        log.error(f"[INDEXER] Exception while sending '{metadata["name"]}' to indexer, will retry later: {e}")
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
        log.info(f"[TORRENT] Creating torrent for '{torrent_name}' using libtorrent")
        fs = lt.file_storage()
        fs.set_name(torrent_name)
        lt.add_files(fs, file_path)
        t = lt.create_torrent(fs)
        t.set_creator(f"PrivateIndexer Client")
        t.set_priv(True)
        lt.set_piece_hashes(t, os.path.dirname(file_path))
        torrent = t.generate()

        with open(torrent_file, "wb") as f:
            f.write(lt.bencode(torrent))
    else:
        log.info(f"[TORRENT] Torrent '{torrent_name}' already exists")

    # attempt to pull the v1 and v2 hash information from the torrent file, otherwise fail and remove torrent file from disk
    try:
        info = lt.torrent_info(torrent_file)
        hashes = info.info_hashes()
        if not hashes.has_v1():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v1 = str(hashes.v1)
        if not hashes.has_v2():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v2 = str(hashes.v2)
    except Exception as e:
        log.error(f"[TORRENT] Failed to read hash for '{torrent_name}', it has been removed: {e}")
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
        log.error(f"[TORRENT] Failed to create torrent for '{file_path}': {e}")
        return None
