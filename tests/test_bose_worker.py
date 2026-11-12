from core.bose_worker import BoseSoundTouchWorker


def test_play_direct_uses_http_for_https_streams(monkeypatch):
    worker = BoseSoundTouchWorker(ip_address="127.0.0.1")
    attempted_urls = []

    def trigger_upnp_stream(stream_url):
        attempted_urls.append(stream_url)
        return True

    monkeypatch.setattr(worker, "trigger_upnp_stream", trigger_upnp_stream)

    assert worker.play_direct("https://stream.example/radio") is True
    assert attempted_urls == ["http://stream.example/radio"]


def test_play_direct_reports_http_stream_failure(monkeypatch):
    worker = BoseSoundTouchWorker(ip_address="127.0.0.1")
    attempted_urls = []

    def trigger_upnp_stream(stream_url):
        attempted_urls.append(stream_url)
        return False

    monkeypatch.setattr(worker, "trigger_upnp_stream", trigger_upnp_stream)

    assert worker.play_direct("https://stream.example/radio") is False
    assert attempted_urls == ["http://stream.example/radio"]


def test_play_direct_leaves_http_stream_unchanged(monkeypatch):
    worker = BoseSoundTouchWorker(ip_address="127.0.0.1")
    attempted_urls = []

    def trigger_upnp_stream(stream_url):
        attempted_urls.append(stream_url)
        return True

    monkeypatch.setattr(worker, "trigger_upnp_stream", trigger_upnp_stream)

    assert worker.play_direct("http://stream.example/radio") is True
    assert attempted_urls == ["http://stream.example/radio"]
