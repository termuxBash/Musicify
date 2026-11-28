import core.history_db as history_db


def test_init_db_creates_canonical_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(history_db, "PLAYLIST_DIR", str(tmp_path))

    history_db.init_db()

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
            "id", "work_key", "youtube_id", "local_path", "preferred_source",
            "track_name", "artist_name", "album_name", "duration_ms", "tags",
        } <= song_columns

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
