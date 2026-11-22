import io
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
import core.stream_manager as stream_manager
from core.stream_manager import QueuePlayer
from services.ffmpeg_service import FFmpegService, _log_process_stderr


def test_ffmpeg_service_publisher_lifecycle():
    service = FFmpegService(icecast_url="icecast://source:hackme@127.0.0.1:8000/mpv.ogg")
    pub = service.ensure_publisher()
    try:
        assert service.is_publisher_alive()
        # Test writing audio chunk to pump queue
        res = service.write_pcm(b"\x00" * 4096)
        assert res is True
    finally:
        service.stop_publisher()
        assert not service.is_publisher_alive()


def test_ffmpeg_service_pump_continuous_silence():
    # Test that when no audio is sent, the pump continually feeds silence to the publisher
    service = FFmpegService(icecast_url="icecast://source:hackme@127.0.0.1:8000/mpv.ogg")
    service.ensure_publisher()
    try:
        assert service.is_publisher_alive()
        # Wait 1.5 seconds with empty audio queue: pump must feed silence without dying
        time.sleep(1.5)
        assert service.is_publisher_alive()
    finally:
        service.stop_publisher()


def test_ffmpeg_filter_runs_on_decoder_not_publisher(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append(cmd)
        return MagicMock()

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService()
    service._ensure_pump_thread = lambda: None

    service.start_publisher()
    service.start_decoder("/music/song.mp3")

    publisher_cmd, decoder_cmd = popen_calls
    audio_filter = ["-af", "dynaudnorm=f=500:g=5:m=10:p=0.95"]
    assert not any(
        publisher_cmd[index:index + len(audio_filter)] == audio_filter
        for index in range(len(publisher_cmd))
    )
    assert any(
        decoder_cmd[index:index + len(audio_filter)] == audio_filter
        for index in range(len(decoder_cmd))
    )


def test_decoder_prefers_resolved_direct_url(monkeypatch):
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService()

    decoder = service.start_decoder({
        "title": "Direct stream",
        "url": "https://rr.example.test/videoplayback?id=abc",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "stream_headers": {
            "Referer": "https://www.youtube.com/",
            "User-Agent": "Musicify-Test",
        },
    })

    assert isinstance(decoder, MagicMock)
    assert len(popen_calls) == 1
    ffmpeg_cmd, ffmpeg_kwargs = popen_calls[0]
    assert ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1] == (
        "https://rr.example.test/videoplayback?id=abc"
    )
    assert "-headers" not in ffmpeg_cmd
    assert ffmpeg_cmd[ffmpeg_cmd.index("-referer") + 1] == (
        "https://www.youtube.com/"
    )
    assert ffmpeg_cmd[ffmpeg_cmd.index("-user_agent") + 1] == "Musicify-Test"
    assert "-reconnect" in ffmpeg_cmd
    assert "youtube:player_client" not in " ".join(ffmpeg_cmd)


def test_decoder_pipes_yt_dlp_when_no_direct_url(monkeypatch):
    monkeypatch.setattr(
        "services.ffmpeg_service.settings.YOUTUBE_PLAYER_CLIENT",
        None,
    )
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        process = MagicMock()
        process.stdout = MagicMock()
        return process

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService()

    decoder = service.start_decoder({
        "title": "Playlist track",
        "url": None,
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
    })

    assert isinstance(decoder, MagicMock)
    assert len(popen_calls) == 2
    yt_dlp_cmd = popen_calls[0][0]
    ffmpeg_cmd = popen_calls[1][0]
    assert "yt_dlp" in yt_dlp_cmd
    assert "--extractor-args" not in yt_dlp_cmd
    assert ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1] == "pipe:0"


def test_decoder_passes_configured_yt_dlp_player_client(monkeypatch):
    monkeypatch.setattr(
        "services.ffmpeg_service.settings.YOUTUBE_PLAYER_CLIENT",
        "android",
    )
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        process = MagicMock()
        process.stdout = MagicMock()
        return process

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService()

    service.start_decoder({
        "title": "Android client track",
        "url": None,
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
    })

    yt_dlp_cmd = popen_calls[0][0]
    extractor_args_index = yt_dlp_cmd.index("--extractor-args")
    assert yt_dlp_cmd[extractor_args_index + 1] == (
        "youtube:player_client=android"
    )


def test_decoder_uses_downloaded_cache_file(monkeypatch, tmp_path):
    cached_song = tmp_path / "timestamp-Cached track.webm"
    cached_song.write_bytes(b"cached")
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService(cache_dir=tmp_path)

    service.start_decoder({
        "title": "Cached track",
        "cache_path": str(cached_song),
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
    })

    ffmpeg_cmd = popen_calls[0][0]
    assert ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1] == str(cached_song)


def test_decoder_falls_back_when_cache_file_is_missing(monkeypatch, tmp_path):
    popen_calls = []

    def fake_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs))
        return MagicMock()

    monkeypatch.setattr("services.ffmpeg_service.subprocess.Popen", fake_popen)
    service = FFmpegService(cache_dir=tmp_path)

    service.start_decoder({
        "title": "Stale cache",
        "cache_path": str(tmp_path / "missing.webm"),
        "url": "https://rr.example.test/videoplayback?id=abc",
        "stream_headers": {
            "Referer": "https://www.youtube.com/",
            "User-Agent": "Musicify-Test",
        },
    })

    ffmpeg_cmd = popen_calls[0][0]
    assert ffmpeg_cmd[ffmpeg_cmd.index("-i") + 1] == (
        "https://rr.example.test/videoplayback?id=abc"
    )
    assert "-headers" not in ffmpeg_cmd


