import os
import tempfile
import time

import httpx
from fastapi import Depends, HTTPException, Query, File, Request, Form, APIRouter, status, UploadFile
from fastapi.responses import PlainTextResponse, JSONResponse

from privateindexer_client.core import config, torrent_client, utils
from privateindexer_client.core.config import API_KEY, DOWNLOADS_DIR
from privateindexer_client.core.logger import log

router = APIRouter(prefix="/api/v2")

SESSIONS = {}
SESSION_TTL = 3600  # 1 hour sessions


async def cookie_required(request: Request) -> Request:
    """
    Dependency for checking the cookie's value against the user's API key
    """
    sid = request.cookies.get("SID")
    if not sid or sid not in SESSIONS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    # check expiration of session
    if time.time() > SESSIONS[sid]:
        del SESSIONS[sid]
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return request


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
    if password != API_KEY:
        log.warning(f"[API] API login failed, invalid key used: {password} ({request.headers.get("user-agent")})")
        return PlainTextResponse("Fails.")

    sid = utils.generate_sid(API_KEY)
    SESSIONS[sid] = time.time() + SESSION_TTL

    log.info(f"[API] API login successful ({request.headers.get("user-agent")})")

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
        torrents = torrent_client.get_all_torrents()
        response = [utils.map_torrent_to_qbit(torrent) for torrent in torrents]
    except Exception as e:
        log.error(f"[API] Failed to get torrent status: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return JSONResponse(response)


@router.get("/torrents/categories", dependencies=[Depends(cookie_required)])
async def get_categories(request: Request):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/categories
    Returns a dict of the categories in config file
    """
    log.debug(f"[API] Categories requested ({request.headers.get("user-agent")})")

    config_data = await config.load_config_threadsafe()
    categories = config_data.get("categories", {})

    return JSONResponse(categories)


@router.post("/torrents/createCategory", dependencies=[Depends(cookie_required)])
async def create_category(request: Request, category: str = Form()):
    """
    Mimics qBittorrent endpoint /api/v2/torrents/createCategory
    Adds a new category to the config file if it doesn't exist
    """
    log.debug(f"[API] New category requested ({request.headers.get("user-agent")})")

    config_data = await config.load_config_threadsafe()
    categories = config_data.get("categories", {})

    # don't store duplicate categories
    if category in categories:
        log.warning(f"[API] Refusing to create duplicate category: {category}")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT)

    save_dir = os.path.join(DOWNLOADS_DIR, category)

    # try to make a directory in the downloads directory for this category and add it to the config file
    try:
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)

        # we have to store them like this per qBittorrent's format
        categories[category] = {
            "name": category,
            "savePath": save_dir
        }
        config_data["categories"] = categories

        await config.save_config_threadsafe(config_data)
    except Exception as e:
        log.error(f"[API] Failed to create category directory {save_dir}: {e}")
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
    Also accepts an optional form parameter query
    The client will try to put the download in the directory for that category
    qBittorrent technically allows multiple URLs or files, but we don't
    """
    log.info(f"[API] New torrent requested ({request.headers.get("user-agent")})")

    if not torrents and not urls:
        log.warning(f"[API] No file upload or URL provided ({request.headers.get("user-agent")})")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)

    if category:
        config_data = await config.load_config_threadsafe()
        categories = config_data.get("categories", {})

        # ensure category exists before storing data in it
        if category not in categories:
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
        # write the file stream
        contents = await torrents.read()
        torrent_file = os.path.join(tempfile.gettempdir(), torrents.filename)
        with open(torrent_file, "wb") as f:
            f.write(contents)
    else:
        # download torrent from URL
        async with httpx.AsyncClient() as client:
            response = await client.get(urls)
            if response.status_code != 200:
                log.error(f"[API] Failed to download new torrent file: {urls}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
            torrent_file = os.path.join(tempfile.gettempdir(), os.path.basename(urls))
            with open(torrent_file, "wb") as f:
                f.write(response.content)

    # attempt to add the torrent to the download client and match qBittorrent return text
    result = "Ok." if torrent_client.add_torrent_for_download(torrent_file, save_dir) else "Fails."

    # remove the temporary file
    os.unlink(torrent_file)

    return PlainTextResponse(result)
