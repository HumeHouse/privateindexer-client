import asyncio
import datetime
import itertools
import os
from concurrent.futures.process import ProcessPoolExecutor
from enum import Enum

from privateindexer_client.core import torrent_client, database, utils, thread_executor
from privateindexer_client.core.config import SCAN_INTERVAL, DOWNLOADS_DIR, TORRENTS_DIR, PURGE_UNTRACKED_TORRENTS, SCAN_BATCH_SIZE, PURGE_DUPLICATE_SEEDS, \
    PURGE_UNTRACKED_DOWNLOADS
from privateindexer_client.core.logger import log
from privateindexer_client.core.utils import TorrentCreationMetadata

SCAN_PROCESS_STATE: int = 0
SCAN_TOTAL_ITEMS: int = 0
SCAN_DONE_ITEMS: int = 0


class ScannerStates(Enum):
    IDLE = 0
    PRE_SCAN = 1
    SCANNING = 2
    PROCESSING = 3
    POST_SCAN = 4


class ScanTorrentJob:
    def __init__(self, file_path: str):
        self.file_path: str = file_path
        self.torrent_name: str = None
        self.app_id: int = None
        self.torrent_file: str = None


async def scan_media_library(hash_executor: ProcessPoolExecutor) -> tuple[int, int, int, int]:
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

    torrents = await database.fetch_all("SELECT id, name, media_path, torrent_path, app_id FROM torrents WHERE media_path IS NOT NULL")
    torrent_data_map = {torrent["media_path"]: torrent for torrent in torrents}
    ignored_torrents = set([torrent["torrent_path"] for torrent in torrents])

    total_files = 0
    ignored_files = 0
    created_files = 0
    updated_files = 0

    loop = asyncio.get_running_loop()
    media_data_entries = await utils.get_managed_media_data()

    # set scan state to scanning and update progress values
    SCAN_TOTAL_ITEMS = len(media_data_entries)
    SCAN_DONE_ITEMS = 0
    SCAN_PROCESS_STATE = ScannerStates.SCANNING.value

    # create a list to store files that need to be processed
    scan_jobs: list[ScanTorrentJob] = []

    # loop through the files we intend to scan
    for media_data_entry in media_data_entries:
        file_path = media_data_entry.path

        # make sure we can see whatever was passed through from Radarr/Sonarr
        if not os.path.exists(file_path):
            log.debug(f"[SCAN] File path not found or accessible, skipped: {file_path}")
            # increment global items counter
            SCAN_DONE_ITEMS += 1
            continue

        total_files += 1

        # ignore the media file if the current path is matches what is in the database
        if file_path in torrent_data_map:
            has_updates = False

            torrent_data = torrent_data_map[file_path]
            torrent_id = torrent_data["id"]
            torrent_name = torrent_data["name"]
            torrent_file = torrent_data["torrent_path"]

            # check if the app_id is correct in the database
            if torrent_data["app_id"] != media_data_entry.app_id:
                has_updates = True
                # trigger a re-upload to make sure the app metadata gets synced to the server again
                await database.execute("UPDATE torrents SET app_id = ?, uploaded = FALSE WHERE id = ?", (media_data_entry.app_id, torrent_id,))
                log.info(f"[SCAN] Updated app ID for media at '{file_path}', will re-upload to server during next sync")

            # check if the torrent name is correct in the database
            if torrent_name != media_data_entry.title:
                has_updates = True
                new_name = media_data_entry.title
                await database.execute("UPDATE torrents SET name = ? WHERE id = ?", (new_name, torrent_id,))
                log.info(f"[SCAN] Updated local torrent name from '{torrent_name}' to '{new_name}'")

            if has_updates:
                updated_files += 1

            # only ignore the creation process if the torrent file exists
            if os.path.exists(torrent_file):
                ignored_files += 1
                # increment global items counter
                SCAN_DONE_ITEMS += 1
                continue

        log.debug(f"[SCAN] Trying to locate torrent file for: {file_path}")
        # ignore the media file if we can find a matching torrent file for it
        find_future = loop.run_in_executor(hash_executor, utils.find_existing_torrent, file_path, ignored_torrents)
        torrent_file = await find_future
        if torrent_file:
            # ignore this torrent file on subsequent loops
            ignored_torrents.add(torrent_file)

            # try to update the media path in the database to match the current path
            result = await database.fetch_one("SELECT id, name, media_path FROM torrents WHERE torrent_path = ?", (torrent_file,))
            if result and result.get("id") is not None:
                # check to see if the file path was only moved, not renamed or modified
                if utils.path_exists_in_torrent(torrent_file, file_path):
                    updated_files += 1
                    # detect category in case it's not matching in the database
                    category_id = utils.detect_torznab_category(file_path)
                    # update the old media location to match current location
                    await database.execute("UPDATE torrents SET media_path = ?, category = ?, app_id = ? WHERE id = ?",
                                           (file_path, category_id, media_data_entry.app_id, result["id"],))
                    log.info(f"[SCAN] Updated the media path for '{result["name"]}'")
                    # increment global items counter
                    SCAN_DONE_ITEMS += 1
                    continue
                else:
                    log.debug(f"[SCAN] File was modified, media path not updated: {result["name"]}")

        # construct the scan job
        scan_job = ScanTorrentJob(file_path)
        scan_job.app_id = media_data_entry.app_id
        scan_job.torrent_file = torrent_file

        if media_data_entry.title:
            scan_job.torrent_name = media_data_entry.title

        # add the scan job to the queue
        scan_jobs.append(scan_job)
        # increment global items counter
        SCAN_DONE_ITEMS += 1

    # stop here if we don't have any new items to process
    if not scan_jobs:
        return total_files, ignored_files, updated_files, created_files

    # set scan state to processing and update global variables to track the processing files now
    SCAN_TOTAL_ITEMS = len(scan_jobs)
    SCAN_DONE_ITEMS = 0
    SCAN_PROCESS_STATE = ScannerStates.PROCESSING.value

    # batch the items into processing groups to peridically save progress to database
    batches: list[list[ScanTorrentJob]] = itertools.batched(scan_jobs, SCAN_BATCH_SIZE)
    # calculate number of batches we made based on number of items we need to process
    num_batches = (len(scan_jobs) + SCAN_BATCH_SIZE - 1) // SCAN_BATCH_SIZE

    log.info(f"[SCAN] {num_batches} batches created for processing {SCAN_TOTAL_ITEMS} items")

    # spawn a new creation executor
    creation_executor = thread_executor.get_creation_executor()

    batch_index = 0
    # process each batch of files, one at a time, synchronously
    for batched_jobs in batches:
        batch_index += 1
        futures = []

        log.info(f"[SCAN] Starting batch {batch_index} of {num_batches} ({len(batched_jobs)} items)")

        # add each scan job to the execution queue
        for batch_job in batched_jobs:
            log.debug(f"[SCAN] Queueing file for processing: {batch_job.file_path}")

            # dispatch the torrent creation to the pool of worker threads
            future = loop.run_in_executor(creation_executor, utils.create_torrent_threadsafe,
                                          batch_job.file_path, batch_job.torrent_name, batch_job.app_id, batch_job.torrent_file)
            futures.append(future)

        log.info(f"[SCAN] Queued {len(futures)} files for processing")

        # collect the workers as they finish and process their output
        async for future in asyncio.as_completed(futures):
            # keep a rolling count of the total files completed in global variable
            SCAN_DONE_ITEMS += 1
            try:
                future_result: tuple[TorrentCreationMetadata, bool] = await future
                metadata, is_new_file = future_result
                if metadata:

                    # attempt to send torrent file to indexer server
                    uploaded = await utils.send_torrent_to_indexer(metadata.torrent_path, metadata.category, metadata.name, app_id=metadata.app_id)

                    # add the data for the torrent to the database
                    await utils.add_torrent_to_database(metadata.name, metadata.size, metadata.torrent_path, uploaded, metadata.files, metadata.category,
                                                        media_path=metadata.media_path, torrent_hash=metadata.infohash, app_id=metadata.app_id)

                    if is_new_file:
                        created_files += 1
                        # attempt to add the torrent to the libtorrent session right away for immediate seeding
                        if await torrent_client.add_torrent_for_seeding(metadata.torrent_path, metadata.media_path):
                            log.info(f"[SCAN] Created and started seeding new torrent: {metadata.name}")
                        else:
                            log.warning(f"[SCAN] Created but failed to start seeding new torrent: {metadata.name}")
                    else:
                        log.debug(f"[SCAN] Updated existing torrent: {metadata.name}")
            except Exception as e:
                log.error(f"[SCAN] Exception during torrent post-torrent-creation process: {e}")

        log.info(f"[SCAN] Completed batch {batch_index} of {num_batches} ({SCAN_DONE_ITEMS} of {SCAN_TOTAL_ITEMS} total items processed)")

    # shut down the creation executor
    creation_executor.shutdown()
    log.debug(f"[SCAN] Creation executor workers closed")

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

            # spawn a new hash executor
            hash_executor = thread_executor.get_hash_executor()

            total_files, ignored_files, updated_files, created_files = await scan_media_library(hash_executor)

            log.debug("[SCAN] Scan complete, running post-scan checks")

            # update the scan state
            SCAN_PROCESS_STATE = ScannerStates.POST_SCAN.value

            removed_entries = 0
            duplicate_entries: dict[str, dict] = {}

            loop = asyncio.get_running_loop()
            media_data_entries = await utils.get_managed_media_data()

            # create a map to index entries by their path
            media_entry_path_map = {entry.path: entry for entry in media_data_entries}

            # here we perform various database integrity and value correction checks
            torrents = await database.fetch_all("SELECT * FROM torrents")
            for torrent in torrents:
                torrent_id = torrent["id"]
                torrent_name = torrent["name"]
                torrent_hash = torrent["infohash"]
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
                    await torrent_client.remove_torrent_by_hash(torrent.get("infohash"))
                    # remove torrent file and from database
                    await utils.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] All files missing for '{torrent_name}', removed torrent from database and torrent client")
                    continue

                # case where only the media data is missing, nullify the media_path in the database
                if media_path and not media_exists:
                    updated_files += 1
                    await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent_id,))
                    log.info(f"[SCAN] Media files missing for '{torrent_name}', purged media path from database")

                # case if this is an external torrent (should have a download path), try to locate the download media if it's missing or invalid
                if download_path and (not download_exists or download_path == DOWNLOADS_DIR or
                                      download_path == os.path.join(DOWNLOADS_DIR, (utils.detect_torrent_category(download_path)))):
                    log.info(f"[SCAN] Trying to locate download media for: {torrent_path}")

                    # locate the download in the hash thread pool
                    find_future = loop.run_in_executor(hash_executor, utils.find_media_for_torrent, torrent_path, DOWNLOADS_DIR)
                    download_path = await find_future

                    if download_path:
                        download_exists = os.path.exists(download_path) if download_path else False

                        updated_files += 1
                        # update the database if the download path exists
                        if download_exists:
                            await database.execute("UPDATE torrents SET download_path = ? WHERE id = ?", (download_path, torrent_id,))
                            log.info(f"[SCAN] Updated the download path for '{torrent_name}'")
                        else:
                            await database.execute("UPDATE torrents SET download_path = NULL WHERE id = ?", (torrent_id,))
                            log.info(f"[SCAN] Removed the download path for '{torrent_name}', no file could be matched")

                # case where media exists but the torznab category is unknown (0), try to fix it
                if media_exists and torrent["category"] == 0:
                    category_id = utils.detect_torznab_category(media_path)

                    # update the category if a match was found
                    if category_id != 0:
                        updated_files += 1
                        await database.execute("UPDATE torrents SET category = ? WHERE id = ?", (category_id, torrent_id,))
                        log.info(f"[SCAN] Updated the category to '{category_id}' for '{torrent_name}'")

                # case where we have tracked media but it's somehow set inside the downloads directory, nullify its value so it can be rescanned next time
                if media_path and media_path.startswith(DOWNLOADS_DIR):
                    updated_files += 1
                    await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent_id,))
                    log.info(f"[SCAN] Invalid media path for '{torrent_name}', purged media path from database")
                    continue

                # case where we have a multi-file torrent tracked, but either files inside are still being seeded individually or it is no longer discovered
                if media_path and os.path.isdir(media_path) and torrent["files"] > 1:
                    for searching_torrent in torrents:
                        searched_media_path = searching_torrent.get("media_path")

                        # skip empty and identical ID matches
                        if not searched_media_path or searching_torrent["id"] == torrent_id:
                            continue

                        # skip non-matching media paths
                        if os.path.commonpath([searched_media_path, media_path]) != media_path:
                            continue

                        # check the media data entries for a path match
                        if media_path not in media_entry_path_map:
                            # if no match was found, purge the multi-file torrent because it lost discovery - individual episodes exist instead
                            removed_entries += 1
                            # remove from torrent client
                            await torrent_client.remove_torrent_by_hash(torrent.get("infohash"))
                            # remove torrent file and from database
                            await utils.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                            log.warning(f"[SCAN] Purged undiscovered multi-file torrent: '{torrent_name}'")
                            break

                        duplicate_entries[searching_torrent["id"]] = searching_torrent
                        log.warning(f"[SCAN] Potential duplicate seed found for '{torrent_name}': {searching_torrent['name']}")

            # purge duplicate seeds if the user has this option enabled
            if PURGE_DUPLICATE_SEEDS:
                for duplicate_entry in duplicate_entries.values():
                    duplicate_torrent_path = duplicate_entry["torrent_path"]
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(duplicate_entry.get("infohash"))
                    # remove torrent file and from database
                    await utils.remove_torrent_from_database(duplicate_entry.get("infohash"), torrent_file=duplicate_torrent_path)
                    log.info(f"[SCAN] Purged duplicate: {duplicate_entry['name']}")

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

            # purge downloads if the user has this option enabled
            if PURGE_UNTRACKED_DOWNLOADS:
                download_paths = set()

                # build a set of download paths we have tracked
                for torrent in torrents:
                    download_path = torrent.get("download_path")
                    if not download_path:
                        continue
                    download_path = os.path.abspath(download_path)
                    download_paths.add(download_path)

                    # add all files from directory to the set
                    if os.path.isdir(download_path):
                        for root, _, files in os.walk(download_path):
                            for file in files:
                                download_paths.add(os.path.join(root, file))

                # walk over the downloads directory and delete every file which is not being tracked
                for root, _, files in os.walk(DOWNLOADS_DIR):
                    for file in files:
                        file_path = os.path.abspath(os.path.join(root, file))

                        if file_path not in download_paths:
                            os.unlink(file_path)
                            log.info(f"[SCAN] Removed untracked download: {file_path}")

                deleted_dirs = utils.delete_empty_directories(DOWNLOADS_DIR)
                if deleted_dirs:
                    log.info(f"[SCAN] Removed {deleted_dirs} empty download directories")

            duplicate_entries = len(duplicate_entries.keys())

            # close the hash executor
            hash_executor.shutdown()
            log.debug(f"[SCAN] Hash executor workers closed")

            delta = datetime.datetime.now() - before
            log.info(f"[SCAN] Media library scan completed ({delta}): "
                     f"total {total_files} files, {ignored_files} ignored, {updated_files} updated, {created_files} created, {removed_entries} removed, {duplicate_entries} duplicates")

        except Exception as e:
            log.error(f"[SCAN] Exception during periodic scan: {e}")

        # set scan state back to idle
        SCAN_PROCESS_STATE = ScannerStates.IDLE.value

        await asyncio.sleep(SCAN_INTERVAL)
