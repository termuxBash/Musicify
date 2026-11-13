"""#core.settings - Centralized configuration and environment variable management for Musicify
Settings file that can be imported for access to all environment variables and configuration values in one place. This helps avoid circular imports and keeps configuration organized.

"""
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


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

def env_int(name, default=0):
    value = os.getenv(name)
    if value is None:
        return default
    v = value.split("#")[0].strip()
    try:
        return int(v)
    except ValueError:
        return default


DEFAULT_LYRICS_OFFSET_MS = -13000
LYRICS_EFFORT_LEVELS = ("LRCLIB", "LYRICA", "GEMINI")
RADIO_GENRES = (
    "pop",
    "rock",
    "jazz",
    "classical",
    "electronic",
    "dance",
    "hip hop",
    "metal",
    "country",
    "blues",
    "ambient",
    "folk",
    "reggae",
    "soul",
    "funk",
    "indie",
    "alternative",
    "house",
    "techno",
    "chillout",
)
USER_CONFIG_PATH = Path(__file__).resolve().parent.parent / "userconfig.json"
_user_config_lock = threading.RLock()
_logger = logging.getLogger(__name__)


def _read_user_config():
    config = {}
    try:
        with USER_CONFIG_PATH.open(encoding="utf-8") as config_file:
            loaded_config = json.load(config_file)
        if isinstance(loaded_config, dict):
            config = loaded_config
        else:
            _logger.warning("Ignoring userconfig.json because its root value must be an object.")
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as error:
        _logger.warning("Could not read userconfig.json: %s", error)

    legacy_offset_ms = config.pop("lyrics_offset_ms", DEFAULT_LYRICS_OFFSET_MS)
    if type(legacy_offset_ms) is not int:
        _logger.warning(
            "Invalid lyrics_offset_ms in userconfig.json; using %d.",
            DEFAULT_LYRICS_OFFSET_MS,
        )
        legacy_offset_ms = DEFAULT_LYRICS_OFFSET_MS

    offsets = {}
    for source in ("online", "local"):
        key = f"lyrics_offset_{source}_ms"
        offset_ms = config.get(key, legacy_offset_ms)
        if type(offset_ms) is not int:
            _logger.warning(
                "Invalid %s in userconfig.json; using %d.",
                key,
                legacy_offset_ms,
            )
            offset_ms = legacy_offset_ms
        offsets[key] = offset_ms

    effort = config.get("lyrics_effort", "GEMINI")
    if not isinstance(effort, str) or effort.upper() not in LYRICS_EFFORT_LEVELS:
        _logger.warning("Invalid lyrics_effort in userconfig.json; using GEMINI.")
        effort = "GEMINI"
    else:
        effort = effort.upper()

    return {**config, **offsets, "lyrics_effort": effort}


_user_config = _read_user_config()
LYRICS_OFFSET_ONLINE_MS = _user_config["lyrics_offset_online_ms"]
LYRICS_OFFSET_LOCAL_MS = _user_config["lyrics_offset_local_ms"]
# Keep the legacy constant as an alias for callers that still expect one offset.
LYRICS_OFFSET_MS = LYRICS_OFFSET_ONLINE_MS
LYRICS_EFFORT = _user_config["lyrics_effort"]


def update_user_config(key, value):
    """Persist an app-managed setting and update the in-memory config."""
    global _user_config
    update_values = {key: value}
    if key in {"lyrics_offset_online_ms", "lyrics_offset_local_ms"}:
        if type(value) is not int:
            raise ValueError(f"{key} must be an integer")
    elif key == "lyrics_offset_ms":
        if type(value) is not int:
            raise ValueError("lyrics_offset_ms must be an integer")
        update_values = {
            "lyrics_offset_online_ms": value,
            "lyrics_offset_local_ms": value,
        }
    elif key == "lyrics_effort":
        if not isinstance(value, str) or value not in LYRICS_EFFORT_LEVELS:
            raise ValueError("lyrics_effort must be LRCLIB, LYRICA, or GEMINI")
    elif key == "radio":
        if not isinstance(value, dict):
            raise ValueError("radio must be an object")
        for number in map(str, range(1, 7)):
            station = value.get(number)
            if station is not None and (
                not isinstance(station, dict)
                or not isinstance(station.get("name"), str)
                or not isinstance(station.get("url"), str)
            ):
                raise ValueError(f"radio slot {number} must contain a name and URL")
        favorites = value.get("favorites")
        if not isinstance(favorites, list) or any(
            not isinstance(station, dict)
            or not isinstance(station.get("name"), str)
            or not isinstance(station.get("url"), str)
            for station in favorites
        ):
            raise ValueError("radio favorites must be a list of stations")
        genres = value.get("genres", RADIO_GENRES)
        if not isinstance(genres, (list, tuple)) or any(
            not isinstance(genre, str) or genre not in RADIO_GENRES
            for genre in genres
        ):
            raise ValueError("radio genres must be selected from the supported genre list")
    else:
        raise ValueError(f"Unsupported userconfig setting: {key}")

    with _user_config_lock:
        updated_config = {**_user_config, **update_values}
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=USER_CONFIG_PATH.parent,
                prefix=".userconfig-",
                suffix=".tmp",
                delete=False,
            ) as config_file:
                temporary_path = Path(config_file.name)
                json.dump(updated_config, config_file, indent=2)
                config_file.write("\n")
            os.replace(temporary_path, USER_CONFIG_PATH)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        _user_config = updated_config


def get_radio_config():
    """Return a copy of the saved radio presets and favorites."""
    with _user_config_lock:
        configured_radio = _user_config.get("radio", {})
        configured_radio = configured_radio if isinstance(configured_radio, dict) else {}
        presets = {}
        for number in range(1, 7):
            key = str(number)
            station = configured_radio.get(key)
            presets[key] = (
                dict(station)
                if isinstance(station, dict)
                and isinstance(station.get("name"), str)
                and isinstance(station.get("url"), str)
                else None
            )

        configured_favorites = configured_radio.get("favorites", [])
        favorites = [
            dict(station)
            for station in configured_favorites
            if isinstance(station, dict)
            and isinstance(station.get("name"), str)
            and isinstance(station.get("url"), str)
        ] if isinstance(configured_favorites, list) else []
        configured_genres = configured_radio.get("genres", RADIO_GENRES)
        genres = (
            [genre for genre in configured_genres if genre in RADIO_GENRES]
            if isinstance(configured_genres, list)
            else list(RADIO_GENRES)
        )
        return {**presets, "favorites": favorites, "genres": genres}


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
HISTORY_TABLE = os.getenv("HISTORY_TABLE", "history")
DB_FILENAME = os.getenv("DB_FILENAME", "history.sqlite3")
_history_file_lock = threading.Lock()

def get_history_table():
    with _history_file_lock:
        return HISTORY_TABLE

def set_history_table(table_name):
    global HISTORY_TABLE
    with _history_file_lock:
        HISTORY_TABLE = table_name

TIME_OFFSET = env_int("TIME_OFFSET", 0)
DEFAULT_LYRICS_ENABLED = env_bool("LYRICS_ENABLED", False)
DEFAULT_AUTOPLAY_ENABLED = env_bool("AUTOPLAY_ENABLED", False)
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
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


MUSIC_ATLAS_KEY = os.getenv("MUSIC_ATLAS_KEY", "PlaceholderKeyForMusicAtlas")