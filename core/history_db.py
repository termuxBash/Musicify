"""core.history_db - SQLite-backed playback history manager for Musicify
Manages history tables, CRUD operations, auto-migration, and timestamp conversions in history.sqlite3.
"""

import os
import re
import sqlite3
import threading
import time
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


def get_rick_roll_timestamps():
    """Retrieve all recorded rick_roll_timestamp values from extra_data."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='extra_data';")
        if not cursor.fetchone():
            return []
        cursor.execute("SELECT rick_roll_timestamp FROM extra_data;")
        return [row["rick_roll_timestamp"] for row in cursor.fetchall()]



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


def _safe_table(name):
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

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        known_tables = {row["name"] for row in cursor.fetchall()}

    # Keep bootstrap compatibility for first-run history table creation.
    if clean == "history" or clean in known_tables:
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
    return conn


def hex_to_iso_datetime(hex_str):
    """Convert stored hex timestamp (elapsed or full) to an ISO datetime string for HTML inputs."""
    try:
        if not hex_str:
            return datetime.now().strftime("%Y-%m-%dT%H:%M")
        val = int(str(hex_str).strip(), 16)
        if val < 1000000000:
            unix_ts = val + TIME_OFFSET
        else:
            unix_ts = val
        return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        return datetime.now().strftime("%Y-%m-%dT%H:%M")


def iso_datetime_to_hex(dt_str):
    """Convert an ISO datetime string (from HTML datetime-local) to the stored hex string."""
    try:
        dt = datetime.fromisoformat(str(dt_str).strip())
        unix_ts = int(dt.timestamp())
        elapsed = max(0, unix_ts - TIME_OFFSET)
        return format(elapsed, "x")
    except Exception:
        elapsed = max(0, int(time.time()) - TIME_OFFSET)
        return format(elapsed, "x")


def create_table(table_name):
    """Create a history table if it doesn't already exist.

    ``song_id`` carries a UNIQUE constraint so that the same YouTube video
    can never produce two rows in the same table regardless of how the song
    name was spelled at record time.
    """
    clean = _safe_table(table_name)
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{clean}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id   TEXT NOT NULL UNIQUE,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT NOT NULL
            );
        """)
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'extra_data' ORDER BY name;")
        tables = [row["name"] for row in cursor.fetchall()]
    if "history" not in tables:
        create_table("history")
        tables.insert(0, "history")
    return tables


