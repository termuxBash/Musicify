"""core.playback_controller.py - Thread-safe controller for managing access to the music player
The PlaybackController class provides a thread-safe interface for managing access to the music player across multiple Flask
blueprints. It uses a reentrant lock to ensure that only one blueprint can control the player at a time, while allowing any blueprint to enqueue songs or skip tracks if they have ownership.
The controller also provides methods for acquiring and releasing control, as well as checking the current status of the player and its owner.
This design allows for flexible integration of different music sources while maintaining a consistent user experience and preventing conflicts between blueprints.
"""

import threading

class PlaybackController:

    def __init__(self, player):
        self.player = player
        self._owner = None
        self._lock = threading.RLock()

    @property
    def owner(self):
        return self._owner

    def acquire(self, blueprint_name, force=False):
        """
        Request control of the player.

        Returns:
            True -> granted
            False -> denied
        """

        with self._lock:

            # nobody owns it
            if self._owner is None:
                self._owner = blueprint_name
                return True

            # same owner
            if self._owner == blueprint_name:
                return True

            # force takeover
            if force:
                self._reset_player()
                self._owner = blueprint_name
                return True

            return False

    def release(self, blueprint_name):

        with self._lock:

            if self._owner != blueprint_name:
                return False

            self._reset_player()

            self._owner = None

            return True

    def enqueue(self, blueprint_name, song):
        with self._lock:
            if self._owner != blueprint_name:
                return False
            self.player.enqueue(song)

            return True

    def enqueue_many(self, blueprint_name, songs):

        with self._lock:
            if self._owner != blueprint_name:
                return False
            self.player.enqueue_many(songs)
            return True

    def remove_from_queue(self, index):

        with self._lock:
            #Allow any bp to remove from the queue, but not shift ownership
            return self.player.remove_from_queue(index)
    
    def stop(self):
        with self._lock:
            #Allow any bp to stop the track, but not shift ownership
            self.player.stop()
            return True

    def skip(self):
        with self._lock:
            #Allow any bp to skip the track
            self.player.skip()

            return True

    def status(self):
        data = self.player.status()
        data["owner"] = self._owner
        return data

    def _reset_player(self):
        # stop currently playing track
        self.player.skip()

        # clear queue
        with self.player.lock:
            self.player.queue.clear()






"""            Need to clear the list if i want to with a button that doesnt care about ownership also,
if power button is presses it should route it through this app which then clears the queue and then sends it to the bose
"""