import copy
import logging
import os
import re
import sys


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

    FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"
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


class WerkzeugAccessLogHandler(logging.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)
        self.stats_by_ip = {}
        self._stats_line_active = False

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
                    self.stream.write(f"\r{self.format(summary_record)}")
                    self.flush()
                    self._stats_line_active = True
                    return

                formatted_message = (
                    f'{details["ip"]} "{details["method"]} {details["path"]}" '
                    f'{details["status"]} {details["size"]}'
                )
                record = copy.copy(record)
                record.msg = formatted_message
                record.args = ()

            if self._stats_line_active:
                self.stream.write("\n")
                self._stats_line_active = False
            self.stream.write(f"{self.format(record)}\n")
            self.flush()
        except Exception:
            self.handleError(record)


def setup_logging():
    if os.name == "nt":
        os.system("")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter())
    root_logger.addHandler(console_handler)

    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.handlers.clear()
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.propagate = False
    access_log_handler = WerkzeugAccessLogHandler(sys.stdout)
    access_log_handler.setFormatter(ColorFormatter())
    werkzeug_logger.addHandler(access_log_handler)
