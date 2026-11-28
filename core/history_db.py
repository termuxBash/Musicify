"""core.history_db - SQLite-backed playback history manager for Musicify."""

import subprocess
import os
import json
import re
import sqlite3
import threading
import time
import unicodedata
import zlib
from datetime import datetime
import logging
from core.settings import (
    DB_FILENAME,
    PLAYLIST_DIR,
    TIME_OFFSET,
    get_history_table,
    set_history_table,
)

logger = logging.getLogger(__name__)

_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_active_table_lock = threading.Lock()
_active_table_name = None

RICK_ROLL_VIDEO_ID = "dQw4w9WgXcQ"
RICK_ROLL_ALLOWED_PHRASE = "never gonna give you up"
LYRICS_DB_FILENAME = "lyrics.sqlite3"


def is_blacklisted_rick_roll(video_id_or_song, song_name=None):
    """Return True if video_id is 'dQw4w9WgXcQ' and not accompanied by 'Never Gonna Give You Up'.

    Blacklists video id 'dQw4w9WgXcQ' if it is NOT accompanied by a song name
    that contains 'Never Gonna Give You Up' (case-insensitive).
    Accepts either (video_id, song_name) or a song dictionary.
    """
    if isinstance(video_id_or_song, dict):
        raw_id = (
            video_id_or_song.get("videoId")
            or video_id_or_song.get("song_id")
            or video_id_or_song.get("source_url")
            or video_id_or_song.get("url")
            or video_id_or_song.get("source")
            or video_id_or_song.get("id")
            or ""
        )
        if song_name is None:
            song_name = (
                video_id_or_song.get("title")
                or video_id_or_song.get("song_name")
                or ""
            )
    else:
        raw_id = str(video_id_or_song or "")

    vid = normalize_video_id(raw_id)
    if vid == RICK_ROLL_VIDEO_ID:
        name = str(song_name or "").lower()
        if RICK_ROLL_ALLOWED_PHRASE not in name:
            return True
    return False


def record_rick_roll_timestamp(timestamp=None):
    """Add the timestamp in table 'extra_data' in column 'rick_roll_timestamp'.

    Creates extra_data table if not exists, and adds a timestamp row
    each time a blacklisted Rick Roll is detected or tries to play.
    """
    if timestamp is None:
        timestamp = str(int(time.time()))
    else:
        timestamp = str(timestamp)

    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extra_data (
                rick_roll_timestamp TEXT NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO extra_data (rick_roll_timestamp) VALUES (?);",
            (timestamp,)
        )
        conn.commit()


def sanitize_table_name(name):
    """Sanitize any input string into a safe SQLite table identifier.

    Only allows alphanumeric characters and underscores.  Strips path
    components and file extensions so callers can pass raw filenames.
    """
    if not name:
        return "history"
    base = os.path.splitext(os.path.basename(str(name).strip()))[0]
    clean = re.sub(r'[^a-zA-Z0-9_]', '_', base).strip('_')
    return clean if clean else "history"


# Pre-compiled pattern used by _safe_table for the final hard check
_SAFE_TABLE_RE = re.compile(r'^[A-Za-z0-9_]+$')


def _safe_table(name, allow_create=False):
    """Return a sanitised table name that is guaranteed injection-free.

    This is the single choke-point used by every SQL-executing function.
    It sanitises the raw name **and** then performs a strict allowlist
    re-check on the result so that, even if sanitize_table_name were
    somehow bypassed, no dangerous characters could reach the SQL string.

    Raises ValueError if the sanitised name fails the allowlist check
    or if it is not an existing, server-known table.
    """
    clean = sanitize_table_name(name)
    if not _SAFE_TABLE_RE.fullmatch(clean):
        raise ValueError(f"Rejected unsafe table name after sanitisation: {clean!r}")

    if allow_create or clean == "history":
        return clean

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        known_tables = {row["name"] for row in cursor.fetchall()}

    if clean in known_tables:
        return clean

    raise ValueError(f"Rejected unknown table name: {clean!r}")


