from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import RADARR_URL, RADARR_API_KEY
from privateindexer_client.core.logger import log


async def test_connection():
    """
    Tests connection to Radarr API
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(RADARR_URL + "/api", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)

            if response.status_code == 200:
                log.info(f"[RADARR] Connected to Radarr")
            else:
                log.warning(f"[RADARR] Failed to connect to Radarr: {response.status_code}")
    except Exception as e:
        log.error(f"[RADARR] Exception while testing Radarr connection: {e}")


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Radarr is configured to monitor
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(RADARR_URL + "/api/v3/rootfolder", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)
            response.raise_for_status()
            folder_response = response.json()
            log.debug(f"[RADARR] Fetched root folders ({len(folder_response)} directories)")
            return [root_folder.get("path") for root_folder in folder_response]
    except Exception as e:
        log.error(f"[RADARR] Failed to fetch root folders: {e}")
        return []


async def fetch_movie_library() -> list[dict]:
    """
    Fetches the list of movies currently being tracked by Radarr
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(RADARR_URL + "/api/v3/movie", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)
            response.raise_for_status()
            movie_response = response.json()
            log.debug(f"[RADARR] Fetched movie library ({len(movie_response)} movies)")
            return movie_response
    except Exception as e:
        log.error(f"[RADARR] Failed to fetch movie library: {e}")
        return []
