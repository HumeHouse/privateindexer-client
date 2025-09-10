import asyncio

from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import SONARR_URL, SONARR_API_KEY
from privateindexer_client.core.logger import log


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Sonarr is configured to monitor
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(SONARR_URL + "/api/v3/rootfolder", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)
            response.raise_for_status()
            folder_response = response.json()
            log.debug(f"[SONARR] Fetched root folders ({len(folder_response)} directories)")
            return [root_folder.get("path") for root_folder in folder_response]
    except Exception as e:
        log.error(f"[SONARR] Failed to fetch root folders: {e}")
        return []


async def fetch_tv_library() -> list[dict]:
    """
    Fetches the list of TV series tracked by Sonarr
    Attaches episode files to their respective seasons within each series dict
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(SONARR_URL + "/api/v3/series", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)
            response.raise_for_status()
            series_list = response.json()

            # asynchronously fetch episodes for the series
            tasks = [fetch_series_episodes(series["id"]) for series in series_list]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # build a list of just episode data
            all_episode_files = [episode for res in results for episode in res]

            log.debug(f"[SONARR] Fetched TV library ({len(series_list)} series, {len(all_episode_files)} episodes)")

            return all_episode_files
    except Exception as e:
        log.error(f"[SONARR] Failed to fetch TV library: {e}")
        return []


async def fetch_series_episodes(series_id: str) -> list[dict]:
    """
    Fetches the episode files for the given series ID
    """
    try:
        async with httpx_request.get_client() as client:
            params = {"seriesID": series_id, }
            response = await client.get(SONARR_URL + "/api/v3/episodeFile", headers={"X-API-Key": SONARR_API_KEY}, params=params, timeout=30)
            response.raise_for_status()
            episode_response = response.json()
            log.debug(f"[SONARR] Fetched episode files for series ID {series_id} ({len(episode_response)} episodes)")
            return episode_response
    except Exception as e:
        log.error(f"[SONARR] Failed to fetch episode files: {e}")
        return []
