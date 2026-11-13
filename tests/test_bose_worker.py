from io import BytesIO

import requests

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


def test_stream_metadata_skips_tracklist_links_and_uses_next_song_title(monkeypatch):
    stream_url = "https://stream.example/radio"
    titles = (
        "NO Example FM ## Tracklist: https://example.com/",
        "Artist - Current Song",
    )
    chunks = bytearray()
    for title in titles:
        metadata = f"StreamTitle='{title}';".encode()
        metadata += b"\0" * ((-len(metadata)) % 16)
        chunks.extend(b"x" + bytes([len(metadata) // 16]) + metadata)

    response = type(
        "StreamResponse",
        (),
        {
            "headers": {"icy-name": "Example FM", "icy-metaint": "1"},
            "raw": BytesIO(chunks),
            "raise_for_status": lambda self: None,
            "close": lambda self: None,
        },
    )()
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)

    metadata = BoseSoundTouchWorker().get_stream_metadata(stream_url)

    assert metadata["station"] == "Example FM"
    assert metadata["artist"] == "Artist"
    assert metadata["song"] == "Current Song"
    assert metadata["stream_title"] == "Artist - Current Song"
    assert metadata["metadata_available"] is True
