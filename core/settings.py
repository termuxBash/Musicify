import os


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


BOSE_IP = os.getenv("BOSE_IP", "192.168.29.234")
STREAM_URL = os.getenv("STREAM_URL", "http://192.168.29.157:8000/mpv.ogg")
STREAM_FALLBACK_URLS = env_list(
    "STREAM_FALLBACK_URLS",
    ["http://192.168.29.229:8000/mpv.ogg", "http://127.0.0.1:8000/mpv.ogg"]
)
PLAYLIST_DIR = os.getenv("PLAYLIST_DIR", "/opt/radio/playlists")
DEFAULT_LYRICS_ENABLED = env_bool("LYRICS_ENABLED", True)
DEFAULT_AUTOPLAY_ENABLED = env_bool("AUTOPLAY_ENABLED", False)
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "5001"))