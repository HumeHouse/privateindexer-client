import asyncio
import datetime
import os

from diskcache import Cache as DiskCache

from privateindexer_client.core.config import CACHE_CLEAN_INTERVAL, CACHE_DIR, CACHE_EXPIRATION
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

            # manually expire cache keys
            amount_expired = cache.file_hash_cache.expire()
            amount_expired += cache.torrent_info_cache.expire()

            # clean up dead file hashes
            for file_path in cache.iter_file_hash_paths():
                if not os.path.exists(file_path):
                    cache.delete_file_hashes(file_path)

            # clean up dead torrent objects
            for torrent_path in cache.iter_torrent_object_paths():
                if not os.path.exists(torrent_path):
                    cache.delete_torrent_object(torrent_path)

            delta = datetime.datetime.now() - before
            log.info(f"[CACHE] Cache clean completed ({delta}): {amount_expired} expired")
        except Exception as e:
            log.error(f"[CACHE] Error during cache clean task: {e}")


class Cache:
    _instance = None

    def __init__(self):
        self.file_hash_cache: DiskCache = DiskCache(os.path.join(CACHE_DIR, "file_piece"))
        self.torrent_info_cache: DiskCache = DiskCache(os.path.join(CACHE_DIR, "torrent_info"))

    @classmethod
    def get_instance(cls) -> "Cache":
        """
        Get the current Cache instance or create one if not initialized
        """
        if cls._instance is None:
            cls._instance = Cache()
        return cls._instance

    def get_file_hashes(self, file_path: str, piece_length: int) -> list[bytes] | None:
        """
        Get hashes for a file path in cache
        """
        key = (file_path, piece_length)
        hashes = self.file_hash_cache.get(key)

        if hashes is None:
            log.debug(f"[CACHE] Hash cache miss for file: {file_path}")
        return hashes

    def put_file_hashes(self, file_path: str, piece_length: int, hashes: list[bytes]):
        """
        Store hashes for a file path in cache
        """
        key = (file_path, piece_length)
        self.file_hash_cache.set(key, hashes, expire=CACHE_EXPIRATION)

    def delete_file_hashes(self, file_path: str):
        """
        Removes hashes for a file path from cache
        """
        for key in list(self.file_hash_cache.iterkeys()):
            if key[0] == file_path:
                del self.file_hash_cache[key]

    def iter_file_hash_paths(self):
        """
        Yield unique file paths stored in file hash cache
        """
        seen = set()
        for key in self.file_hash_cache.iterkeys():
            file_path = key[0]
            if file_path not in seen:
                seen.add(file_path)
                yield file_path

    def total_file_hash_entries(self):
        """
        Total file hash cache entries
        """
        return self.file_hash_cache.__len__()

    def file_hash_size(self) -> int:
        """
        Get the total size of file hash cache on disk
        """
        return self.file_hash_cache.volume()

    def get_torrent_object(self, torrent_path: str) -> dict | None:
        """
        Get torrent object from cache
        """
        torrent_object = self.torrent_info_cache.get(torrent_path)

        if torrent_object is None:
            log.debug(f"[CACHE] Info cache miss for torrent file: {torrent_path}")
        return torrent_object

    def put_torrent_object(self, torrent_path: str, obj: dict):
        """
        Stores a torrent object in cache
        """
        self.torrent_info_cache.set(torrent_path, obj, expire=CACHE_EXPIRATION)

    def delete_torrent_object(self, torrent_path: str):
        """
        Removes torrent object from cache
        """
        del self.torrent_info_cache[torrent_path]

    def iter_torrent_object_paths(self):
        """
        Yield unique torrent paths stored in torrent object cache
        """
        for torrent_path in self.torrent_info_cache.iterkeys():
            yield torrent_path

    def total_torrent_object_entries(self):
        """
        Total file hash cache entries
        """
        return self.torrent_info_cache.__len__()

    def torrent_object_size(self) -> int:
        """
        Get the total size of torrent_object cache on disk
        """
        return self.torrent_info_cache.volume()
