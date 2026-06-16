import sys
import signal
from flask import Flask, redirect, url_for
from core.bose_routes import bose_control_bp
from core.stats import stats_bp
from apps.local.routes import local_bp
from apps.yt.routes import youtube_bp
from collections import deque
from core.bose_worker import BoseSoundTouchWorker
from core.playback_controller import PlaybackController
from core.stream_manager import QueuePlayer
from services.ffmpeg_service import FFmpegService
from services.lyrics_service import LyricsService
from core.settings import APP_HOST, APP_PORT, BOSE_IP, DEFAULT_AUTOPLAY_ENABLED, DEFAULT_LYRICS_ENABLED



def create_app():
    app = Flask(__name__)
    app.config['song_queue'] = deque()
    app.config['current_song'] = None

    # Instantiate services
    app.lyrics_service = LyricsService(enabled=DEFAULT_LYRICS_ENABLED)
    app.last_known_title = None

    app.player = QueuePlayer(
        ffmpeg_service=FFmpegService(),
        autoplay_enabled=DEFAULT_AUTOPLAY_ENABLED,
        bose_worker=BoseSoundTouchWorker(
            ip_address=BOSE_IP
        )
    )

    app.player.start()
    app.playback = PlaybackController(app.player)

    @app.route("/")
    def index():
        return redirect(url_for('youtube.index'))

    app.register_blueprint(bose_control_bp)
    app.register_blueprint(local_bp, url_prefix='/local')
    app.register_blueprint(youtube_bp, url_prefix='/youtube')
    app.register_blueprint(stats_bp)
    return app

if __name__ == "__main__":
    app = create_app()

    # Graceful teardown hook on SIGINT / Ctrl+C
    def handle_sigint(signum, frame):
        print("\nShutting down cleanly... Stopping active FFmpeg pipelines.")
        try:
            # Tell your player loop to terminate and join threads
            if hasattr(app, 'player'):
                app.player.stop() 
        except Exception as e:
            print(f"Error during player thread stop: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    app.run(host=APP_HOST, port=APP_PORT, debug=False, use_reloader=True)