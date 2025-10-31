import asyncio
import datetime
import itertools
import os
from enum import Enum

from privateindexer_client.core import torrent_client, database, utils
from privateindexer_client.core.config import SCAN_INTERVAL, DOWNLOADS_DIR, TORRENTS_DIR, PURGE_UNTRACKED_TORRENTS, SCAN_BATCH_SIZE
from privateindexer_client.core.logger import log
from privateindexer_client.core.thread_executor import SCAN_EXECUTOR

SCAN_PROCESS_STATE: int = 0
SCAN_TOTAL_ITEMS: int = 0
SCAN_DONE_ITEMS: int = 0


class ScannerStates(Enum):
    IDLE = 0
    PRE_SCAN = 1
    SCANNING = 2
    PROCESSING = 3
    POST_SCAN = 4


async def scan_media_library() -> tuple[int, int, int, int]:
    """
    Main loop for scanning media libraries defined by user
    Will walk over all defined category paths, each single file gets turned into a single torrent file
    Will ignore existing and correctly uploaded torrent files
    Torrent creation is batched into a multi-threaded executor, number of threads defined by user
    Will attempt to use send_torrent_to_indexer() and seed_torrents() for each torrent if conditions are met
    """
    global SCAN_PROCESS_STATE, SCAN_TOTAL_ITEMS, SCAN_DONE_ITEMS

    # fetch updated root folders from Radarr/Sonarr
    category_paths = await utils.update_torznab_category_paths()

    # make sure we have at least 1 directory to scan, otherwise skip scan
    if len(category_paths) == 0:
        log.warning(f"[SCAN] No root folders accessible for scanning")
        return 0, 0, 0, 0

    # set scan state to pre-scan
    SCAN_PROCESS_STATE = ScannerStates.PRE_SCAN.value

    torrents = await database.fetch_all("SELECT media_path, torrent_path FROM torrents")
    existing_media = {t["media_path"]: t["torrent_path"] for t in torrents}

    total_files = 0
    ignored_files = 0
    created_files = 0
    updated_files = 0

    loop = asyncio.get_running_loop()
    files = await utils.get_all_media_files()

    # set scan state to scanning and update progress values
    SCAN_TOTAL_ITEMS = len(files)
    SCAN_DONE_ITEMS = 0
    SCAN_PROCESS_STATE = ScannerStates.SCANNING.value

    # create a list to store files that need to be processed
    to_process: list[tuple[str, str | None]] = []

    # loop through the files we intend to scan
    for file_path in files:
        # make sure we can see whatever was passed through from Radarr/Sonarr
        if not os.path.exists(file_path):
            log.debug(f"[SCAN] File path not found or accessible, skipped: {file_path}")
            # increment global items counter
            SCAN_DONE_ITEMS += 1
            continue

        filename = os.path.basename(file_path)
        total_files += 1

        # ignore the media file if the current path is matches what is in the database
        if file_path in existing_media:
            torrent_path = existing_media[file_path]
            # only ignore the creation process if the torrent file exists
            if os.path.exists(torrent_path):
                ignored_files += 1
                # increment global items counter
                SCAN_DONE_ITEMS += 1
                continue

        log.debug(f"[SCAN] Trying to locate torrent file for: '{file_path}'")
        # ignore the media file if we can find a matching torrent file for it
        find_future = loop.run_in_executor(SCAN_EXECUTOR, utils.find_existing_torrent, file_path, list(existing_media.values()))
        torrent_file = await find_future
        if torrent_file:
            # try to update the media path in the database to match the current path
            result = await database.fetch_one("SELECT id, name, media_path FROM torrents WHERE torrent_path = ?", (torrent_file,))
            if result and result.get("id") is not None:
                # check to see if the file was only moved, not renamed
                if utils.file_exists_in_torrent(torrent_file, filename):
                    updated_files += 1
                    # detect category in case it's not matching in the database
                    category_id = utils.detect_torznab_category(file_path)
                    # update the old media location to match current location
                    await database.execute("UPDATE torrents SET media_path = ?, category = ? WHERE id = ?", (file_path, category_id, result["id"],))
                    log.info(f"[SCAN] Updated the media path for '{result["name"]}'")
                    # increment global items counter
                    SCAN_DONE_ITEMS += 1
                    continue
                else:
                    log.info(f"[SCAN] File was renamed, media path not updated: '{result["name"]}'")

        # once we've reached this point, we should add the file to the processing queue to be batched
        to_process.append((file_path, torrent_file))
        # increment global items counter
        SCAN_DONE_ITEMS += 1

    # stop here if we don't have any new items to process
    if not to_process:
        return total_files, ignored_files, updated_files, created_files

    # set scan state to processing and update global variables to track the processing files now
    SCAN_TOTAL_ITEMS = len(to_process)
    SCAN_DONE_ITEMS = 0
    SCAN_PROCESS_STATE = ScannerStates.PROCESSING.value

    # batch the items into processing groups to peridically save progress to database
    batches = itertools.batched(to_process, SCAN_BATCH_SIZE)
    # calculate number of batches we made based on number of items we need to process
    num_batches = (len(to_process) + SCAN_BATCH_SIZE - 1) // SCAN_BATCH_SIZE

    log.info(f"[SCAN] {num_batches} batches created for processing {SCAN_TOTAL_ITEMS} items")

    batch_index = 0
    # process each batch of files, one at a time, synchronously
    for batched_job in batches:
        batch_index += 1
        futures = []

        log.info(f"[SCAN] Starting batch {batch_index} of {num_batches} ({len(batched_job)} items)")

        # add each file/torrent pair to the execution queue
        for job_item in batched_job:
            file_path, torrent_file = job_item
            log.debug(f"[SCAN] Queueing file for processing: '{file_path}'")

            # dispatch the torrent creation to the pool of worker threads
            future = loop.run_in_executor(SCAN_EXECUTOR, utils.create_torrent_threadsafe, file_path, torrent_file)
            futures.append(future)

        log.info(f"[SCAN] Queued {len(futures)} files for processing")

        # collect the workers as they finish and process their output
        async for future in asyncio.as_completed(futures):
            # keep a rolling count of the total files completed in global variable
            SCAN_DONE_ITEMS += 1
            try:
                metadata, is_new_file = await future
                if metadata:

                    # attempt to send torrent file to indexer server
                    uploaded = await utils.send_torrent_to_indexer(metadata["torrent_path"], metadata["category"])

                    # add the data for the torrent to the database
                    await utils.add_torrent_to_database(metadata["name"], metadata["size"], metadata["torrent_path"], uploaded, metadata["files"],
                                                        metadata["category"],
                                                        media_path=metadata["media_path"], hash_v1=metadata["hash_v1"], hash_v2=metadata["hash_v2"])

                    if is_new_file:
                        created_files += 1
                        # attempt to add the torrent to the libtorrent session right away for immediate seeding
                        if await torrent_client.add_torrent_for_seeding(metadata["torrent_path"], metadata["media_path"]):
                            log.info(f"[SCAN] Created and started seeding new torrent: {metadata["name"]}")
                        else:
                            log.warning(f"[SCAN] Created but failed to start seeding new torrent: {metadata["name"]}")
                    else:
                        log.debug(f"[SCAN] Updated existing torrent: {metadata["name"]}")
            except Exception as e:
                log.error(f"[SCAN] Error in torrent post-torrent-creation process: {e}")

        log.info(f"[SCAN] Completed batch {batch_index} of {num_batches} ({SCAN_DONE_ITEMS} of {SCAN_TOTAL_ITEMS} total items processed)")

    return total_files, ignored_files, updated_files, created_files


