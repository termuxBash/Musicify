"""#yt/rotes.py - Flask routes for Online Music integration
Music Routes - Stream audio via FFmpeg to Bose
"""
import threading
import os
import re
from flask import Blueprint, jsonify, redirect, request, render_template, url_for, current_app  # type: ignore
from services.yt_service import YTService
from core.settings import (
    MUSIC_ATLAS_KEY,
    LASTFM_KEY,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    get_history_file,
    PLAYLIST_DIR,
    set_history_file,
    SONG_NAME_CLEANUP,
)
import logging
import random
import requests # type: ignore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Blueprint setup
youtube_bp = Blueprint('youtube', __name__, template_folder='templates')


def _safe_path(base_dir, relative_path):
    base_path = os.path.realpath(base_dir)
    candidate_path = os.path.realpath(os.path.join(base_path, relative_path))

    try:
        is_within_base = os.path.commonpath([base_path, candidate_path]) == base_path
    except ValueError:
        is_within_base = False

    if not is_within_base:
        raise ValueError("path escapes its base directory")

    return candidate_path


# ---------------- DISPLAY ----------------

def show_lyric(text):
    try:
        import subprocess
        subprocess.Popen(["python3", "display.py", text])
    except Exception as e:
        print("DISPLAY ERROR:", e)


# ---------------- LYRICS ----------------

def fetch_synced_lyrics(title):
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"q": title},
            timeout=10,
            verify=False
        )

        data = r.json()
        if not data:
            return []

        synced = data[0].get("syncedLyrics")
        if not synced:
            return []

        parsed = []

        for line in synced.splitlines():
            if not line.startswith("["):
                continue
            try:
                ts = line.split("]")[0][1:]
                lyric = line.split("]")[1]

                mins, secs = ts.split(":")
                total = int(mins) * 60 + float(secs)

                parsed.append((total, lyric))
            except:
                pass

        return parsed

    except Exception as e:
        logger.error("Lyrics fetch failed: " + str(e))
        return []



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

def parse_artist_track(text):
    """Light heuristic parser for explicit formats."""
    clean = text.lower()

    junk_patterns = [
        "(official music video)", "(official video)", "(official audio)",
        "[official video]", "[lyrics]", "(lyrics)", "(official)",
        "music video", "official video", "video"
    ]

    for p in junk_patterns:
        clean = clean.replace(p, "")

    original = text

    if " - " in original:
        a, t = original.split(" - ", 1)
        return a.strip(), t.strip()

    if " by " in clean:
        idx = clean.find(" by ")
        return original[:idx].strip(), original[idx + 4:].strip()

    return None, None


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
        history_path = _safe_path(PLAYLIST_DIR, get_history_file())
    except ValueError:
        logger.error("Invalid playback history path")
        return []

    try:
        with open(history_path, "r", encoding="utf8") as history_file:
            history = history_file.read().strip()
    except FileNotFoundError:
        history = ""
    except OSError as error:
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
            try:
                timestamp = int(parts[3].strip(), 16)
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
            song_name, separator, song_id = line.strip().partition(">")
            if separator and song_name.strip() and song_id.strip() and ">" not in song_id:
                recommendations.append({
                    "song_name": song_name.strip(),
                    "song_id": song_id.strip(),
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


def _queue_ai_recommendations(app, recommendations):
    try:
        with app.app_context():
            record_history = not app.incogni_mode

            for recommendation in recommendations:
                result = {
                    "title": recommendation["song_name"],
                    "videoId": recommendation["song_id"],
                    "thumbnail": f"https://img.youtube.com/vi/{recommendation['song_id']}/hqdefault.jpg",
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


@youtube_bp.route("/<filename>")
def switch_history_file(filename):
    filename = filename.strip()
    if not filename or os.path.basename(filename) != filename:
        return jsonify({"error": "file must be a filename"}), 400

    set_history_file(filename)
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
def get_musicatlas_recommendations(song_title, limit=5):
    """
    Fetches recommended track objects from the MusicAtlas API based on an input song.
    """
    if not MUSIC_ATLAS_KEY:
        logger.error("MUSIC_ATLAS_KEY environment variable is not set.")
        return []

    # Using MusicAtlas endpoint for single-track or prompt similarity
    url = "https://api.musicatlas.ai/v1/similar_tracks"

    headers = {
        "Authorization": f"Bearer {MUSIC_ATLAS_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "text": song_title,
        "limit": limit
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)

        if response.status_code != 200:
            logger.error(f"MusicAtlas API error: {response.status_code} - {response.text}")
            return []

        data = response.json()
        recommendations = []

        # Iterate through the returned recommendations map
        for track in data.get("tracks", []):
            title = track.get("title", "Unknown Track")
            artists = track.get("artists", [])
            artist_name = artists[0].get("name") if artists else "Unknown Artist"

            # Extract YouTube videoId safely from platform mappings if present
            platform_ids = track.get("platform_ids", {})
            youtube_id = platform_ids.get("youtube")

            recommendations.append({
                "title": f"{artist_name} - {title}",
                "videoId": youtube_id # Could be None if unavailable
            })

        return recommendations

    except Exception as e:
        logger.error(f"Failed to fetch MusicAtlas recommendations: {e}")
        return []

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
