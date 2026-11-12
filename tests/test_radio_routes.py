import threading
import time

from flask import Flask
from flask import jsonify

from apps.radio import routes
from core import stats
from core.playback_controller import PlaybackController


class FakeBose:
    def __init__(self, play_result=True, playing_url=None, fallback_metadata=None):
        self.play_result = play_result
        self.playing_url = playing_url or {
            "success": True,
            "url": "https://stream.example/radio",
        }
        self.fallback_metadata = fallback_metadata
        self.played_urls = []
        self.metadata_calls = []

    def play_direct(self, stream_url):
        self.played_urls.append(stream_url)
        return self.play_result

    def get_playing_url(self):
        return self.playing_url

    def get_stream_metadata(self, stream_url=None):
        self.metadata_calls.append(stream_url)
        if stream_url is None and self.fallback_metadata is not None:
            return self.fallback_metadata
        return {
            "success": True,
            "url": stream_url,
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


def test_deleting_preset_clears_only_the_selected_slot(monkeypatch):
    saved = {}
    config = {
        **{str(number): None for number in range(1, 7)},
        "2": {"name": "Preset FM", "url": "https://stream.example/preset"},
        "favorites": [{"name": "Favorite FM", "url": "https://stream.example/favorite"}],
    }
    monkeypatch.setattr(routes, "get_radio_config", lambda: config.copy())
    monkeypatch.setattr(
        routes,
        "update_user_config",
        lambda key, value: saved.update({key: value}),
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().delete("/radio/preset/2")

    assert response.status_code == 200
    assert response.json == {"status": "deleted", "number": 2}
    assert saved["radio"]["2"] is None
    assert saved["radio"]["favorites"] == config["favorites"]


def test_deleting_favorite_keeps_other_favorites_and_presets(monkeypatch):
    saved = {}
    first_favorite = {"name": "First FM", "url": "https://stream.example/first"}
    second_favorite = {"name": "Second FM", "url": "https://stream.example/second"}
    config = {
        **{str(number): None for number in range(1, 7)},
        "3": {"name": "Preset FM", "url": "https://stream.example/preset"},
        "favorites": [first_favorite, second_favorite],
    }
    monkeypatch.setattr(routes, "get_radio_config", lambda: config.copy())
    monkeypatch.setattr(
        routes,
        "update_user_config",
        lambda key, value: saved.update({key: value}),
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().delete(
        "/radio/favorites",
        json={"url": first_favorite["url"]},
    )

    assert response.status_code == 200
    assert response.json["status"] == "deleted"
    assert saved["radio"]["3"] == config["3"]
    assert saved["radio"]["favorites"] == [second_favorite]


def test_deleting_unknown_favorite_returns_not_found(monkeypatch):
    monkeypatch.setattr(
        routes,
        "get_radio_config",
        lambda: {
            **{str(number): None for number in range(1, 7)},
            "favorites": [],
        },
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().delete(
        "/radio/favorites",
        json={"url": "https://stream.example/missing"},
    )

    assert response.status_code == 404


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


def test_favorite_get_route_matches_partial_name(monkeypatch):
    station = {"name": "101 SMOOTH JAZZ", "url": "https://stream.example/jazz"}
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

    response = app.test_client().get("/radio/fav/jaz")

    assert response.status_code == 302
    assert bose.played_urls == [station["url"]]


def test_homepage_stations_use_pyradios_vote_search(monkeypatch):
    results = [
        {
            "name": f"Station {number}",
            "url": f"https://stream.example/{number}",
            "votes": 20 - number,
        }
        for number in range(1, 19)
    ]
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return results

    monkeypatch.setattr(routes._radio_browser, "search", search)

    stations = routes._top_stations()

    assert len(stations) == 18
    assert stations[0]["name"] == "Station 1"
    assert calls == [{"order": "votes", "reverse": True, "limit": 18}]


def test_radio_browser_uses_official_api_host():
    assert routes._radio_browser.base_url == "https://de1.api.radio-browser.info/"


def test_search_route_filters_by_supported_genre(monkeypatch):
    results = [{"name": "Example Hip Hop", "url": "https://stream.example/hip-hop"}]
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return results

    monkeypatch.setattr(routes._radio_browser, "search", search)
    app = create_radio_app(FakeBose())

    response = app.test_client().get("/radio/search?tags=hip%20hop")

    assert response.status_code == 200
    assert response.json[0]["name"] == "Example Hip Hop"
    assert calls == [{
        "limit": 18,
        "order": "votes",
        "reverse": True,
        "hidebroken": True,
        "tag_list": "hip hop",
    }]


def test_search_route_combines_name_and_genre_filters(monkeypatch):
    calls = []
    monkeypatch.setattr(
        routes._radio_browser,
        "search",
        lambda **kwargs: calls.append(kwargs) or [],
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().get("/radio/search?q=live&tags=jazz")

    assert response.status_code == 200
    assert calls[0]["name"] == "live"
    assert calls[0]["tag_list"] == "jazz"


def test_search_route_supports_multiple_genres(monkeypatch):
    calls = []
    monkeypatch.setattr(
        routes._radio_browser,
        "search",
        lambda **kwargs: calls.append(kwargs) or [],
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().get("/radio/search?tags=pop%2Crock%2Cjazz")

    assert response.status_code == 200
    assert calls[0]["tag_list"] == "pop,rock,jazz"


def test_search_route_rejects_unsupported_genre(monkeypatch):
    monkeypatch.setattr(
        routes._radio_browser,
        "search",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("search should not run")),
    )
    app = create_radio_app(FakeBose())

    response = app.test_client().get("/radio/search?tags=not-a-genre")

    assert response.status_code == 400


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
        "limit": 18,
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
    assert response.json["is_playing"] is True
    assert bose.metadata_calls == ["https://stream.example/radio"]


def test_status_reports_failed_playback_when_speaker_has_no_stream_url():
    bose = FakeBose(playing_url={"success": False, "url": None})
    app = create_radio_app(bose)
    app.playback.acquire("radio")
    app.radio_station = {
        "name": "Selected FM",
        "url": "https://stream.example/selected",
        "favicon": "",
    }
    app.radio_playback_started_at = time.monotonic() - routes._PLAYBACK_START_GRACE_SECONDS - 1
    app.radio_now_playing = {"title": "Old station"}

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["is_playing"] is False
    assert response.json["pending"] is False
    assert response.json["error"]
    assert response.json["station"]["name"] == "Selected FM"
    assert app.radio_is_playing is False
    assert app.radio_playback_error == response.json["error"]


def test_status_accepts_http_retry_of_https_station():
    bose = FakeBose(playing_url={
        "success": True,
        "url": "http://stream.example/radio",
    })
    app = create_radio_app(bose)
    app.playback.acquire("radio")
    app.radio_station = {
        "name": "Example FM",
        "url": "https://stream.example/radio",
        "favicon": "",
    }
    app.radio_now_playing = None
    app.radio_metadata_updated_at = 0

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["is_playing"] is True


def test_status_uses_metadata_when_playing_url_is_unavailable():
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={
            "success": True,
            "url": "http://stream.example/radio",
            "station": "Example FM",
            "genre": "Dance",
            "song": "All about Dance from 2000 till today!",
            "artist": None,
            "stream_title": "All about Dance from 2000 till today!",
            "metadata_available": True,
        },
    )
    app = create_radio_app(bose)
    app.playback.acquire("radio")
    app.radio_station = {
        "name": "Example FM",
        "url": "https://stream.example/radio",
        "favicon": "",
    }
    app.radio_now_playing = None
    app.radio_metadata_updated_at = 0

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["is_playing"] is True
    assert response.json["station"]["station"] == "Example FM"
    assert response.json["station"]["title"] == "All about Dance from 2000 till today!"
    assert bose.metadata_calls == [None]


def test_status_shows_bose_metadata_when_stream_url_differs_from_selected_station():
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={
            "success": True,
            "url": "http://mangoradio.stream.laut.fm/mangoradio",
            "station": "mangoradio",
            "genre": "Electro",
            "bitrate": "128",
            "song": "If I Can't Have You",
            "artist": "Shawn Mendes",
            "stream_title": "Shawn Mendes - If I Can't Have You",
            "metadata_available": True,
        },
    )
    app = create_radio_app(bose)
    app.playback.acquire("radio")
    app.radio_station = {
        "name": "Selected FM",
        "url": "https://selected.example/radio",
        "favicon": "",
    }
    app.radio_now_playing = None
    app.radio_metadata_updated_at = 0

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["is_playing"] is True
    assert response.json["station"]["station"] == "mangoradio"
    assert response.json["station"]["title"] == "Shawn Mendes - If I Can't Have You"
    assert response.json["station"]["song"] == "If I Can't Have You"
    assert response.json["station"]["artist"] == "Shawn Mendes"
    assert response.json["station"]["genre"] == "Electro"
    assert response.json["station"]["bitrate"] == "128"
    assert response.json["station"]["stream_title"] == "Shawn Mendes - If I Can't Have You"
    assert response.json["station"]["url"] == "http://mangoradio.stream.laut.fm/mangoradio"
    assert bose.metadata_calls == [None]


def test_status_assigns_radio_owner_when_bose_radio_is_detected_at_startup():
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={
            "success": True,
            "url": "http://mangoradio.stream.laut.fm/mangoradio",
            "station": "mangoradio",
            "genre": "Electro",
            "bitrate": "128",
            "song": "Fading",
            "artist": "Alle Farben & Ilira",
            "stream_title": "Alle Farben & Ilira - Fading",
            "metadata_available": True,
        },
    )
    app = create_radio_app(bose)
    app.player.queue = [{"title": "Queued song"}]
    app.player.current_song = {"title": "Current song"}

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["is_radio"] is True
    assert response.json["is_playing"] is True
    assert response.json["station"]["station"] == "mangoradio"
    assert response.json["station"]["title"] == "Alle Farben & Ilira - Fading"
    assert response.json["station"]["song"] == "Fading"
    assert response.json["station"]["artist"] == "Alle Farben & Ilira"
    assert response.json["station"]["genre"] == "Electro"
    assert response.json["station"]["bitrate"] == "128"
    assert app.playback.owner == "radio"
    assert app.player.queue == [{"title": "Queued song"}]
    assert app.player.current_song == {"title": "Current song"}
    assert app.player.autoplay_suppressed
    assert bose.metadata_calls == [None]


def test_status_uses_stream_url_lookup_for_detected_station_thumbnail(monkeypatch):
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={
            "success": True,
            "url": "http://mangoradio.stream.laut.fm/mangoradio",
            "station": "mangoradio",
            "genre": "Electro",
            "bitrate": "128",
            "song": "Fading",
            "artist": "Alle Farben & Ilira",
            "stream_title": "Alle Farben & Ilira - Fading",
            "metadata_available": True,
        },
    )
    app = create_radio_app(bose)
    calls = []

    def search(**kwargs):
        calls.append(kwargs)
        return [{
            "name": "mangoradio",
            "url": "https://mangoradio.stream.laut.fm/mangoradio",
            "favicon": "https://images.example/mango-radio.png",
        }]

    monkeypatch.setattr(routes._radio_browser, "search", search)

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json["station"]["thumbnail"] == "https://images.example/mango-radio.png"
    assert response.json["station"]["favicon"] == "https://images.example/mango-radio.png"
    assert app.radio_station["favicon"] == "https://images.example/mango-radio.png"
    assert calls == [{
        "name": "mangoradio",
        "name_exact": True,
        "limit": 10,
    }]


def test_stats_keeps_confirmed_bose_radio_metadata_in_player_bar(monkeypatch):
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={
            "success": True,
            "url": "http://mangoradio.stream.laut.fm/mangoradio",
            "station": "mangoradio",
            "genre": "Electro",
            "bitrate": "128",
            "song": "Fading",
            "artist": "Alle Farben & Ilira",
            "stream_title": "Alle Farben & Ilira - Fading",
            "metadata_available": True,
        },
    )
    app = create_radio_app(bose)
    app.lyrics_service = type(
        "FakeLyricsService",
        (),
        {
            "enabled": False,
            "song_start_time": None,
            "reset": lambda self, title: None,
            "get_lyrics_state": lambda self: {},
        },
    )()
    app.incogni_mode = False
    app.last_known_title = None
    app.player.status = lambda: {
        "current_song": {"title": "Youtube song"},
        "playing": True,
        "queue": [],
        "autoplay_enabled": False,
    }
    monkeypatch.setattr(stats, "get_status", lambda: jsonify({"volume": 30}))
    app.register_blueprint(stats.stats_bp)
    client = app.test_client()

    radio_response = client.get("/radio/status")
    stats_response = client.get("/stats")

    assert radio_response.json["is_radio"] is True
    assert stats_response.json["owner"] == "radio"
    assert stats_response.json["is_radio"] is True
    assert stats_response.json["is_playing"] is True
    assert stats_response.json["now_playing"]["title"] == "Alle Farben & Ilira - Fading"


def test_status_remains_non_radio_for_other_playback_without_bose_metadata():
    bose = FakeBose(
        playing_url={"success": False, "url": None},
        fallback_metadata={"success": False, "url": None},
    )
    app = create_radio_app(bose)
    app.playback.acquire("youtube")

    response = app.test_client().get("/radio/status")

    assert response.status_code == 200
    assert response.json == {"is_radio": False}
    assert app.playback.owner == "youtube"


def test_failed_direct_play_is_reported_in_response_and_radio_state():
    app = create_radio_app(FakeBose(play_result=False))

    response = app.test_client().post(
        "/radio/play",
        json={"name": "Unavailable FM", "url": "https://stream.example/unavailable"},
    )

    assert response.status_code == 502
    assert response.json["error"]
    assert app.radio_is_playing is False
    assert app.radio_playback_error == response.json["error"]
