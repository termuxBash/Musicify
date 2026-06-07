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
        
    player_status = current_app.player.status()
    show_lyrics_enabled = True  # Simulated synced state
    
    return jsonify({
        "cpu": psutil.cpu_percent(),
        "volume": volume,
        "queue": list(player_status.get("queue", [])),
        "now_playing": player_status.get("current_song", None),
        "is_playing": player_status.get("playing", False),
        "autoplay_enabled": player_status.get("autoplay_enabled", False), # 🔥 FEEDBACK TO UI
        "show_lyrics": show_lyrics_enabled   
    })

@stats_bp.route("/toggle_autoplay", methods=["POST"])
def toggle_autoplay():
    """
    Accepts JSON: {"enabled": true} or {"enabled": false}
    Changes the state inside the core audio loop instantly.
    """
    data = request.get_json() or {}
    enabled = data.get("enabled", False)
    
    current_app.player.toggle_autoplay(enabled)
    
    return jsonify({
        "status": "success",
        "autoplay_enabled": current_app.player.status().get("autoplay_enabled")
    })