def _migrate_unique_song_id(table):
    """Retrofit a UNIQUE constraint on song_id for pre-existing tables.

    For tables created before the uniqueness requirement was introduced this
    function:
      1. Merges duplicate song_id rows (summing listens, keeping the latest
         timestamp and the song_name from the row with the most listens).
      2. Recreates the table with the UNIQUE constraint via the
         CREATE TABLE … / INSERT … / DROP / ALTER rename pattern so that
         SQLite's limited ALTER TABLE support is not an issue.

    The operation is idempotent — it checks for the UNIQUE index first and
    skips all work if it is already present.
    """
    clean = _safe_table(table)
    with get_connection() as conn:
        cursor = conn.cursor()

        # Check whether any unique index contains only song_id.
        has_unique_song_id = False
        cursor.execute(f'PRAGMA index_list("{clean}");')
        for index in cursor.fetchall():
            if not index["unique"]:
                continue
            cursor.execute(f'PRAGMA index_info("{index["name"]}");')
            columns = [row["name"] for row in cursor.fetchall()]
            if columns == ["song_id"]:
                has_unique_song_id = True
                break

        cursor.execute(f'SELECT song_id FROM "{clean}";')
        stored_ids = [row["song_id"] for row in cursor.fetchall()]
        normalized_ids = [normalize_video_id(song_id) for song_id in stored_ids]
        invalid_ids = sum(not song_id for song_id in normalized_ids)
        ids_changed = any(
            normalized and normalized != stored
            for stored, normalized in zip(stored_ids, normalized_ids)
        )
        has_normalized_duplicates = len(set(normalized_ids)) != len(normalized_ids)
        if has_unique_song_id and not invalid_ids and not ids_changed and not has_normalized_duplicates:
            return

        logger.info(f"Migrating table '{clean}': enforcing UNIQUE(song_id)...")

        # Step 1 – collect deduplicated rows: group by song_id, merge listens
        cursor.execute(f"""
            SELECT
                MIN(id) AS id,
                song_name,
                song_id,
                SUM(number_of_listens) AS number_of_listens,
                MAX(timestamp) AS timestamp
            FROM "{clean}"
            GROUP BY song_id;
        """)
        merged_rows = cursor.fetchall()
        normalized_rows = {}
        for row in merged_rows:
            song_id = normalize_video_id(row["song_id"])
            if not song_id:
                logger.warning("Skipping invalid YouTube ID %r in table %s", row["song_id"], clean)
                continue
            if song_id in normalized_rows:
                previous = normalized_rows[song_id]
                normalized_rows[song_id] = (
                    previous[0],
                    previous[1] + row["number_of_listens"],
                    max(previous[2], row["timestamp"]),
                )
            else:
                normalized_rows[song_id] = (
                    row["song_name"],
                    row["number_of_listens"],
                    row["timestamp"],
                )

        # Step 2 – create a temp table with the UNIQUE constraint
        tmp = f"_tmp_migrate_{clean}"
        conn.execute(f'DROP TABLE IF EXISTS "{tmp}";')
        conn.execute(f"""
            CREATE TABLE "{tmp}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id   TEXT NOT NULL UNIQUE,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT NOT NULL
            );
        """)

        # Step 3 – insert merged rows
        for song_id, (song_name, listens, timestamp) in normalized_rows.items():
            conn.execute(
                f'INSERT INTO "{tmp}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                (song_name, song_id, listens, timestamp)
            )

        # Step 4 – swap: drop old table, rename temp to original name
        conn.execute(f'DROP TABLE "{clean}";')
        conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{clean}";')

        # Step 5 – recreate the composite search index
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS "idx_{clean}_song" ON "{clean}" (song_name, song_id);
        """)
        conn.commit()
        logger.info(f"Migration of '{clean}' complete — {len(normalized_rows)} unique songs retained.")


def init_db():
    """Initialize default history table and migrate from history.txt if needed."""
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

    # Check if migration from history.txt is needed
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as cnt FROM "history";')
            count = cursor.fetchone()["cnt"]
            if count == 0:
                txt_path = os.path.join(PLAYLIST_DIR, "history.txt")
                if os.path.exists(txt_path):
                    logger.info("Migrating existing history.txt into history.sqlite3...")
                    with open(txt_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            parts = line.split(">")
                            if len(parts) >= 2:
                                song_name = parts[0].strip()
                                song_id = parts[1].strip()
                                listens = int(parts[2].strip()) if len(parts) >= 3 and parts[2].strip().isdigit() else 1
                                ts = parts[3].strip() if len(parts) >= 4 else format(max(0, int(time.time()) - TIME_OFFSET), "x")
                                cursor.execute(
                                    'INSERT OR IGNORE INTO "history" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                                    (song_name, song_id, listens, ts)
                                )
                    conn.commit()
                    logger.info("Migration of history.txt into SQLite completed.")
    except Exception as e:
        logger.error(f"Error initializing or migrating history database: {e}")

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
    elapsed_timestamp = format(max(0, int(time.time()) - TIME_OFFSET), "x")

    with get_connection() as conn:
        cursor = conn.cursor()
        # Look up solely by video ID — name variations are irrelevant
        cursor.execute(
            f'SELECT id, number_of_listens FROM "{table}" WHERE song_id = ? LIMIT 1;',
            (video_id,)
        )
        row = cursor.fetchone()

        if row:
            cursor.execute(
                f'UPDATE "{table}" SET number_of_listens = ?, timestamp = ? WHERE id = ?;',
                (row["number_of_listens"] + 1, elapsed_timestamp, row["id"])
            )
        else:
            # INSERT OR IGNORE is safe here: if a concurrent insert sneaked in,
            # we simply skip — the next play will find and update the row.
            cursor.execute(
                f'INSERT OR IGNORE INTO "{table}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, 1, ?);',
                (title, video_id, elapsed_timestamp)
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
    if table == "extra_data":
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
            "datetime_str": hex_to_iso_datetime(r["timestamp"])
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
        ts = iso_datetime_to_hex(datetime_str)
    elif timestamp:
        ts = str(timestamp).strip()
    else:
        ts = format(max(0, int(time.time()) - TIME_OFFSET), "x")

    sid = normalize_video_id(song_id)
    sname = song_name.strip()
    listens = int(number_of_listens)
    if not sid:
        raise ValueError("song_id must be a valid 11-character YouTube video ID")

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
                f'UPDATE "{table}" SET number_of_listens = ?, timestamp = ? WHERE id = ?;',
                (existing["number_of_listens"] + listens, ts, existing["id"])
            )
            conn.commit()
            return existing["id"]
        else:
            cursor.execute(
                f'INSERT INTO "{table}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                (sname, sid, listens, ts)
            )
            conn.commit()
            return cursor.lastrowid


def update_entry(table_name, row_id, song_name, song_id, number_of_listens, timestamp=None, datetime_str=None):
    """Update an existing row by ID."""
    table = _safe_table(table_name)

    if datetime_str:
        ts = iso_datetime_to_hex(datetime_str)
    elif timestamp:
        ts = str(timestamp).strip()
    else:
        ts = format(max(0, int(time.time()) - TIME_OFFSET), "x")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'UPDATE "{table}" SET song_name = ?, song_id = ?, number_of_listens = ?, timestamp = ? WHERE id = ?;',
            (song_name.strip(), normalize_video_id(song_id), int(number_of_listens), ts, int(row_id))
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_entry(table_name, row_id):
    """Delete a row by ID from the specified table."""
    table = _safe_table(table_name)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'DELETE FROM "{table}" WHERE id = ?;', (int(row_id),))
        conn.commit()
        return cursor.rowcount > 0


def delete_table(table_name):
    """Delete an entire table (cannot delete default 'history' or 'extra_data')."""
    clean = _safe_table(table_name)
    if clean in ("history", "extra_data"):
        return False

    with get_connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{clean}";')
        conn.commit()

    if get_active_table() == clean:
        set_active_table("history")
    return True
