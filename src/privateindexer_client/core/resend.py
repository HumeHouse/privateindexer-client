import asyncio
import datetime
import os

from privateindexer_client.core import database, utils
from privateindexer_client.core.config import RESEND_INTERVAL
from privateindexer_client.core.logger import log


async def periodic_resend_task():
    """
    Periodically attempt to resend failed torrents up to the indexer server
    """
    log.debug("[RESEND] Task loop started")
    while True:
        try:
            before = datetime.datetime.now()

            # attempt to resend all failed uploads to indexer server
            failed_upload_torrents = await database.fetch_all("SELECT id, name, torrent_path, category FROM torrents WHERE uploaded = FALSE")

            total = len(failed_upload_torrents)

            if total > 0:
                uploaded = 0
                failed = 0
                log.info(f"[RESEND] Attempting to resend {len(failed_upload_torrents)} failed torrents")

                for torrent_metadata in failed_upload_torrents:
                    torrent_path = torrent_metadata["torrent_path"]
                    if os.path.exists(torrent_path):
                        log.info(f"[RESEND] Attempting to resend torrent to indexer: '{torrent_metadata["name"]}'")
                        if await utils.send_torrent_to_indexer(torrent_path, torrent_metadata["category"]):
                            await database.execute("UPDATE torrents SET uploaded = TRUE WHERE id = ?", (torrent_metadata["id"],))
                            uploaded += 1
                        else:
                            failed += 1
                    else:
                        failed += 1

                delta = datetime.datetime.now() - before
                log.info(f"[RESEND] Torrent resend task completed ({delta}): {total} total, {uploaded} uploaded, {failed} failed")

        except Exception as e:
            log.error(f"[RESEND] Error during resend task: {e}")
        await asyncio.sleep(RESEND_INTERVAL)
