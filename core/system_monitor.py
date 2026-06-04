"""
System monitoring utilities
"""
import psutil
import threading
import time

class SystemMonitor:
    """Monitor system resources"""
    
    def __init__(self):
        self.cpu_percent = 0
        self.memory_percent = 0
        self.update_interval = 1  # Update every second
        self._update_thread = None
        self._start_monitoring()
    
    def _start_monitoring(self):
        """Start background monitoring thread"""
        def monitor():
            while True:
                try:
                    self.cpu_percent = psutil.cpu_percent(interval=0.1)
                    self.memory_percent = psutil.virtual_memory().percent
                except:
                    pass
                time.sleep(self.update_interval)
        
        self._update_thread = threading.Thread(target=monitor, daemon=True)
        self._update_thread.start()
    
    def get_status(self):
        """Get current system status"""
        try:
            return {
                "cpu": round(self.cpu_percent, 1),
                "memory": round(self.memory_percent, 1),
                "processes": len(psutil.pids())
            }
        except:
            return {
                "cpu": 0,
                "memory": 0,
                "processes": 0
            }


# Global instance
system_monitor = SystemMonitor()
