import datetime
import hashlib
import json
import os
import secrets
import time

import libtorrent as lt

from privateindexer_client.core import config, httpx_request, database, radarr, sonarr
from privateindexer_client.core.config import TORZNAB_CATEGORY_PATHS, API_KEY, INDEXER_API_URL, TORRENTS_DIR, FASTRESUME_DIR, APP_VERSION, STATS_FILE, \
    MOVIE_DIR, SONARR_URL, RADARR_URL
from privateindexer_client.core.logger import log

_file_piece_hash_cache: dict[str, dict[int, list[bytes]]] = {}


def detect_torznab_category(file_path: str) -> int:
    """
    Tries to match the file's path with the known torznab category directories and returns its ID
    """
    for cat_info in TORZNAB_CATEGORY_PATHS:
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
            response = await client.get(INDEXER_API_URL + "/user/stats", headers={"X-API-Key": API_KEY}, timeout=30)
            return response.json()
    except Exception as e:
        log.error(f"[INDEXER] Failed to fetch user stats: {e}")
        return None


async def send_torrent_to_indexer(torrent_path: str, category: int):
    """
    Attempt to upload the torrent file along with the category to the PrivateIndexer server
    Will mark a file as uploaded in the database if the server API returns a 409 status code
    """
    try:
        with open(torrent_path, "rb") as file:
            torrent_basename = os.path.basename(torrent_path)
            # build the request with all the necessary torrent metadata required by indexer
            files = {"torrent_file": (torrent_basename, file, "application/x-bittorrent")}
            data = {"category": category}

            async with httpx_request.get_client() as client:
                response = await client.post(INDEXER_API_URL + "/upload", headers={"X-API-Key": API_KEY}, data=data, files=files)

                # based on the response from API, we will know status of upload
                if response.status_code == 200:
                    log.info(f"[INDEXER] Successfully sent '{torrent_basename}' to indexer")
                    return True
                elif response.status_code == 409:
                    log.info(f"[INDEXER] Torrent {torrent_basename} already exists on indexer, marking as uploaded")
                    return True
                else:
                    log.warning(f"[INDEXER] Failed to send '{torrent_basename}' to indexer, will retry later: {response.status_code} - {response.text}")
                    return False
    except Exception as e:
        log.error(f"[INDEXER] Exception while sending '{torrent_basename}' to indexer, will retry later: {e}")
        return False


def using_legacy_media_source() -> bool:
    """
    Legacy function to check if the old MOVIE_DIR is being used or not
    # TODO: deprecated - remove in upcoming release
    """
    return MOVIE_DIR is not None


async def get_all_media_files() -> list[str]:
    """
    Returns list of file paths for all tracked media
    """
    media_files = []

    # TODO: deprecated - remove in upcoming release
    if using_legacy_media_source():
        for cat_info in TORZNAB_CATEGORY_PATHS:
            for root, _, files in os.walk(cat_info["path"]):
                for file in files:
                    media_files.append(os.path.join(root, file))

    else:
        # fetch all movie files from Radarr if configured
        if RADARR_URL:
            radarr_movies = await radarr.fetch_movie_library()
            for movie in radarr_movies:
                path = movie.get("movieFile", {}).get("path")
                if path:
                    media_files.append(path)

        # fetch all TV episode files from Sonarr if configured
        if SONARR_URL:
            sonarr_episodes = await sonarr.fetch_tv_library()
            for episode in sonarr_episodes:
                path = episode.get("path")
                if path:
                    media_files.append(path)

    return media_files


def hash_file_by_pieces(file_path: str, piece_length: int) -> list[str]:
    """
    Return list of SHA1 hashes (hex) of file split into piece_length chunks
    Caches results per file_path and piece_length
    """
    # try to return a value from the peice length cache
    if file_path in _file_piece_hash_cache:
        if piece_length in _file_piece_hash_cache[file_path]:
            log.debug(f"[TORRENT] Cache hit for file '{file_path}'")
            return _file_piece_hash_cache[file_path][piece_length]
    log.debug(f"[TORRENT] Cache miss for file '{file_path}'")

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

