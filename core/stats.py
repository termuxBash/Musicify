# stats_bp.py
import json
from flask import Blueprint, request, jsonify, current_app
from core.bose_routes import get_status
import psutil

stats_bp = Blueprint("stats", __name__)

@stats_bp.route("/stats")
def stats():
    bose = get_status().get_json()  # Get current Bose status for volume info
    volume = 0

    if bose:
        try:
            volume = bose["volume"]
        except:
            pass
    else:
        volume = 0
    # Simulated song queue and current song for demonstration
    global song_queue
    global current_song
    show_lyrics_enabled = True  # Simulated synced state
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "volume": volume,
        "queue": list(current_app.player.status().get("queue", [])),
        "now_playing": current_app.player.status().get("current_song", None),
        "is_playing": current_app.player.status().get("playing", False),
        "show_lyrics": show_lyrics_enabled   # 🔥 SYNCED STATE
    })