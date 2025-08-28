import json
import os

import aiosqlite

from privateindexer_client.core import utils
from privateindexer_client.core.config import DATABASE_FILE, TORRENTS_FILE, TORRENTS_DIR
from privateindexer_client.core.logger import log

TORRENTS_TABLE_SQL = """
                     CREATE TABLE IF NOT EXISTS "torrents"
                     (
                         id            INTEGER primary key,
                         name          TEXT    not null,
                         size          INTEGER not null,
                         torrent_path TEXT not null unique,
                         media_path    TEXT,
                         download_path TEXT,
                         uploaded      BOOLEAN not null,
                         files         INTEGER not null,
                         category      INTEGER not null,
                         hash_v1       TEXT,
                         hash_v2       TEXT
                     )
                     """


async def initialize():
    """
    Initialize the SQLite database with all required tables
    Also performs any necessary migrations
    """
    # create the tables needed by the app
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute(TORRENTS_TABLE_SQL)
        await db.commit()

    # TODO: this is temporary, remove it in upcoming versions or once everyone has migrated
    # if old JSON-based database exists, migrate it to SQLite
    if os.path.exists(TORRENTS_FILE):
        log.info(f"[DATABASE] Migrating torrents from JSON to SQLite")
        try:
            # get the current torrents
            with open(TORRENTS_FILE, "r") as f:
                torrents = json.load(f)
            to_migrate = len(torrents)

            async with aiosqlite.connect(DATABASE_FILE) as db:
                # loop through each entry and try to find a matching torrent file with the media
                for t in torrents:
                    # try to find the torrent based on name
                    torrent_file = os.path.join(TORRENTS_DIR, t["name"] + ".torrent")
                    if not os.path.exists(torrent_file):
                        # otherwise use the utility method of hashing the media and finding a matching torrent file
                        torrent_file = utils.find_existing_torrent(t["path"])
                        if not torrent_file:
                            # otherwise fail
                            log.warning(f"[DATABASE] Couldn't find a match for torrent, it will be skipped: '{t["name"]}'")
                            continue

                    # insert the data into the new table
                    await db.execute(
                        "INSERT INTO torrents (name, size, media_path, torrent_path, uploaded, files, category, hash_v1, hash_v2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (t["name"], t["size"], t["path"], torrent_file, t["uploaded"], t["files"], t["category"], t["hash_v1"], t["hash_v2"]))
                await db.commit()
                async with db.execute("SELECT COUNT(*) as count FROM torrents") as cur:
                    migrated = (await cur.fetchone())[0]

            log.info(f"[DATABASE] Migrated {migrated} torrents from JSON to SQLite")

            # check to make sure all torrents were migrated from the old database
            if to_migrate != migrated:
                log.warning(f"[DATABASE] {to_migrate - migrated} torrents were not migrated, the old database file was not removed")
            else:
                # remove the old JSON file
                os.unlink(TORRENTS_FILE)
                log.info(f"[DATABASE] Removed old JSON database file")

        except Exception as e:
            log.error(f"[DATABASE] Failed to migrate JSON database to SQLite: {e}")
            # delete the database file because it is probably broken
            os.unlink(DATABASE_FILE)
            exit(1)


async def fetch_all(query: str, params: tuple = ()):
    """
    Returns fetchall() on the query passed to the database
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def fetch_one(query: str, params: tuple = ()):
    """
    Returns fetchone() on the query passed to the database or None if query did not return anything
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def execute(query: str, params: tuple = ()):
    """
    Returns the row ID returned by the query passed to the database
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur.lastrowid
