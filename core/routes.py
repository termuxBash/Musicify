"""
Core routes - Home and status endpoints
"""
from flask import Blueprint, jsonify
from core.system_monitor import system_monitor

core_bp = Blueprint('core', __name__)


@core_bp.route('/', methods=['GET'])
def home():
    """Home endpoint - returns API status"""
    return jsonify({
        "status": "ok",
        "app": "Musicify",
        "endpoints": {
            "local": "/local",
            "youtube": "/youtube",
            "bose": "/bose"
        }
    })


@core_bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200


@core_bp.route('/system-monitor', methods=['GET'])
def get_system_monitor():
    """Get system resource monitoring data"""
    return jsonify(system_monitor.get_status())
