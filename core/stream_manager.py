"""core.stream_manager.py - Threaded music queue manager with dynamic autoplay and Icecast listener monitoring
The StreamQueueManager class provides a thread-safe music queue management system that integrates with FFmpeg for streaming to Icecast.
It maintains a queue of songs to be played, handles the lifecycle of FFmpeg processes, and includes an autoplay feature that dynamically generates new tracks based on the currently playing song
using Last.fm recommendations or local file shuffling depending on the source. The manager also monitors the number of active listeners connected to the Icecast stream and the state of the Bose speaker to intelligently disable autoplay when no one is listening, preventing unnecessary resource usage.
The QueuePlayer class encapsulates the core functionality of managing the playback queue, starting and stopping streams
"""

import threading
import time
import logging
import os
import urllib.request
import json
from core.settings import DEFAULT_AUTOPLAY_ENABLED, STREAM_URL, get_system_prompt
from core.history_db import is_blacklisted_rick_roll, record_rick_roll_timestamp

logger = logging.getLogger(__name__)

CHUNK_SIZE = 17640
SILENCE_CHUNK = bytes(CHUNK_SIZE)
EMPTY_QUEUE_PUBLISHER_TIMEOUT = 120
PRECACHE_REMAINING_SECONDS = 50

class QueuePlayer:

    def __init__(
        self,
        ffmpeg_service,
        bose_worker=None,
        autoplay_enabled=DEFAULT_AUTOPLAY_ENABLED,
        flask_app=None,
    ):
        self.ffmpeg = ffmpeg_service
        self.bose = bose_worker
        self.flask_app = flask_app

        self.queue = []
        self.lock = threading.RLock()

        self.current_process = None
        self.current_song = None

        self.running = False
        self.thread = None
        self._playback_generation = 0
        
        self.autoplay_enabled = autoplay_enabled 
        self._autoplay_suppressed = False
        self.incogni_mode = False
        self.radio_handler = None
        self._ready_cache = {}
        self._precache_threads = set()
        self._active_cache_path = None
        self._playback_elapsed = 0.0
        self._current_duration = None
        self._playback_started_monotonic = None

    def _bump_playback_generation(self):
        with self.lock:
            self._playback_generation += 1
            return self._playback_generation

    def start(self):
        if self.running:
            return
        self.running = True
        if hasattr(self.ffmpeg, "ensure_publisher"):
            try:
                self.ffmpeg.ensure_publisher()
            except Exception as e:
                logger.error(f"Failed to start FFmpeg publisher: {e}")
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self._bump_playback_generation()
        self.running = False
        self.skip()
        with self.lock:
            self.queue.clear()
            self.current_song = None
            cache_paths = list(self._ready_cache.values())
        for cache_path in cache_paths:
            if hasattr(self.ffmpeg, "clear_cached_song"):
                self.ffmpeg.clear_cached_song(cache_path)
        self._ready_cache.clear()
        self._active_cache_path = None
        self._playback_elapsed = 0.0
        self._current_duration = None
        if hasattr(self.ffmpeg, "stop_publisher"):
            try:
                self.ffmpeg.stop_publisher()
            except Exception as e:
                logger.error(f"Failed to stop FFmpeg publisher: {e}")

    def enqueue(self, song):
        with self.lock:
            self.queue.append(song)

    def enqueue_many(self, songs):
        with self.lock:
            self.queue.extend(songs)

    def _enqueue_autoplay(self, songs):
        with self.lock:
            if not self.autoplay_enabled or self._autoplay_suppressed:
                return False
            self.queue.extend(songs)
            return True

    def remove_from_queue(self, index):
        with self.lock:
            if 0 <= index < len(self.queue):
                song = self.queue.pop(index)
                cache_path = song.get("cache_path")
                identity = self._song_identity(song)
                self._ready_cache.pop(identity, None)
                if cache_path:
                    if hasattr(self.ffmpeg, "clear_cached_song"):
                        self.ffmpeg.clear_cached_song(cache_path)
                return song
        return None

    def move_to_front(self, index):
        return self.move_to_index(index, 0)

    def move_to_index(self, source_index, target_index):
        with self.lock:
            if not 0 <= source_index < len(self.queue):
                return False
            if not 0 <= target_index < len(self.queue):
                return False
            self.queue.insert(target_index, self.queue.pop(source_index))
            return True

    def skip(self):
        self._bump_playback_generation()
        if hasattr(self.ffmpeg, "clear_buffer"):
            try:
                self.ffmpeg.clear_buffer()
            except Exception:
                pass
        if self.current_process:
            logger.info("Skip button clicked. Terminating decoder process.")
            try:
                self.ffmpeg.kill_process(self.current_process)
            except Exception as e:
                logger.error(f"Error killing process: {e}")

    def toggle_autoplay(self, status: bool):
        with self.lock:
            self.autoplay_enabled = status
            logger.info(f"Autoplay state explicitly shifted to: {self.autoplay_enabled}")

    def suppress_autoplay(self):
        with self.lock:
            self._autoplay_suppressed = True
            if self.autoplay_enabled:
                logger.info("Autoplay temporarily suppressed during a queue operation")

    def resume_autoplay(self):
        with self.lock:
            was_suppressed = self._autoplay_suppressed
            self._autoplay_suppressed = False
            if was_suppressed and self.autoplay_enabled:
                logger.info("Autoplay suppression released")

    def status(self):
        with self.lock:
            queue_copy = list(self.queue)
            autoplay_status = self.autoplay_enabled
            is_playing = self.current_song is not None
            elapsed = self._playback_elapsed
            duration = self._current_duration
            remaining = (
                max(0.0, duration - elapsed)
                if isinstance(duration, (int, float))
                else None
            )

        return {
            "queue": queue_copy,
            "current_song": self.current_song,
            "queue_size": len(queue_copy),
            "playing": is_playing,
            "autoplay_enabled": autoplay_status,
            "playback_elapsed": elapsed,
            "current_duration": duration,
            "playback_remaining": remaining,
        }

    @staticmethod
    def _song_identity(song):
        if not isinstance(song, dict):
            return ""
        return str(
            song.get("videoId")
            or song.get("source_url")
            or song.get("url")
            or song.get("title")
            or ""
        )

    def _mark_precache_status(self, song, status, cache_path=None, duration=None):
        identity = self._song_identity(song)
        with self.lock:
            song["_precache_status"] = status
            if cache_path:
                song["cache_path"] = cache_path
                self._ready_cache[identity] = cache_path
            if isinstance(duration, (int, float)):
                song["_duration"] = duration

    def _precache_worker(self, song):
        identity = self._song_identity(song)
        try:
            result = self.ffmpeg.precache_song(song)
            cache_path = result.get("path") if isinstance(result, dict) else None
            duration = result.get("duration") if isinstance(result, dict) else None
            if not isinstance(cache_path, str) or not cache_path:
                raise RuntimeError("Precache produced no file")
            self._mark_precache_status(
                song,
                "ready",
                cache_path=cache_path,
                duration=duration,
            )
            logger.info("Precached queue song: %s", song.get("title", "Unknown"))
        except Exception as error:
            logger.warning(
                "Could not precache '%s': %s",
                song.get("title", "Unknown"),
                error,
            )
            self._mark_precache_status(song, "error")
            with self.lock:
                for index, queued_song in enumerate(list(self.queue)):
                    if queued_song is song:
                        self.queue.pop(index)
                        self.queue.append(queued_song)
                        logger.info(
                            "Moved failed precache song to bottom of queue: %s",
                            queued_song.get("title", "Unknown"),
                        )
                        break
            logger.info("Precache failed for queue song: %s", identity)
        finally:
            with self.lock:
                self._precache_threads.discard(threading.current_thread())

    def _ensure_next_precache(self):
        with self.lock:
            if not self.queue:
                return
            song = self.queue[0]
            status = song.get("_precache_status")
            if status in {"downloading", "ready", "error"}:
                return
            if not (song.get("source_url") or song.get("videoId")):
                return

            song["_precache_status"] = "downloading"
            thread = threading.Thread(
                target=self._precache_worker,
                args=(song,),
                daemon=True,
                name="musicify-precache",
            )
            self._precache_threads.add(thread)
        thread.start()

    def _monitor_precache(self):
        elapsed = self._playback_elapsed
        if (
            isinstance(self._current_duration, (int, float))
            and self._current_duration - elapsed <= PRECACHE_REMAINING_SECONDS
        ):
            self._ensure_next_precache()

    def _cleanup_finished_cache(self, song):
        cache_path = song.get("cache_path") if isinstance(song, dict) else None
        if cache_path:
            if hasattr(self.ffmpeg, "clear_cached_song"):
                self.ffmpeg.clear_cached_song(cache_path)
            if self._active_cache_path == cache_path:
                self._active_cache_path = None

    def _cleanup_old_cache(self):
        keep_paths = []
        if isinstance(self._active_cache_path, str):
            keep_paths.append(self._active_cache_path)
        with self.lock:
            keep_paths.extend(
                path
                for path in self._ready_cache.values()
                if isinstance(path, str)
            )
        if hasattr(self.ffmpeg, "cleanup_cache"):
            self.ffmpeg.cleanup_cache(keep_paths=keep_paths)

    def _get_next_song(self):
        with self.lock:
            if not self.queue:
                return None
            return self.queue.pop(0)

    def _get_icecast_listeners(self):
        """Fetches the active listener count from Icecast's JSON endpoint."""
        try:
            # Gets the stream status from the stream url link. Cuts off mpv.ogg and replaces with status-json.xsl
            cut = STREAM_URL.split("/")[-1]
            url = STREAM_URL.split(cut)[0] + "status-json.xsl"
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
                        if ("/" + cut) in source.get("listenurl", ""):
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
                from apps.local.routes import get_random_local_track_payload
                autoplays_to_append = []

                if self.flask_app is None:
                    raise RuntimeError("Local autoplay requires the Flask application instance.")

                # url_for needs a request context when SERVER_NAME is unset.
                with self.flask_app.test_request_context():
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
                    if self._enqueue_autoplay(autoplays_to_append):
                        logger.info(f"✅ Success! Enqueued {len(autoplays_to_append)} random local tracks into the buffer.")
                else:
                    logger.warning("⚠️ Local random autoplay loop found no music files in directory structure.")
            except Exception as local_err:
                logger.error(f"❌ Failed executing local fallback autoplay allocation: {local_err}", exc_info=True)
            return

        # ---- PATH B: YOUTUBE / PLAYLIST LAST.FM RECOMMENDATIONS ----
        try:
            from services.yt_service import YTService
            from apps.yt.routes import (
                get_gemini_recommendations,
                get_lastfm_recommendations,
            )

            gemini_prompt = (
                f"Current playing track: '{full_title}'. "
                f"{get_system_prompt()}"
            )
            gemini_tracks = get_gemini_recommendations(gemini_prompt)
            recommended_tracks = [
                {
                    "title": track["song_name"],
                    "videoId": track["song_id"],
                }
                for track in gemini_tracks
            ]

            if not recommended_tracks:
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
                    track_vid = track.get("videoId")
                    if track_vid and is_blacklisted_rick_roll(track_vid, query):
                        logger.warning(f"Autoplay detected blacklisted Rick Roll ID {track_vid} for '{query}'")
                        record_rick_roll_timestamp()
                        resolved = YTService.auto_pick_song(query)
                    elif track_vid:
                        resolved = {
                            "title": query,
                            "videoId": track_vid,
                        }
                    else:
                        resolved = YTService.auto_pick_song(query)
                    if resolved and resolved.get("videoId"):
                        if resolved["videoId"] == reference_song.get("videoId"):
                            logger.info(f"Skipping duplicate videoId match for query: {query}")
                            continue
                            
                        watch_url = f"https://www.youtube.com/watch?v={resolved['videoId']}"
                        try:
                            stream_url = YTService.resolve_stream(watch_url)
                        except Exception as resolve_error:
                            if not track.get("videoId"):
                                raise

                            logger.warning(
                                f"Gemini YouTube ID failed for '{query}', searching by title instead: "
                                f"{resolve_error}"
                            )
                            resolved = YTService.auto_pick_song(query)
                            if not resolved or not resolved.get("videoId"):
                                continue

                            watch_url = f"https://www.youtube.com/watch?v={resolved['videoId']}"
                            stream_url = YTService.resolve_stream(watch_url)
                        
                        if stream_url:
                            resolved["url"] = stream_url
                            resolved["record_history"] = not self.incogni_mode
                            resolved.setdefault(
                                "thumbnail",
                                f"https://img.youtube.com/vi/{resolved['videoId']}/hqdefault.jpg",
                            )
                            autoplays_to_append.append(resolved)
                            logger.info(f"YouTube Autoplay resolved stream for: {resolved.get('title')}")
                            
                            if len(autoplays_to_append) >= 3:
                                break
                except Exception as track_err:
                    logger.error(f"Failed to resolve YouTube context query entry '{query}': {track_err}")

            if autoplays_to_append:
                if self._enqueue_autoplay(autoplays_to_append):
                    logger.info(f"✅ Success! Enqueued {len(autoplays_to_append)} lookahead tracks into the buffer.")
            else:
                logger.warning("YouTube lookahead loop completed but stream links could not be verified.")

        except Exception as general_err:
            logger.error(f"YouTube autoplay engine exception: {general_err}", exc_info=True)

    def _worker(self):
        if hasattr(self.ffmpeg, "ensure_publisher"):
            try:
                self.ffmpeg.ensure_publisher()
            except Exception as pub_err:
                logger.error(f"Failed to ensure FFmpeg publisher in worker: {pub_err}")

        queue_empty_since = None
        publisher_stopped_for_idle = False

        while self.running:
            song = self._get_next_song()

            if song is None:
                if queue_empty_since is None:
                    queue_empty_since = time.monotonic()
                elif (
                    not publisher_stopped_for_idle
                    and time.monotonic() - queue_empty_since >= EMPTY_QUEUE_PUBLISHER_TIMEOUT
                ):
                    with self.lock:
                        queue_still_empty = not self.queue and self.current_song is None
                    if queue_still_empty and hasattr(self.ffmpeg, "stop_publisher"):
                        try:
                            self.ffmpeg.stop_publisher()
                            publisher_stopped_for_idle = True
                            logger.info("Stopped FFmpeg publisher after 2 minutes with an empty queue.")
                        except Exception as pub_err:
                            logger.error("Failed to stop idle FFmpeg publisher: %s", pub_err)
                time.sleep(0.25)
                continue

            queue_empty_since = None
            publisher_stopped_for_idle = False

            previous_cache_path = self._active_cache_path
            next_cache_path = song.get("cache_path") if isinstance(song, dict) else None
            if isinstance(next_cache_path, str) and not os.path.isfile(next_cache_path):
                logger.warning(
                    "Precached file missing for '%s'; removing stale cache entry.",
                    song.get("title", "Unknown"),
                )
                song.pop("cache_path", None)
                identity = self._song_identity(song)
                with self.lock:
                    self._ready_cache.pop(identity, None)
                next_cache_path = None
            if (
                isinstance(previous_cache_path, str)
                and previous_cache_path != next_cache_path
                and hasattr(self.ffmpeg, "clear_cached_song")
            ):
                self.ffmpeg.clear_cached_song(previous_cache_path)
            self._active_cache_path = (
                next_cache_path
                if isinstance(next_cache_path, str)
                else None
            )
            self._cleanup_old_cache()
            self._playback_elapsed = 0.0
            self._current_duration = (
                song.get("_duration")
                if isinstance(song.get("_duration"), (int, float))
                else None
            )

            # Detect blacklisted Rick Roll attempt each time it tries to play
            if is_blacklisted_rick_roll(song):
                logger.warning(
                    f"Blacklisted video ID detected as track tries to play: {song.get('title')}. Recording timestamp in extra_data."
                )
                record_rick_roll_timestamp()
                query = (song.get("title") or "").strip()
                from services.yt_service import YTService
                picked = YTService.auto_pick_song(query) if query else None
                if (
                    picked
                    and picked.get("videoId")
                    and not is_blacklisted_rick_roll(picked.get("videoId"), picked.get("title"))
                ):
                    watch_url = f"https://www.youtube.com/watch?v={picked['videoId']}"
                    try:
                        stream_info = YTService.resolve_stream_info(watch_url)
                        picked["url"] = stream_info["url"]
                        picked["source_url"] = watch_url
                        picked["stream_headers"] = stream_info.get("http_headers", {})
                        picked["record_history"] = song.get("record_history", True)
                        song = picked
                    except Exception as resolve_err:
                        logger.error(f"Failed to resolve stream for auto-picked replacement track: {resolve_err}")
                        continue
                else:
                    logger.warning(f"Could not auto-pick replacement for blacklisted song '{query}', skipping playback.")
                    continue

            with self.lock:
                self.current_song = None
                song_generation = self._playback_generation

            title = song.get("title")
            has_radio_url = isinstance(song.get("url"), str) or (
                isinstance(song.get("station"), dict) and bool(song["station"].get("url"))
            )
            if (
                isinstance(title, str)
                and title.startswith("Radio:(")
                and title.endswith(")")
                and has_radio_url
            ):
                with self.lock:
                    self.current_song = song
                try:
                    if self.radio_handler is None:
                        logger.error("Cannot play queued radio station: no radio handler is configured.")
                    else:
                        self.radio_handler(song.get("station") or song)
                except Exception:
                    logger.exception("Could not start queued radio station.")
                finally:
                    with self.lock:
                        if self.current_song == song:
                            self.current_song = None
                continue

            # --- DYNAMIC AUTOPLAY CANCELLATION CHECK ---
            if self.autoplay_enabled:
                listeners = self._get_icecast_listeners()
                bose_on = self.bose.get_status()["is_on"] if self.bose else False
                
                logger.info(f"Autoplay Status Check -> Connected Listeners: {listeners} | Bose Speaker Awake: {bose_on}")
                
                if listeners == 0 and not bose_on:
                    with self.lock:
                        self.autoplay_enabled = False
                    logger.warning("🛑 Autoplay Disabled: 0 active Icecast listeners found and Bose speaker is off.")

            # Assess if autoplay needs to trigger now that we popped the last song
            with self.lock:
                should_trigger_now = (
                    len(self.queue) == 0
                    and self.autoplay_enabled
                    and not self._autoplay_suppressed
                )

            if should_trigger_now:
                logger.info("Queue dropped to 0 while launching track. Generating proactive recommendations...")
                threading.Thread(target=self._trigger_autoplay, args=(song,), daemon=True).start()

            decoder = None
            playback_started = False
            playback_interrupted = False
            try:
                logger.info(f"Playing: {song.get('title', 'Unknown')}")
                target = song
                if self._current_duration is None and hasattr(self.ffmpeg, "get_media_duration"):
                    duration_target = (
                        target.get("cache_path")
                        or target.get("url")
                        or target.get("source_url")
                    )
                    if isinstance(duration_target, str):
                        self._current_duration = self.ffmpeg.get_media_duration(duration_target)
                
                if hasattr(self.ffmpeg, "start_decoder"):
                    decoder = self.ffmpeg.start_decoder(target)
                elif hasattr(self.ffmpeg, "start_stream"):
                    decoder = self.ffmpeg.start_stream(target)
                else:
                    raise RuntimeError("No start_decoder or start_stream method on ffmpeg service.")

                with self.lock:
                    self.current_process = decoder

                if self.bose and self.bose.get_status()["is_on"]:
                    try:
                        playing_info = self.bose.get_playing_url()
                        # Only re-initiate stream on Bose if it's not already playing our mount
                        if not (playing_info and playing_info.get("url") == STREAM_URL):
                            self.bose.trigger_upnp_stream(STREAM_URL)
                    except Exception as bose_err:
                        logger.warning(f"Bose check error: {bose_err}")
                        self.bose.trigger_upnp_stream(STREAM_URL)

                # Feed decoded PCM from decoder to the persistent Icecast publisher
                while self.running:
                    with self.lock:
                        if self._playback_generation != song_generation:
                            logger.info("Playback generation shifted (skipped/stopped).")
                            playback_interrupted = True
                            break

                    chunk = decoder.stdout.read(CHUNK_SIZE) if decoder and decoder.stdout else None
                    if not chunk:
                        # Reached EOF of song
                        break

                    self._playback_elapsed += len(chunk) / (44100 * 4)
                    self._monitor_precache()

                    if hasattr(self.ffmpeg, "write_pcm"):
                        if self.ffmpeg.write_pcm(chunk) is False:
                            continue

                    if not playback_started:
                        with self.lock:
                            if self._playback_generation != song_generation:
                                playback_interrupted = True
                                break
                            self.current_song = song
                        playback_started = True
                        from core.stats import record_played_song
                        record_played_song(song)

            except Exception as loop_error:
                logger.exception("Playback failed for '%s': %s", title or "Unknown", loop_error)

            finally:
                with self.lock:
                    if self._playback_generation != song_generation:
                        playback_interrupted = True
                    self.current_process = None
                    self.current_song = None
                    identity = self._song_identity(song)
                    if self._ready_cache.get(identity) == song.get("cache_path"):
                        self._ready_cache.pop(identity, None)
                if decoder:
                    try:
                        return_code = decoder.poll()
                        if not playback_interrupted:
                            if isinstance(return_code, int) and return_code != 0:
                                logger.error(
                                    "Decoder exited with code %d for '%s'.",
                                    return_code,
                                    title or "Unknown",
                                )
                            elif not playback_started:
                                logger.warning(
                                    "Decoder ended before producing audio for '%s' (exit code: %s).",
                                    title or "Unknown",
                                    return_code,
                                )
                    except Exception:
                        logger.exception("Could not inspect decoder status for '%s'.", title or "Unknown")
                    try:
                        self.ffmpeg.kill_process(decoder)
                    except Exception:
                        logger.exception("Failed to stop decoder for '%s'.", title or "Unknown")