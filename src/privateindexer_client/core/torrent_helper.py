import datetime
import hashlib
import os
from typing import Any
from urllib.parse import urlparse

import libtorrent as lt

from privateindexer_client.core import media_helper, database, utils
from privateindexer_client.core.cache import Cache
from privateindexer_client.core.config import TORRENTS_DIR, APP_VERSION, INDEXER_API_URL
from privateindexer_client.core.logger import log


def generate_media_hash(media_paths: list[str]) -> list[bytes]:
    """
    Return list of SHA1 hashes (hex) of media using libtorrent
    """
    before = datetime.datetime.now()
    parent_directory = os.path.dirname(media_paths[0])
    log.debug(f"[TORRENT] Generating hashes for '{parent_directory}'")

    try:
        # initialize a file storage and add the media to it
        fs = lt.file_storage()

        # add the media to the file storage
        for media_path in media_paths:
            # check if file is valid
            if not utils.valid_file(media_path):
                log.warning(f"[TORRENT] File invalid, it will not be hashed: {media_path}")
                continue
            file_size = os.path.getsize(media_path)
            fs.add_file(os.path.join(os.path.basename(parent_directory), os.path.basename(media_path)), file_size)

        # use libtorrent helpers to generate the hashes
        torrent = lt.create_torrent(fs)
        lt.set_piece_hashes(torrent, os.path.dirname(parent_directory))
        torrent_info = lt.torrent_info(torrent.generate())

        # pull the hashes from the torrent info
        hashes = [torrent_info.hash_for_piece(i) for i in range(torrent_info.num_pieces())]

    except Exception as e:
        log.error(f"[TORRENT] Exception while generating hashes for '{parent_directory}': {e}")
        return []

    delta = datetime.datetime.now() - before
    log.debug(f"[TORRENT] Hashed {len(hashes)} chunks from '{parent_directory}' in {delta}")

    return hashes


def torrent_matches_media(torrent_path: str, media_paths: list[str]) -> bool:
    """
    Checks if a torrent file and media hashes match
    Calculates hash of the media and compares to the hashes found in the torrent file
    """
    parent_directory = os.path.dirname(media_paths[0])
    # check if torrent piece hashes are cached
    try:
        cache = Cache.get_instance()
        cached_data = cache.get_torrent_object(torrent_path)

        # check if the torrent object is cached
        if cached_data:
            # read the hashes from the cache
            torrent_hashes = cached_data["torrent_hashes"]
            piece_length = cached_data["piece_length"]
        else:
            # read torrent info from file
            info = lt.torrent_info(torrent_path)

            # get piece length
            piece_length = info.piece_length()

            # get piece hashes from torrent info
            torrent_hashes = [info.hash_for_piece(i) for i in range(info.num_pieces())]

            # store in cache
            torrent_object = {"piece_length": piece_length, "torrent_hashes": torrent_hashes, }
            cache.put_torrent_object(torrent_path, torrent_object)

        # create an index based on the media paths
        path_list_index = hashlib.md5(("".join(sorted(media_paths))).encode()).hexdigest()

        # check if file hashes are cached
        file_hashes = cache.get_file_hashes(path_list_index, piece_length)

        # if no cache exists, generate new hashes
        if file_hashes is None:
            # hash the media using libtorrent
            file_hashes = generate_media_hash(media_paths)

            # store in cache
            cache.put_file_hashes(path_list_index, piece_length, file_hashes)

        return file_hashes == torrent_hashes
    except Exception as e:
        log.error(f"[TORRENT] Exception while comparing hashes for '{parent_directory}' to '{torrent_path}': {e}")
        return False


class TorrentCreationMetadata:
    def __init__(self):
        self.app_id: int = None
        self.name: str = None
        self.size: int = None
        self.file_paths: list[str] = None
        self.torrent_path: str = None
        self.uploaded: bool = None
        self.torznab_category: int = None
        self.infohash: str = None
        self.seed_path: str = None


