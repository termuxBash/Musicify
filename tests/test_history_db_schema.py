import core.history_db as history_db


def test_init_db_creates_canonical_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))

    history_db.init_db()

    assert (tmp_path / history_db.LYRICS_DB_FILENAME).is_file()
    with history_db.get_connection() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"songs", "history_tables", "history"} <= tables
        assert "lyrics" not in tables

        song_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(songs)").fetchall()
        }
        assert {
            "id", "work_key", "youtube_id", "local_path",
            "track_name", "artist_name", "album_name", "duration_ms", "tags",
        } <= song_columns
        assert "preferred_source" not in song_columns

    with history_db.get_lyrics_connection() as conn:
        lyric_tables = {
            row["name"] for row in conn.execute(
                "SELECT name FROM lyrics_db.sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "lyrics" in lyric_tables
        assert conn.execute(
            "PRAGMA lyrics_db.table_info(lyrics)"
        ).fetchall()[3]["type"] == "BLOB"


def test_history_entry_links_to_canonical_song(tmp_path, monkeypatch):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(history_db, "LASTFM_KEY", "", raising=False)
    history_db.init_db()

    row_id = history_db.add_entry(
        "history",
        "Cher - Believe",
        "nZXRV4MezEw",
        number_of_listens=2,
    )

    with history_db.get_connection() as conn:
        history_row = conn.execute(
            "SELECT song_pk FROM history WHERE id = ?", (row_id,)
        ).fetchone()
        song = conn.execute(
            "SELECT youtube_id, track_name FROM songs WHERE id = ?",
            (history_row["song_pk"],),
        ).fetchone()

    assert song["youtube_id"] == "nZXRV4MezEw"
    assert song["track_name"] == "Cher - Believe"


def test_local_song_merges_with_youtube_song_and_keeps_local_path(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    monkeypatch.setattr(
        history_db,
        "_read_local_song_metadata",
        lambda _path: {"duration_ms": 238000},
    )
    history_db.init_db()
    local_path = str(tmp_path / "Believe.mp3")

    local_id = history_db.upsert_local_song(
        local_path,
        "Believe.mp3",
        track_name="Believe",
        artist_name="Cher",
    )
    youtube_id = history_db.upsert_song(
        "nZXRV4MezEw",
        "Cher - Believe Official Video",
        track_name="Believe",
        artist_name="Cher",
        duration_ms=0,
    )

    assert youtube_id == local_id
    assert history_db.get_preferred_local_path(
        "nZXRV4MezEw", "Cher - Believe"
    ) == local_path
    with history_db.get_connection() as conn:
        song = conn.execute(
            "SELECT youtube_id, local_path, duration_ms FROM songs WHERE id = ?",
            (local_id,),
        ).fetchone()
    assert song["youtube_id"] == "nZXRV4MezEw"
    assert song["local_path"] == local_path
    assert song["duration_ms"] == 238000


def test_deleting_last_history_reference_deletes_song(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))
    history_db.init_db()

    row_id = history_db.add_entry(
        "history",
        "Believe",
        "nZXRV4MezEw",
    )
    assert history_db.delete_entry("history", row_id)

    with history_db.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM songs WHERE youtube_id = 'nZXRV4MezEw'"
        ).fetchone()[0] == 0
