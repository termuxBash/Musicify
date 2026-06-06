import time
import queue
import threading
from services.ffmpeg_service import FFmpegService

class MediaStreamer:
    def __init__(self, yt_service, bose_service, stream_url):
        self.ffmpeg = FFmpegService()
        self.yt_service = yt_service
        self.bose = bose_service
        self.bose_stream_url = stream_url
        
        # Internal Queue: Stores dictionaries like {"type": "url"/"file", "path": "...", "title": "..."}
        self.media_queue = queue.Queue()
        
        # Shared Pipeline State
        self.state = {
            "current_track": None,
            "is_playing": False,
            "current_process": None,
            "next_process": None,
            "next_track": None
        }
        self.lock = threading.Lock()
        self.next_ready = threading.Event()
        
        # Start Threads automatically
        threading.Thread(target=self._prefetch_worker, daemon=True).start()
        threading.Thread(target=self._playback_worker, daemon=True).start()

    def add_to_queue(self, media_type, path, title="Unknown Track"):
        """Public method for Flask Blueprints to queue media."""
        item = {"type": media_type, "path": path, "title": title}
        self.media_queue.put(item)
        return item

    def _resolve_target(self, item):
        """Helper to distinguish between local files and YouTube URLs"""
        if item["type"] == "url":
            return self.yt_service.resolve_stream(item["path"])
        return item["path"] # Return local file path as-is

    def _prefetch_worker(self):
        """Looks ahead in the queue to prepare the next stream before the current one finishes."""
        while True:
            # If next buffer is full, or nothing is currently playing, idle.
            if self.state["next_process"] or not self.state["is_playing"]:
                time.sleep(1)
                continue
            
            # Look at the next item in the queue without extracting it yet
            if not self.media_queue.empty():
                try:
                    # Snatch a copy of the next item safely
                    next_item = self.media_queue.queue[0] 
                    
                    print(f"[Prefetcher] Buffering next item: {next_item['title']}")
                    resolved_target = self._resolve_target(next_item)
                    
                    if resolved_target:
                        proc = self.ffmpeg.start_stream(resolved_target)
                        
                        with self.lock:
                            self.state["next_process"] = proc
                            self.state["next_track"] = next_item
                        
                        self.next_ready.set()
                        print("[Prefetcher] Next track buffered successfully.")
                except Exception as e:
                    print(f"[Prefetcher] Error pre-buffering: {e}")
                    
            time.sleep(1)

    def _playback_worker(self):
        """Monitors and loops through active playback streams."""
        while True:
            with self.lock:
                # If everything is dry, sit tight
                if self.media_queue.empty() and not self.state["next_process"]:
                    self.state["is_playing"] = False
                    time.sleep(1)
                    continue

                # Case 1: Prefetched track is buffered and ready to swap
                if self.state["next_process"] and self.next_ready.is_set():
                    if self.state["current_process"]:
                        self.ffmpeg.kill_process(self.state["current_process"])
                    
                    # Advance state variables smoothly
                    self.state["current_process"] = self.state["next_process"]
                    self.state["current_track"] = self.state["next_track"]
                    
                    # Reset buffering slot
                    self.state["next_process"] = None
                    self.state["next_track"] = None
                    self.next_ready.clear()
                    
                    # Pop the actual item from queue now that it's active
                    self.media_queue.get()
                    self.state["is_playing"] = True
                
                # Case 2: System was idle (Cold start)
                else:
                    item = self.media_queue.get()
                    self.state["current_track"] = item
                    self.state["is_playing"] = True
                    
                    try:
                        resolved_target = self._resolve_target(item)
                        if self.state["current_process"]:
                            self.ffmpeg.kill_process(self.state["current_process"])
                        self.state["current_process"] = self.ffmpeg.start_stream(resolved_target)
                    except Exception as e:
                        print(f"[Playback Engine] Cold start failed: {e}")
                        continue

            # Execute active streaming to Hardware
            try:
                # Direct Bose to look at the continuous Icecast mount point
                self.bose.trigger_upnp_stream(self.bose_stream_url)
                
                # Hand over control to process block until track finishes natively
                if self.state["current_process"]:
                    self.state["current_process"].wait()
            except Exception as e:
                print(f"[Playback Engine] Playback Runtime Exception: {e}")