from concurrent.futures.process import ProcessPoolExecutor

from privateindexer_client.core.config import MAX_THREADS
from privateindexer_client.core.logger import log

_fastresume_executor = None
_creation_executor = None
_hash_executor = None


def get_fastresume_executor() -> ProcessPoolExecutor:
    """
    Create or return an existing process pool executor for loading fastresume data
    """
    global _fastresume_executor
    if _fastresume_executor is None or _fastresume_executor._shutdown_thread:
        _fastresume_executor = ProcessPoolExecutor(max_workers=MAX_THREADS)
        log.debug("[EXECUTOR] Spawned new fastresume executor")
    return _fastresume_executor


def get_creation_executor() -> ProcessPoolExecutor:
    """
    Create or return an existing process pool executor for creating torrents
    """
    global _creation_executor
    if _creation_executor is None or _creation_executor._shutdown_thread:
        _creation_executor = ProcessPoolExecutor(max_workers=MAX_THREADS)
        log.debug("[EXECUTOR] Spawned new creation executor")
    return _creation_executor


def get_hash_executor() -> ProcessPoolExecutor:
    """
    Create or return an existing process pool executor for hashing files
    """
    global _hash_executor
    if _hash_executor is None or _hash_executor._shutdown_thread:
        _hash_executor = ProcessPoolExecutor(max_workers=MAX_THREADS)
        log.debug("[EXECUTOR] Spawned new hash executor")
    return _hash_executor
