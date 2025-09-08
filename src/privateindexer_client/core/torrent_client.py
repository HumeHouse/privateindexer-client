import asyncio
import datetime
import os
import shutil
import time

import libtorrent as lt

from privateindexer_client.core import database, utils
from privateindexer_client.core.config import TORRENTING_PORT, TORRENTS_DIR, ANNOUNCE_TRACKER_URL, FASTRESUME_DIR, FASTRESUME_INTERVAL, ANNOUNCE_IP
from privateindexer_client.core.logger import log
from privateindexer_client.core.thread_executor import FASTRESUME_EXECUTOR
from privateindexer_client.core.utils import process_fastresume_file

libtorrent_session: lt.session
_session_stats_now: dict[str, int] = None
_session_stats_prev: dict[str, int] = None
_session_stats_time_now: float = None
_session_stats_time_prev: float = None
_all_time_download: int = 0
_all_time_upload: int = 0


def create_libtorrent_session(app_version: str):
    """
    Initialize a libtorrent session with custom settings
    """
    global libtorrent_session, _all_time_download, _all_time_upload
    settings = {"listen_interfaces": f"0.0.0.0:{TORRENTING_PORT}",  # listen on all IPv4 interfaces
                "active_downloads": -1,  # allow unlimited downloads
                "active_seeds": -1,  # allow unlimited seeds
                "enable_dht": False, "enable_lsd": False, "enable_upnp": False,  # disable non-private torrent features
                "out_enc_policy": 0,  # force encrypted outgoing connections
                "in_enc_policy": 0,  # force encrypted incoming connections
                "validate_https_trackers": False,  # necessary because of OPENSSL stuff
                "user_agent": f"privateindexer-client/{app_version}",  # send custom user agent
                "always_send_user_agent": True,  # always send the user agent with every tracker request
                "seed_time_limit": -1,  # no seed limit for torrents
                "active_tracker_limit": -1,  # unlimited trackers
                "active_limit": -1,  # unlimited number of torrents
                "unchoke_slots_limit": -1,  # unlimited number of unchoked peers
                "connections_limit": -1,  # unlimited connections
                "seed_choking_algorithm": lt.seed_choking_algorithm_t.fastest_upload,  # choke based on upload speed
                }

    # add the manual announce IP if configured
    if ANNOUNCE_IP:
        settings["announce_ip"] = ANNOUNCE_IP

    libtorrent_session = lt.session(settings)
    _all_time_download, _all_time_upload = utils.load_persistent_stats()


def get_session_stats() -> tuple[
    dict[str, int] | None,
    float | None,
    dict[str, int] | None,
    float | None,
]:
    """
    Returns a 4-tuple of the current session stats including the timestamps they were gathered at
    """
    return (
        _session_stats_now.copy() if _session_stats_now else None,
        _session_stats_time_now,
        _session_stats_prev.copy() if _session_stats_prev else None,
        _session_stats_time_prev,
    )


def get_all_time_stats() -> tuple[int, int,]:
    """
    Returns a tuple of the current all-time download and upload stats
    """
    return _all_time_download, _all_time_upload


def get_all_torrents() -> list:
    """
    Return the list of all torrents currently added to the libtorrent session
    """
    return libtorrent_session.get_torrents()