def create_torrent(media_path: str, output_torrent_file: str = None) -> tuple[dict, bool]:
    """
    Synchronous routine to build and generate a complete torrent file from the media passed in as media_path
    Checks if output torrent file already exists and skips the torrent generation process
    Will fail if v1/v2 hash checks do not succeeed
    Removes the torrent file if any failures occur so a new one can be generated
    """

    # check if the torrent file supplied exists
    if output_torrent_file and os.path.exists(output_torrent_file):
        is_new_file = False
        # skip generation if torrent exists
        log.debug(f"[TORRENT] Torrent file '{output_torrent_file}' already exists, generation will be skipped")

    else:
        is_new_file = True
        log.debug(f"[TORRENT] Torrent for '{media_path}' does not exist, generating one")

        log.info(f"[TORRENT] Creating torrent for '{os.path.basename(media_path)}'")

        # create the file storage object
        fs = lt.file_storage()

        # add file to the filestorage
        lt.add_files(fs, media_path)

        # create the torrent from the file storage object
        t = lt.create_torrent(fs)
        t.set_creator(f"PrivateIndexer Client v{APP_VERSION}")
        t.set_priv(True)
        lt.set_piece_hashes(t, os.path.dirname(media_path))
        torrent_data = t.generate()

        # this will become the name of the new torrent, if it's a file, split off the extension
        torrent_name, _ = os.path.splitext(os.path.basename(media_path)) if os.path.isfile(media_path) else os.path.basename(media_path)
        output_torrent_file = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")

        with open(output_torrent_file, "wb") as f:
            f.write(lt.bencode(torrent_data))

    # attempt to pull the v1 and v2 hash information from the torrent file, otherwise fail and remove torrent file from disk
    try:
        info = lt.torrent_info(output_torrent_file)
        torrent_name = info.name()
        hashes = info.info_hashes()
        if not hashes.has_v1():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(output_torrent_file)
            return None, False
        torrent_hash_v1 = str(hashes.v1)
        if not hashes.has_v2():
            log.error(f"[TORRENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(output_torrent_file)
            return None, False
        torrent_hash_v2 = str(hashes.v2)

        # get the number of files in the torrent
        file_count = info.num_files()
    except Exception as e:
        log.error(f"[TORRENT] Failed to read hash for '{output_torrent_file}', it has been removed: {e}")
        os.unlink(output_torrent_file)
        return None, False

    category_id = detect_torznab_category(media_path)
    # get size of media
    total_media_size = os.path.getsize(media_path)

    return ({"name": torrent_name, "size": total_media_size, "media_path": media_path, "torrent_path": output_torrent_file, "uploaded": False,
             "files": file_count, "category": category_id, "hash_v1": torrent_hash_v1, "hash_v2": torrent_hash_v2},
            is_new_file)


def create_torrent_threadsafe(media_path: str, output_torrent_file: str = None) -> tuple[dict, bool]:
    """
    Wraps the create_torrent() routine in a try/accept to catch all runtime errors
    """
    try:
        return create_torrent(media_path, output_torrent_file)
    except Exception as e:
        log.error(f"[TORRENT] Failed to create torrent for '{media_path}': {e}")
        return None, False


def file_exists_in_torrent(torrent_path: str, target_filename: str) -> bool:
    """
    Helper to check if file exists in torrent based on the index of a file inside the files() of a torrent_info object
    """
    # get the file storage from the torrent
    try:
        torrent_info = lt.torrent_info(torrent_path)
        fs = torrent_info.files()

        # loop through all the files and check for matches
        for i in range(fs.num_files()):
            filename = os.path.basename(fs.file_path(i))
            if filename == target_filename:
                return True
    except Exception as e:
        log.error(f"[TORRENT] Failed to get file index for '{target_filename}' in torrent {torrent_path}: {e}")
    return False


def find_existing_torrent(media_path: str) -> str | None:
    """
    Given a media path, check if a torrent already exists in TORRENTS_DIR with the same name or hash
    Returns the existing torrent path if found, otherwise None
    """
    # get just the name of file or directory without the path
    basename = os.path.basename(media_path)

    # try to find the torrent file based on the file or directory name
    torrent_file = os.path.join(TORRENTS_DIR, basename + ".torrent")
    if os.path.exists(torrent_file):
        log.debug(f"[TORRENT] Matched '{media_path}' to '{torrent_file}' by name")
        return torrent_file

    # if this media is a file, we can try to strip the extension off and find a match for the filename
    if os.path.isfile(media_path):
        filename, _ = os.path.splitext(os.path.basename(media_path))
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


def find_media_for_torrent(torrent_path: str, media_dir: str) -> str | None:
    """
    Effectively an inverse of find_existing_torrent() which tries to locate the media for a torrent
    Given a torrent file, check if the media already exists in media_dir with the same name or hash
    Returns the existing path if found, otherwise None
    """
    # walk through the media_dir directory to try and find a media file that has matching hash to the torrent file
    for root, _, files in os.walk(media_dir):
        for file in files:
            file_path = os.path.join(root, file)
            try:
                # return the media path if it matches the torrent
                if torrent_matches_file(torrent_path, file_path):
                    log.debug(f"[TORRENT] Matched '{file_path}' to '{torrent_path}' by hash")
                    return file_path
            except Exception as e:
                log.error(f"[TORRENT] Error comparing hash for '{file_path}' to '{torrent_path}': {e}")

    log.debug(f"[TORRENT] Couldn't find media for: '{torrent_path}")
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


def fastresume_ignore_exists(torrent_hash: str) -> bool:
    """
    Checks if a fastresume ignore file exists in the FASTRESUME_DIR for the given torrent hash
    """
    ignore_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume.ignore")

    # skip saving data if a fastresume-ignore file exists for this hash
    exists = os.path.exists(ignore_file)
    if exists:
        log.debug(f"[FASTRESUME] Found fastresume-ignore file for hash: {torrent_hash}")
        return True
    return False


def save_fastresume_to_disk(alert: lt.save_resume_data_alert) -> str | None:
    """
    Takes the alert from libtorrent and processes the fastresume data into a file on the disk
    Creates fastresume-ignore file to skip saving the file if it is already seeding
    """
    try:
        torrent_handle = alert.handle

        status = torrent_handle.status()
        # we only use v1_hash for storing fastresume data
        torrent_hash = status.info_hashes.v1.to_bytes().hex()

        log.debug(f"[FASTRESUME] Writing fastresume file for hash: {torrent_hash}")
        # save the fastresume data
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
        with open(fastresume_file, "wb") as f:
            f.write(lt.bencode(alert.resume_data))

        ignore_file = f"{fastresume_file}.ignore"
        # add ignore file for next auto-save if this torrent is seeding if it doesn't already exist
        if status.state == lt.torrent_status.seeding and not os.path.exists(ignore_file):
            log.debug(f"[FASTRESUME] Creating fastresume-ignore file due to seeding status for hash: {torrent_hash}")
            with open(ignore_file, mode='a'):
                pass
    except Exception as e:
        log.error(f"[FASTRESUME] Failed to save fastresume data: {e}")
        return None

    log.debug(f"[FASTRESUME] Saved fastresume data for hash: {torrent_hash}")
    return torrent_hash


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
               lt.torrent_status.finished: "finished", lt.torrent_status.seeding: "uploading", lt.torrent_status.checking_resume_data: "checkingResumeData", }
    peer_count = status.num_peers
    torrent_state = mapping.get(status.state, "unknown")
    if torrent_state == "downloading" and peer_count == 0:
        torrent_state = "stalledDL"
    if torrent_state == "uploading" and peer_count == 0:
        torrent_state = "stalledUP"
    return torrent_state


