import os

import libtorrent as lt

from privateindexer_client.core import database, config
from privateindexer_client.core.logger import log


def calc_eta(status: lt.torrent_status) -> int:
    """
    Calculate an eta based on torrent status (partially matched to how qBittorrent source does it)
    """
    if status.download_rate > 0 and status.total_wanted > 0:
        remaining = status.total_wanted - status.total_wanted_done
        return int(remaining / status.download_rate)
    return 8640000  # qBittorrent uses 100 days as "infinite ETA"


def safe_ratio(status: lt.torrent_status) -> float:
    """
    Converts torrent status to ratio based on download/upload values
    """
    upload = status.all_time_upload
    download = status.all_time_download

    if download > 0:
        return upload / download
    elif upload > 0:
        # return qBittorrent infinity equivalent
        return 8640000
    else:
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


async def map_torrents_to_qbit(client_torrents: list, category_filter: str = None) -> dict:
    """
    Convert a libtorrent.torrent_status object into a qBittorrent-compatible dict
    Most of this is default or general taken from the qBittorrent API docs
    Many of the keys were removed because they weren't required by the apps
    """
    torrents = await database.fetch_all("SELECT name, infohash FROM torrents")
    name_hash_map = {torrent["infohash"]: torrent["name"] for torrent in torrents}

    all_mapped_torrents = []

    for torrent in client_torrents:
        # check torrent handle for validity before proceeding
        if not torrent.is_valid():
            continue

        status = torrent.status()

        # try to match the save_path with the configured category paths
        category = detect_torrent_category(status.save_path)

        # skip if this torrent is not wanted by the category filter
        if category_filter and category != category_filter:
            continue

        # normalize the infohash due to raw bytes out of the status
        torrent_hash = status.info_hashes.v2.to_bytes().hex()

        # we want to show the name in the database, not the internal torrent name - it's usually ugly (we use the internal one as a fallback)
        torrent_name = name_hash_map.get(torrent_hash, status.name)

        mapped = {"added_on": int(status.added_time or 0), "content_path": os.path.join(status.save_path, status.name), "dlspeed": status.download_payload_rate,
                  "download_path": status.save_path, "downloaded": status.all_time_download, "downloaded_session": status.total_payload_download, "eta": calc_eta(status),
                  "hash": torrent_hash, "name": torrent_name, "num_complete": status.num_complete, "num_incomplete": status.num_incomplete,
                  "num_leechs": max(0, status.num_peers - status.num_seeds), "num_seeds": status.num_seeds, "progress": round(status.progress, 3),
                  "ratio": safe_ratio(status), "ratio_limit": -1, "save_path": status.save_path, "seeding_time": int(status.seeding_duration.total_seconds()),
                  "seeding_time_limit": -1, "size": status.total_wanted, "state": map_state(status), "uploaded": status.all_time_upload,
                  "uploaded_session": status.total_payload_upload, "upspeed": status.upload_payload_rate, "category": category, }

        peers_list = []
        for p in torrent.get_peer_info():
            peers_list.append({"ip": p.ip[0], "port": p.ip[1], "client": p.client.decode("utf-8", errors="ignore") if isinstance(p.client, bytes) else str(p.client),
                               "flags": format_peer_flags(p), "up_speed": p.payload_up_speed, "down_speed": p.payload_down_speed, "progress": p.progress, })
        mapped["peers"] = peers_list

        # we only ever have one tracker, ours, so process the first in the list
        tracker_data = next(iter(torrent.trackers()))
        mapped["tracker"] = {"verified": tracker_data["verified"], "next_announce": tracker_data.get("next_announce")}

        all_mapped_torrents.append(mapped)

    return all_mapped_torrents


def map_stats_to_qbit(stats_now: dict[str, int] | None, time_now: float | None, stats_prev: dict[str, int] | None, time_prev: float | None, all_time_download: int,
                      all_time_upload: int, ) -> dict[str, int | str]:
    """
    Converts the raw data from a current and previous update of libtorrent stats to match what qbit would normally return in an API request
    """
    mapped = {}

    # session totals
    total_download = stats_now["net.recv_payload_bytes"] if stats_now else 0
    total_upload = stats_now["net.sent_payload_bytes"] if stats_now else 0
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
    if stats_prev and time_now and time_prev:
        # offset the interval based on the previous timestamp
        interval = max(time_now - time_prev, 1e-6)

        prev_download = stats_prev.get("net.recv_payload_bytes", 0)
        prev_upload = stats_prev.get("net.sent_payload_bytes", 0)

        mapped["dl_info_speed"] = int((total_download - prev_download) / interval)
        mapped["up_info_speed"] = int((total_upload - prev_upload) / interval)
    else:
        mapped["dl_info_speed"] = 0
        mapped["up_info_speed"] = 0

    # base the connection status on the number of connections or if there is incoming traffic
    if (stats_now and stats_now.get("net.has_incoming_connections", 0)) or mapped["up_info_speed"] > 0:
        mapped["connection_status"] = "connected"
    else:
        mapped["connection_status"] = "disconnected"

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
