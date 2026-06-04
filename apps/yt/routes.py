"""
YouTube Music Routes - Stream YouTube audio via FFmpeg to Bose
"""
import os
import threading
import time
from flask import Blueprint, jsonify, request, render_template # type: ignore
from services.yt_service import YTService
from services.ffmpeg_service import FFmpegService
from core.bose_worker import BoseSoundTouchWorker
#from core.lock_manager import ffmpeg_lock
#from core.system_monitor import system_monitor









# Blueprint setup
youtube_bp = Blueprint('youtube', __name__, template_folder='templates')

# Initialize services
yt_service = YTService()
ffmpeg = FFmpegService()
bose = BoseSoundTouchWorker(
    ip_address=os.getenv("BOSE_IP", "192.168.29.234")
)

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
worker_thread = threading.Thread(target=stream_worker, daemon=True)
worker_thread.start()


@youtube_bp.route('/')
def index():
    return render_template('yt.html')


@youtube_bp.route('/search', methods=['POST'])
def search_youtube():
    """Search YouTube and return multiple results"""
    data = request.get_json()
    query = data.get('query') if data else None
    
    if not query:
        return jsonify({"error": "Query required"}), 400
    
    try:
        from yt_dlp import YoutubeDL
        with YoutubeDL({
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True
        }) as ydl:
            search_results = ydl.extract_info(f"ytsearch10:{query}", download=False)
            if search_results and 'entries' in search_results:
                results = []
                for entry in search_results['entries']:
                    results.append({
                        "title": entry.get('title', 'Unknown'),
                        "url": f"https://www.youtube.com/watch?v={entry['id']}",
                        "duration": entry.get('duration', 0)
                    })
                return jsonify({"results": results})
        
        return jsonify({"error": "No results found"}), 404
    except Exception as e:
        print(f"[YouTube Search] Error: {e}")
        return jsonify({"error": str(e)}), 500


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

