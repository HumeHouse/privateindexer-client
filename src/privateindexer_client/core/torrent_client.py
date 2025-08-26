import asyncio
import os

import libtorrent as lt

from privateindexer_client.core.config import TORRENTING_PORT, TORRENTS_DIR, ANNOUNCE_TRACKER_URL, API_KEY
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

        # add to the libtorrent session
        libtorrent_session.add_torrent(params)
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


async def periodic_torrent_status_task():
    """
    Periodically check torrent status and validate error status
    and status changes every 5 seconds.
    """
    while True:
        try:
            torrents = libtorrent_session.get_torrents()
            for t in torrents:
                status = t.status()

                name = status.name
                if status.errc and status.errc.value() != 0:
                    log.error(f"[STATUS] Torrent '{name}' is in error state")

        except Exception as e:
            log.error(f"[STATUS] Error in torrent status loop: {e}")

        await asyncio.sleep(5)
