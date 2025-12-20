import os.path

import aiosqlite

from privateindexer_client.core.config import FASTRESUME_DIR
from privateindexer_client.core.logger import log


async def v0_to_v1(db: aiosqlite.Connection):
    """
    Adds app_id column to torrents table
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "app_id" not in cols:
        await db.execute("ALTER TABLE torrents ADD COLUMN app_id INTEGER")
        log.info("[DATABASE] Added app_id column to torrents table")


async def v1_to_v2(db: aiosqlite.Connection):
    """
    Rewrites the category 1000 values to category 2000 to conform to Newznab specification
    """
    await db.execute("UPDATE torrents SET category = 2000 WHERE category = 1000")
    log.info("[DATABASE] Updated category 1000 values to category 2000 in torrents table")


async def v2_to_v3(db: aiosqlite.Connection):
    """
    Renames the hash_v2 column to infohash in torrents table
    Removes the legacy hash_v1 column from torrents table
    Renames all v1 hash files with their v2 hash - fastresume, ignore files
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "hash_v1" in cols:
        cursor = await db.execute("SELECT hash_v1, hash_v2 FROM torrents")
        old_data = await cursor.fetchall()

        renamed = 0

        for torrent in old_data:
            old_fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent["hash_v1"]}.fastresume")
            new_fastresume_file = os.path.join(FASTRESUME_DIR, f"{torrent["hash_v2"]}.fastresume")
            if os.path.exists(old_fastresume_file):
                os.rename(old_fastresume_file, new_fastresume_file)
                renamed += 1

            old_ignore_file = f"{old_fastresume_file}.ignore"
            new_ignore_file = f"{new_fastresume_file}.ignore"
            if os.path.exists(old_ignore_file):
                os.rename(old_ignore_file, new_ignore_file)
                renamed += 1

        log.info(f"[DATABASE] Renamed {renamed} fastresume, ignore files with v2 hash")

        await db.execute("ALTER TABLE torrents DROP COLUMN hash_v1")
        log.info("[DATABASE] Removed hash_v1 column from torrents table")

    if "infohash" not in cols:
        await db.execute("ALTER TABLE torrents RENAME COLUMN hash_v2 to infohash")
        log.info("[DATABASE] Renamed hash_v2 column to infohash in torrents table")
