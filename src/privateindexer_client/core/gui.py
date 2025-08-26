from fastapi import Request, APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from privateindexer_client.core import torrent_client, utils

router = APIRouter()
templates = Jinja2Templates(directory="/app/src/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", context={"request": request})


@router.get("/dashboard/torrents")
async def dashboard_torrents():
    torrents = torrent_client.get_all_torrents()
    return [utils.map_torrent_to_qbit(torrent) for torrent in torrents]
