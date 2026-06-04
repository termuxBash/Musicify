from flask import Flask
from core.routes import core_bp
from apps.local_music.routes import local_bp
from apps.youtube_music.routes import youtube_bp

def create_app():
    app = Flask(__name__)

    # Register Blueprints
    app.register_blueprint(core_bp, url_prefix='/')
    app.register_blueprint(local_bp, url_prefix='/local')
    app.register_blueprint(youtube_bp, url_prefix='/youtube')

    # Register the shared bose control routes under a unified prefix
    app.register_blueprint(bose_control_bp, url_prefix='/bose')
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)