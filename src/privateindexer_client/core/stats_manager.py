import json
import os

from privateindexer_client.core.config import STATS_FILE


def load_persistent_stats() -> tuple[int, int]:
    """
    Load the all-time download and upload stats from the stats file if it exists
    """
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as file:
                data = json.loads(file.read())
            return data.get("all_time_download", 0), data.get("all_time_upload", 0)
        except Exception:
            return 0, 0
    return 0, 0


def save_persistent_stats(all_time_download: int, all_time_upload: int):
    """
    Save the all-time download and upload stats to the stats file
    """
    data = {"all_time_download": all_time_download, "all_time_upload": all_time_upload}
    with open(STATS_FILE, "w") as file:
        file.write(json.dumps(data))
