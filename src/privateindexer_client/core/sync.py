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
            local_torrents = await database.fetch_all("SELECT id, hash_v1, hash_v2 FROM torrents")
            total = len(local_torrents)
            log.info(f"[SYNC] Syncing {total} torrents with indexer")

            # call the sync endpoint to get the list of existing and missing torrents on the server
            async with httpx_request.get_client() as client:
                response = await client.post(INDEXER_API_URL + "/sync", headers={"X-API-Key": API_KEY}, json=local_torrents)

                # make sure the sync was successful on the server
                if response.status_code != 200:
                    log.warning(f"[SYNC] Failed to sync torrents with server, will retry later: {response.status_code} - {response.text}")
                    continue

            synced_torrents = response.json()
            found_torrents = synced_torrents["found"]
            missing_torrents = synced_torrents["missing"]
            uploaded = 0
            failed = 0

            # complete sync if no missing torrents
            if not missing_torrents:
                delta = datetime.datetime.now() - before
                log.info(f"[SYNC] Server sync task completed ({delta}): {len(found_torrents)} existing, 0 missing")
                continue

            # gather additional data from the database for the missing torrents
            missing_ids = [t["id"] for t in missing_torrents]
            if not missing_ids:
                log.error("[SYNC] Missing torrents had no IDs from server, aborting sync")
                continue
            placeholders = ", ".join(["?"] * len(missing_ids))
            query = f"SELECT id, name, category, torrent_path, media_path, download_path FROM torrents WHERE id IN ({placeholders})"
            missing_metadata = await database.fetch_all(query, tuple(missing_ids))
            torrent_lookup = {t["id"]: t for t in missing_metadata}

            # loop through the missing torrents and upload to the server if valid
            for missing_torrent in missing_torrents:
                # get the rest of the missing metadata from the lookup dict
                torrent_metadata = torrent_lookup.get(missing_torrent["id"])
                if not torrent_metadata:
                    failed += 1
                    continue

                torrent_path = torrent_metadata["torrent_path"]
                media_path = torrent_metadata["media_path"]
                download_path = torrent_metadata["download_path"]
                # make sure the torrent and either the media or the download files exist before uploading to the server
                if os.path.exists(torrent_path) and (os.path.exists(media_path) or os.path.exists(download_path)):
                    log.debug(f"[SYNC] Attempting to resend torrent to indexer: '{torrent_metadata["name"]}'")
                    if await utils.send_torrent_to_indexer(torrent_path, torrent_metadata["category"]):
                        await database.execute("UPDATE torrents SET uploaded = TRUE WHERE id = ?", (torrent_metadata["id"],))
                        uploaded += 1
                    else:
                        failed += 1
                else:
                    log.debug(f"[SYNC] Aborting upload for '{torrent_metadata["name"]}' due to missing files")
                    failed += 1

            delta = datetime.datetime.now() - before
            log.info(
                f"[SYNC] Server sync task completed ({delta}): {len(found_torrents)} existing, {len(missing_torrents)} missing, {uploaded} uploaded, {failed} failed")

        except Exception as e:
            log.error(f"[SYNC] Error during sync task: {e}")