def format_peer_flags(peer: lt.peer_info) -> list[tuple[str, str]]:
    """
    Convert a libtorrent peer_info.flags into a qBittorrent-style string
    https://web.archive.org/web/20141111072948/http://www.utorrent.com/help/faq/misc#faq13
    """
    flags = []

    # downloading states
    if peer.flags & peer.interesting:
        if peer.flags & peer.remote_choked:
            flags.append(("d", "Trying to download - Interested (local) & Choked (peer)"))
        else:
            flags.append(("D", "Downloading - Interested (local) & Unchoked (peer)"))

    # uploading states
    if peer.flags & peer.remote_interested:
        if peer.flags & peer.choked:
            flags.append(("u", "Not uploading - Interested (peer) & Choked (local)"))
        else:
            flags.append(("U", "Uploading - Interested (peer) & Unchoked (local)"))

    if peer.flags & peer.optimistic_unchoke:
        flags.append(("O", "Optimistic unchoke"))

    if peer.flags & peer.snubbed:
        flags.append(("S", "Peer is snubbed"))

    if not (peer.flags & peer.outgoing_connection):
        flags.append(("I", "Incoming connection"))

    # unchoked by peer but we're not interested
    if not (peer.flags & peer.interesting) and not (peer.flags & peer.remote_choked):
        flags.append(("K", "Not downloading - Not interested (local) & Unchoked (peer)"))

    # we unchoked them but they’re not interested
    if not (peer.flags & peer.remote_interested) and not (peer.flags & peer.choked):
        flags.append(("?", "Not uploading - Not interested (peer) & Unchoked (local)"))

    # encrypted
    if peer.flags & peer.rc4_encrypted:
        flags.append(("E", "Encrypted traffic"))
    elif peer.flags & peer.plaintext_encrypted:
        flags.append(("e", "Encrypted handshake"))

    # uTP
    # the enum is missing for some reason in libtorrent python bindings
    if peer.flags & (1 << 17):
        flags.append(("P", "Peer using uTP"))

    return flags


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


