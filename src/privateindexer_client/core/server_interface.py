import os

from privateindexer_client.core import httpx_request, database, radarr, sonarr, lidarr
from privateindexer_client.core import logger
from privateindexer_client.core.config import INDEXER_API_URL, API_KEY, RADARR_URL, SONARR_URL, LIDARR_URL, APP_VERSION, TORRENTING_PORT, TAG_SEARCH_RESULTS, ANNOUNCE_IP
from privateindexer_client.core.media_helper import RADARR_ROOT_CATEGORY, SONARR_ROOT_CATEGORY, LIDARR_ROOT_CATEGORY


async def fetch_indexer_user_data():
    """
    Request the current user's indexer statistics for use in the GUI
    """
    try:
        async with httpx_request.get_client() as client:
            try:
                response = await client.get(f"{INDEXER_API_URL}/user/stats", headers={"X-API-Key": API_KEY}, timeout=30)
            except Exception as e:
                logger.channel("indexer").warning(f"Unable to connect to PrivateIndexer server - server could be down ({e})")
                return {}
            else:
                if response.status_code != 200:
                    logger.channel("indexer").warning(f"Failed to fetch user stats: {response.status_code} - {response.text}")
                    return {}

                return response.json()
    except Exception as e:
        logger.channel("indexer").exception(f"Exception when fetching user stats: {e}")
        return None


async def send_torrent_to_indexer(torrent_path: str, category: int, torrent_name: str, app_id: int = None) -> tuple[bool, bool]:
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
                try:
                    response = await client.post(f"{INDEXER_API_URL}/upload", headers={"X-API-Key": API_KEY}, data=data, files=files)
                except Exception as e:
                    logger.channel("indexer").warning(f"Unable to connect to PrivateIndexer server - server could be down ({e})")
                    return False, False
                else:
                    # based on the response from API, we will know status of upload
                    if response.status_code == 200:
                        logger.channel("indexer").info(f"Successfully sent '{torrent_name}' to indexer")
                        return True, False
                    elif response.status_code == 409:
                        logger.channel("indexer").info(f"Torrent '{torrent_name}' already exists on indexer, marking as uploaded")
                        return True, False
                    elif response.status_code == 422:
                        logger.channel("indexer").error(f"Server failed to process torrent '{torrent_name}'")
                        return False, True
                    else:
                        logger.channel("indexer").warning(f"Failed to send '{torrent_name}' to indexer, will retry later: {response.status_code} - {response.text}")
                        return False, False
    except Exception as e:
        logger.channel("indexer").exception(f"Exception while sending '{torrent_name}' to indexer, will retry later: {e}")
        return False, False


async def sync_torrents_with_indexer(local_torrents: dict) -> dict | None:
    """
    Attempt to sync the local torrent database with the PrivateIndexer server
    """
    async with httpx_request.get_client() as client:
        try:
            # call the sync endpoint to get the list of existing and missing torrents on the server
            response = await client.post(f"{INDEXER_API_URL}/sync", headers={"X-API-Key": API_KEY}, json=local_torrents)
        except Exception as e:
            logger.channel("indexer").warning(f"Unable to connect to PrivateIndexer server to sync torrents - server could be down ({e})")
            return False

    return response.json() if response.status_code == 200 else False


async def test_indexer_connection() -> bool:
    """
    Attempt to connect and authenticate with the PrivateIndexer server
    Exits if server rejects our key or is unable to connect to our libtorrent server
    """
    async with httpx_request.get_client() as client:
        params = {"v": APP_VERSION, "port": TORRENTING_PORT, "public_uploads": TAG_SEARCH_RESULTS}
        if ANNOUNCE_IP:
            params["announce_ip"] = ANNOUNCE_IP
        try:
            indexer_response = await client.get(f"{INDEXER_API_URL}/user", headers={"X-API-Key": API_KEY}, params=params, timeout=10)
        except Exception as e:
            logger.channel("indexer").warning(f"Unable to connect to PrivateIndexer server - server could be down ({e})")
        else:
            status_code = indexer_response.status_code
            if status_code == 200:
                response_json = indexer_response.json()
                user_label = response_json["user_label"]
                announce_ip = response_json["announce_ip"]
                logger.channel("indexer").info(f"Connected to PrivateIndexer server as '{user_label}'")
                is_reachable = response_json["is_reachable"]
                if is_reachable:
                    logger.channel("indexer").info(f"PrivateIndexer server successfully verified we are REACHABLE at {announce_ip}:{TORRENTING_PORT}")
                else:
                    logger.channel("indexer").critical(
                        f"PrivateIndexer server is UNABLE TO REACH US at {announce_ip}:{TORRENTING_PORT} - check your port forwarding settings")
                    exit(1)
            elif status_code == 403:
                logger.channel("indexer").critical("API key rejected by PrivateIndexer server")
                exit(1)
            else:
                logger.channel("indexer").warning(f"Unable to validate API key and port status with PrivateIndexer server - server could be down (status {status_code})")


async def validate_torrent_with_indexer(torrent_hash: str) -> bool:
    """
    Checks on the server if a torrent exists on server, otherwise rejects it
    """
    async with httpx_request.get_client() as client:
        try:
            # check with the server to validate that this is a torrent from PrivateIndexer
            response = await client.get(f"{INDEXER_API_URL}/validate?infohash={torrent_hash}", headers={"X-API-Key": API_KEY})
        except Exception as e:
            logger.channel("indexer").warning(f"Unable to connect to PrivateIndexer server to validate torrent - server could be down ({e})")
            return False
        else:
            # based on the response from API, we will know if torrent exists on server
            if response.status_code == 200:
                # this means the torrent exists in the server database
                return True
            elif response.status_code == 404:
                logger.channel("indexer").warning(f"Torrent hash was not found on PrivateIndexer server: {torrent_hash}")
                return False
            else:
                logger.channel("indexer").critical(
                    f"Unknown error code occurred when validating hash '{torrent_hash}' with indexer: {response.status_code} - {response.text}")
                return False
