"""#core.settings - Centralized configuration and environment variable management for Musicify
Settings file that can be imported for access to all environment variables and configuration values in one place. This helps avoid circular imports and keeps configuration organized.

"""
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
DEFAULT_LYRICS_ENABLED = env_bool("LYRICS_ENABLED", False)
DEFAULT_AUTOPLAY_ENABLED = env_bool("AUTOPLAY_ENABLED", True)
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "5000"))

ROOT_DIR = os.getenv("ROOT_DIR", os.path.expanduser("~/Music"))

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
BACKUP_YOUTUBE_API_KEY = os.getenv("BACKUP_YOUTUBE_API_KEY", YOUTUBE_API_KEY)
LASTFM_KEY = os.getenv("LASTFM_KEY")