import logging
import re
import threading
import time
from difflib import get_close_matches
from urllib.parse import unquote, urlparse

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
import httpx
from pyradios import RadioBrowser

from core.settings import RADIO_GENRES, get_radio_config, update_user_config


logger = logging.getLogger(__name__)
radio_bp = Blueprint("radio", __name__)
# Keep the certificate workaround scoped to Radio Browser requests.
_radio_browser = RadioBrowser(session=httpx.Client(verify=False, timeout=1.5))
_radio_browser.base_url = "https://de1.api.radio-browser.info/"
_status_lock = threading.Lock()
_METADATA_CACHE_SECONDS = 20
_PLAYBACK_START_GRACE_SECONDS = 8
_RADIO_FALLBACK_THUMBNAIL = "/static/radio.svg"
_GENERIC_STREAM_NAMES = {
    "audio",
    "feed",
    "index",
    "live",
    "listen",
    "mp3",
    "mpv",
    "radio",
    "stream",
    "streams",
}
_INVALID_STATION_NAMES = {
    "invaliduri",
    "invalid uri",
    "unknown",
    "n/a",
    "none",
    "null",
}


def _same_stream_url(first, second):
    if not first or not second:
        return False
    try:
        first_url = urlparse(first)
        second_url = urlparse(second)
        first_scheme = first_url.scheme.lower()
        second_scheme = second_url.scheme.lower()
        first_port = first_url.port
        second_port = second_url.port
    except ValueError:
        return False
    if first_scheme not in {"http", "https"} or second_scheme not in {"http", "https"}:
        return False
    same_port = (
        first_port == second_port
        if first_scheme != second_scheme
        else (first_port or (443 if first_scheme == "https" else 80))
        == (second_port or (443 if second_scheme == "https" else 80))
    )
    return (
        first_url.hostname is not None
        and second_url.hostname is not None
        and (first_url.hostname or "").lower() == (second_url.hostname or "").lower()
        and same_port
        and first_url.path.rstrip("/") == second_url.path.rstrip("/")
        and first_url.query == second_url.query
    )


def _usable_station_name(name):
    if not isinstance(name, str) or not name.strip():
        return False
    normalized_name = name.strip().casefold()
    return (
        normalized_name not in _INVALID_STATION_NAMES
        and "://" not in normalized_name
    )


def _usable_track_text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "://" not in value
        and "tracklist" not in value.casefold()
    )


def _confirmed_station_name(metadata, station):
    metadata_name = metadata.get("station") if isinstance(metadata, dict) else None
    if (
        isinstance(metadata, dict)
        and metadata.get("success")
        and _usable_station_name(metadata_name)
    ):
        station["name"] = metadata_name.strip()
        return station["name"]

    station_name = station.get("name")
    if station.get("stationuuid") and _usable_station_name(station_name):
        return station_name.strip()
    return None


def _station_payload(station):
    if not isinstance(station, dict):
        return None

    name = station.get("name")
    stream_url = station.get("url_resolved") or station.get("url")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(stream_url, str):
        return None
    stream_url = stream_url.strip()
    try:
        parsed_url = urlparse(stream_url)
    except ValueError:
        return None
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return None

    return {
        "name": name.strip(),
        "url": stream_url,
        "favicon": station.get("favicon") if isinstance(station.get("favicon"), str) else "",
        "stationuuid": station.get("stationuuid") if isinstance(station.get("stationuuid"), str) else "",
        "homepage": station.get("homepage") if isinstance(station.get("homepage"), str) else "",
        "country": station.get("country") if isinstance(station.get("country"), str) else "",
        "tags": station.get("tags") if isinstance(station.get("tags"), str) else "",
        "codec": station.get("codec") if isinstance(station.get("codec"), str) else "",
        "bitrate": station.get("bitrate") or "",
    }


def _public_station(station):
    payload = _station_payload(station)
    if payload is None:
        return None
    return payload


