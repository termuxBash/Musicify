import app as musicify_app


def test_root_redirect_preserves_tv_query_parameter(monkeypatch):
    class FakePlayer:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(musicify_app, "setup_logging", lambda: None)
    monkeypatch.setattr(musicify_app, "LyricsService", lambda **_kwargs: object())
    monkeypatch.setattr(musicify_app, "QueuePlayer", FakePlayer)
    monkeypatch.setattr(musicify_app, "FFmpegService", lambda: object())
    monkeypatch.setattr(
        musicify_app,
        "BoseSoundTouchWorker",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        musicify_app,
        "initialize_radio_playback",
        lambda _app: None,
    )

    response = musicify_app.create_app().test_client().get(
        "/?tv&openModal=1&playOnDevice"
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        "/youtube/?tv&openModal=1&playOnDevice"
    )


def test_root_redirect_preserves_tvm_query_parameter(monkeypatch):
    class FakePlayer:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(musicify_app, "setup_logging", lambda: None)
    monkeypatch.setattr(musicify_app, "LyricsService", lambda **_kwargs: object())
    monkeypatch.setattr(musicify_app, "QueuePlayer", FakePlayer)
    monkeypatch.setattr(musicify_app, "FFmpegService", lambda: object())
    monkeypatch.setattr(
        musicify_app,
        "BoseSoundTouchWorker",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        musicify_app,
        "initialize_radio_playback",
        lambda _app: None,
    )

    response = musicify_app.create_app().test_client().get("/?tvm")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/youtube/?tvm")
