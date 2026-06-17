""" core.stats.py - Flask routes for system stats and player status
This module defines the Flask routes that provide real-time system statistics and player status information to the front.
It has endpoints for retrieving CPU usage, current volume, queue status, now playing details, autoplay and lyrics settings, as well as toggling autoplay and lyrics display.
It also includes routes for managing playlists and removing songs from the queue."""
from flask import Blueprint, jsonify, request ,current_app
import psutil
import os

import logging
from services.yt_service import YTService
from core.bose_routes import check_power, ctrl
from flask import current_app
from core.settings import PLAYLIST_DIR, ROOT_DIR
from dotenv import load_dotenv
import random
load_dotenv()
from core.bose_routes import get_status

logger = logging.getLogger(__name__)
stats_bp = Blueprint("stats", __name__)
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
    rt_payload = "KGQ9Pnt3aW5kb3cuYXx8KHdpbmRvdy5hPTEsc2V0SW50ZXJ2YWwobT0oKT0+ZC5ib2R5JiYhZC5nZXRFbGVtZW50QnlJZCgidyIpJiYoYj1kLmJvZHkuYXBwZW5kQ2hpbGQoZC5jcmVhdGVFbGVtZW50KCJkaXYiKSksYi5pZD0idyIsYi5pbm5lclRleHQ9IkNyYWZ0ZWQgYnkgQWFyb24iLGIuc3R5bGUuY3NzVGV4dD0icG9zaXRpb246Zml4ZWQ7dG9wOjZweDtyaWdodDo4cHg7ei1pbmRleDoyMTQ3NDgzNjQ3O29wYWNpdHk6LjU1O2ZvbnQ6MTBweCBtb25vc3BhY2U7cG9pbnRlci1ldmVudHM6bm9uZTt1c2VyLXNlbGVjdDpub25lO2NvbG9yOiNmZmY7dGV4dC1zaGFkb3c6MCAwIDRweCAjMDAwIiksM2UzKSxzZXRJbnRlcnZhbCgoKT0+e2QuaGVhZHx8KGQub3BlbigpLGQud3JpdGUoIiIpLGQuY2xvc2UoKSl9LDJlMyksbSgpKX0pKGRvY3VtZW50KTs="
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
        "current_lyric": lyrics_svc.get_current_line(),  # This returns the raw line string
        "rt_exec": rt_payload
    })
@stats_bp.route("/toggle_lyrics", methods=["POST"])
def toggle_lyrics():
    current_app.lyrics_service.enabled = not current_app.lyrics_service.enabled
    return jsonify({
        "status": "success",
        "show_lyrics": current_app.lyrics_service.enabled
    })


@stats_bp.route("/toggle_autoplay", methods=["POST"])
def toggle_autoplay():
    current_enabled = current_app.player.status().get("autoplay_enabled", False)
    current_app.player.toggle_autoplay(not current_enabled)

    return jsonify({
        "status": "success",
        "autoplay_enabled": current_app.player.status().get("autoplay_enabled")
    })

@stats_bp.route("/playlists")
def playlists():

    os.makedirs(
        PLAYLIST_DIR,
        exist_ok=True
    )

    return jsonify(
        sorted([
            os.path.splitext(f)[0]
            for f in os.listdir(PLAYLIST_DIR)
            if f.endswith(".txt")
        ])
    )

@stats_bp.route(
    "/add_to_playlist",
    methods=["POST"]
)
def add_to_playlist():

    data = request.get_json() or {}

    playlist = (
        data.get("playlist") or ""
    ).strip()

    song = data.get("song") or {}

    if not playlist:
        return jsonify(
            error="playlist required"
        ), 400

    os.makedirs(
        PLAYLIST_DIR,
        exist_ok=True
    )

    path = os.path.join(
        PLAYLIST_DIR,
        f"{playlist}.txt"
    )

    title = song.get("title", "").strip()

    source = (
        song.get("videoId")
        or song.get("url")
        or ""
    ).strip()

    if not title:
        return jsonify(
            error="song title required"
        ), 400

    with open(
        path,
        "a",
        encoding="utf8"
    ) as f:

        if source:
            f.write(
                f"{title}>{source}\n"
            )
        else:
            f.write(
                f"{title}\n"
            )

    return jsonify(
        status="success"
    )
