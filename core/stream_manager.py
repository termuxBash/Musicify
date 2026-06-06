import threading
from flask import current_app # type: ignore
from services.ffmpeg_service import FFmpegService, StreamQueueManager
import time

class StreamManager:
    def __init__(self, streamer_instance):
        self.streamer = streamer_instance
        self.active_session = None
        self.lock = threading.Lock()

    def request_control(self, session_id, override=False):
        """
        Validates if a blueprint can take control.
        Returns True if access granted, False otherwise.
        """
        with self.lock:
            # 1. Access granted if idle or same session returning
            if self.active_session is None or self.active_session == session_id:
                self.active_session = session_id
                return True
            
            # 2. Access granted via override (hijack)
            if override:
                print(f"[Manager] Session '{session_id}' overriding '{self.active_session}'")
                self.active_session = session_id
                
                # Forcefully clear and reset the streamer's internal states
                self._reset_streamer()
                return True
            
            # 3. Denied if another blueprint is busy
            return False

    def stream_media(self, session_id, media_type, path, title="Unknown Track"):
        """Proxies the payload to the streamer ONLY if the session matches."""
        with self.lock:
            if self.active_session != session_id:
                return False
        
        self.streamer.add_to_queue(media_type, path, title)
        return True

    def release_control(self, session_id):
        """Gracefully allows a blueprint to clear its lock."""
        with self.lock:
            if self.active_session == session_id:
                self._reset_streamer()
                self.active_session = None
                print(f"[Manager] Session '{session_id}' released control.")

    def _reset_streamer(self):
        """Violently clears the streamer's queue and terminates active processes."""
        # Wipe the queue safely
        while not self.streamer.media_queue.empty():
            try:
                self.streamer.media_queue.get_nowait()
                self.streamer.media_queue.task_done()
            except:
                break
        
        # Kill running or prefetched tracks immediately
        if self.streamer.state["next_process"]:
            self.streamer.ffmpeg.kill_process(self.streamer.state["next_process"])
            self.streamer.state["next_process"] = None
            self.streamer.state["next_track"] = None
            self.streamer.next_ready.clear()

        if self.streamer.state["current_process"]:
            self.streamer.ffmpeg.kill_process(self.streamer.state["current_process"])



stream_worker = StreamManager(None)  # Placeholder, will be set to actual Streamer instance in app.py






import uuid

class LockedWorker:
    def __init__(self):
        self.shared_list = []
        self._current_owner = None  # Tracks which blueprint holds the lock

    def acquire_lock(self, client_id) -> bool:
        """Attempts to secure the lock for a specific blueprint."""
        if self._current_owner is None:
            self._current_owner = client_id
            return True
        return False

    def force_unlock(self):
        """Allows any blueprint to break the current lock immediately."""
        self._current_owner = None

    def add_to_list(self, client_id, item):
        """Updates the list only if the calling blueprint holds the lock."""
        if self._current_owner == client_id:
            self.shared_list.append(item)
            return True
        return False


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

    def start(self):

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._worker,
            daemon=True
        )

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
            self.ffmpeg.kill_process(self.current_process)

    def status(self):

        with self.lock:
            queue_copy = list(self.queue)

        return {
            "queue": queue_copy,
            "current_song": self.current_song,
            "queue_size": len(queue_copy),
            "playing": self.current_process is not None
        }

    def _get_next_song(self):

        with self.lock:

            if not self.queue:
                return None

            return self.queue.pop(0)

    def _worker(self):

        while self.running:

            song = self._get_next_song()

            if song is None:
                time.sleep(1)
                continue

            try:

                self.current_song = song

                logger.info(
                    f"Playing: {song.get('title', 'Unknown')}"
                )

                target = song["url"]

                self.current_process = (
                    self.ffmpeg.start_stream(target)
                )

                if self.bose:
                    time.sleep(2)
                    self.bose.trigger_upnp_stream(
                        STREAM_URL
                    )

                self.current_process.wait()

            except Exception:
                logger.exception(
                    "Playback error"
                )

            finally:

                self.current_process = None
                self.current_song = None