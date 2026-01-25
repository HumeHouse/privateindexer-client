import os.path
import shutil

import aiosqlite

from privateindexer_client.core.config import FASTRESUME_DIR, TORRENTS_DIR
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


async def v3_to_v4(db: aiosqlite.Connection):
    """
    Removes the legacy media_path column from torrents table
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "media_path" in cols:
        cursor = await db.execute("SELECT id, media_path FROM torrents")
        old_data = await cursor.fetchall()

        converted = 0

        for torrent in old_data:
            torrent_id = torrent["id"]
            old_media_path = torrent["media_path"]

            if old_media_path is None:
                continue

            if os.path.exists(old_media_path) and os.path.isfile(old_media_path):
                file_size = os.path.getsize(old_media_path)
                await db.execute("INSERT INTO media (torrent_id, size, file_path) VALUES (?, ?, ?)", (torrent_id, file_size, old_media_path))
                converted += 1

        log.info(f"[DATABASE] Converted {converted} torrent media paths to new media table out of {len(old_data)} total")

        await db.execute("ALTER TABLE torrents DROP COLUMN media_path")
        log.info("[DATABASE] Removed media_path column from torrents table")


async def v4_to_v5(db: aiosqlite.Connection):
    """
    Removes the legacy files column from torrents table
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "files" in cols:
        await db.execute("ALTER TABLE torrents DROP COLUMN files")
        log.info("[DATABASE] Removed files column from torrents table")


async def v5_to_v6(db: aiosqlite.Connection):
    """
    Renames torrent files to organize by torrent hash
    Updates torrent path in database
    """
    cursor = await db.execute("SELECT id, torrent_path, infohash FROM torrents")
    torrents = await cursor.fetchall()

    renamed = 0

    for torrent in torrents:
        torrent_path = torrent["torrent_path"]
        if not os.path.exists(torrent_path):
            log.warning(f"[DATABASE] Torrent file not found during migration: {torrent_path}")
            continue

        torrent_infohash = torrent["infohash"]
        new_torrent_path = os.path.join(TORRENTS_DIR, f"{torrent_infohash}.torrent")

        if torrent_path == new_torrent_path:
            continue

        if os.path.exists(new_torrent_path):
            log.critical(f"[DATABASE] New torrent path already exists: {new_torrent_path}")
            continue

        try:
            shutil.move(torrent_path, new_torrent_path)

            torrent_id = torrent["id"]
            await db.execute("UPDATE torrents SET torrent_path = ? WHERE id = ?", (new_torrent_path, torrent_id,))

            renamed += 1
        except Exception as e:
            log.error(f"[DATABASE] Exception while renaming torrent file: {torrent_path}: {e}")

    log.info(f"[DATABASE] Renamed {renamed} of {len(torrents)} torrent files")
