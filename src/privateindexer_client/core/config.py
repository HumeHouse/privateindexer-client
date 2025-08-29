import json
import os
import threading

from privateindexer_client.core.logger import log

APP_VERSION = "1.2.2"

# gather/set environment variables for usage later
DATABASE_FILE = "/app/data/torrents.db"
# TODO: remove this key in a few versions or once everyone is updated to SQLite
TORRENTS_FILE = "/app/data/torrents.json"
TORRENTS_DIR = "/app/data/torrents"
FASTRESUME_DIR = os.path.join(TORRENTS_DIR, "fastresume")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR")

CONFIG_FILE = "/app/data/config.json"

TORZNAB_CATEGORY_PATHS = {}

INDEXER_API_URL = "https://indexer.humehouse.com"

SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", "5"))
MAX_THREADS = int(os.getenv("MAX_THREADS", "8"))

MOVIE_DIR = os.getenv("MOVIE_DIR", "false")
MOVIE_EXTENSIONS = os.getenv("MOVIE_EXTENSIONS", "mp4,mkv,m4v,avi").split(",")

API_KEY = os.getenv("API_KEY")
ANNOUNCE_TRACKER_URL = "https://tracker.humehouse.com/announce" + "?apikey=" + API_KEY

TORRENTING_PORT = int(os.getenv("TORRENTING_PORT", "6881"))

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
            save_config(_config_cache)
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
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            _config_cache = config
        except Exception as e:
            log.error(f"[CONFIG] Failed to write config.json: {e}")
