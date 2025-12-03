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
