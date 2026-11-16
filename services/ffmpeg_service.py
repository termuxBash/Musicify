"""#services.ffmpeg_service.py - FFmpeg process management for streaming to Icecast
This module provides a clean interface for managing a persistent FFmpeg publisher
that streams audio continuously to an Icecast server, as well as decoder processes
that feed audio from local files or YouTube streams into the publisher.
"""

import subprocess
import os
import signal
import sys
import threading
import time
import logging

logger = logging.getLogger(__name__)

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"


class FFmpegService:
    def __init__(self, icecast_url=ICECAST_URL):
        self.icecast_url = icecast_url
        self.publisher_process = None
        self._publisher_lock = threading.RLock()
        self._last_publisher_start = 0.0

    def start_publisher(self):
        """Starts a persistent FFmpeg publisher process streaming to Icecast."""
        with self._publisher_lock:
            if self.publisher_process:
                self.kill_process(self.publisher_process)
                self.publisher_process = None

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-re",
                "-f", "s16le",
                "-ar", "44100",
                "-ac", "2",
                "-i", "pipe:0",
                "-vn",
                "-af", "dynaudnorm=f=500:g=5:m=10:p=0.95",
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
            return self.publisher_process

    def ensure_publisher(self):
        """Ensures that the persistent FFmpeg publisher process is alive."""
        with self._publisher_lock:
            if self.publisher_process is None or self.publisher_process.poll() is not None:
                # 1 second cooldown to avoid tight restart loops if Icecast is down
                if time.monotonic() - self._last_publisher_start >= 1.0:
                    return self.start_publisher()
            return self.publisher_process

    def is_publisher_alive(self):
        with self._publisher_lock:
            return self.publisher_process is not None and self.publisher_process.poll() is None

    def write_pcm(self, data: bytes) -> bool:
        """Writes raw PCM audio bytes to the persistent publisher."""
        self.ensure_publisher()
        try:
            if self.publisher_process and self.publisher_process.stdin:
                self.publisher_process.stdin.write(data)
                self.publisher_process.stdin.flush()
                return True
        except (BrokenPipeError, OSError) as e:
            logger.warning("Error writing to FFmpeg publisher: %s. Reconnecting...", e)
            self.start_publisher()
            try:
                if self.publisher_process and self.publisher_process.stdin:
                    self.publisher_process.stdin.write(data)
                    self.publisher_process.stdin.flush()
                    return True
            except Exception as retry_err:
                logger.error("Failed to write to restarted publisher: %s", retry_err)
        return False

    def stop_publisher(self):
        """Safely stops the persistent publisher process."""
        with self._publisher_lock:
            if self.publisher_process:
                logger.info("Stopping persistent FFmpeg publisher.")
                self.kill_process(self.publisher_process)
                self.publisher_process = None

    def start_decoder(self, target):
        """
        Starts an FFmpeg process (and optional yt-dlp source) that decodes
        a local file or stream URL into raw PCM (s16le, 44100Hz, stereo) on stdout.
        """
        source_process = None
        stream_headers = target.get("stream_headers", {}) if isinstance(target, dict) else {}
        target_url = target.get("url") if isinstance(target, dict) else target
        source_url = target.get("source_url") if isinstance(target, dict) else None

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
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
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
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
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
