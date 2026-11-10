"""#yt/rotes.py - Flask routes for Online Music integration
Music Routes - Stream audio via FFmpeg to Bose
"""
import threading
import os
import re
from flask import Blueprint, jsonify, redirect, request, render_template, url_for, current_app  # type: ignore
from services.yt_service import YTService
from core.history_db import normalize_video_id, is_blacklisted_rick_roll, record_rick_roll_timestamp
from core.settings import (
    DB_FILENAME,
    MUSIC_ATLAS_KEY,
    LASTFM_KEY,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    PLAYLIST_DIR,
    SONG_NAME_CLEANUP,
)
import logging
import random
import requests # type: ignore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Allowlist pattern: only alphanumeric + underscores, must not be empty
_TABLE_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')


def _validate_table_name(name):
    """Return sanitised table name or raise ValueError if invalid.

    Called at route boundaries so malformed names are rejected with a
    400 response before they can reach any SQL-executing code.
    """
    from core.history_db import sanitize_table_name
    clean = sanitize_table_name(str(name).strip()) if name else ""
    if not clean or not _TABLE_NAME_RE.fullmatch(clean):
        raise ValueError(f"Invalid table name: {name!r}")
    return clean

# Blueprint setup
youtube_bp = Blueprint('youtube', __name__, template_folder='templates')


# ---------------- DISPLAY ----------------


# ---------------- LYRICS ----------------


# ---------- PLAYBACK CONTROL ----------

def resolve_track(query, api_key):
    url = "https://ws.audioscrobbler.com/2.0/"

    params = {
        "method": "track.search",
        "track": query,
        "api_key": api_key,
        "format": "json",
        "limit": 5
    }

    resp = requests.get(url, params=params, timeout=10)
    data = resp.json()

    matches = (
        data.get("results", {})
            .get("trackmatches", {})
            .get("track", [])
    )

    if not matches:
        return None, None

    if isinstance(matches, dict):
        matches = [matches]

    best = matches[0]

    artist = best.get("artist", "")
    track = best.get("name", "")

    # 🔥 HARD SANITIZATION (important fix)
    artist = artist.split(" - ")[0].strip()
    track = track.split(" - ")[-1].strip()

    return artist, track

def clean_track_name(track):
    junk_patterns = [
        "(official music video)", "(official video)", "(official audio)",
        "[official video]", "[lyrics]", "(lyrics)", "(official)",
        "music video", "official video", "video"
    ]

    t = track.lower()
    for p in junk_patterns:
        t = t.replace(p, "")

    return " ".join(t.split()).strip()


