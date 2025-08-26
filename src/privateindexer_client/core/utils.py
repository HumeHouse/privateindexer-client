import hashlib
import json
import os
import secrets
import time

import httpx
import libtorrent as lt

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


async def fetch_indexer_user_data():
    """
    Request the current user's indexer statistics for use in the GUI
    """
    try:
        async with httpx.AsyncClient() as client:
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

            async with httpx.AsyncClient() as client:
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


def create_torrent(media_file_path: str):
    """
    Main synchronous routine to build and generate a complete torrent file from the media passed in as file_path
    Checks for existing torrent file in case database save operation was interrupted from a previous app run
    Will fail if v1/v2 hash checks do not succeeed
    Removes the torrent file if any failures occur so a new one can be generated
    """
    # split the extension off the filename, this will become the name of the torrent if needed
    torrent_name, _ = os.path.splitext(os.path.basename(media_file_path))
    torrent_file_path = find_existing_torrent(media_file_path)

    if torrent_file_path:
        log.info(f"[TORRENT] Torrent '{torrent_name}' already exists")
    else:
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
    except Exception as e:
        log.error(f"[TORRENT] Failed to read hash for '{torrent_name}', it has been removed: {e}")
        os.unlink(torrent_file_path)
        return None

    size = os.path.getsize(media_file_path)
    category_id = detect_category(media_file_path)

    return {"name": torrent_name, "size": size, "path": media_file_path, "uploaded": False, "files": 1, "category": category_id, "hash_v1": torrent_hash_v1,
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


def find_existing_torrent(media_file_path: str) -> str | None:
    """
    Given a media file path, check if a torrent already exists in TORRENTS_DIR with the same info_hash (v1 or v2)
    Returns the existing torrent path if found, otherwise None
    """
    torrent_name, _ = os.path.splitext(os.path.basename(media_file_path))

    try:
        # build a temporary torrent object in-memory for this media file
        fs = lt.file_storage()
        fs.set_name(torrent_name)
        lt.add_files(fs, media_file_path)
        t = lt.create_torrent(fs)
        t.set_priv(True)
        lt.set_piece_hashes(t, os.path.dirname(media_file_path))
        new_info = lt.torrent_info(t.generate())
        new_hashes = new_info.info_hashes()
        new_hash_v1 = str(new_hashes.v1) if new_hashes.has_v1() else None
        new_hash_v2 = str(new_hashes.v2) if new_hashes.has_v2() else None
    except Exception as e:
        log.error(f"[TORRENT] Failed to generate info hash for '{media_file_path}': {e}")
        return None

    # compare against torrents in our directory
    for f in os.listdir(TORRENTS_DIR):
        if not f.endswith(".torrent"):
            continue

        existing_path = os.path.join(TORRENTS_DIR, f)
        try:
            existing_info = lt.torrent_info(existing_path)
            existing_hashes = existing_info.info_hashes()

            if (
                    (new_hash_v1 and existing_hashes.has_v1() and str(existing_hashes.v1) == new_hash_v1) or
                    (new_hash_v2 and existing_hashes.has_v2() and str(existing_hashes.v2) == new_hash_v2)
            ):
                return existing_path
        except Exception:
            continue  # skip invalid torrent files

    return None


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
              "upspeed": status.upload_rate, }

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
