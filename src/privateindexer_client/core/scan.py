import asyncio
import datetime
import itertools
import os
from collections import defaultdict
from concurrent.futures.process import ProcessPoolExecutor
from enum import Enum

from privateindexer_client.core import torrent_client, database, utils, thread_executor, media_helper, server_interface, torrent_helper, qbit_translator
from privateindexer_client.core.config import SCAN_INTERVAL, DOWNLOADS_DIR, TORRENTS_DIR, PURGE_UNTRACKED_TORRENTS, SCAN_BATCH_SIZE, PURGE_DUPLICATE_SEEDS, \
    PURGE_UNTRACKED_DOWNLOADS
from privateindexer_client.core.logger import log
from privateindexer_client.core.media_helper import MediaDataEntry
from privateindexer_client.core.torrent_helper import TorrentCreationMetadata

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
    def __init__(self, file_paths: list[str]):
        self.file_paths: list[str] = file_paths
        self.torrent_name: str = None
        self.app_id: int = None
        self.torrent_file: str = None


async def scan_media_library(media_data_entries: list[MediaDataEntry], hash_executor: ProcessPoolExecutor) -> tuple[int, int, set[int]]:
    """
    Main loop for scanning media libraries defined by user
    Will walk over all defined category paths, each single file gets turned into a single torrent file
    Will ignore existing and correctly uploaded torrent files
    Torrent creation is batched into a multi-threaded executor, number of threads defined by user
    Will attempt to use send_torrent_to_indexer() and seed_torrents() for each torrent if conditions are met
    """
    global SCAN_PROCESS_STATE, SCAN_TOTAL_ITEMS, SCAN_DONE_ITEMS

    query = """
            SELECT t.id,
                   t.name,
                   t.torrent_path,
                   t.app_id,
                   GROUP_CONCAT(m.file_path || "%SIZEDELIMIT%" || m.size, "%PATHDELIMIT%") AS media_paths
            FROM torrents t
                     LEFT JOIN media m ON t.id = m.torrent_id
            GROUP BY t.id, t.name, t.torrent_path, t.app_id
            """
    torrents = await database.fetch_all(query)

    # ignore torrent files from hash checks which we already track media for in database
    ignored_torrents = set(torrent["torrent_path"] for torrent in torrents if torrent["media_paths"])

    # create a map of torrent data keyed on their database torrent IDs
    torrent_data_map = {}

    # create a map of media files keyed on their database torrent IDs
    torrent_file_map: dict[int, dict[str, int]] = {}
    for torrent in torrents:
        files = {}
        if torrent["media_paths"]:

            # loop through each media path tracked in database
            for entry in torrent["media_paths"].split("%PATHDELIMIT%"):
                # split the previously concatenated string from the database query
                path, size = entry.rsplit("%SIZEDELIMIT%", 1)
                files[path] = int(size)

        # index the files by torrent ID
        torrent_file_map[torrent["id"]] = files

        # update the data map
        torrent_data_map[torrent["id"]] = {"name": torrent["name"], "torrent_path": torrent["torrent_path"], "app_id": torrent["app_id"], }

    # create a lookup index for each file path and the torrent ID it belongs to
    file_path_id_map: dict[str, set[int]] = defaultdict(set)
    for torrent_id, files in torrent_file_map.items():
        for path in files:
            file_path_id_map[path].add(torrent_id)

    total_entries, created_entries = 0, 0
    updated_entries = set()

    loop = asyncio.get_running_loop()

    # set scan state to scanning and update progress values
    SCAN_TOTAL_ITEMS = len(media_data_entries)
    SCAN_DONE_ITEMS = 0
    SCAN_PROCESS_STATE = ScannerStates.SCANNING.value

    # create a list to store files that need to be processed
    scan_jobs: list[ScanTorrentJob] = []

    # loop through the files we intend to scan
    for media_data_entry in media_data_entries:
        file_paths = set(media_data_entry.files)

        # get the input path parent directory name, use the first media path in the list
        parent_directory = os.path.dirname(next(iter(file_paths)))

        # make sure we can see the parent directory of the files passed through from the *arr app
        if not os.path.exists(parent_directory):
            log.warning(f"[SCAN] Path doesn't exist or is not accessible, skipped: {parent_directory}")
            # increment global items counter
            SCAN_DONE_ITEMS += 1
            continue

        total_entries += 1

        # loop through all the files in this media entry and compare to database files
        torrent_id_matches = None
        for path in file_paths:
            # get all torrents with this file associated
            torrents_with_file = file_path_id_map.get(path)

            # if a single path is not available in the database, abort matching and continue to further processing
            if not torrents_with_file:
                torrent_id_matches = set()
                break

            # append the matched ID with previously matched IDs
            torrent_id_matches = (torrents_with_file if torrent_id_matches is None else torrent_id_matches & torrents_with_file)

        # we only want to skip media entries if we actually track everything passed through
        if torrent_id_matches is not None and len(torrent_id_matches) == 1:
            # get the matching torrent ID and files belonging to it
            torrent_id = next(iter(torrent_id_matches))
            tracked_files = set(torrent_file_map[torrent_id].keys())

            # make sure the media entry and the database torrent entry track the exact same file list
            if len(tracked_files) > 0 and tracked_files == file_paths:

                # compare all file sizes on disk with what is tracked in database
                sizes_match = all(utils.valid_file(path) and torrent_file_map[torrent_id][path] == os.path.getsize(path) for path in file_paths)

                # if all sizes match, continue the skip/ignore process
                if sizes_match:
                    torrent_data = torrent_data_map[torrent_id]
                    torrent_name = torrent_data["name"]
                    torrent_file = torrent_data["torrent_path"]

                    # check if the app_id is correct in the database
                    if torrent_data["app_id"] != media_data_entry.app_id:
                        updated_entries.add(torrent_id)
                        # trigger a re-upload to make sure the app metadata gets synced to the server again
                        await database.execute("UPDATE torrents SET app_id = ?, uploaded = FALSE WHERE id = ?", (media_data_entry.app_id, torrent_id,))
                        log.info(f"[SCAN] Updated app ID for torrent '{torrent_name}', will re-upload to server during next sync")

                    # check if the torrent name is correct in the database
                    if torrent_name != media_data_entry.title:
                        updated_entries.add(torrent_id)
                        new_name = media_data_entry.title
                        await database.execute("UPDATE torrents SET name = ? WHERE id = ?", (new_name, torrent_id,))
                        log.info(f"[SCAN] Updated local torrent name from '{torrent_name}' to '{new_name}'")

                    # only ignore the creation process if the torrent file exists
                    if os.path.exists(torrent_file):
                        # increment global items counter
                        SCAN_DONE_ITEMS += 1
                        continue

        # ignore the media files if we can find a matching torrent file
        torrent_data = await (loop.run_in_executor(hash_executor, torrent_helper.find_existing_torrent, torrents, list(file_paths), ignored_torrents))

        if torrent_data:
            torrent_id = torrent_data["id"]
            torrent_name = torrent_data["name"]
            torrent_file = torrent_data["torrent_path"]

            # ignore this torrent file on subsequent loops
            ignored_torrents.add(torrent_file)

            updated_entries.add(torrent_id)
            # detect category in case it's not matching in the database
            category_id = media_helper.detect_torznab_category(parent_directory)
            # update the torrent metadata
            await database.execute("UPDATE torrents SET category = ?, app_id = ? WHERE id = ?", (category_id, media_data_entry.app_id, torrent_id,))

            # clear the old media paths for this torrent
            await database.execute("DELETE FROM media WHERE torrent_id = ?", (torrent_id,))

            # loop through each media entry file
            for file_path in file_paths:
                # check if file is valid
                if not utils.valid_file(file_path):
                    log.warning(f"[SCAN] Skipped invalid file: {file_path}")
                    continue

                # get file size
                file_size = os.path.getsize(file_path)
                # add each file to the database
                await database.execute("INSERT INTO media (torrent_id, size, file_path) VALUES (?, ?, ?)"
                                       "ON CONFLICT(file_path) DO UPDATE SET torrent_id=excluded.torrent_id, size=excluded.size", (torrent_id, file_size, file_path))
            log.info(f"[SCAN] Updated the media paths for '{torrent_name}'")
            # increment global items counter
            SCAN_DONE_ITEMS += 1
            continue

        # construct the scan job
        scan_job = ScanTorrentJob(list(file_paths))
        scan_job.app_id = media_data_entry.app_id
        scan_job.torrent_file = torrent_data["torrent_path"] if torrent_data else None

        if media_data_entry.title:
            scan_job.torrent_name = media_data_entry.title

        # add the scan job to the queue
        scan_jobs.append(scan_job)
        # increment global items counter
        SCAN_DONE_ITEMS += 1

    # stop here if we don't have any new items to process
    if not scan_jobs:
        return total_entries, created_entries, updated_entries

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
            # dispatch the torrent creation to the pool of worker threads
            future = loop.run_in_executor(creation_executor, torrent_helper.create_torrent_threadsafe, batch_job.file_paths, batch_job.torrent_name, batch_job.app_id,
                                          batch_job.torrent_file)
            futures.append(future)

        log.info(f"[SCAN] Queued {len(futures)} jobs for processing")

        # collect the workers as they finish and process their output
        async for future in asyncio.as_completed(futures):
            # keep a rolling count of the total files completed in global variable
            SCAN_DONE_ITEMS += 1
            try:
                future_result: tuple[TorrentCreationMetadata, bool] = await future
                metadata, is_new_torrent = future_result
                if metadata:

                    # attempt to send torrent file to indexer server
                    uploaded = await server_interface.send_torrent_to_indexer(metadata.torrent_path, metadata.torznab_category, metadata.name, app_id=metadata.app_id)

                    # add the data for the torrent to the database
                    await torrent_helper.add_torrent_to_database(metadata.name, metadata.size, metadata.torrent_path, uploaded, metadata.torznab_category,
                                                                 file_paths=metadata.file_paths, torrent_hash=metadata.infohash, app_id=metadata.app_id)

                    if is_new_torrent:
                        created_entries += 1
                        # attempt to add the torrent to the libtorrent session right away for immediate seeding
                        if await torrent_client.add_torrent_for_seeding(metadata.torrent_path, metadata.seed_path, replace=True):
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

    return total_entries, created_entries, updated_entries


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

            # set scan state to pre-scan
            SCAN_PROCESS_STATE = ScannerStates.PRE_SCAN.value

            # spawn a new hash executor
            hash_executor = thread_executor.get_hash_executor()

            # fetch updated root folders from *arr apps
            category_paths = await media_helper.update_torznab_category_paths()

            # fetch the compiled list of tracked media from *arr apps
            media_data_entries = await media_helper.get_managed_media_data()

            total_entries, created_entries, updated_entries = 0, 0, set()

            # make sure we have at least 1 directory to scan, otherwise skip scan
            if len(category_paths) == 0:
                log.warning(f"[SCAN] No root folders accessible for scanning")
            else:
                try:
                    total_entries, created_entries, updated_entries = await scan_media_library(media_data_entries, hash_executor)
                except Exception as e:
                    log.error(f"[SCAN] Exception during periodic scan task (scan/processing): {e}")

            log.debug("[SCAN] Scan complete, running post-scan checks")

            # update the scan state
            SCAN_PROCESS_STATE = ScannerStates.POST_SCAN.value

            removed_entries = 0
            duplicate_entries: dict[str, dict] = {}

            # create a set of media entry paths
            media_entry_path_set = set([file for entry in media_data_entries for file in entry.files])
            # build a map for indexing entry app IDs by their torznab category
            media_entry_torznab_category_map = defaultdict(set)
            for media_entry in media_data_entries:
                media_entry_torznab_category_map[media_entry.torznab_category].add(media_entry.app_id)

            # pull fresh data from torrents and media tables
            query = """
                    SELECT t.id,
                           t.name,
                           t.infohash,
                           t.torrent_path,
                           t.category,
                           t.app_id,
                           t.download_path,
                           GROUP_CONCAT(m.file_path || "%SIZEDELIMIT%" || m.size, "%PATHDELIMIT%") AS media_paths
                    FROM torrents t
                             LEFT JOIN media m ON t.id = m.torrent_id
                    GROUP BY t.id, t.name, t.torrent_path, t.app_id
                    """
            torrents = await database.fetch_all(query)

            # build a map of torrent files indexed by their torrent ID
            torrent_file_map: dict[int, dict[str, int]] = {}
            for torrent in torrents:
                # only map torrents with files
                if torrent["media_paths"]:
                    files = {}
                    # loop through each media path tracked in database
                    for entry in torrent["media_paths"].split("%PATHDELIMIT%"):
                        # split the previously concatenated string from the database query
                        path, size = entry.rsplit("%SIZEDELIMIT%", 1)
                        files[path] = int(size)

                    # index the files by torrent ID
                    torrent_file_map[torrent["id"]] = files

            # here we perform various database integrity and value correction checks
            for torrent in torrents:
                torrent_id = torrent["id"]
                torrent_name = torrent["name"]
                torrent_hash = torrent["infohash"]
                torrent_path: str = torrent["torrent_path"]
                torrent_exists = os.path.exists(torrent_path)
                torznab_category = torrent["category"]
                app_id = torrent.get("app_id")
                download_path: str | None = torrent.get("download_path")
                download_exists = os.path.exists(download_path) if download_path else False

                # pull media files from map
                media_files = torrent_file_map.get(torrent_id, {})

                if media_files:
                    # get the media's parent directory if tracked media is found
                    media_parent_directory = os.path.dirname(next(iter(media_files)))
                    # media is valid if and only if all files exist and file size matches database
                    media_valid = all(utils.valid_file(media_file) and os.path.getsize(media_file) == file_size for media_file, file_size in media_files.items())

                # if no media files are tracked, purge torrent
                elif len(media_entry_torznab_category_map[torznab_category]) > 0 and app_id not in media_entry_torznab_category_map:
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] Media files missing for '{torrent_name}', removed torrent from database and torrent client")
                    continue

                else:
                    media_parent_directory = None
                    media_valid = False

                # case where the torrent file is missing, purge torrent
                if not torrent_exists:
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] Torrent file missing for '{torrent_name}', removed torrent from database and torrent client")
                    continue

                # case where the media data is missing or has mismatch sizes, purge torrent
                if media_files and not media_valid:
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] Media files don't exist or contain size mismatches for '{torrent_name}', removed torrent from database and torrent client")
                    continue

                # case where the app ID is missing, purge torrent
                if media_files and app_id is None:
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] App ID missing for '{torrent_name}', removed torrent from database and torrent client")
                    continue

                # case where the download data doesn't exist or is invalid, clear the download path from database
                if download_path is not None and (not download_exists or download_path == DOWNLOADS_DIR or download_path == os.path.join(DOWNLOADS_DIR, (
                        qbit_translator.detect_torrent_category(download_path)))):
                    updated_entries.add(torrent_id)
                    # remove the download path from database
                    await database.execute("UPDATE torrents SET download_path = NULL WHERE id = ?", (torrent_id,))
                    log.info(f"[SCAN] Download data missing for '{torrent_name}', removed download path from database")

                # case where media exists but the torznab category is unknown (0), try to fix it
                if media_valid and torznab_category == 0 and media_parent_directory is not None:
                    category_id = media_helper.detect_torznab_category(media_parent_directory)

                    # update the category if a match was found
                    if category_id != 0:
                        updated_entries.add(torrent_id)
                        await database.execute("UPDATE torrents SET category = ? WHERE id = ?", (category_id, torrent_id,))
                        log.info(f"[SCAN] Updated the category to '{category_id}' for '{torrent_name}'")

                    # otherwise, remove the torrent
                    else:
                        removed_entries += 1
                        # remove from torrent client
                        await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                        # remove torrent file and from database
                        await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                        log.info(f"[SCAN] Unknown category for '{torrent_name}', removed torrent from database and torrent client")
                        continue

                # check to make sure this torrent's category actually has data from the app
                category_has_tracked_entries = bool(media_entry_torznab_category_map[torznab_category])
                # check for loss of discovery from the media apps
                if category_has_tracked_entries and any(media_file not in media_entry_path_set for media_file in media_files):
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(torrent_hash, True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(torrent_hash, torrent_file=torrent_path)
                    log.info(f"[SCAN] '{torrent_name}' is no longer discovered, removed torrent from database and torrent client")
                    continue

                # case where we have a multi-file torrent tracked, but files inside are still being seeded individually
                if len(media_files) > 1:
                    for searching_torrent in torrents:
                        searching_id = searching_torrent["id"]

                        # skip identical ID matches
                        if searching_id == torrent_id:
                            continue

                        # pull media paths from map
                        searched_media_files = torrent_file_map.get(searching_id, {})

                        # skip empty matches and other multi-file torrents
                        if not searched_media_files or len(searched_media_files) > 1:
                            continue

                        # get the parent directory of the searched torrent
                        searched_parent_directory = os.path.dirname(next(iter(searched_media_files)))

                        # if a common parent directory is shared between the current torrent's file and the comparison, mark as duplicate
                        if searched_parent_directory == media_parent_directory:
                            duplicate_entries[searching_torrent["id"]] = searching_torrent
                            log.warning(f"[SCAN] Potential duplicate seed found for '{torrent_name}': {searching_torrent['name']}")

            # purge duplicate seeds if the user has this option enabled
            if PURGE_DUPLICATE_SEEDS:
                for duplicate_entry in duplicate_entries.values():
                    duplicate_torrent_path = duplicate_entry["torrent_path"]
                    removed_entries += 1
                    # remove from torrent client
                    await torrent_client.remove_torrent_by_hash(duplicate_entry["infohash"], True)
                    # remove torrent file and from database
                    await torrent_helper.remove_torrent_from_database(duplicate_entry["infohash"], torrent_file=duplicate_torrent_path)
                    log.info(f"[SCAN] Purged duplicate: {duplicate_entry['name']}")

            # only purge dangling torrents if the user has this option enabled
            if PURGE_UNTRACKED_TORRENTS:
                torrent_paths = [torrent["torrent_path"] for torrent in torrents]
                for fname in os.listdir(TORRENTS_DIR):
                    # ignore non-torrent files
                    if not fname.endswith(".torrent"):
                        continue
                    torrent_path = os.path.join(TORRENTS_DIR, fname)
                    if torrent_path not in torrent_paths and os.path.exists(torrent_path):
                        try:
                            os.unlink(torrent_path)
                            log.info(f"[SCAN] Removed dangling torrent file '{torrent_path}'")
                        except Exception as e:
                            log.error(f"[SCAN] Exception while removing dangling torrent file '{torrent_path}': {e}")

            # purge downloads if the user has this option enabled
            if PURGE_UNTRACKED_DOWNLOADS:
                # fetch fresh torrent metadata from database
                torrents = await database.fetch_all("SELECT * FROM torrents WHERE download_path IS NOT NULL")

                # build a set of download paths we have tracked
                download_paths = set()
                for torrent in torrents:
                    download_path = torrent["download_path"]

                    download_path = os.path.abspath(download_path)
                    # add all files from directory to the set
                    if os.path.isdir(download_path):
                        for root, _, files in os.walk(download_path):
                            for file in files:
                                download_paths.add(os.path.join(root, file))
                    else:
                        download_paths.add(download_path)

                # walk over the downloads directory and delete every file which is not being tracked
                for root, _, files in os.walk(DOWNLOADS_DIR):
                    for file in files:
                        file_path = os.path.abspath(os.path.join(root, file))

                        if file_path not in download_paths and os.path.isfile(file_path):
                            try:
                                os.unlink(file_path)
                                log.info(f"[SCAN] Removed untracked downloaded file: {file_path}")
                            except Exception as e:
                                log.error(f"[SCAN] Exception while removing untracked downloaded file '{file_path}': {e}")

            # delete empty download directories for each torrent category
            deleted_dirs = qbit_translator.purge_empty_categories()
            if deleted_dirs:
                log.info(f"[SCAN] Removed {deleted_dirs} empty download directories")

            # close the hash executor
            hash_executor.shutdown()
            log.debug(f"[SCAN] Hash executor workers closed")

            updated = len(updated_entries)
            stats = [("total", total_entries), ("created", created_entries), ("removed", removed_entries), ("updated", updated), ]

            stats_list = ", ".join(f"{count} {name}" for name, count in stats if count > 0)
            delta = datetime.datetime.now() - before

            log.info(f"[SCAN] Media library scan completed ({delta}): {stats_list}")

        except Exception as e:
            log.error(f"[SCAN] Exception during periodic scan task (pre/post-processing): {e}")

        # set scan state back to idle
        SCAN_PROCESS_STATE = ScannerStates.IDLE.value

        await asyncio.sleep(SCAN_INTERVAL)
