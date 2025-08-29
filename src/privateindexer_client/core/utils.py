import datetime
import hashlib
import json
import os
import secrets
import time

import libtorrent as lt

from privateindexer_client.core import config, httpx_request
from privateindexer_client.core.config import TORZNAB_CATEGORY_PATHS, API_KEY, INDEXER_API_URL, TORRENTS_DIR
from privateindexer_client.core.logger import log

_file_piece_hash_cache: dict[str, dict[int, list[bytes]]] = {}


def detect_torznab_category(file_path: str) -> int:
    """
    Tries to match the file's path with the known torznab category directories and returns its ID
    """
    for name, cat_info in TORZNAB_CATEGORY_PATHS.items():
        if file_path.startswith(cat_info["path"]):
            return cat_info["id"]
    return 0


def get_torrent_categories() -> dict[str, dict[str, str]]:
    return config.load_config().get("categories", {})


def add_torrent_category(name: str, save_dir: str):
    """
    try to make a directory in the downloads directory for this category and add it to the config file
    """
    config_data = config.load_config()
    categories = config_data.get("categories", {})

    if not os.path.exists(save_dir):
        os.mkdir(save_dir)

    # we have to store them like this per qBittorrent's format
    categories[name] = {
        "name": name,
        "savePath": save_dir
    }
    config_data["categories"] = categories

    config.save_config(config_data)


def detect_torrent_category(file_path: str) -> str:
    """
    Tries to match the file's path with the categories in the config and returns its name
    """
    for category_data in get_torrent_categories().values():
        if file_path.startswith(category_data.get("savePath")):
            return category_data.get("name")
    return ""


