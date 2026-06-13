""" yt/rotes.py - Flask routes for YouTube Music integration
YouTube Music Routes - Stream YouTube audio via FFmpeg to Bose
"""
import os
import subprocess
import threading
import time
from flask import Blueprint, jsonify, request, render_template, url_for, current_app  # type: ignore
from services.yt_service import YTService
from services.ffmpeg_service import FFmpegService
from core.bose_worker import BoseSoundTouchWorker
from core.settings import BOSE_IP, PLAYLIST_DIR, STREAM_FALLBACK_URLS, STREAM_URL
import logging
import random
import requests # type: ignore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Blueprint setup
youtube_bp = Blueprint('youtube', __name__, template_folder='templates')
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
LASTFM_KEY = os.getenv("LASTFM_KEY")

# Initialize services
ffmpeg = FFmpegService()
bose = BoseSoundTouchWorker(ip_address=BOSE_IP)
# ---------------- DISPLAY ----------------

def show_lyric(text):
    try:
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



current_song = None
ffmpeg_process = None

# State management
state = {
    "queue": [],
    "queue_titles": [],
    "current_url": None,
    "current_title": None,
    "is_playing": False,
    "lock": threading.Lock()
}


def get_stream_url():
    """Detect local Icecast stream URL"""
    import socket
    candidate_urls = [STREAM_URL, *STREAM_FALLBACK_URLS]

    for url in candidate_urls:
        try:
            import requests # type: ignore
            r = requests.get(url, timeout=1, stream=True)
            if r.status_code == 200:
                return url
        except:
            pass
    
    # Fallback to local auto-detect
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return f"http://{local_ip}:8000/mpv.ogg"
    except:
        return "http://127.0.0.1:8000/mpv.ogg"


STREAM_URL = get_stream_url()

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
# ---------------- ROUTES ----------------


@youtube_bp.route('/')
def index():
    return render_template('yt.html', api_prefix=url_for("youtube.index").rstrip("/"))


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
    MUSIC_ATLAS_KEY = os.getenv("MUSIC_ATLAS_KEY")
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

@youtube_bp.route('/play', methods=['POST'])
def play_youtube():
    """Queue a YouTube URL for playback"""
    data = request.get_json()
    youtube_url = data.get('url') if data else None
    title = data.get('title', 'Unknown') if data else 'Unknown'
    
    if not youtube_url:
        return jsonify({"error": "URL required"}), 400
    
    with state["lock"]:
        state["queue"].append(youtube_url)
        state["queue_titles"].append(title)
    
    return jsonify({
        "status": "queued",
        "url": youtube_url,
        "title": title,
        "queue_length": len(state["queue"])
    })


@youtube_bp.route('/stop', methods=['POST'])
def stop_playback():
    """Stop current playback"""
    ffmpeg.stop()
    with state["lock"]:
        state["queue"].clear()
        state["queue_titles"].clear()
        state["is_playing"] = False
    
    return jsonify({"status": "stopped"})


@youtube_bp.route('/toggle', methods=['POST'])
def toggle_playback():
    """Toggle play/pause (simplified for YouTube player)"""
    return jsonify({"status": "toggled"})

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

@youtube_bp.route("/enqueue", methods=["POST"])
def enqueue():

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")

    song = request.get_json()

    success = YTService.enqueue_youtube_result(song)

    if not success:
        return jsonify({
            "error": "youtube blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "queued"
    })

@youtube_bp.route("/recommend_and_enqueue", methods=["POST"])
def recommend_and_enqueue():
    """
    Route to recommend songs based on an input query and inject them into the queue.
    Uses Direct YouTube ID from MusicAtlas if present; falls back to search if not.
    """
    data = request.get_json() or {}
    input_song = data.get("song")
    limit = data.get("limit", 5)

    if not input_song:
        return jsonify({"error": "Input 'song' string is required"}), 400

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")

    # 1. Fetch recommendations from MusicAtlas
    recommended_tracks = get_lastfm_recommendations(input_song)
    
    if not recommended_tracks:
        return jsonify({"error": "No recommendations found or API error"}), 404

    added_songs = []

    # 2. Process recommendations
    for track in recommended_tracks:
        try:
            result = None
            
            # PATH A: Direct match found in MusicAtlas metadata
            if track["videoId"]:
                logger.info(f"Direct YouTube ID found via MusicAtlas for: {track['title']}")
                result = {
                    "title": track["title"],
                    "videoId": track["videoId"],
                    "thumbnail": f"https://img.youtube.com/vi/{track['videoId']}/hqdefault.jpg"
                }
            
            # PATH B: Fallback to your heuristic search engine
            else:
                logger.warning(f"No YouTube ID in MusicAtlas data for: {track['title']}. Falling back to search.")
                result = YTService.auto_pick_song(track["title"])

            # 3. Stream link resolution and queue execution
            if result:
                success = YTService.enqueue_youtube_result(result)
                if success:
                    added_songs.append({
                        "title": result["title"],
                        "videoId": result["videoId"]
                    })

        except Exception as e:
            logger.error(f"Recommendation handling failed for '{track.get('title')}': {e}")

    return jsonify({
        "status": "completed",
        "input_song": input_song,
        "count_requested": len(recommended_tracks),
        "count_enqueued": len(added_songs),
        "enqueued_songs": added_songs
    })




@youtube_bp.route('/status', methods=['GET'])
def get_status():
    """Get playback status"""
    with state["lock"]:
        return jsonify({
            "is_playing": state["is_playing"],
            "current_url": state["current_url"],
            "current_title": state["current_title"],
            "queue": state["queue_titles"][:5],
            "stream_url": STREAM_URL
        })



    