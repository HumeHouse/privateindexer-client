import aiosqlite

from privateindexer_client.core.config import DATABASE_FILE

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
