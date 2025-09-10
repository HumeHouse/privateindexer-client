from fastapi import Request, APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from privateindexer_client.core import torrent_client, utils
from privateindexer_client.core.logger import log

router = APIRouter()
templates = Jinja2Templates(directory="/app/src/templates")


@router.get("/")
async def root():
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    log.debug("[GUI] Dashboard loaded")
    return templates.TemplateResponse(name="dashboard.html", request=request)


@router.get("/dashboard/maindata")
async def dashboard_maindata():
    log.debug("[GUI] Main data fetched")
    main_data = {}

    try:
        torrents = torrent_client.get_all_torrents()
        mapped = [utils.map_torrent_to_qbit(t) for t in torrents]

        main_data["torrents"] = mapped
    except Exception as e:
        log.error(f"[GUI] Failed to get torrent list: {e}")
        return HTTPException(status_code=500, detail="Failed to get torrent list")

    try:
        stats_now, time_now, stats_prev, time_prev = torrent_client.get_session_stats()
        all_time_download, all_time_upload = torrent_client.get_all_time_stats()

        main_data["server_state"] = utils.map_stats_to_qbit(stats_now, time_now, stats_prev, time_prev, all_time_download, all_time_upload)
    except Exception as e:
        log.error(f"[GUI] Failed to get session info: {e}")
        return HTTPException(status_code=500, detail="Failed to get session info")

    return main_data


@router.get("/dashboard/user")
async def dashboard_user_stats():
    log.debug("[GUI] User statistics fetched")
    user_data = await utils.fetch_indexer_user_data()
    if not user_data:
        # we don't log the error to console here because fetch_indexer_user_data() does for us
        return HTTPException(status_code=500, detail="Failed to fetch user stats")
    return user_data
