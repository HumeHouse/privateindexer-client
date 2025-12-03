import aiosqlite

from privateindexer_client.core.config import DATABASE_FILE
from privateindexer_client.core.database_migrations import migrate_0_to_1, migrate_1_to_2
from privateindexer_client.core.logger import log

LATEST_SCHEMA_VERSION = 2

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
                         hash_v2      TEXT,
                         app_id       INTEGER
                     )
                     """

MIGRATIONS = {0: migrate_0_to_1, 1: migrate_1_to_2, }


async def initialize():
    """
    Initialize the SQLite database with all required tables
    Also performs any necessary migrations
    """
    async with aiosqlite.connect(DATABASE_FILE) as db:
        # ensure tables exist
        await db.execute(TORRENTS_TABLE_SQL)

        # get current version
        version_result = await db.execute("PRAGMA user_version")
        row = await version_result.fetchone()
        current_version = row[0]

        # check if outdated
        if current_version > LATEST_SCHEMA_VERSION:
            log.error(
                f"[DATABASE] Current database version ({current_version}) is higher than max supported version ({LATEST_SCHEMA_VERSION}) - application was most likely rolled back.")
            exit(1)

        log.info(f"[DATABASE] Current schema version: {current_version}")

        # check if outdated
        if current_version == LATEST_SCHEMA_VERSION:
            return

        # migrate database one version at a time
        while current_version < LATEST_SCHEMA_VERSION:
            next_version = current_version + 1
            log.info(f"[DATABASE] Migrating from {current_version} to {next_version}...")

            migration_script = MIGRATIONS.get(current_version)
            if not migration_script:
                log.error(f"[DATABASE] No migration script found for {current_version} to {next_version}")
                exit(1)

            await migration_script(db)

            # update database version
            await db.execute(f"PRAGMA user_version = {next_version}")
            current_version = next_version

        await db.commit()
        log.info(f"[DATABASE] Schema upgraded to version {LATEST_SCHEMA_VERSION}")


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