async def add_torrent_for_seeding(torrent_file: str, save_path: str) -> bool:
    """
    Adds a single torrent file to libtorrent session in seed mode
    """
    if not os.path.exists(torrent_file):
        log.error(f"[TORCLIENT] Torrent file not found: {torrent_file}")
        return False

    try:
        info = lt.torrent_info(torrent_file)
        torrent_name = info.name()

        # make sure the torrent we're trying to seed has a v1 and a v2 hash
        hashes = info.info_hashes()
        if not hashes.has_v1():
            log.error(f"[TORCLIENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(torrent_file)
            return False
        if not hashes.has_v2():
            log.error(f"[TORCLIENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file)
            return False

        # skip torrent if torrent already exists in libtorrent session
        if await torrent_exists_in_session(info.info_hash()):
            return False

        # add the tracker URL
        info.add_tracker(ANNOUNCE_TRACKER_URL)

        params = {"ti": info, "save_path": os.path.dirname(save_path)}

        flags = lt.torrent_flags.default_flags | lt.torrent_flags.seed_mode
        params["flags"] = flags

        # add to the libtorrent session
        libtorrent_session.add_torrent(params)
        log.info(f"[TORCLIENT] Added torrent for seeding: {torrent_name}")
        return True
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to add torrent '{torrent_file}': {e}")
        return False


async def add_torrent_for_download(torrent_file: str, save_path: str) -> bool:
    """
    Adds a single torrent file to libtorrent session for download
    """
    if not os.path.exists(torrent_file):
        log.error(f"[TORCLIENT] Torrent file not found: {torrent_file}")
        return False

    # attempt to add the torrent to the client
    try:
        info = lt.torrent_info(torrent_file)
        torrent_name = info.name()

        # get the number of files in the torrent
        file_count = info.num_files()
        # get the number of files in the torrent
        total_size = info.total_size()

        # make sure the torrent we download has a v1 and a v2 hash
        hashes = info.info_hashes()
        if not hashes.has_v1():
            log.error(f"[TORCLIENT] Torrent '{torrent_name}' did not generate a v1 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v1 = str(hashes.v1)
        if not hashes.has_v2():
            log.error(f"[TORCLIENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash_v2 = str(hashes.v2)

        if await torrent_exists_in_session(info.info_hash()):
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

        # create the save path if it doesn't exist
        if not os.path.exists(save_path):
            os.makedirs(save_path)

        # add to the libtorrent session
        torrent_handle = libtorrent_session.add_torrent(params)
        # trigger a fastresume save task only
        torrent_handle.save_resume_data()
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to add new torrent: {e}")
        return False

    # add the data for the torrent to the database
    await utils.add_torrent_to_database(name=torrent_name, size=total_size, torrent_path=torrent_file_out, uploaded=True, files=file_count, category=0,
                                        download_path=save_path, hash_v1=torrent_hash_v1, hash_v2=torrent_hash_v2)

    log.info(f"[TORCLIENT] Added new torrent for download: {torrent_name}")
    return True


async def torrent_exists_in_session(info_hash: str | bytes) -> bool:
    """
    Checks for a torrent hash in the libtorrent session
    """
    # convert str hex to bytes hash
    try:
        if isinstance(info_hash, str):
            info_hash = lt.sha1_hash(bytes.fromhex(info_hash))

        existing = libtorrent_session.find_torrent(info_hash)
        return existing.is_valid()
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to check if torrent exists: {e}")
        return False


async def remove_torrent_by_hash(torrent_hash: str, remove_downloads: bool = False):
    """
    Remove a torrent from the libtorrent session if it exists
    Also removes fastresume data from the disk if it exists
    """
    try:
        info_hash = lt.sha1_hash(bytes.fromhex(torrent_hash))
        existing = libtorrent_session.find_torrent(info_hash)
        if existing.is_valid():
            libtorrent_session.remove_torrent(existing)
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to remove torrent: {e}")
        return False

    # remove the fastresume/fastresume-ignore files if either exists
    try:
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
        ignore_file = f"{fastresume_file}.ignore"
        for file in [fastresume_file, ignore_file]:
            if os.path.exists(file):
                os.unlink(file)
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to remove fastresume data when removing torrent: {e}")
        return False

    try:
        if remove_downloads:
            # try to remove the downloaded files if any exist
            result = await database.fetch_one("SELECT download_path FROM torrents WHERE hash_v1 = ? or hash_v2 = ?", (torrent_hash, torrent_hash,))
            if result and result.get("download_path"):
                os.unlink(result["download_path"])
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to remove downloads when removing torrent: {e}")
        return False


async def load_fastresume_data():
    """
    Load fastresume and torrent files from torrents dir into the session
    """
    log.info("[FASTRESUME] Loading fastresume data into torrent client")
    before = datetime.datetime.now()

    # build a map of all the hashes and their respective torrent files
    torrents = await database.fetch_all("SELECT * FROM torrents")
    torrent_hash_path_map = {t["hash_v1"]: t["torrent_path"] for t in torrents}

    loop = asyncio.get_running_loop()
    futures = []

    # loop through the files in the fastresume directory
    for fname in os.listdir(FASTRESUME_DIR):
        # ignore files we don't care about
        if not fname.endswith(".fastresume"):
            continue
        fastresume_path = os.path.join(FASTRESUME_DIR, fname)
        hash_v1 = fname.replace(".fastresume", "")
        torrent_path = torrent_hash_path_map.get(hash_v1)

        # remove fastresume files which do not have a matching torrent file
        if not torrent_path or not os.path.exists(torrent_path):
            ignore_file = f"{fastresume_path}.ignore"
            for file in [fastresume_path, ignore_file]:
                if os.path.exists(file):
                    os.unlink(file)
            log.info(f"[FASTRESUME] Removed dangling fastresume data with hash: {hash_v1}")
            continue

        # dispatch the fastresume file to the pool of worker threads
        futures.append(loop.run_in_executor(FASTRESUME_EXECUTOR, process_fastresume_file, fastresume_path, hash_v1, torrent_path))

    # collect results as they finish
    async for future in asyncio.as_completed(futures):
        try:
            raw_data, hash_v1, torrent_path = await future
            if raw_data and os.path.exists(torrent_path):
                # assemble the raw data into fastresume add_torrent_params
                atp = lt.read_resume_data(raw_data)

                # remove the fastresume data if it points to a save path that no longer exists
                if not os.path.exists(atp.save_path):
                    fastresume_file = os.path.join(FASTRESUME_DIR, f"{hash_v1}.fastresume")
                    ignore_file = f"{fastresume_file}.ignore"
                    for file in [fastresume_file, ignore_file]:
                        if os.path.exists(file):
                            os.unlink(file)
                    log.warning(f"[FASTRESUME] Removed invalid fastresume data with hash: {hash_v1}")
                    continue

                # attach the torrent info to the params
                atp.ti = lt.torrent_info(torrent_path)
                # add the torrent to the session
                libtorrent_session.add_torrent(atp)
        except Exception as e:
            log.error(f"[FASTRESUME] Error in fastresume data post-processing: {e}")

    # loop through the torrents in the database
    for torrent in torrents:
        # skip the torrent if it's already in the torrent client
        if await torrent_exists_in_session(torrent.get("hash_v1")):
            continue

        torrent_exists = os.path.exists(torrent["torrent_path"])
        download_path = torrent.get("download_path")
        download_exists = os.path.exists(download_path) if download_path else False
        media_path = torrent.get("media_path")
        media_exists = os.path.exists(media_path) if media_path else False

        # try to seed the download media first, then fall back to media path
        seed_path = download_path if download_exists else (media_path if media_exists else None)
        if torrent_exists and seed_path:
            if await add_torrent_for_seeding(torrent["torrent_path"], seed_path):
                log.info(f"[FASTRESUME] Re-added '{torrent["name"]}' for seeding from {"download" if download_exists else "media"} path")
            else:
                log.warning(f"[FASTRESUME] Unable to re-add '{torrent["name"]}' for seeding from {"download" if download_exists else "media"} path")

    delta = datetime.datetime.now() - before
    log.info(f"[FASTRESUME] Finished loading fastresume data ({delta})")


def save_all_fastresume_data() -> tuple[int, int]:
    """
    Immediately schedules a save of fastresume data for all torrents in the session
    This function waits for all alerts to clear before finishing
    """
    torrents = libtorrent_session.get_torrents()
    total = len(torrents)
    completed = 0
    try:
        hashes_to_await = set()
        for torrent in torrents:
            try:
                status = torrent.status()

                infohash_v1 = status.info_hashes.v1.to_bytes().hex() if status.info_hashes.has_v1() else None
                infohash_v2 = status.info_hashes.v2.to_bytes().hex() if status.info_hashes.has_v2() else None

                # trigger a fastresume save task only if no ignore file exists
                if utils.fastresume_ignore_exists(infohash_v1):
                    continue

                torrent.save_resume_data()

                # try using the v1 otherwise fall back to v2
                torrent_hash = infohash_v1 or infohash_v2
                hashes_to_await.add(torrent_hash)
            except Exception as e:
                log.error(f"[FASTRESUME] Error saving fastresume data for torrent: {e}")

        while hashes_to_await:
            alerts = libtorrent_session.pop_alerts()
            for alert in alerts:
                if isinstance(alert, lt.save_resume_data_alert):
                    torrent_hash = utils.save_fastresume_to_disk(alert)
                    if torrent_hash and torrent_hash in hashes_to_await:
                        hashes_to_await.remove(torrent_hash)
                        completed += 1
            # let the thread sleep so libtorrent has time to generate alerts
            time.sleep(0.1)
    except Exception as e:
        log.error(f"[FASTRESUME] Error saving fastresume data for all torrents: {e}")
    return completed, total


async def periodic_torrent_status_task():
    """
    Periodically check torrent status and validate error status every 5 seconds.
    Also sends a request to the libtorrent session to obtain session stats (async, caught during periodic_alerts_task)
    """
    log.debug("[STATUS] Task loop started")
    while True:
        try:
            # request session stats async
            libtorrent_session.post_session_stats()

            # loop through torrents and check their status
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
    log.debug("[FASTRESUME] Task loop started")
    while True:
        await asyncio.sleep(FASTRESUME_INTERVAL)
        try:
            log.info("[FASTRESUME] Saving all fastresume data")
            before = datetime.datetime.now()

            completed, total = save_all_fastresume_data()

            delta = datetime.datetime.now() - before
            log.info(f"[FASTRESUME] Fastresume task completed, {completed} saved, {total} total torrents ({delta})")
        except Exception as e:
            log.error(f"[FASTRESUME] Error in torrent fastresume loop: {e}")


async def periodic_alerts_task():
    """
    Periodically check for alerts and process them every 5 seconds
    """
    log.debug("[ALERTS] Task loop started")
    global _session_stats_now, _session_stats_prev
    global _session_stats_time_now, _session_stats_time_prev
    global _all_time_download, _all_time_upload

    while True:
        try:
            alerts = libtorrent_session.pop_alerts()
            for alert in alerts:

                # process fastresume available alerts
                if isinstance(alert, lt.save_resume_data_alert):
                    utils.save_fastresume_to_disk(alert)

                if isinstance(alert, lt.session_stats_alert):
                    # shift current snapshot to previous
                    if _session_stats_now:
                        _session_stats_prev = _session_stats_now
                        _session_stats_time_prev = _session_stats_time_now

                    # store new snapshot
                    _session_stats_now = alert.values
                    _session_stats_time_now = time.monotonic()

                    # update all-time totals
                    total_download = _session_stats_now["net.recv_bytes"]
                    total_upload = _session_stats_now["net.sent_bytes"]

                    _all_time_download += total_download - (_session_stats_prev["net.recv_bytes"]) if _session_stats_prev else 0
                    _all_time_upload += total_upload - (_session_stats_prev["net.sent_bytes"]) if _session_stats_prev else 0

                    # persist the stats to disk every 60 seconds
                    if int(time.monotonic()) % 60 == 0:
                        utils.save_persistent_stats(_all_time_download, _all_time_upload)
                        log.debug("[ALERTS] Saved persistent client stats")

        except Exception as e:
            log.error(f"[ALERTS] Error in torrent alerts loop: {e}")

        await asyncio.sleep(5)
