"""#local/routes.py - Flask routes for local file browsing and playback
"""

import os
import random
from core.settings import ROOT_DIR
from flask import Blueprint, current_app, jsonify, render_template, request, url_for # type: ignore

local_bp = Blueprint("local", __name__, template_folder="templates")


def _safe_path(base_dir, relative_path):
    if not isinstance(relative_path, str):
        raise ValueError("path must be a string")

    if os.path.isabs(relative_path):
        raise ValueError("absolute paths are not allowed")

    base_path = os.path.realpath(os.path.abspath(base_dir))
    candidate_path = os.path.realpath(os.path.abspath(os.path.join(base_path, relative_path)))

    try:
        is_within_base = os.path.commonpath([base_path, candidate_path]) == base_path
    except ValueError:
        is_within_base = False

    base_prefix = base_path if base_path.endswith(os.sep) else base_path + os.sep
    if not (candidate_path == base_path or candidate_path.startswith(base_prefix)):
        is_within_base = False

    if not is_within_base:
        raise ValueError("path escapes its base directory")

    return candidate_path


def _local_thumbnail_url():
    return url_for("static", filename="local-audio.svg")


@local_bp.route('/')
@local_bp.route('/browse/')
@local_bp.route('/browse/<path:subpath>')
def browse(subpath=""):
    try:
        full_path = _safe_path(ROOT_DIR, subpath)
    except ValueError:
        return jsonify({"error": "invalid path"}), 400

    items = []
    if os.path.exists(full_path):
        for entry in os.scandir(full_path):
            if entry.is_dir() or entry.name.lower().endswith((
                '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.opus', '.webm', '.wma', 
                '.alac', '.ape', '.aiff', '.au', '.dsd', '.dff', '.mka', '.pcm', '.ra', '.tta', 
                '.mp4', '.avi', '.mov', '.flv', '.mkv', '.webm', '.mpeg', '.mpg', '.3gp', '.wmv'
            )):
                items.append({
                    "name": entry.name, 
                    "is_dir": entry.is_dir(), 
                    "rel_path": os.path.relpath(entry.path, ROOT_DIR)
                })
    items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))
    parent = os.path.dirname(subpath) if subpath else None
    return render_template('local.html', items=items, current_path=subpath, parent=parent, api_prefix=url_for("local.browse").rstrip("/"))

@local_bp.route("/acquire", methods=["POST"])
def acquire():

    data = request.get_json(silent=True) or {}

    force = data.get("force", False)

    granted = current_app.playback.acquire(
        "local",
        force=force
    )

    return jsonify({
        "granted": granted,
        "owner": current_app.playback.owner
    })

# ---------- QUEUE ----------

def get_random_local_track_payload(subpath=""):
    """Helper function to find a random audio file and format its payload."""
    audio_exts = (
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.opus', '.webm', '.wma',
        '.alac', '.ape', '.aiff', '.au', '.dsd', '.dff', '.mka', '.pcm', '.ra',
        '.tta', '.mp4', '.avi', '.mov', '.flv', '.mkv', '.mpeg', '.mpg', '.3gp', '.wmv'
    )
    
    try:
        target_dir = _safe_path(ROOT_DIR, subpath or "")
    except (TypeError, ValueError):
        return None

    if not os.path.exists(target_dir):
        return None

    if not os.path.isdir(target_dir):
        target_dir = os.path.dirname(target_dir)

    all_files = []
    for root, _, filenames in os.walk(target_dir):
        for f in filenames:
            if f.lower().endswith(audio_exts):
                abs_path = _safe_path(root, f)
                all_files.append({
                    "title": f,
                    "url": abs_path
                })
                
    if not all_files:
        return None
        
    picked = random.choice(all_files)
    return {
        "title": picked["title"],
        "thumbnail": _local_thumbnail_url(),
        "url": picked["url"]
    }

