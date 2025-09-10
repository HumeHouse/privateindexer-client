import json
import os
import threading

from privateindexer_client.core.logger import log

APP_VERSION = "1.3.2"

# gather/set environment variables for usage later
DATABASE_FILE = "/app/data/torrents.db"
TORRENTS_DIR = "/app/data/torrents"
FASTRESUME_DIR = os.path.join(TORRENTS_DIR, "fastresume")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR")

STATS_FILE = "/app/data/stats.json"

CONFIG_FILE = "/app/data/config.json"

TORZNAB_CATEGORY_PATHS = []

INDEXER_API_URL = "https://indexer.humehouse.com"

MAX_THREADS = int(os.getenv("MAX_THREADS", 8))

SYNC_INTERVAL = 60 * 60
SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", 30))

FASTRESUME_INTERVAL = 60 * int(os.getenv("FASTRESUME_INTERVAL", 60))

RADARR_URL = (os.getenv("RADARR_URL", "")).strip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY")

SONARR_URL = (os.getenv("SONARR_URL", "")).strip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY")

API_KEY = os.getenv("API_KEY")
ANNOUNCE_TRACKER_URL = "https://tracker.humehouse.com/announce" + "?apikey=" + API_KEY

ANNOUNCE_IP = os.getenv("ANNOUNCE_IP")
TORRENTING_PORT = int(os.getenv("TORRENTING_PORT", 6881))

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
            log.error(f"[CONFIG] Failed to load config.json: {e}")
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
            log.error(f"[CONFIG] Failed to write config.json: {e}")
