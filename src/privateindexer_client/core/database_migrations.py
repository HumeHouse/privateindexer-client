import os.path
import shutil

import aiosqlite

from privateindexer_client.core import logger
from privateindexer_client.core.config import TORRENTS_DIR


async def v4_to_v5(db: aiosqlite.Connection):
    """
    Removes the legacy files column from torrents table
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "files" in cols:
        await db.execute("ALTER TABLE torrents DROP COLUMN files")
        logger.channel("database").info("Removed files column from torrents table")


async def v5_to_v6(db: aiosqlite.Connection):
    """
    Renames torrent files to organize by torrent hash
    Updates torrent path in database
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "torrent_path" not in cols:
        return

    cursor = await db.execute("SELECT id, torrent_path, infohash FROM torrents")
    torrents = await cursor.fetchall()

    renamed = 0

    for torrent in torrents:
        torrent_path = torrent["torrent_path"]
        if not os.path.exists(torrent_path):
            logger.channel("database").warning(f"Torrent file not found during migration: {torrent_path}")
            continue

        torrent_infohash = torrent["infohash"]
        new_torrent_path = os.path.join(TORRENTS_DIR, f"{torrent_infohash}.torrent")

        if torrent_path == new_torrent_path:
            continue

        if os.path.exists(new_torrent_path):
            logger.channel("database").critical(f"New torrent path already exists: {new_torrent_path}")
            continue

        try:
            shutil.move(torrent_path, new_torrent_path)

            torrent_id = torrent["id"]
            await db.execute("UPDATE torrents SET torrent_path = ? WHERE id = ?", (new_torrent_path, torrent_id,))

            renamed += 1
        except Exception as e:
            logger.channel("database").exception(f"Exception while renaming torrent file: {torrent_path}: {e}")

    logger.channel("database").info(f"Renamed {renamed} of {len(torrents)} torrent files")
