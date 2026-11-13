import threading

from flask import Flask

from core import bose_routes, stats
from core.playback_controller import PlaybackController


class FakeBose:
    def __init__(self):
        self.is_on = True
        self.volume_levels = []
        self.power_toggles = 0

    def get_status(self):
        return {"is_on": self.is_on}

    def set_volume(self, level):
        self.volume_levels.append(level)
        return True

    def toggle_power(self):
        self.power_toggles += 1
        self.is_on = not self.is_on
        return True


class FakePlayer:
    def __init__(self):
        self.lock = threading.Lock()
        self.queue = [{"title": "Queued song"}]
        self.current_song = {"title": "Current song"}
        self.autoplay_enabled = True
        self.autoplay_suppressed = False
        self.skip_count = 0

    def skip(self):
        self.skip_count += 1

    def toggle_autoplay(self, enabled):
        self.autoplay_enabled = enabled

    def suppress_autoplay(self):
        self.autoplay_suppressed = True

    def resume_autoplay(self):
        self.autoplay_suppressed = False


def create_power_app():
    app = Flask(__name__)
    app.player = FakePlayer()
    app.playback = PlaybackController(app.player)
    app.playback.acquire("radio")
    app.register_blueprint(bose_routes.bose_control_bp)
    app.register_blueprint(stats.stats_bp)
    return app


def suppress_shutdown_thread(monkeypatch):
    class FakeThread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(stats.threading, "Thread", FakeThread)


def test_power_button_stops_playback_and_sets_volume_to_zero(monkeypatch):
    fake_bose = FakeBose()
    monkeypatch.setattr(bose_routes, "bose", fake_bose)
    app = create_power_app()

    response = app.test_client().post("/power")

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert app.player.queue == []
    assert app.player.current_song is None
    assert app.player.autoplay_enabled is False
    assert app.playback.owner is None
    assert fake_bose.volume_levels == [0]
    assert fake_bose.power_toggles == 1


def test_direct_bose_power_control_uses_same_power_off_behavior(monkeypatch):
    fake_bose = FakeBose()
    monkeypatch.setattr(bose_routes, "bose", fake_bose)
    app = create_power_app()

    response = app.test_client().get("/ctrl/bose_power")

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert app.player.queue == []
    assert app.player.current_song is None
    assert app.player.autoplay_enabled is False
    assert app.playback.owner is None
    assert fake_bose.volume_levels == [0]
    assert fake_bose.power_toggles == 1


def test_powering_on_does_not_clear_playback_or_zero_volume(monkeypatch):
    fake_bose = FakeBose()
    fake_bose.is_on = False
    monkeypatch.setattr(bose_routes, "bose", fake_bose)
    app = create_power_app()

    response = app.test_client().post("/power")

    assert response.status_code == 200
    assert app.player.queue == [{"title": "Queued song"}]
    assert app.player.current_song == {"title": "Current song"}
    assert app.player.autoplay_enabled is True
    assert app.playback.owner == "radio"
    assert fake_bose.volume_levels == []
    assert fake_bose.power_toggles == 1


def test_poweroff_stops_playback_and_checks_bose_after_clearing_queue(monkeypatch):
    fake_bose = FakeBose()
    monkeypatch.setattr(bose_routes, "bose", fake_bose)
    app = create_power_app()
    observed_playback_state = []
    get_status = fake_bose.get_status

    def get_status_after_playback_stops():
        observed_playback_state.append((
            list(app.player.queue),
            app.player.current_song,
            app.player.autoplay_enabled,
            app.playback.owner,
        ))
        return get_status()

    fake_bose.get_status = get_status_after_playback_stops

    response = app.test_client().get("/poweroff")

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert observed_playback_state == [([], None, False, None)]
    assert app.player.skip_count == 1
    assert fake_bose.volume_levels == [0]
    assert fake_bose.power_toggles == 1


def test_poweroff_does_not_toggle_bose_when_already_off(monkeypatch):
    fake_bose = FakeBose()
    fake_bose.is_on = False
    monkeypatch.setattr(bose_routes, "bose", fake_bose)
    app = create_power_app()

    response = app.test_client().get("/poweroff")

    assert response.status_code == 200
    assert response.json["status"] == "success"
    assert app.player.queue == []
    assert app.player.current_song is None
    assert app.player.autoplay_enabled is False
    assert app.playback.owner is None
    assert fake_bose.volume_levels == []
    assert fake_bose.power_toggles == 0


def test_shutdown_does_not_power_off_bose_when_radio_owns_playback(monkeypatch):
    app = create_power_app()
    power_off_calls = []
    monkeypatch.setattr(stats, "_power_off_bose", lambda: power_off_calls.append(True))
    suppress_shutdown_thread(monkeypatch)

    response = app.test_client().post("/shutdown")

    assert response.status_code == 200
    assert power_off_calls == []


def test_shutdown_still_powers_off_bose_for_other_owners(monkeypatch):
    app = create_power_app()
    app.playback.release("radio")
    app.playback.acquire("youtube")
    power_off_calls = []
    monkeypatch.setattr(stats, "_power_off_bose", lambda: power_off_calls.append(True))
    suppress_shutdown_thread(monkeypatch)

    response = app.test_client().post("/shutdown")

    assert response.status_code == 200
    assert power_off_calls == [True]