def _top_stations():
    stations = _radio_browser.search(
        order="votes",
        reverse=True,
        limit=18,
    )
    return [station for item in stations if (station := _public_station(item))]


def _stream_station(station_name, stream_url):
    if not _usable_station_name(station_name):
        return None

    if station_name.strip().casefold() == "mpv":
        return None

    try:
        stations = _radio_browser.search(
            name=station_name.strip(),
            name_exact=True,
            limit=10,
        )
    except Exception:
        logger.warning("Could not look up radio station artwork.", exc_info=True)
        return None

    valid_stations = [
        station
        for item in stations
        if (station := _public_station(item))
    ]
    for station in valid_stations:
        if _same_stream_url(station["url"], stream_url):
            return station

    for station in valid_stations:
        if (
            station["name"].casefold() == station_name.strip().casefold()
            and station["favicon"]
        ):
            station["url"] = stream_url
            return station
    return None


def _station_for_stream_url(stream_url):
    parsed_url = urlparse(stream_url)
    path_name = unquote(parsed_url.path.rstrip("/").rsplit("/", 1)[-1])
    path_name = re.sub(r"\.(aac|m3u8?|mp3|ogg|pls|wav)$", "", path_name, flags=re.IGNORECASE)

    if (
        path_name
        and path_name.casefold() not in _GENERIC_STREAM_NAMES
        and _usable_station_name(path_name)
    ):
        try:
            stations = _radio_browser.search(name=path_name, limit=25)
        except Exception:
            logger.warning(
                "Could not look up radio station metadata for stream URL %s.",
                stream_url,
                exc_info=True,
            )
            stations = []

        for candidate in stations:
            station = _public_station(candidate)
            candidate_urls = (
                candidate.get("url"),
                candidate.get("url_resolved"),
            ) if isinstance(candidate, dict) else ()
            if station and any(
                _same_stream_url(candidate_url, stream_url)
                for candidate_url in candidate_urls
            ):
                station["name"] = station["name"].strip()
                station["url"] = stream_url
                station["favicon"] = station["favicon"] or _RADIO_FALLBACK_THUMBNAIL
                return station

    hostname = parsed_url.hostname or ""
    host_name = next(
        (
            label
            for label in hostname.split(".")
            if label.casefold() not in {"www", "stream", "streams", "radio", "live", "listen"}
        ),
        "",
    )
    display_name = path_name or host_name or "Internet Radio"
    display_name = re.sub(r"[-_.]+", " ", display_name).strip().title()
    return {
        "name": display_name or "Internet Radio",
        "url": stream_url,
        "favicon": _RADIO_FALLBACK_THUMBNAIL,
        "stationuuid": "",
        "homepage": "",
        "country": "",
        "tags": "",
        "codec": "",
        "bitrate": "",
    }


def _station_from_stream_metadata(metadata, stream_url):
    station_name = metadata.get("station") if isinstance(metadata, dict) else None
    if isinstance(station_name, str) and station_name.strip():
        if not _usable_station_name(station_name):
            return None
        return {
            "name": station_name.strip(),
            "url": stream_url,
            "favicon": "",
            "stationuuid": "",
        }
    return _station_for_stream_url(stream_url)


def _is_http_stream_url(stream_url):
    if not isinstance(stream_url, str) or not stream_url.strip():
        return False

    try:
        parsed_url = urlparse(stream_url.strip())
        port = parsed_url.port
    except ValueError:
        return False

    return (
        parsed_url.scheme.lower() in {"http", "https"}
        and parsed_url.hostname is not None
        and (port is None or 0 <= port <= 65535)
    )


