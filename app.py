from flask import Flask, jsonify, render_template, redirect, url_for, request   #type: ignore
from core.bose_routes import bose_control_bp
from core.stats import stats_bp
from apps.local.routes import local_bp
from apps.yt.routes import youtube_bp
from dotenv import load_dotenv
from collections import deque
from core.bose_worker import BoseSoundTouchWorker
from core.playback_controller import PlaybackController
from core.stream_manager import QueuePlayer
from services.ffmpeg_service import FFmpegService


load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config['song_queue'] = deque()
    app.config['current_song'] = None

    app.player = QueuePlayer(
        ffmpeg_service=FFmpegService(),
        bose_worker=BoseSoundTouchWorker(
            ip_address="192.168.29.234"
        )
    )

    app.player.start()

    app.playback = PlaybackController(app.player)

    @app.route("/")
    def index():
        #return render_template("index.html")
        return redirect(url_for('youtube.index'))  # Redirect to YouTube interface by default

    @app.route("/status")
    def status():
        return jsonify({"status": "ok"})

    # Register Blueprints
    app.register_blueprint(bose_control_bp)
    app.register_blueprint(local_bp, url_prefix='/local')
    app.register_blueprint(youtube_bp, url_prefix='/youtube')
    app.register_blueprint(stats_bp)
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)