"""core.stream_manager.py - Threaded music queue manager with dynamic autoplay and Icecast listener monitoring
The StreamQueueManager class provides a thread-safe music queue management system that integrates with FFmpeg for streaming to Icecast.
It maintains a queue of songs to be played, handles the lifecycle of FFmpeg processes, and includes an autoplay feature that dynamically generates new tracks based on the currently playing song
using Last.fm recommendations or local file shuffling depending on the source. The manager also monitors the number of active listeners connected to the Icecast stream and the state of the Bose speaker to intelligently disable autoplay when no one is listening, preventing unnecessary resource usage.
The QueuePlayer class encapsulates the core functionality of managing the playback queue, starting and stopping streams
"""

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
        """Generates lookahead track additions using Last.fm recommendations or local shuffles."""
        full_title = reference_song.get("title", "Unknown")
        song_url = reference_song.get("url", "")

        # ---- OWNER CHECK (Mirroring the Blueprint logic safely outside request context) ----
        # Read directly from the class instance properties safely wrapped in your thread lock
        with self.lock:
            # If your app initializes with self.owner tracking ownership state:
            current_owner = getattr(self, "owner", None)
            
            # Match the Blueprint strategy: "if player.owner is None: assume local fallback or match URL"
            if current_owner is None:
                if song_url.startswith("/") or "ROOT_DIR" in song_url or not song_url.startswith("http"):
                    current_owner = "local"
                else:
                    current_owner = "youtube"

        logger.info(f"🔮 AUTOPLAY ACTIVE [Evaluated Owner: {current_owner}]: Processing lookup for -> '{full_title}'")
        
        # ---- PATH A: LOCAL OWNER RANDOM PICK ----
        if current_owner == "local":
            try:
                # Import from your blueprint context directly 
                from apps.local.routes import get_random_local_track_payload
                autoplays_to_append = []
                
                # Fetch up to 3 unique random local tracks
                for _ in range(15):  # Give extra cycles to avoid duplicates
                    track_payload = get_random_local_track_payload()
                    if track_payload:
                        # Ensure we do not immediately queue the track that just started playing
                        if track_payload["url"] == song_url:
                            continue
                        if any(t["url"] == track_payload["url"] for t in autoplays_to_append):
                            continue
                            
                        autoplays_to_append.append(track_payload)
                        logger.info(f"📦 Local Autoplay picked random track: {track_payload.get('title')}")
                        
                        if len(autoplays_to_append) >= 3:
                            break
                            
                if autoplays_to_append:
                    self.enqueue_many(autoplays_to_append)
                    logger.info(f"✅ Success! Enqueued {len(autoplays_to_append)} random local tracks into the buffer.")
                else:
                    logger.warning("⚠️ Local random autoplay loop found no music files in directory structure.")
            except Exception as local_err:
                logger.error(f"❌ Failed executing local fallback autoplay allocation: {local_err}", exc_info=True)
            return

        # ---- PATH B: YOUTUBE / PLAYLIST LAST.FM RECOMMENDATIONS ----
        try:
            from services.yt_service import YTService
            from apps.yt.routes import get_lastfm_recommendations

            recommended_tracks = get_lastfm_recommendations(full_title)
            
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

            for track in recommended_tracks:
                query = track["title"]
                try:
                    resolved = YTService.auto_pick_song(query)
                    if resolved and resolved.get("videoId"):
                        if resolved["videoId"] == reference_song.get("videoId"):
                            logger.info(f"Skipping duplicate videoId match for query: {query}")
                            continue
                            
                        watch_url = f"https://www.youtube.com/watch?v={resolved['videoId']}"
                        stream_url = YTService.resolve_stream(watch_url)
                        
                        if stream_url:
                            resolved["url"] = stream_url
                            autoplays_to_append.append(resolved)
                            logger.info(f"YouTube Autoplay resolved stream for: {resolved.get('title')}")
                            
                            if len(autoplays_to_append) >= 3:
                                break
                except Exception as track_err:
                    logger.error(f"Failed to resolve YouTube context query entry '{query}': {track_err}")

            if autoplays_to_append:
                self.enqueue_many(autoplays_to_append)
                logger.info(f"✅ Success! Enqueued {len(autoplays_to_append)} lookahead tracks into the buffer.")
            else:
                logger.warning("YouTube lookahead loop completed but stream links could not be verified.")

        except Exception as general_err:
            logger.error(f"YouTube autoplay engine exception: {general_err}", exc_info=True)

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