from fastapi import Request, APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from privateindexer_client.core import torrent_client, utils

router = APIRouter()
templates = Jinja2Templates(directory="/app/src/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    log.debug("[GUI] Dashboard loaded")
    return templates.TemplateResponse("dashboard.html", context={"request": request})


@router.get("/dashboard/torrents")
async def dashboard_torrents():
    log.debug("[GUI] Torrent data fetched")
    torrents = torrent_client.get_all_torrents()
    if not torrents:
        return JSONResponse({"error": "Failed to get torrents"}, status_code=500)
    return [utils.map_torrent_to_qbit(torrent) for torrent in torrents]


@router.get("/dashboard/user")
async def dashboard_user_stats():
    log.debug("[GUI] User statistics fetched")
    user_data = await utils.fetch_indexer_user_data()
    if not user_data:
        return JSONResponse({"error": "Failed to fetch user stats"}, status_code=500)
    return user_data
