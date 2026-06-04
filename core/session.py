# core/session.py

class StreamSession:

    def __init__(self, owner, source_type, source):

        self.owner = owner

        self.source_type = source_type
        # youtube | file

        self.source = source

        self.process = None

        self.started_at = None