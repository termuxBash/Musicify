"""Fetch lyrics and align romanized/translated text to LRCLIB timestamps."""

import logging
import re
import threading
import time

import requests

from core.settings import DEFAULT_LYRICS_ENABLED, LASTFM_KEY
from services.lastfm_service import resolve_track

logger = logging.getLogger(__name__)

LYRICS_API_URL = "https://wilooper-lyrica.hf.space/lyrics/"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
TIMESTAMP_PATTERN = re.compile(r"\[(\d+):(\d{2}(?:\.\d+)?)\]")


def parse_synced_lyrics(synced_lyrics):
    """Return non-empty LRC lyric lines as sorted (seconds, text) pairs."""
    parsed = []
    for line in synced_lyrics.splitlines():
        timestamps = list(TIMESTAMP_PATTERN.finditer(line))
        if not timestamps:
            continue

        text = TIMESTAMP_PATTERN.sub("", line).strip()
        if not text:
            continue

        for timestamp in timestamps:
            minutes, seconds = timestamp.groups()
            parsed.append((int(minutes) * 60 + float(seconds), text))

    return sorted(parsed, key=lambda lyric: lyric[0])


def align_lyrics(transformed_lyrics, synced_lyrics, plain_lyrics=""):
    """Use LRCLIB's timestamps for transformed text, retaining unsynced lines otherwise."""
    source_lines = parse_synced_lyrics(synced_lyrics or "")
    transformed_lines = [
        re.sub(r"^(?:\[\d+:\d+(?:\.\d+)?\])+", "", line).strip()
        for line in (transformed_lyrics or "").splitlines()
    ]
    transformed_lines = [line for line in transformed_lines if line]

    if not transformed_lines:
        if source_lines:
            return source_lines
        return [(None, line.strip()) for line in (plain_lyrics or "").splitlines() if line.strip()]

    if not source_lines:
        return [(None, line) for line in transformed_lines]

    last_source_index = len(source_lines) - 1
    last_transformed_index = len(transformed_lines) - 1
    aligned = []
    for index, text in enumerate(transformed_lines):
        source_index = (
            0
            if last_transformed_index == 0
            else round(index * last_source_index / last_transformed_index)
        )
        aligned.append((source_lines[source_index][0], text))
    return aligned


class LyricsService:
    def __init__(self, enabled=DEFAULT_LYRICS_ENABLED):
        self.current_lyrics = []
        self.song_start_time = None
        self.enabled = enabled
        self.last_sent_lyric = None
        self._lock = threading.Lock()
        self._generation = 0

    def reset(self, song_title):
        """Reset the timeline and fetch lyrics asynchronously for a new track."""
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.current_lyrics = []
            self.song_start_time = time.time()
            self.last_sent_lyric = None

        if not song_title or not self.enabled:
            return

        threading.Thread(
            target=self._load_lyrics,
            args=(song_title, generation),
            daemon=True,
        ).start()

    def shift_timeline(self, seconds):
        with self._lock:
            if self.song_start_time is not None:
                self.song_start_time += seconds

    def _load_lyrics(self, song_title, generation):
        clean_query = re.sub(r"\(.*?\)|\[.*?\]", "", song_title)
        clean_query = re.sub(
            r"official music video|official video|video|lyrics",
            "",
            clean_query,
            flags=re.IGNORECASE,
        )
        query = " ".join(clean_query.split()).strip()
        artist, track = self._resolve_artist_and_track(query)
        if not track:
            logger.warning("Could not resolve a track for lyrics: %s", query)
            return

        synced_lyrics = ""
        plain_lyrics = ""
        try:
            response = requests.get(
                LRCLIB_SEARCH_URL,
                params={"q": " ".join(part for part in (artist, track) if part)},
                timeout=5,
            )
            response.raise_for_status()
            results = response.json()
            if isinstance(results, list) and results and isinstance(results[0], dict):
                synced_lyrics = results[0].get("syncedLyrics") or ""
                plain_lyrics = results[0].get("plainLyrics") or ""
        except (requests.RequestException, ValueError) as error:
            logger.warning("LRCLIB lookup failed for %s: %s", query, error)

        transformed_lyrics = ""
        if artist:
            try:
                response = requests.get(
                    LYRICS_API_URL,
                    params={
                        "artist": artist,
                        "song": track,
                        "translate": "true",
                        "romanize": "true",
                        "language": "en",
                    },
                    timeout=20,
                )
                response.raise_for_status()
                result = response.json()
                if isinstance(result, dict) and result.get("status") in (None, "success"):
                    lyrics = result.get("lyrics")
                    if isinstance(lyrics, str):
                        transformed_lyrics = lyrics
            except (requests.RequestException, ValueError) as error:
                logger.warning("Romanized/translated lyrics lookup failed for %s: %s", query, error)

        lyrics = align_lyrics(transformed_lyrics, synced_lyrics, plain_lyrics)
        with self._lock:
            if generation == self._generation:
                self.current_lyrics = lyrics
                logger.info("Loaded %d lyric lines for %s.", len(lyrics), query)

    @staticmethod
    def _resolve_artist_and_track(query):
        artist = None
        track = None
        if LASTFM_KEY:
            try:
                artist, track = resolve_track(query, LASTFM_KEY)
            except (requests.RequestException, ValueError) as error:
                logger.warning("Last.fm track lookup failed for %s: %s", query, error)

        if track:
            return artist, track

        if " - " in query:
            fallback_artist, fallback_track = query.split(" - ", 1)
            return fallback_artist.strip(), fallback_track.strip()
        return None, query

    def get_lyrics_state(self):
        """Return all display lines and the line active at the current playback time."""
        with self._lock:
            lyrics = list(self.current_lyrics)
            start_time = self.song_start_time
            enabled = self.enabled
            elapsed = time.time() - start_time if start_time is not None else None

            current_index = None
            if enabled and elapsed is not None:
                for index, (timestamp, _) in enumerate(lyrics):
                    if timestamp is not None and elapsed >= timestamp:
                        current_index = index

            current_lyric = lyrics[current_index][1] if current_index is not None else ""
            if current_lyric and current_lyric != self.last_sent_lyric:
                self.last_sent_lyric = current_lyric

            return {
                "lyrics": [
                    {"time": timestamp, "text": text}
                    for timestamp, text in lyrics
                ] if enabled else [],
                "current_lyric_index": current_index,
                "current_lyric": current_lyric,
            }

    def get_current_line(self):
        """Return the lyric active at the current playback time."""
        return self.get_lyrics_state()["current_lyric"]

    def trigger_display(self, text):
        """Runs the display subprocess."""
        import os
        import subprocess
        import sys

        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.abspath(os.path.join(current_dir, "..", "core", "display.py"))
            if os.path.exists(script_path):
                subprocess.Popen([sys.executable, script_path, str(text)])
        except OSError as error:
            logger.error("Hardware spawn error: %s", error)
