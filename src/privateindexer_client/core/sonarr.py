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
        series_in_scope = [series for series in series_list if series["rootFolderPath"] in tracked_root_folders]
        tasks = [fetch_series_episodes(series["id"]) for series in series_in_scope]
        episodes_results = await asyncio.gather(*tasks, return_exceptions=True)

        # create a map that associates episodes with their series ID
        series_episodes_map = {series_in_scope[i]["id"]: episodes_results[i] for i in range(len(series_in_scope)) if not isinstance(episodes_results[i], Exception)}

        final_entries = []

        # loop through each series to build a list of entries
        for series in series_list:
            series_id = series["id"]
            series_episodes = series_episodes_map.get(series_id, [])

            # skip if no episodes are found
            if not series_episodes:
                continue

            # create a map for season stats
            season_stats = {season["seasonNumber"]: season["statistics"] for season in series["seasons"]}

            # group episodes by season number
            seasons = {}
            for episode in series_episodes:
                season_number = episode["seasonNumber"]
                seasons.setdefault(season_number, []).append(episode)

            # work through each season
            for season_number, season_episodes in seasons.items():
                # get the percent of episodes for season that are currently tracked on disk
                percent_of_episodes = season_stats[season_number]["percentOfEpisodes"]
                missing_episode_count = season_stats[season_number]["totalEpisodeCount"] - season_stats[season_number]["episodeFileCount"]

                # build season pack for full & ended seasons
                if percent_of_episodes == 100 and missing_episode_count == 0:
                    # TODO: figure out a way to add better quality tags to the title - right now we're just going to use the first episode's quality data and some metadata
                    sample_episode_data = season_episodes[0]
                    quality_name = sample_episode_data["quality"]["quality"]["name"]
                    release_group = f"-{sample_episode_data["releaseGroup"]}" if sample_episode_data.get("releaseGroup") else ""
                    media_info = sample_episode_data["mediaInfo"]
                    hdr_info = f"[{media_info["videoDynamicRangeType"]}]" if media_info.get("videoDynamicRangeType") else ""
                    quality_tags = f"[{quality_name}][{media_info["videoBitDepth"]}bit]{hdr_info}[{media_info["videoCodec"]}]({media_info["audioCodec"]} {media_info["audioChannels"]})"
                    title = f"{series["title"]} ({series["year"]}) - S{str(season_number).zfill(2)} {quality_tags}{release_group}"

                    # starting with the first episode, compare all episode paths to ensure they share a single directory
                    path = os.path.dirname(sample_episode_data["path"])
                    for episode in season_episodes:
                        if os.path.dirname(episode["path"]) != path:
                            log.warning(f"[SONARR] Episodes from '{series["title"]} Season {season_number} do not share a single parent directory, it will be skipped.")
                            path = None

                    # skip the season if episodes do not share a single directory
                    if not path:
                        continue

                    final_entries.append({"id": series_id, "title": title, "path": path, "season_pack": True, })

                else:
                    # if there are missing episodes, just build each episode one at a time
                    for season_episode in season_episodes:
                        final_entries.append({"id": series_id, "path": season_episode["path"], "season_pack": False, })

        log.debug(f"[SONARR] Fetched TV library ({len(final_entries)} series, {len(episodes_results)} episodes)")

        return final_entries
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


async def fetch_series_metadata(series_id: str) -> dict:
    """
    Fetches the metadata for the given series ID
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{SONARR_URL}/api/v3/series/{series_id}", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[SONARR] Failed to fetch series metadata: {response.status_code}")
                return []

            series_response = response.json()
            log.debug(f"[SONARR] Fetched metadata for series ID {series_id}")
            return series_response
    except Exception as e:
        log.error(f"[SONARR] Exception while fetching series metadata: {e}")
        return []
