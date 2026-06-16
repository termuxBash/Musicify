"""#services.ffmpeg_service.py - FFmpeg process management for streaming to Icecast
This module provides a clean interface for starting and stopping FFmpeg processes that stream audio to an Icecast server. It abstracts away the complexities of subprocess management, 
allowing the rest of the application to simply call start_stream with a file path or URL, and handles the lifecycle of the FFmpeg process internally. It also includes a StreamQueueManager class that can manage a queue of tracks to be streamed sequentially, with support for skipping and stopping playback.
"""

import subprocess
import os
import signal
import threading
import queue
from flask import current_app

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"

class FFmpegService:
    def start_stream(self, target):
        """
        Starts an FFmpeg process for either a local file path or a web URL.
        """
        # Common flags for audio-only streaming to Icecast
        cmd = [
            "ffmpeg",
            "-re",               # Read input at native frame/sample rate
            "-i", target,        # Input source (file path or URL)
            "-vn",               # Disable video
            "-c:a", "libvorbis", # Codec
            '-content_type', 'application/ogg',
                '-ar', '44100', '-ac', '2',
                '-f', 'ogg', ICECAST_URL
        ]
        
        # Start process in a new session group for reliable termination
        process = subprocess.Popen(
            cmd,
            preexec_fn=os.setsid,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return process

    def kill_process(self, process):
        """Safely stops a running FFmpeg instance."""
        if not process:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except:
                pass




class StreamQueueManager:
    def __init__(self):
        self.ffmpeg_service = FFmpegService()
        self.play_queue = queue.Queue()
        self.current_process = None
        self.is_running = False
        self.worker_thread = None
        
    def add_to_queue(self, target):
        """Adds a single file path or URL to the end of the queue."""
        self.play_queue.put(target)
        
    def add_multiple_to_queue(self, targets):
        """Adds a list of file paths or URLs to the end of the queue."""
        for target in targets:
            self.play_queue.put(target)

    def start(self):
        """Starts the background playback loop."""
        if self.is_running:
            return
        
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._playback_loop, daemon=True)
        self.worker_thread.start()

    def stop(self):
        """Stops the playback loop and kills any currently running stream."""
        self.is_running = False
        # Clear out remaining items in queue
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
                self.play_queue.task_done()
            except queue.Empty:
                break
                
        if self.current_process:
            self.ffmpeg_service.kill_process(self.current_process)
            self.current_process = None

    def skip_current(self):
        """Skips the currently playing song/URL."""
        if self.current_process:
            # Killing the process forces the playback loop to move to the next item
            self.ffmpeg_service.kill_process(self.current_process)

    def _playback_loop(self):
        """Internal background loop that constantly processes the queue."""
        while self.is_running:
            try:
                # Blocks for 1 second waiting for an item. 
                # Using a timeout allows the loop to check `self.is_running` periodically.
                target = self.play_queue.get(timeout=1)
            except queue.Empty:
                continue

            # Start streaming the track
            self.current_process = self.ffmpeg_service.start_stream(target)
            
            # Wait for the FFmpeg process to naturally finish (or be killed externally)
            if self.current_process:
                self.current_process.wait()
                self.current_process = None
            
            # Signal to the queue that the item has been completely processed
            self.play_queue.task_done()