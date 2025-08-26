import asyncio
import json
import os

from privateindexer_client.core.logger import log

# gather/set environment variables for usage later
TORRENTS_FILE = "/app/data/torrents.json"
TORRENTS_DIR = "/app/data/torrents"
FASTRESUME_DIR = os.path.join(TORRENTS_DIR, "fastresume")

DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR")

CONFIG_FILE = "/app/data/config.json"

CATEGORY_PATHS = {}

INDEXER_API_URL = "https://indexer.humehouse.com"
ANNOUNCE_TRACKER_URL = "https://tracker.humehouse.com/announce"

SCANNER_THREADS = int(os.getenv("SCANNER_THREADS", "16"))
SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", "5"))

MOVIE_DIR = os.getenv("MOVIE_DIR", "false")
MOVIE_EXTENSIONS = os.getenv("MOVIE_EXTENSIONS", "mp4,mkv,m4v,avi").split(",")

API_KEY = os.getenv("API_KEY")

TORRENTING_PORT = int(os.getenv("TORRENTING_PORT", "6881"))

config_lock = asyncio.Lock()


async def load_config_threadsafe():
    """
    Reads data from the JSON configuration file
    """
    async with config_lock:
        if not os.path.exists(CONFIG_FILE):
            return {}
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"[CONFIG] Failed to load config.json: {e}")
            return {}


async def save_config_threadsafe(config):
    """
    Writes data to the JSON configuration file
    """
    async with config_lock:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            log.error(f"[CONFIG] Failed to write config.json: {e}")
