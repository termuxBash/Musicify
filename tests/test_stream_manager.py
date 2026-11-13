from flask import Flask

from apps.local import routes as local_routes
from core.stream_manager import QueuePlayer


class FakeFFmpeg:
    pass


def test_local_autoplay_generates_thumbnail_inside_request_context(monkeypatch):
    app = Flask(__name__)
    player = QueuePlayer(FakeFFmpeg(), autoplay_enabled=True, flask_app=app)

    def local_track_payload():
        return {
            "title": "Next track",
            "thumbnail": local_routes._local_thumbnail_url(),
            "url": "/music/next-track.mp3",
        }

    monkeypatch.setattr(
        local_routes,
        "get_random_local_track_payload",
        local_track_payload,
    )

    player._trigger_autoplay({"title": "Current track", "url": "/music/current-track.mp3"})

    assert player.queue == [
        {
            "title": "Next track",
            "thumbnail": "/static/local-audio.svg",
            "url": "/music/next-track.mp3",
        }
    ]
