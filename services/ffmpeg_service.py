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
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from core import settings

logger = logging.getLogger(__name__)

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"
CHUNK_SIZE = 17640
CHUNK_DURATION = 0.1
SILENCE_CHUNK = bytes(CHUNK_SIZE)

# Transient mid-stream socket/TLS/demuxer resets (especially at stream end)
IGNORED_DECODER_STDERR_PATTERNS = (
    "[tls @",
    "Error in the pull function",
    "IO error: Connection reset by peer",
    "The specified session has been invalidated",
    "Last message repeated",
    "Error during demuxing: Input/output error",
    "Error retrieving a packet from demuxer: Input/output error",
)


def _should_ignore_decoder_error(line: str, is_decoder: bool) -> bool:
    if not is_decoder:
        return False
    return any(pattern in line for pattern in IGNORED_DECODER_STDERR_PATTERNS)


def _log_process_stderr(process, label, redacted_values=(), is_decoder=False):
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
                    if _should_ignore_decoder_error(line, is_decoder):
                        logger.debug("%s (ignored mid-stream reset): %s", label, line)
                    else:
                        logger.error("%s: %s", label, line)
        except OSError:
            logger.exception("Failed reading stderr from %s.", label)

    threading.Thread(target=drain, daemon=True).start()


class FFmpegService:
    def __init__(self, icecast_url=ICECAST_URL, cache_dir=None):
        self.icecast_url = icecast_url
        self.publisher_process = None
        self._publisher_lock = threading.RLock()
        self._last_publisher_start = 0.0
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir else self._default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Dedicated audio buffer queue for decoupled, continuous real-time pumping
        self._audio_queue = queue.Queue(maxsize=30)  # holds ~0.7s of audio buffer
        self._pump_thread = None
        self._pump_stop_event = threading.Event()

    def _default_cache_dir(self):
        for configured_dir in getattr(settings, "DOWNLOAD_DIRS", []):
            if not isinstance(configured_dir, str) or not configured_dir.strip():
                continue
            candidate = Path(configured_dir.strip()).expanduser()
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return candidate
            except OSError:
                continue

        fallback = Path.home() / "Downloads" / "ytdlp"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def precache_song(self, song):
        """Download a YouTube track with yt-dlp and return cache metadata."""
        source_url = song.get("source_url") if isinstance(song, dict) else None
        video_id = song.get("videoId") if isinstance(song, dict) else None
        if not isinstance(video_id, str) or not video_id:
            if isinstance(source_url, str) and source_url:
                parsed_url = urlparse(source_url)
                if parsed_url.hostname in {"youtu.be", "www.youtu.be"}:
                    video_id = parsed_url.path.strip("/").split("/")[0] or None
                else:
                    video_id = (parse_qs(parsed_url.query).get("v") or [None])[0]
        if not source_url and video_id:
            source_url = f"https://www.youtube.com/watch?v={video_id}"
        if not isinstance(source_url, str) or not source_url:
            raise ValueError("Song has no YouTube source URL")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(video_id, str) and video_id:
            for cached_path in self.cache_dir.glob("*"):
                if (
                    cached_path.is_file()
                    and cached_path.stat().st_size > 0
                    and f"__{video_id}__" in cached_path.name
                ):
                    duration = self.get_media_duration(str(cached_path.resolve()))
                    return {
                        "path": str(cached_path.resolve()),
                        "duration": duration,
                        "downloaded_at": int(cached_path.stat().st_mtime),
                        "reused": True,
                    }

        before = {path.resolve() for path in self.cache_dir.glob("*") if path.is_file()}
        timestamp = int(time.time())
        title = song.get("title") if isinstance(song, dict) else None
        title = str(title).strip() if isinstance(title, str) and title.strip() else "Unknown"
        video_key = video_id if isinstance(video_id, str) and video_id else "unknown"
        output_template = str(
            self.cache_dir / f"{timestamp}__{video_key}__%(title)s.%(ext)s"
        )
        command = [
            sys.executable, "-m", "yt_dlp",
            "--quiet", "--no-warnings", "--no-check-certificates",
            "--remote-components", "ejs:github",
            "--no-part", "--no-playlist",
            "--format", "bestaudio",
            "--restrict-filenames",
            "--paths", str(self.cache_dir),
            "--output", output_template,
            "--print", "after_move:filepath",
        ]
        if settings.YOUTUBE_PLAYER_CLIENT:
            command.extend([
                "--extractor-args",
                f"youtube:player_client={settings.YOUTUBE_PLAYER_CLIENT}",
            ])
        command.append(source_url)

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30 * 60,
        )
        if result.returncode != 0:
            error = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error or f"yt-dlp exited with code {result.returncode}")

        downloaded = None
        for line in reversed((result.stdout or "").splitlines()):
            candidate = Path(line.strip())
            if candidate.is_file():
                downloaded = candidate.resolve()
                break

        if downloaded is None:
            after = [
                path.resolve()
                for path in self.cache_dir.glob("*")
                if path.is_file() and path.resolve() not in before
            ]
            if after:
                downloaded = max(after, key=lambda path: path.stat().st_mtime)
        if downloaded is None:
            raise RuntimeError("yt-dlp completed without producing a cache file")

        duration = self.get_media_duration(str(downloaded))
        return {
            "path": str(downloaded),
            "duration": duration,
            "downloaded_at": timestamp,
        }

    def find_cached_song(self, target):
        """Find an existing cache file matching a target's YouTube video ID."""
        if not isinstance(target, dict):
            return None

        video_id = target.get("videoId")
        source_url = target.get("source_url")
        if not isinstance(video_id, str) or not video_id:
            candidate_url = (
                source_url
                if isinstance(source_url, str) and source_url
                else target.get("url")
            )
            if isinstance(candidate_url, str) and candidate_url:
                parsed_url = urlparse(candidate_url)
                if parsed_url.hostname in {"youtu.be", "www.youtu.be"}:
                    video_id = parsed_url.path.strip("/").split("/")[0] or None
                elif parsed_url.hostname and "youtube" in parsed_url.hostname:
                    video_id = (parse_qs(parsed_url.query).get("v") or [None])[0]

        if not isinstance(video_id, str) or not video_id:
            return None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for cached_path in self.cache_dir.glob("*"):
            if (
                cached_path.is_file()
                and cached_path.stat().st_size > 0
                and f"__{video_id}__" in cached_path.name
            ):
                return str(cached_path.resolve())
        return None

    def get_media_duration(self, target):
        if not isinstance(target, str) or not target:
            return None
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    target,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                value = float((result.stdout or "").strip())
                return value if value > 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return None

    def clear_cached_song(self, path):
        if not isinstance(path, str) or not path:
            return
        try:
            cached_path = Path(path)
            if cached_path.is_file() and cached_path.parent.resolve() == self.cache_dir.resolve():
                cached_path.unlink()
        except OSError as error:
            logger.warning("Could not delete cached song %s: %s", path, error)

    def cleanup_cache(self, keep_paths=(), max_age_seconds=300):
        keep = {
            str(Path(path).resolve())
            for path in keep_paths
            if isinstance(path, str) and path
        }
        cutoff = time.time() - max_age_seconds
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            for path in self.cache_dir.glob("*"):
                if not path.is_file():
                    continue
                try:
                    if str(path.resolve()) not in keep and path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError as error:
                    logger.warning("Could not clean cached song %s: %s", path, error)
        except OSError as error:
            logger.warning("Could not clean cache directory %s: %s", self.cache_dir, error)

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
        cache_path = target.get("cache_path") if isinstance(target, dict) else None
        if (
            not isinstance(cache_path, str)
            or not os.path.isfile(cache_path)
            or os.path.getsize(cache_path) <= 0
        ):
            cached_path = self.find_cached_song(target)
            if cached_path:
                cache_path = cached_path
                if isinstance(target, dict):
                    target["cache_path"] = cached_path
                    logger.info(
                        "Using existing cache for '%s': %s",
                        target.get("title", "Unknown"),
                        cached_path,
                    )
        valid_cache_path = (
            isinstance(cache_path, str)
            and os.path.isfile(cache_path)
            and os.path.getsize(cache_path) > 0
        )
        target_url = (
            cache_path
            if valid_cache_path
            else target.get("url")
        ) if isinstance(target, dict) else target
        source_url = target.get("source_url") if isinstance(target, dict) else None
        track_title = target.get("title", "Unknown") if isinstance(target, dict) else "stream"

        if isinstance(cache_path, str) and not valid_cache_path:
            logger.warning(
                "Precached file is unavailable for '%s'; using the resolved stream.",
                track_title,
            )

        # Prefer yt-dlp only for URL resolution. Once a direct media URL is
        # available, let FFmpeg handle downloading and decoding itself.
        if not target_url and source_url:
            yt_dlp_cmd = [
                sys.executable, "-m", "yt_dlp",
                "--quiet", "--no-warnings", "--no-check-certificates",
                "--remote-components", "ejs:github",
            ]
            if settings.YOUTUBE_PLAYER_CLIENT:
                yt_dlp_cmd.extend([
                    "--extractor-args",
                    f"youtube:player_client={settings.YOUTUBE_PLAYER_CLIENT}",
                ])
            yt_dlp_cmd.extend([
                "--format", "bestaudio",
                "--output", "-", source_url,
            ])
            source_process = subprocess.Popen(
                yt_dlp_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )
            _log_process_stderr(
                source_process,
                f"yt-dlp source for {track_title}",
                (source_url,),
                is_decoder=False,
            )
            target_url = "pipe:0"

        is_direct_http_url = (
            isinstance(target_url, str)
            and target_url.startswith(("http://", "https://"))
        )
        header_args = []
        if is_direct_http_url:
            user_agent = stream_headers.get("User-Agent") or stream_headers.get("user-agent")
            referer = stream_headers.get("Referer") or stream_headers.get("referer")
            if user_agent:
                header_args.extend(["-user_agent", str(user_agent)])
            if referer:
                header_args.extend(["-referer", str(referer)])

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            *header_args,
            *(
                [
                    "-reconnect", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                ]
                if is_direct_http_url
                else []
            ),
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
            is_decoder=True,
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