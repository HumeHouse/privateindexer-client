import asyncio
import json
import os

from privateindexer_client.core.config import TORRENTS_FILE
from privateindexer_client.core.logger import log

torrents_lock = asyncio.Lock()


async def load_torrents_threadsafe():
    """
    Reads JSON database of all actively tracked torrents on this client
    """
    async with torrents_lock:
        if not os.path.exists(TORRENTS_FILE):
            return []
        try:
            with open(TORRENTS_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"[TORRENTS] Failed to load torrents.json: {e}")
            return []


async def save_torrents_threadsafe(torrents):
    """
    Writes to the JSON database for torrent data, same purpose as load_torrents_threadsafe()
    """
    async with torrents_lock:
        try:
            with open(TORRENTS_FILE, "w") as f:
                json.dump(torrents, f, indent=2)
        except Exception as e:
            log.error(f"[TORRENTS] Failed to write torrents.json: {e}")
