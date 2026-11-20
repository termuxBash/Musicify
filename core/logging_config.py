import copy
import logging
import os
import re
import sys
import threading

_ACCESS_LOG_PATTERN = re.compile(
    r'^(?P<ip>\S+) - - \[[^\]]+\] "(?P<method>\S+) (?P<path>.*?) '
    r'HTTP/\d(?:\.\d)?" (?P<status>\d{3}) (?P<size>\S+)$'
)


class ColorFormatter(logging.Formatter):
    GREY = "\x1b[38;20m"
    BLUE = "\x1b[34;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"

    FORMAT = "%(asctime)s %(levelname)s: %%%(name)s : %(message)s"
    DATE_FORMAT = "%d/%m/%y-%H:%M:%S"

    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: BLUE,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def format(self, record):
        log_color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        formatter = logging.Formatter(
            f"{log_color}{self.FORMAT}{self.RESET}",
            datefmt=self.DATE_FORMAT,
        )
        return formatter.format(record)


class StreamLineState:
    """Tracks whether the terminal line is currently being overwritten with \\r."""

    def __init__(self):
        self.lock = threading.Lock()
        self.stats_line_active = False

    def write_stats_line(self, stream, text):
        with self.lock:
            # \r returns to column 0; \x1b[K clears any leftover chars from a longer line
            stream.write(f"\r{text}\x1b[K")
            stream.flush()
            self.stats_line_active = True


class CoordinatedStreamHandler(logging.StreamHandler):
    """Ensures any active \\r stats line is terminated before normal logs print."""

    def __init__(self, stream=None, line_state=None):
        super().__init__(stream)
        self.line_state = line_state or StreamLineState()

    def emit(self, record):
        try:
            msg = self.format(record)
            with self.line_state.lock:
                if self.line_state.stats_line_active:
                    self.stream.write("\n")
                    self.line_state.stats_line_active = False
                self.stream.write(f"{msg}\n")
                self.flush()
        except Exception:
            self.handleError(record)


class WerkzeugAccessLogHandler(CoordinatedStreamHandler):
    def __init__(self, stream=None, line_state=None):
        super().__init__(stream, line_state)
        self.stats_by_ip = {}

    def emit(self, record):
        try:
            message = record.getMessage()
            match = _ACCESS_LOG_PATTERN.match(message)
            if match:
                details = match.groupdict()
                if details["path"].split("?", 1)[0] == "/stats":
                    ip = details["ip"]
                    self.stats_by_ip[ip] = self.stats_by_ip.get(ip, 0) + 1
                    summary = "stats -> " + " | ".join(
                        f"{client_ip}: {count}"
                        for client_ip, count in self.stats_by_ip.items()
                    )
                    summary_record = copy.copy(record)
                    summary_record.msg = summary
                    summary_record.args = ()
                    self.line_state.write_stats_line(self.stream, self.format(summary_record))
                    return

                formatted_message = (
                    f'{details["ip"]} "{details["method"]} {details["path"]}" '
                    f'{details["status"]} {details["size"]}'
                )
                record = copy.copy(record)
                record.msg = formatted_message
                record.args = ()

            super().emit(record)
        except Exception:
            self.handleError(record)


def setup_logging():
    if os.name == "nt":
        os.system("")

    line_state = StreamLineState()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console_handler = CoordinatedStreamHandler(sys.stdout, line_state=line_state)
    console_handler.setFormatter(ColorFormatter())
    root_logger.addHandler(console_handler)

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.handlers.clear()
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.propagate = False
    access_log_handler = WerkzeugAccessLogHandler(sys.stdout, line_state=line_state)
    access_log_handler.setFormatter(ColorFormatter())
    werkzeug_logger.addHandler(access_log_handler)