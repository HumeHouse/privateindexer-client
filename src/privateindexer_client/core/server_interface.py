import os

from privateindexer_client.core import httpx_request, database, radarr, sonarr, lidarr
from privateindexer_client.core import logger
from privateindexer_client.core.config import INDEXER_API_URL, API_KEY, RADARR_URL, SONARR_URL, LIDARR_URL
from privateindexer_client.core.media_helper import RADARR_ROOT_CATEGORY, SONARR_ROOT_CATEGORY, LIDARR_ROOT_CATEGORY


async def fetch_indexer_user_data():
    """
    Request the current user's indexer statistics for use in the GUI
    """
    try:
        async with httpx_request.get_client() as client:
            response = await client.get(f"{INDEXER_API_URL}/user/stats", headers={"X-API-Key": API_KEY}, timeout=30)

            if response.status_code != 200:
                logger.channel("indexer").warning(f"Failed to fetch user stats: {response.status_code} - {response.text}")
                return {}

            return response.json()
    except Exception as e:
        logger.channel("indexer").exception(f"Exception when fetching user stats: {e}")
        return None


async def send_torrent_to_indexer(torrent_path: str, category: int, torrent_name: str, app_id: int = None):
    """
    Attempt to upload the torrent file along with the category and name to the PrivateIndexer server
    Will mark a file as uploaded in the database if the server API returns a 409 status code
    """
    try:
        imdbid = None
        tmdbid = None
        tvdbid = None
        artist = None
        album = None

        if app_id is None:
            result = await database.fetch_one("SELECT app_id FROM torrents WHERE torrent_path = ?", (torrent_path,))
            if result and result.get("app_id"):
                app_id = result["app_id"]

        if app_id:
            if category == RADARR_ROOT_CATEGORY and RADARR_URL:
                movie_metadata = await radarr.fetch_movie_metadata(movie_id=app_id)
                imdbid = movie_metadata.get("imdbId")
                tmdbid = movie_metadata.get("tmdbId")
            elif category == SONARR_ROOT_CATEGORY and SONARR_URL:
                series_metadata = await sonarr.fetch_series_metadata(series_id=app_id)
                imdbid = series_metadata.get("imdbId")
                tmdbid = series_metadata.get("tmdbId")
                tvdbid = series_metadata.get("tvdbId")
            elif category == LIDARR_ROOT_CATEGORY and LIDARR_URL:
                album_metadata = await lidarr.fetch_album_metadata(album_id=app_id)
                artist = album_metadata.get("artist", {}).get("artistName")
                album = album_metadata.get("title")

        with open(torrent_path, "rb") as file:
            torrent_basename = os.path.basename(torrent_path)
            # build the request with all the necessary torrent metadata required by indexer
            files = {"torrent_file": (torrent_basename, file, "application/x-bittorrent")}
            data = {"category": category, "torrent_name": torrent_name}

            if imdbid:
                data["imdbid"] = imdbid

            if tmdbid:
                data["tmdbid"] = tmdbid

            if tvdbid:
                data["tvdbid"] = tvdbid

            if artist:
                data["artist"] = artist

            if album:
                data["album"] = album

            async with httpx_request.get_client() as client:
                response = await client.post(f"{INDEXER_API_URL}/upload", headers={"X-API-Key": API_KEY}, data=data, files=files)

                # based on the response from API, we will know status of upload
                if response.status_code == 200:
                    logger.channel("indexer").info(f"Successfully sent '{torrent_name}' to indexer")
                    return True
                elif response.status_code == 409:
                    logger.channel("indexer").info(f"Torrent '{torrent_name}' already exists on indexer, marking as uploaded")
                    return True
                else:
                    logger.channel("indexer").warning(f"Failed to send '{torrent_name}' to indexer, will retry later: {response.status_code} - {response.text}")
                    return False
    except Exception as e:
        logger.channel("indexer").exception(f"Exception while sending '{torrent_name}' to indexer, will retry later: {e}")
        return False
