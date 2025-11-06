import os

from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import RADARR_URL, RADARR_API_KEY
from privateindexer_client.core.logger import log


async def test_connection():
    """
    Tests connection to Radarr API
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{RADARR_URL}/api", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)

            if response.status_code == 200:
                log.info(f"[RADARR] Connected to Radarr")
            else:
                log.warning(f"[RADARR] Failed to connect to Radarr: {response.status_code}")
    except Exception as e:
        log.error(f"[RADARR] Exception while testing Radarr connection: {e}")


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Radarr is configured to monitor
    Updates the torznab category list with valid directories
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{RADARR_URL}/api/v3/rootfolder", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[RADARR] Failed to fetch root folders: {response.status_code}")
                return []

            root_folders = response.json()
            log.debug(f"[RADARR] Fetched root folders ({len(root_folders)} directories)")

            tracked_root_folders = []
            # check each root folder for access and add to tracked paths
            for root_folder_entry in root_folders:
                root_folder_path = root_folder_entry["path"]
                # skip if we can't access this directory
                if not os.path.exists(root_folder_path):
                    log.warning(f"[RADARR] Unable to access root folder: {root_folder_path}")
                    continue

                tracked_root_folders.append(root_folder_path)
                log.debug(f"[RADARR] Tracking Radarr path: {root_folder_path}")

            return tracked_root_folders
    except Exception as e:
        log.error(f"[RADARR] Exception while fetching root folders: {e}")
        return []


async def fetch_movie_library(tracked_root_folders: list[str]) -> list[dict]:
    """
    Fetches the list of movies currently being tracked by Radarr
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{RADARR_URL}/api/v3/movie", headers={"X-API-Key": RADARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[RADARR] Failed to fetch movie library: {response.status_code}")
                return []

            movie_response = response.json()

            # build list of movie data for movies which are located in our tracked root folders
            all_movies = [movie for movie in movie_response if movie.get("rootFolderPath") in tracked_root_folders]

            log.debug(f"[RADARR] Fetched movie library ({len(all_movies)} movies)")

            return all_movies
    except Exception as e:
        log.error(f"[RADARR] Exception while fetching movie library: {e}")
        return []
