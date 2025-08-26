import asyncio
import datetime
import os
from concurrent.futures import ProcessPoolExecutor

from privateindexer_client.core import torrent_client, database, utils
from privateindexer_client.core.config import TORRENTS_DIR, SCAN_INTERVAL, SCANNER_THREADS, TORZNAB_CATEGORY_PATHS, MOVIE_EXTENSIONS
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
    torrents = await database.load_torrents_threadsafe()
    torrents_by_path = {t["path"]: t for t in torrents}

    total_files = 0
    ignored_files = 0
    created_files = 0

    loop = asyncio.get_event_loop()
    futures = []

    # loop through all files in the media directories
    for category_key, cat_info in TORZNAB_CATEGORY_PATHS.items():
        for root, _, files in os.walk(cat_info["path"]):
            for f in files:
                total_files += 1

                # skip files that have non-whitelisted extensions
                file_path = os.path.join(root, f)
                extension = os.path.splitext(os.path.basename(file_path))[1].replace(".", "")
                if extension not in MOVIE_EXTENSIONS:
                    log.debug(f"[SCAN] Skipping file with {extension} extension")
                    continue

                # ignore the media file if it has already been uploaded according to the database
                if file_path in torrents_by_path and torrents_by_path[file_path].get("uploaded", False):
                    ignored_files += 1
                    continue

                # dispatch the torrent creation to the pool of worker threads
                future = loop.run_in_executor(EXECUTOR, utils.create_torrent_threadsafe, file_path)
                futures.append(future)

    if len(futures) > 0:
        log.info(f"[SCAN] Queued {len(futures)} torrents for creation")

    # collect the workers as they finish and process their output
    for future in asyncio.as_completed(futures):
        try:
            torrent_metadata = await future
            if torrent_metadata:
                created_files += 1

                # attempt to send torrent file to indexer server
                if not torrent_metadata["uploaded"]:
                    if await utils.send_torrent_to_indexer(torrent_metadata):
                        torrent_metadata["uploaded"] = True

                torrents_by_path[torrent_metadata["path"]] = torrent_metadata
                await database.save_torrents_threadsafe(list(torrents_by_path.values()))

                # attempt to add the torrent to the libtorrent session right away for immediate seeding
                torrent_client.add_torrents_for_seeding([torrent_metadata])

                log.info(f"[SCAN] Created or updated torrent: {torrent_metadata["name"]}")
        except Exception as e:
            log.error(f"[SCAN] Error in torrent post-torrent-creation process: {e}")

    torrents = list(torrents_by_path.values())

    # here we check to make sure the media files for a torrent still exist on the disk, otherwise remove the torrent from the local database ONLY
    still_existing = []
    for torrent in torrents:
        if os.path.exists(torrent["path"]):
            still_existing.append(torrent)
        else:
            log.info(f"[SCAN] Media files missing for '{torrent["name"]}', removed it from database and torrent client")
            await torrent_client.remove_torrent_by_hash(torrent.get("hash_v2"))

    # attempt to add any missing files to the libtorrent session for seeding
    torrent_client.add_torrents_for_seeding(still_existing)

    await database.save_torrents_threadsafe(still_existing)

    removed_entries = len(torrents) - len(still_existing)

    return total_files, ignored_files, created_files, removed_entries


async def periodic_scan_task():
    """
    Wraps scan_media_library() asynchronously and periodically scans media libraries defined by user
    Will also attempt to resend failed uploads torrents to the PrivateIndexer server after each scan
    """
    log.info("[SCAN] Task loop started")
    while True:
        try:
            log.info("[SCAN] Running media library scan")
            before = datetime.datetime.now()

            total_files, ignored_files, created_files, removed_entries = await scan_media_library()

            delta = datetime.datetime.now() - before
            log.info(f"[SCAN] Media library scan complete ({delta}): "
                     f"total {total_files} files, {ignored_files} ignored, {created_files} created, {removed_entries} removed")

            torrents = await database.load_torrents_threadsafe()
            updated = False
            # attempt to resend all failed uploads to indexer server
            for torrent in torrents:
                if torrent.get("uploaded"):
                    continue

                torrent_file = os.path.join(TORRENTS_DIR, f"{torrent["name"]}.torrent")
                if os.path.exists(torrent_file):
                    log.info(f"[SCAN] Attempting to resend torrent to indexer: '{torrent["name"]}'")
                    if await utils.send_torrent_to_indexer(torrent):
                        torrent["uploaded"] = True
                        updated = True

                # torrent file is missing, remove the entry from database so it can be regenerated on next scan
                else:
                    torrents.pop(torrent)
                    updated = True
                    log.warning(f"[SCAN] Torrent file doesn't exist, removed from database: '{torrent_file}'")
            if updated:
                await database.save_torrents_threadsafe(torrents)
        except Exception as e:
            log.error(f"[SCAN] Error during periodic scan: {e}")
        await asyncio.sleep(SCAN_INTERVAL)