async def fetch_indexer_user_data():
    """
    Request the current user's indexer statistics for use in the GUI
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(INDEXER_API_URL + "/user/stats", params={"apikey": API_KEY})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        log.error(f"[INDEXER] Failed to fetch user stats: {e}")
        return None


async def send_torrent_to_indexer(metadata):
    """
    Attempt to upload the torrent file along with its metadata to the PrivateIndexer server
    Will mark a file as uploaded in the database if the server API returns a 409 status code
    """
    torrent_file = os.path.join(TORRENTS_DIR, f"{metadata["name"]}.torrent")
    try:
        with open(torrent_file, "rb") as f:
            # build the request with all the necessary torrent metadata required by indexer
            files = {"torrent_file": (os.path.basename(torrent_file), f, "application/x-bittorrent")}
            data = {"apikey": API_KEY, "metadata": json.dumps(
                {"name": metadata["name"], "size": metadata["size"], "category": metadata["category"], "hash_v1": metadata.get("hash_v1"),
                 "hash_v2": metadata.get("hash_v2"), "files": metadata["files"]})}

            async with httpx_request.get_client() as client:
                response = await client.post(INDEXER_API_URL + "/create", data=data, files=files)

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


def hash_file_by_pieces(file_path: str, piece_length: int) -> list[str]:
    """
    Return list of SHA1 hashes (hex) of file split into piece_length chunks
    Caches results per file_path and piece_length
    """
    # try to return a value from the peice length cache
    if file_path in _file_piece_hash_cache:
        if piece_length in _file_piece_hash_cache[file_path]:
            return _file_piece_hash_cache[file_path][piece_length]

    hashes = []
    before = datetime.datetime.now()

    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(piece_length):
                h = hashlib.sha1(chunk).digest()
                hashes.append(h)
    except Exception as e:
        log.error(f"[TORRENT] Error generating hashes for '{file_path}': {e}")
        return []

    delta = datetime.datetime.now() - before
    log.debug(f"[TORRENT] Hashed {len(hashes)}x {piece_length} Byte chunks from '{file_path}' in {delta}")

    # store in cache
    if file_path not in _file_piece_hash_cache:
        _file_piece_hash_cache[file_path] = {}
    _file_piece_hash_cache[file_path][piece_length] = hashes

    return hashes


def torrent_matches_file(torrent_path: str, media_path: str) -> bool:
    """
    Checks if a torrent file and media hashes match
    Calculates hash of the media and compares to the hashes found in the torrent file
    """
    # read torrent info
    info = lt.torrent_info(torrent_path)

    # single file torrent
    if info.num_files() == 1:
        if os.path.getsize(media_path) != info.total_size():
            return False

        piece_length = info.piece_length()
        file_hashes = hash_file_by_pieces(media_path, piece_length)

        # get piece hashes from torrent info
        torrent_hashes = [info.hash_for_piece(i) for i in range(info.num_pieces())]
        return file_hashes == torrent_hashes

    # TODO: multi-file torrents require walking directory
    return False


def create_torrent(media_file_path: str):
    """
    Main synchronous routine to build and generate a complete torrent file from the media passed in as file_path
    Will fail if v1/v2 hash checks do not succeeed
    Removes the torrent file if any failures occur so a new one can be generated
    """
    # split the extension off the filename, this will become the name of the torrent if needed
    torrent_name, _ = os.path.splitext(os.path.basename(media_file_path))
    torrent_file_path = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")

    # use libtorrent to initialize temporary storage, add the media, sign the torrent, set to private, and encode data to the torrent file
    log.info(f"[TORRENT] Creating torrent for '{torrent_name}'")
    fs = lt.file_storage()
    fs.set_name(torrent_name)
    lt.add_files(fs, media_file_path)
    t = lt.create_torrent(fs)
    t.set_creator("PrivateIndexer Client")
    t.set_priv(True)
    lt.set_piece_hashes(t, os.path.dirname(media_file_path))
    torrent_data = t.generate()

    with open(torrent_file_path, "wb") as f:
        f.write(lt.bencode(torrent_data))

    # attempt to pull the v1 and v2 hash information from the torrent file, otherwise fail and remove torrent file from disk
    try:
        info = lt.torrent_info(torrent_file_path)
        hashes = info.info_hashes()
        if not hashes.has_v1():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(torrent_file_path)
            return None
        torrent_hash_v1 = str(hashes.v1)
        if not hashes.has_v2():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file_path)
            return None
        torrent_hash_v2 = str(hashes.v2)

        # get the number of files in the torrent
        file_count = info.num_files()
    except Exception as e:
        log.error(f"[TORRENT] Failed to read hash for '{torrent_name}', it has been removed: {e}")
        os.unlink(torrent_file_path)
        return None

    size = os.path.getsize(media_file_path)
    category_id = detect_torznab_category(media_file_path)

    return {"name": torrent_name, "size": size, "media_path": media_file_path, "torrent_path": torrent_file_path, "uploaded": False, "files": file_count,
            "category": category_id, "hash_v1": torrent_hash_v1, "hash_v2": torrent_hash_v2}


def create_torrent_threadsafe(file_path: str):
    """
    Wraps the create_torrent() routine in a try/accept to catch all runtime errors
    """
    try:
        return create_torrent(file_path)
    except Exception as e:
        log.error(f"[TORRENT] Failed to create torrent for '{file_path}': {e}")
        return None


def find_existing_torrent(media_path: str) -> str | None:
    """
    Given a media path, check if a torrent already exists in TORRENTS_DIR with the same name
    Returns the existing torrent path if found, otherwise None
    """
    # get just the name of file or directory without the path
    basename = os.path.basename(media_path)

    # try to find the torrent file based on the file or directory name
    torrent_file = os.path.join(TORRENTS_DIR, basename + ".torrent")
    if os.path.exists(torrent_file):
        log.debug(f"[TORRENT] Matched '{media_path}' to '{torrent_file}' by name")
        return torrent_file

    # if this media is a file, we can try to strip the extension off and find a match
    if os.path.isfile(media_path):
        filename = os.path.splitext(os.path.basename(media_path))[0]
        torrent_file = os.path.join(TORRENTS_DIR, filename + ".torrent")
        if os.path.exists(torrent_file):
            log.debug(f"[TORRENT] Matched '{media_path}' to '{torrent_file}' by filename")
            return torrent_file

    # find a torrent whose file hashes match that of what we are looking for
    for torrent_file in os.listdir(TORRENTS_DIR):
        if not torrent_file.endswith(".torrent"):
            continue
        torrent_path = os.path.join(TORRENTS_DIR, torrent_file)
        try:
            if torrent_matches_file(torrent_path, media_path):
                log.debug(f"[TORRENT] Matched '{media_path}' to '{torrent_path}' by hash")
                return torrent_path
        except Exception as e:
            log.error(f"[TORRENT] Error comparing hash for '{media_path}' to '{torrent_file}': {e}")

    log.debug(f"[TORRENT] Couldn't find torrent file for: '{media_path}")
    return None


async def add_torrent_to_database(name: str, size: int, torrent_path: str, uploaded: bool, files: int, category: int, media_path: str = None, download_path: str = None,
                                  hash_v1: str = None, hash_v2: str = None):
    """
    Add torrent metadata into the database or update upon duplicate torrent_path
    """
    await database.execute(
        "INSERT INTO torrents (name, size, torrent_path, uploaded, files, category, media_path, download_path, hash_v1, hash_v2)"
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        "ON CONFLICT(torrent_path) DO UPDATE SET name=excluded.name, size=excluded.size, uploaded=excluded.uploaded, files=excluded.files, category=excluded.category, media_path=excluded.media_path, download_path=excluded.download_path, hash_v1=excluded.hash_v1, hash_v2=excluded.hash_v2",
        (name, size, torrent_path, uploaded, files, category, media_path, download_path, hash_v1, hash_v2,))


def process_fastresume_file(fastresume_path: str, hash_v1: str, torrent_path: str | None):
    """
    Thread-safe way to read fastresume file bytes, returns raw data to main thread
    """
    try:
        # read bytes from fastresume file
        with open(fastresume_path, "rb") as f:
            data = f.read()
        log.debug(f"[FASTRESUME] Loaded fastresume file for hash: {hash_v1}")
        return data, hash_v1, torrent_path
    except Exception as e:
        log.error(f"[FASTRESUME] Failed to read fastresume file for hash: {hash_v1}: {e}")
        return None, hash_v1, torrent_path


def generate_sid(api_key: str) -> str:
    """
    Generate a simple session ID based on the user's API key
    """
    nonce = secrets.token_hex(16)
    raw = f"{api_key}:{nonce}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def calc_eta(status: lt.torrent_status) -> int:
    """
    Calculate an eta based on torrent status (partially matched to how qBittorrent source does it)
    """
    if status.download_rate > 0 and status.total_wanted > 0:
        remaining = status.total_wanted - status.total_wanted_done
        return int(remaining / status.download_rate)
    return 8640000  # qBittorrent uses 100 days as "infinite ETA"


def safe_ratio(status: lt.torrent_status) -> float:
    if status.total_payload_download > 0:
        return status.total_payload_upload / status.total_payload_download
    return 0.0


def map_state(status: lt.torrent_status) -> str:
    """
    Map libtorrent state to qBittorrent string
    """
    if status.errc and status.errc.value() != 0:
        return "error"
    mapping = {lt.torrent_status.checking_files: "checkingDL", lt.torrent_status.downloading_metadata: "metaDL", lt.torrent_status.downloading: "downloading",
               lt.torrent_status.finished: "stalledUP", lt.torrent_status.seeding: "uploading", lt.torrent_status.checking_resume_data: "checkingResumeData", }
    return mapping.get(status.state, "unknown")


def format_peer_flags(peer: lt.peer_info):
    """
    Convert a libtorrent peer_info.flags + peer_info.source into a qBittorrent-style string
    https://web.archive.org/web/20141111072948/http://www.utorrent.com/help/faq/misc#faq13
    """
    flags = []

    # downloading states
    if peer.flags & peer.interesting:
        if peer.flags & peer.choked:
            flags.append("d")  # interested, but peer choked us
        else:
            flags.append("D")  # actively downloading

    # uploading states
    if peer.flags & peer.remote_interested:
        if peer.flags & peer.remote_choked:
            flags.append("u")  # they want, but we choke
        else:
            flags.append("U")  # uploading to them

    # optimistic unchoke
    if peer.flags & peer.optimistic_unchoke:
        flags.append("O")

    # snubbed
    if peer.flags & peer.snubbed:
        flags.append("S")

    # incoming connection
    if peer.flags & peer.local_connection:
        flags.append("I")

    # unchoked by peer but we're not interested
    if not (peer.flags & peer.interesting) and not (peer.flags & peer.choked):
        flags.append("K")

    # we unchoked them but they’re not interested
    if not (peer.flags & peer.remote_interested) and not (peer.flags & peer.remote_choked):
        flags.append("?")

    # peer sources
    if peer.source & peer.pex:
        flags.append("X")
    if peer.source & peer.dht:
        flags.append("H")

    # encrypted
    if peer.flags & peer.rc4_encrypted:
        flags.append("E")
    elif peer.flags & peer.plaintext_encrypted:
        flags.append("e")

    # # uTP
    # if peer.flags & peer.utp_socket:
    #     flags.append("P")

    # local peer
    if peer.flags & peer.i2p_socket:
        flags.append("L")

    return "".join(flags)


def map_torrent_to_qbit(torrent: lt.torrent_handle) -> dict:
    """
    Convert a libtorrent.torrent_status object into a qBittorrent-compatible dict
    Most of this is default or general taken from the qBittorrent API docs
    Many of the keys were removed because they weren't required by the apps
    """
    status = torrent.status()

    # here we have to normalize the infohashes because they are raw bytes out of the status
    infohash_v1 = status.info_hashes.v1.to_bytes().hex() if status.info_hashes.has_v1() else None
    infohash_v2 = status.info_hashes.v2.to_bytes().hex() if status.info_hashes.has_v2() else None
    # qBittorrent "hash" field is always the v1 hash if available, otherwise fall back to v2
    torrent_hash = infohash_v1 or infohash_v2

    # try to match the save_path with the configured category paths
    category = detect_torrent_category(status.save_path)

    mapped = {"added_on": int(status.added_time or 0), "amount_left": int(status.total_wanted - status.total_wanted_done), "availability": status.distributed_copies,
              "completed": int(status.total_done), "completion_on": int(status.completed_time or -1), "content_path": os.path.join(status.save_path, status.name),
              "dlspeed": status.download_rate, "download_path": status.save_path, "downloaded": status.all_time_download,
              "downloaded_session": status.total_payload_download, "eta": calc_eta(status), "has_metadata": status.has_metadata, "hash": torrent_hash,
              "infohash_v1": infohash_v1 or "", "infohash_v2": infohash_v2 or "", "name": status.name, "num_complete": status.num_complete,
              "num_incomplete": status.num_incomplete, "num_leechs": max(0, status.num_peers - status.num_seeds), "num_seeds": status.num_seeds,
              "progress": round(status.progress, 3), "ratio": safe_ratio(status), "ratio_limit": -1, "reannounce": int(status.next_announce.total_seconds()),
              "save_path": status.save_path, "seeding_time": int(status.seeding_duration.total_seconds()), "seeding_time_limit": -1,
              "seen_complete": int(status.last_seen_complete or -1), "seq_dl": bool(status.flags & lt.torrent_flags.sequential_download), "size": status.total_wanted,
              "state": map_state(status), "time_active": int(status.active_duration.total_seconds()), "total_size": status.total, "tracker": status.current_tracker,
              "trackers_count": 1 if status.current_tracker else 0, "uploaded": status.all_time_upload, "uploaded_session": status.total_payload_upload,
              "upspeed": status.upload_rate, "category": category, }

    peers_list = []
    for p in torrent.get_peer_info():
        peers_list.append({
            "ip": p.ip[0],
            "port": p.ip[1],
            "client": p.client.decode("utf-8", errors="ignore") if isinstance(p.client, bytes) else str(p.client),
            "flags": format_peer_flags(p),
            "up_speed": p.up_speed,
            "down_speed": p.down_speed,
            "progress": p.progress,
        })
    mapped["peers"] = peers_list

    trackers_list = []
    for t in torrent.trackers():
        trackers_list.append({
            "url": t["url"],
            "verified": t["verified"],
            "next_announce": t.get("next_announce"),
            "min_announce": t.get("min_announce")
        })
    mapped["trackers"] = trackers_list

    return mapped
