import asyncio
import datetime
import os
import shutil
import time

import libtorrent as lt

from privateindexer_client.core import database, utils, thread_executor
from privateindexer_client.core.config import TORRENTING_PORT, TORRENTS_DIR, ANNOUNCE_TRACKER_URL, FASTRESUME_DIR, FASTRESUME_INTERVAL, ANNOUNCE_IP, \
    STALE_TORRENT_THRESHOLD, LEW_MEMORY_MODE, ALLOW_UTP_CONNECTIONS
from privateindexer_client.core.logger import log
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

    settings: dict = lt.min_memory_usage() if LEW_MEMORY_MODE else {}

    settings.update({"listen_interfaces": f"0.0.0.0:{TORRENTING_PORT}",  # listen on all IPv4 interfaces
                     "enable_dht": False, "enable_lsd": False, "enable_upnp": False,  # disable non-private torrent features
                     "out_enc_policy": 0,  # force encrypted outgoing connections
                     "in_enc_policy": 0,  # force encrypted incoming connections
                     "validate_https_trackers": False,  # necessary because of OPENSSL stuff
                     "user_agent": f"privateindexer-client/{app_version}",  # send custom user agent
                     "always_send_user_agent": True,  # always send the user agent with every tracker request
                     "seed_time_limit": -1,  # no seed limit for torrents
                     "active_tracker_limit": -1,  # unlimited trackers
                     "active_limit": -1,  # unlimited number of torrents
                     "active_downloads": -1,  # allow unlimited downloads
                     "active_seeds": -1,  # allow unlimited seeds
                     "seed_choking_algorithm": lt.seed_choking_algorithm_t.fastest_upload,  # choke based on upload speed
                     "mixed_mode_algorithm": 0,  # disable TCP/uTP load balancer algorithm
                     })

    # enable/disable uTP
    settings.update({
        "enable_incoming_utp": ALLOW_UTP_CONNECTIONS,  # incoming uTP connections
        "enable_outgoing_utp": ALLOW_UTP_CONNECTIONS,  # outgoing uTP connections
    })

    # enable some extra memory-saving settings
    if LEW_MEMORY_MODE:
        settings.update({
            "max_queued_disk_bytes": 1024 * 512,  # limit disk queue (1/2 default)
            "connections_limit": 200,  # set a lower connection limit (default)
            "unchoke_slots_limit": 4,  # limit unchoked peers (1/2 default)
            "max_peerlist_size": 10000,  # limit the number of peers we keep (1/3 default)
        })
    else:
        settings.update({
            "max_queued_disk_bytes": -1,  # unlimited queued disk bytes
            "connections_limit": -1,  # unlimited connections
            "unchoke_slots_limit": -1,  # unlimited number of unchoked peers
            "max_out_request_queue": 1500,  # increase number of outstanding requests to send to a peer 3x (default 500)
            "file_pool_size": 250,  # increase file pool size (default 40)
            "connection_speed": 500,  # bump connection rate to 500/s (default 30)
            "send_buffer_low_watermark": 1048576,  # bump low buffer 10x (default 10*1024)
            "send_buffer_watermark": 3145728,  # bump buffer 6x (default 500*1024)
            "send_buffer_watermark_factor": 150,  # bump factor 3x (default 50)
            "max_peer_recv_buffer_size": 6291456,  # bump peer receive by 3x (default 2*1024*1024)
        })

    # add the manual announce IP if configured
    if ANNOUNCE_IP:
        settings["announce_ip"] = ANNOUNCE_IP

    log.debug("[TORCLIENT] Settings active:")
    for key, value in settings.items():
        log.debug(f"[TORCLIENT] {key}: {value}")

    libtorrent_session = lt.session(settings)
    _all_time_download, _all_time_upload = utils.load_persistent_stats()


def get_session_stats() -> tuple[dict[str, int] | None, float | None, dict[str, int] | None, float | None,]:
    """
    Returns a 4-tuple of the current session stats including the timestamps they were gathered at
    """
    return (_session_stats_now.copy() if _session_stats_now else None, _session_stats_time_now, _session_stats_prev.copy() if _session_stats_prev else None,
            _session_stats_time_prev,)


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
        log.critical(f"[TORCLIENT] Torrent file not found: {torrent_file}")
        return False

    try:
        info = lt.torrent_info(torrent_file)

        # make sure the torrent we're trying to seed has v2 hash
        hashes = info.info_hashes()
        if not hashes.has_v2():
            log.critical(f"[TORCLIENT] Torrent '{torrent_file}' did not generate a v2 hash, it has been removed")
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
        libtorrent_session.async_add_torrent(params)
        log.info(f"[TORCLIENT] Added torrent for seeding: {torrent_file}")
        return True
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while adding torrent '{torrent_file}': {e}")
        return False


