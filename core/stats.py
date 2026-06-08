# core/stats.py
from flask import Blueprint, jsonify, request ,current_app
import psutil
from core.bose_routes import get_status

stats_bp = Blueprint("stats", __name__)

@stats_bp.route("/stats")
def stats():
    bose = get_status().get_json() if get_status else None
    volume = bose.get("volume", 0) if bose else 0
        
    player_status = current_app.player.status()
    is_playing = player_status.get("playing", False)
    
    current_song = player_status.get("current_song")
    current_title = current_song.get("title") if current_song else None
    
    # Reference the service tied directly to current_app context
    lyrics_svc = current_app.lyrics_service

    # Check if a new track started playing
    if getattr(current_app, 'last_known_title', None) != current_title:
        current_app.last_known_title = current_title
        lyrics_svc.reset(current_title)
        
    # If paused, shift back the clock baseline so lyrics don't keep running ahead
    if not is_playing and lyrics_svc.song_start_time is not None:
        lyrics_svc.song_start_time += 2  # Match front-end poll loop interval

    return jsonify({
        "cpu": psutil.cpu_percent(),
        "volume": volume,
        "queue": list(player_status.get("queue", [])),
        "now_playing": current_song,
        "is_playing": is_playing,
        "autoplay_enabled": player_status.get("autoplay_enabled", False),
        "show_lyrics": lyrics_svc.enabled,
        "current_lyric": lyrics_svc.get_current_line()  # This returns the raw line string
    })
@stats_bp.route("/toggle_lyrics", methods=["GET"])
def toggle_lyrics():
    current_app.lyrics_service.enabled = not current_app.lyrics_service.enabled
    return jsonify({
        "status": "success",
        "show_lyrics": current_app.lyrics_service.enabled
    })


@stats_bp.route("/toggle_autoplay", methods=["GET"])
def toggle_autoplay():
    current_enabled = current_app.player.status().get("autoplay_enabled", False)
    current_app.player.toggle_autoplay(not current_enabled)

    return jsonify({
        "status": "success",
        "autoplay_enabled": current_app.player.status().get("autoplay_enabled")
    })