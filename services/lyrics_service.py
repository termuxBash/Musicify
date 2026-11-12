"""Fetch lyrics and align romanized/translated text to LRCLIB timestamps."""

import logging
import re
import threading
import time
import unicodedata

import requests

from core.settings import (
    DEFAULT_LYRICS_ENABLED,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LASTFM_KEY,
    LYRICS_OFFSET_MS,
)
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


def is_non_english_script(lyrics):
    """Return whether text contains alphabetic characters outside the Latin script."""
    if not lyrics:
        return False

    return any(
        "LATIN" not in unicodedata.name(char, "")
        for char in lyrics
        if char.isalpha()
    )


def romanize_lyrics(lyrics):
    """Romanize non-Latin lyrics with Gemini, preserving source line structure."""
    if not lyrics or not lyrics.strip() or not GEMINI_API_KEY:
        return None

    prompt = f"""You are an expert multilingual transliteration and romanization system.

Automatically detect the language and transliterate the lyrics below into Latin characters.
Do not translate them into English. Preserve the original language, words, meaning,
pronunciation, every line break, and line order. Use standard conventions such as
Hepburn for Japanese and readable standard transliteration for Hindi. Leave lines
already written in Latin characters unchanged. Preserve timestamps and formatting.
Return only the romanized lyrics, with no headings or explanations.

LYRICS:
{lyrics}
"""
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent"
    )

    try:
        response = requests.post(
            endpoint,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "text/plain",
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        response_json = response.json()
        candidates = response_json.get("candidates", [])
        content = candidates[0].get("content", {}) if candidates else {}
        parts = content.get("parts", [])
        romanized = parts[0].get("text") if parts else None
        if not isinstance(romanized, str):
            logger.warning("Gemini returned no lyric text.")
            return None
        romanized = romanized.strip()
        if not romanized:
            logger.warning("Gemini returned empty lyrics.")
            return None
        return romanized
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
        logger.exception("Failed to romanize lyrics: %s", error)
        return None


class LyricsService:
    def __init__(self, enabled=DEFAULT_LYRICS_ENABLED, offset_ms=LYRICS_OFFSET_MS):
        self.current_lyrics = []
        self.song_start_time = None
        self.enabled = enabled
        self.offset_ms = offset_ms
        self.last_sent_lyric = None
        self.lyrics_source = None
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
            self.lyrics_source = None

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

    def set_offset(self, offset_ms):
        with self._lock:
            self.offset_ms = offset_ms

    def _publish_lyrics(self, generation, lyrics, source):
        if not lyrics:
            return
        with self._lock:
            if generation == self._generation:
                self.current_lyrics = lyrics
                self.lyrics_source = source

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

        lrclib_lyrics = plain_lyrics or "\n".join(
            text for _, text in parse_synced_lyrics(synced_lyrics)
        )
        lrclib_lines = align_lyrics("", synced_lyrics, plain_lyrics)
        if lrclib_lines:
            self._publish_lyrics(generation, lrclib_lines, "LRCLIB")

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
                    timeout=30,
                )
                response.raise_for_status()
                result = response.json()
                if isinstance(result, dict) and result.get("status") in (None, "success"):
                    lyrics = result.get("lyrics")
                    if isinstance(lyrics, str):
                        transformed_lyrics = lyrics
            except (requests.RequestException, ValueError) as error:
                logger.warning("Romanized/translated lyrics lookup failed for %s: %s", query, error)

        lyrica_lines = align_lyrics(transformed_lyrics, synced_lyrics, plain_lyrics)
        if transformed_lyrics.strip():
            self._publish_lyrics(generation, lyrica_lines, "Lyrica")

        gemini_source = (
            lrclib_lyrics
            if is_non_english_script(lrclib_lyrics)
            else transformed_lyrics
            if is_non_english_script(transformed_lyrics)
            else ""
        )
        gemini_lyrics = romanize_lyrics(gemini_source) if gemini_source else None

        if gemini_lyrics:
            gemini_lines = align_lyrics(gemini_lyrics, synced_lyrics, plain_lyrics)
            self._publish_lyrics(generation, gemini_lines, "Gemini")

        with self._lock:
            if generation == self._generation:
                logger.info("Loaded %d lyric lines for %s.", len(self.current_lyrics), query)

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
            offset_ms = self.offset_ms
            elapsed = (
                time.time() - start_time + offset_ms / 1000
                if start_time is not None
                else None
            )

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
                "lyrics_source": self.lyrics_source if enabled and lyrics else None,
                "lyrics_offset_ms": offset_ms,
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
