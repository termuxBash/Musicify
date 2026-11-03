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
from core.settings import PLAYLIST_DIR, TIME_OFFSET, get_history_file, set_history_file

logger = logging.getLogger(__name__)

DB_FILENAME = "history.sqlite3"
_active_table_lock = threading.Lock()
_active_table_name = None


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
    (this should never happen in practice but is an extra safety net).
    """
    clean = sanitize_table_name(name)
    if not _SAFE_TABLE_RE.fullmatch(clean):
        raise ValueError(f"Rejected unsafe table name after sanitisation: {clean!r}")
    return clean


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
            _active_table_name = sanitize_table_name(get_history_file())
        return _active_table_name


def set_active_table(name):
    """Set the active table name and synchronize settings."""
    global _active_table_name
    clean = sanitize_table_name(name)
    with _active_table_lock:
        _active_table_name = clean
    set_history_file(clean)
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
    """Create a history table if it doesn't already exist."""
    clean = _safe_table(table_name)
    with get_connection() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS "{clean}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id TEXT NOT NULL,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp TEXT NOT NULL
            );
        """)
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
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
        tables = [row["name"] for row in cursor.fetchall()]
    if "history" not in tables:
        create_table("history")
        tables.insert(0, "history")
    return tables


def init_db():
    """Initialize default history table and migrate from history.txt if needed."""
    create_table("history")
    active = get_active_table()
    if active != "history":
        create_table(active)

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
                                    'INSERT INTO "history" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                                    (song_name, song_id, listens, ts)
                                )
                    conn.commit()
                    logger.info("Migration of history.txt into SQLite completed.")
    except Exception as e:
        logger.error(f"Error initializing or migrating history database: {e}")


def record_played_song(song, table_name=None):
    """Record or increment the play count of a song in the specified (or active) history table."""
    if not song.get("record_history"):
        return

    title = (song.get("title") or "").strip()
    video_id = (song.get("videoId") or "").strip()
    if not title or not video_id:
        return

    table = _safe_table(table_name or get_active_table())
    create_table(table)
    elapsed_timestamp = format(max(0, int(time.time()) - TIME_OFFSET), "x")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'SELECT id, number_of_listens FROM "{table}" WHERE song_name = ? AND song_id = ? LIMIT 1;', (title, video_id))
        row = cursor.fetchone()

        if row:
            row_id = row["id"]
            new_count = row["number_of_listens"] + 1
            cursor.execute(
                f'UPDATE "{table}" SET number_of_listens = ?, timestamp = ? WHERE id = ?;',
                (new_count, elapsed_timestamp, row_id)
            )
        else:
            cursor.execute(
                f'INSERT INTO "{table}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, 1, ?);',
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
    """Add a new row into the specified table."""
    table = _safe_table(table_name)
    create_table(table)

    if datetime_str:
        ts = iso_datetime_to_hex(datetime_str)
    elif timestamp:
        ts = str(timestamp).strip()
    else:
        ts = format(max(0, int(time.time()) - TIME_OFFSET), "x")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'INSERT INTO "{table}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
            (song_name.strip(), song_id.strip(), int(number_of_listens), ts)
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
            (song_name.strip(), song_id.strip(), int(number_of_listens), ts, int(row_id))
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
    """Delete an entire table (cannot delete default 'history')."""
    clean = _safe_table(table_name)
    if clean == "history":
        return False

    with get_connection() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{clean}";')
        conn.commit()

    if get_active_table() == clean:
        set_active_table("history")
    return True