def load_persistent_stats() -> tuple[int, int]:
    """
    Load the all-time download and upload stats from the stats file if it exists
    """
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as file:
                data = json.loads(file.read())
            return data.get("all_time_download", 0), data.get("all_time_upload", 0)
        except Exception:
            return 0, 0
    return 0, 0


def save_persistent_stats(all_time_download: int, all_time_upload: int):
    """
    Save the all-time download and upload stats to the stats file
    """
    data = {
        "all_time_download": all_time_download,
        "all_time_upload": all_time_upload
    }
    with open(STATS_FILE, "w") as file:
        file.write(json.dumps(data))


def map_stats_to_qbit(
        stats_now: dict[str, int],
        time_now: float,
        stats_prev: dict[str, int] | None,
        time_prev: float | None,
        all_time_download: int,
        all_time_upload: int,
) -> dict[str, int | str]:
    """
    Converts the raw data from a current and previous update of libtorrent stats to match what qbit would normally return in an API request
    """
    mapped = {}

    # session totals
    total_download = stats_now["net.recv_bytes"]
    total_upload = stats_now["net.sent_bytes"]
    mapped["dl_info_data"] = total_download
    mapped["up_info_data"] = total_upload

    # all-time totals
    mapped["alltime_dl"] = all_time_download
    mapped["alltime_ul"] = all_time_upload

    # global ratio is UL/DL if UL>0
    if all_time_upload > 0:
        mapped["global_ratio"] = round(all_time_upload / all_time_download, 2)
    else:
        mapped["global_ratio"] = 0.0

    # rates (compare with prev snapshot if available)
    if stats_prev and time_prev:
        # offset the interval based on the previous timestamp
        interval = max(time_now - time_prev, 1e-6)

        prev_download = stats_prev["net.recv_bytes"]
        prev_upload = stats_prev["net.sent_bytes"]

        mapped["dl_info_speed"] = int((total_download - prev_download) / interval)
        mapped["up_info_speed"] = int((total_upload - prev_upload) / interval)
    else:
        mapped["dl_info_speed"] = 0
        mapped["up_info_speed"] = 0

    # base the connection status on the number of connections or if there is incoming traffic
    if stats_now.get("net.has_incoming_connections", 0) or mapped["up_info_speed"] > 0:
        mapped["connection_status"] = "connected"
    else:
        mapped["connection_status"] = "disconnected"

    return mapped
