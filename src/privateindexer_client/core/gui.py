from fastapi import Request, APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import PlainTextResponse
from starlette.status import HTTP_404_NOT_FOUND, HTTP_500_INTERNAL_SERVER_ERROR

from privateindexer_client.core import torrent_client, utils, scan, database
from privateindexer_client.core.config import APP_VERSION
from privateindexer_client.core.logger import log

router = APIRouter()
templates = Jinja2Templates(directory="/app/src/templates")


@router.get("/")
async def root():
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    log.debug("[GUI] Dashboard loaded")
    return templates.TemplateResponse(name="dashboard.html", context={"APP_VERSION": APP_VERSION}, request=request)


@router.get("/dashboard/maindata")
async def dashboard_maindata():
    log.debug("[GUI] Main data fetched")
    main_data = {}

    try:
        mapped = await utils.map_torrents_to_qbit(torrent_client.get_all_torrents())

        main_data["torrents"] = mapped
    except Exception as e:
        log.error(f"[GUI] Failed to get torrent list: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get torrent list")

    try:
        stats_now, time_now, stats_prev, time_prev = torrent_client.get_session_stats()
        all_time_download, all_time_upload = torrent_client.get_all_time_stats()

        main_data["server_state"] = utils.map_stats_to_qbit(stats_now, time_now, stats_prev, time_prev, all_time_download, all_time_upload)
    except Exception as e:
        log.error(f"[GUI] Failed to get session info: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get session info")

    try:
        main_data["scanner_status"] = {"state": scan.SCAN_PROCESS_STATE, "total_items": scan.SCAN_TOTAL_ITEMS, "done_items": scan.SCAN_DONE_ITEMS}
    except Exception as e:
        log.error(f"[GUI] Failed to get scanner info: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to get scanner info")

    return main_data


@router.get("/dashboard/user")
async def dashboard_user_stats():
    log.debug("[GUI] User statistics fetched")
    user_data = await utils.fetch_indexer_user_data()
    if not user_data:
        # we don't log the error to console here because fetch_indexer_user_data() does for us
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch user stats")
    return user_data


@router.post("/dashboard/delete_torrent")
async def delete_torrent(torrent_hash: str = Query(), remove_downloads: bool = Query()):
    log.debug("[GUI] GUI request to delete torrent")

    # gather the info about the torrent
    result = await database.fetch_one("SELECT infohash, torrent_path FROM torrents WHERE infohash = ?", (torrent_hash,))

    if not result:
        log.error(f"[GUI] Torrent hash not found: {torrent_hash}")
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="Torrent hash not found")

    torrent_path = result["torrent_path"]
    infohash = result["infohash"]

    # remove from torrent client
    if await torrent_client.remove_torrent_by_hash(infohash, remove_downloads):
        return PlainTextResponse("Successfully removed torrent")

    try:
        # remove from database and delete torrent file
        await utils.remove_torrent_from_database(infohash, torrent_file=torrent_path)
    except Exception as e:
        log.error(f"[GUI] Failed to delete torrent file: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete torrent file from disk")

    raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove torrent from client")
