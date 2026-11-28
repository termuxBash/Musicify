import json
import zlib

import services.lyrics_service as lyrics_service
import core.history_db as history_db


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_lyrics_are_saved_and_reused_from_compact_json(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    history_db.init_db()
    monkeypatch.setattr(
        lyrics_service.LyricsService,
        "_resolve_artist_and_track",
        staticmethod(lambda _query: ("Artist", "Track")),
    )
    responses = iter([
        FakeResponse([{
            "syncedLyrics": "[00:01.00] Original lyric",
            "plainLyrics": "Original lyric",
        }]),
        FakeResponse({"lyrics": "Romanized lyric"}),
    ])
    requests_get = lambda *_args, **_kwargs: next(responses)
    monkeypatch.setattr(lyrics_service.requests, "get", requests_get)

    first_service = lyrics_service.LyricsService(lyrics_effort="LYRICA")
    title = "Artist - Track"
    first_service._load_lyrics(title, 0)

    assert first_service.current_lyrics == [(1.0, "Romanized lyric")]
    assert first_service.lyrics_source == "Lyrica"
    with history_db.get_lyrics_connection() as conn:
        row = conn.execute(
            """
            SELECT lyrics, source, typeof(lyrics) AS storage_type
            FROM lyrics_db.lyrics WHERE work_key = ?
            """,
            ("artist track",),
        ).fetchone()
    json_lyrics = zlib.decompress(row["lyrics"]).decode("utf-8")
    assert row["source"] == "Lyrica"
    assert row["storage_type"] == "blob"
    assert json.loads(json_lyrics) == [
        {"time": 1.0, "text": "Romanized lyric"}
    ]
    assert ", " not in json_lyrics
    assert ": " not in json_lyrics

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("Cached lyrics must avoid provider requests")

    monkeypatch.setattr(lyrics_service.requests, "get", unexpected_request)
    cached_service = lyrics_service.LyricsService(lyrics_effort="GEMINI")
    cached_service._load_lyrics(title, 0)

    assert cached_service.current_lyrics == [(1.0, "Romanized lyric")]
    assert cached_service.lyrics_source == "Lyrica"


def test_lrclib_result_is_cached_when_no_higher_effort_is_requested(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    history_db.init_db()
    monkeypatch.setattr(
        lyrics_service.LyricsService,
        "_resolve_artist_and_track",
        staticmethod(lambda _query: ("Artist", "Track")),
    )
    monkeypatch.setattr(
        lyrics_service.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse([{
            "syncedLyrics": "[00:02.00] Cached lyric",
            "plainLyrics": "Cached lyric",
        }]),
    )

    service = lyrics_service.LyricsService(lyrics_effort="LRCLIB")
    service._load_lyrics("Artist - Track", 0)

    assert history_db.get_lyrics_cache("Track", "Artist") == (
        [(2.0, "Cached lyric")],
        "LRCLIB",
    )


def test_legacy_lyrics_are_moved_to_separate_compressed_database(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    history_db.init_db()
    with history_db.get_connection() as conn:
        conn.execute("""
            CREATE TABLE lyrics (
                id INTEGER PRIMARY KEY,
                song_id INTEGER,
                work_key TEXT NOT NULL UNIQUE,
                lyrics TEXT,
                source TEXT,
                updated_at INTEGER
            )
        """)
        conn.execute(
            """
            INSERT INTO lyrics (song_id, work_key, lyrics, source, updated_at)
            VALUES (NULL, ?, ?, ?, ?)
            """,
            (
                "artist track",
                '[{"time":3.0,"text":"Previously cached"}]',
                "Lyrica",
                123,
            ),
        )

    assert history_db.get_lyrics_cache("Track", "Artist") == (
        [(3.0, "Previously cached")],
        "Lyrica",
    )
    with history_db.get_connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lyrics'"
        ).fetchone() is None
