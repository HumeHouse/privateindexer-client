import os
import tempfile
import time

import libtorrent as lt
from fastapi import Depends, HTTPException, Query, File, Request, Form, APIRouter, status, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse

from privateindexer_client.core import torrent_client, utils, httpx_request, database, qbit_translator, torrent_helper
from privateindexer_client.core.config import API_KEY, DOWNLOADS_DIR, INDEXER_API_URL, API_USERNAME, API_PASSWORD
from privateindexer_client.core.logger import log

router = APIRouter(prefix="/api/v2")

SESSIONS = {}
# 30-day session lifetime
SESSION_TTL = 60 * 60 * 24 * 30


async def cookie_required(request: Request) -> Request:
    """
    Dependency for checking the cookie's value against the user's API key
    """
    # get the SID cookie
    sid = request.cookies.get("SID")

    # check session ID validity
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    # check expiration of session
    if time.time() > SESSIONS[sid]:
        # remove expired sessions
        del SESSIONS[sid]
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    # refresh session lifetime
    SESSIONS[sid] = time.time() + SESSION_TTL

    return request


@router.get("/health")
def get_health():
    """
    Endpoint to be used by Docker for checking the readiness of the API
    """
    return PlainTextResponse("OK")


@router.get("/app/version")
async def app_version():
    """
    Mimics qBittorrent endpoint /api/v2/app/version
    """
    return PlainTextResponse("v5.1.2")


@router.get("/app/webapiVersion")
async def app_webapi_version():
    """
    Mimics qBittorrent endpoint /api/v2/app/webapiVersion
    """
    return PlainTextResponse("2.11.4")


@router.post("/auth/login")
async def auth_login(request: Request, username: str = Form(), password: str = Form()):
    """
    Mimics qBittorrent endpoint /api/v2/auth/login
    Accepts form username/password (any username is accepted) with password being the user's API key
    Returns a SID cookie for compatibility with the user's API_KEY
    """
    if not username or not password:
        log.warning(f"[API] API login failed, missing username or password ({request.headers.get("user-agent")})")
        return PlainTextResponse("Fails.")
    if username != API_USERNAME:
        log.warning(f"[API] API login failed, invalid username: {username} ({request.headers.get("user-agent")})")
        return PlainTextResponse("Fails.")
    if password != API_PASSWORD:
        log.warning(f"[API] API login failed, invalid key: {password} ({request.headers.get("user-agent")})")
        return PlainTextResponse("Fails.")

    sid = utils.generate_sid()
    SESSIONS[sid] = time.time() + SESSION_TTL

    log.debug(f"[API] API login successful ({request.headers.get("user-agent")})")

    response = PlainTextResponse("Ok.")
    response.set_cookie(key="SID", value=sid, httponly=True, secure=False, path="/")
    return response


@router.get("/app/preferences", dependencies=[Depends(cookie_required)])
async def get_preferences(request: Request):
    """
    Mimics qBittorrent endpoint /api/v2/app/preferences
    Returns static preferences to allow apps to connect properly
    """
    log.debug(f"[API] Preferences requested ({request.headers.get("user-agent")})")

    preferences = {
        "save_path": DOWNLOADS_DIR,  # static directory to be mounted by user configuration
        "max_ratio_enabled": False,  # apps require this to be off most of the time
        "max_ratio": 1.0,  # as long it's at least 1, apps will accept this
        "max_seeding_time_enabled": False,  # apps require this to be off most of the time
        "max_seeding_time": 0,  # just send nothing here
        "max_ratio_act": 0,  # this is qBittorrent's "Pause" action once max ratio has been met
        "queueing_enabled": False,  # built-in client doesn't use queues
        "dht": False,  # don't show that DHT is enabled
    }

    return JSONResponse(preferences)