def get_lastfm_recommendations(query):
    """
    Fully unified recommender:
    - parses artist/track if possible
    - otherwise resolves via search
    - then fetches similar tracks
    """

    if not LASTFM_KEY:
        logger.error("Missing LASTFM_KEY")
        return []

    # -------------------------
    # 1. Try direct parsing
    # -------------------------
    seed_artist, seed_track = resolve_track(query, LASTFM_KEY)

    if seed_track:
        seed_track = clean_track_name(seed_track)

    # -------------------------
    # 2. If incomplete → resolve via API
    # -------------------------
    if not seed_artist or not seed_track:
        logger.info(f"Resolving track via search: {query}")
        seed_artist, seed_track = resolve_track(query, LASTFM_KEY)

    if not seed_artist or not seed_track:
        logger.warning("Could not resolve track/artist.")
        return []

    logger.info(f"Seed resolved → Artist: {seed_artist} | Track: {seed_track}")

    # -------------------------
    # 3. Get similar tracks
    # -------------------------
    url = "https://ws.audioscrobbler.com/2.0/"

    params = {
        "method": "track.getsimilar",
        "artist": seed_artist,
        "track": seed_track,
        "api_key": LASTFM_KEY,
        "format": "json",
        "limit": 100,
        "autocorrect": 1
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()

        sim_tracks = data.get("similartracks", {}).get("track", [])

        if not sim_tracks:
            logger.warning("No similar tracks found.")
            return []

        same_artist = []
        diff_artist = []

        for t in sim_tracks:
            track_name = t.get("name", "").strip()
            artist_name = t.get("artist", {}).get("name", "").strip()

            if not track_name or not artist_name:
                continue

            if track_name.lower() == seed_track.lower():
                continue

            item = {
                "title": f"{artist_name} - {track_name}",
                "videoId": None
            }

            if artist_name.lower() == seed_artist.lower():
                same_artist.append(item)
            else:
                diff_artist.append(item)

        recommendations = []

        # 2 from same artist
        if same_artist:
            recommendations.extend(random.sample(same_artist, min(2, len(same_artist))))

        # 1 from different artist
        if diff_artist:
            recommendations.append(random.choice(diff_artist))

        # fallback fill
        pool = same_artist + diff_artist
        while len(recommendations) < 3 and pool:
            pick = random.choice(pool)
            if pick not in recommendations:
                recommendations.append(pick)

        return recommendations[:3]

    except Exception as e:
        logger.error(f"Recommendation failed: {e}")
        return []


def get_gemini_recommendations(user_prompt=""):
    """Return three YouTube song recommendations based on playback history."""
    if not GEMINI_API_KEY:
        logger.error("Missing GEMINI_API_KEY")
        return []

    try:
        from core.history_db import get_history_text
        history = get_history_text().strip()
    except Exception as error:
        logger.error(f"Failed to read playback history: {error}")
        return []

    cleanup_pattern = re.compile(
        r"(?:^|[\s\[\(\-])(?:"
        + "|".join(re.escape(term) for term in sorted(SONG_NAME_CLEANUP, key=len, reverse=True))
        + r")(?:$|[\s\]\)\-])",
        re.IGNORECASE,
    )

    history_entries = []
    for line_number, line in enumerate(history.splitlines()):
        parts = line.split(">")
        if len(parts) < 3:
            continue
        song_name, play_count = parts[0].strip(), parts[2].strip()
        timestamp = 0
        if len(parts) >= 4:
            ts_str = parts[3].strip()
            try:
                timestamp = int(ts_str)
            except ValueError:
                try:
                    timestamp = int(ts_str, 16)
                except ValueError:
                    pass

        cleaned_name = cleanup_pattern.sub(" ", song_name)
        cleaned_name = re.sub(r"\s+", " ", cleaned_name).strip(" -")
        if cleaned_name and play_count.strip():
            history_entries.append((timestamp, line_number, f"{cleaned_name}>{play_count}"))

    history_entries.sort(key=lambda entry: (entry[0], entry[1]))
    cleaned_history_lines = [entry[2] for entry in history_entries]

    cleaned_history = "\n".join(cleaned_history_lines)
    prompt = f"""Recommend exactly 3 songs based on this playback history.
The history uses one entry per line in this exact compact format:
Song Name>PlayCount
Use the song names and play counts
as context, then find the best matching YouTube video IDs yourself.

Playback history:
{cleaned_history or "(no playback history yet)"}

{user_prompt.strip()}

Respond with exactly 3 lines and no markdown, numbering, or explanation.
Each line must use this format and contain no other > characters:
SongName>YouTubeVideoId
YouTubeVideoId must be a valid YouTube video ID."""

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )
    def build_payload(include_tools):
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
            },
        }
        if include_tools:
            payload["tools"] = [{"googleSearch": {}}]
        return payload

    try:
        response = requests.post(
            endpoint,
            params={"key": GEMINI_API_KEY},
            json=build_payload(include_tools=True),
            timeout=20,
        )

        if response.status_code == 429:
            logger.warning("Gemini request with Google Search was rate limited; retrying without tools")
            response = requests.post(
                endpoint,
                params={"key": GEMINI_API_KEY},
                json=build_payload(include_tools=False),
                timeout=20,
            )

        response.raise_for_status()
        # print("Gemini response:", response.text)  # Debugging output
        response_json = response.json()
        response_text = response_json["candidates"][0]["content"]["parts"][0]["text"]
        recommendations = []
        for line in response_text.splitlines():
            song_name, song_id = _parse_gemini_recommendation(line)
            if song_name and song_id:
                recommendations.append({
                    "song_name": song_name,
                    "song_id": song_id,
                })

    except (requests.RequestException, KeyError, IndexError, TypeError) as error:
        logger.error(
            "Gemini recommendation request failed: %s; response=%s",
            error,
            getattr(error.response, "text", None),
        )
        return []


    if not isinstance(recommendations, list) or len(recommendations) != 3:
        logger.error("Gemini returned an invalid recommendation count")
        return []

    validated = []
    for recommendation in recommendations:
        if not isinstance(recommendation, dict):
            return []
        if set(recommendation) != {"song_name", "song_id"}:
            return []
        if not all(isinstance(recommendation[field], str) and recommendation[field].strip()
                   for field in ("song_name", "song_id")):
            return []
        validated.append({
            "song_name": recommendation["song_name"].strip(),
            "song_id": recommendation["song_id"].strip(),
        })

    return validated


