import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from privateindexer_client.core import torrent_client, scan, api, gui, httpx_request, database
from privateindexer_client.core.config import TORRENTS_DIR, SCAN_INTERVAL, MOVIE_DIR, TORZNAB_CATEGORY_PATHS, INDEXER_API_URL, API_KEY, TORRENTING_PORT, \
    DOWNLOADS_DIR, FASTRESUME_DIR, APP_VERSION, MAX_THREADS, FASTRESUME_INTERVAL
from privateindexer_client.core.logger import log


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info(f"[APP] Starting PrivateIndexer client v{APP_VERSION}")

    # initialize the database
    await database.initialize()

    # try to create torrents and fastresume directories
    log.info(f"[APP] Torrent data directory: {TORRENTS_DIR}")
    os.makedirs(TORRENTS_DIR, exist_ok=True)
    os.makedirs(FASTRESUME_DIR, exist_ok=True)

    # check if the downloads directory exists, otherwise fail
    if os.path.exists(DOWNLOADS_DIR):
        log.info(f"[APP] Downloads directory: {DOWNLOADS_DIR}")
    else:
        log.error(f"[APP] Downloads directory doesn't exist or not accessible: {DOWNLOADS_DIR}")
        exit(1)

    log.info(f"[APP] Scan interval: {SCAN_INTERVAL} seconds")
    log.info(f"[APP] Fastresume interval: {FASTRESUME_INTERVAL} seconds")

    log.info(f"[APP] Maximum threads: {MAX_THREADS}")

    # make sure media directory exists and index it with ID in the category paths
    if MOVIE_DIR.lower() != "false":
        if not os.path.exists(MOVIE_DIR):
            log.error(f"[APP] Movies directory doesn't exist: {MOVIE_DIR}")
            exit(1)
        log.info(f"[APP] Using movies directory: {MOVIE_DIR}")
        TORZNAB_CATEGORY_PATHS["movies"] = {"id": 1000, "path": MOVIE_DIR}

    # try to authenticate with the API to validate the API key, otherwise fail
    try:
        status_code = None
        while status_code not in (403, 200):
            async with httpx_request.get_client() as client:
                indexer_response = await client.get(INDEXER_API_URL + "/user", headers={"X-API-Key": API_KEY}, params={"v": APP_VERSION})
                status_code = indexer_response.status_code
                if status_code == 200:
                    TORRENT_SIGNER = indexer_response.text
                    log.info(f"[APP] Connected to PrivateIndexer server as '{TORRENT_SIGNER}'")
                elif status_code == 403:
                    log.error("[APP] API key rejected by PrivateIndexer server")
                    exit(1)
                else:
                    log.error("[APP] PrivateIndexer server unavailable, trying again in 30 seconds")
                    await asyncio.sleep(30)
    except Exception as e:
        log.error(f"[APP] Failed to validate API key: {e}")
        exit(1)

    log.info(f"[APP] Creating libtorrent session, listening on port {TORRENTING_PORT}")
    # init the libtorrent client session
    torrent_client.create_libtorrent_session(APP_VERSION)

    # load the fastresume data into the client session using background task
    asyncio.create_task(torrent_client.load_fastresume_data())

    log.info("[APP] Starting periodic tasks")

    # send the scan task to the asyncio scheduler
    scan_task = asyncio.create_task(scan.periodic_scan_task())

    # send the torrent status task to the asyncio scheduler
    status_task = asyncio.create_task(torrent_client.periodic_torrent_status_task())

    # send the torrent fastresume task to the asyncio scheduler
    fastresume_task = asyncio.create_task(torrent_client.periodic_fastresume_task())

    # send the torrent alerts task to the asyncio scheduler
    alerts_task = asyncio.create_task(torrent_client.periodic_alerts_task())

    log.info("[APP] API server started on 0.0.0.0:80")

    yield

    log.info("[APP] Shutting down PrivateIndexer client")

    log.info(f"[APP] Stopping tasks")
    scan_task.cancel()
    status_task.cancel()
    fastresume_task.cancel()
    alerts_task.cancel()

    log.info("[APP] Saving fastresume data")

    # send the fastresume
    save_task = asyncio.create_task(asyncio.to_thread(torrent_client.save_all_fastresume_data))

    while not save_task.done():
        try:
            # wait for the task to finish at 60 second intervals
            await asyncio.wait_for(asyncio.shield(save_task), timeout=60)
        except asyncio.TimeoutError:
            log.info("[APP] Still saving fastresume data...")

    # ensure any exceptions from save_task are raised
    await save_task

    log.info("[APP] Shutdown complete")


app = FastAPI(lifespan=lifespan)
# mount the static files directory to fastapi
app.mount("/static", StaticFiles(directory="/app/src/static"), name="static")

app.include_router(api.router)
app.include_router(gui.router)
