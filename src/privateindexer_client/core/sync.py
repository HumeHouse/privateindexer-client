import asyncio
import datetime
import os

from privateindexer_client.core import database, server_interface
from privateindexer_client.core import logger
from privateindexer_client.core.config import SYNC_INTERVAL


async def periodic_sync_task():
    """
    Periodically syncronizes torrent database with the indexer server
    Will ignore existing and correctly uploaded torrent files
    """
    logger.channel("sync").debug("Task loop started")
    while True:
        try:
            before = datetime.datetime.now()

            # gather minimal data from database for each torrent from database for sync
            local_torrents = await database.fetch_all("SELECT id, name, infohash FROM torrents")
            total = len(local_torrents)
            logger.channel("sync").info(f"Syncing {total} torrents with indexer")

            synced_torrents = await server_interface.sync_torrents_with_indexer(local_torrents)

            if not synced_torrents:
                await asyncio.sleep(SYNC_INTERVAL)
                continue

            missing_ids = synced_torrents["missing_ids"]
            existing = total - len(missing_ids)
            uploaded = 0
            failed = 0

            # assemble metadata lookup map for the missing torrents
            torrents_to_upload = []
            if missing_ids:
                placeholders = ", ".join(["?"] * len(missing_ids))
                query = f"SELECT id, name, category, torrent_path, app_id FROM torrents WHERE id IN ({placeholders})"
                missing_torrent_data = await database.fetch_all(query, tuple(missing_ids))
                torrents_to_upload.extend(missing_torrent_data)

            # fetch torrents which need to be uploaded to server
            not_uploaded = await database.fetch_all("SELECT id, name, category, torrent_path, app_id FROM torrents WHERE uploaded = FALSE")
            # add these to the metadata lookup map
            torrents_to_upload.extend(not_uploaded)

            # loop through the missing torrents and upload to the server if valid
            for torrent_data in torrents_to_upload:
                torrent_name = torrent_data["name"]
                torrent_path = torrent_data["torrent_path"]

                # make sure the torrent and either the media or the download files exist before uploading to the server
                if os.path.exists(torrent_path):
                    logger.channel("sync").debug(f"Attempting to resend torrent to indexer: {torrent_name}")
                    if await server_interface.send_torrent_to_indexer(torrent_path, torrent_data["category"], torrent_name, torrent_data["app_id"]):
                        await database.execute("UPDATE torrents SET uploaded = TRUE WHERE id = ?", (torrent_data["id"],))
                        uploaded += 1
                    else:
                        failed += 1
                else:
                    logger.channel("sync").debug(f"Aborting upload for '{torrent_name}' due to missing files")
                    failed += 1

            stats = [("existing", existing), ("missing", len(missing_ids)), ("uploaded", uploaded), ("failed", failed)]
            stats_list = ", ".join(f"{count} {name}" for name, count in stats if count > 0)

            delta = datetime.datetime.now() - before

            logger.channel("sync").info(f"Server sync task completed ({delta}): {stats_list}")

        except Exception as e:
            logger.channel("sync").exception(f"Exception during sync task: {e}")

        await asyncio.sleep(SYNC_INTERVAL)
