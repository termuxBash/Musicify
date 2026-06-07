"""
YouTube Music Routes - Stream YouTube audio via FFmpeg to Bose
"""
import os
import threading
import time
from flask import Blueprint, jsonify, request, render_template, url_for, current_app  # type: ignore
from services.yt_service import YTService
from services.ffmpeg_service import FFmpegService
from core.bose_worker import BoseSoundTouchWorker
import logging
import random
from services.yt_service import YTService
import requests # type: ignore
#from core.lock_manager import ffmpeg_lock
#from core.system_monitor import system_monitor


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Blueprint setup
youtube_bp = Blueprint('youtube', __name__, template_folder='templates')
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# Initialize services
yt_service = YTService()
ffmpeg = FFmpegService()
bose = BoseSoundTouchWorker(
    ip_address=os.getenv("BOSE_IP", "192.168.29.234")
)
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
    ips = [
        os.getenv("STREAM_HOST", "192.168.29.157"),
        "192.168.29.229",
        "127.0.0.1"
    ]
    
    for ip in ips:
        url = f"http://{ip}:8000/mpv.ogg"
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
def enqueue_youtube_result(result):
    """
    Enqueue a YouTube search result into the playback queue.
    Expected keys:
        title
        thumbnail
        videoId
    """

    video_url = (
        f"https://www.youtube.com/watch?v={result['videoId']}"
    )

    stream_url = yt_service.resolve_stream(video_url)

    if not stream_url:
        return False

    return current_app.playback.enqueue(
        "youtube",
        {
            "title": result["title"],
            "thumbnail": result["thumbnail"],
            "url": stream_url
        }
    )


def auto_pick_song(query):

    url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "q": query + " music",
        "type": "video",
        "maxResults": 10,
        "key": YOUTUBE_API_KEY
    }

    res = requests.get(url, params=params).json()

    items = res.get("items", [])

    if not items:
        return None

    bad_words = [
        "live",
        "cover",
        "slowed",
        "reverb",
        "nightcore",
        "8d",
        "remix",
        "bass boosted"
    ]

    best_score = -999
    best_item = None

    for item in items:

        title = item["snippet"]["title"].lower()
        channel = item["snippet"]["channelTitle"].lower()

        score = 0

        # good signals

        if "official" in title:
            score += 5

        if "topic" in channel:
            score += 5

        if "vevo" in channel:
            score += 4

        if "music" in channel:
            score += 2

        # bad signals

        for word in bad_words:
            if word in title:
                score -= 10

        # prefer shorter cleaner titles

        score -= len(title) // 40

        if score > best_score:
            best_score = score
            best_item = item

    if not best_item:
        return None

    return {
        "title": best_item["snippet"]["title"],
        "thumbnail": best_item["snippet"]["thumbnails"]["high"]["url"],
        "videoId": best_item["id"]["videoId"],
        "channel": best_item["snippet"]["channelTitle"]
    }

# ---------- MUSICATLAS RECOMMENDATIONS ----------

def get_musicatlas_recommendations(song_title, limit=5):
    """
    Fetches recommended track objects from the MusicAtlas API based on an input song.
    """
    MUSICATLAS_API_KEY = os.getenv("MUSICATLAS_API_KEY")
    if not MUSICATLAS_API_KEY:
        logger.error("MUSICATLAS_API_KEY environment variable is not set.")
        return []

    # Using MusicAtlas endpoint for single-track or prompt similarity
    url = "https://api.musicatlas.ai/v1/similar_tracks"
    
    headers = {
        "Authorization": f"Bearer {MUSICATLAS_API_KEY}",
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

# ---------------- ROUTES ----------------


@youtube_bp.route('/')
def index():
    return render_template('yt.html', api_prefix=url_for("youtube.index").rstrip("/"))


@youtube_bp.route("/search", methods=["POST"])
def search():

    query = request.form.get("query")

    if not query:
        return jsonify([])

    url = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part": "snippet",
        "q": query + " music",
        "type": "video",
        "maxResults": 12,
        "key": YOUTUBE_API_KEY
    }

    res = requests.get(url, params=params).json()

    results = []

    for item in res.get("items", []):

        results.append({
            "title": item["snippet"]["title"],
            "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
            "videoId": item["id"]["videoId"],
            "channel": item["snippet"]["channelTitle"]
        })

    return jsonify(results)


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

    success = enqueue_youtube_result(song)

    if not success:
        return jsonify({
            "error": "youtube blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "queued"
    })

@youtube_bp.route(
    "/remove_from_queue/<int:index>",
    methods=["POST"]
)
def remove_from_queue(index):

    removed = current_app.playback.remove_from_queue(
        "youtube",
        index
    )

    if removed is None:
        return jsonify({
            "error": "not owner"
        }), 403

    return jsonify({
        "status": "removed",
        "title": removed["title"]
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
    recommended_tracks = get_musicatlas_recommendations(input_song, limit=limit)
    
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
                result = auto_pick_song(track["title"])

            # 3. Stream link resolution and queue execution
            if result:
                success = enqueue_youtube_result(result)
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

# ---------- SKIP ----------

@youtube_bp.route("/skip", methods=["POST"])
def skip():

    global ffmpeg_process

    if ffmpeg_process:
        ffmpeg_process.terminate()

    return jsonify({"status": "skipped"})



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



    

PLAYLIST_DIR = "/opt/radio/playlists"
@youtube_bp.route("/playlist/<name>", methods=["POST"])
def playlist(name):

    path = os.path.join(
        PLAYLIST_DIR,
        f"{name}.txt"
    )

    if not os.path.exists(path):
        return jsonify({
            "error": "playlist not found"
        }), 404

    with open(path, "r") as f:
        songs = [
            x.strip()
            for x in f.readlines()
            if x.strip()
        ]

    if not songs:
        return jsonify({
            "error": "playlist empty"
        }), 400

    if current_app.playback.owner is None:
        current_app.playback.acquire("youtube")

    random.shuffle(songs)

    added = []

    for query in songs:

        try:
            result = auto_pick_song(query)

            if not result:
                continue

            success = enqueue_youtube_result(result)

            if success:
                added.append(result["title"])

        except Exception as e:
            logger.error(
                f"Playlist enqueue failed for '{query}': {e}"
            )

    return jsonify({
        "status": "queued",
        "count": len(added),
        "songs": added
    })