async def add_torrent_for_download(torrent_file: str, save_path: str) -> bool:
    """
    Adds a single torrent file to libtorrent session for download
    """
    if not os.path.exists(torrent_file):
        log.critical(f"[TORCLIENT] Torrent file not found: {torrent_file}")
        return False

    # attempt to add the torrent to the client
    try:
        info = lt.torrent_info(torrent_file)
        torrent_name = os.path.splitext(os.path.basename(torrent_file))[0]

        # get the number of files in the torrent
        file_count = info.num_files()
        # get the number of files in the torrent
        total_size = info.total_size()

        # make sure the torrent we download has a v2 hash
        hashes = info.info_hashes()

        if not hashes.has_v2():
            log.critical(f"[TORCLIENT] Torrent '{torrent_name}' did not generate a v2 hash, it has been removed")
            os.unlink(torrent_file)
            return None
        torrent_hash = str(hashes.v2)

        if await torrent_exists_in_session(info.info_hash()):
            log.warning(f"[TORCLIENT] Torrent already exists on client: {torrent_name}")
            return False

        # save torrent metadata to a torrent file in the torrents directory
        torrent_file_out = os.path.join(TORRENTS_DIR, f"{torrent_name}.torrent")
        try:
            shutil.move(torrent_file, torrent_file_out)
            log.debug(f"[TORCLIENT] Saved torrent file for {torrent_name}")
        except Exception as e:
            log.error(f"[TORCLIENT] Exception while saving torrent file for {torrent_name}: {e}")

        save_path = os.path.join(save_path, torrent_hash)

        params = {"ti": info, "save_path": save_path}

        # create the save path if it doesn't exist
        if not os.path.exists(save_path):
            os.makedirs(save_path)

        # add to the libtorrent session
        torrent_handle = libtorrent_session.add_torrent(params)
        # trigger a fastresume save task only
        torrent_handle.save_resume_data()
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while adding new torrent: {e}")
        return False

    # add the data for the torrent to the database
    await utils.add_torrent_to_database(name=torrent_name, size=total_size, torrent_path=torrent_file_out, uploaded=True, files=file_count, category=0,
                                        download_path=save_path, torrent_hash=torrent_hash)

    log.info(f"[TORCLIENT] Added new torrent for download: {torrent_name}")
    return True


async def torrent_exists_in_session(torrent_hash: str | bytes) -> bool:
    """
    Checks for a torrent hash in the libtorrent session
    """
    try:
        # convert str hex to sha1 using libtorrent if it comes as str
        if isinstance(torrent_hash, str):
            torrent_hash = lt.sha1_hash(bytes.fromhex(torrent_hash))

        existing = libtorrent_session.find_torrent(torrent_hash)
        return existing.is_valid()
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while checking if torrent exists: {e}")
        return False


async def remove_torrent_by_hash(torrent_hash: str, remove_downloads: bool = False) -> bool:
    """
    Remove a torrent from the libtorrent session if it exists
    Also removes fastresume data from the disk if it exists
    Optionally removes download path for torrent
    Returns bool True if successful, False otherwise
    """
    try:
        sha1_hash = lt.sha1_hash(bytes.fromhex(torrent_hash))
        existing = libtorrent_session.find_torrent(sha1_hash)
        if existing.is_valid():
            libtorrent_session.remove_torrent(existing)
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while removing torrent: {e}")
        return False

    # remove the fastresume/fastresume-ignore files if either exists
    try:
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
        ignore_file = f"{fastresume_file}.ignore"
        for file in [fastresume_file, ignore_file]:
            if os.path.exists(file):
                os.unlink(file)
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while removing fastresume data when removing torrent: {e}")
        return False

    try:
        if remove_downloads:
            # try to remove the downloaded files if any exist
            result = await database.fetch_one("SELECT download_path FROM torrents WHERE infohash = ?", (torrent_hash,))
            if result and result.get("download_path"):
                download_path = result["download_path"]
                if os.path.isfile(download_path):
                    os.unlink(download_path)
                else:
                    shutil.rmtree(download_path)
    except Exception as e:
        log.error(f"[TORCLIENT] Exception while removing downloads when removing torrent: {e}")
        return False

    log.info(f"[TORCLIENT] Removed torrent with hash: {torrent_hash}")
    return True