@router.get("/torrents/info", dependencies=[Depends(cookie_required)])
async def get_torrent_info(request: Request, category: str = Query(None)):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/info
    """
    log.debug(f"[API] Torrent list requested{f" (category: {category})" if category else ""} ({request.headers.get("user-agent")})")

    try:
        mapped = await qbit_translator.map_torrents_to_qbit(torrent_client.get_all_torrents(), category_filter=category)

        return JSONResponse(mapped)
    except Exception as e:
        log.error(f"[API] Exception while getting torrent status: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/sync/maindata")
async def get_main_data(request: Request):
    """
    Mimics qBittorrent endpoint /api/v2/sync/maindata
    """
    log.debug(f"[API] Main data requested ({request.headers.get("user-agent")})")

    main_data = {}

    try:
        mapped = await qbit_translator.map_torrents_to_qbit(torrent_client.get_all_torrents())

        main_data["torrents"] = mapped
    except Exception as e:
        log.error(f"[API] Exception while getting torrent list: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    try:
        stats_now, time_now, stats_prev, time_prev = torrent_client.get_session_stats()
        all_time_download, all_time_upload = torrent_client.get_all_time_stats()

        main_data["server_state"] = qbit_translator.map_stats_to_qbit(stats_now, time_now, stats_prev, time_prev, all_time_download, all_time_upload)
    except Exception as e:
        log.error(f"[API] Exception while getting session info: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse(main_data)


@router.get("/torrents/categories", dependencies=[Depends(cookie_required)])
async def get_categories(request: Request):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/categories
    Returns a dict of the categories in config file
    """
    log.debug(f"[API] Categories requested ({request.headers.get("user-agent")})")

    categories = qbit_translator.get_torrent_categories()

    return JSONResponse(categories)


