"""
Local Music Routes - Stream local files via FFmpeg to Bose
"""
import os
import threading
import time
from pathlib import Path
from flask import Blueprint, jsonify, request, render_template
from services.ffmpeg_service import FFmpegService
from core.bose_worker import BoseSoundTouchWorker
from core.lock_manager import ffmpeg_lock
from core.system_monitor import system_monitor

# Blueprint setup
local_bp = Blueprint('local', __name__, template_folder='templates')

# Initialize services
ffmpeg = FFmpegService()
bose = BoseSoundTouchWorker(
    ip_address=os.getenv("BOSE_IP", "192.168.29.234")
)

# State management
state = {
    "queue": [],
    "current_file": None,
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
            import requests
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
            
            file_path = state["queue"].pop(0)
            state["current_file"] = file_path
            state["is_playing"] = True
        
        try:
            # Stream to FFmpeg
            ffmpeg.stream_file(file_path)
            time.sleep(2)
            
            # Trigger Bose playback
            bose.trigger_upnp_stream(STREAM_URL)
            
            # Wait for file to finish
            if ffmpeg.process:
                ffmpeg.process.wait(timeout=3600)
        except Exception as e:
            print(f"[Local Streaming] Error: {e}")
        finally:
            ffmpeg.stop()


# Start background worker
worker_thread = threading.Thread(target=stream_worker, daemon=True)
worker_thread.start()


@local_bp.route('/list', methods=['GET'])
def list_local_files():
    """List available local music files"""
    music_dir = os.getenv("MUSIC_DIR", "/home/armbianx96/downloads")
    
    if not os.path.exists(music_dir):
        return jsonify({"error": "Music directory not found"}), 404
    
    files = []
    for file in Path(music_dir).glob("**/*.*"):
        if file.suffix.lower() in ['.mp3', '.flac', '.ogg', '.wav']:
            files.append({
                "name": file.name,
                "path": str(file),
                "size": file.stat().st_size
            })
    
    return jsonify({"files": files})


@local_bp.route('/play/<path:filename>', methods=['POST'])
def play_local(filename):
    """Add a local file to the playback queue"""
    music_dir = os.getenv("MUSIC_DIR", "/home/armbianx96/downloads")
    file_path = os.path.join(music_dir, filename)
    
    # Security check
    if not os.path.isfile(file_path) or not os.path.abspath(file_path).startswith(os.path.abspath(music_dir)):
        return jsonify({"error": "Invalid file path"}), 400
    
    with state["lock"]:
        state["queue"].append(file_path)
    
    return jsonify({
        "status": "queued",
        "file": filename,
        "queue_length": len(state["queue"])
    })


@local_bp.route('/stop', methods=['POST'])
def stop_playback():
    """Stop current playback"""
    ffmpeg.stop()
    with state["lock"]:
        state["queue"].clear()
        state["is_playing"] = False
    
    return jsonify({"status": "stopped"})


@local_bp.route('/status', methods=['GET'])
def get_status():
    """Get playback status"""
    with state["lock"]:
        queue_files = [os.path.basename(f) for f in state["queue"]]
        return jsonify({
            "is_playing": state["is_playing"],
            "current_file": os.path.basename(state["current_file"]) if state["current_file"] else None,
            "queue": queue_files,
            "stream_url": STREAM_URL
        })


@local_bp.route('/', methods=['GET'])
def index():
    """Serve the local music player page"""
    return render_template('local_music.html')


@local_bp.route('/browse', methods=['GET'])
def browse_files():
    """Browse files in a directory"""
    music_dir = os.getenv("MUSIC_DIR", "/home/armbianx96/downloads")
    path = request.args.get('path', '')
    
    # Security check
    full_path = os.path.join(music_dir, path)
    full_path = os.path.abspath(full_path)
    
    if not full_path.startswith(os.path.abspath(music_dir)):
        return jsonify({"error": "Invalid path"}), 400
    
    if not os.path.isdir(full_path):
        return jsonify({"error": "Not a directory"}), 400
    
    items = []
    try:
        for entry in sorted(os.listdir(full_path)):
            entry_path = os.path.join(full_path, entry)
            rel_path = os.path.join(path, entry) if path else entry
            
            is_dir = os.path.isdir(entry_path)
            
            # Skip hidden files
            if entry.startswith('.'):
                continue
            
            # Include directories and audio files
            if is_dir or any(entry.lower().endswith(ext) for ext in ['.mp3', '.flac', '.ogg', '.wav']):
                items.append({
                    "name": entry,
                    "rel_path": rel_path,
                    "is_dir": is_dir
                })
    except Exception as e:
        print(f"[Browse] Error: {e}")
        return jsonify({"error": str(e)}), 500
    
    return jsonify({"items": items})


@local_bp.route('/play', methods=['POST'])
def play_file():
    """Queue a file for playback"""
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({"error": "Path required"}), 400
    
    music_dir = os.getenv("MUSIC_DIR", "/home/armbianx96/downloads")
    file_path = os.path.join(music_dir, data['path'])
    file_path = os.path.abspath(file_path)
    
    # Security check
    if not file_path.startswith(os.path.abspath(music_dir)):
        return jsonify({"error": "Invalid file path"}), 400
    
    if not os.path.isfile(file_path):
        return jsonify({"error": "File not found"}), 404
    
    with state["lock"]:
        state["queue"].append(file_path)
    
    return jsonify({
        "status": "queued",
        "file": os.path.basename(file_path),
        "queue_length": len(state["queue"])
    })


@local_bp.route('/play_folder', methods=['POST'])
def play_folder():
    """Queue all songs in a folder"""
    data = request.get_json()
    if not data or 'path' not in data:
        return jsonify({"error": "Path required"}), 400
    
    music_dir = os.getenv("MUSIC_DIR", "/home/armbianx96/downloads")
    folder_path = os.path.join(music_dir, data['path'])
    folder_path = os.path.abspath(folder_path)
    
    # Security check
    if not folder_path.startswith(os.path.abspath(music_dir)):
        return jsonify({"error": "Invalid folder path"}), 400
    
    if not os.path.isdir(folder_path):
        return jsonify({"error": "Folder not found"}), 404
    
    # Collect all audio files
    audio_files = []
    for file in sorted(Path(folder_path).rglob("*.*")):
        if file.suffix.lower() in ['.mp3', '.flac', '.ogg', '.wav']:
            audio_files.append(str(file))
    
    with state["lock"]:
        state["queue"].extend(audio_files)
    
    return jsonify({
        "status": "queued",
        "count": len(audio_files),
        "queue_length": len(state["queue"])
    })


@local_bp.route('/toggle', methods=['POST'])
def toggle_playback():
    """Toggle play/pause (simplified for local player)"""
    # In a real implementation, this would pause/resume FFmpeg
    return jsonify({"status": "toggled"})
