import os
from flask import Blueprint, jsonify, request
from core.bose_worker import BoseSoundTouchWorker

# Initialize the blueprint
bose_control_bp = Blueprint('bose_control', __name__)

# Load configurations (Fallback to your default hardcoded Bose IP if env is missing)
BOSE_IP = os.getenv("BOSE_IP", "192.168.29.234")
STREAM_URL = os.getenv("STREAM_URL", "http://192.168.29.157:8000/mpv.ogg")

# Instantiate the single shared worker instance
bose = BoseSoundTouchWorker(ip_address=BOSE_IP)

@bose_control_bp.route("/ctrl/<cmd>")
def ctrl(cmd):
    """Handles standard button triggers and input sources."""
    status = "success"
    
    if cmd == "vol_up":
        bose.volume_up()
    elif cmd == "vol_down":
        bose.volume_down()
    elif cmd == "bose_power":
        bose.toggle_power()
    elif cmd == "mute":
        bose.toggle_mute()
    elif cmd == "bluetooth":
        bose.select_source("BLUETOOTH")
    elif cmd == "aux":
        bose.select_source("AUX")
    elif cmd == "listen":
        # Uses the configured network stream URL
        bose.trigger_upnp_stream(STREAM_URL)
    else:
        status = "unknown command"

    return jsonify({"status": status})

@bose_control_bp.route("/set_volume/<int:level>")
def set_volume(level):
    """Directly sets volume level (0-100)."""
    success = bose.set_volume(level)
    return jsonify({"status": "success" if success else "failed"})

@bose_control_bp.route("/get_status")
def get_status():
    """Returns current hardware volume and input source."""
    now_playing = bose.get_now_playing()
    return jsonify({
        "volume": bose.get_volume(),
        "bose_source": now_playing.get("source", "UNKNOWN"),
        "track": now_playing.get("track", ""),
        "artist": now_playing.get("artist", "")
    })

@bose_control_bp.route("/is_on")
def check_power():
    """Returns a JSON payload indicating if the speaker is turned on."""
    return jsonify({
        "is_on": bose.is_on()
    })