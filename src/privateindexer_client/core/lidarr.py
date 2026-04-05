import asyncio
import os
from collections import defaultdict
from datetime import datetime

from privateindexer_client.core import httpx_request, arr_formatter, utils
from privateindexer_client.core import logger
from privateindexer_client.core.arr_formatter import AUDIO_EXTRACTORS
from privateindexer_client.core.config import LIDARR_URL, LIDARR_API_KEY


async def test_connection():
    """
    Tests connection to Lidarr API
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code == 200:
                logger.channel("lidarr").info(f"Connected to Lidarr")
            else:
                logger.channel("lidarr").critical(f"Failed to connect to Lidarr: {response.status_code} - {response.text}")
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while testing Lidarr connection: {e}")


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Lidarr is configured to monitor
    Updates the torznab category list with valid directories
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/rootfolder", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                logger.channel("lidarr").critical(f"Failed to fetch root folders: {response.status_code} - {response.text}")
                return []

            root_folders = response.json()
            logger.channel("lidarr").debug(f"Fetched root folders ({len(root_folders)} directories)")

            tracked_root_folders = []
            # check each root folder for access and add to tracked paths
            for root_folder_entry in root_folders:
                root_folder_path = root_folder_entry["path"]
                # skip if we can't access this directory
                if not os.path.exists(root_folder_path):
                    logger.channel("lidarr").warning(f"Unable to access root folder: {root_folder_path}")
                    continue

                tracked_root_folders.append(root_folder_path)
                logger.channel("lidarr").debug(f"Tracking Lidarr path: {root_folder_path}")

            return tracked_root_folders
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while fetching root folders: {e}")
        return []


async def fetch_music_library(tracked_root_folders: list[str]) -> list[dict]:
    """
    Fetches the list of music tracks currently being tracked by Lidarr
    """
    try:
        # fetch all artists
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/artist", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                logger.channel("lidarr").critical(f"Failed to fetch artist list: {response.status_code} - {response.text}")
                return []

            artist_list = response.json()

        # asynchronously fetch albums for the artist, only if they are located in our tracked root folders
        artists_in_scope = [artist for artist in artist_list if artist["rootFolderPath"].rstrip("/") in tracked_root_folders]
        tasks = [fetch_artist_tracks(artist["id"]) for artist in artists_in_scope]
        track_results = await asyncio.gather(*tasks, return_exceptions=True)

        # create a map that associates tracks with their artist
        artist_track_map = defaultdict(list)
        for artist_tracks in track_results:
            for track in artist_tracks:
                artist_track_map[track["artistId"]].append(track)

        # fetch all albums
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/album", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                logger.channel("lidarr").critical(f"Failed to fetch album list: {response.status_code} - {response.text}")
                return []

            album_list = response.json()

        # key albums by their ID
        all_album_metadata = {album["id"]: album for album in album_list}

        final_entries = []

        # loop through each artist to build a list of entries
        for artist in artists_in_scope:
            artist_id = artist["id"]
            artist_tracks = artist_track_map.get(artist_id, [])

            # skip if no tracks are found
            if not artist_tracks:
                continue

            # group tracks by album
            albums = defaultdict(list)
            for track in artist_tracks:
                album_id = track["albumId"]
                albums[album_id].append(track)

            # work through each album
            for album_id, album_tracks in albums.items():
                album_metadata = all_album_metadata[album_id]
                album_stats = album_metadata["statistics"]

                # get the percent of tracks for album that are currently tracked on disk
                percent_of_tracks = album_stats["percentOfTracks"]
                missing_track_count = album_stats["totalTrackCount"] - album_stats["trackFileCount"]

                track_paths = [track["path"] for track in album_tracks]

                # check for any invalid files
                tracks_valid = all(utils.valid_file(track_path) for track_path in track_paths)

                # do not build album if any tracks are invalid
                if not tracks_valid:
                    logger.channel("lidarr").warning(f"Detected invalid files for '{artist["artistName"]} - {album_metadata["title"]}', album will NOT be created")
                    continue

                # add all the track parent directories to a set to ensure none are unique
                shared_directory = len({os.path.dirname(path) for path in track_paths}) == 1

                # do not build album if tracks do not share a single directory
                if not shared_directory:
                    logger.channel("lidarr").warning(f"Inconsistent parent directory for '{artist["artistName"]} - {album_metadata["title"]}', album will NOT be created")

                dt = datetime.fromisoformat(album_metadata["releaseDate"].replace("Z", "+00:00"))
                album_year = dt.year

                # build full albums which share a single directory and all files are valid
                if percent_of_tracks == 100 and missing_track_count == 0 and shared_directory and tracks_valid:
                    aggregated_metadata = arr_formatter.aggregate_metadata(album_tracks, app_name="LIDARR", extractors=AUDIO_EXTRACTORS, )
                    metadata_tags = arr_formatter.format_tags(aggregated_metadata)
                    title = f"{artist["artistName"]} - {album_metadata["title"]} ({album_year}) {metadata_tags}"

                    logger.channel("lidarr").debug(f"Grouped album ({len(album_tracks)} tracks): {title}")
                    final_entries.append({"id": album_id, "title": title, "files": track_paths, "album": True, })

                else:
                    # if there are missing tracks or non-shared directory, just build each track one at a time
                    for album_track in album_tracks:
                        # skip if no file is tracked
                        track_path = album_track.get("path")
                        if not track_path:
                            continue

                        # skip invalid files
                        if not utils.valid_file(track_path):
                            logger.channel("lidarr").warning(f"Invalid file path discovered: {track_path}")
                            continue

                        track_number = album_track["trackNumber"]
                        track_title = album_track["title"]
                        aggregated_metadata = arr_formatter.aggregate_metadata([album_track], app_name="LIDARR", extractors=AUDIO_EXTRACTORS, )
                        metadata_tags = arr_formatter.format_tags(aggregated_metadata)
                        title = f"{artist["artistName"]} - {album_metadata["title"]} ({album_year}) - {str(track_number).zfill(2)} {track_title} {metadata_tags}"

                        logger.channel("lidarr").debug(f"Found individual track: {title}")
                        final_entries.append({"id": album_id, "title": title, "files": [track_path], "album": False, })

        album_count = 0
        individual_tracks = 0
        for final_entry in final_entries:
            if final_entry["album"]:
                album_count += 1
            else:
                individual_tracks += 1

        logger.channel("lidarr").debug(f"Fetched music library ({album_count} albums, {individual_tracks} individual tracks)")

        return final_entries
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while fetching music library: {e}")
        return []


async def fetch_artist_tracks(artist_id: str) -> list[dict]:
    """
    Fetches the music tracks, including their files, for the given artist ID
    """
    try:
        async with httpx_request.get_client() as client:
            params = {"artistId": artist_id, }

            track_file_response = await client.get(f"{LIDARR_URL}/api/v1/trackfile", headers={"X-API-Key": LIDARR_API_KEY}, params=params, timeout=30)

            if track_file_response.status_code != 200:
                logger.channel("lidarr").critical(
                    f"Failed to fetch track files for artist ID {artist_id}: {track_file_response.status_code} - {track_file_response.text}")
                return []

            track_files = track_file_response.json()

            track_response = await client.get(f"{LIDARR_URL}/api/v1/track", headers={"X-API-Key": LIDARR_API_KEY}, params=params, timeout=30)

            if track_response.status_code != 200:
                logger.channel("lidarr").critical(f"Failed to fetch tracks data for artist ID {artist_id}: {track_response.status_code} - {track_response.text}")
                return []

            tracks = track_response.json()

        # merge the two respones together based on the track file ID
        files_by_id = {f["id"]: f for f in track_files}
        merged_response = []
        for track in tracks:
            if not track["hasFile"]:
                continue
            track_file_id = track.get("trackFileId")
            merged_track = {**track}
            merged_track.update(files_by_id[track_file_id])
            merged_response.append(merged_track)

        logger.channel("lidarr").debug(f"Fetched track files for artist ID {artist_id} ({len(merged_response)} tracks)")
        return merged_response
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while fetching track files for artist ID {artist_id}: {e}")
        return []


async def fetch_album_metadata(album_id: str) -> dict:
    """
    Fetches the metadata for the given album ID
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/album/{album_id}", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                logger.channel("lidarr").critical(f"Failed to fetch album metadata for album ID {album_id}: {response.status_code} - {response.text}")
                return []

            album_response = response.json()
            logger.channel("lidarr").debug(f"Fetched metadata for album ID {album_id}")
            return album_response
    except Exception as e:
        logger.channel("lidarr").exception(f"Exception while fetching album metadata for album ID {album_id}: {e}")
        return []
