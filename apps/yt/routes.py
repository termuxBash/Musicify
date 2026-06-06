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


def stream_worker():
    """Background worker to manage FFmpeg streaming to Bose"""
    while True:
        with state["lock"]:
            if not state["queue"]:
                state["is_playing"] = False
                time.sleep(1)
                continue
            
            youtube_url = state["queue"].pop(0)
            state["current_url"] = youtube_url
            if state["queue_titles"]:
                state["current_title"] = state["queue_titles"].pop(0)
            state["is_playing"] = True
        
        try:
            # Resolve YouTube stream URL
            stream_url = yt_service.resolve_stream(youtube_url)
            
            if not stream_url:
                print("[YouTube] Failed to resolve stream URL")
                continue
            
            # Stream to FFmpeg
            ffmpeg.stream_url(stream_url)
            time.sleep(2)
            
            # Trigger Bose playback
            bose.trigger_upnp_stream(STREAM_URL)
            
            # Wait for stream to finish
            if ffmpeg.process:
                ffmpeg.process.wait(timeout=3600)
        except Exception as e:
            print(f"[YouTube Streaming] Error: {e}")
        finally:
            ffmpeg.stop()


# Start background worker
#worker_thread = threading.Thread(target=stream_worker, daemon=True)
#worker_thread.start()


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

    video_url = (
        f"https://www.youtube.com/watch?v={song['videoId']}"
    )

    url = yt_service.resolve_stream(video_url)

    success = current_app.playback.enqueue(
        "youtube",
        {
            "title": song["title"],
            "thumbnail": song["thumbnail"],
            "url": url
        }
    )

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
    

PLAYLIST_DIR = "/opt/radio/playlists"
@youtube_bp.route("/playlist/<name>", methods=["POST"])
def playlist(name):

    path = os.path.join(PLAYLIST_DIR, f"{name}.txt")

    if not os.path.exists(path):
        return jsonify({"error": "playlist not found"}), 404

    with open(path, "r") as f:
        songs = [x.strip() for x in f.readlines() if x.strip()]

    if len(songs) == 0:
        return jsonify({"error": "playlist empty"}), 400

    random.shuffle(songs)

    added = []

    for query in songs:

        try:

            result = auto_pick_song(query)

            if result:
                current_app.config["song_queue"].append(result)
                added.append(result["title"])

        except Exception as e:
            logger.error("Playlist enqueue failed: " + str(e))

    return jsonify({
        "status": "queued",
        "count": len(added),
        "songs": added
    })