async def periodic_scan_task():
    """
    Wraps scan_media_library() asynchronously and periodically scans media libraries defined by user
    Will also attempt to resend failed uploads torrents to the PrivateIndexer server after each scan
    """
    global SCAN_PROCESS_STATE
    log.debug("[SCAN] Task loop started")
    while True:
        try:
            log.info("[SCAN] Scanning media library for new or updated files")
            before = datetime.datetime.now()

            total_files, ignored_files, updated_files, created_files = await scan_media_library()

            log.debug("[SCAN] Scan complete, running post-scan checks")

            # update the scan state
            SCAN_PROCESS_STATE = ScannerStates.POST_SCAN.value

            removed_entries = 0

            # here we perform various database integrity and value correction checks
            torrents = await database.fetch_all("SELECT * FROM torrents")
            for torrent in torrents:
                torrent_path: str = torrent["torrent_path"]
                torrent_exists = os.path.exists(torrent_path)
                media_path: str | None = torrent.get("media_path")
                download_path: str | None = torrent.get("download_path")
                media_exists = os.path.exists(media_path) if media_path else False
                download_exists = os.path.exists(download_path) if download_path else False

                # case where either the torrent is missing or both the media and the downloaded data are missing, purge from database
                if not torrent_exists or (not media_exists and not download_exists):
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent.get("hash_v1"))
                    # remove torrent file
                    if os.path.exists(torrent_path):
                        os.unlink(torrent_path)
                    # remove from database
                    await database.execute("DELETE FROM torrents WHERE id = ?", (torrent["id"],))
                    log.info(f"[SCAN] All files missing for '{torrent["name"]}', removed torrent from database and torrent client")
                    continue

                # case where only the media data is missing, nullify the media_path in the database
                if media_path and not media_exists:
                    updated_files += 1
                    await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent["id"],))
                    log.info(f"[SCAN] Media files missing for '{torrent["name"]}', purged media path from database")

                # case if this is an external torrent (should have a download path), try to locate the download media if it's missing
                if download_path and not download_exists:
                    log.debug(f"[SCAN] Trying to locate download media for: '{torrent_path}'")
                    download_path = utils.find_media_for_torrent(torrent_path, DOWNLOADS_DIR)
                    download_exists = os.path.exists(download_path) if download_path else False

                    # update the database if the download path exists
                    if download_exists:
                        updated_files += 1
                        await database.execute("UPDATE torrents SET download_path = ? WHERE id = ?", (download_path, torrent["id"],))
                        log.info(f"[SCAN] Updated the download path for '{torrent["name"]}'")

                # case where media exists but the torznab category is unknown (0), try to fix it
                if media_exists and torrent["category"] == 0:
                    category_id = utils.detect_torznab_category(media_path)

                    # update the category if a match was found
                    if category_id != 0:
                        updated_files += 1
                        await database.execute("UPDATE torrents SET category = ? WHERE id = ?", (category_id, torrent["id"],))
                        log.info(f"[SCAN] Updated the category to '{category_id}' for '{torrent["name"]}'")

                # case where we have tracked media but it's somehow set inside the downloads directory, nullify its value so it can be rescanned next time
                if media_path and media_path.startswith(DOWNLOADS_DIR):
                    updated_files += 1
                    await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent["id"],))
                    log.info(f"[SCAN] Invalid media path for '{torrent["name"]}', purged media path from database")

            # only purge dangling torrents if the user has this option enabled
            if PURGE_UNTRACKED_TORRENTS:
                torrent_paths = [torrent["torrent_path"] for torrent in torrents]
                for fname in os.listdir(TORRENTS_DIR):
                    # ignore non-torrent files
                    if not fname.endswith(".torrent"):
                        continue
                    torrent_path = os.path.join(TORRENTS_DIR, fname)
                    if torrent_path not in torrent_paths:
                        os.unlink(torrent_path)
                        log.info(f"[SCAN] Removed danlging torrent file '{torrent_path}'")

            delta = datetime.datetime.now() - before
            log.info(f"[SCAN] Media library scan completed ({delta}): "
                     f"total {total_files} files, {ignored_files} ignored, {updated_files} updated, {created_files} created, {removed_entries} removed")

        except Exception as e:
            log.error(f"[SCAN] Error during periodic scan: {e}")

        # set scan state back to idle
        SCAN_PROCESS_STATE = ScannerStates.IDLE.value

        await asyncio.sleep(SCAN_INTERVAL)
