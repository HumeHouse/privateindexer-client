import asyncio
import os
from collections import defaultdict

from privateindexer_client.core import httpx_request
from privateindexer_client.core.config import LIDARR_URL, LIDARR_API_KEY
from privateindexer_client.core.logger import log


class AggregatedTrackMetadata:
    def __init__(self):
        self.qualities: set = set()
        self.audio_codecs: set = set()
        self.audio_channels: set = set()
        self.bit_depths: set = set()
        self.release_groups: set = set()
        self.sample_rates: set = set()


def aggregate_album_metadata(album_tracks: list[dict]) -> AggregatedTrackMetadata:
    """
    Gathers info from each track in an album and combines into a large aggregate object to be used for title formatting
    """
    aggregated = AggregatedTrackMetadata()

    for track in album_tracks:
        quality = track.get("quality", {}).get("quality", {}).get("name", "Unknown")
        aggregated.qualities.add(quality)

        if track.get("mediaInfo"):
            media_info = track["mediaInfo"]

            aggregated.audio_codecs.add(media_info.get("audioCodec"))
            aggregated.audio_channels.add(media_info.get("audioChannels"))
            aggregated.bit_depths.add(media_info.get("audioBits"))
            aggregated.sample_rates.add(media_info.get("audioSampleRate"))
        else:
            log.warning(f"[LIDARR] Track has no media info tracked by app: {track.get("path", "Unknown path")}")

        if track.get("releaseGroup") and len(track["releaseGroup"].strip()) > 0:
            aggregated.release_groups.add(track["releaseGroup"])

    return aggregated


def build_tags_from_metadata(aggregated: AggregatedTrackMetadata) -> str:
    """
    Assembles a final string of metadata tags from an aggregate metadata object to be appended to album titles
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

    tags = [tag(aggregated.qualities), tag({str(bit_depth) for bit_depth in aggregated.bit_depths}), tag(aggregated.sample_rates)]

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
    Tests connection to Lidarr API
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code == 200:
                log.info(f"[LIDARR] Connected to Lidarr")
            else:
                log.warning(f"[LIDARR] Failed to connect to Lidarr: {response.status_code}")
    except Exception as e:
        log.error(f"[LIDARR] Exception while testing Lidarr connection: {e}")


async def fetch_root_folders() -> list[str]:
    """
    Fetches the list of directories (root folders) Lidarr is configured to monitor
    Updates the torznab category list with valid directories
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/rootfolder", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[LIDARR] Failed to fetch root folders: {response.status_code}")
                return []

            root_folders = response.json()
            log.debug(f"[LIDARR] Fetched root folders ({len(root_folders)} directories)")

            tracked_root_folders = []
            # check each root folder for access and add to tracked paths
            for root_folder_entry in root_folders:
                root_folder_path = root_folder_entry["path"]
                # skip if we can't access this directory
                if not os.path.exists(root_folder_path):
                    log.warning(f"[LIDARR] Unable to access root folder: {root_folder_path}")
                    continue

                tracked_root_folders.append(root_folder_path)
                log.debug(f"[LIDARR] Tracking Lidarr path: {root_folder_path}")

            return tracked_root_folders
    except Exception as e:
        log.error(f"[LIDARR] Exception while fetching root folders: {e}")
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
                log.warning(f"[LIDARR] Failed to fetch artist list: {response.status_code}")
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
                log.warning(f"[LIDARR] Failed to fetch album list: {response.status_code}")
                return []

            album_list = response.json()

        # key albums by their ID
        album_metadata = {album["id"]: album for album in album_list}

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
                album_metadata = album_metadata[album_id]
                album_stats = album_metadata["statistics"]

                # get the percent of tracks for album that are currently tracked on disk
                percent_of_tracks = album_stats["percentOfTracks"]
                missing_track_count = album_stats["totalTrackCount"] - album_stats["trackFileCount"]

                # add all the track parent directories to a set to ensure none are unique
                track_paths = {os.path.dirname(track["path"]) for track in album_tracks}
                shared_directory = len(track_paths) == 1

                # skip the album if tracks do not share a single directory
                if not shared_directory:
                    log.warning(f"[LIDARR] Skipping album creation for '{artist["artistName"]} - {album_metadata["title"]}', must share a single parent directory")

                # build full albums which share a single directory
                if percent_of_tracks == 100 and missing_track_count == 0 and shared_directory:
                    aggregated_metadata = aggregate_album_metadata(album_tracks)
                    metadata_tags = build_tags_from_metadata(aggregated_metadata)
                    title = f"{artist["artistName"]} - {album_metadata["title"]} {metadata_tags}"
                    log.debug(f"[LIDARR] Album ({len(album_tracks)} tracks) grouped with title: {title}")

                    final_entries.append({"id": album_id, "title": title, "path": track_paths.pop(), "album": True, })

                else:
                    # if there are missing tracks or non-shared directory, just build each track one at a time
                    for album_track in album_tracks:
                        final_entries.append({"id": album_id, "path": album_track["path"], "album": False, })

        album_count = 0
        individual_tracks = 0
        for final_entry in final_entries:
            if final_entry["album"]:
                album_count += 1
            else:
                individual_tracks += 1

        log.info(f"[LIDARR] Fetched music library ({album_count} albums, {individual_tracks} individual tracks)")

        return final_entries
    except Exception as e:
        log.error(f"[LIDARR] Exception while fetching music library: {e}")
        return []


async def fetch_artist_tracks(artist_id: str) -> list[dict]:
    """
    Fetches the music track files for the given artist ID
    """
    try:
        async with httpx_request.get_client() as client:
            params = {"artistId": artist_id, }
            response = await client.get(f"{LIDARR_URL}/api/v1/trackfile", headers={"X-API-Key": LIDARR_API_KEY}, params=params, timeout=30)

            if response.status_code != 200:
                log.warning(f"[LIDARR] Failed to fetch track files: {response.status_code}")
                return []

            track_response = response.json()
            log.debug(f"[LIDARR] Fetched track files for artist ID {artist_id} ({len(track_response)} tracks)")
            return track_response
    except Exception as e:
        log.error(f"[LIDARR] Exception while fetching track files: {e}")
        return []


async def fetch_album_metadata(album_id: str) -> dict:
    """
    Fetches the metadata for the given album ID
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{LIDARR_URL}/api/v1/album/{album_id}", headers={"X-API-Key": LIDARR_API_KEY}, timeout=30)

            if response.status_code != 200:
                log.warning(f"[LIDARR] Failed to fetch album metadata: {response.status_code}")
                return []

            album_response = response.json()
            log.debug(f"[LIDARR] Fetched metadata for album ID {album_id}")
            return album_response
    except Exception as e:
        log.error(f"[LIDARR] Exception while fetching album metadata: {e}")
        return []