def create_torrent(media_paths: list[str], torrent_name: str, app_id: int, output_torrent_file: str = None) -> tuple[TorrentCreationMetadata, bool]:
    """
    Synchronous routine to build and generate a complete torrent file from the media passed in as media_paths
    Checks if output torrent file already exists and skips the torrent generation process
    Will fail if hash checks do not succeeed
    Removes the torrent file if any failures occur so a new one can be generated
    """
    # get the input path parent directory name, use the first media path in the list
    parent_directory = os.path.dirname(media_paths[0])
    is_multi_file = len(media_paths) > 1

    # check if the torrent file supplied exists
    if output_torrent_file and os.path.exists(output_torrent_file):
        is_new_torrent = False
        # skip generation if torrent exists
        log.info(f"[TORRENT] Torrent file for '{torrent_name}' already exists, generation will be skipped")

        # attempt to pull the file size and hash information from the torrent file, otherwise fail and remove torrent file from disk
        try:
            info = lt.torrent_info(output_torrent_file)
            hashes = info.info_hashes()
            torrent_infohash = str(hashes.v2)
            total_media_size = info.files().total_size()
        except Exception as e:
            log.error(f"[TORRENT] Exception while reading hash for '{output_torrent_file}', it has been removed: {e}")
            try:
                os.unlink(output_torrent_file)
            except Exception as e:
                log.error(f"[TORRENT] Exception while removing torrent file '{output_torrent_file}': {e}")
            return None, False

    else:
        is_new_torrent = True

        log.info(f"[TORRENT] Creating torrent '{torrent_name}'")

        # create the file storage object
        fs = lt.file_storage()

        # add the media to the file storage
        for media_path in media_paths:
            # check if file is valid
            if not utils.valid_file(media_path):
                log.warning(f"[TORRENT] File invalid, it will not be added to torrent: {media_path}")
                continue

            file_size = os.path.getsize(media_path)

            # add mutli-file torrents to a parent directory
            if is_multi_file:
                fs.add_file(os.path.join(os.path.basename(parent_directory), os.path.basename(media_path)), file_size)

            # single-file torrents get added directly to the root
            else:
                fs.add_file(os.path.basename(media_path), file_size)

        # create the torrent from the file storage object
        t = lt.create_torrent(fs)
        t.set_creator(f"PrivateIndexer Client v{APP_VERSION}")
        t.set_priv(True)

        # build peice map from parent directory
        lt.set_piece_hashes(t, os.path.dirname(parent_directory) if is_multi_file else parent_directory)

        # generate and bencode the torrent files
        torrent_content = lt.bencode(t.generate())

        # pull the torrent info from the newly generated content
        info = lt.torrent_info(torrent_content)
        hashes = info.info_hashes()
        torrent_infohash = str(hashes.v2)
        total_media_size = info.files().total_size()

        output_torrent_file = os.path.join(TORRENTS_DIR, f"{torrent_infohash}.torrent")

        # write the new content to the torrent file
        with open(output_torrent_file, "wb") as f:
            f.write(torrent_content)

    category_id = media_helper.detect_torznab_category(parent_directory)

    torrent_metadata = TorrentCreationMetadata()
    torrent_metadata.app_id = app_id
    torrent_metadata.name = torrent_name
    torrent_metadata.size = total_media_size
    torrent_metadata.file_paths = media_paths
    torrent_metadata.torrent_path = output_torrent_file
    torrent_metadata.uploaded = False
    torrent_metadata.torznab_category = category_id
    torrent_metadata.infohash = torrent_infohash
    torrent_metadata.seed_path = os.path.dirname(parent_directory) if is_multi_file else parent_directory

    return torrent_metadata, is_new_torrent


def create_torrent_threadsafe(media_paths: list[str], torrent_name: str, app_id: int, output_torrent_file: str = None) -> tuple[TorrentCreationMetadata, bool]:
    """
    Wraps the create_torrent() routine in a try/accept to catch all runtime errors
    """
    try:
        return create_torrent(media_paths, torrent_name, app_id, output_torrent_file)
    except Exception as e:
        log.error(f"[TORRENT] Exception while creating torrent for '{torrent_name}': {e}")
        return None, False


