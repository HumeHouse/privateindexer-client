import asyncio
import os
import shutil

import libtorrent as lt

from privateindexer_client.core import database
from privateindexer_client.core import utils
from privateindexer_client.core.config import TORRENTING_PORT, TORRENTS_DIR, ANNOUNCE_TRACKER_URL, FASTRESUME_DIR
from privateindexer_client.core.logger import log

libtorrent_session: lt.session


def create_libtorrent_session(app_version: str):
    """
    Initialize a libtorrent session with custom settings
    """
    global libtorrent_session
    settings = {"listen_interfaces": f"0.0.0.0:{TORRENTING_PORT}",  # listen on all IPv4 interfaces
                "active_downloads": -1,  # allow unlimited downloads
                "active_seeds": -1,  # allow unlimited seeds
                "enable_dht": False, "enable_lsd": False, "enable_upnp": False,  # disable non-private torrent features
                "out_enc_policy": 0,  # force encrypted outgoing connections
                "in_enc_policy": 0,  # force encrypted incoming connections
                "validate_https_trackers": False,  # necessary because of OPENSSL stuff
                "user_agent": f"privateindexer-client/{app_version}",  # send custom user agent
                "always_send_user_agent": True,  # always send the user agent with every tracker request
                }
    libtorrent_session = lt.session(settings)


def get_all_torrents() -> list:
    """
    Return the list of all torrents currently added to the libtorrent session
    """
    return libtorrent_session.get_torrents()


