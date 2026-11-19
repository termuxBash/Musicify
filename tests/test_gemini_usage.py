import sqlite3
import unittest
from unittest.mock import MagicMock, patch
import logging

from core.history_db import (
    init_db,
    init_gemini_usage_db,
    log_gemini_usage,
    get_connection,
    get_all_tables,
    delete_table,
    GEMINI_SOURCE_AUTOPLAY,
    GEMINI_SOURCE_LYRICS,
)


class GeminiUsageTests(unittest.TestCase):
    def setUp(self):
        # Ensure fresh connection / tables
        init_db()

    def test_table_and_views_exist_with_correct_schema(self):
        with get_connection() as conn:
            # Check table existence and columns
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(gemini_usage);")
            columns = {row["name"]: row["type"].upper() for row in cursor.fetchall()}
            self.assertIn("timestamp", columns)
            self.assertIn("source_code", columns)
            self.assertIn("prompt_token_count", columns)
            self.assertIn("candidates_token_count", columns)

            # Check views existence
            cursor.execute("SELECT name FROM sqlite_master WHERE type='view';")
            view_names = {row["name"] for row in cursor.fetchall()}
            self.assertIn("Gemini_tokens", view_names)
            self.assertIn("Gemini_tokens_daily", view_names)
            self.assertIn("Gemini_tokens_hourly", view_names)

    def test_log_gemini_usage_with_object_and_source_codes(self):
        class MockUsage:
            prompt_token_count = 120
            candidates_token_count = 80
            total_token_count = 200

        with patch("core.history_db.logger.info") as mock_info:
            log_gemini_usage(MockUsage(), source_code=0)
            mock_info.assert_called_with(
                "Gemini token usage: prompt=%s, candidates=%s, total=%s",
                120,
                80,
                200,
            )

        with patch("core.history_db.logger.info") as mock_info:
            log_gemini_usage(MockUsage(), source_code="lyrics")
            mock_info.assert_called_with(
                "Gemini token usage: prompt=%s, candidates=%s, total=%s",
                120,
                80,
                200,
            )

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT source_code, prompt_token_count, candidates_token_count FROM gemini_usage ORDER BY rowid DESC LIMIT 2;")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 2)
            # Most recent is lyrics (1)
            self.assertEqual(rows[0]["source_code"], 1)
            self.assertEqual(rows[0]["prompt_token_count"], 120)
            self.assertEqual(rows[0]["candidates_token_count"], 80)
            # Previous is autoplay (0)
            self.assertEqual(rows[1]["source_code"], 0)

    def test_views_output_and_column_names(self):
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM gemini_usage;")
            # Insert known timestamps:
            # 1700000000: 2023-11-14 22:13:20 UTC
            # 1700000100: same hour and day
            # 1700003700: same day, next hour (1 hour is 3600s)
            conn.execute(
                "INSERT INTO gemini_usage (timestamp, source_code, prompt_token_count, candidates_token_count) VALUES (?, ?, ?, ?);",
                (1700000000, 0, 100, 20),
            )
            conn.execute(
                "INSERT INTO gemini_usage (timestamp, source_code, prompt_token_count, candidates_token_count) VALUES (?, ?, ?, ?);",
                (1700000100, 0, 150, 30),
            )
            conn.execute(
                "INSERT INTO gemini_usage (timestamp, source_code, prompt_token_count, candidates_token_count) VALUES (?, ?, ?, ?);",
                (1700003700, 1, 500, 50),
            )
            conn.commit()

            # Test Gemini_tokens view
            cursor.execute("SELECT * FROM Gemini_tokens ORDER BY \"DateTime\" ASC;")
            col_names = [d[0] for d in cursor.description]
            self.assertEqual(col_names, ["DateTime", "Source", "Input Tokens", "Output Tokens", "Total Tokens"])
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["Source"], "autoplay")
            self.assertEqual(rows[0]["Input Tokens"], 100)
            self.assertEqual(rows[0]["Output Tokens"], 20)
            self.assertEqual(rows[0]["Total Tokens"], 120)

            self.assertEqual(rows[2]["Source"], "lyrics")
            self.assertEqual(rows[2]["Input Tokens"], 500)
            self.assertEqual(rows[2]["Output Tokens"], 50)
            self.assertEqual(rows[2]["Total Tokens"], 550)

            # Test Gemini_tokens_daily view
            cursor.execute("SELECT * FROM Gemini_tokens_daily ORDER BY \"Source\" ASC;")
            daily_cols = [d[0] for d in cursor.description]
            self.assertEqual(daily_cols, ["DateTime", "Source", "Input Tokens", "Output Tokens", "Total Tokens"])
            daily_rows = cursor.fetchall()
            self.assertEqual(len(daily_rows), 2)
            # Autoplay aggregated: 100 + 150 = 250, 20 + 30 = 50, total 300
            self.assertEqual(daily_rows[0]["Source"], "autoplay")
            self.assertEqual(daily_rows[0]["Input Tokens"], 250)
            self.assertEqual(daily_rows[0]["Output Tokens"], 50)
            self.assertEqual(daily_rows[0]["Total Tokens"], 300)
            # Lyrics aggregated: 500, 50, total 550
            self.assertEqual(daily_rows[1]["Source"], "lyrics")
            self.assertEqual(daily_rows[1]["Input Tokens"], 500)
            self.assertEqual(daily_rows[1]["Output Tokens"], 50)
            self.assertEqual(daily_rows[1]["Total Tokens"], 550)

            # Test Gemini_tokens_hourly view
            cursor.execute("SELECT * FROM Gemini_tokens_hourly ORDER BY \"DateTime\" ASC, \"Source\" ASC;")
            hourly_cols = [d[0] for d in cursor.description]
            self.assertEqual(hourly_cols, ["DateTime", "Source", "Input Tokens", "Output Tokens", "Total Tokens"])
            hourly_rows = cursor.fetchall()
            self.assertEqual(len(hourly_rows), 2)
            self.assertEqual(hourly_rows[0]["Source"], "autoplay")
            self.assertEqual(hourly_rows[0]["Input Tokens"], 250)
            self.assertEqual(hourly_rows[0]["Output Tokens"], 50)
            self.assertEqual(hourly_rows[0]["Total Tokens"], 300)
            self.assertEqual(hourly_rows[1]["Source"], "lyrics")
            self.assertEqual(hourly_rows[1]["Input Tokens"], 500)
            self.assertEqual(hourly_rows[1]["Output Tokens"], 50)
            self.assertEqual(hourly_rows[1]["Total Tokens"], 550)

    def test_gemini_usage_table_hidden_from_general_history_tables(self):
        tables = get_all_tables()
        self.assertNotIn("gemini_usage", tables)
        self.assertNotIn("extra_data", tables)
        self.assertFalse(delete_table("gemini_usage"))

    @patch("apps.yt.routes.settings.GEMINI_API_KEY", "fake-key")
    @patch("core.history_db.get_history_text", return_value="Song A>vid1>1>100")
    @patch("apps.yt.routes.genai.Client")
    @patch("apps.yt.routes.log_gemini_usage")
    def test_yt_recommendations_invokes_log_gemini_usage(self, mock_log_usage, mock_client_cls, mock_history):
        from apps.yt.routes import get_gemini_recommendations
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "Song 1>1234567890a\nSong 2>1234567890b\nSong 3>1234567890c"
        mock_usage = MagicMock()
        mock_response.usage_metadata = mock_usage
        mock_client.models.generate_content.return_value = mock_response

        recs = get_gemini_recommendations()
        self.assertEqual(len(recs), 3)
        mock_log_usage.assert_called_once_with(mock_usage, source_code=0)

    @patch("services.lyrics_service.settings.GEMINI_API_KEY", "fake-key")
    @patch("services.lyrics_service.genai.Client")
    @patch("services.lyrics_service.log_gemini_usage")
    def test_lyrics_service_invokes_log_gemini_usage(self, mock_log_usage, mock_client_cls):
        from services.lyrics_service import romanize_lyrics
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = "Romanized | English"
        mock_usage = MagicMock()
        mock_response.usage_metadata = mock_usage
        mock_client.models.generate_content.return_value = mock_response

        res = romanize_lyrics("歌詞")
        self.assertEqual(res, "Romanized | English")
        mock_log_usage.assert_called_once_with(mock_usage, source_code=1)


if __name__ == "__main__":
    unittest.main()

