import os

import libtorrent as lt

from privateindexer_client.core import logger
from privateindexer_client.core.config import FASTRESUME_DIR


def process_fastresume_file(fastresume_path: str, torrent_hash: str, torrent_path: str | None):
    """
    Thread-safe way to read fastresume file bytes, returns raw data to main thread
    """
    try:
        # read bytes from fastresume file
        with open(fastresume_path, "rb") as f:
            data = f.read()
        logger.channel("fastresume").debug(f"Loaded fastresume file for hash: {torrent_hash}")
        return data, torrent_hash, torrent_path
    except Exception as e:
        logger.channel("fastresume").exception(f"Exception while reading fastresume file for hash: {torrent_hash}: {e}")
        return None, torrent_hash, torrent_path


def fastresume_ignore_exists(torrent_hash: str) -> str | bool:
    """
    Checks if a fastresume ignore file exists in the FASTRESUME_DIR for the given torrent hash
    Returns fastresume ignore file path if true
    """
    ignore_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume.ignore")

    return ignore_file if os.path.exists(ignore_file) else False


def save_fastresume_to_disk(alert: lt.save_resume_data_alert) -> str | None:
    """
    Takes the alert from libtorrent and processes the fastresume data into a file on the disk
    Creates fastresume-ignore file to skip saving the file if it is already seeding
    """
    try:
        torrent_handle = alert.handle

        status = torrent_handle.status()
        torrent_hash = status.info_hashes.v2.to_bytes().hex()

        logger.channel("fastresume").debug(f"Writing fastresume file for hash: {torrent_hash}")
        # save the fastresume data
        fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent_hash}.fastresume")
        with open(fastresume_file, "wb") as f:
            f.write(lt.bencode(alert.resume_data))

        ignore_file = f"{fastresume_file}.ignore"
        # add ignore file for next auto-save if this torrent is seeding if it doesn't already exist
        if status.state == lt.torrent_status.seeding and not os.path.exists(ignore_file):
            logger.channel("fastresume").debug(f"Creating fastresume-ignore file due to seeding status for hash: {torrent_hash}")
            with open(ignore_file, mode='a'):
                pass
    except Exception as e:
        logger.channel("fastresume").exception(f"Exception while saving fastresume data: {e}")
        return None

    logger.channel("fastresume").debug(f"Saved fastresume data for hash: {torrent_hash}")
    return torrent_hash
