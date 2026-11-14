from flask import Flask
from types import SimpleNamespace
from unittest.mock import Mock

from apps.yt import routes
from services import yt_service


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


def test_search_falls_back_to_video_and_playlist_ids(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.youtube_bp, url_prefix="/youtube")
    monkeypatch.setattr(
        routes.YTService,
        "get_youtube_search_results",
        staticmethod(lambda query, max_results: ({"items": []}, 200)),
    )
    monkeypatch.setattr(
        routes.YTService,
        "get_youtube_id_metadata",
        staticmethod(lambda video_id, playlist_id: [
            {"kind": "video", "title": "Video title", "thumbnail": "/video.jpg", "videoId": video_id},
            {"kind": "playlist", "title": "Playlist title", "thumbnail": "/playlist.jpg", "playlistId": playlist_id},
        ]),
    )

    response = app.test_client().post(
        "/youtube/search",
        data={
            "query": (
                "https://music.youtube.com/watch?v=2pQKqQ9sG50"
                "&list=OLAK5uy_mjNB8hu_s5goDncwoopQoSRXjmRjEPq54"
            )
        },
    )

    assert response.status_code == 200
    assert [item["title"] for item in response.json] == ["Video title", "Playlist title"]
    assert [(item["kind"], item.get("videoId"), item.get("playlistId"))
            for item in response.json] == [
        ("video", "2pQKqQ9sG50", None),
        ("playlist", None, "OLAK5uy_mjNB8hu_s5goDncwoopQoSRXjmRjEPq54"),
    ]


def test_search_falls_back_to_playlist_id_without_video(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.youtube_bp, url_prefix="/youtube")
    monkeypatch.setattr(
        routes.YTService,
        "get_youtube_search_results",
        staticmethod(lambda query, max_results: ({"items": []}, 200)),
    )
    monkeypatch.setattr(
        routes.YTService,
        "get_youtube_id_metadata",
        staticmethod(lambda video_id, playlist_id: [
            {"kind": "playlist", "title": "Playlist title", "thumbnail": "/playlist.jpg", "playlistId": playlist_id},
        ]),
    )

    response = app.test_client().post(
        "/youtube/search",
        data={"query": "https://www.youtube.com/playlist?list=PLxCzCOWd7aiFAN6I8CuViBuCdJgiOkT2Y"},
    )

    assert response.status_code == 200
    assert len(response.json) == 1
    assert response.json[0]["kind"] == "playlist"
    assert response.json[0]["playlistId"] == "PLxCzCOWd7aiFAN6I8CuViBuCdJgiOkT2Y"


def test_enqueue_youtube_playlist_expands_entries_with_yt_dlp(monkeypatch):
    app = Flask(__name__)
    playback = Mock()
    playback.enqueue_many.return_value = True
    app.playback = playback

    class FakeYoutubeDL:
        def __init__(self, options):
            assert options["extract_flat"] == "in_playlist"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert url == "https://www.youtube.com/playlist?list=PL1234567890"
            assert download is False
            return {
                "entries": [
                    {"id": "abcdefghijk", "title": "First track"},
                    {"url": "https://music.youtube.com/watch?v=12345678901", "title": "Second track"},
                ]
            }

    monkeypatch.setattr(yt_service, "YoutubeDL", FakeYoutubeDL)

    with app.app_context():
        assert yt_service.YTService.enqueue_youtube_playlist(
            "PL1234567890",
            record_history=False,
        )

    playback.enqueue_many.assert_called_once_with(
        "youtube",
        [
            {
                "title": "First track",
                "thumbnail": "https://img.youtube.com/vi/abcdefghijk/hqdefault.jpg",
                "url": None,
                "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                "videoId": "abcdefghijk",
                "record_history": False,
            },
            {
                "title": "Second track",
                "thumbnail": "https://img.youtube.com/vi/12345678901/hqdefault.jpg",
                "url": None,
                "source_url": "https://www.youtube.com/watch?v=12345678901",
                "videoId": "12345678901",
                "record_history": False,
            },
        ],
    )


def test_enqueue_playlist_respects_playback_owner(monkeypatch):
    app = Flask(__name__)
    app.playback = SimpleNamespace(owner="radio")
    app.register_blueprint(routes.youtube_bp, url_prefix="/youtube")

    def enqueue_playlist(*args, **kwargs):
        raise AssertionError("A playlist should not be resolved before takeover.")

    monkeypatch.setattr(
        routes.YTService,
        "enqueue_youtube_playlist",
        staticmethod(enqueue_playlist),
    )

    response = app.test_client().post(
        "/youtube/enqueue_playlist",
        json={"playlistId": "PL1234567890"},
    )

    assert response.status_code == 403
    assert response.json["owner"] == "radio"
