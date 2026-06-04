# services/ffmpeg_service.py

import subprocess
import os
import signal

ICECAST_URL = "icecast://source:hackme@127.0.0.1:8000/mpv.ogg"


class FFmpegService:

    def __init__(self):

        self.process = None

    def stop(self):

        if not self.process:
            return

        try:
            os.killpg(
                os.getpgid(self.process.pid),
                signal.SIGTERM
            )

            self.process.wait(timeout=2)

        except:
            pass

        self.process = None

    def stream_file(self, file_path):

        self.stop()

        cmd = [
            "ffmpeg",
            "-re",
            "-i", file_path,
            "-vn",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-f", "mp3",
            ICECAST_URL
        ]

        self.process = subprocess.Popen(
            cmd,
            preexec_fn=os.setsid
        )

        return self.process

    def stream_url(self, stream_url):

        self.stop()

        cmd = [
            "ffmpeg",
            "-re",
            "-i", stream_url,
            "-vn",
            "-c:a", "libvorbis",
            "-q:a", "5",
            "-f", "ogg",
            ICECAST_URL
        ]

        self.process = subprocess.Popen(
            cmd,
            preexec_fn=os.setsid
        )

        return self.process