async def load_fastresume_data():
    """
    Load fastresume and torrent files from torrents dir into the session
    """
    log.info("[FASTRESUME] Loading fastresume data into torrent client, this may take a while")
    before = datetime.datetime.now()

    # fetch all torrents from database
    torrents = await database.fetch_all("SELECT * FROM torrents")

    # build a map of all the hashes and their respective torrent data
    torrent_map = {t["infohash"]: t for t in torrents}

    # cleanup dangling fastresume files
    for fname in os.listdir(FASTRESUME_DIR):
        # ignore files we don't care about
        if not fname.endswith(".fastresume"):
            continue
        torrent_hash = os.path.splitext(fname)[0]
        torrent = torrent_map.get(torrent_hash)
        fastresume_path = os.path.join(FASTRESUME_DIR, fname)

        # remove fastresume files which do not have a matching torrent file
        if not torrent or not os.path.exists(torrent["torrent_path"]):
            for path in [fastresume_path, f"{fastresume_path}.ignore"]:
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except Exception as e:
                        log.error(f"[FASTRESUME] Exception while removing dangling fastresume data with hash '{torrent_hash}': {e}")

            log.info(f"[FASTRESUME] Removed dangling fastresume data with hash: {torrent_hash}")

    loop = asyncio.get_running_loop()
    futures = []

    # spawn a new executor
    executor = thread_executor.get_fastresume_executor()

    # loop through the torrents in the database
    for torrent in torrents:
        torrent_hash = torrent["infohash"]
        torrent_path = torrent["torrent_path"]
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")

        # load the torrent if fastresume data exists
        if os.path.exists(fastresume_file):
            log.debug(f"[FASTRESUME] Queueing fastresume data processing for hash: {torrent_hash}")
            # dispatch the fastresume file to the pool of worker threads
            futures.append(loop.run_in_executor(executor, process_fastresume_file, fastresume_file, torrent_hash, torrent_path))
            continue

        # skip the torrent if it's already in the torrent client
        if await torrent_exists_in_session(torrent_hash):
            continue

        # if no fastresume data exists, add the torrent manually so libtorrent can do a re-check and continue seeding
        download_path = torrent.get("download_path")
        download_exists = os.path.exists(download_path) if download_path else False
        media_path = torrent.get("media_path")
        media_exists = os.path.exists(media_path) if media_path else False

        # try to seed the download media first, then fall back to media path
        seed_path = download_path if download_exists else (media_path if media_exists else None)
        if os.path.exists(torrent_path) and seed_path:
            if await add_torrent_for_seeding(torrent_path, seed_path):
                log.info(f"[FASTRESUME] Re-added '{torrent["name"]}' for seeding from {"download" if download_exists else "media"} path")
            else:
                log.warning(f"[FASTRESUME] Unable to re-add '{torrent["name"]}' for seeding from {"download" if download_exists else "media"} path")

    log.info(f"[FASTRESUME] Queued {len(futures)} fastresume data files for processing")

    # collect results as they finish
    async for future in asyncio.as_completed(futures):
        try:
            raw_data, torrent_hash, torrent_path = await future
            if not raw_data or not os.path.exists(torrent_path):
                continue
            # assemble the raw data into fastresume add_torrent_params
            atp = lt.read_resume_data(raw_data)

            # remove the fastresume data if it points to a save path that no longer exists
            if not os.path.exists(atp.save_path):
                fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
                ignore_file = f"{fastresume_file}.ignore"
                for file in [fastresume_file, ignore_file]:
                    if os.path.exists(file):
                        try:
                            os.unlink(file)
                        except Exception as e:
                            log.error(f"[FASTRESUME] Exception while removing invalid fastresume file '{file}': {e}")
                log.warning(f"[FASTRESUME] Removed invalid fastresume data with hash: {torrent_hash}")
                continue

            # attach the torrent info to the params
            atp.ti = lt.torrent_info(torrent_path)
            # add the torrent to the session
            libtorrent_session.async_add_torrent(atp)
        except Exception as e:
            log.error(f"[FASTRESUME] Exception during fastresume data post-processing: {e}")

    executor.shutdown()
    log.debug(f"[FASTRESUME] Executor workers closed")

    delta = datetime.datetime.now() - before
    log.info(f"[FASTRESUME] Finished loading fastresume data ({delta})")


