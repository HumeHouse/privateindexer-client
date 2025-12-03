import aiosqlite

from privateindexer_client.core.logger import log


async def migrate_0_to_1(db: aiosqlite.Connection):
    """
    Adds app_id column to torrents table
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "app_id" not in cols:
        await db.execute("ALTER TABLE torrents ADD COLUMN app_id INTEGER")
        log.info("[DATABASE] Added app_id column to torrents table")


async def migrate_1_to_2(db: aiosqlite.Connection):
    """
    Rewrites the category 1000 values to category 2000 to conform to Newznab specification
    """
    cursor = await db.execute("PRAGMA table_info(torrents)")
    cols = {row[1] for row in await cursor.fetchall()}

    if "app_id" not in cols:
        await db.execute("ALTER TABLE torrents ADD COLUMN app_id INTEGER")
        log.info("[DATABASE] Added app_id column to torrents table")
