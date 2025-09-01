import asyncio
import datetime
import os

from privateindexer_client.core import torrent_client, database, utils
from privateindexer_client.core.config import SCAN_INTERVAL, TORZNAB_CATEGORY_PATHS, MOVIE_EXTENSIONS, DOWNLOADS_DIR
from privateindexer_client.core.logger import log
from privateindexer_client.core.thread_executor import EXECUTOR


async def scan_media_library():
    """
    Main loop for scanning media libraries defined by user
    Will walk over all defined category paths, each single file gets turned into a single torrent file
    Will ignore existing and correctly uploaded torrent files
    Torrent creation is batched into a multi-threaded executor, number of threads defined by user
    Will attempt to use send_torrent_to_indexer() and seed_torrents() for each torrent if conditions are met
    """
    torrents = await database.fetch_all("SELECT media_path, torrent_path FROM torrents")
    existing_media = {t["media_path"]: t["torrent_path"] for t in torrents}

    total_files = 0
    ignored_files = 0
    created_files = 0
    updated_files = 0

    loop = asyncio.get_running_loop()
    futures = []

    # loop through all files in the media directories
    for category_key, cat_info in TORZNAB_CATEGORY_PATHS.items():
        for root, _, files in os.walk(cat_info["path"]):
            for file in files:
                # skip the file if user doesn't include its extension in configuration
                filename, extension = os.path.splitext(os.path.basename(file))
                if extension.replace(".", "") not in MOVIE_EXTENSIONS:
                    log.debug(f"[SCAN] Skipping file with {extension} extension")
                    continue

                file_path = os.path.join(root, file)
                total_files += 1

                # ignore the media file if the current path is matches what is in the database
                if file_path in existing_media:
                    torrent_path = existing_media[file_path]
                    # only ignore the creation process if the torrent file exists
                    if os.path.exists(torrent_path):
                        ignored_files += 1
                        continue

                log.debug(f"[SCAN] Trying to locate torrent file for: '{file_path}'")
                # ignore the media file if we can find a matching torrent file for it
                torrent_file = utils.find_existing_torrent(file_path)
                if torrent_file:
                    # try to update the media path in the database to match the current path
                    result = await database.fetch_one("SELECT id, name FROM torrents WHERE torrent_path = ?", (torrent_file,))
                    if result and result.get("id") is not None:
                        updated_files += 1
                        # detect category in case it's not matching in the database
                        category_id = utils.detect_torznab_category(file_path)
                        # update the old media location to match current location
                        await database.execute("UPDATE torrents SET media_path = ?, category = ? WHERE id = ?", (file_path, category_id, result["id"],))
                        log.info(f"[SCAN] Updated the media path for '{result["name"]}'")

                log.debug(f"[SCAN] Queueing for torrent creation: '{file_path}'")
                # dispatch the torrent creation to the pool of worker threads
                future = loop.run_in_executor(EXECUTOR, utils.create_torrent_threadsafe, file_path, torrent_file)
                futures.append(future)

    if len(futures) > 0:
        log.info(f"[SCAN] Queued {len(futures)} torrents for creation")

    # collect the workers as they finish and process their output
    async for future in asyncio.as_completed(futures):
        try:
            metadata, is_new_file = await future
            if metadata:
                created_files += 1

                # attempt to send torrent file to indexer server
                uploaded = await utils.send_torrent_to_indexer(metadata["torrent_path"], metadata["category"])

                # add the data for the torrent to the database
                await utils.add_torrent_to_database(metadata["name"], metadata["size"], metadata["torrent_path"], uploaded, metadata["files"], metadata["category"],
                                                    media_path=metadata["media_path"], hash_v1=metadata["hash_v1"], hash_v2=metadata["hash_v2"])

                if is_new_file:
                    # attempt to add the torrent to the libtorrent session right away for immediate seeding
                    await torrent_client.add_torrent_for_seeding(metadata["torrent_path"], metadata["media_path"])

                    log.info(f"[SCAN] Created and started seeding new torrent: {metadata["name"]}")
                else:
                    log.debug(f"[SCAN] Updated existing torrent: {metadata["name"]}")
        except Exception as e:
            log.error(f"[SCAN] Error in torrent post-torrent-creation process: {e}")

    # here we check to make sure the media files for a torrent still exist on the disk, otherwise remove the torrent from the database
    torrents = await database.fetch_all("SELECT * FROM torrents")
    removed_entries = 0
    for torrent in torrents:
        media_path = torrent.get("media_path")
        download_path = torrent.get("download_path")
        media_exists = os.path.exists(media_path) if media_path else False
        download_exists = os.path.exists(download_path) if download_path else False

        # case where both the media and the downloaded data are missing, we assume the user deleted them and purge it
        if not media_exists and not download_exists:
            removed_entries += 1
            # remove from torrent client
            await torrent_client.remove_torrent_by_hash(torrent.get("hash_v2"))
            # remove from database
            await database.execute("DELETE FROM torrents WHERE id = ?", (torrent["id"],))
            log.info(f"[SCAN] All files missing for '{torrent["name"]}', removed torrent from database and torrent client")

        # case where only the media data is missing, remove the media_path in the database
        elif not media_exists:
            updated_files += 1
            await database.execute("UPDATE torrents SET media_path = NULL WHERE id = ?", (torrent["id"],))
            log.info(f"[SCAN] Media files missing for '{torrent["name"]}', purged media path from database")

    return total_files, ignored_files, updated_files, created_files, removed_entries


async def periodic_scan_task():
    """
    Wraps scan_media_library() asynchronously and periodically scans media libraries defined by user
    Will also attempt to resend failed uploads torrents to the PrivateIndexer server after each scan
    """
    log.debug("[SCAN] Task loop started")
    while True:
        try:
            log.info("[SCAN] Scanning media library for new or updated files")
            before = datetime.datetime.now()

            total_files, ignored_files, updated_files, created_files, removed_entries = await scan_media_library()

            # run a check on multiple factors of each torrent in the database
            torrents = await database.fetch_all("SELECT * FROM torrents")
            for torrent in torrents:
                torrent_path = torrent["torrent_path"]
                # remove torrent files that don't exist on the disk from the database
                if not os.path.exists(torrent_path):
                    removed_entries += 1
                    await database.execute("DELETE FROM torrents WHERE id = ?", (torrent["id"],))
                    log.warning(f"[SCAN] Torrent file doesn't exist, removed from database: '{torrent_path}'")
                    continue

                download_path = torrent.get("download_path")
                download_exists = os.path.exists(download_path) if download_path else False

                # if this is an external torrent (should have a download path), try to locate the download media if it's missing
                if download_path and not download_exists:
                    log.debug(f"[SCAN] Trying to locate download media for: '{torrent_path}'")
                    download_path = utils.find_media_for_torrent(torrent_path, DOWNLOADS_DIR)
                    download_exists = os.path.exists(download_path) if download_path else False

                    # update the database if the download path exists
                    if download_exists:
                        updated_files += 1
                        await database.execute("UPDATE torrents SET download_path = ? WHERE id = ?", (download_path, torrent["id"],))
                        log.info(f"[SCAN] Updated the download path for '{torrent["name"]}'")

            delta = datetime.datetime.now() - before
            log.info(f"[SCAN] Media library scan completed ({delta}): "
                     f"total {total_files} files, {ignored_files} ignored, {updated_files} updated, {created_files} created, {removed_entries} removed")

        except Exception as e:
            log.error(f"[SCAN] Error during periodic scan: {e}")
        await asyncio.sleep(SCAN_INTERVAL)
