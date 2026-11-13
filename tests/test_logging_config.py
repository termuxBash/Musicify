import io
import logging
import re
import unittest

from core.logging_config import ColorFormatter, WerkzeugAccessLogHandler


class LoggingConfigTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = WerkzeugAccessLogHandler(self.stream)
        self.handler.setFormatter(ColorFormatter())

    def emit_access_log(self, message):
        record = logging.LogRecord(
            "werkzeug",
            logging.INFO,
            "werkzeug.py",
            1,
            message,
            (),
            None,
        )
        self.handler.emit(record)

    def test_stats_access_logs_update_single_counter_line_per_ip(self):
        self.emit_access_log(
            '127.0.0.1 - - [13/Nov/2026 11:15:03] "GET /stats HTTP/1.1" 200 -'
        )
        self.emit_access_log(
            '192.168.29.133 - - [13/Nov/2026 11:15:04] "GET /stats HTTP/1.1" 200 -'
        )
        self.emit_access_log(
            '127.0.0.1 - - [13/Nov/2026 11:15:05] "GET /stats?source=ui HTTP/1.1" 200 -'
        )

        output = re.sub(r"\x1b\[[0-9;]*m", "", self.stream.getvalue())

        self.assertIn("\r", output)
        self.assertIn("stats -> 127.0.0.1: 2 | 192.168.29.133: 1", output)
        self.assertNotIn("\n", output)
        self.assertNotIn("HTTP/1.1", output)

    def test_other_access_logs_use_short_request_target_and_requested_date_format(self):
        self.emit_access_log(
            '192.168.29.133 - - [13/Nov/2026 11:15:08] '
            '"POST /local/enqueue_random HTTP/1.1" 200 -'
        )

        output = re.sub(r"\x1b\[[0-9;]*m", "", self.stream.getvalue())

        self.assertRegex(output, r"\d{2}/\d{2}/\d{2}-\d{2}:\d{2}:\d{2} INFO:werkzeug:")
        self.assertIn('192.168.29.133 "POST /local/enqueue_random" 200 -', output)
        self.assertNotIn("HTTP/1.1", output)

    def test_non_stats_access_log_finishes_live_counter_line(self):
        self.emit_access_log(
            '127.0.0.1 - - [13/Nov/2026 11:15:03] "GET /stats HTTP/1.1" 200 -'
        )
        self.emit_access_log(
            '127.0.0.1 - - [13/Nov/2026 11:15:08] '
            '"POST /local/enqueue_random HTTP/1.1" 200 -'
        )

        output = re.sub(r"\x1b\[[0-9;]*m", "", self.stream.getvalue())

        self.assertIn("\r", output)
        self.assertIn("stats -> 127.0.0.1: 1", output)
        self.assertIn('\n13/', output)
        self.assertIn('INFO:werkzeug:127.0.0.1 "POST /local/enqueue_random" 200 -', output)