def normalize_video_id(value):
    """Return a strict YouTube video ID, removing surrounding response punctuation."""
    text = str(value or "").strip().strip("\"'`")
    if _YOUTUBE_ID_RE.fullmatch(text):
        return text
    match = _YOUTUBE_ID_TOKEN_RE.search(text)
    return match.group(1) if match else ""


def get_db_path():
    """Return the absolute path to history.sqlite3 in PLAYLIST_DIR."""
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    return os.path.join(PLAYLIST_DIR, DB_FILENAME)


def get_lyrics_db_path():
    """Return the separate lyrics database path."""
    os.makedirs(PLAYLIST_DIR, exist_ok=True)
    return os.path.join(PLAYLIST_DIR, LYRICS_DB_FILENAME)


def get_active_table():
    """Return the currently selected active table name."""
    global _active_table_name
    with _active_table_lock:
        if _active_table_name is None:
            # Initialize from settings/environment
            _active_table_name = sanitize_table_name(get_history_table())
        return _active_table_name


def set_active_table(name):
    """Set the active table name and synchronize settings."""
    global _active_table_name
    clean = sanitize_table_name(name)
    with _active_table_lock:
        _active_table_name = clean
    set_history_table(clean)
    create_table(clean)
    return clean


def get_connection():
    """Create a thread-safe connection to history.sqlite3 with WAL enabled."""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def timestamp_to_iso_datetime(offset):
    """Convert stored integer timestamp offset to an ISO datetime string for HTML inputs."""
    try:
        if offset is None or offset == "":
            return datetime.now().strftime("%Y-%m-%dT%H:%M")
        unix_ts = int(offset) + TIME_OFFSET
        return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return datetime.now().strftime("%Y-%m-%dT%H:%M")


def iso_datetime_to_timestamp(dt_str):
    """Convert an ISO datetime string (from HTML datetime-local) to stored integer offset from TIME_OFFSET."""
    try:
        dt = datetime.fromisoformat(str(dt_str).strip())
        unix_ts = int(dt.timestamp())
        return max(0, unix_ts - TIME_OFFSET)
    except Exception:
        return max(0, int(time.time()) - TIME_OFFSET)


def _ensure_core_schema():
    """Create canonical library tables before creating or opening history tables."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_key TEXT,
                youtube_id TEXT,
                youtube_title TEXT,
                local_path TEXT,
                track_name TEXT NOT NULL,
                artist_name TEXT,
                album_name TEXT,
                duration_ms INTEGER,
                listeners INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                created_at INTEGER NOT NULL DEFAULT (unixepoch())
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_songs_youtube_id
                ON songs(youtube_id) WHERE youtube_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_songs_local_path
                ON songs(local_path) WHERE local_path IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_songs_work_key ON songs(work_key);

            CREATE TABLE IF NOT EXISTS history_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL UNIQUE,
                created_at INTEGER NOT NULL DEFAULT (unixepoch())
            );
        """)
        conn.commit()


def _ensure_lyrics_schema(conn):
    """Create the compressed lyric table in the attached lyrics database."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lyrics_db.lyrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_id INTEGER,
            work_key TEXT NOT NULL UNIQUE,
            lyrics BLOB,
            source TEXT,
            updated_at INTEGER NOT NULL DEFAULT (unixepoch())
        )
    """)


def _attach_lyrics_database(conn):
    """Attach the separate lyrics database and ensure its compressed table."""
    conn.execute(
        "ATTACH DATABASE ? AS lyrics_db",
        (get_lyrics_db_path(),),
    )
    _ensure_lyrics_schema(conn)


def get_lyrics_connection():
    """Return a connection with the separate compressed lyrics database attached."""
    conn = get_connection()
    try:
        _attach_lyrics_database(conn)
    except Exception:
        conn.close()
        raise
    return conn


def get_all_registered_tables():
    """Return history table names from the registry."""
    _ensure_core_schema()
    with get_connection() as conn:
        return [
            row["table_name"]
            for row in conn.execute(
                "SELECT table_name FROM history_tables ORDER BY table_name"
            ).fetchall()
        ]


