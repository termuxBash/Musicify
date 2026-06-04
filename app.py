from flask import Flask, jsonify, render_template # type: ignore
from core.bose_routes import bose_control_bp
from apps.local.routes import local_bp
from apps.yt.routes import youtube_bp

def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/status")
    def status():
        return jsonify({"status": "ok"})

    # Register Blueprints
    app.register_blueprint(bose_control_bp, url_prefix='/bose')
    app.register_blueprint(local_bp, url_prefix='/local')
    app.register_blueprint(youtube_bp, url_prefix='/youtube')
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)