def initialize_radio_playback(app):
    playback = getattr(app, "playback", None)
    player = getattr(app, "player", None)
    bose = getattr(player, "bose", None)
    if playback is None or playback.owner is not None or bose is None:
        return False

    try:
        playing = bose.get_playing_url()
    except Exception:
        logger.exception("Could not check for existing radio playback at startup.")
        return False

    stream_url = playing.get("url") if isinstance(playing, dict) else None
    if not _is_http_stream_url(stream_url):
        return False
    stream_url = stream_url.strip()

    try:
        metadata = bose.get_stream_metadata(stream_url)
    except Exception:
        logger.exception("Could not retrieve radio metadata at startup.")
        metadata = None

    station = _station_from_stream_metadata(metadata, stream_url)
    if station is None:
        return False

    station_name = _confirmed_station_name(metadata, station)
    if station_name is None or not playback.acquire("radio"):
        return False
    metadata = metadata if isinstance(metadata, dict) else {}

    if not station.get("favicon") or station.get("favicon") == _RADIO_FALLBACK_THUMBNAIL:
        matched_station = _stream_station(station_name, stream_url)
        if matched_station is not None:
            station = matched_station
    thumbnail = station.get("favicon") or _RADIO_FALLBACK_THUMBNAIL

    now = time.monotonic()
    app.radio_station = station
    app.radio_is_playing = True
    app.radio_playback_error = None
    app.radio_playback_started_at = now
    app.radio_metadata_updated_at = now

    song = metadata.get("song") or metadata.get("stream_title") or ""
    if not _usable_track_text(song):
        song = ""
    artist = metadata.get("artist") or ""
    if not _usable_track_text(artist):
        artist = ""
    title = f"{artist} - {song}" if artist and song else song or station_name
    app.radio_now_playing = {
        "title": title,
        "thumbnail": thumbnail,
        "station": station_name,
        "url": stream_url,
        "song": song,
        "artist": artist,
        "favicon": thumbnail,
        "genre": metadata.get("genre") or "",
        "bitrate": metadata.get("bitrate") or "",
        "stream_title": metadata.get("stream_title") or "",
        "metadata_available": metadata.get("metadata_available", False),
    }
    app.radio_metadata_updated_at = time.monotonic()
    return True


def _find_favorite(name):
    favorites = get_radio_config()["favorites"]
    query = name.strip().casefold()
    exact = next((station for station in favorites if station["name"].casefold() == query), None)
    if exact:
        return exact

    partial_matches = [
        station for station in favorites if query and query in station["name"].casefold()
    ]
    if partial_matches:
        return min(partial_matches, key=lambda station: len(station["name"]))

    names = {station["name"].casefold(): station for station in favorites}
    matches = get_close_matches(query, names.keys(), n=1, cutoff=0.55)
    return names[matches[0]] if matches else None


def _play_station(station):
    payload = _station_payload(station)
    if payload is None:
        return None, (jsonify({"error": "A valid radio station URL is required."}), 400)

    current_app.player.suppress_autoplay()
    granted = current_app.playback.acquire("radio", force=True)
    if not granted:
        return None, (jsonify({"error": "Could not acquire radio playback."}), 409)

    current_app.radio_station = payload
    current_app.radio_is_playing = False
    current_app.radio_playback_error = None
    current_app.radio_now_playing = {
        "title": payload["name"],
        "thumbnail": payload["favicon"] or _RADIO_FALLBACK_THUMBNAIL,
        "station": payload["name"],
        "url": payload["url"],
        "song": "",
        "artist": "",
    }

    bose = getattr(current_app.player, "bose", None)
    if bose is None:
        error = "SoundTouch playback is unavailable."
        current_app.radio_playback_error = error
        current_app.radio_playback_started_at = time.monotonic() - _PLAYBACK_START_GRACE_SECONDS
        return None, (jsonify({"error": error}), 503)
    if not bose.play_direct(payload["url"]):
        error = "The SoundTouch speaker could not play this station."
        current_app.radio_playback_error = error
        current_app.radio_playback_started_at = time.monotonic() - _PLAYBACK_START_GRACE_SECONDS
        return None, (jsonify({"error": error}), 502)

    current_app.radio_playback_started_at = time.monotonic()
    current_app.radio_metadata_updated_at = 0

    if payload["stationuuid"]:
        try:
            _radio_browser.click_counter(payload["stationuuid"])
        except Exception:
            logger.warning("Could not register radio station click.", exc_info=True)
    return payload, None


