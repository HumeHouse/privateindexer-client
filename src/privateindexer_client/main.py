import asyncio
import os
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI

from privateindexer_client.core import torrent_client, scan
from privateindexer_client.core.config import TORRENTS_DIR, SCAN_INTERVAL, SCANNER_THREADS, MOVIE_DIR, CATEGORY_PATHS, INDEXER_API_URL, API_KEY, TORRENTING_PORT
from privateindexer_client.core.logger import log

APP_VERSION = "1.2.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(f"[APP] Starting PrivateIndexer client v{APP_VERSION}")

    # check if the torrent storage directory exists, otherwise create it
    if os.path.exists(TORRENTS_DIR):
        log.info(f"[APP] Torrents directory: {TORRENTS_DIR}")
    else:
        log.info(f"[APP] Creating torrents directory: {TORRENTS_DIR}")
        os.makedirs(TORRENTS_DIR)

    # create the multi-thread executor with user-defined number of threads
    log.info(f"[APP] Scan interval: {SCAN_INTERVAL} seconds")
    log.info(f"[APP] Scanner threads: {SCANNER_THREADS}")

    # make sure media directory exists and index it with ID in the category paths
    if MOVIE_DIR.lower() != "false":
        if not os.path.exists(MOVIE_DIR):
            log.error(f"[APP] Movies directory doesn't exist: {MOVIE_DIR}")
            exit(1)
        log.info(f"[APP] Using movies directory: {MOVIE_DIR}")
        CATEGORY_PATHS["movies"] = {"id": 1000, "path": MOVIE_DIR}

    # try to authenticate with the API to validate the API key, otherwise fail
    try:
        status_code = None
        while status_code not in (403, 200):
            indexer_response = requests.get(f"{INDEXER_API_URL}/user?apikey={API_KEY}&v={APP_VERSION}")
            status_code = indexer_response.status_code
            if status_code == 200:
                TORRENT_SIGNER = indexer_response.text
                log.info(f"[APP] Connected to PrivateIndexer server as '{TORRENT_SIGNER}'")
            elif status_code == 403:
                log.error(f"[APP] API key rejected by PrivateIndexer server")
                exit(1)
            else:
                log.error(f"[APP] PrivateIndexer server unavailable, trying again in 30 seconds")
                await asyncio.sleep(30)
    except Exception as e:
        log.error(f"[APP] Failed to validate API key: {e}")
        exit(1)

    log.info(f"[APP] Creating libtorrent session, listening on port {TORRENTING_PORT}")
    # init the libtorrent client session
    torrent_client.create_libtorrent_session()

    log.info("[APP] Starting periodic tasks")

    # send the scan task to the asyncio scheduler
    asyncio.create_task(scan.periodic_scan_task())

    # send the torrent status task to the asyncio scheduler
    asyncio.create_task(torrent_client.periodic_torrent_status_task())

    yield

    log.info(f"[APP] Shutting down PrivateIndexer client")


app = FastAPI(lifespan=lifespan)
