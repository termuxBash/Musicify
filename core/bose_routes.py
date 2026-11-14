"""#core.bose_routes.py - Flask routes for controlling Bose SoundTouch speakers
This module defines the Flask routes that allow the front-end to send commands to the Bose SoundTouch
speaker, such as adjusting volume, changing input sources, and triggering playback of a network stream. It uses a shared instance of the BoseSoundTouchWorker to execute these commands and retrieve status information.
The routes return JSON responses that indicate the success of the operations and provide current status details for the speaker.

**This file is focused solely on the Flask route definitions and their interactions with the BoseSoundTouchWorker. The actual implementation of the worker, including how it communicates with the Bose hardware, is contained in the bose_worker module. This separation allows for cleaner code organization and easier maintenance.**
"""

import math
from flask import Blueprint, current_app, jsonify
from core.bose_worker import BoseSoundTouchWorker
from core.settings import BOSE_IP, STREAM_URL

# Initialize the blueprint
bose_control_bp = Blueprint('bose_control', __name__)

# Instantiate the single shared worker instance
bose = BoseSoundTouchWorker(ip_address=BOSE_IP)

def power_off_bose():
    """Power off the speaker only when its current state is on."""
    if not bose.get_status().get("is_on"):
        return True

    volume_set = bose.set_volume(0)
    power_toggled = bose.toggle_power()
    return volume_set and power_toggled

@bose_control_bp.route("/ctrl/<cmd>")
def ctrl(cmd):
    """Handles standard button triggers and input sources."""
    status = "success"
    
    if cmd == "vol_up":
        bose.volume_up()
    elif cmd == "vol_down":
        bose.volume_down()
    elif cmd == "bose_power":
        if bose.get_status().get("is_on"):
            current_app.player.toggle_autoplay(False)
            owner = current_app.playback.owner
            if owner is None:
                current_app.playback.stop()
            else:
                current_app.playback.release(owner)
            volume_set = bose.set_volume(0)
        else:
            volume_set = True
        power_toggled = bose.toggle_power()
        status = "success" if volume_set and power_toggled else "failed"
    elif cmd == "mute":
        bose.toggle_mute()
    elif cmd == "bluetooth":
        bose.select_source("BLUETOOTH")
    elif cmd == "aux":
        bose.select_source("AUX")
    elif cmd == "listen":
        # Uses the configured network stream URL
        bose.trigger_upnp_stream(STREAM_URL)
        status = STREAM_URL
    else:
        status = "unknown command"

    return jsonify({"status": status})

@bose_control_bp.route("/set_vol/<int:level>")
@bose_control_bp.route("/set_volume/<int:level>")
def set_volume(level):
    """Directly sets volume level (0-100) with anti-spike dampening."""
    level = max(0, min(100, level))
    current_vol = bose.get_volume() if hasattr(bose, "get_volume") else 0
    if not isinstance(current_vol, int):
        current_vol = 0
    current_vol = max(0, min(100, current_vol))

    delta = level - current_vol
    # Apply anti-spike dampening if increasing volume by > 10% and exceeding safe range (> 15%)
    if delta > 10 and (current_vol > 15 or level > 15):
        # Exponential decreasing scaling factor:
        # e.g. delta = 14 (16 -> 30) applies 50% -> new volume 23
        # e.g. delta = 40 (0 -> 40) applies ~37.5% -> new volume 15
        factor = 0.5 * math.exp(-0.01 * (delta - 14))
        applied_delta = max(1, int(round(delta * factor)))
        level = max(0, min(100, current_vol + applied_delta))

    success = bose.set_volume(level)
    return jsonify({"status": "success" if success else "failed", "volume": level})

@bose_control_bp.route("/get_status")
def get_status():
    """Returns current hardware volume and input source."""
    device_status = bose.get_status()
    now_playing = device_status["now_playing"]
    power_state = device_status["power_state"]
    return jsonify({
        "volume": bose.get_volume(),
        "is_on": power_state == "on",
        "power_state": power_state,
        "bose_source": now_playing.get("source", "UNKNOWN"),
        "track": now_playing.get("track", ""),
        "artist": now_playing.get("artist", "")
    })

@bose_control_bp.route("/is_on")
def check_power():
    """Returns a JSON payload indicating if the speaker is turned on."""
    device_status = bose.get_status()
    return jsonify({
        "is_on": device_status["is_on"],
        "power_state": device_status["power_state"],
    })