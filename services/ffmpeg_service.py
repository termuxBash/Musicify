"""#services.ffmpeg_service.py - FFmpeg process management for streaming to Icecast
This module provides a clean interface for starting and stopping FFmpeg processes that stream audio to an Icecast server. It abstracts away the complexities of subprocess management, 
allowing the rest of the application to simply call start_stream with a file path or URL, and handles the lifecycle of the FFmpeg process internally. It also includes a StreamQueueManager class that can manage a queue of tracks to be streamed sequentially, with support for skipping and stopping playback.
"""

import subprocess
import os
import signal
import sys

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"

class FFmpegService:
    def start_stream(self, target):
        """
        Starts an FFmpeg process for either a local file path or a web URL.
        """
        # Common flags for audio-only streaming to Icecast
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
                stderr=subprocess.DEVNULL
            )
            target_url = "-"
        header_value = "".join(
            f"{key}: {value}\r\n" for key, value in stream_headers.items()
        )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-re",
            *( ["-headers", header_value] if header_value and not source_process else [] ),
            "-i", target_url,
            "-vn",
            "-c:a", "libvorbis",
            "-ar", "44100",
            "-ac", "2",
            "-content_type", "application/ogg",
            "-f", "ogg",
            ICECAST_URL
        ]

        process = subprocess.Popen(
            cmd,
            stdin=source_process.stdout if source_process else None,
            preexec_fn=os.setsid
        )
        if source_process:
            source_process.stdout.close()
            process.source_process = source_process
        return process

    def kill_process(self, process):
        """Safely stops a running FFmpeg instance."""
        if not process:
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            source_process = getattr(process, "source_process", None)
            if source_process:
                source_process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass