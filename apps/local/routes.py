from flask import Blueprint, jsonify, render_template # type: ignore

local_bp = Blueprint("local", __name__, template_folder="templates")


@local_bp.route("/")
def index():
    return render_template("local.html")


@local_bp.route("/status")
def status():
    return jsonify({"status": "ok", "service": "local"})

