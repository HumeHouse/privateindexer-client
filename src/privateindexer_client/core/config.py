import json
import os
import threading

from privateindexer_client.core.logger import log

APP_VERSION = "1.10.1"

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
            log.error(f"[CONFIG] Exception while loading config.json: {e}")
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
            log.error(f"[CONFIG] Exception while writing config.json: {e}")
