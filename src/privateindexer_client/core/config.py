import os

# gather/set environment variables for usage later
TORRENTS_FILE = "/app/data/torrents.json"
TORRENTS_DIR = "/app/data/torrents"

CATEGORY_PATHS = {}

INDEXER_API_URL = "https://indexer.humehouse.com"
ANNOUNCE_TRACKER_URL = "https://tracker.humehouse.com/announce"

SCANNER_THREADS = int(os.getenv("SCANNER_THREADS", "16"))
SCAN_INTERVAL = 60 * int(os.getenv("SCAN_INTERVAL", "5"))

MOVIE_DIR = os.getenv("MOVIE_DIR", "false")
MOVIE_EXTENSIONS = os.getenv("MOVIE_EXTENSIONS", "mp4,mkv,m4v,avi").split(",")

API_KEY = os.getenv("API_KEY")

TORRENTING_PORT = int(os.getenv("TORRENTING_PORT", "6881"))
