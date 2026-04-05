import json
import os
import threading

from privateindexer_client.core import logger

APP_VERSION = "1.10.3"

DATA_DIR = "/app/data"

# gather/set environment variables for usage later
DATABASE_FILE = os.path.join(DATA_DIR, "torrents.db")
TORRENTS_DIR = os.path.join(DATA_DIR, "torrents")
FASTRESUME_DIR = os.path.join(TORRENTS_DIR, "fastresume")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR")

STATS_FILE = os.path.join(DATA_DIR, "stats.json")

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

CACHE_DIR = os.path.join(DATA_DIR, "cache")

api_url = (os.getenv("INDEXER_API_URL", "")).strip("/")
INDEXER_API_URL = f"{api_url}/api/v2"

MAX_THREADS = int(os.getenv("MAX_THREADS", 8))

CACHE_CLEAN_INTERVAL = 60 * 60 * int(os.getenv("CACHE_CLEAN_INTERVAL", 12))
CACHE_EXPIRATION = 24 * 60 * 60 * int(os.getenv("CACHE_EXPIRATION", 7))

SYNC_INTERVAL = 60 * int(os.getenv("SYNC_INTERVAL", 60))
SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", 30))
SCAN_BATCH_SIZE = int(os.getenv("SCAN_BATCH_SIZE", 128))

FASTRESUME_INTERVAL = 60 * int(os.getenv("FASTRESUME_INTERVAL", 60))

MEMORY_LOG_INTERVAL = int(os.getenv("MEMORY_LOG_INTERVAL", 0))

STALE_TORRENT_THRESHOLD = 24 * 60 * 60 * int(os.getenv("STALE_TORRENT_THRESHOLD", 30))

RADARR_URL = (os.getenv("RADARR_URL", "")).strip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")

SONARR_URL = (os.getenv("SONARR_URL", "")).strip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")

LIDARR_URL = (os.getenv("LIDARR_URL", "")).strip("/")
LIDARR_API_KEY = os.getenv("LIDARR_API_KEY")

API_KEY = os.getenv("API_KEY")

API_USERNAME = os.getenv("API_USERNAME", "privateindexer")
API_PASSWORD = os.getenv("API_PASSWORD", "privateindexer")

TRACKER_API_URL = (os.getenv("TRACKER_API_URL", "")).strip("/")
ANNOUNCE_TRACKER_URL = f"{TRACKER_API_URL}/announce?apikey={API_KEY}"

ANNOUNCE_IP = os.getenv("ANNOUNCE_IP")
TORRENTING_PORT = int(os.getenv("TORRENTING_PORT", 6881))
TAG_SEARCH_RESULTS = os.getenv("TAG_SEARCH_RESULTS", "true").lower() == "true"

PURGE_UNTRACKED_TORRENTS = os.getenv("PURGE_UNTRACKED_TORRENTS", "true").lower() == "true"

PURGE_UNTRACKED_DOWNLOADS = os.getenv("PURGE_UNTRACKED_DOWNLOADS", "true").lower() == "true"

PURGE_DUPLICATE_SEEDS = os.getenv("PURGE_DUPLICATE_SEEDS", "true").lower() == "true"

LEW_MEMORY_MODE = os.getenv("LEW_MEMORY_MODE", "false").lower() == "true"

ALLOW_UTP_CONNECTIONS = os.getenv("ALLOW_UTP_CONNECTIONS", "false").lower() == "true"

MAX_UNCHOKE_SLOTS = int(os.getenv("MAX_UNCHOKE_SLOTS", -1))
MAX_DOWNLOAD_SLOTS = int(os.getenv("MAX_DOWNLOAD_SLOTS", -1))

config_lock = threading.Lock()
_config_cache = None


def load_config():
    global _config_cache
    with config_lock:
        # we have to disable this inspection because it gets confused, default is None, but we update it
        # noinspection PyUnreachableCode
        if _config_cache:
            return _config_cache

        if not os.path.exists(CONFIG_FILE):
            # create the file if it doesn't exist and return empty config
            _config_cache = {}
            with open(CONFIG_FILE, "w") as f:
                json.dump(_config_cache, f, indent=2)
            return _config_cache

        try:
            with open(CONFIG_FILE, "r") as f:
                _config_cache = json.load(f)
        except Exception as e:
            logger.channel("config").exception(f"Exception while loading config.json: {e}")
            _config_cache = {}
        return _config_cache


def save_config(config):
    global _config_cache
    with config_lock:
        try:
            _config_cache = config
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.channel("config").exception(f"Exception while writing config.json: {e}")


def validate_environment():
    """
    Check environment variables for validity and exit on errors
    """
    logger.channel("config").info("Validating environment")

    # check if data directory exists
    if not os.path.isdir(DATA_DIR):
        logger.channel("config").critical(f"Data directory does not exist: {DATA_DIR}")
        exit(1)

    # check if data directory has correct permissions
    try:
        test_file = os.path.join(DATA_DIR, ".write_test")
        with open(test_file, "w"):
            pass
        os.unlink(test_file)
    except OSError:
        logger.channel("config").critical(f"Data directory is not writable: {DATA_DIR}")
        exit(1)

    # ensure indexer URL is set
    if not INDEXER_API_URL:
        logger.channel("config").critical(f"Indexer URL not set: {INDEXER_API_URL}")
        exit(1)

    # ensure tracker URL is set
    if not TRACKER_API_URL:
        logger.channel("config").critical(f"Tracker URL not set: {TRACKER_API_URL}")
        exit(1)

    # try to create torrents and fastresume directories
    try:
        os.makedirs(TORRENTS_DIR, exist_ok=True)
        os.makedirs(FASTRESUME_DIR, exist_ok=True)
    except Exception as e:
        logger.channel("config").exception(f"Exception while creating torrent data directory: {e}")
        exit(1)

    # try to create cache directory
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except Exception as e:
        logger.channel("config").exception(f"Exception while creating torrent data directory: {e}")
        exit(1)

    # check if downloads directory exists
    if not os.path.isdir(DOWNLOADS_DIR):
        logger.channel("config").critical(f"Downloads directory does not exist: {DOWNLOADS_DIR}")
        exit(1)

    # check if downloads directory has correct permissions
    try:
        test_file = os.path.join(DOWNLOADS_DIR, ".write_test")
        with open(test_file, "w"):
            pass
        os.unlink(test_file)
    except OSError:
        logger.channel("config").critical(f"Downloads directory is not writable: {DOWNLOADS_DIR}")
        exit(1)

    # check if Radarr key exists if enabled
    if RADARR_URL:
        if not RADARR_API_KEY:
            logger.channel("config").critical(f"No API key provided for Radarr")
            exit(1)

    # check if Sonarr key exists if enabled
    if SONARR_URL:
        if not SONARR_API_KEY:
            logger.channel("config").critical(f"No API key provided for Sonarr")
            exit(1)

    # check if Lidarr key exists if enabled
    if LIDARR_URL:
        if not LIDARR_API_KEY:
            logger.channel("config").critical(f"No API key provided for Lidarr")
            exit(1)

    logger.channel("config").info("Environment is valid")
