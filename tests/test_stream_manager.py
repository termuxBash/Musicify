from flask import Flask

from apps.local import routes as local_routes
from core.stream_manager import QueuePlayer


class FakeFFmpeg:
    def __init__(self):
        self.cleared = []
        self.cleanup_calls = []

    def clear_cached_song(self, path):
        self.cleared.append(path)

    def cleanup_cache(self, keep_paths=()):
        self.cleanup_calls.append(list(keep_paths))


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
            "thumbnail": "/static/music-note.svg",
            "url": "/music/next-track.mp3",
        }
    ]


def test_youtube_queue_uses_registered_local_song(tmp_path, monkeypatch):
    import core.history_db as history_db

    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path / "db"))
    history_db.init_db()
    local_path = tmp_path / "local-copy.mp3"
    local_path.write_bytes(b"audio")
    history_db.upsert_local_song(
        str(local_path),
        "Track.mp3",
        track_name="Track",
        artist_name="Artist",
    )
    history_db.upsert_song(
        "nZXRV4MezEw",
        "Artist - Track Official Video",
        track_name="Track",
        artist_name="Artist",
    )

    player = QueuePlayer(FakeFFmpeg(), autoplay_enabled=False)
    song_payload = {
        "title": "Artist - Track Official Video",
        "videoId": "nZXRV4MezEw",
        "source_url": "https://youtube.com/watch?v=nZXRV4MezEw",
        "cache_path": "/cache/old-youtube-copy.m4a",
    }
    player.enqueue(song_payload)
    player._ready_cache["nZXRV4MezEw"] = "/cache/old-youtube-copy.m4a"

    song = player._get_next_song()

    assert song["url"] == str(local_path)
    assert song["local_path"] == str(local_path)
    assert song["_local_override"] is True
    assert song["videoId"] == "nZXRV4MezEw"
    assert "cache_path" not in song
    assert "nZXRV4MezEw" not in player._ready_cache


def test_cache_files_are_only_cleaned_when_autoplay_runs(monkeypatch):
    app = Flask(__name__)
    ffmpeg = FakeFFmpeg()
    player = QueuePlayer(ffmpeg, autoplay_enabled=True, flask_app=app)
    player.enqueue({"title": "Queued", "cache_path": "/cache/queued.m4a"})
    player._ready_cache["queued"] = "/cache/queued.m4a"

    player.remove_from_queue(0)
    player.stop()

    assert ffmpeg.cleared == []
    assert ffmpeg.cleanup_calls == []

    monkeypatch.setattr(local_routes, "get_random_local_track_payload", lambda: None)
    player._trigger_autoplay({"title": "Current", "url": "/music/current.mp3"})

    assert len(ffmpeg.cleanup_calls) == 1