def add_torrent_for_download(torrent_file: str, save_path: str) -> bool:
    """
    Adds a single torrent file to libtorrent session
    """
    if not os.path.exists(torrent_file):
        log.error(f"[TORCLIENT] Torrent file not found: {torrent_file}")
        return False

    # attempt to add the torrent to the client
    try:
        # skip torrent if torrent already exists in libtorrent session
        info = lt.torrent_info(torrent_file)
        torrent_name = info.name()

        # get the number of files in the torrent
        file_count = info.num_files()
        # get the number of files in the torrent
        total_size = info.total_size()

        # make sure the torrent we download has a v1 and a v2 hash
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

        existing = libtorrent_session.find_torrent(info.info_hash())
        if existing.is_valid():
            log.warning(f"[TORCLIENT] Torrent already exists on client: {torrent_name}")
            return False

        # save torrent metadata to a torrent file in the torrents directory
        torrent_file_out = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")
        try:
            shutil.move(torrent_file, torrent_file_out)
            log.debug(f"[TORCLIENT] Saved torrent file for {torrent_name}")
        except Exception as e:
            log.error(f"[TORCLIENT] Failed to save torrent file for {torrent_name}: {e}")

        params = {"ti": info, "save_path": save_path}

        # add to the libtorrent session
        torrent_handle = libtorrent_session.add_torrent(params)
        # trigger a fastresume save task
        torrent_handle.save_resume_data()
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to add new torrent: {e}")
        return False

    # find the category based on the file save path
    category = utils.detect_torrent_category(save_path)

    # add the data for the torrent to the database
    await database.execute(
        "INSERT INTO torrents (name, size, download_path, torrent_path, uploaded, files, category, hash_v1, hash_v2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (torrent_name, total_size, save_path, torrent_file_out, True, file_count, category, torrent_hash_v1, torrent_hash_v2,))

    log.info(f"[TORCLIENT] Added new torrent for download: {torrent_name}")
    return True


def add_torrents_for_seeding(torrents_to_add: list[dict]):
    """
    Add multiple torrent files to libtorrent session
    Ensures torrent file exists before adding
    """
    added = 0
    for torrent_metadata in torrents_to_add:
        torrent_path = os.path.join(TORRENTS_DIR, f"{torrent_metadata['name']}.torrent")
        if not os.path.exists(torrent_path):
            log.error(f"[TORCLIENT] Torrent file not found: {torrent_path}")
            continue

        try:
            # skip torrent if torrent already exists in libtorrent session
            info_hash = lt.sha1_hash(bytes.fromhex(torrent_metadata["hash_v1"]))
            existing = libtorrent_session.find_torrent(info_hash)
            if existing.is_valid():
                continue

            # add the tracker URL
            info = lt.torrent_info(torrent_path)
            info.add_tracker(f"{ANNOUNCE_TRACKER_URL}?apikey={API_KEY}")

            params = {"ti": info, "save_path": os.path.dirname(torrent_metadata["media_path"])}

            flags = lt.torrent_flags.default_flags | lt.torrent_flags.seed_mode
            params["flags"] = flags

            # add to the libtorrent session
            libtorrent_session.add_torrent(params)
            added += 1
        except Exception as e:
            log.error(f"[TORCLIENT] Failed to add {torrent_metadata["name"]}: {e}")
    if added > 0:
        log.info(f"[TORCLIENT] Added {added} torrent(s) to libtorrent client")


async def remove_torrent_by_hash(torrent_hash: str, remove_downloads: bool = False):
    """
    Remove a torrent from the libtorrent session if it exists
    Also removes fastresume data from the disk if it exists
    """
    info_hash = lt.sha1_hash(bytes.fromhex(torrent_hash))
    existing = libtorrent_session.find_torrent(info_hash)
    if existing.is_valid():
        libtorrent_session.remove_torrent(existing)

    # remove the fastresume data if it exists
    fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
    if os.path.exists(fastresume_file):
        os.unlink(fastresume_file)

    if remove_downloads:
        # try to remove the downloaded files if any exist
        result = await database.fetch_one("SELECT download_path FROM torrents WHERE hash_v1 = ? or hash_v2 = ?", (torrent_hash, torrent_hash,))
        if result and result.get("download_path"):
            os.unlink(result["download_path"])


async def save_fastresume_to_disk(alert: lt.save_resume_data_alert) -> str | None:
    """
    Takes the alert from libtorrent and processes the fastresume data into a file on the disk
    """
    try:
        torrent_handle = alert.handle

        status = torrent_handle.status()
        infohash_v1 = status.info_hashes.v1.to_bytes().hex() if status.info_hashes.has_v1() else None
        infohash_v2 = status.info_hashes.v2.to_bytes().hex() if status.info_hashes.has_v2() else None
        # try using the v1 otherwise fall back to v2
        torrent_hash = infohash_v1 or infohash_v2

        # save the fastresume data
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
        with open(fastresume_file, "wb") as f:
            f.write(lt.bencode(alert.resume_data))
    except Exception as e:
        log.error(f"[FASTRESUME] Failed to save fastresume data: {e}")
        return None

    log.debug(f"[FASTRESUME] Saved fastresume data for hash: {torrent_hash}")
    return torrent_hash


async def load_fastresume_data():
    """
    Load fastresume and torrent files from torrents dir into the session
    """
    # get all the torrent files from the database and build a lookup table by hash_v1
    torrents = await database.fetch_all("SELECT hash_v1, torrent_path FROM torrents")
    torrent_hash_path_map = {t["hash_v1"]: t["torrent_path"] for t in torrents}

    # loop through all fastresume files in the directory
    for fname in os.listdir(FASTRESUME_DIR):
        if not fname.endswith(".fastresume"):
            continue

        fastresume_path = os.path.join(FASTRESUME_DIR, fname)

        # strip off the extension so we can use the hash for processing
        hash_v1 = fname.replace(".fastresume", "")

        # find the torrent file for the hash
        torrent_path = torrent_hash_path_map.get(hash_v1)

        # if the torrent path doesn't exist, remove the fastresume data
        if not torrent_path or not os.path.exists(torrent_path):
            os.unlink(fastresume_path)
            log.warning(f"[FASTRESUME] Removed invalid fastresume file with hash: {hash_v1}")
            continue

        try:
            # read fastresume data
            with open(fastresume_path, "rb") as f:
                data = f.read()
            atp = lt.read_resume_data(data)

            # read torrent metadata
            try:
                ti = lt.torrent_info(torrent_path)
                atp.ti = ti
            except Exception as e:
                log.error(f"[FASTRESUME] Failed to load torrent metadata for hash: {hash_v1}: {e}")
                continue

            # add to session
            libtorrent_session.add_torrent(atp)
            log.debug(f"[FASTRESUME] Loaded fastresume data for hash: {hash_v1}")

        except Exception as e:
            log.error(f"[FASTRESUME] Failed to read fastresume data for hash: {hash_v1}: {e}")


async def save_all_fastresume_data():
    """
    Immediately schedules a save of fastresume data for all torrents in the session
    This function waits for all alerts to clear before finishing
    """
    try:
        torrents = libtorrent_session.get_torrents()
        hashes_to_await = set()
        for torrent in torrents:
            try:
                status = torrent.status()

                torrent.save_resume_data()
                infohash_v1 = status.info_hashes.v1.to_bytes().hex() if status.info_hashes.has_v1() else None
                infohash_v2 = status.info_hashes.v2.to_bytes().hex() if status.info_hashes.has_v2() else None
                # try using the v1 otherwise fall back to v2
                torrent_hash = infohash_v1 or infohash_v2
                hashes_to_await.add(torrent_hash)
            except Exception as e:
                log.error(f"[FASTRESUME] Error saving fastresume data for torrent: {e}")
        while len(hashes_to_await) > 0:
            alerts = libtorrent_session.pop_alerts()
            for alert in alerts:

                # process fastresume alerts
                if isinstance(alert, lt.save_resume_data_alert):

                    torrent_hash = await save_fastresume_to_disk(alert)
                    if torrent_hash:
                        hashes_to_await.remove(torrent_hash)

    except Exception as e:
        log.error(f"[FASTRESUME] Error saving fastresume data for all torrents: {e}")


async def periodic_torrent_status_task():
    """
    Periodically check torrent status and validate error status every 5 seconds.
    """
    log.info("[STATUS] Task loop started")
    while True:
        try:
            torrents = libtorrent_session.get_torrents()
            for torrent in torrents:
                status = torrent.status()

                name = status.name
                if status.errc and status.errc.value() != 0:
                    log.error(f"[STATUS] Torrent '{name}' is in error state")

        except Exception as e:
            log.error(f"[STATUS] Error in torrent status loop: {e}")

        await asyncio.sleep(5)


async def periodic_fastresume_task():
    """
    Periodically schedule fastresume saves every 60 minutes
    """
    # TODO: allow user to change fastresume interval
    log.info("[FASTRESUME] Task loop started")
    while True:
        try:
            await save_all_fastresume_data()
        except Exception as e:
            log.error(f"[FASTRESUME] Error in torrent fastresume loop: {e}")

        await asyncio.sleep(3600)


async def periodic_alerts_task():
    """
    Periodically check for alerts and process them every 5 seconds
    """
    log.info("[ALERTS] Task loop started")
    while True:
        try:
            alerts = libtorrent_session.pop_alerts()
            for alert in alerts:

                # process fastresume available alerts
                if isinstance(alert, lt.save_resume_data_alert):
                    await save_fastresume_to_disk(alert)

        except Exception as e:
            log.error(f"[ALERTS] Error in torrent alerts loop: {e}")

        await asyncio.sleep(5)
