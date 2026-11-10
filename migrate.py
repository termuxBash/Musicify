#!/usr/bin/env python3
"""migrate.py - Migrate text playlists / history files into history.sqlite3.

Usage:
    # Migrate all .txt files found in the configured PLAYLIST_DIR:
    python3 migrate.py

    # Migrate a specific file:
    python3 migrate.py /path/to/my_playlist.txt

    # Migrate multiple files:
    python3 migrate.py file1.txt file2.txt

    # Migrate a specific file into a custom table:
    python3 migrate.py --table rock /path/to/my_songs.txt

    # Merge duplicate video IDs in all existing tables:
    python3 migrate.py --unique-song-id

    # Update existing tables and change records to INTEGER timestamp:
    python3 migrate.py --integer-timestamp

    # Update a specific table to INTEGER timestamp:
    python3 migrate.py --integer-timestamp --table history
"""

import argparse
import os
import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.settings import PLAYLIST_DIR, TIME_OFFSET
from core.history_db import (
    create_table,
    get_all_tables,
    get_connection,
    get_db_path,
    normalize_video_id,
    sanitize_table_name,
)


def default_timestamp():
    """Generate default integer timestamp offset relative to TIME_OFFSET."""
    return max(0, int(time.time()) - TIME_OFFSET)


def parse_legacy_timestamp(raw_ts):
    """Convert a timestamp (legacy hex string, full Unix timestamp, or int) into an integer offset from TIME_OFFSET."""
    if raw_ts is None or raw_ts == "":
        return default_timestamp()
    if isinstance(raw_ts, int):
        val = raw_ts
    else:
        s = str(raw_ts).strip()
        if not s:
            return default_timestamp()
        try:
            val = int(s, 16)
        except ValueError:
            try:
                val = int(s)
            except ValueError:
                return default_timestamp()
    if val >= 1_000_000_000:
        return max(0, val - TIME_OFFSET)
    return max(0, val)


def parse_playlist_line(line):
    """
    Parse a single line from a playlist/history text file.
    Supports formats:
      - song_name>song_id>number_of_listens>timestamp (history format)
      - song_name>song_id>number_of_listens
      - song_name>song_id (playlist format)
      - song_name
    """
    line = line.strip()
    if not line:
        return None

    parts = [p.strip() for p in line.split(">")]
    song_name = parts[0]
    if not song_name:
        return None

    song_id = normalize_video_id(parts[1] if len(parts) >= 2 else "")
    
    # Parse listens
    listens = 1
    if len(parts) >= 3 and parts[2].isdigit():
        listens = max(1, int(parts[2]))

    # Parse timestamp as integer offset
    raw_ts = parts[3].strip() if len(parts) >= 4 and parts[3] else None
    timestamp = parse_legacy_timestamp(raw_ts) if raw_ts else default_timestamp()

    return {
        "song_name": song_name,
        "song_id": song_id,
        "number_of_listens": listens,
        "timestamp": timestamp,
    }


def migrate_file(file_path, target_table=None):
    """Migrate a text playlist file into the specified SQLite table."""
    file_path = Path(file_path).resolve()
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return 0, 0

    table_name = sanitize_table_name(target_table or file_path.stem)
    create_table(table_name)

    inserted = 0
    updated = 0
    skipped = 0

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        print(f"ℹ️  File '{file_path.name}' is empty. Created empty table '{table_name}'.")
        return 0, 0

    with get_connection() as conn:
        cursor = conn.cursor()
        for line in lines:
            record = parse_playlist_line(line)
            if not record:
                continue

            song_name = record["song_name"]
            song_id = record["song_id"]
            if not song_id:
                skipped += 1
                continue
            listens = record["number_of_listens"]
            ts = record["timestamp"]

            # Merge by video ID so title spelling differences cannot create duplicates.
            cursor.execute(
                f'SELECT id, number_of_listens FROM "{table_name}" WHERE song_id = ? LIMIT 1;',
                (song_id,)
            )
            existing = cursor.fetchone()

            if existing:
                row_id = existing["id"]
                current_listens = existing["number_of_listens"]
                cursor.execute(
                    f'UPDATE "{table_name}" SET number_of_listens = ?, timestamp = ? WHERE id = ?;',
                    (current_listens + listens, ts, row_id)
                )
                updated += 1
            else:
                cursor.execute(
                    f'INSERT INTO "{table_name}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                    (song_name, song_id, listens, ts)
                )
                inserted += 1

        conn.commit()

    print(
        f"✅ Table '{table_name}' from '{file_path.name}': "
        f"{inserted} inserted, {updated} updated, {skipped} unchanged (total: {len(lines)})"
    )
    return inserted, updated


