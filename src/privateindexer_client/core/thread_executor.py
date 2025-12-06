from concurrent.futures.process import ProcessPoolExecutor
from concurrent.futures.thread import ThreadPoolExecutor

from privateindexer_client.core.config import MAX_THREADS

# intial process pool for loading fastresume data
FASTRESUME_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_THREADS)
# torrent creation process pool
CREATE_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_THREADS)
# file hashing thread pool
HASH_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_THREADS)
