"""#local/routes.py - Flask routes for local file browsing and playback
"""

import os
import random
from core.settings import ROOT_DIR
from flask import Blueprint, current_app, jsonify, render_template, request, url_for # type: ignore

local_bp = Blueprint("local", __name__, template_folder="templates")


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

def get_random_local_track_payload():
    """Helper function to find a random audio file and format its payload."""
    audio_exts = (
        '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.opus', '.webm', '.wma',
        '.alac', '.ape', '.aiff', '.au', '.dsd', '.dff', '.mka', '.pcm', '.ra',
        '.tta', '.mp4', '.avi', '.mov', '.flv', '.mkv', '.mpeg', '.mpg', '.3gp', '.wmv'
    )
    
    all_files = []
    for root, _, filenames in os.walk(ROOT_DIR):
        for f in filenames:
            if f.lower().endswith(audio_exts):
                abs_path = _safe_path(root, f)
                rel_path = os.path.relpath(abs_path, ROOT_DIR)
                all_files.append({
                    "title": f,
                    "rel_path": rel_path,
                    "url": abs_path
                })
                
    if not all_files:
        return None
        
    picked = random.choice(all_files)
    return {
        "title": picked["title"],
        "thumbnail": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E",
        "url": picked["url"]
    }

@local_bp.route("/enqueue_random", methods=["POST"])
def enqueue_random():
    """Route targeted by the UI button to queue one random track."""
    if current_app.playback.owner is None:
        current_app.playback.acquire("local")
        
    payload = get_random_local_track_payload()
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
            "thumbnail": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E",
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
                rel_path = os.path.relpath(abs_path, ROOT_DIR)

                files.append({
                    "title": f,
                    "rel_path": rel_path,
                    "url": abs_path
                })

    # sort alphabetically
    files.sort(key=lambda x: x["title"].lower())

    # Ensure player blueprint ownership is checked/acquired just like individual enqueues
    if current_app.playback.owner is None:
        current_app.playback.acquire("local")

    # enqueue in order
    queued = 0

    for song in files:
        # Build the exact object payload structure your streaming playback architecture requires
        ok = current_app.playback.enqueue(
            "local",
            {
                "title": song["title"],
                "thumbnail": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E",
                "url": song["url"]
            }
        )
        if ok:
            queued += 1

    return jsonify({
        "status": "ok",
        "queued": queued,
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
