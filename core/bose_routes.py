"""#core.bose_routes.py - Flask routes for controlling Bose SoundTouch speakers
This module defines the Flask routes that allow the front-end to send commands to the Bose SoundTouch
speaker, such as adjusting volume, changing input sources, and triggering playback of a network stream. It uses a shared instance of the BoseSoundTouchWorker to execute these commands and retrieve status information.
The routes return JSON responses that indicate the success of the operations and provide current status details for the speaker.

**This file is focused solely on the Flask route definitions and their interactions with the BoseSoundTouchWorker. The actual implementation of the worker, including how it communicates with the Bose hardware, is contained in the bose_worker module. This separation allows for cleaner code organization and easier maintenance.**
"""

from flask import Blueprint, jsonify
from core.bose_worker import BoseSoundTouchWorker
from core.settings import BOSE_IP, STREAM_URL

# Initialize the blueprint
bose_control_bp = Blueprint('bose_control', __name__)

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

@bose_control_bp.route("/set_vol/<int:level>")
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