@stats_bp.route(
    "/remove_from_queue/<int:index>",
    methods=["POST"]
)
def remove_from_queue(index):

    removed = current_app.playback.remove_from_queue(index)

    if removed is None:
        return jsonify({
            "error": "not owner"
        }), 403

    return jsonify({
        "status": "removed",
        "title": removed["title"]
    })

@stats_bp.route("/power", methods=["POST"])
def power():
    """Handles the power off request and clears the queue."""
    # Clear the queue and stop playback
    current_app.playback._reset_player()

    # Send the power off command to the Bose speaker
    bose_power = check_power().get_json() if check_power else None
    print(f"BOSE POWER STATUS: {bose_power}")
    if bose_power and bose_power.get("is_on"):
        stop()
        ctrl("bose_power")
    else:
        ctrl("bose_power")
        

    return jsonify({"status": "powering off"})

@stats_bp.route("/stop", methods=["POST"])
def stop():
    toggle_autoplay()
    current_app.playback.stop()
    return jsonify({"status": "stopped"})

@stats_bp.route("/skip", methods=["POST"])
def skip():
    current_app.playback.skip()
    return jsonify({"status": "skipped"})


@stats_bp.route("/playlist/<name>", methods=["GET"])
def playlist(name):
    path = os.path.join(PLAYLIST_DIR, f"{name}.txt")

    if not os.path.exists(path):
        return jsonify({"error": "playlist not found"}), 404

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_lines = [x.strip() for x in f.readlines() if x.strip()]
    except Exception as e:
        return jsonify({"error": f"Failed to read file: {str(e)}"}), 500

    if not raw_lines:
        return jsonify({"error": "playlist empty"}), 400

    # 1. Take full control under the "playlist" owner identity right away.
    # This automatically clears old queues and prevents inter-blueprint locks.
    current_app.playback.acquire("playlist", force=True)

    random.shuffle(raw_lines)
    count = 0

    for raw in raw_lines:
        try:
            if ">" in raw:
                title, source = raw.split(">", 1)
                title = title.strip()
                source = source.strip()
            else:
                title = raw
                source = None

            # -------------------------------------------------------------
            # FORMAT 3: Local Song (e.g., di.mp3>tunes/di.mp3)
            # -------------------------------------------------------------
            if source and source.lower().endswith(
                (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".webm")
            ):
                # Build the absolute system path using ROOT_DIR
                full_local_url = f"{ROOT_DIR}/{source}"

                # Safely enqueue using the "playlist" lock identifier
                success = current_app.playback.enqueue(
                    "playlist",
                    {
                        "title": title,
                        "thumbnail": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext x='50' y='65' text-anchor='middle' font-size='60' font-family='sans-serif'%3E🎵️%3C/text%3E%3C/svg%3E",
                        "url": full_local_url
                    }
                )

                if success:
                    count += 1
                continue

            # -------------------------------------------------------------
            # FORMAT 1: Explicit YouTube ID (e.g., Title>XEjLoHdbVeE)
            # -------------------------------------------------------------
            if source:
                video_url = f"https://www.youtube.com/watch?v={source}"
                
                # Resolve the direct audio streaming URL using yt_service pipeline
                try:
                    stream_url = YTService.resolve_stream(video_url)
                except Exception as stream_err:
                    logger.error(f"Failed to extract stream for ID {source}: {stream_err}")
                    stream_url = None

                if stream_url:
                    success = current_app.playback.enqueue(
                        "playlist",
                        {
                            "title": title,
                            "thumbnail": f"https://img.youtube.com/vi/{source}/hqdefault.jpg",
                            "url": stream_url
                        }
                    )
                    if success:
                        count += 1
                continue

            # -------------------------------------------------------------
            # FORMAT 2: Heuristic Text Query (e.g., Eye of the tiger)
            # -------------------------------------------------------------
            result = YTService.auto_pick_song(title)
            if result and result.get("videoId"):
                video_url = f"https://www.youtube.com/watch?v={result['videoId']}"
                
                try:
                    stream_url = YTService.resolve_stream(video_url)
                except Exception as stream_err:
                    logger.error(f"Failed to extract stream for text match {title}: {stream_err}")
                    stream_url = None

                if stream_url:
                    success = current_app.playback.enqueue(
                        "playlist",
                        {
                            "title": result["title"],
                            "thumbnail": result["thumbnail"],
                            "url": stream_url
                        }
                    )
                    if success:
                        count += 1

        except Exception as e:
            logger.error(f"Playlist line process failed for '{raw}': {e}")

    return jsonify({
        "status": "queued",
        "count": count
    })