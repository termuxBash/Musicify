from flask import Flask
from types import SimpleNamespace

from apps.yt import routes


def test_enqueue_reports_radio_ownership_before_resolving_song(monkeypatch):
    app = Flask(__name__)
    app.playback = SimpleNamespace(owner="radio")
    app.register_blueprint(routes.youtube_bp, url_prefix="/youtube")

    def enqueue_youtube_result(*args, **kwargs):
        raise AssertionError("A song should not be resolved before takeover.")

    monkeypatch.setattr(
        routes.YTService,
        "enqueue_youtube_result",
        staticmethod(enqueue_youtube_result),
    )

    response = app.test_client().post(
        "/youtube/enqueue",
        json={"title": "Test Song", "videoId": "test-video"},
    )

    assert response.status_code == 403
    assert response.json["owner"] == "radio"
