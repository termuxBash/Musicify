"""#services.ffmpeg_service.py - FFmpeg process management for streaming to Icecast
This module provides a clean interface for managing a persistent FFmpeg publisher
that streams audio continuously to an Icecast server, as well as decoder processes
that feed audio from local files or YouTube streams into the publisher.
An independent audio pump thread guarantees a continuous, unbroken stream of audio
(or digital silence when idle) to prevent Icecast socket timeouts and client disconnects.
"""

import subprocess
import os
import signal
import sys
import threading
import time
import queue
import logging
import io

logger = logging.getLogger(__name__)

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"
CHUNK_SIZE = 17640
CHUNK_DURATION = 0.1
SILENCE_CHUNK = bytes(CHUNK_SIZE)


def _log_process_stderr(process, label, redacted_values=()):
    stderr = getattr(process, "stderr", None)
    if not isinstance(stderr, io.IOBase):
        return

    def drain():
        try:
            for raw_line in stderr:
                line = raw_line.decode(errors="replace") if isinstance(raw_line, bytes) else raw_line
                for value in redacted_values:
                    if value:
                        line = line.replace(value, "<stream-url>")
                line = line.strip()
                if line:
                    logger.error("%s: %s", label, line)
        except OSError:
            logger.exception("Failed reading stderr from %s.", label)

    threading.Thread(target=drain, daemon=True).start()


