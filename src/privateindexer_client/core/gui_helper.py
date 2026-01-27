import os

import libtorrent as lt

from privateindexer_client.core import database, config, qbit_translator
from privateindexer_client.core.logger import log


def calculate_eta(status: lt.torrent_status) -> int:
    """
    Calculate an ETA based on torrent status
    """
    if status.download_rate > 0 and 0 < status.total_wanted != status.total_wanted_done:
        remaining = status.total_wanted - status.total_wanted_done
        return int(remaining / status.download_rate)

    # use -1 to signify no ETA
    return -1


def format_peer_flags(peer: lt.peer_info) -> list[tuple[str, str]]:
    """
    Format torrent peer flags into human-readable text with explaination of each status
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


async def format_torrents(client_torrents: list) -> dict:
    """
    Convert libtorrent session torrent status objects into a dict to be displayed in the dashboard
    """
    torrents = await database.fetch_all("SELECT name, infohash FROM torrents")
    name_hash_map = {torrent["infohash"]: torrent["name"] for torrent in torrents}

    formatted_torrents = []

    for torrent in client_torrents:
        # check torrent handle for validity before proceeding
        if not torrent.is_valid():
            continue

        status = torrent.status()

        # normalize the infohash due to raw bytes out of the status
        torrent_hash = status.info_hashes.v2.to_bytes().hex()

        # we want to show the name in the database, not the internal torrent name - it's usually ugly (we use the internal one as a fallback)
        torrent_name = name_hash_map.get(torrent_hash, status.name)

        mapped = {"added_on": int(status.added_time or 0), "download_speed": status.download_payload_rate, "upload_speed": status.upload_payload_rate,
                  "session_download": status.total_payload_download, "session_upload": status.total_payload_upload, "eta": calculate_eta(status),
                  "infohash": torrent_hash, "name": torrent_name, "total_seeds": status.num_complete, "total_peers": status.num_incomplete,
                  "connected_peers": status.num_peers, "connected_seeds": status.num_seeds, "progress": round(status.progress, 3), "save_path": status.save_path,
                  "size": status.total_wanted, "state": qbit_translator.map_state(status), }

        peers_list = []
        for p in torrent.get_peer_info():
            peers_list.append({"ip": p.ip[0], "port": p.ip[1], "client": p.client.decode("utf-8", errors="ignore") if isinstance(p.client, bytes) else str(p.client),
                               "flags": format_peer_flags(p), "upload_speed": p.payload_up_speed, "download_speed": p.payload_down_speed, "progress": p.progress, })
        mapped["peers"] = peers_list

        # we only ever have one tracker, ours, so process the first in the list
        tracker_data = next(iter(torrent.trackers()))
        mapped["tracker"] = {"verified": tracker_data["verified"], "next_announce": tracker_data.get("next_announce")}

        formatted_torrents.append(mapped)

    return formatted_torrents


def format_client_stats(stats_now: dict[str, int] | None, time_now: float | None, stats_prev: dict[str, int] | None, time_prev: float | None, all_time_download: int,
                        all_time_upload: int, ) -> dict[str, int | str]:
    """
    Convert internal client statistics into a dict of data that the dashboard can display
    """
    # all-time totals
    mapped = {"total_download": all_time_download, "total_upload": all_time_upload}

    # ratio is UL/DL if UL>0
    if all_time_upload > 0:
        mapped["client_ratio"] = round(all_time_upload / all_time_download, 2)
    else:
        mapped["client_ratio"] = 0.0

    # rates (compare with prev snapshot if available)
    if stats_prev and time_now and time_prev:
        # offset the interval based on the previous timestamp
        interval = max(time_now - time_prev, 1e-6)

        total_download = stats_now["net.recv_payload_bytes"] if stats_now else 0
        total_upload = stats_now["net.sent_payload_bytes"] if stats_now else 0

        prev_download = stats_prev.get("net.recv_payload_bytes", 0)
        prev_upload = stats_prev.get("net.sent_payload_bytes", 0)

        mapped["download_speed"] = int((total_download - prev_download) / interval)
        mapped["upload_speed"] = int((total_upload - prev_upload) / interval)
    else:
        mapped["download_speed"] = 0
        mapped["upload_speed"] = 0

    # torrent state counters
    mapped["seeding_torrents"] = stats_now["ses.num_seeding_torrents"] if stats_now else 0
    mapped["downloading_torrents"] = stats_now["ses.num_downloading_torrents"] if stats_now else 0

    # peer state counters
    mapped["peers_connected"] = stats_now["peer.num_peers_connected"] if stats_now else 0

    return mapped


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
    categories[name] = {"name": name, "savePath": save_dir}
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


def purge_empty_categories() -> int:
    """
    Helper function to recursively delete empty download directories in the tracked torrent category paths
    """
    deleted = set()

    for category_data in get_torrent_categories().values():
        root = category_data.get("savePath")

        for current_dir, subdirs, files in os.walk(root, topdown=False):

            still_has_subdirs = False
            for subdir in subdirs:
                if os.path.join(current_dir, subdir) not in deleted:
                    still_has_subdirs = True
                    break

            if not any(files) and not still_has_subdirs:
                try:
                    os.rmdir(current_dir)
                    deleted.add(current_dir)
                except Exception as e:
                    log.error(f"[SCAN] Exception while removing empty download directory: {e}")

    return len(deleted)
