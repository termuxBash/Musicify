"""
Global FFmpeg Worker Lock Manager
Manages which app (local or youtube) has control of the FFmpeg worker
"""
import threading
import time
from datetime import datetime

class FFmpegLockManager:
    """Manages exclusive access to FFmpeg worker"""
    
    def __init__(self):
        self.lock = threading.Lock()
        self.current_owner = None
        self.locked_at = None
        self.lock_timeout = 300  # 5 minutes timeout
    
    def acquire(self, owner_name):
        """Attempt to acquire the lock"""
        with self.lock:
            # Check if lock is expired
            if self.current_owner and self.locked_at:
                elapsed = time.time() - self.locked_at
                if elapsed > self.lock_timeout:
                    self.current_owner = None
                    self.locked_at = None
            
            # If no owner or lock expired, grant ownership
            if self.current_owner is None:
                self.current_owner = owner_name
                self.locked_at = time.time()
                return {"acquired": True, "owner": owner_name}
            
            # Lock held by someone else
            return {
                "acquired": False,
                "owner": self.current_owner,
                "time_remaining": self.lock_timeout - (time.time() - self.locked_at)
            }
    
    def force_acquire(self, owner_name):
        """Force acquire the lock (for user-initiated takeover)"""
        with self.lock:
            self.current_owner = owner_name
            self.locked_at = time.time()
            return {"acquired": True, "owner": owner_name, "forced": True}
    
    def release(self, owner_name):
        """Release the lock if owned by caller"""
        with self.lock:
            if self.current_owner == owner_name:
                self.current_owner = None
                self.locked_at = None
                return {"released": True}
            return {"released": False, "reason": "Not owner"}
    
    def get_status(self):
        """Get current lock status"""
        with self.lock:
            if self.current_owner and self.locked_at:
                elapsed = time.time() - self.locked_at
                if elapsed > self.lock_timeout:
                    self.current_owner = None
                    self.locked_at = None
            
            return {
                "owner": self.current_owner,
                "locked_at": self.locked_at,
                "time_elapsed": time.time() - self.locked_at if self.locked_at else 0
            }


# Global instance
ffmpeg_lock = FFmpegLockManager()