def find_existing_torrent(torrents: list[dict[str, Any]], media_paths: list[str], ignored_torrents: set[str]) -> dict[str, Any]:
    """
    Given a media path, check if a torrent already exists in TORRENTS_DIR with the same name or hash
    Returns the existing torrent path if found, otherwise None
    """
    parent_directory = os.path.dirname(media_paths[0])
    log.debug(f"[SCAN] Trying to locate torrent file for: {parent_directory}")

    # find a torrent whose file hashes match that of what we are looking for
    for torrent in torrents:

        torrent_path = torrent.get("torrent_path")

        # skip any torrent files which are passed through to ignore
        if torrent_path in ignored_torrents:
            continue

        # check if file hashes inside the torrent match what is on the disk
        if not torrent_matches_media(torrent_path, media_paths):
            continue

        # if all checks pass, the torrent is a match
        log.debug(f"[TORRENT] Matched '{parent_directory}' to '{torrent_path}' by hash")
        return torrent

    log.debug(f"[TORRENT] Couldn't find torrent file for: {parent_directory}")
    return None


async def add_torrent_to_database(name: str, size: int, torrent_path: str, uploaded: bool, category: int, file_paths: list[str] = None, download_path: str = None,
                                  torrent_hash: str = None, app_id: str = None):
    """
    Add torrent metadata into the database or update upon duplicate torrent_path
    """
    await database.execute("INSERT INTO torrents (name, size, torrent_path, uploaded, category, download_path, infohash, app_id)"
                           "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                           "ON CONFLICT(torrent_path) DO UPDATE SET name=excluded.name, size=excluded.size, uploaded=excluded.uploaded, category=excluded.category, download_path=COALESCE(excluded.download_path, download_path), infohash=excluded.infohash, app_id=excluded.app_id",
                           (name, size, torrent_path, uploaded, category, download_path, torrent_hash, app_id))

    result = await database.fetch_one("SELECT id FROM torrents WHERE infohash = ?", (torrent_hash,))
    torrent_id = result["id"]

    if file_paths is not None:
        # loop through each file path and add to media table
        for file_path in file_paths:
            # check if file exists and is actually a file
            if not utils.valid_file(file_path):
                log.warning(f"[TORRENT] File path invalid, not added to database: {file_path}")
                continue

            # get file size and insert into media table
            file_size = os.path.getsize(file_path)
            await database.execute("INSERT INTO media (torrent_id, size, file_path) VALUES (?, ?, ?)"
                                   "ON CONFLICT(file_path) DO UPDATE SET torrent_id=excluded.torrent_id, size=excluded.size", (torrent_id, file_size, file_path))


async def remove_torrent_from_database(torrent_hash: str, remove_torrent_file: bool = True, torrent_file: str = None) -> bool:
    """
    Delete torrent metadata from the database
    Optionally deletes torrent file
    """
    # fetch the torrent ID for this hash
    result = await database.fetch_one("SELECT id, torrent_path FROM torrents WHERE infohash = ?", (torrent_hash,))
    torrent_id = result.get("id")
    if torrent_id is None:
        log.warning(f"[TORRENT] Torrent hash not in database during removal: {torrent_id}")

    if remove_torrent_file:
        if torrent_file is None:
            torrent_file = result.get("torrent_path")

        if torrent_file is not None:
            if os.path.exists(torrent_file):
                try:
                    os.unlink(torrent_file)
                except Exception as e:
                    log.error(f"[TORRENT] Exception while removing torrent file '{torrent_file}': {e}")

    if torrent_id is not None:
        # purge media first, then torrent
        await database.execute("DELETE FROM media WHERE torrent_id = ?", (torrent_id,))
        await database.execute("DELETE FROM torrents WHERE id = ?", (torrent_id,))
        return True

    return False


def validate_torrent_url(raw_url: str) -> str | None:
    """
    Validate a user-supplied torrent URL to reduce SSRF risk
    Only HTTP/HTTPS URLs pointing to the same host as INDEXER_API_URL are allowed
    """
    if not raw_url:
        return None

    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    host = parsed.hostname or ""

    indexer_parsed = urlparse(INDEXER_API_URL)
    allowed_host = indexer_parsed.hostname
    if allowed_host and host != allowed_host:
        return None

    return raw_url
