import asyncio
import os
from collections import defaultdict

from privateindexer_client.core import httpx_request, arr_formatter, utils
from privateindexer_client.core.arr_formatter import VIDEO_EXTRACTORS
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
                log.critical(f"[SONARR] Failed to connect to Sonarr: {response.status_code} - {response.text}")
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
                log.critical(f"[SONARR] Failed to fetch root folders: {response.status_code} - {response.text}")
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
        log.error(f"[SONARR] Exception while fetching root folders: {e}")
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
                log.critical(f"[SONARR] Failed to fetch TV library: {response.status_code} - {response.text}")
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
        for series in series_in_scope:
            series_id = series["id"]
            series_episodes = series_episodes_map.get(series_id, [])

            # skip if no episodes are found
            if not series_episodes:
                continue

            # create a map for season stats
            season_stats = {season["seasonNumber"]: season["statistics"] for season in series["seasons"]}

            # group episodes by season number
            seasons = defaultdict(list)
            for episode in series_episodes:
                season_number = episode["seasonNumber"]
                seasons[season_number].append(episode)

            # work through each season
            for season_number, season_episodes in seasons.items():
                current_season_stats = season_stats[season_number]
                # get the percent of episodes for season that are currently tracked on disk
                percent_of_episodes = current_season_stats["percentOfEpisodes"]
                missing_episode_count = current_season_stats["totalEpisodeCount"] - current_season_stats["episodeFileCount"]

                episode_paths = [episode["path"] for episode in season_episodes]

                # check for any invalid files
                episodes_valid = all(utils.valid_file(episode_path) for episode_path in episode_paths)

                # do not build season pack if any episodes are invalid
                if not episodes_valid:
                    log.warning(f"[SONARR] Detected invalid files for '{series["title"]}' (Season {season_number}), season pack will NOT be created")
                    continue

                # add all the episode paths to a set to ensure non are unique
                shared_directory = len({os.path.dirname(path) for path in episode_paths}) == 1

                # do not build season pack if episodes do not share a single directory
                if not shared_directory:
                    log.warning(f"[SONARR] Inconsistent parent directory for '{series["title"]}' (Season {season_number}), season pack will NOT be created")

                # build season pack for full seasons which share a single directory and all files are valid
                if percent_of_episodes == 100 and missing_episode_count == 0 and shared_directory and episodes_valid:
                    aggregated_metadata = arr_formatter.aggregate_metadata(season_episodes, app_name="SONARR", extractors=VIDEO_EXTRACTORS, )
                    metadata_tags = arr_formatter.format_tags(aggregated_metadata)
                    title = f"{series["title"]} ({series["year"]}) - S{str(season_number).zfill(2)} {metadata_tags}"
                    log.debug(f"[SONARR] Grouped season pack ({len(season_episodes)} episodes): {title}")

                    final_entries.append({"id": series_id, "title": title, "files": episode_paths, "season_pack": True, })

                else:
                    # if there are missing episodes or non-shared directory, just build each episode one at a time
                    for season_episode in season_episodes:
                        # skip if no file is tracked
                        episode_path = season_episode.get("path")
                        if not episode_path:
                            continue

                        # skip invalid files
                        if not utils.valid_file(episode_path):
                            log.warning(f"[SONARR] Invalid file path discovered: {episode_path}")
                            continue

                        episode_number = season_episode["episodeNumber"]
                        episode_title = season_episode["title"]
                        aggregated_metadata = arr_formatter.aggregate_metadata([season_episode], app_name="SONARR", extractors=VIDEO_EXTRACTORS, )
                        metadata_tags = arr_formatter.format_tags(aggregated_metadata)
                        title = f"{series["title"]} ({series["year"]}) - S{str(season_number).zfill(2)}E{str(episode_number).zfill(2)} {episode_title} {metadata_tags}"

                        log.debug(f"[SONARR] Found individual episode: {title}")
                        final_entries.append({"id": series_id, "title": title, "files": [episode_path], "season_pack": False, })

        season_packs = 0
        individual_episodes = 0
        for final_entry in final_entries:
            if final_entry["season_pack"]:
                season_packs += 1
            else:
                individual_episodes += 1

        log.debug(f"[SONARR] Fetched TV library ({season_packs} season packs, {individual_episodes} individual episodes)")

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
            episode_file_response = await client.get(f"{SONARR_URL}/api/v3/episodeFile", headers={"X-API-Key": SONARR_API_KEY}, params=params, timeout=30, )

            if episode_file_response.status_code != 200:
                log.critical(f"[SONARR] Failed to fetch episode files: {episode_file_response.status_code}")
                return []

            episode_files = episode_file_response.json()

            episode_response = await client.get(f"{SONARR_URL}/api/v3/episode", headers={"X-API-Key": SONARR_API_KEY}, params=params, timeout=30, )

            if episode_response.status_code != 200:
                log.critical(f"[SONARR] Failed to fetch episodes data: {episode_response.status_code}")
                return []

            episodes = episode_response.json()

        # merge episodes with episode files based on episodeFileId
        files_by_id = {f["id"]: f for f in episode_files}
        merged_response = []
        for episode in episodes:
            if not episode["hasFile"]:
                continue
            episode_file_id = episode.get("episodeFileId")
            merged_episode = {**episode}
            merged_episode.update(files_by_id[episode_file_id])
            merged_response.append(merged_episode)

        log.debug(f"[SONARR] Fetched episodes for series ID {series_id} ({len(merged_response)} episodes)")

        return merged_response
    except Exception as e:
        log.error(f"[SONARR] Exception while fetching episode files for series ID {series_id}: {e}")
        return []


async def fetch_series_metadata(series_id: str) -> dict:
    """
    Fetches the metadata for the given series ID
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{SONARR_URL}/api/v3/series/{series_id}", headers={"X-API-Key": SONARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.critical(f"[SONARR] Failed to fetch series metadata for series ID {series_id}: {response.status_code} - {response.text}")
                return []

            series_response = response.json()
            log.debug(f"[SONARR] Fetched metadata for series ID {series_id}")
            return series_response
    except Exception as e:
        log.error(f"[SONARR] Exception while fetching series metadata for series ID {series_id}: {e}")
        return []
