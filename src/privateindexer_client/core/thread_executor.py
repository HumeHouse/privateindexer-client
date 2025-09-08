from concurrent.futures.process import ProcessPoolExecutor
from math import floor

from privateindexer_client.core.config import MAX_THREADS

half_threads = floor(MAX_THREADS / 2)

# intial threaded worked for loading fastresume - can use all 48
FASTRESUME_EXECUTOR = ProcessPoolExecutor(max_workers=MAX_THREADS)
# seach and creation threaded tasks will share the max threads during runtime
CREATE_EXECUTOR = ProcessPoolExecutor(max_workers=half_threads)
SEARCH_EXECUTOR = ProcessPoolExecutor(max_workers=half_threads)
