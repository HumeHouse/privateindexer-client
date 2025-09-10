from concurrent.futures.process import ProcessPoolExecutor

from privateindexer_client.core.config import MAX_THREADS

# intial threaded workload for loading fastresume - can use all threads
FASTRESUME_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_THREADS)
# torrent creation threaded workload - can use all threads
SCAN_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_THREADS)