def _play_queued_radio(app, station):  # pydead: keep
    with app.app_context():
        _, error = _play_station(station)
        if error:
            app.logger.error("Could not start queued radio station: %s", error[0].get_json())


@radio_bp.record
def _configure_radio_playback(state):  # pydead: keep
    app = state.app
    app.player.radio_handler = lambda station: _play_queued_radio(app, station)


@radio_bp.route("/")
def index():
    load_error = None
    try:
        stations = _top_stations()
    except Exception:
        logger.exception("Could not load top-voted radio stations.")
        stations = []
        load_error = "Radio stations are temporarily unavailable."

    config = get_radio_config()
    return render_template(
        "radio.html",
        page_title="Musicify Radio",
        api_prefix=url_for("radio.index").rstrip("/"),
        stations=stations,
        radio_presets=config,
        radio_favorites=config["favorites"],
        radio_genres=config["genres"],
        load_error=load_error,
    )


@radio_bp.route("/search")
def search():
    query = request.args.get("q", "").strip()
    tags = [
        tag.strip().lower()
        for tag in request.args.get("tags", "").split(",")
        if tag.strip()
    ]
    if not query and not tags:
        return jsonify([])
    if len(set(tags)) != len(tags) or any(tag not in RADIO_GENRES for tag in tags):
        return jsonify({"error": "Choose a supported radio genre."}), 400

    try:
        search_options = {
            "limit": 18,
            "order": "votes",
            "reverse": True,
            "hidebroken": True,
        }
        if query:
            search_options["name"] = query
        if tags:
            search_options["tag_list"] = ",".join(tags)
        results = _radio_browser.search(**search_options)
    except Exception:
        logger.exception("Radio station search failed.")
        return jsonify({"error": "Radio station search failed."}), 502

    return jsonify([station for item in results if (station := _public_station(item))])


@radio_bp.route("/play", methods=["POST"])
def play():
    data = request.get_json(silent=True) or {}
    station, error = _play_station(data)
    if error:
        return error
    return jsonify({"status": "playing", "station": station})


@radio_bp.route("/<int:number>")
def play_preset(number):
    if number not in range(1, 7):
        return jsonify({"error": "Radio preset must be between 1 and 6."}), 404
    station = get_radio_config().get(str(number))
    if not station:
        return jsonify({"error": f"Radio preset {number} is empty."}), 404

    _, error = _play_station(station)
    if error:
        return error
    return redirect(url_for("radio.index"))


@radio_bp.route("/fav/<path:name>")
def play_favorite_by_name(name):
    station = _find_favorite(name)
    if station is None:
        return jsonify({"error": f"No matching radio favorite found for '{name}'."}), 404

    _, error = _play_station(station)
    if error:
        return error
    return redirect(url_for("radio.index"))


@radio_bp.route("/preset", methods=["POST"])
def save_preset():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Choose a radio station and a preset from 1 to 6."}), 400
    number = data.get("number")
    station = _station_payload(data.get("station"))
    if type(number) is not int or number not in range(1, 7) or station is None:
        return jsonify({"error": "Choose a radio station and a preset from 1 to 6."}), 400

    config = get_radio_config()
    config[str(number)] = station
    try:
        update_user_config("radio", config)
    except (OSError, ValueError):
        logger.exception("Could not save radio preset.")
        return jsonify({"error": "Could not save the radio preset."}), 500
    return jsonify({"status": "saved", "number": number, "station": station})


