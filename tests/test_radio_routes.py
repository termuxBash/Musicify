import threading

from flask import Flask

from apps.radio import routes
from core.playback_controller import PlaybackController


class FakeBose:
    def __init__(self, play_result=True):
        self.play_result = play_result
        self.played_urls = []
        self.metadata_calls = []

    def play_direct(self, stream_url):
        self.played_urls.append(stream_url)
        return self.play_result

    def get_playing_url(self):
        return {"success": True, "url": "https://stream.example/radio"}

    def get_stream_metadata(self, stream_url):
        self.metadata_calls.append(stream_url)
        return {
            "success": True,
            "station": "Example FM",
            "song": "Test Song",
            "artist": "Test Artist",
        }


class FakePlayer:
    def __init__(self, bose):
        self.bose = bose
        self.lock = threading.Lock()
        self.queue = []
        self.current_song = None
        self.autoplay_suppressed = False

    def skip(self):
        pass

    def suppress_autoplay(self):
        self.autoplay_suppressed = True

    def resume_autoplay(self):
        self.autoplay_suppressed = False


def create_radio_app(bose):
    app = Flask(__name__)
    app.player = FakePlayer(bose)
    app.playback = PlaybackController(app.player)
    app.register_blueprint(routes.radio_bp, url_prefix="/radio")
    return app


def test_play_forces_radio_ownership_and_uses_direct_play():
    bose = FakeBose()
    app = create_radio_app(bose)
    app.playback.acquire("youtube")
    client = app.test_client()

    response = client.post(
        "/radio/play",
        json={
            "name": "Example FM",
            "url": "https://stream.example/radio",
        },
    )

    assert response.status_code == 200
    assert response.json["status"] == "playing"
    assert app.playback.owner == "radio"
    assert app.player.autoplay_suppressed
    assert bose.played_urls == ["https://stream.example/radio"]

    assert app.playback.acquire("youtube", force=True)
    assert not app.player.autoplay_suppressed


def test_numbered_route_plays_saved_station_and_returns_to_radio_page(monkeypatch):
    bose = FakeBose()
    app = create_radio_app(bose)
    monkeypatch.setattr(
        routes,
        "get_radio_config",
        lambda: {
            **{str(number): None for number in range(1, 7)},
            "2": {"name": "Example FM", "url": "https://stream.example/radio"},
            "favorites": [],
        },
    )

    response = app.test_client().get("/radio/2")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/radio/")
    assert bose.played_urls == ["https://stream.example/radio"]


def test_saving_preset_does_not_add_favorite(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        routes,
        "get_radio_config",
        lambda: {
            **{str(number): None for number in range(1, 7)},
            "favorites": [],
        },
    )
    monkeypatch.setattr(
        routes,
        "update_user_config",
        lambda key, value: saved.update({key: value}),
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().post(
        "/radio/preset",
        json={
            "number": 4,
            "station": {
                "name": "Example FM",
                "url": "https://stream.example/radio",
                "favicon": "https://stream.example/logo.png",
            },
        },
    )

    assert response.status_code == 200
    assert saved["radio"]["4"]["name"] == "Example FM"
    assert saved["radio"]["favorites"] == []


def test_saving_favorite_does_not_change_presets(monkeypatch):
    saved = {}
    config = {
        **{str(number): None for number in range(1, 7)},
        "2": {"name": "Preset FM", "url": "https://stream.example/preset"},
        "favorites": [],
    }
    monkeypatch.setattr(routes, "get_radio_config", lambda: config.copy())
    monkeypatch.setattr(
        routes,
        "update_user_config",
        lambda key, value: saved.update({key: value}),
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().post(
        "/radio/favorites",
        json={"station": {
            "name": "Favorite FM",
            "url": "https://stream.example/favorite",
        }},
    )

    assert response.status_code == 200
    assert saved["radio"]["2"] == config["2"]
    assert saved["radio"]["favorites"] == [{
        "name": "Favorite FM",
        "url": "https://stream.example/favorite",
        "favicon": "",
        "stationuuid": "",
        "homepage": "",
        "country": "",
        "tags": "",
        "codec": "",
        "bitrate": "",
    }]


def test_favorite_get_route_fuzzy_matches_and_forces_playback(monkeypatch):
    station = {"name": "Example Favorite FM", "url": "https://stream.example/radio"}
    monkeypatch.setattr(
        routes,
        "get_radio_config",
        lambda: {
            **{str(number): None for number in range(1, 7)},
            "favorites": [station],
        },
    )
    bose = FakeBose()
    app = create_radio_app(bose)
    app.playback.acquire("local")

    response = app.test_client().get("/radio/fav/Example%20Favrite%20FM")

    assert response.status_code == 302
    assert app.playback.owner == "radio"
    assert bose.played_urls == [station["url"]]


def test_homepage_stations_use_pyradios_vote_search(monkeypatch):
    results = [
        {
            "name": f"Station {number}",
            "url": f"https://stream.example/{number}",
            "votes": 20 - number,
        }
        for number in range(1, 21)
    ]
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return results

    monkeypatch.setattr(routes._radio_browser, "search", search)

    stations = routes._top_stations()

    assert len(stations) == 20
    assert stations[0]["name"] == "Station 1"
    assert calls == [{"order": "votes", "reverse": True, "limit": 20}]


def test_search_route_passes_query_and_returns_station_cards(monkeypatch):
    results = [
        {
            "name": "Example FM",
            "url": "https://stream.example/radio",
            "votes": 100,
        }
    ]
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return results

    monkeypatch.setattr(routes._radio_browser, "search", search)
    app = create_radio_app(FakeBose())

    response = app.test_client().get("/radio/search?q=example")

    assert response.status_code == 200
    assert response.json[0]["name"] == "Example FM"
    assert calls == [{
        "name": "example",
        "limit": 20,
        "order": "votes",
        "reverse": True,
        "hidebroken": True,
    }]


def test_status_reads_current_stream_metadata():
    bose = FakeBose()
    app = create_radio_app(bose)
    app.playback.acquire("radio")
    app.radio_station = {
        "name": "Example FM",
        "url": "https://stream.example/radio",
        "favicon": "https://stream.example/logo.png",
    }
    app.radio_now_playing = None
    app.radio_metadata_updated_at = 0

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["station"]["title"] == "Test Artist - Test Song"
    assert response.json["station"]["station"] == "Example FM"
    assert bose.metadata_calls == ["https://stream.example/radio"]