class FFmpegService:
    def __init__(self, icecast_url=ICECAST_URL):
        self.icecast_url = icecast_url
        self.publisher_process = None
        self._publisher_lock = threading.RLock()
        self._last_publisher_start = 0.0

        # Dedicated audio buffer queue for decoupled, continuous real-time pumping
        self._audio_queue = queue.Queue(maxsize=30)  # holds ~0.7s of audio buffer
        self._pump_thread = None
        self._pump_stop_event = threading.Event()

    def start_publisher(self):
        """Starts a persistent FFmpeg publisher process streaming to Icecast and ensures the pump is running."""
        with self._publisher_lock:
            self._stop_publisher_process()

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "s16le",
                "-ar", "44100",
                "-ac", "2",
                "-i", "pipe:0",
                "-vn",
                "-threads", "1",
                "-c:a", "libvorbis",
                "-qscale:a", "3",
                "-ac", "2",
                "-ar", "44100",
                "-content_type", "application/ogg",
                "-f", "ogg",
                self.icecast_url
            ]

            logger.info("Starting persistent FFmpeg publisher to Icecast (%s)", self.icecast_url)
            self.publisher_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
            self._last_publisher_start = time.monotonic()

            self._ensure_pump_thread()
            return self.publisher_process

    def _stop_publisher_process(self):
        if self.publisher_process:
            self.kill_process(self.publisher_process)
            self.publisher_process = None

    def _ensure_pump_thread(self):
        if self._pump_thread is None or not self._pump_thread.is_alive():
            self._pump_stop_event.clear()
            self._pump_thread = threading.Thread(target=self._audio_pump_worker, daemon=True)
            self._pump_thread.start()

    def _audio_pump_worker(self):
        """
        Independent audio pump thread.
        Guarantees that audio data (or digital silence if buffer is empty)
        is fed to the FFmpeg publisher at a continuous 1.0x real-time pace.
        This prevents Icecast from ever encountering a socket timeout.
        """
        logger.info("Audio pump thread started.")
        next_time = time.monotonic()

        while not self._pump_stop_event.is_set():
            # Get next audio chunk from queue or fall back to pure silence
            try:
                chunk = self._audio_queue.get_nowait()
            except queue.Empty:
                chunk = SILENCE_CHUNK

            with self._publisher_lock:
                if self.publisher_process is None or self.publisher_process.poll() is not None:
                    # Publisher died (e.g. Icecast restart), attempt recovery after cooldown
                    if time.monotonic() - self._last_publisher_start >= 1.0:
                        try:
                            self.start_publisher()
                        except Exception as e:
                            logger.error("Failed to restart publisher in pump: %s", e)

                if self.publisher_process and self.publisher_process.stdin:
                    try:
                        self.publisher_process.stdin.write(chunk)
                        self.publisher_process.stdin.flush()
                    except (BrokenPipeError, OSError) as e:
                        logger.warning("Pump write error: %s. Publisher will be reconnected.", e)
                        self._stop_publisher_process()

            # Pace to exact real-time duration
            next_time += CHUNK_DURATION
            now = time.monotonic()
            sleep_time = next_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            elif sleep_time < -0.5:
                # If fell behind by more than 0.5s, reset baseline to current clock
                next_time = now

        logger.info("Audio pump thread stopped.")

    def ensure_publisher(self):
        """Ensures that the persistent FFmpeg publisher process and pump thread are alive."""
        with self._publisher_lock:
            if self.publisher_process is None or self.publisher_process.poll() is not None:
                if time.monotonic() - self._last_publisher_start >= 1.0:
                    return self.start_publisher()
            self._ensure_pump_thread()
            return self.publisher_process

    def is_publisher_alive(self):
        with self._publisher_lock:
            return self.publisher_process is not None and self.publisher_process.poll() is None

    def write_pcm(self, data: bytes, timeout: float = 2.0) -> bool:
        """Pushes raw PCM audio bytes to the pump's audio queue."""
        self.ensure_publisher()
        try:
            self._audio_queue.put(data, timeout=timeout)
            return True
        except queue.Full:
            logger.warning("Audio queue full; dropped PCM chunk.")
            return False

    def clear_buffer(self):
        """Purges any buffered audio (e.g. when skipping tracks)."""
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def stop_publisher(self):
        """Safely stops the persistent publisher and pump thread."""
        self._pump_stop_event.set()
        self.clear_buffer()
        with self._publisher_lock:
            self._stop_publisher_process()

    def start_decoder(self, target):
        """
        Starts an FFmpeg process (and optional yt-dlp source) that decodes
        a local file or stream URL into raw PCM (s16le, 44100Hz, stereo) on stdout.
        """
        source_process = None
        stream_headers = target.get("stream_headers", {}) if isinstance(target, dict) else {}
        target_url = target.get("url") if isinstance(target, dict) else target
        source_url = target.get("source_url") if isinstance(target, dict) else None
        track_title = target.get("title", "Unknown") if isinstance(target, dict) else "stream"

        if source_url:
            source_process = subprocess.Popen(
                [
                    sys.executable, "-m", "yt_dlp",
                    "--quiet", "--no-warnings", "--no-check-certificates",
                    "--remote-components", "ejs:github",
                    "--extractor-args", "youtube:player_client=web_embedded",
                    "--format", "bestaudio",
                    "--output", "-", source_url
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )
            _log_process_stderr(
                source_process,
                f"yt-dlp source for {track_title}",
                (source_url,),
            )
            target_url = "pipe:0"

        header_value = "".join(
            f"{key}: {value}\r\n" for key, value in stream_headers.items()
        )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            *( ["-headers", header_value] if header_value and not source_process else [] ),
            "-i", target_url,
            "-vn",
            "-af", "dynaudnorm=f=500:g=5:m=10:p=0.95",
            "-threads", "1",
            "-f", "s16le",
            "-ar", "44100",
            "-ac", "2",
            "pipe:1"
        ]

        decoder = subprocess.Popen(
            cmd,
            stdin=source_process.stdout if source_process else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid
        )
        _log_process_stderr(
            decoder,
            f"FFmpeg decoder for {track_title}",
            (target_url,),
        )

        if source_process:
            source_process.stdout.close()
            decoder.source_process = source_process

        return decoder

    def start_stream(self, target):
        """Backward-compatibility alias for start_decoder."""
        return self.start_decoder(target)

    def kill_process(self, process):
        """Safely stops a running subprocess and its child processes."""
        if not process:
            return
        try:
            source_process = getattr(process, "source_process", None)
            if source_process:
                try:
                    os.killpg(os.getpgid(source_process.pid), signal.SIGTERM)
                    source_process.wait(timeout=1)
                except Exception:
                    try:
                        source_process.kill()
                    except Exception:
                        pass
            if hasattr(process, "pid") and process.poll() is None:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=1)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
