import asyncio
import datetime
import os

from privateindexer_client.core import database, utils, httpx_request
from privateindexer_client.core.config import SYNC_INTERVAL, INDEXER_API_URL, API_KEY
from privateindexer_client.core.logger import log


async def periodic_sync_task():
    """
    Periodically syncronizes torrent database with the indexer server
    Will ignore existing and correctly uploaded torrent files
    """
    log.debug("[SYNC] Task loop started")
    while True:
        await asyncio.sleep(SYNC_INTERVAL)
        try:
            before = datetime.datetime.now()

            # gather minimal data from database for each torrent from database for sync
            local_torrents = await database.fetch_all("SELECT id, infohash FROM torrents")
            total = len(local_torrents)
            log.info(f"[SYNC] Syncing {total} torrents with indexer")

            # call the sync endpoint to get the list of existing and missing torrents on the server
            async with httpx_request.get_client() as client:
                response = await client.post(f"{INDEXER_API_URL}/sync", headers={"X-API-Key": API_KEY}, json=local_torrents)

                # make sure the sync was successful on the server
                if response.status_code != 200:
                    log.warning(f"[SYNC] Failed to sync torrents with server, will retry later: {response.status_code} - {response.text}")
                    continue

            synced_torrents = response.json()
            missing_ids = synced_torrents["missing_ids"]
            existing = total - len(missing_ids)
            uploaded = 0
            failed = 0

            # assemble metadata lookup map for the missing torrents
            torrents_to_upload = []
            if missing_ids:
                placeholders = ", ".join(["?"] * len(missing_ids))
                query = f"SELECT id, name, category, torrent_path, media_path, download_path, app_id FROM torrents WHERE id IN ({placeholders})"
                missing_torrent_data = await database.fetch_all(query, tuple(missing_ids))
                torrents_to_upload.extend(missing_torrent_data)

            # fetch torrents which need to be uploaded to server
            not_uploaded = await database.fetch_all("SELECT id, name, category, torrent_path, media_path, download_path, app_id FROM torrents WHERE uploaded = FALSE")
            # add these to the metadata lookup map
            torrents_to_upload.extend(not_uploaded)

            # loop through the missing torrents and upload to the server if valid
            for torrent_data in torrents_to_upload:
                torrent_name = torrent_data["name"]
                torrent_path = torrent_data["torrent_path"]
                media_path = torrent_data["media_path"]
                download_path = torrent_data["download_path"]

                # make sure the torrent and either the media or the download files exist before uploading to the server
                if os.path.exists(torrent_path) and (os.path.exists(media_path) or os.path.exists(download_path)):
                    log.debug(f"[SYNC] Attempting to resend torrent to indexer: {torrent_name}")
                    if await utils.send_torrent_to_indexer(torrent_path, torrent_data["category"], torrent_name, torrent_data["app_id"]):
                        await database.execute("UPDATE torrents SET uploaded = TRUE WHERE id = ?", (torrent_data["id"],))
                        uploaded += 1
                    else:
                        failed += 1
                else:
                    log.debug(f"[SYNC] Aborting upload for '{torrent_name}' due to missing files")
                    failed += 1

            delta = datetime.datetime.now() - before
            log.info(
                f"[SYNC] Server sync task completed ({delta}): {existing} existing, {len(missing_ids)} missing, {uploaded} uploaded, {failed} failed")

        except Exception as e:
            log.error(f"[SYNC] Error during sync task: {e}")
