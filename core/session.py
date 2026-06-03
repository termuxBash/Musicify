class StreamSession:

    def __init__(self):

        self.owner = None
        self.track = None

        self.ffmpeg_process = None

        self.helper_processes = []

        self.started_at = None