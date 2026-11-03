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
    _migrate_unique_song_id as _enforce_unique_song_id,
    create_table,
    get_all_tables,
    get_connection,
    get_db_path,
    normalize_video_id,
    sanitize_table_name,
)


def default_hex_timestamp():
    """Generate default hex timestamp relative to TIME_OFFSET."""
    return format(max(0, int(time.time()) - TIME_OFFSET), "x")


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

    # Parse timestamp
    timestamp = parts[3] if len(parts) >= 4 and parts[3] else default_hex_timestamp()

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
    _enforce_unique_song_id(table)


def migrate_unique_song_ids():
    """Merge duplicate video IDs and enforce UNIQUE(song_id) on every table."""
    tables = get_all_tables()
    for table_name in tables:
        _migrate_unique_song_id(table_name)
    return tables


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
        help="Target table name (only valid when migrating a single file).",
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

    args = parser.parse_args()
    db_path = get_db_path()

    print(f"🎵 Musicify SQLite History Migration Tool")
    print(f"📦 Target Database: {db_path}")

    if args.unique_song_id:
        tables = migrate_unique_song_ids()
        print(f"✅ UNIQUE(song_id) migration completed for {len(tables)} table(s).")
        if not args.files:
            print(f"📋 Current tables in database: {', '.join(tables)}")
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