def _parse_gemini_recommendation(line):
    """Parse a Gemini recommendation with either delimiter-separated or spaced IDs."""
    text = line.strip()
    if not text:
        return "", ""

    if ">" in text:
        song_name, separator, song_id = text.partition(">")
        if not separator or ">" in song_id:
            return "", ""
        song_name = song_name.strip()
        song_id = song_id.strip()
    else:
        match = re.search(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", text)
        if not match:
            return "", ""
        song_name = text[:match.start()].rstrip(" -:")
        song_id = match.group(1)

    song_id = normalize_video_id(song_id)
    if not song_name or not song_id:
        return "", ""
    return song_name, song_id


def _queue_ai_recommendations(app, recommendations):
    try:
        with app.app_context():
            record_history = not app.incogni_mode

            for recommendation in recommendations:
                song_name = recommendation["song_name"]
                song_id = recommendation["song_id"]

                if is_blacklisted_rick_roll(song_id, song_name):
                    logger.warning(f"AI recommendation has blacklisted ID {song_id} for '{song_name}'")
                    record_rick_roll_timestamp()
                    picked = YTService.auto_pick_song(song_name)
                    if (
                        picked
                        and picked.get("videoId")
                        and not is_blacklisted_rick_roll(picked.get("videoId"), picked.get("title"))
                    ):
                        result = picked
                    else:
                        continue
                else:
                    result = {
                        "title": song_name,
                        "videoId": song_id,
                        "thumbnail": f"https://img.youtube.com/vi/{song_id}/hqdefault.jpg",
                    }

                try:
                    success = YTService.enqueue_youtube_result(
                        result,
                        record_history=record_history,
                    )
                except Exception as direct_error:
                    logger.warning(
                        f"AI recommendation ID failed for '{result['title']}', searching by title: {direct_error}"
                    )
                    result = YTService.auto_pick_song(result["title"])
                    if not result:
                        continue
                    try:
                        success = YTService.enqueue_youtube_result(
                            result,
                            record_history=record_history,
                        )
                    except Exception as fallback_error:
                        logger.error(
                            f"Failed to queue AI title fallback '{result['title']}': {fallback_error}"
                        )
                        continue

                if not success:
                    logger.warning(f"AI recommendation was not queued: {result['title']}")
    finally:
        app.player.resume_autoplay()


@youtube_bp.route("/ai_recommendations", methods=["POST"])
def ai_recommendations():
    data = request.get_json(silent=True) or {}
    user_prompt = (data.get("user_prompt") or "").strip()

    if not user_prompt:
        return jsonify({"error": "prompt required"}), 400

    current_app.player.suppress_autoplay()
    recommendations = get_gemini_recommendations(user_prompt)
    if not recommendations:
        current_app.player.resume_autoplay()
        return jsonify({"error": "AI recommendations unavailable"}), 502

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")
    if current_app.playback.owner != "youtube":
        current_app.player.resume_autoplay()
        return jsonify({
            "error": "youtube blueprint does not own player",
            "owner": current_app.playback.owner,
        }), 403

    app = current_app._get_current_object()
    try:
        threading.Thread(
            target=_queue_ai_recommendations,
            args=(app, recommendations),
            daemon=True,
        ).start()
    except Exception:
        current_app.player.resume_autoplay()
        logger.exception("Failed to start AI recommendation queue worker")
        return jsonify({"error": "AI recommendations unavailable"}), 502

    return jsonify({
        "status": "queueing",
        "count": len(recommendations),
    })
# ---------------- ROUTES ----------------


@youtube_bp.route('/')
def index():
    return render_template('yt.html', api_prefix=url_for("youtube.index").rstrip("/"))


# ---------------- HISTORY ROUTES ----------------

@youtube_bp.route('/hist')
@youtube_bp.route('/hist/')
def history_manager():
    from core.history_db import get_all_tables, get_active_table, get_history_rows, create_table
    tables = get_all_tables()
    active_table = get_active_table()
    requested_table = request.args.get("table", "").strip()
    if requested_table:
        try:
            current_table = _validate_table_name(requested_table)
        except ValueError:
            return jsonify({"error": "Invalid table name"}), 400
    else:
        current_table = active_table

    if current_table not in tables:
        create_table(current_table)
        tables = get_all_tables()

    rows = get_history_rows(current_table)
    return render_template(
        'hist.html',
        database_filename=DB_FILENAME,
        tables=tables,
        current_table=current_table,
        active_table=active_table,
        rows=rows,
        api_prefix=url_for("youtube.index").rstrip("/")
    )


@youtube_bp.route('/hist/api/update', methods=['POST'])
def hist_update_record():
    from core.history_db import update_entry
    data = request.get_json(silent=True) or {}
    row_id = data.get("id")
    song_name = data.get("song_name", "").strip()
    song_id = data.get("song_id", "").strip()
    number_of_listens = data.get("number_of_listens", 1)
    datetime_str = data.get("datetime_str")

    if not data.get("table") or not row_id or not song_name or not song_id:
        return jsonify({"error": "Missing required fields"}), 400
    song_id = normalize_video_id(song_id)
    if not song_id:
        return jsonify({"error": "Song ID must be an 11-character YouTube video ID"}), 400

    try:
        table = _validate_table_name(data.get("table"))
    except ValueError:
        return jsonify({"error": "Invalid table name"}), 400

    success = update_entry(
        table_name=table,
        row_id=row_id,
        song_name=song_name,
        song_id=song_id,
        number_of_listens=number_of_listens,
        datetime_str=datetime_str
    )
    if success:
        return jsonify({"status": "success"})
    return jsonify({"error": "Record not found or update failed"}), 404


@youtube_bp.route('/hist/api/delete', methods=['POST'])
def hist_delete_record():
    from core.history_db import delete_entry
    data = request.get_json(silent=True) or {}
    row_id = data.get("id")

    if not data.get("table") or not row_id:
        return jsonify({"error": "Table and row ID required"}), 400

    try:
        table = _validate_table_name(data.get("table"))
    except ValueError:
        return jsonify({"error": "Invalid table name"}), 400

    success = delete_entry(table, row_id)
    if success:
        return jsonify({"status": "success"})
    return jsonify({"error": "Delete failed"}), 404


@youtube_bp.route('/hist/api/add', methods=['POST'])
def hist_add_record():
    from core.history_db import add_entry
    data = request.get_json(silent=True) or {}
    song_name = data.get("song_name", "").strip()
    song_id = data.get("song_id", "").strip()
    number_of_listens = data.get("number_of_listens", 1)
    datetime_str = data.get("datetime_str")

    if not data.get("table") or not song_name or not song_id:
        return jsonify({"error": "Table, song name, and song ID required"}), 400
    song_id = normalize_video_id(song_id)
    if not song_id:
        return jsonify({"error": "Song ID must be an 11-character YouTube video ID"}), 400

    try:
        table = _validate_table_name(data.get("table"))
    except ValueError:
        return jsonify({"error": "Invalid table name"}), 400

    new_id = add_entry(
        table_name=table,
        song_name=song_name,
        song_id=song_id,
        number_of_listens=number_of_listens,
        datetime_str=datetime_str
    )
    return jsonify({"status": "success", "id": new_id})


@youtube_bp.route('/hist/api/set_active', methods=['POST'])
def hist_set_active():
    from core.history_db import set_active_table
    data = request.get_json(silent=True) or {}
    if not data.get("table_name", "").strip():
        return jsonify({"error": "Table name required"}), 400

    try:
        table_name = _validate_table_name(data.get("table_name"))
    except ValueError:
        return jsonify({"error": "Invalid table name"}), 400

    active = set_active_table(table_name)
    return jsonify({"status": "success", "active_table": active})


@youtube_bp.route('/hist/api/create_table', methods=['POST'])
def hist_create_table():
    from core.history_db import create_table
    data = request.get_json(silent=True) or {}
    if not data.get("table_name", "").strip():
        return jsonify({"error": "Table name required"}), 400

    try:
        table_name = _validate_table_name(data.get("table_name"))
    except ValueError:
        return jsonify({"error": "Invalid table name — use only letters, digits and underscores"}), 400

    clean = create_table(table_name)
    return jsonify({"status": "success", "table": clean})


@youtube_bp.route('/hist/api/delete_table', methods=['POST'])
def hist_delete_table():
    from core.history_db import delete_table
    data = request.get_json(silent=True) or {}
    raw = data.get("table_name", "").strip()
    if not raw or raw == "history":
        return jsonify({"error": "Cannot delete default history table"}), 400

    try:
        table_name = _validate_table_name(raw)
    except ValueError:
        return jsonify({"error": "Invalid table name"}), 400

    success = delete_table(table_name)
    if success:
        return jsonify({"status": "success"})
    return jsonify({"error": "Delete table failed"}), 400


@youtube_bp.route("/<filename>")
def switch_history_file(filename):
    filename = filename.strip()
    if not filename or os.path.basename(filename) != filename:
        return jsonify({"error": "file must be a filename"}), 400

    try:
        validated = _validate_table_name(filename)
    except ValueError:
        return jsonify({"error": "Invalid filename — use only letters, digits and underscores"}), 400

    from core.history_db import set_active_table, create_table
    table = set_active_table(validated)
    create_table(table)
    return redirect(url_for("youtube.index").rstrip("/"))


@youtube_bp.route("/search", methods=["POST"])
def search():
    query = request.form.get("query")
    if not query:
        return jsonify([])

    # Fetch data using our new robust key rotation function
    res, status_code = YTService.get_youtube_search_results(query, max_results=12)

    if status_code != 200:
        return jsonify(res if res else {"error": "Lookup failed"}), status_code

    results = []
    for item in res.get("items", []):
        results.append({
            "title": item["snippet"]["title"],
            "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
            "videoId": item["id"]["videoId"],
            "channel": item["snippet"]["channelTitle"]
        })

    return jsonify(results)
@youtube_bp.route("/auto_pick", methods=["POST"])
def auto_pick():
    query = request.form.get("query")
    if not query:
        return jsonify({"error": "query required"}), 400

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")

    result = YTService.auto_pick_song(query)
    if not result:
        return jsonify({"error": "No suitable song found"}), 404

    success = YTService.enqueue_youtube_result(result)
    if not success:
        return jsonify({
            "error": "youtube blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "queued",
        "song": result
    })

@youtube_bp.route("/acquire", methods=["POST"])
def acquire():

    data = request.get_json(silent=True) or {}

    force = data.get("force", False)

    granted = current_app.playback.acquire(
        "youtube",
        force=force
    )

    return jsonify({
        "granted": granted,
        "owner": current_app.playback.owner
    })

# ---------- QUEUE ----------

def _enqueue_song(add_to_history):

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")

    song = request.get_json()

    try:
        success = YTService.enqueue_youtube_result(song, record_history=add_to_history)
    except Exception as error:
        logger.warning("YouTube video could not be resolved during queueing: %s", error)
        return jsonify({
            "error": "YouTube video is unavailable or could not be resolved"
        }), 422

    if not success:
        return jsonify({
            "error": "youtube blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "queued"
    })


@youtube_bp.route("/enqueue", methods=["POST"])
def enqueue():
    return _enqueue_song(add_to_history=True)


@youtube_bp.route("/enqueue_incognito", methods=["POST"])
def enqueue_without_history():
    return _enqueue_song(add_to_history=False)
