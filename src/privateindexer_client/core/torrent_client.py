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
                "active_downloads": 0,  # disable downloads
                "active_seeds": -1,  # allow unlimited seeds
                "enable_dht": False, "enable_lsd": False, "enable_upnp": False,  # disable non-private torrent features
                "out_enc_policy": 0,  # force encrypted outgoing connections
                "in_enc_policy": 0,  # force encrypted incoming connections
                "validate_https_trackers": False,  # necessary because of OPENSSL stuff
                }
    libtorrent_session = lt.session(settings)


def get_seeding_torrents() -> list:
    """
    Return the list of all torrents currently added to the libtorrent session
    """
    return libtorrent_session.get_torrents()


def seed_torrents(torrents_to_add: list[dict]):
    """
    Add multiple torrent files to libtorrent session for seeding
    Ensures torrent file exists before adding
    """
    added = 0
    for torrent_metadata in torrents_to_add:
        torrent_path = os.path.join(TORRENTS_DIR, f"{torrent_metadata['name']}.torrent")
        if not os.path.exists(torrent_path):
            log.error(f"[SEEDER] Torrent file not found: {torrent_path}")
            continue

        try:
            # skip torrent if libtorrent session is already seeding it
            info_hash = lt.sha1_hash(bytes.fromhex(torrent_metadata["hash_v1"]))
            existing = libtorrent_session.find_torrent(info_hash)
            if existing.is_valid():
                continue

            # add the tracker URL and set parameters for seeding
            info = lt.torrent_info(torrent_path)
            info.add_tracker(f"{ANNOUNCE_TRACKER_URL}?apikey={API_KEY}")

            flags = lt.torrent_flags.default_flags | lt.torrent_flags.seed_mode
            params = {"ti": info, "save_path": os.path.dirname(torrent_metadata["path"]), "flags": flags}

            # add to the libtorrent session
            libtorrent_session.add_torrent(params)
            added += 1
        except Exception as e:
            log.error(f"[SEEDER] Failed to add {torrent_metadata["name"]}: {e}")
    if added > 0:
        log.info(f"[SEEDER] Added {added} torrents to seed client")


async def periodic_torrent_status():
    """
    Periodically check torrent status and log peer connections/disconnections
    and status changes every 5 seconds.
    """
    while True:
        try:
            # poll torrent statuses
            torrents = libtorrent_session.get_torrents()
            for t in torrents:
                status = t.status()
                name = status.name
                new_state = str(status.state)

                # detect non seeding torrents and report
                if new_state != "seeding":
                    log.info(f"[STATUS] Torrent '{name}' is not seeding (currently in {new_state})")

        except Exception as e:
            log.error(f"[STATUS] Error in torrent status loop: {e}")

        await asyncio.sleep(5)
