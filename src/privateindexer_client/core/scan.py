import asyncio
import datetime
import os
from concurrent.futures import ProcessPoolExecutor

from privateindexer_client.core import torrent_client, database, utils
from privateindexer_client.core.config import SCAN_INTERVAL, SCANNER_THREADS, TORZNAB_CATEGORY_PATHS, MOVIE_EXTENSIONS
from privateindexer_client.core.logger import log

EXECUTOR = ProcessPoolExecutor(max_workers=SCANNER_THREADS)


async def scan_media_library():
    """
    Main loop for scanning media libraries defined by user
    Will walk over all defined category paths, each single file gets turned into a single torrent file
    Will ignore existing and correctly uploaded torrent files
    Torrent creation is batched into a multi-threaded executor, number of threads defined by user
    Will attempt to use send_torrent_to_indexer() and seed_torrents() for each torrent if conditions are met
    """
    torrents = await database.fetch_all("SELECT media_path FROM torrents")
    existing_media = [t["media_path"] for t in torrents]

    total_files = 0
    ignored_files = 0
    created_files = 0

    loop = asyncio.get_event_loop()
    futures = []

    # loop through all files in the media directories
    for category_key, cat_info in TORZNAB_CATEGORY_PATHS.items():
        for root, _, files in os.walk(cat_info["path"]):
            for f in files:
                # skip files that have non-whitelisted extensions
                file_path = os.path.join(root, f)
                extension = os.path.splitext(os.path.basename(file_path))[1].replace(".", "")
                if extension not in MOVIE_EXTENSIONS:
                    log.debug(f"[SCAN] Skipping file with {extension} extension")
                    continue
                total_files += 1

                # ignore the media file if the current path is matches what is in the database
                if file_path in existing_media:
                    ignored_files += 1
                    continue

                # ignore the media file if we have a torrent file for it
                torrent_file = utils.find_existing_torrent(file_path)
                if torrent_file:
                    # try to update the media path in the database to match the current path
                    result = await database.fetch_one("SELECT id, name FROM torrents WHERE torrent_path = ?", (torrent_file,))
                    if result and result.get("id") is not None:
                        # update the old media location to match current location
                        await database.execute("UPDATE torrents SET media_path = ? WHERE id = ?", (file_path, result["id"],))
                        log.info(f"[SCAN] Updated the media path for '{result["name"]}'")
                    else:
                        log.error(f"[SCAN] Failed to update the media path in database for '{file_path}'")
                    continue

                # dispatch the torrent creation to the pool of worker threads
                future = loop.run_in_executor(EXECUTOR, utils.create_torrent_threadsafe, file_path)
                futures.append(future)

    if len(futures) > 0:
        log.info(f"[SCAN] Queued {len(futures)} torrents for creation")

    # collect the workers as they finish and process their output
    for future in asyncio.as_completed(futures):
        try:
            metadata = await future
            if metadata:
                created_files += 1

                # attempt to send torrent file to indexer server
                metadata["uploaded"] = await utils.send_torrent_to_indexer(metadata)

                # add the torrent metadata to the database
                await database.execute(
                    "INSERT INTO torrents (name, size, media_path, torrent_path, uploaded, files, category, hash_v1, hash_v2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (metadata["name"], metadata["size"], metadata["media_path"], metadata["torrent_path"], metadata["uploaded"], metadata["files"], metadata["category"],
                     metadata["hash_v1"], metadata["hash_v2"],))

                # attempt to add the torrent to the libtorrent session right away for immediate seeding
                await torrent_client.add_torrent_for_seeding(metadata["torrent_path"], metadata["media_path"])

                log.info(f"[SCAN] Created or updated torrent: {metadata["name"]}")
        except Exception as e:
            log.error(f"[SCAN] Error in torrent post-torrent-creation process: {e}")

    # here we check to make sure the media files for a torrent still exist on the disk, otherwise remove the torrent from the database
    torrents = await database.fetch_all("SELECT * FROM torrents")
    removed_entries = 0
    for torrent in torrents:
        media_path = torrent.get("media_path")
        download_path = torrent.get("download_path")

        # case where both the media and the downloaded data are missing, we assume the user deleted them and purge it
        if (not media_path or (media_path and not os.path.exists(media_path))) and (not download_path or (download_path and not os.path.exists(download_path))):
            removed_entries += 1
            # remove from torrent client
            await torrent_client.remove_torrent_by_hash(torrent.get("hash_v2"))
            # remove from database
            await database.execute("DELETE FROM torrents WHERE id = ?", (torrent["id"],))
            log.info(f"[SCAN] All files missing for '{torrent["name"]}', removed it from database and torrent client")

        # case where only the media data is missing, remove the media_path in the database
        elif media_path and not os.path.exists(media_path):
            await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent["id"],))

    # TODO: keep this legacy code in here until the next version to let the client build/save fastresume data
    log.info(f"[SCAN] Legacy-mode: adding all torrents to torrent client for seeding")
    # add all torrents to the torrent client if they aren't already
    torrents = await database.fetch_all("SELECT * FROM torrents")
    torrent_client.add_torrents_for_seeding(torrents)

    return total_files, ignored_files, created_files, removed_entries


async def periodic_scan_task():
    """
    Wraps scan_media_library() asynchronously and periodically scans media libraries defined by user
    Will also attempt to resend failed uploads torrents to the PrivateIndexer server after each scan
    """
    log.debug("[SCAN] Task loop started")
    while True:
        try:
            log.info("[SCAN] Running media library scan")
            before = datetime.datetime.now()

            total_files, ignored_files, created_files, removed_entries = await scan_media_library()

            delta = datetime.datetime.now() - before
            log.info(f"[SCAN] Media library scan complete ({delta}): "
                     f"total {total_files} files, {ignored_files} ignored, {created_files} created, {removed_entries} removed")

            # attempt to resend all failed uploads to indexer server
            failed_upload_torrents = await database.fetch_all("SELECT * FROM torrents WHERE uploaded = FALSE")
            for torrent in failed_upload_torrents:
                torrent_file = torrent["torrent_path"]
                if os.path.exists(torrent_file):
                    log.info(f"[SCAN] Attempting to resend torrent to indexer: '{torrent["name"]}'")
                    if await utils.send_torrent_to_indexer(torrent):
                        await database.execute("UPDATE torrents SET uploaded = TRUE WHERE id = ?", (torrent["id"],))

                # torrent file is missing, remove the entry from database so it can be regenerated on next scan
                else:
                    await database.execute("DELETE FROM torrents WHERE id = ?", (torrent["id"],))
                    log.warning(f"[SCAN] Torrent file doesn't exist, removed from database: '{torrent_file}'")

        except Exception as e:
            log.error(f"[SCAN] Error during periodic scan: {e}")
        await asyncio.sleep(SCAN_INTERVAL)
