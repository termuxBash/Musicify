""" local/routes.py - Flask routes for local file browsing and playback
"""

import os

from flask import Blueprint, current_app, jsonify, render_template, request, url_for # type: ignore

local_bp = Blueprint("local", __name__, template_folder="templates")

ROOT_DIR = os.path.abspath('/home/linuxlite/Music/')
@local_bp.route('/')
@local_bp.route('/browse/')
@local_bp.route('/browse/<path:subpath>')
def browse(subpath=""):
    full_path = os.path.join(ROOT_DIR, subpath)
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

@local_bp.route("/enqueue", methods=["POST"])
def enqueue():

    if current_app.playback.owner is None:
        current_app.playback.acquire("local")
    song = request.get_json()
    url = f"{ROOT_DIR}/{song['rel_path']}"

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

    full_path = os.path.join(ROOT_DIR, subpath)

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
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, ROOT_DIR)

                files.append({
                    "title": f,
                    "rel_path": rel_path,
                    "url": abs_path
                })

    # sort alphabetically (optional)
    files.sort(key=lambda x: x["title"].lower())

    # enqueue in order
    queued = 0

    for song in files:
        ok = current_app.playback.enqueue(
            "local",
            {
                "title": song["title"],
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


@local_bp.route("/status")
def status():
    return jsonify({"status": "ok", "service": "local"})

@local_bp.route("/search")
def search_tracks():
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
                full_path = os.path.join(root, d)
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                results.append({
                    "name": d,
                    "rel_path": rel_path,
                    "is_dir": True
                })

        # 2. Evaluate matching files
        for f in files:
            if f.endswith(audio_extensions) and query in f.lower():
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, ROOT_DIR)
                results.append({
                    "name": f,
                    "rel_path": rel_path,
                    "is_dir": False
                })

    return jsonify(results)