@router.post("/torrents/createCategory", dependencies=[Depends(cookie_required)])
async def create_category(request: Request, category: str = Form()):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/createCategory
    Adds a new category to the config file if it doesn't exist
    """
    log.debug(f"[API] New category requested ({request.headers.get("user-agent")})")

    # don't store duplicate categories
    if category in qbit_translator.get_torrent_categories().keys():
        log.warning(f"[API] Refusing to create duplicate category: {category}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    save_dir = os.path.join(DOWNLOADS_DIR, category)

    # try to create the category on the client, this includes updating the config and making a directory in the DOWNLOAD_DIR
    try:
        qbit_translator.add_torrent_category(category, save_dir)
    except Exception as e:
        log.error(f"[API] Exception while creating category directory {save_dir}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    log.info(f"[API] Created new category '{category}' at {save_dir} ({request.headers.get("user-agent")})")

    return PlainTextResponse("Ok.")


@router.post("/torrents/add", dependencies=[Depends(cookie_required)])
async def add_torrent(
        request: Request,
        torrents: UploadFile = File(None),
        urls: str = Form(None),
        category: str = Form(None),
):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/add
    Accepts a file upload or a URL and adds it to the torrent client
    Also accepts an optional form parameter query category
    The client will try to put the download in the directory for that category
    qBittorrent technically allows multiple URLs or files, but we don't
    Only torrents created from PrivateIndexer are allowed
    """
    log.debug(f"[API] New torrent requested ({request.headers.get("user-agent")})")

    if not torrents and not urls:
        log.warning(f"[API] No file upload or URL provided ({request.headers.get("user-agent")})")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    if category:
        # ensure category exists before storing data in it
        if category not in qbit_translator.get_torrent_categories().keys():
            log.warning(f"[API] Refusing to add torrent, category doesn't exist: {category}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

        save_dir = os.path.join(DOWNLOADS_DIR, category)

        # make sure the directory exists or create it
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)
    else:
        save_dir = DOWNLOADS_DIR

    torrent_file = None
    # save torrent data to a temporary file
    if torrents:
        filename = torrents.filename
        log.debug(f"[API] Saving torrent file '{filename}' to temp directory")
        # write the file stream to temporary file
        try:
            contents = await torrents.read()
            torrent_file = os.path.join(tempfile.gettempdir(), filename)
            with open(torrent_file, "wb") as f:
                f.write(contents)
        except Exception as e:
            log.error(f"[API] Exception while saving torrent '{filename}': {e}")
            raise HTTPException(status_code=status.INTERNAL_SERVER_ERROR)
    else:
        # we only allow a single URL to be added at a time
        torrent_url = urls.split(",")[0] if "," in urls else urls

        # validate torrent URL
        torrent_url = torrent_helper.validate_torrent_url(torrent_url)
        if torrent_url is None:
            log.critical(f"[API] URL is invalid, refusing to download: {torrent_url}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

        # download torrent from URL
        try:
            async with httpx_request.get_client() as client:
                response = await client.get(torrent_url)
                if response.status_code != 200:
                    log.critical(f"[API] Failed to download new torrent file ({torrent_url}): {response.status_code} - {response.text}")
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
                torrent_file = os.path.join(tempfile.gettempdir(), os.path.basename(torrent_url))
                with open(torrent_file, "wb") as f:
                    f.write(response.content)
        except Exception as e:
            log.error(f"[API] Exception while downloading URL '{torrent_url}': {e}")
            raise HTTPException(status_code=status.INTERNAL_SERVER_ERROR)

    log.debug(f"[API] Validating torrent: {torrent_file}")
    info = lt.torrent_info(torrent_file)

    # make sure the torrent we're trying to download has v2 infohash
    hashes = info.info_hashes()
    if not hashes.has_v2():
        log.warning(f"[API] Refusing to keep torrent, no v2 hash (not from PrivateIndexer?): {torrent_file}")
        try:
            if os.path.exists(torrent_file):
                os.unlink(torrent_file)
        except Exception as e:
            log.error(f"[API] Exception while removing torrent file '{torrent_file}': {e}")
            raise HTTPException(status_code=status.INTERNAL_SERVER_ERROR)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    torrent_hash = str(hashes.v2)

    # check with the server to validate that this is a torrent from PrivateIndexer
    async with httpx_request.get_client() as client:
        response = await client.get(f"{INDEXER_API_URL}/validate?infohash={torrent_hash}", headers={"X-API-Key": API_KEY})
        # based on the response from API, we will know if torrent exists on server
        if response.status_code == 200:
            # this means the torrent exists in the server database
            pass
        elif response.status_code == 404:
            log.warning(f"[API] Refusing to keep torrent, hash not found on PrivateIndexer server: {torrent_hash}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
        else:
            log.critical(f"[API] Unknown error code occurred when validating hash '{torrent_hash}' with indexer: {response.status_code} - {response.text}")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    # attempt to add the torrent to the download client and match qBittorrent return text
    result = "Ok." if await torrent_client.add_torrent_for_download(torrent_file, save_dir) else "Fails."

    # remove the temporary file if it still exists
    if os.path.exists(torrent_file):
        try:
            os.unlink(torrent_file)
        except Exception as e:
            log.error(f"[API] Exception while removing torrent file '{torrent_file}': {e}")
            raise HTTPException(status_code=status.INTERNAL_SERVER_ERROR)

    return PlainTextResponse(result)


@router.post("/torrents/delete", dependencies=[Depends(cookie_required)])
async def delete_torrent(
        request: Request,
        hashes: str = Form(),
        deleteFiles: bool = Form(False)
):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/delete
    Accepts a string of torrent hashes, separated by a | for multiple hashes
    Also accepts an optional deleteFiles parameter to remove downloaded torrent data
    """
    log.debug(f"[API] Torrent removal requested ({request.headers.get("user-agent")})")

    split_hashes = hashes.split("|")

    failures = 0

    for torrent_hash in split_hashes:
        # query database for torrent info
        result = await database.fetch_one("SELECT infohash, torrent_path FROM torrents WHERE infohash = ?", (torrent_hash,))

        if not result:
            log.warning(f"[API] Torrent hash not found during delete request: {torrent_hash}")
            failures += 1
            continue

        torrent_path = result["torrent_path"]
        infohash = result["infohash"]

        # remove from torrent client
        if not await torrent_client.remove_torrent_by_hash(infohash, deleteFiles):
            log.critical(f"[API] Failed to remove torrent with hash '{torrent_hash}' from torrent client")
            failures += 1

        try:
            # remove from database and delete torrent file
            await torrent_helper.remove_torrent_from_database(infohash, torrent_file=torrent_path)
        except Exception as e:
            log.error(f"[API] Exception while deleting torrent file with hash '{torrent_hash}': {e}")
            failures += 1
            continue

    return "Ok." if failures == 0 else "Fails."
