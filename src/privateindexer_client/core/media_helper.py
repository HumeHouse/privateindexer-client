import enum
import os.path

from privateindexer_client.core import logger
from privateindexer_client.core import radarr, sonarr, lidarr
from privateindexer_client.core.config import RADARR_URL, SONARR_URL, LIDARR_URL

_torznab_category_paths: list[dict[str, str]] = []
RADARR_ROOT_CATEGORY = 2000
LIDARR_ROOT_CATEGORY = 3000
SONARR_ROOT_CATEGORY = 5000


class MediaType(enum.Enum):
    RADARR_MOVIE = 1
    SONARR_EPISODE = 2
    SONARR_SEASON = 3
    LIDARR_TRACK = 4
    LIDARR_ALBUM = 5


class MediaDataEntry:
    def __init__(self, media_type: MediaType):
        self.media_type: MediaType = media_type
        self.app_id: int = None
        self.title: str = None
        self.files: list[str] = None
        self.torznab_category: int = None


def detect_torznab_category(file_path: str) -> int:
    """
    Tries to match the file's path with the known torznab category directories and returns its ID
    Returns 0 for invalid category
    """
    for cat_info in _torznab_category_paths:
        category_path = cat_info["path"]
        if os.path.commonpath([file_path, category_path]) == category_path:
            return cat_info["id"]
    return 0


async def update_torznab_category_paths() -> set[dict[str, str]]:
    """
    Fetches root folders from *arr apps and updates the tracked paths with valid directories
    """
    global _torznab_category_paths
    _torznab_category_paths = []

    if RADARR_URL:
        radarr_root_folders = await radarr.fetch_root_folders()

        # add the root paths to tracking
        for radarr_root_folder in radarr_root_folders:
            _torznab_category_paths.append({"id": RADARR_ROOT_CATEGORY, "path": radarr_root_folder})

    if SONARR_URL:
        sonarr_root_folders = await sonarr.fetch_root_folders()

        # add the root paths to tracking
        for sonarr_root_folder in sonarr_root_folders:
            _torznab_category_paths.append({"id": SONARR_ROOT_CATEGORY, "path": sonarr_root_folder})

    if LIDARR_URL:
        lidarr_root_folders = await lidarr.fetch_root_folders()

        # add the root paths to tracking
        for lidarr_root_folder in lidarr_root_folders:
            _torznab_category_paths.append({"id": LIDARR_ROOT_CATEGORY, "path": lidarr_root_folder})

    return _torznab_category_paths


async def get_managed_media_data() -> list[MediaDataEntry]:
    """
    Returns list of data dicts for all tracked media
    Includes app ID (*arr apps) and optionally a title
    """
    # check if we have category paths available, otherwise update from apps
    if not _torznab_category_paths:
        await update_torznab_category_paths()

    tracked_root_folders = [cat_info.get("path") for cat_info in _torznab_category_paths]
    media_data = []

    try:
        # fetch all movie entries from Radarr if configured
        if RADARR_URL:
            radarr_entries = await radarr.fetch_movie_library(tracked_root_folders)
            for radarr_entry in radarr_entries:
                media_entry = MediaDataEntry(MediaType.RADARR_MOVIE)
                media_entry.app_id = radarr_entry["id"]
                media_entry.files = radarr_entry["files"]
                media_entry.title = radarr_entry["title"]
                media_entry.torznab_category = RADARR_ROOT_CATEGORY

                media_data.append(media_entry)
    except Exception as e:
        logger.channel("radarr").exception(f"Exception while gathering media data: {e}")

    try:
        # fetch all TV entries from Sonarr if configured
        if SONARR_URL:
            sonarr_entries = await sonarr.fetch_tv_library(tracked_root_folders)
            for sonarr_entry in sonarr_entries:

                # build a season entry for season packs
                if sonarr_entry["season_pack"]:
                    media_entry = MediaDataEntry(MediaType.SONARR_SEASON)

                # build single episode entries for individual episodes
                else:
                    media_entry = MediaDataEntry(MediaType.SONARR_EPISODE)

                media_entry.app_id = sonarr_entry["id"]
                media_entry.files = sonarr_entry["files"]
                media_entry.title = sonarr_entry["title"]
                media_entry.torznab_category = SONARR_ROOT_CATEGORY

                media_data.append(media_entry)
    except Exception as e:
        logger.channel("sonarr").exception(f"Exception while gathering media data: {e}")

    try:
        # fetch all music entries from Lidarr if configured
        if LIDARR_URL:
            lidarr_entries = await lidarr.fetch_music_library(tracked_root_folders)
            for lidarr_entry in lidarr_entries:

                # build an album entry for full albums
                if lidarr_entry["album"]:
                    media_entry = MediaDataEntry(MediaType.LIDARR_ALBUM)

                # build single track entries for individual tracks
                else:
                    media_entry = MediaDataEntry(MediaType.LIDARR_TRACK)

                media_entry.app_id = lidarr_entry["id"]
                media_entry.files = lidarr_entry["files"]
                media_entry.title = lidarr_entry["title"]
                media_entry.torznab_category = LIDARR_ROOT_CATEGORY

                media_data.append(media_entry)
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while gathering media data: {e}")

    return media_data
