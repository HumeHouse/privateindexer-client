import asyncio
import os

from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import SONARR_URL, SONARR_API_KEY
from privateindexer_client.core.logger import log


class AggregatedSeasonMetadata:
    def __init__(self):
        self.qualities: set = set()
        self.video_codecs: set = set()
        self.audio_codecs: set = set()
        self.audio_channels: set = set()
        self.bit_depths: set = set()
        self.hdr_types: set = set()
        self.release_groups: set = set()


def normalize_video_codec(codec: str | None) -> str | None:
    """
    Helper function to standardize the video codec
    """
    if not codec:
        return None

    codec = codec.strip().lower()

    if codec in {"x265", "h265", "hevc", "h.265"}:
        return "H265"

    if codec in {"x264", "h264", "avc", "h.264"}:
        return "H264"

    # fallback to just uppercase codec if no matches
    return codec.upper()


def normalize_audio_codec(codec: str | None) -> str | None:
    """
    Helper function to standardize the audio codec
    """
    if not codec:
        return None

    codec = codec.strip().lower()

    if codec in {"eac3", "ddp", "dd+", "dolby digital plus"}:
        return "DDP"
    if codec in {"dts", "dtshd"}:
        return "DTS"
    if codec in {"truehd"}:
        return "TrueHD"

    # fallback to just uppercase codec if no matches
    return codec.upper()


def aggregate_season_metadata(season_episodes: list[dict]) -> AggregatedSeasonMetadata:
    """
    Gathers info from each episode in a season and combines into a large aggregate object to be used for title formatting
    """
    aggregated = AggregatedSeasonMetadata()

    for episode in season_episodes:
        quality = (episode.get("quality", {}).get("quality", {}).get("name", "Unknown"))
        aggregated.qualities.add(quality)

        if episode.get("mediaInfo"):
            media_info = episode["mediaInfo"]

            video_codec = normalize_video_codec(media_info.get("videoCodec"))
            audio_codec = normalize_audio_codec(media_info.get("audioCodec"))

            aggregated.video_codecs.add(video_codec)
            aggregated.audio_codecs.add(audio_codec)
            aggregated.audio_channels.add(media_info.get("audioChannels"))
            aggregated.bit_depths.add(f"{media_info.get("videoBitDepth")}bit")
            aggregated.hdr_types.add(media_info.get("videoDynamicRangeType") or "")
        else:
            log.warning(f"[SONARR] Episode has no media info tracked by app: {episode.get("path", "Unknown path")}")

        if episode.get("releaseGroup") and len(episode["releaseGroup"].strip()) > 0:
            aggregated.release_groups.add(episode["releaseGroup"])

    return aggregated


def build_tags_from_metadata(aggregated: AggregatedSeasonMetadata) -> str:
    """
    Assembles a final string of metadata tags from an aggregate metadata object to be appended to season pack titles
    """

    def tag(values: list[str], wrap: str = "[]") -> str:
        # sort and get first 3 tags
        clean_values = sorted(v for v in values if v)

        # no tags → return empty
        if not clean_values:
            return ""

        left, right = wrap

        formatted = f"{left}{'+'.join(clean_values[:3])}{"++" if len(clean_values) > 3 else ""}{right}"

        return formatted

    tags = [tag(aggregated.qualities), tag(aggregated.video_codecs), tag({str(bit_depth) for bit_depth in aggregated.bit_depths})]

    if any(aggregated.hdr_types):
        tags.append(tag(aggregated.hdr_types))

    # add audio codecs and channels in parenthesis wrapper together
    audio_entries = set()

    for codec in sorted(v for v in aggregated.audio_codecs if v):
        for ch in sorted(v for v in aggregated.audio_channels if v):
            audio_entries.add(f"{codec} {ch:.1f}")

    if audio_entries:
        tags.append(tag(audio_entries, wrap="()"))

    # put release groups at the end with hyphen separator
    if aggregated.release_groups:
        # sort and get first 3 release groups
        release_groups = sorted(g for g in aggregated.release_groups if g)

        # append all the groups
        tags.append(f"-{"+".join(release_groups[:3])}")

        if len(release_groups) > 3:
            tags.append("++")

    return "".join(tags)


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

                # add all the episode paths to a set to ensure non are unique
                episode_paths = {os.path.dirname(episode["path"]) for episode in season_episodes}
                shared_directory = len(episode_paths) == 1

                # skip the season if episodes do not share a single directory
                if not shared_directory:
                    log.warning(f"[SONARR] Skipping season pack for '{series["title"]}' (Season {season_number}), must share a single parent directory")

                # build season pack for full seasons which share a single directory
                if percent_of_episodes == 100 and missing_episode_count == 0 and shared_directory:
                    aggregated_metadata = aggregate_season_metadata(season_episodes)
                    metadata_tags = build_tags_from_metadata(aggregated_metadata)
                    title = f"{series["title"]} ({series["year"]}) - S{str(season_number).zfill(2)} {metadata_tags}"
                    log.debug(f"[SONARR] Season pack ({len(season_episodes)} episodes) grouped with title: {title}")

                    final_entries.append({"id": series_id, "title": title, "path": episode_paths.pop(), "season_pack": True, })

                else:
                    # if there are missing episodes or non-shared directory, just build each episode one at a time
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
