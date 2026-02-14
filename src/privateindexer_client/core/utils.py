import hashlib
import os
import secrets
import time


def format_bytes(num_bytes: int) -> str:
    """
    Helper to format bytes into a human-readable string
    """
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.2f} KiB"
    if num_bytes < 1024 ** 3:
        return f"{num_bytes / (1024 ** 2):.2f} MiB"
    return f"{num_bytes / (1024 ** 3):.2f} GiB"


def valid_file(media_file: str) -> bool:
    """
    Helper to check if a media file is okay to use in a torrent
    """
    return os.path.exists(media_file) and os.path.isfile(media_file)


def generate_sid() -> str:
    """
    Generate a simple session ID
    """
    nonce = secrets.token_hex(16)
    raw = f"{nonce}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()
