import asyncio
import os

from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import SONARR_URL, SONARR_API_KEY
from privateindexer_client.core.logger import log


async def test_connection():
    """
    Tests connection to Sonarr API
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{SONARR_URL}/api", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)

            if response.status_code == 200:
                log.info(f"[SONARR] Connected to Sonarr")
            else:
                log.warning(f"[SONARR] Failed to connect to Sonarr: {response.status_code}")
    except Exception as e:
        log.error(f"[SONARR] Exception while testing Sonarr connection: {e}")


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Sonarr is configured to monitor
    Updates the torznab category list with valid directories
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{SONARR_URL}/api/v3/rootfolder", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[SONARR] Failed to fetch root folders: {response.status_code}")
                return []

            root_folders = response.json()
            log.debug(f"[SONARR] Fetched root folders ({len(root_folders)} directories)")

            tracked_root_folders = []
            # check each root folder for access and add to tracked paths
            for root_folder_entry in root_folders:
                root_folder_path = root_folder_entry["path"]
                # skip if we can't access this directory
                if not os.path.exists(root_folder_path):
                    log.warning(f"[SONARR] Unable to access root folder: {root_folder_path}")
                    continue

                tracked_root_folders.append(root_folder_path)
                log.debug(f"[SONARR] Tracking Sonarr path: {root_folder_path}")

            return tracked_root_folders
    except Exception as e:
        log.error(f"[SONARR] Failed to fetch root folders: {e}")
        return []


async def fetch_tv_library(tracked_root_folders: list[str]) -> list[dict]:
    """
    Fetches the list of TV series tracked by Sonarr
    Attaches episode files to their respective seasons within each series dict
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{SONARR_URL}/api/v3/series", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[SONARR] Failed to fetch TV library: {response.status_code}")
                return []

            series_list = response.json()

            # asynchronously fetch episodes for the series, only if they are located in our tracked root folders
            tasks = [fetch_series_episodes(series["id"]) for series in series_list if series.get("rootFolderPath") in tracked_root_folders]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # build a list of just episode data
            all_episode_files = [episode for res in results for episode in res]

            log.debug(f"[SONARR] Fetched TV library ({len(series_list)} series, {len(all_episode_files)} episodes)")

            return all_episode_files
    except Exception as e:
        log.error(f"[SONARR] Exception while fetching TV library: {e}")
        return []


async def fetch_series_episodes(series_id: str) -> list[dict]:
    """
    Fetches the episode files for the given series ID
    """
    try:
        async with httpx_request.get_client() as client:
            params = {"seriesID": series_id, }
            response = await client.get(f"{SONARR_URL}/api/v3/episodeFile", headers={"X-API-Key": SONARR_API_KEY}, params=params, timeout=30)

            if response.status_code != 200:
                log.warning(f"[SONARR] Failed to fetch episode files: {response.status_code}")
                return []

            episode_response = response.json()
            log.debug(f"[SONARR] Fetched episode files for series ID {series_id} ({len(episode_response)} episodes)")
            return episode_response
    except Exception as e:
        log.error(f"[SONARR] Exception while fetching episode files: {e}")
        return []
