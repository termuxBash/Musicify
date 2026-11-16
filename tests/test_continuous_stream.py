import threading
import time
from unittest.mock import MagicMock
from core.stream_manager import QueuePlayer
from services.ffmpeg_service import FFmpegService


def test_ffmpeg_service_publisher_lifecycle():
    service = FFmpegService(icecast_url="icecast://source:hackme@127.0.0.1:8000/mpv.ogg")
    pub = service.ensure_publisher()
    try:
        assert service.is_publisher_alive()
        # Test writing silence PCM
        res = service.write_pcm(b"\x00" * 4096)
        assert res is True
    finally:
        service.stop_publisher()
        assert not service.is_publisher_alive()


def test_queue_player_single_publisher_across_tracks():
    written_chunks = []
    publisher_start_count = [0]
    publisher_stop_count = [0]
    killed_processes = []

    class MockFFmpegService:
        def __init__(self):
            self.publisher_alive = False

        def ensure_publisher(self):
            if not self.publisher_alive:
                self.publisher_alive = True
                publisher_start_count[0] += 1
            return MagicMock()

        def stop_publisher(self):
            self.publisher_alive = False
            publisher_stop_count[0] += 1

        def write_pcm(self, data):
            written_chunks.append(data)
            return True

        def start_decoder(self, song):
            decoder = MagicMock()
            decoder.stdout = MagicMock()
            # Return 2 chunks of audio, then EOF
            pcm_payload = [b"chunk1_" + song["title"].encode(), b"chunk2_" + song["title"].encode(), b""]
            decoder.stdout.read.side_effect = lambda size: pcm_payload.pop(0) if pcm_payload else b""
            return decoder

        def kill_process(self, proc):
            killed_processes.append(proc)

    ffmpeg = MockFFmpegService()
    player = QueuePlayer(ffmpeg)
    player.start()

    try:
        # Enqueue two tracks
        player.enqueue({"title": "Song A", "url": "/music/a.mp3"})
        player.enqueue({"title": "Song B", "url": "/music/b.mp3"})

        # Wait for both tracks to finish playing
        timeout = 5.0
        start_t = time.time()
        while time.time() - start_t < timeout:
            if player.status()["queue_size"] == 0 and player.status()["current_song"] is None:
                # Check that both song chunks were written
                has_song_a = any(b"chunk1_Song A" in c for c in written_chunks)
                has_song_b = any(b"chunk1_Song B" in c for c in written_chunks)
                if has_song_a and has_song_b:
                    break
            time.sleep(0.05)

        assert publisher_start_count[0] == 1, "Publisher must be started only ONCE across multiple tracks"
        assert any(b"chunk1_Song A" in c for c in written_chunks)
        assert any(b"chunk1_Song B" in c for c in written_chunks)
        # Verify silence is sent when idle
        assert any(c == b"\x00" * 4096 for c in written_chunks)

    finally:
        player.stop()
        assert publisher_stop_count[0] == 1


def test_queue_player_skip_terminates_decoder_not_publisher():
    publisher_start_count = [0]
    killed_processes = []

    class MockFFmpegService:
        def __init__(self):
            self.pub = None

        def ensure_publisher(self):
            if not self.pub:
                publisher_start_count[0] += 1
                self.pub = MagicMock()
            return self.pub

        def stop_publisher(self):
            pass

        def write_pcm(self, data):
            return True

        def start_decoder(self, song):
            decoder = MagicMock()
            decoder.stdout = MagicMock()
            # Infinite stream until killed
            decoder.stdout.read.side_effect = lambda size: time.sleep(0.05) or b"infinite_audio"
            return decoder

        def kill_process(self, proc):
            killed_processes.append(proc)

    ffmpeg = MockFFmpegService()
    player = QueuePlayer(ffmpeg)
    player.start()

    try:
        player.enqueue({"title": "Song 1", "url": "/music/1.mp3"})
        player.enqueue({"title": "Song 2", "url": "/music/2.mp3"})

        # Wait for Song 1 to start
        timeout = 2.0
        start_t = time.time()
        while time.time() - start_t < timeout:
            if player.current_song and player.current_song.get("title") == "Song 1":
                break
            time.sleep(0.02)

        assert player.current_song["title"] == "Song 1"

        # Skip Song 1
        player.skip()

        # Wait for Song 2 to start
        start_t = time.time()
        while time.time() - start_t < timeout:
            if player.current_song and player.current_song.get("title") == "Song 2":
                break
            time.sleep(0.02)

        assert player.current_song["title"] == "Song 2"
        # Verify the decoder for Song 1 was killed
        assert len(killed_processes) >= 1
        # Verify the publisher was started only once
        assert publisher_start_count[0] == 1
    finally:
        player.stop()