def create_table(table_name):
    """Create a history table if it doesn't already exist.

    ``song_id`` carries a UNIQUE constraint so that the same YouTube video
    can never produce two rows in the same table regardless of how the song
    name was spelled at record time. ``song_pk`` links the row to the canonical
    songs table while retaining the YouTube ID column.
    """
    clean = _safe_table(table_name, allow_create=True)
    _ensure_core_schema()
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{clean}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id   TEXT NOT NULL UNIQUE,
                song_pk   INTEGER,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp INTEGER NOT NULL,
                FOREIGN KEY (song_pk) REFERENCES songs(id) ON DELETE CASCADE
            );
        """)
        conn.execute("""
            INSERT OR IGNORE INTO history_tables (table_name)
            VALUES (?);
        """, (clean,))
        # Fast lookup by song_id (covering unique constraint already indexed,
        # but the composite index below speeds up name-based searches).
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS "idx_{clean}_song" ON "{clean}" (song_name, song_id);
        """)
        conn.commit()
    return clean


def get_all_tables():
    """Return a list of all custom tables currently in history.sqlite3."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
              AND name NOT IN (
                  'extra_data', 'gemini_usage', 'songs', 'history_tables'
              )
            ORDER BY name;
        """)
        tables = [row["name"] for row in cursor.fetchall()]
    if "history" not in tables:
        create_table("history")
        tables.insert(0, "history")
    return tables


def init_db():
    """Initialize canonical song tables, registry, and default history table."""
    _ensure_core_schema()
    with get_lyrics_connection() as conn:
        conn.commit()
    create_table("history")
    active = get_active_table()
    if active != "history":
        create_table(active)

    # Initialize extra_data table for tracking blacklisted attempts
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extra_data (
                rick_roll_timestamp TEXT NOT NULL
            );
        """)
        conn.commit()

    init_gemini_usage_db()


def _normalize_work_key(track_name, artist_name=None):
    text = " - ".join(part.strip() for part in (artist_name, track_name) if part and str(part).strip())
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[_\W]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def get_lyrics_cache(track_name, artist_name=None, song_id=None):
    """Return cached lyric lines and their provider for a normalized work key."""
    import json

    work_key = _normalize_work_key(track_name, artist_name)
    if not work_key:
        return None

    with get_lyrics_connection() as conn:
        row = conn.execute(
            """
            SELECT lyrics, source
            FROM lyrics_db.lyrics
            WHERE work_key = ?
            """,
            (work_key,),
        ).fetchone()
        if row is None and song_id is not None:
            row = conn.execute(
                """
                SELECT lyrics, source
                FROM lyrics_db.lyrics
                WHERE song_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (song_id,),
            ).fetchone()
    if not row or not row["lyrics"]:
        return None

    try:
        payload = json.loads(zlib.decompress(row["lyrics"]).decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("Cached lyrics must be a JSON array")
        lyric_lines = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("Cached lyric line has an invalid shape")
            timestamp = item.get("time")
            lyric_lines.append((float(timestamp) if timestamp is not None else None, item["text"]))
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError, zlib.error) as error:
        logger.warning("Ignoring invalid cached lyrics for %s: %s", work_key, error)
        return None

    return lyric_lines, row["source"] or "Cached"


