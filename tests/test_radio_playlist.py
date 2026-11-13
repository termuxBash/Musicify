import threading

from flask import Flask

from apps.radio import routes
from core import stats
from core.playback_controller import PlaybackController
from core.stream_manager import QueuePlayer


class FakePlayer:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = []
        self.current_song = None
        self.autoplay_suppressed = False

    def skip(self):
        pass

    def enqueue(self, song):
        self.queue.append(song)

    def suppress_autoplay(self):
        self.autoplay_suppressed = True

    def resume_autoplay(self):
        self.autoplay_suppressed = False


def create_playlist_app():
    app = Flask(__name__)
    app.player = FakePlayer()
    app.playback = PlaybackController(app.player)
    app.register_blueprint(stats.stats_bp)
    return app


def test_add_radio_replaces_previous_radio_line_and_appends(monkeypatch, tmp_path):
    monkeypatch.setattr(stats, "PLAYLIST_DIR", str(tmp_path))
    playlist_file = tmp_path / "1.txt"
    playlist_file.write_text(
        "Song>video-id\nRadio>https://old.example/stream\nradio>keep-this\n",
        encoding="utf-8",
    )

    response = create_playlist_app().test_client().post(
        "/add_to_playlist",
        json={
            "playlist": "1",
            "song": {
                "title": "Radio:(New Station)",
                "url": "https://new.example/stream",
            },
        },
    )

    assert response.status_code == 200
    assert playlist_file.read_text(encoding="utf-8") == (
        "Song>video-id\nradio>keep-this\nRadio>https://new.example/stream\n"
    )


def test_playlist_queues_radio_after_songs_with_station_artwork(monkeypatch, tmp_path):
    monkeypatch.setattr(stats, "PLAYLIST_DIR", str(tmp_path / "playlists"))
    monkeypatch.setattr(stats, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "apps.radio.routes._station_for_stream_url",
        lambda stream_url: {
            "name": "Example FM",
            "url": stream_url,
            "favicon": "https://images.example/example-fm.png",
        },
    )
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    (playlist_dir / "1.txt").write_text(
        "Local Track>song.mp3\nRadio>https://stream.example/radio\n",
        encoding="utf-8",
    )

    app = create_playlist_app()
    response = app.test_client().get("/playlist/1")

    assert response.status_code == 200
    assert response.json["count"] == 2
    assert [song["title"] for song in app.player.queue] == [
        "Local Track",
        "Radio:(Example FM)",
    ]
    assert app.player.queue[-1]["title"] == "Radio:(Example FM)"
    assert app.player.queue[-1]["thumbnail"] == "https://images.example/example-fm.png"
    assert app.player.autoplay_suppressed


def test_station_lookup_matches_resolved_stream_url(monkeypatch):
    class FakeRadioBrowser:
        def search(self, **kwargs):
            assert kwargs == {"name": "example", "limit": 25}
            return [{
                "name": "Example FM",
                "url": "http://stream.example/example",
                "url_resolved": "https://stream.example/example",
                "favicon": "https://images.example/example-fm.png",
            }]

    monkeypatch.setattr(routes, "_radio_browser", FakeRadioBrowser())

    station = routes._station_for_stream_url("https://stream.example/example")

    assert station["name"] == "Example FM"
    assert station["url"] == "https://stream.example/example"
    assert station["favicon"] == "https://images.example/example-fm.png"


def test_station_lookup_uses_station_slug(monkeypatch):
    class FakeRadioBrowser:
        def search(self, **kwargs):
            assert kwargs == {"name": "mangoradio", "limit": 25}
            return [{
                "name": "MANGORADIO",
                "url": "https://mangoradio.stream.laut.fm/mangoradio",
                "url_resolved": "https://mangoradio.stream.laut.fm/mangoradio",
                "favicon": "https://images.example/mangoradio.png",
            }]

    monkeypatch.setattr(routes, "_radio_browser", FakeRadioBrowser())

    station = routes._station_for_stream_url(
        "https://mangoradio.stream.laut.fm/mangoradio"
    )

    assert station["name"] == "MANGORADIO"
    assert station["favicon"] == "https://images.example/mangoradio.png"


def test_station_lookup_skips_mpv_stream_search(monkeypatch):
    class FakeRadioBrowser:
        def search(self, **kwargs):
            raise AssertionError(f"Unexpected Radio Browser search: {kwargs}")

    monkeypatch.setattr(routes, "_radio_browser", FakeRadioBrowser())

    station = routes._station_for_stream_url("http://192.168.29.157:8000/mpv.ogg")

    assert station["name"] == "Mpv"
    assert station["url"] == "http://192.168.29.157:8000/mpv.ogg"


def test_stream_station_skips_mpv_search(monkeypatch):
    class FakeRadioBrowser:
        def search(self, **kwargs):
            raise AssertionError(f"Unexpected Radio Browser search: {kwargs}")

    monkeypatch.setattr(routes, "_radio_browser", FakeRadioBrowser())

    assert routes._stream_station("mpv", "http://192.168.29.157:8000/mpv.ogg") is None


def test_station_lookup_uses_radio_thumbnail_when_art_is_unavailable(monkeypatch):
    class FakeRadioBrowser:
        def search(self, **kwargs):
            return []

    monkeypatch.setattr(routes, "_radio_browser", FakeRadioBrowser())

    station = routes._station_for_stream_url(
        "https://stream.example/live"
    )

    assert station["name"] == "Example"
    assert station["favicon"] == "/static/radio.svg"


def test_queue_player_hands_radio_item_to_handler_without_ffmpeg():
    handled = threading.Event()
    handled_stations = []

    class FakeFFmpeg:
        def start_stream(self, song):
            raise AssertionError("Radio must not use the Icecast/FFmpeg route.")

    player = QueuePlayer(FakeFFmpeg())
    station = {"name": "Example FM", "url": "https://stream.example/radio"}
    player.radio_handler = lambda payload: (handled_stations.append(payload), handled.set())
    player.enqueue({"station": station, "title": "Radio:(Example FM)"})
    player.start()

    try:
        assert handled.wait(timeout=2)
    finally:
        player.stop()

    assert handled_stations == [station]
