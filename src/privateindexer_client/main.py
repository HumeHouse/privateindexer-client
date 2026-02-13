import asyncio
from asyncio import Task
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from privateindexer_client.core import torrent_client, scan, api, gui, database, utils, sync, radarr, sonarr, cache, lidarr, memory, thread_executor, stats_manager, \
    httpx_request, config
from privateindexer_client.core.cache import Cache
from privateindexer_client.core.config import SCAN_INTERVAL, TORRENTING_PORT, APP_VERSION, MAX_THREADS, FASTRESUME_INTERVAL, RADARR_URL, SONARR_URL, SYNC_INTERVAL, \
    CACHE_CLEAN_INTERVAL, LEW_MEMORY_MODE, LIDARR_URL, MEMORY_LOG_INTERVAL, ANNOUNCE_IP, TAG_SEARCH_RESULTS, INDEXER_API_URL, API_KEY
from privateindexer_client.core.logger import log

APP_TASKS: list[Task] = []


async def startup_tasks():
    """
    Async startup task to ensure processes start in order
    """
    global APP_TASKS
    # wait for fastresume data to load into the client session before continuing to avoid blocking other tasks
    await torrent_client.load_fastresume_data()

    log.info("[APP] Creating periodic tasks")

    # send the system task to the asyncio scheduler and store them in the app state
    APP_TASKS.extend([
        asyncio.create_task(scan.periodic_scan_task(), name="scan"),
        asyncio.create_task(torrent_client.periodic_torrent_status_task(), name="status"),
        asyncio.create_task(torrent_client.periodic_fastresume_task(), name="fastresume"),
        asyncio.create_task(torrent_client.periodic_alerts_task(), name="alerts"),
        asyncio.create_task(sync.periodic_sync_task(), name="sync"),
        asyncio.create_task(cache.periodic_cache_clean_task(), name="cache_clean"),
    ])


@asynccontextmanager
async def lifespan(_: FastAPI):
    global APP_TASKS
    log.info(f"[APP] Starting PrivateIndexer client v{APP_VERSION}")

    # activate memory logging if user has enabled
    if MEMORY_LOG_INTERVAL > 0:
        log.info(f"[APP] Started memory logging every {MEMORY_LOG_INTERVAL} seconds")
        APP_TASKS.append(asyncio.create_task(memory.periodic_memory_task(), name="memory"))

    # initialize the database
    try:
        await database.initialize()
        log.info("[APP] Database initialized")
    except Exception as e:
        log.error(f"[APP] Exception while setting up database: {e}")
        exit(1)

    log.info(f"[APP] Scan interval: {SCAN_INTERVAL} seconds")
    log.info(f"[APP] Sync interval: {SYNC_INTERVAL} seconds")
    log.info(f"[APP] Cache clean interval: {CACHE_CLEAN_INTERVAL} seconds")
    log.info(f"[APP] Fastresume interval: {FASTRESUME_INTERVAL} seconds")

    log.info(f"[APP] Maximum threads: {MAX_THREADS}")

    # test Radarr connection if user has it configured
    if RADARR_URL:
        await radarr.test_connection()

    # test Sonarr if user has it configured
    if SONARR_URL:
        await sonarr.test_connection()

    # test Lidarr connection if user has it configured
    if LIDARR_URL:
        await lidarr.test_connection()

    log.debug(f"[APP] Opening libtorrent session")
    # init the libtorrent client session
    torrent_client.create_libtorrent_session(APP_VERSION)
    log.info(f"[APP] Started libtorrent session - listening on port {TORRENTING_PORT}")

    if LEW_MEMORY_MODE:
        log.warning(f"[APP] Libtorrent client is running in Lew memory mode")

    # attempt to authenticate with the API to validate the API key and check our external IP/port accessibility, otherwise warn console
    log.debug(f"[APP] Trying to connect to PrivateIndexer server")
    try:
        async with httpx_request.get_client() as client:
            params = {"v": APP_VERSION, "port": TORRENTING_PORT, "public_uploads": TAG_SEARCH_RESULTS}
            if ANNOUNCE_IP:
                params["announce_ip"] = ANNOUNCE_IP
            indexer_response = await client.get(f"{INDEXER_API_URL}/user", headers={"X-API-Key": API_KEY}, params=params, timeout=10)
            status_code = indexer_response.status_code
            if status_code == 200:
                response_json = indexer_response.json()
                user_label = response_json["user_label"]
                announce_ip = response_json["announce_ip"]
                log.info(f"[APP] Connected to PrivateIndexer server as '{user_label}'")
                is_reachable = response_json["is_reachable"]
                if is_reachable:
                    log.info(f"[APP] PrivateIndexer server successfully verified we are REACHABLE at {announce_ip}:{TORRENTING_PORT}")
                else:
                    log.critical(f"[APP] PrivateIndexer server is UNABLE TO REACH US at {announce_ip}:{TORRENTING_PORT} - check your port forwarding settings")
                    exit(1)
            elif status_code == 403:
                log.critical("[APP] API key rejected by PrivateIndexer server")
                exit(1)
            else:
                log.warning(f"[APP] Unable to validate API key and port status with PrivateIndexer server - server could be down (status {status_code})")
    except Exception as e:
        log.error(f"[APP] Exception while validating API key: {e}")
        exit(1)

    log.debug("[APP] Loading cache")
    cache = Cache.get_instance()

    total_hashes = cache.total_file_hash_entries()
    total_objects = cache.total_torrent_object_entries()
    file_hash_size = cache.file_hash_size()
    torrent_object_size = cache.torrent_object_size()
    cache_size = utils.format_bytes(file_hash_size + torrent_object_size)
    log.info(f"[APP] Cache loaded ({cache_size}): {total_hashes} file hashes, {total_objects} torrent objects")

    log.info("[APP] Running startup tasks")

    asyncio.create_task(startup_tasks())

    log.info("[APP] API server started on 0.0.0.0:8080")

    yield

    log.info("[APP] Shutting down PrivateIndexer client")

    log.info("[APP] Saving all-time stats")
    all_time_download, all_time_upload = torrent_client.get_all_time_stats()
    stats_manager.save_persistent_stats(all_time_download, all_time_upload)

    log.info(f"[APP] Stopping tasks")

    for task in APP_TASKS:
        try:
            task.cancel()
        except Exception:
            pass

    log.info(f"[APP] Shutting down executor process pools")

    # if any of the executors are alive, shut down each indidivually
    fastresume_executor = thread_executor.get_fastresume_executor(spawn_new=False)
    if fastresume_executor:
        fastresume_executor.shutdown(wait=False)
    creation_executor = thread_executor.get_creation_executor(spawn_new=False)
    if creation_executor:
        creation_executor.shutdown(wait=False)
    hash_executor = thread_executor.get_hash_executor(spawn_new=False)
    if hash_executor:
        hash_executor.shutdown(wait=False)

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

    log.info("[APP] Shutdown complete, closing libtorrent session")


# validate Python environment
config.validate_environment()

app = FastAPI(lifespan=lifespan)
# mount the static files directory to fastapi
app.mount("/static", StaticFiles(directory="/app/src/static"), name="static")

app.include_router(api.router)
app.include_router(gui.router)