def save_lyrics_cache(track_name, artist_name, lyric_lines, source, song_id=None):
    """Persist lyric lines as compact JSON under a normalized work key."""
    import json

    work_key = _normalize_work_key(track_name, artist_name)
    if not work_key or not lyric_lines:
        return False

    payload = json.dumps(
        [{"time": timestamp, "text": text} for timestamp, text in lyric_lines],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    compressed = zlib.compress(payload)
    with get_lyrics_connection() as conn:
        if song_id is None:
            song = conn.execute(
                "SELECT id FROM songs WHERE work_key = ? LIMIT 1",
                (work_key,),
            ).fetchone()
            song_id = song["id"] if song else None
        conn.execute("""
            INSERT INTO lyrics_db.lyrics
                (song_id, work_key, lyrics, source, updated_at)
            VALUES (?, ?, ?, ?, unixepoch())
            ON CONFLICT(work_key) DO UPDATE SET
                song_id = COALESCE(excluded.song_id, song_id),
                lyrics = excluded.lyrics,
                source = excluded.source,
                updated_at = unixepoch()
        """, (
            song_id,
            work_key,
            compressed,
            source,
        ))
        conn.commit()
    return True


def upsert_song(video_id, title, track_name=None, artist_name=None, album_name=None,
                duration_ms=None, listeners=None, tags=None, local_path=None):
    """Insert or update a canonical song and return its stable integer ID."""
    _ensure_core_schema()
    sid = normalize_video_id(video_id)
    if not sid:
        return None

    resolved_track = (track_name or title or "").strip()
    work_key = _normalize_work_key(resolved_track, artist_name)
    if not resolved_track:
        return None

    import json
    tags_json = json.dumps(tags if isinstance(tags, list) else [])

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, local_path FROM songs WHERE youtube_id = ?",
            (sid,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT id, local_path FROM songs
                WHERE work_key = ?
                ORDER BY local_path IS NOT NULL DESC, id
                LIMIT 1
                """,
                (work_key,),
            ).fetchone()
        if row:
            song_id = row["id"]
            known_local_path = row["local_path"] or local_path
            if known_local_path and os.path.isfile(known_local_path):
                local_metadata = _read_local_song_metadata(known_local_path)
                duration_ms = local_metadata.get("duration_ms") or duration_ms
            conn.execute("""
                UPDATE songs
                SET work_key = COALESCE(NULLIF(?, ''), work_key),
                    youtube_id = COALESCE(youtube_id, ?),
                    youtube_title = COALESCE(NULLIF(?, ''), youtube_title),
                    local_path = COALESCE(?, local_path),
                    track_name = COALESCE(NULLIF(?, ''), track_name),
                    artist_name = COALESCE(?, artist_name),
                    album_name = COALESCE(?, album_name),
                    duration_ms = COALESCE(NULLIF(?, 0), duration_ms),
                    listeners = COALESCE(?, listeners),
                    tags = CASE WHEN ? <> '[]' THEN ? ELSE tags END
                WHERE id = ?
            """, (
                work_key,
                sid,
                title,
                local_path,
                resolved_track,
                artist_name,
                album_name,
                duration_ms,
                listeners,
                tags_json,
                tags_json,
                song_id,
            ))
        else:
            cursor = conn.execute("""
                INSERT INTO songs (
                    work_key, youtube_id, youtube_title, local_path,
                    track_name, artist_name, album_name, duration_ms, listeners, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                work_key,
                sid,
                title,
                local_path,
                resolved_track,
                artist_name,
                album_name,
                duration_ms,
                listeners if listeners is not None else 0,
                tags_json,
            ))
            song_id = cursor.lastrowid
        conn.commit()
        return song_id


def _read_local_song_metadata(path):
    """Read duration and common tags from a local media file using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:format_tags=artist,title,album:stream_tags=artist,title,album",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        logger.debug("Could not read local media metadata from %s: %s", path, error)
        return {}

    metadata = {}
    format_data = payload.get("format")
    if isinstance(format_data, dict):
        try:
            duration_seconds = float(format_data.get("duration"))
            if duration_seconds > 0:
                metadata["duration_ms"] = round(duration_seconds * 1000)
        except (TypeError, ValueError):
            pass
        tags = format_data.get("tags")
        if isinstance(tags, dict):
            normalized_tags = {str(key).lower(): value for key, value in tags.items()}
            for key, field in (("artist", "artist_name"), ("album", "album_name")):
                if isinstance(normalized_tags.get(key), str):
                    metadata[field] = normalized_tags[key]
    return metadata


def upsert_local_song(local_path, title, track_name=None, artist_name=None,
                      album_name=None, duration_ms=None, listeners=None, tags=None):
    """Store a local file and merge it with a matching YouTube-only song."""
    if not isinstance(local_path, str) or not local_path.strip():
        return None

    path = os.path.abspath(os.path.expanduser(local_path.strip()))
    media_metadata = (
        _read_local_song_metadata(path)
        if duration_ms is None or artist_name is None or album_name is None
        else {}
    )
    if duration_ms is None:
        duration_ms = media_metadata.get("duration_ms")
    if artist_name is None:
        artist_name = media_metadata.get("artist_name")
    if album_name is None:
        album_name = media_metadata.get("album_name")
    resolved_track = (track_name or title or "").strip()
    work_key = _normalize_work_key(resolved_track, artist_name)
    if not work_key or not resolved_track:
        return None

    import json
    tags_json = json.dumps(tags if isinstance(tags, list) else [])
    _ensure_core_schema()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM songs WHERE local_path = ?",
            (path,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT id FROM songs
                WHERE work_key = ?
                ORDER BY local_path IS NOT NULL DESC, id
                LIMIT 1
                """,
                (work_key,),
            ).fetchone()

        if row:
            song_id = row["id"]
            conn.execute("""
                UPDATE songs
                SET work_key = ?,
                    youtube_title = COALESCE(NULLIF(?, ''), youtube_title),
                    local_path = ?,
                    track_name = ?,
                    artist_name = COALESCE(?, artist_name),
                    album_name = COALESCE(?, album_name),
                    duration_ms = COALESCE(?, duration_ms),
                    listeners = COALESCE(?, listeners),
                    tags = CASE WHEN ? <> '[]' THEN ? ELSE tags END
                WHERE id = ?
            """, (
                work_key, title, path, resolved_track, artist_name, album_name,
                duration_ms, listeners, tags_json, tags_json, song_id,
            ))
        else:
            cursor = conn.execute("""
                INSERT INTO songs (
                    work_key, local_path, track_name,
                    youtube_title, artist_name, album_name, duration_ms,
                    listeners, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                work_key, path, resolved_track, title, artist_name, album_name,
                duration_ms, listeners if listeners is not None else 0, tags_json,
            ))
            song_id = cursor.lastrowid
        conn.commit()
        return song_id


def get_preferred_local_path(video_id=None, title=None):
    """Return a known local file for a YouTube song or normalized work title."""
    sid = normalize_video_id(video_id)
    work_key = _normalize_work_key(title or "")
    _ensure_core_schema()
    with get_connection() as conn:
        row = None
        if sid:
            row = conn.execute(
                """
                SELECT local_path FROM songs
                WHERE youtube_id = ? AND local_path IS NOT NULL
                """,
                (sid,),
            ).fetchone()
        if row is None and work_key:
            row = conn.execute(
                """
                SELECT local_path FROM songs
                WHERE work_key = ? AND local_path IS NOT NULL
                ORDER BY id
                LIMIT 1
                """,
                (work_key,),
            ).fetchone()
    return row["local_path"] if row else None


def record_played_song(song, table_name=None):
    """Record or increment the play count of a song in the specified (or active) history table.

    Matches exclusively on ``song_id`` so that the same YouTube video is
    always merged into one row, regardless of how Gemini or any other source
    spelled the song name.
    """
    if not song.get("record_history"):
        return

    title = (song.get("title") or "").strip()
    video_id = normalize_video_id(song.get("videoId"))
    if not title or not video_id:
        return

    if is_blacklisted_rick_roll(video_id, title):
        logger.warning(
            "Preventing blacklisted Rick Roll from being recorded to history: %s (%s)",
            title,
            video_id,
        )
        return

    table = _safe_table(table_name or get_active_table())
    create_table(table)
    elapsed_timestamp = max(0, int(time.time()) - TIME_OFFSET)
    metadata = {}
    try:
        from core import settings
        from services.lastfm_service import get_track_info, resolve_track

        if settings.LASTFM_KEY:
            artist, track_name = resolve_track(title, settings.LASTFM_KEY)
            if artist and track_name:
                metadata = get_track_info(
                    artist,
                    track_name,
                    settings.LASTFM_KEY,
                ) or {}
    except Exception as error:
        logger.warning("Could not fetch Last.fm metadata for %s: %s", title, error)

    song_pk = upsert_song(
        video_id,
        title,
        track_name=metadata.get("track_name"),
        artist_name=metadata.get("artist_name"),
        album_name=metadata.get("album_name"),
        duration_ms=metadata.get("duration_ms"),
        listeners=metadata.get("listeners"),
        tags=metadata.get("tags"),
        local_path=song.get("local_path"),
    )

    with get_connection() as conn:
        cursor = conn.cursor()
        # Look up solely by video ID — name variations are irrelevant
        cursor.execute(
            f'SELECT id, number_of_listens, song_pk FROM "{table}" WHERE song_id = ? LIMIT 1;',
            (video_id,)
        )
        row = cursor.fetchone()

        if row:
            cursor.execute(
                f'UPDATE "{table}" SET number_of_listens = ?, timestamp = ?, song_pk = ? WHERE id = ?;',
                (row["number_of_listens"] + 1, elapsed_timestamp, song_pk, row["id"])
            )
        else:
            # INSERT OR IGNORE is safe here: if a concurrent insert sneaked in,
            # we simply skip — the next play will find and update the row.
            cursor.execute(
                f'INSERT OR IGNORE INTO "{table}" (song_name, song_id, song_pk, number_of_listens, timestamp) VALUES (?, ?, ?, 1, ?);',
                (title, video_id, song_pk, elapsed_timestamp)
            )
        conn.commit()


def get_history_text(table_name=None):
    """Return all history entries as formatted text matching legacy history.txt for recommendation engines."""
    init_db()
    table = _safe_table(table_name or get_active_table())
    create_table(table)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT song_name, song_id, number_of_listens, timestamp FROM "{table}" ORDER BY id ASC;')
        rows = cursor.fetchall()

    lines = [f"{r['song_name']}>{r['song_id']}>{r['number_of_listens']}>{r['timestamp']}" for r in rows]
    return "\n".join(lines) + ("\n" if lines else "")


def get_history_rows(table_name=None):
    """Return all rows from the specified table formatted with datetime strings for web display."""
    init_db()
    table = _safe_table(table_name or get_active_table())
    if table in ("extra_data", "gemini_usage"):
        return []
    create_table(table)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT id, song_name, song_id, number_of_listens, timestamp FROM "{table}" ORDER BY id DESC;')
        rows = cursor.fetchall()

    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "song_name": r["song_name"],
            "song_id": r["song_id"],
            "number_of_listens": r["number_of_listens"],
            "timestamp": r["timestamp"],
            "datetime_str": timestamp_to_iso_datetime(r["timestamp"])
        })
    return result


def add_entry(table_name, song_name, song_id, number_of_listens=1, timestamp=None, datetime_str=None):
    """Add a new row into the specified table, or accumulate listens if song_id already exists.

    Because ``song_id`` is UNIQUE, attempting to insert a duplicate would
    raise a constraint error.  Instead we merge: the existing listen count is
    incremented by ``number_of_listens`` and the timestamp is updated to the
    newer value.  The existing song_name is preserved so manual edits are not
    overwritten.
    """
    table = _safe_table(table_name)
    create_table(table)

    if datetime_str:
        ts = iso_datetime_to_timestamp(datetime_str)
    elif timestamp is not None and str(timestamp).strip() != "":
        ts = int(timestamp)
    else:
        ts = max(0, int(time.time()) - TIME_OFFSET)

    sid = normalize_video_id(song_id)
    sname = song_name.strip()
    listens = int(number_of_listens)
    if not sid:
        raise ValueError("song_id must be a valid 11-character YouTube video ID")
    song_pk = upsert_song(sid, sname)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'SELECT id, number_of_listens FROM "{table}" WHERE song_id = ? LIMIT 1;',
            (sid,)
        )
        existing = cursor.fetchone()

        if existing:
            # Accumulate listens rather than inserting a duplicate
            cursor.execute(
                f'UPDATE "{table}" SET number_of_listens = ?, timestamp = ?, song_pk = ? WHERE id = ?;',
                (existing["number_of_listens"] + listens, ts, song_pk, existing["id"])
            )
            conn.commit()
            return existing["id"]
        else:
            cursor.execute(
                f'INSERT INTO "{table}" (song_name, song_id, song_pk, number_of_listens, timestamp) VALUES (?, ?, ?, ?, ?);',
                (sname, sid, song_pk, listens, ts)
            )
            conn.commit()
            return cursor.lastrowid


def update_entry(table_name, row_id, song_name, song_id, number_of_listens, timestamp=None, datetime_str=None):
    """Update an existing row by ID."""
    table = _safe_table(table_name)
    normalized_id = normalize_video_id(song_id)
    if not normalized_id:
        raise ValueError("song_id must be a valid 11-character YouTube video ID")
    song_pk = upsert_song(normalized_id, song_name)

    if datetime_str:
        ts = iso_datetime_to_timestamp(datetime_str)
    elif timestamp is not None and str(timestamp).strip() != "":
        ts = int(timestamp)
    else:
        ts = max(0, int(time.time()) - TIME_OFFSET)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'UPDATE "{table}" SET song_name = ?, song_id = ?, song_pk = ?, number_of_listens = ?, timestamp = ? WHERE id = ?;',
            (song_name.strip(), normalized_id, song_pk, int(number_of_listens), ts, int(row_id))
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_entry(table_name, row_id):
    """Delete a row by ID from the specified table."""
    table = _safe_table(table_name)
    with get_connection() as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            f'SELECT song_pk FROM "{table}" WHERE id = ?;',
            (int(row_id),),
        ).fetchone()
        cursor.execute(f'DELETE FROM "{table}" WHERE id = ?;', (int(row_id),))
        deleted = cursor.rowcount > 0
        if row and row["song_pk"] is not None:
            song_pk = row["song_pk"]
            referenced = False
            for history_row in cursor.execute(
                "SELECT table_name FROM history_tables"
            ).fetchall():
                history_table = history_row["table_name"]
                if not _SAFE_TABLE_RE.fullmatch(history_table):
                    continue
                columns = {
                    column["name"]
                    for column in cursor.execute(
                        f'PRAGMA table_info("{history_table}")'
                    ).fetchall()
                }
                if "song_pk" not in columns:
                    continue
                if cursor.execute(
                    f'SELECT 1 FROM "{history_table}" WHERE song_pk = ? LIMIT 1;',
                    (song_pk,),
                ).fetchone():
                    referenced = True
                    break
            if not referenced:
                _attach_lyrics_database(conn)
                conn.execute(
                    "UPDATE lyrics_db.lyrics SET song_id = NULL WHERE song_id = ?",
                    (song_pk,),
                )
                cursor.execute("DELETE FROM songs WHERE id = ?", (song_pk,))
        conn.commit()
        return deleted


def delete_table(table_name):
    """Delete an entire table (cannot delete default 'history' or 'extra_data')."""
    clean = _safe_table(table_name)
    if clean in ("history", "extra_data", "gemini_usage"):
        return False

    with get_connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{clean}";')
        conn.execute("DELETE FROM history_tables WHERE table_name = ?", (clean,))
        conn.commit()

    if get_active_table() == clean:
        set_active_table("history")
    return True


_gemini_db_lock = threading.Lock()
_gemini_db_initialized = False

GEMINI_SOURCE_AUTOPLAY = 0
GEMINI_SOURCE_LYRICS = 1


def init_gemini_usage_db(conn=None):
    """Ensure the gemini_usage table and token views exist in history.sqlite3."""
    global _gemini_db_initialized
    sql_statements = """
    CREATE TABLE IF NOT EXISTS gemini_usage (
        timestamp INTEGER NOT NULL DEFAULT (unixepoch()),
        source_code INTEGER NOT NULL,
        prompt_token_count INTEGER NOT NULL,
        candidates_token_count INTEGER NOT NULL
    );

    CREATE VIEW IF NOT EXISTS Gemini_tokens AS
    SELECT
        datetime(timestamp, 'unixepoch', 'localtime') AS "DateTime",
        CASE source_code
            WHEN 0 THEN 'autoplay'
            WHEN 1 THEN 'lyrics'
        END AS "Source",
        prompt_token_count AS "Input Tokens",
        candidates_token_count AS "Output Tokens",
        prompt_token_count + candidates_token_count AS "Total Tokens"
    FROM gemini_usage;

    CREATE VIEW IF NOT EXISTS Gemini_tokens_daily AS
    SELECT
        strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') AS "DateTime",
        CASE source_code
            WHEN 0 THEN 'autoplay'
            WHEN 1 THEN 'lyrics'
        END AS "Source",
        SUM(prompt_token_count) AS "Input Tokens",
        SUM(candidates_token_count) AS "Output Tokens",
        SUM(prompt_token_count + candidates_token_count) AS "Total Tokens"
    FROM gemini_usage
    GROUP BY strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime'), source_code;

    CREATE VIEW IF NOT EXISTS Gemini_tokens_hourly AS
    SELECT
        strftime('%Y-%m-%d %H:00:00', timestamp, 'unixepoch', 'localtime') AS "DateTime",
        CASE source_code
            WHEN 0 THEN 'autoplay'
            WHEN 1 THEN 'lyrics'
        END AS "Source",
        SUM(prompt_token_count) AS "Input Tokens",
        SUM(candidates_token_count) AS "Output Tokens",
        SUM(prompt_token_count + candidates_token_count) AS "Total Tokens"
    FROM gemini_usage
    GROUP BY strftime('%Y-%m-%d %H', timestamp, 'unixepoch', 'localtime'), source_code;
    """
    if conn is not None:
        conn.executescript(sql_statements)
        _gemini_db_initialized = True
    else:
        with _gemini_db_lock:
            with get_connection() as c:
                c.executescript(sql_statements)
                c.commit()
            _gemini_db_initialized = True


def log_gemini_usage(usage, source_code=0, source=None):
    """Log Gemini token usage and insert a record into gemini_usage table.

    Handles the logger.info call and database persistence.
    source_code: 0 for autoplay, 1 for lyrics (accepts int or string).
    """
    if not usage:
        return

    if source is not None:
        source_code = source

    if hasattr(usage, "usage_metadata") and usage.usage_metadata is not None:
        usage = usage.usage_metadata

    if isinstance(usage, dict):
        prompt_tokens = int(usage.get("prompt_token_count") or 0)
        candidates_tokens = int(usage.get("candidates_token_count") or 0)
        total_tokens = usage.get("total_token_count")
    else:
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        candidates_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        total_tokens = getattr(usage, "total_token_count", None)

    if total_tokens is None:
        total_tokens = prompt_tokens + candidates_tokens
    else:
        total_tokens = int(total_tokens)

    logger.info(
        "Gemini token usage: prompt=%s, candidates=%s, total=%s",
        prompt_tokens,
        candidates_tokens,
        total_tokens,
    )

    if isinstance(source_code, str):
        normalized = source_code.strip().lower()
        if normalized == "lyrics":
            numeric_source = 1
        elif normalized == "autoplay":
            numeric_source = 0
        else:
            try:
                numeric_source = int(source_code)
            except ValueError:
                numeric_source = 0
    else:
        try:
            numeric_source = int(source_code)
        except (ValueError, TypeError):
            numeric_source = 0

    try:
        global _gemini_db_initialized
        if not _gemini_db_initialized:
            init_gemini_usage_db()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO gemini_usage (source_code, prompt_token_count, candidates_token_count) VALUES (?, ?, ?);",
                (numeric_source, prompt_tokens, candidates_tokens),
            )
            conn.commit()
    except Exception as error:
        logger.error("Failed to record Gemini usage to database: %s", error)

