import asyncio
import os
import shutil

import libtorrent as lt

from privateindexer_client.core.config import TORRENTING_PORT, TORRENTS_DIR, ANNOUNCE_TRACKER_URL, API_KEY, FASTRESUME_DIR
from privateindexer_client.core.logger import log

libtorrent_session: lt.session


def create_libtorrent_session():
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
        existing = libtorrent_session.find_torrent(info.info_hash())
        if existing.is_valid():
            log.warning(f"[TORCLIENT] Torrent already exists on client: {torrent_file}")
            return False

        params = {"ti": info, "save_path": save_path}

        # add to the libtorrent session and trigger a resume data save
        torrent_handle = libtorrent_session.add_torrent(params)

        status = torrent_handle.status()
        infohash_v1 = status.info_hashes.v1.to_bytes().hex() if status.info_hashes.has_v1() else None
        infohash_v2 = status.info_hashes.v2.to_bytes().hex() if status.info_hashes.has_v2() else None
        # try using the v1 otherwise fall back to v2
        torrent_hash = infohash_v1 or infohash_v2

        # save torrent metadata to a torrent file in the fastresume data directory
        torrent_file_out = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.torrent")
        try:
            shutil.copyfile(torrent_file, torrent_file_out)
            log.info(f"[TORCLIENT] Saved torrent file for {torrent_hash}")
        except Exception as e:
            log.error(f"[TORCLIENT] Failed to save torrent file for {torrent_hash}: {e}")

        # trigger a fastresume save task
        torrent_handle.save_resume_data()
    except Exception as e:
        log.error(f"[TORCLIENT] Failed to add new torrent: {e}")
        return False

    log.info(f"[TORCLIENT] Added new torrent to libtorrent client: {torrent_file}")
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

            params = {"ti": info, "save_path": os.path.dirname(torrent_metadata["path"])}

            flags = lt.torrent_flags.default_flags | lt.torrent_flags.seed_mode
            params["flags"] = flags

            # add to the libtorrent session
            libtorrent_session.add_torrent(params)
            added += 1
        except Exception as e:
            log.error(f"[TORCLIENT] Failed to add {torrent_metadata["name"]}: {e}")
    if added > 0:
        log.info(f"[TORCLIENT] Added {added} torrent(s) to libtorrent client")


async def save_fastresume_to_disk(alert: lt.save_resume_data_alert):
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
        return

    log.info(f"[FASTRESUME] Saved fastresume data for hash: {torrent_hash}")


async def load_fastresume_data():
    """
    Load fastresume and torrent files from torrents dir into the session
    """
    for fname in os.listdir(FASTRESUME_DIR):
        if not fname.endswith(".fastresume"):
            continue

        base = fname.replace(".fastresume", "")
        fastresume_path = os.path.join(FASTRESUME_DIR, fname)
        torrent_path = os.path.join(FASTRESUME_DIR, f"{base}.torrent")

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
                log.error(f"[FASTRESUME] Failed to load torrent metadata for hash: {base}: {e}")
                continue

            # add to session
            libtorrent_session.add_torrent(atp)
            log.info(f"[FASTRESUME] Loaded fastresume data for hash: {base}")

        except Exception as e:
            log.error(f"[FASTRESUME] Failed to read fastresume data for hash: {base}: {e}")


def save_all_fastresume_data():
    """
    Immediately schedules a save of fastresume data for all torrens in the session
    """
    try:
        torrents = libtorrent_session.get_torrents()
        for torrent in torrents:
            try:
                status = torrent.status()

                # only save fastresume data for torrents that are downloading
                if status.state == lt.torrent_status.downloading:
                    torrent.save_resume_data()
            except Exception as e:
                log.error(f"[FASTRESUME] Error saving fastresume data for torrent: {e}")

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
            torrents = libtorrent_session.get_torrents()
            for torrent in torrents:
                status = torrent.status()

                # only save fastresume data for torrents that are downloading
                if status.state == lt.torrent_status.downloading:
                    torrent.save_resume_data()

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
