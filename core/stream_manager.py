import threading
import time
import os
import logging

logger = logging.getLogger(__name__)

STREAM_URL = os.getenv(
    "STREAM_URL",
    "http://192.168.29.157:8000/mpv.ogg"
)

class QueuePlayer:

    def __init__(self, ffmpeg_service, bose_worker=None):
        self.ffmpeg = ffmpeg_service
        self.bose = bose_worker

        self.queue = []
        self.lock = threading.Lock()

        self.current_process = None
        self.current_song = None

        self.running = False
        self.thread = None
        
        self.autoplay_enabled = False 

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        self.skip()
        with self.lock:
            self.queue.clear()

    def enqueue(self, song):
        with self.lock:
            self.queue.append(song)

    def enqueue_many(self, songs):
        with self.lock:
            self.queue.extend(songs)

    def remove_from_queue(self, index):
        with self.lock:
            if 0 <= index < len(self.queue):
                return self.queue.pop(index)
        return None

    def skip(self):
        if self.current_process:
            logger.info("Skip button clicked. Terminating FFmpeg process.")
            try:
                self.ffmpeg.kill_process(self.current_process)
            except Exception as e:
                logger.error(f"Error killing process: {e}")

    def toggle_autoplay(self, status: bool):
        with self.lock:
            self.autoplay_enabled = status
            logger.info(f"Autoplay state explicitly shifted to: {self.autoplay_enabled}")

    def status(self):
        with self.lock:
            queue_copy = list(self.queue)
            autoplay_status = self.autoplay_enabled

        return {
            "queue": queue_copy,
            "current_song": self.current_song,
            "queue_size": len(queue_copy),
            "playing": self.current_process is not None,
            "autoplay_enabled": autoplay_status  
        }

    def _get_next_song(self):
        with self.lock:
            if not self.queue:
                return None
            return self.queue.pop(0)

    def _trigger_autoplay(self, reference_song):
        """Generates lookalike track additions using local context processing to bypass endpoint hanging."""
        full_title = reference_song.get("title", "Unknown")
        artist = "Unknown"
        title = full_title

        if " - " in full_title:
            parts = full_title.split(" - ", 1)
            artist = parts[0].strip()
            title = parts[1].strip()

        logger.info(f"🔮 AUTOPLAY ACTIVE: Processing local context lookup for -> Artist: '{artist}' | Title: '{title}'")
        
        try:
            # Import dependencies dynamically to maintain separation
            from apps.yt.routes import auto_pick_song
            from services.yt_service import YTService
            yt_resolver = YTService()

            # Seed lookahead lookup strings based on standard artist profiles
            search_queries = [
                f"{artist} top tracks",
                f"songs similar to {title} {artist}",
                f"{artist} live radio mix"
            ]

            autoplays_to_append = []

            for query in search_queries:
                try:
                    # Leverage your application's integrated track resolution pipeline
                    resolved = auto_pick_song(query)
                    if resolved and resolved.get("videoId"):
                        # Verify we don't accidentally re-enqueue the identical track currently playing
                        if resolved["videoId"] == reference_song.get("videoId"):
                            continue
                            
                        watch_url = f"https://www.youtube.com/watch?v={resolved['videoId']}"
                        stream_url = yt_resolver.resolve_stream(watch_url)
                        
                        if stream_url:
                            resolved["url"] = stream_url
                            autoplays_to_append.append(resolved)
                            logger.info(f"Local Autoplay resolved stream for: {resolved.get('title')}")
                            
                            # Break early once our targeted lookahead buffer limit is satisfied
                            if len(autoplays_to_append) >= 3:
                                break
                except Exception as track_err:
                    logger.error(f"Failed to resolve local context query entry '{query}': {track_err}")

            if autoplays_to_append:
                self.enqueue_many(autoplays_to_append)
                logger.info(f"✅ Success! Enqueued {len(autoplays_to_append)} lookahead tracks into the buffer.")
            else:
                logger.warning("Local lookahead loop completed but stream links could not be verified.")

        except Exception as general_err:
            logger.error(f"Local autoplay engine exception: {general_err}", exc_info=True)

    def _worker(self):
        while self.running:
            song = self._get_next_song()

            if song is None:
                time.sleep(1)
                continue

            with self.lock:
                self.current_song = song
                should_trigger_now = (len(self.queue) == 0) and self.autoplay_enabled

            if should_trigger_now:
                logger.info(f"Queue dropped to 0 while launching track. Generating proactive recommendations...")
                threading.Thread(target=self._trigger_autoplay, args=(song,), daemon=True).start()

            try:
                logger.info(f"Playing: {song.get('title', 'Unknown')}")
                target = song["url"]
                
                self.current_process = self.ffmpeg.start_stream(target)

                if self.bose:
                    time.sleep(2)
                    self.bose.trigger_upnp_stream(STREAM_URL)

                self.current_process.wait()

            except Exception as loop_error:
                logger.warning(f"Playback runtime event: {loop_error}")

            finally:
                with self.lock:
                    self.current_process = None
                    self.current_song = None