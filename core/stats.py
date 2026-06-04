# stats_bp.py
import json
from flask import Blueprint, request, jsonify
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
    song_queue = ["Song A", "Song B", "Song C"]
    current_song = "Song A"
    show_lyrics_enabled = True  # Simulated synced state
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "volume": volume,
        "queue": song_queue,
        "now_playing": current_song,
        "is_playing": current_song is not None,
        "show_lyrics": show_lyrics_enabled   # 🔥 SYNCED STATE
    })