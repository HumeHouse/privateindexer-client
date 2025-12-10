import asyncio
import datetime
import os
import pickle

from privateindexer_client.core.config import CACHE_FILE, CACHE_CLEAN_INTERVAL
from privateindexer_client.core.logger import log


async def periodic_cache_clean_task():
    """
    Periodically checks cache for stale hashes
    """
    log.debug("[CACHE] Task loop started")
    while True:
        await asyncio.sleep(CACHE_CLEAN_INTERVAL)
        try:
            log.info(f"[CACHE] Starting cache clean operation")
            before = datetime.datetime.now()

            cache = Cache().get_instance()
            file_hashes = cache.file_piece_hash_cache.copy()

            cleaned = 0

            for file_path in file_hashes:
                if not os.path.exists(file_path):
                    cache.delete_all_file_piece(file_path)
                    cleaned += 1

            delta = datetime.datetime.now() - before
            log.info(f"[CACHE] Cache clean completed ({delta}): {len(cache.file_piece_hash_cache)} total, {cleaned} cleaned")
        except Exception as e:
            log.error(f"[CACHE] Error during cache clean task: {e}")


class Cache:
    _instance = None

    def __init__(self):
        self.file_piece_hash_cache: dict[str, dict[int, list[bytes]]] = {}
        self.torrent_info_cache: dict[str, dict[str, int | str]] = {}

    @classmethod
    def get_instance(cls) -> "Cache":
        """
        Get the current Cache instance or create one if not initialized
        """
        if cls._instance is None:
            cls._instance = Cache()
        return cls._instance

    def load(self) -> int:
        """
        Imports persistent pickle data to memory for use during a scan
        """
        # skip if cache file doesn't exist
        if not os.path.exists(CACHE_FILE):
            log.debug(f"[CACHE] No cache file found at {CACHE_FILE}")
            return 0

        # attempt to load values from file into cache
        try:
            # pickle load the data
            with open(CACHE_FILE, "rb") as f:
                data = pickle.load(f)

            self.file_piece_hash_cache = data

            list_count = len(self.file_piece_hash_cache)

            log.debug(f"[CACHE] Loaded {list_count} file hash lists")
            return list_count

        except Exception as e:
            # on fail, reset the dict and delete the file since it may just be corrupt
            self.file_piece_hash_cache = {}
            os.unlink(CACHE_FILE)

            log.error(f"[CACHE] Error loading cache, file purged: {e}")

    def save(self):
        """
        Exports file hash cache to disk for persistent storage
        """
        try:
            with open(CACHE_FILE, "wb") as f:
                pickle.dump(self.file_piece_hash_cache, f, protocol=pickle.HIGHEST_PROTOCOL)

            log.debug(f"[CACHE] Saved {len(self.file_piece_hash_cache)} file hash lists")

        except Exception as e:
            log.error(f"[CACHE] Error saving cache: {e}")

    def get_file_piece(self, file_path: str, piece_length: int) -> list[bytes] | None:
        """
        Get file hash from cache
        """
        if file_path in self.file_piece_hash_cache:
            if piece_length in self.file_piece_hash_cache[file_path]:
                return self.file_piece_hash_cache[file_path][piece_length]

        log.debug(f"[CACHE] Hash cache miss for file: {file_path}")
        return None

    def put_file_piece(self, file_path: str, piece_length: int, hashes: list[bytes]):
        """
        Stores a file hash in cache
        """
        self.file_piece_hash_cache.setdefault(file_path, {})
        self.file_piece_hash_cache[file_path][piece_length] = hashes

    def delete_all_file_piece(self, file_path: str):
        """
        Removes a file hash in cache
        """
        self.file_piece_hash_cache.pop(file_path, None)

    def get_torrent_object(self, torrent_path: str) -> dict | None:
        """
        Get torrent object from cache
        """
        if torrent_path in self.torrent_info_cache:
            return self.torrent_info_cache[torrent_path]

        log.debug(f"[CACHE] Info cache miss for torrent file: {torrent_path}")
        return None

    def put_torrent_object(self, torrent_path: str, obj: dict):
        """
        Stores a torrent object in cache
        """
        self.torrent_info_cache[torrent_path] = obj