@radio_bp.route("/preset/<int:number>", methods=["DELETE"])
def delete_preset(number):
    if number not in range(1, 7):
        return jsonify({"error": "Radio preset must be between 1 and 6."}), 404

    config = get_radio_config()
    if config[str(number)] is None:
        return jsonify({"error": f"Radio preset {number} is empty."}), 404
    config[str(number)] = None
    try:
        update_user_config("radio", config)
    except (OSError, ValueError):
        logger.exception("Could not delete radio preset.")
        return jsonify({"error": "Could not delete the radio preset."}), 500
    return jsonify({"status": "deleted", "number": number})


@radio_bp.route("/favorites", methods=["POST"])
def save_favorite():
    data = request.get_json(silent=True)
    station = _station_payload(data.get("station")) if isinstance(data, dict) else None
    if station is None:
        return jsonify({"error": "Choose a valid radio station to favorite."}), 400

    config = get_radio_config()
    existing = next(
        (favorite for favorite in config["favorites"] if favorite["url"] == station["url"]),
        None,
    )
    if existing:
        return jsonify({"status": "already_saved", "station": existing})

    config["favorites"].append(station)
    try:
        update_user_config("radio", config)
    except (OSError, ValueError):
        logger.exception("Could not save radio favorite.")
        return jsonify({"error": "Could not save the radio favorite."}), 500
    return jsonify({"status": "saved", "station": station})


@radio_bp.route("/favorites", methods=["DELETE"])
def delete_favorite():
    data = request.get_json(silent=True)
    station_url = data.get("url") if isinstance(data, dict) else None
    if not isinstance(station_url, str) or not station_url.strip():
        return jsonify({"error": "Choose a radio favorite to delete."}), 400

    config = get_radio_config()
    favorite = next(
        (station for station in config["favorites"] if station["url"] == station_url),
        None,
    )
    if favorite is None:
        return jsonify({"error": "Radio favorite was not found."}), 404

    config["favorites"].remove(favorite)
    try:
        update_user_config("radio", config)
    except (OSError, ValueError):
        logger.exception("Could not delete radio favorite.")
        return jsonify({"error": "Could not delete the radio favorite."}), 500
    return jsonify({"status": "deleted", "station": favorite})