def _migrate_unique_song_id(table):
    """Merge duplicate video IDs and enforce UNIQUE(song_id) for one table."""
    clean = sanitize_table_name(table)
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

        print(f"🔧 Migrating table '{clean}': enforcing UNIQUE(song_id)...")

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
                continue
            int_ts = parse_legacy_timestamp(row["timestamp"])
            listens = int(row["number_of_listens"])
            if song_id in normalized_rows:
                previous = normalized_rows[song_id]
                normalized_rows[song_id] = (
                    previous[0],
                    previous[1] + listens,
                    max(previous[2], int_ts),
                )
            else:
                normalized_rows[song_id] = (
                    row["song_name"],
                    listens,
                    int_ts,
                )

        tmp = f"_tmp_migrate_{clean}"
        conn.execute(f'DROP TABLE IF EXISTS "{tmp}";')
        conn.execute(f"""
            CREATE TABLE "{tmp}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id   TEXT NOT NULL UNIQUE,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp INTEGER NOT NULL
            );
        """)

        for song_id, (song_name, listens, timestamp) in normalized_rows.items():
            conn.execute(
                f'INSERT INTO "{tmp}" (song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?);',
                (song_name, song_id, listens, timestamp)
            )

        conn.execute(f'DROP TABLE "{clean}";')
        conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{clean}";')
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS "idx_{clean}_song" ON "{clean}" (song_name, song_id);
        """)
        conn.commit()


def migrate_unique_song_ids():
    """Merge duplicate video IDs and enforce UNIQUE(song_id) on every table."""
    tables = get_all_tables()
    for table_name in tables:
        _migrate_unique_song_id(table_name)
    return tables


def migrate_table_to_integer(table_name):
    """Migrate a specific table to use INTEGER timestamp column and convert all records to integer offsets."""
    clean = sanitize_table_name(table_name)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f'PRAGMA table_info("{clean}");')
        columns = {row["name"]: row for row in cursor.fetchall()}
        if "timestamp" not in columns:
            return False, 0

        ts_col = columns["timestamp"]
        is_integer_type = ts_col["type"].upper() == "INTEGER"

        cursor.execute(f'SELECT COUNT(*) as cnt FROM "{clean}" WHERE typeof(timestamp) != "integer";')
        non_int_count = cursor.fetchone()["cnt"]

        if is_integer_type and non_int_count == 0:
            return False, 0

        cursor.execute(f'SELECT id, song_name, song_id, number_of_listens, timestamp FROM "{clean}" ORDER BY id ASC;')
        rows = cursor.fetchall()

        merged_rows = {}
        for row in rows:
            sid = normalize_video_id(row["song_id"])
            if not sid:
                continue
            int_ts = parse_legacy_timestamp(row["timestamp"])
            listens = int(row["number_of_listens"])
            if sid in merged_rows:
                prev = merged_rows[sid]
                merged_rows[sid] = (
                    prev[0],
                    row["song_name"] if listens > prev[3] else prev[1],
                    sid,
                    prev[3] + listens,
                    max(prev[4], int_ts),
                )
            else:
                merged_rows[sid] = (
                    row["id"],
                    row["song_name"],
                    sid,
                    listens,
                    int_ts,
                )

        to_insert = list(merged_rows.values())

        tmp = f"_tmp_migrate_int_{clean}"
        conn.execute(f'DROP TABLE IF EXISTS "{tmp}";')
        conn.execute(f"""
            CREATE TABLE "{tmp}" (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                song_id   TEXT NOT NULL UNIQUE,
                number_of_listens INTEGER NOT NULL DEFAULT 1,
                timestamp INTEGER NOT NULL
            );
        """)

        conn.executemany(
            f'INSERT INTO "{tmp}" (id, song_name, song_id, number_of_listens, timestamp) VALUES (?, ?, ?, ?, ?);',
            to_insert
        )

        conn.execute(f'DROP TABLE "{clean}";')
        conn.execute(f'ALTER TABLE "{tmp}" RENAME TO "{clean}";')
        conn.execute(f"""
            CREATE INDEX IF NOT EXISTS "idx_{clean}_song" ON "{clean}" (song_name, song_id);
        """)
        conn.commit()
        return True, len(to_insert)


def migrate_all_tables_to_integer():
    """Migrate all existing tables in history.sqlite3 to INTEGER timestamp."""
    tables = get_all_tables()
    results = {}
    for table_name in tables:
        migrated, count = migrate_table_to_integer(table_name)
        results[table_name] = (migrated, count)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Migrate text playlist/history files into history.sqlite3"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Optional path(s) to text files to migrate. If omitted, scans PLAYLIST_DIR for all .txt files.",
    )
    parser.add_argument(
        "-t", "--table",
        help="Target table name (valid with --integer-timestamp or when migrating a single file).",
    )
    parser.add_argument(
        "-d", "--dir",
        default=PLAYLIST_DIR,
        help=f"Playlist directory to scan when no files are passed (default: {PLAYLIST_DIR}).",
    )
    parser.add_argument(
        "--unique-song-id",
        action="store_true",
        help="Merge duplicate video IDs and enforce UNIQUE(song_id) on all existing tables.",
    )
    parser.add_argument(
        "--integer-timestamp", "--to-integer", "--migrate-timestamps",
        action="store_true",
        dest="integer_timestamp",
        help="Update existing table(s) to use INTEGER timestamp column and change all records to store it as INTEGER.",
    )

    args = parser.parse_args()
    db_path = get_db_path()

    print(f"🎵 Musicify SQLite History Migration Tool")
    print(f"📦 Target Database: {db_path}")

    if args.unique_song_id:
        tables = migrate_unique_song_ids()
        print(f"✅ UNIQUE(song_id) migration completed for {len(tables)} table(s).")
        if not args.files and not args.integer_timestamp:
            print(f"📋 Current tables in database: {', '.join(tables)}")
            return

    if args.integer_timestamp:
        if args.table:
            target_table = sanitize_table_name(args.table)
            migrated, count = migrate_table_to_integer(target_table)
            if migrated:
                print(f"✅ Table '{target_table}' updated to INTEGER timestamp ({count} records converted).")
            else:
                print(f"ℹ️  Table '{target_table}' is already using INTEGER timestamp.")
        else:
            results = migrate_all_tables_to_integer()
            migrated_count = sum(1 for m, _ in results.values() if m)
            total_records = sum(c for _, c in results.values())
            print(f"✅ INTEGER timestamp migration completed: {migrated_count}/{len(results)} table(s) updated ({total_records} total records converted).")
            for tbl, (migrated, count) in results.items():
                status = f"updated ({count} records)" if migrated else "already up to date"
                print(f"   - {tbl}: {status}")

        if not args.files:
            print(f"📋 Current tables in database: {', '.join(get_all_tables())}")
            return

    if args.files:
        if args.table and len(args.files) > 1:
            print("⚠️  --table can only be used when migrating a single file.")
            sys.exit(1)

        total_inserted = 0
        total_updated = 0
        for f in args.files:
            ins, upd = migrate_file(f, target_table=args.table)
            total_inserted += ins
            total_updated += upd

        print(f"\n🎉 Done! Total: {total_inserted} inserted, {total_updated} updated.")
    else:
        scan_dir = Path(args.dir).resolve()
        if not scan_dir.exists():
            print(f"❌ Playlist directory does not exist: {scan_dir}")
            sys.exit(1)

        txt_files = sorted(scan_dir.glob("*.txt"))
        if not txt_files:
            print(f"ℹ️  No .txt files found in: {scan_dir}")
            sys.exit(0)

        print(f"🔍 Found {len(txt_files)} text files in {scan_dir}:")
        total_inserted = 0
        total_updated = 0
        for txt_file in txt_files:
            ins, upd = migrate_file(txt_file)
            total_inserted += ins
            total_updated += upd

        print(f"\n🎉 Migration finished! Total: {total_inserted} inserted, {total_updated} updated across {len(txt_files)} files.")

    print(f"📋 Current tables in database: {', '.join(get_all_tables())}")


if __name__ == "__main__":
    main()