def test_precache_song_uses_path_glob_for_cache_files(monkeypatch, tmp_path):
    cached_song = tmp_path / "timestamp-Cached track.webm"

    def fake_run(command, **kwargs):
        cached_song.write_bytes(b"cached")
        return SimpleNamespace(
            returncode=0,
            stdout=f"{cached_song}\n",
            stderr="",
        )

    monkeypatch.setattr("services.ffmpeg_service.subprocess.run", fake_run)
    monkeypatch.setattr(
        FFmpegService,
        "get_media_duration",
        lambda self, path: 200.0,
    )
    service = FFmpegService(cache_dir=tmp_path)

    result = service.precache_song({
        "title": "Cached track",
        "videoId": "abcdefghijk",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
    })

    assert result["path"] == str(cached_song)
    assert result["duration"] == 200.0


def test_queue_player_marks_failed_precache_and_moves_it_to_bottom(monkeypatch, tmp_path):
    class MockFFmpeg:
        def precache_song(self, song):
            raise RuntimeError("video unavailable")

    player = QueuePlayer(MockFFmpeg())
    first = {"title": "First", "videoId": "abcdefghijk"}
    second = {"title": "Second", "videoId": "12345678901"}
    player.queue.extend([first, second])

    player._precache_worker(first)

    assert player.queue == [second, first]
    assert first["_precache_status"] == "error"


def test_queue_player_stops_publisher_after_idle_timeout(monkeypatch):
    monkeypatch.setattr(stream_manager, "EMPTY_QUEUE_PUBLISHER_TIMEOUT", 0.05)
    publisher_stopped = threading.Event()
    stop_calls = []

    class MockFFmpegService:
        def ensure_publisher(self):
            pass

        def stop_publisher(self):
            stop_calls.append(True)
            publisher_stopped.set()

    player = QueuePlayer(MockFFmpegService())
    player.start()
    try:
        assert publisher_stopped.wait(timeout=1)
        assert len(stop_calls) == 1
    finally:
        player.stop()


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

        def clear_buffer(self):
            pass

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


def test_queue_player_publishes_song_only_after_audio_is_accepted():
    decoder_reading = threading.Event()
    allow_audio = threading.Event()
    audio_accepted = threading.Event()
    finish_track = threading.Event()

    class MockFFmpegService:
        def ensure_publisher(self):
            pass

        def write_pcm(self, data):
            audio_accepted.set()
            return True

        def start_decoder(self, song):
            decoder = MagicMock()
            decoder.stdout = MagicMock()

            def read(_size):
                decoder_reading.set()
                if not allow_audio.wait(timeout=2):
                    return b""
                if not audio_accepted.is_set():
                    return b"audio"
                finish_track.wait(timeout=2)
                return b""

            decoder.stdout.read.side_effect = read
            return decoder

        def kill_process(self, _process):
            pass

    player = QueuePlayer(MockFFmpegService(), autoplay_enabled=False)
    player.start()
    player.enqueue({"title": "Delayed song", "url": "/music/delayed.mp3"})

    try:
        assert decoder_reading.wait(timeout=3)
        assert player.status()["current_song"] is None

        allow_audio.set()
        assert audio_accepted.wait(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current_song = player.status()["current_song"]
            if current_song and current_song.get("title") == "Delayed song":
                break
            time.sleep(0.01)
        assert player.status()["current_song"]["title"] == "Delayed song"
    finally:
        finish_track.set()
        player.stop()


def test_queue_player_logs_decoder_exit_code(caplog):
    decoder_finished = threading.Event()

    class MockFFmpegService:
        def ensure_publisher(self):
            pass

        def start_decoder(self, _song):
            decoder = MagicMock()
            decoder.stdout = MagicMock()
            decoder.stdout.read.return_value = b""
            decoder.poll.return_value = 1
            return decoder

        def kill_process(self, _process):
            decoder_finished.set()

    player = QueuePlayer(MockFFmpegService(), autoplay_enabled=False)
    with caplog.at_level(logging.ERROR, logger="core.stream_manager"):
        player.start()
        player.enqueue({"title": "Broken song", "url": "/music/broken.mp3"})
        try:
            assert decoder_finished.wait(timeout=3)
        finally:
            player.stop()

    assert "Decoder exited with code 1 for 'Broken song'." in caplog.text


def test_decoder_stderr_is_logged_with_stream_url_redacted(caplog):
    signed_url = "https://example.test/audio?signature=secret"
    process = SimpleNamespace(
        stderr=io.BytesIO(f"Could not open {signed_url}\n".encode())
    )

    with caplog.at_level(logging.ERROR, logger="services.ffmpeg_service"):
        _log_process_stderr(process, "FFmpeg decoder for Broken song", (signed_url,))
        deadline = time.monotonic() + 1
        while "Could not open" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)

    assert "Could not open <stream-url>" in caplog.text
    assert "signature=secret" not in caplog.text