@radio_bp.route("/status")
def status():
    radio_owned = current_app.playback.owner == "radio"
    bose = getattr(current_app.player, "bose", None)
    if bose is None:
        if not radio_owned:
            return jsonify({"is_radio": False})
        return jsonify({"error": "SoundTouch playback is unavailable."}), 503

    with _status_lock:
        try:
            station = getattr(current_app, "radio_station", {}) or {}
            metadata = None
            if radio_owned:
                playing = bose.get_playing_url()
                stream_url = playing.get("url") if playing.get("success") else None
            else:
                metadata = bose.get_stream_metadata()
                stream_url = (
                    metadata.get("url")
                    if isinstance(metadata, dict)
                    and metadata.get("success")
                    and isinstance(metadata.get("url"), str)
                    else None
                )
                if not _is_http_stream_url(stream_url):
                    current_app.radio_is_playing = False
                    current_app.radio_now_playing = None
                    current_app.radio_playback_error = None
                    return jsonify({"is_radio": False})
                stream_url = stream_url.strip()
                station = _station_from_stream_metadata(metadata, stream_url)
                if station is None:
                    current_app.radio_is_playing = False
                    current_app.radio_now_playing = None
                    current_app.radio_playback_error = None
                    return jsonify({"is_radio": False})
                if _confirmed_station_name(metadata, station) is None:
                    current_app.radio_is_playing = False
                    current_app.radio_now_playing = None
                    current_app.radio_playback_error = None
                    return jsonify({"is_radio": False})
                current_app.playback.acquire("radio")
                current_app.radio_playback_started_at = time.monotonic()
                current_app.radio_station = station
                radio_owned = True

            now = time.monotonic()
            if not _same_stream_url(stream_url, station.get("url")):
                if metadata is None:
                    metadata = bose.get_stream_metadata()
                metadata_url = (
                    metadata.get("url")
                    if isinstance(metadata, dict)
                    and metadata.get("success")
                    and isinstance(metadata.get("url"), str)
                    else None
                )
                if metadata_url:
                    stream_url = metadata_url
                else:
                    metadata = None

            if (
                not _same_stream_url(stream_url, station.get("url"))
                and metadata is None
            ):
                if not radio_owned:
                    current_app.radio_is_playing = False
                    current_app.radio_now_playing = None
                    current_app.radio_playback_error = None
                    return jsonify({"is_radio": False})
                current_app.radio_is_playing = False
                pending = (
                    now - getattr(current_app, "radio_playback_started_at", 0)
                    < _PLAYBACK_START_GRACE_SECONDS
                )
                error = None if pending else "The SoundTouch speaker did not start the selected radio station."
                current_app.radio_playback_error = error
                current_app.radio_now_playing = {
                    "title": station.get("name") or "Radio station",
                    "thumbnail": station.get("favicon") or _RADIO_FALLBACK_THUMBNAIL,
                    "station": station.get("name") or "Radio station",
                    "url": station.get("url") or stream_url,
                    "song": "",
                    "artist": "",
                }
                return jsonify({
                    "is_radio": True,
                    "is_playing": False,
                    "pending": pending,
                    "error": error,
                    "station": station,
                })

            current_app.radio_is_playing = True
            current_app.radio_playback_error = None
            updated_at = getattr(current_app, "radio_metadata_updated_at", 0)
            cached = getattr(current_app, "radio_now_playing", None)
            if metadata is not None or (
                stream_url
                and (
                    cached is None
                    or cached.get("url") != stream_url
                    or now - updated_at >= _METADATA_CACHE_SECONDS
                )
            ):
                if metadata is None:
                    metadata = bose.get_stream_metadata(stream_url)
                station = getattr(current_app, "radio_station", {}) or {}
                metadata_station = (
                    metadata.get("station") if isinstance(metadata, dict) else None
                )
                station_name = (
                    metadata_station.strip()
                    if _usable_station_name(metadata_station)
                    else station.get("name") or "Internet Radio"
                )
                if (
                    not station.get("favicon")
                    or station.get("favicon") == _RADIO_FALLBACK_THUMBNAIL
                ):
                    matched_station = _stream_station(station_name, stream_url)
                    if matched_station is not None:
                        station = matched_station
                thumbnail = station.get("favicon") or _RADIO_FALLBACK_THUMBNAIL
                song = (
                    metadata.get("song") or metadata.get("stream_title") or ""
                    if isinstance(metadata, dict)
                    else ""
                )
                if not _usable_track_text(song):
                    song = ""
                artist = metadata.get("artist") or "" if isinstance(metadata, dict) else ""
                if not _usable_track_text(artist):
                    artist = ""
                title = f"{artist} - {song}" if artist and song else song or station_name
                cached = {
                    "title": title,
                    "thumbnail": thumbnail,
                    "station": station_name,
                    "song": song,
                    "artist": artist,
                    "url": stream_url,
                    "favicon": thumbnail,
                    "genre": metadata.get("genre") or "" if isinstance(metadata, dict) else "",
                    "bitrate": metadata.get("bitrate") or "" if isinstance(metadata, dict) else "",
                    "stream_title": metadata.get("stream_title") or "" if isinstance(metadata, dict) else "",
                    "metadata_available": (
                        metadata.get("metadata_available", False)
                        if isinstance(metadata, dict)
                        else False
                    ),
                }
                current_app.radio_station = station
                current_app.radio_now_playing = cached
                current_app.radio_metadata_updated_at = now
            return jsonify({
                "is_radio": True,
                "is_playing": True,
                "station": cached or getattr(current_app, "radio_station", {}),
            })
        except Exception:
            logger.exception("Could not retrieve current radio metadata.")
            return jsonify({"error": "Could not retrieve current radio metadata."}), 502
