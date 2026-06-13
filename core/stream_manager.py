# stream_manager.py
import threading
import time
import logging
import urllib.request
import json
from core.settings import DEFAULT_AUTOPLAY_ENABLED, STREAM_URL

logger = logging.getLogger(__name__)

class QueuePlayer:

    def __init__(self, ffmpeg_service, bose_worker=None, autoplay_enabled=DEFAULT_AUTOPLAY_ENABLED):
        self.ffmpeg = ffmpeg_service
        self.bose = bose_worker

        self.queue = []
        self.lock = threading.Lock()

        self.current_process = None
        self.current_song = None

        self.running = False
        self.thread = None
        
        self.autoplay_enabled = autoplay_enabled 

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

    def _get_icecast_listeners(self):
        """Fetches the active listener count from Icecast's JSON endpoint."""
        try:
            # Assumes standard Icecast installation on localhost port 8000
            url = "http://127.0.0.1:8000/status-json.xsl"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=2) as response:
                data = json.loads(response.read().decode())
                
                # Icecast JSON nesting structure parsing
                if "icestats" in data and "source" in data["icestats"]:
                    sources = data["icestats"]["source"]
                    
                    # If there's only 1 mountpoint, Icecast returns a dict instead of a list
                    if isinstance(sources, dict):
                        sources = [sources]
                        
                    for source in sources:
                        if "/mpv.ogg" in source.get("listenurl", ""):
                            return int(source.get("listeners", 0))
        except Exception as e:
            logger.error(f"Error pulling Icecast statistics: {e}")
            
        # Default fallback to 1 so autoplay isn't accidentally killed on network timeouts
        return 1

    def _trigger_autoplay(self, reference_song):
        """Generates lookahead track additions using Last.fm recommendations to prevent loop patterns."""
        full_title = reference_song.get("title", "Unknown")
        
        logger.info(f"🔮 AUTOPLAY ACTIVE: Processing local context lookup for -> '{full_title}'")
        
        try:
            from services.yt_service import YTService
            # Import your Last.fm recommendation engine function here
            from apps.yt.routes import get_lastfm_recommendations

            # 1. Fetch the distinct 3 recommendations from Last.fm
            recommended_tracks = get_lastfm_recommendations(full_title)
            
            # Fallback to broad queries only if the recommendation engine completely fails
            if not recommended_tracks:
                logger.warning("Recommendation engine returned empty. Falling back to default search queries.")
                parts = full_title.split(" - ", 1) if " - " in full_title else ["Unknown", full_title]
                fallback_artist = parts[0].strip()
                recommended_tracks = [
                    {"title": f"{fallback_artist} tracks playlist"},
                    {"title": f"similar to {full_title}"},
                    {"title": f"{fallback_artist} radio"}
                ]

            autoplays_to_append = []

            # 2. Iterate through the concrete tracks generated by Last.fm
            for track in recommended_tracks:
                query = track["title"]
                try:
                    # Resolve the unique song title into a YouTube video payload
                    resolved = YTService.auto_pick_song(query)
                    if resolved and resolved.get("videoId"):
                        
                        # Safety check: Block the track if it somehow matches the seed song's videoId
                        if resolved["videoId"] == reference_song.get("videoId"):
                            logger.info(f"Skipping duplicate videoId match for query: {query}")
                            continue
                            
                        watch_url = f"https://www.youtube.com/watch?v={resolved['videoId']}"
                        stream_url = YTService.resolve_stream(watch_url)
                        
                        if stream_url:
                            resolved["url"] = stream_url
                            autoplays_to_append.append(resolved)
                            logger.info(f"Local Autoplay resolved stream for: {resolved.get('title')}")
                            
                            # Stop once we have successfully resolved our lookahead target buffer count
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

            # --- DYNAMIC AUTOPLAY CANCELLATION CHECK ---
            if self.autoplay_enabled:
                listeners = self._get_icecast_listeners()
                bose_on = self.bose.is_on() if self.bose else False
                
                logger.info(f"Autoplay Status Check -> Connected Listeners: {listeners} | Bose Speaker Awake: {bose_on}")
                
                if listeners == 0 and not bose_on:
                    with self.lock:
                        self.autoplay_enabled = False
                    logger.warning("🛑 Autoplay Disabled: 0 active Icecast listeners found and Bose speaker is off.")

            # Assess if autoplay needs to trigger now that we popped the last song
            with self.lock:
                should_trigger_now = (len(self.queue) == 0) and self.autoplay_enabled

            if should_trigger_now:
                logger.info(f"Queue dropped to 0 while launching track. Generating proactive recommendations...")
                threading.Thread(target=self._trigger_autoplay, args=(song,), daemon=True).start()

            try:
                logger.info(f"Playing: {song.get('title', 'Unknown')}")
                target = song["url"]
                
                self.current_process = self.ffmpeg.start_stream(target)

                if self.bose and self.bose.is_on():
                    time.sleep(2)
                    self.bose.trigger_upnp_stream(STREAM_URL)

                self.current_process.wait()

            except Exception as loop_error:
                logger.warning(f"Playback runtime event: {loop_error}")

            finally:
                with self.lock:
                    self.current_process = None
                    self.current_song = None