@local_bp.route("/enqueue_random", methods=["POST"])
@local_bp.route("/enque_random", methods=["POST"])
def enqueue_random():
    """Route targeted by the UI button to queue one random track from current dir."""
    if current_app.playback.owner is None:
        current_app.playback.acquire("local")
        
    data = request.get_json(silent=True) or {}
    subpath = data.get("path")
    if subpath is None:
        subpath = request.args.get("path")
    if subpath is None and request.referrer:
        from urllib.parse import urlparse, unquote
        parsed = urlparse(request.referrer)
        prefix = "/local/browse/"
        if prefix in parsed.path:
            subpath = unquote(parsed.path.split(prefix, 1)[1])

    payload = get_random_local_track_payload(subpath or "")
    if not payload:
        return jsonify({"error": "No local music files found"}), 404
        
    success = current_app.playback.enqueue("local", payload)
    
    if not success:
        return jsonify({
            "error": "local blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403
        
    return jsonify({"status": "queued", "song": payload})

@local_bp.route("/enqueue", methods=["POST"])
def enqueue():

    if current_app.playback.owner is None:
        current_app.playback.acquire("local")
    song = request.get_json()
    try:
        url = _safe_path(ROOT_DIR, song["rel_path"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "invalid local path"}), 400

    success = current_app.playback.enqueue(
        "local",
        {
            "title": song["title"],
            "thumbnail": _local_thumbnail_url(),
            "url": url
        }
    )

    if not success:
        return jsonify({
            "error": "local blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "queued"
    })
@local_bp.route("/play_folder", methods=["POST"])
def play_folder():
    data = request.get_json(silent=True) or {}
    subpath = data.get("path", "")

    try:
        full_path = _safe_path(ROOT_DIR, subpath)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid folder path"}), 400

    if not os.path.exists(full_path):
        return jsonify({"error": "folder not found"}), 404

    audio_exts = (
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.opus', '.webm', '.wma',
        '.alac', '.ape', '.aiff', '.au', '.dsd', '.dff', '.mka', '.pcm', '.ra',
        '.tta', '.mp4', '.avi', '.mov', '.flv', '.mkv', '.mpeg', '.mpg', '.3gp',
        '.wmv'
    )

    files = []

    for root, _, filenames in os.walk(full_path):
        for f in filenames:
            if f.lower().endswith(audio_exts):
                abs_path = _safe_path(root, f)

                files.append({
                    "title": f,
                    "url": abs_path
                })

    # sort alphabetically
    files.sort(key=lambda x: x["title"].lower())

    owner = current_app.playback.owner
    if owner is not None and owner != "local":
        return jsonify({
            "error": "local blueprint does not own player",
            "owner": owner
        }), 403

    # Ensure player blueprint ownership is checked/acquired just like individual enqueues
    if current_app.playback.owner is None:
        current_app.playback.acquire("local")

    songs = [
        {
            "title": song["title"],
            "thumbnail": _local_thumbnail_url(),
            "url": song["url"]
        }
        for song in files
    ]
    success = current_app.playback.enqueue_many("local", songs)

    if not success:
        return jsonify({
            "error": "local blueprint does not own player",
            "owner": current_app.playback.owner
        }), 403

    return jsonify({
        "status": "ok",
        "queued": len(songs),
        "folder": subpath
    })


@local_bp.route("/search")
def search_local_items():
    query = request.args.get('q', '').lower().strip()
    if not query:
        return jsonify([])

    results = []
    # Supported audio track extensions
    audio_extensions = ('.mp3', '.wav', '.flac', '.m4a', '.ogg')

    # Walk through the base operating system directory structure
    for root, dirs, files in os.walk(ROOT_DIR):
        # 1. Evaluate matching directories
        for d in dirs:
            if query in d.lower():
                full_path = _safe_path(root, d)
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                results.append({
                    "name": d,
                    "rel_path": rel_path,
                    "is_dir": True
                })

        # 2. Evaluate matching files
        for f in files:
            if f.endswith(audio_extensions) and query in f.lower():
                full_path = _safe_path(root, f)
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                results.append({
                    "name": f,
                    "rel_path": rel_path,
                    "is_dir": False
                })

    return jsonify(results)
