# core/stream_manager.py

from core.session import StreamSession


class StreamManager:

    def __init__(
        self,
        ffmpeg_service,
        yt_service
    ):

        self.ffmpeg = ffmpeg_service

        self.yt = yt_service

        self.current_session = None