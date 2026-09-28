"""#core.settings - Centralized configuration and environment variable management for Musicify
Settings file that can be imported for access to all environment variables and configuration values in one place. This helps avoid circular imports and keeps configuration organized.

"""
from dotenv import load_dotenv
import requests
load_dotenv()
import os
import threading

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

def env_hex(name, default=0):
    value = os.getenv(name)
    if value is None:
        return default
    return int(value.strip(), 16)

#Server settings
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "5000"))

# Bose SoundTouch settings
BOSE_IP = os.getenv("BOSE_IP", "192.168.29.234")

STREAM_URL = os.getenv("STREAM_URL", "http://192.168.29.157:8000/mpv.ogg")
raw_fallbacks = os.getenv("STREAM_FALLBACK_URLS", "")

STREAM_FALLBACK_URLS = (
    [u.strip() for u in raw_fallbacks.split(",") if u.strip()]
    if raw_fallbacks
    else ["http://192.168.29.229:8000/mpv.ogg", "http://127.0.0.1:8000/mpv.ogg"]
)

active_stream_url = STREAM_URL

for url in [STREAM_URL] + STREAM_FALLBACK_URLS:
    try:
        if requests.get(url, timeout=2.0, stream=True).status_code == 200:
            active_stream_url = url
            break
    except requests.RequestException:
        continue

STREAM_URL = active_stream_url

#Local file and playlist settings
ROOT_DIR = os.getenv("ROOT_DIR", os.path.expanduser("~/Music"))
PLAYLIST_DIR = os.getenv("PLAYLIST_DIR", os.path.expanduser("~/Music/playlists"))
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.txt")
_history_file_lock = threading.Lock()

def get_history_file():
    with _history_file_lock:
        return HISTORY_FILE

def set_history_file(filename):
    global HISTORY_FILE
    with _history_file_lock:
        HISTORY_FILE = filename

TIME_OFFSET = env_hex("TIME_OFFSET")
DEFAULT_LYRICS_ENABLED = env_bool("LYRICS_ENABLED", False)
DEFAULT_AUTOPLAY_ENABLED = env_bool("AUTOPLAY_ENABLED", True)
DEFAULT_INCOGNI_MODE = env_bool("INCOGNI_MODE", False)

SONG_NAME_CLEANUP = [
    "official music video",
    "official lyrics video",
    "official lyric video",
    "official audio",
    "official video",
    "official lyrics",
    "official lyric",
    "lyrics video",
    "lyric video",
    "music video",
    "club mix",
    "extended mix",
    "radio mix",
    "remix",
    "mix",
    "version",
    "official",
    "lyrics",
    "lyric",
    "audio",
    "video",
]
SONG_NAME_CLEANUP.extend(env_list("SONG_NAME_CLEANUP", []))




YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
BACKUP_YOUTUBE_API_KEY = os.getenv("BACKUP_YOUTUBE_API_KEY", YOUTUBE_API_KEY)
LASTFM_KEY = os.getenv("LASTFM_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


MUSIC_ATLAS_KEY = os.getenv("MUSIC_ATLAS_KEY", "PlaceholderKeyForMusicAtlas")