def save_all_fastresume_data() -> tuple[int, int]:
    """
    Immediately schedules a save of fastresume data for all torrents in the session
    This function waits for all alerts to clear before finishing
    """
    torrents = get_all_torrents()
    total = len(torrents)
    completed = 0
    try:
        hashes_to_await = set()
        for torrent in torrents:
            try:
                status = torrent.status()

                torrent_hash = status.info_hashes.v2.to_bytes().hex()

                # trigger a fastresume save task only if no ignore file exists
                if utils.fastresume_ignore_exists(torrent_hash):
                    continue

                torrent.save_resume_data()

                hashes_to_await.add(torrent_hash)
            except Exception as e:
                log.error(f"[FASTRESUME] Exception while saving fastresume data for torrent: {e}")

        idle_loops = 0

        while hashes_to_await:
            alerts = libtorrent_session.pop_alerts()

            # track empty loops
            if alerts:
                idle_loops = 0
            else:
                idle_loops += 1

            for alert in alerts:
                if isinstance(alert, lt.save_resume_data_alert):
                    # save the data to disk
                    hash_saved = utils.save_fastresume_to_disk(alert)

                    # if the write was successful, remove the hash from pending
                    if hash_saved:
                        hashes_to_await.discard(hash_saved)
                        completed += 1

            # break the loop if the alert doesn't show for 300 loop cycles or about 30 seconds
            if idle_loops > 300:
                log.warning(f"[FASTRESUME] No resume alerts for 30s, {len(hashes_to_await)} torrents did not complete")
                break

            # let the thread sleep so libtorrent has time to generate alerts
            time.sleep(0.1)
    except Exception as e:
        log.error(f"[FASTRESUME] Exception while saving fastresume data for all torrents: {e}")
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
            torrents = get_all_torrents()
            for torrent in torrents:
                status = torrent.status()
                # get the infohash stored as raw bytes
                torrent_hash = status.info_hashes.v2.to_bytes().hex()

                if status.errc and status.errc.value() != 0:
                    log.critical(f"[STATUS] Torrent in error state: {torrent_hash}")

                is_downloading = status.state == lt.torrent_status.downloading
                is_seeding = status.state == lt.torrent_status.seeding
                added_delta = datetime.datetime.now() - datetime.datetime.fromtimestamp(int(status.added_time or 0))

                # check if torrent is not currently seeding but has a fastresume ignore file - remove if true
                if not is_seeding:
                    ignore_file = utils.fastresume_ignore_exists(torrent_hash)
                    if ignore_file:
                        os.unlink(ignore_file)

                # check if torrent is downloading and has been downloading for more than the threshold with no progress OR 2x the threshold with >0 progress
                if is_downloading and ((added_delta.total_seconds() > STALE_TORRENT_THRESHOLD and status.progress == 0) or (
                        added_delta.total_seconds() > 2 * STALE_TORRENT_THRESHOLD and status.progress > 0)):
                    log.warning(f"[STATUS] Removing stale torrent: {torrent_hash}")

                    # remove from client and database
                    await remove_torrent_by_hash(torrent_hash, True)
                    await utils.remove_torrent_from_database(torrent_hash)
                    continue

                # check if torrent is downloading but has no download path stored in the database, in other words "frozen download"
                # this means it probably lost one or more files and is not supposed to be in this state
                if is_downloading:
                    # pull the download path from the database
                    result = await database.fetch_one("SELECT download_path, torrent_path FROM torrents WHERE infohash = ?", (torrent_hash,))
                    if result and not result.get("download_path"):
                        log.warning(f"[STATUS] Removing download-frozen torrent: {torrent_hash}")

                        # remove from client and database
                        await remove_torrent_by_hash(torrent_hash, True)
                        await utils.remove_torrent_from_database(torrent_hash, torrent_file=result["torrent_path"])

        except Exception as e:
            log.error(f"[STATUS] Exception during torrent status loop: {e}")

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
            log.error(f"[FASTRESUME] Exception during torrent fastresume loop: {e}")


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

                if isinstance(alert, lt.performance_alert):
                    log.critical(f"[ALERTS] Performance alert detected: {alert}")

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
            log.error(f"[ALERTS] Exception during torrent alerts loop: {e}")

        await asyncio.sleep(5)
