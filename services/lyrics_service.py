"""services.lyrics_service.py - Service for fetching and displaying synced lyrics
The LyricsService class provides functionality to fetch synced lyrics for the currently playing song using the LRCLIB API. It processes the song title to create a clean search query,
retrieves the lyrics in a timestamped format, and calculates the current lyric line based on the elapsed time since the song started. The service also includes a method to trigger the display of the current lyric line on a hardware screen by spawning a subprocess that runs a separate display script.
The service is designed to be enabled or disabled based on configuration settings, allowing for flexible integration with the music player.

**Note: The actual display of lyrics on hardware is handled by a separate script (core/display.py), and this service is responsible for providing the lyrics to be displayed. The communication between the service and the display script is done through subprocess calls, which allows for decoupling of the lyric fetching logic from the hardware interaction logic.**
"""

import logging
import requests
import time
import re
from core.settings import DEFAULT_LYRICS_ENABLED

logger = logging.getLogger(__name__)

class LyricsService:
    def __init__(self, enabled=DEFAULT_LYRICS_ENABLED):
        self.current_lyrics = []
        self.song_start_time = None
        self.enabled = enabled
        self.last_sent_lyric = None

    def reset(self, song_title):
        """Resets timelines and fetches lyrics instantly."""
        self.current_lyrics = []
        self.song_start_time = time.time()
        self.last_sent_lyric = None
        
        if not song_title or not self.enabled:
            return

        # Clean the title (e.g. "ABBA - Dancing Queen (Official Music Video)" -> "ABBA Dancing Queen")
        clean = re.sub(r'\(.*?\)|\[.*?\]', '', song_title)
        clean = re.sub(r'official music video|official video|video|lyrics', '', clean, flags=re.IGNORECASE)
        query = " ".join(clean.split()).strip()

        logger.info(f"Fetching lyrics for cleaned query: {query}")
        
        try:
            # Query LRCLIB using the text query parameter
            r = requests.get("https://lrclib.net/api/search", params={"q": query}, timeout=5)
            data = r.json()
            
            if data and isinstance(data, list) and data[0].get("syncedLyrics"):
                synced = data[0]["syncedLyrics"]
                parsed = []
                for line in synced.splitlines():
                    if not line.startswith("["):
                        continue
                    try:
                        ts, lyric_text = line.split("]", 1)
                        mins, secs = ts[1:].split(":")
                        total_seconds = int(mins) * 60 + float(secs)
                        parsed.append((total_seconds, lyric_text.strip()))
                    except Exception:
                        pass
                
                self.current_lyrics = sorted(parsed, key=lambda x: x[0])
                logger.info(f"Loaded {len(self.current_lyrics)} lyric lines successfully.")
            else:
                logger.warning("No synced lyrics found in LRCLIB response.")
        except Exception as e:
            logger.error(f"Failed to communicate with lyrics server: {e}")

    def get_current_line(self):
        """Calculates current lyric string based on elapsed time."""
        if not self.enabled or not self.song_start_time or not self.current_lyrics:
            return ""

        elapsed = time.time() - self.song_start_time
        current_line = ""
        
        for ts, line in self.current_lyrics:
            if elapsed >= ts:
                current_line = line
            else:
                break

        # Fire off to hardware screen if it changes
        if current_line and current_line != self.last_sent_lyric:
            self.last_sent_lyric = current_line
            self.trigger_display(current_line)

        return current_line

    def trigger_display(self, text):
        """Runs the display subprocess."""
        import subprocess
        import sys
        import os
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.abspath(os.path.join(current_dir, "..", "core", "display.py"))
            if os.path.exists(script_path):
                subprocess.Popen([sys.executable, script_path, str(text)])
        except Exception as e:
            logger.error(f"Hardware spawn error: {e}")