import unittest
from unittest.mock import ANY, patch

import requests
from flask import Flask

from core.stats import stats_bp
from services.lyrics_service import (
    LyricsService,
    align_lyrics,
    is_non_english_script,
    parse_synced_lyrics,
    romanize_lyrics,
)


class LyricsServiceTests(unittest.TestCase):
    def test_lyrics_offset_route_updates_runtime_service_offset(self):
        app = Flask(__name__)
        app.register_blueprint(stats_bp)
        app.lyrics_service = LyricsService(enabled=True, offset_ms=-3000)

        response = app.test_client().post("/lyrics_offset", json={"offset_ms": 1250})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["lyrics_offset_ms"], 1250)
        self.assertEqual(app.lyrics_service.offset_ms, 1250)

    def test_lyrics_offset_route_rejects_non_integer_values(self):
        app = Flask(__name__)
        app.register_blueprint(stats_bp)
        app.lyrics_service = LyricsService(enabled=True, offset_ms=-3000)

        response = app.test_client().post("/lyrics_offset", json={"offset_ms": True})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(app.lyrics_service.offset_ms, -3000)

    def test_parse_synced_lyrics_reads_lrc_timestamps(self):
        parsed = parse_synced_lyrics(
            "[00:01.20] First line\n[00:03.5][00:04.25] Second line\n[00:05.00] "
        )

        self.assertEqual(
            parsed,
            [(1.2, "First line"), (3.5, "Second line"), (4.25, "Second line")],
        )

    def test_align_lyrics_uses_lrclib_timestamps(self):
        aligned = align_lyrics(
            "Translated one\nTranslated two",
            "[00:01.00] Source one\n[00:04.00] Source two\n[00:08.00] Source three",
        )

        self.assertEqual(aligned, [(1.0, "Translated one"), (8.0, "Translated two")])

    def test_align_lyrics_keeps_unsynced_provider_lines(self):
        aligned = align_lyrics("First line\nSecond line", "")

        self.assertEqual(aligned, [(None, "First line"), (None, "Second line")])

    def test_non_english_script_detection_ignores_latin_diacritics(self):
        self.assertFalse(is_non_english_script("Crème brûlée"))
        self.assertTrue(is_non_english_script("こんにちは"))

    def test_lyrics_state_selects_the_active_lrclib_line(self):
        service = LyricsService(enabled=True, offset_ms=0)
        service.current_lyrics = [(1.0, "First line"), (4.0, "Second line")]
        service.song_start_time = 100

        with patch("services.lyrics_service.time.time", return_value=105):
            state = service.get_lyrics_state()

        self.assertEqual(state["current_lyric_index"], 1)
        self.assertEqual(state["current_lyric"], "Second line")
        self.assertEqual(
            state["lyrics"],
            [
                {"time": 1.0, "text": "First line"},
                {"time": 4.0, "text": "Second line"},
            ],
        )
        self.assertEqual(state["lyrics_offset_ms"], 0)

    def test_negative_lyrics_offset_delays_highlighting(self):
        service = LyricsService(enabled=True, offset_ms=-3000)
        service.current_lyrics = [(1.0, "First line"), (4.0, "Second line")]
        service.song_start_time = 100

        with patch("services.lyrics_service.time.time", return_value=105):
            state = service.get_lyrics_state()

        self.assertEqual(state["current_lyric_index"], 0)

    def test_positive_lyrics_offset_advances_highlighting(self):
        service = LyricsService(enabled=True, offset_ms=3000)
        service.current_lyrics = [(1.0, "First line"), (4.0, "Second line")]
        service.song_start_time = 100

        with patch("services.lyrics_service.time.time", return_value=105):
            state = service.get_lyrics_state()

        self.assertEqual(state["current_lyric_index"], 1)

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_uses_lastfm_names_and_both_lyrics_sources(
        self, get, resolve_track
    ):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse(
                [
                    {
                        "syncedLyrics": "[00:01.00] Original one\n[00:04.00] Original two",
                        "plainLyrics": "Original one\nOriginal two",
                    }
                ]
            ),
            FakeResponse({"lyrics": "Romanized one\nRomanized two"}),
        ]
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa (Official Video)", 0)

        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)
        self.assertEqual(
            get.call_args_list[0].kwargs["params"],
            {"q": "Ado Ussewa"},
        )
        self.assertEqual(
            get.call_args_list[1].kwargs["params"],
            {
                "artist": "Ado",
                "song": "Ussewa",
                "translate": "true",
                "romanize": "true",
                "language": "en",
            },
        )
        self.assertEqual(
            service.current_lyrics,
            [(1.0, "Romanized one"), (4.0, "Romanized two")],
        )
        self.assertEqual(service.lyrics_source, "Lyrica")

    @patch("services.lyrics_service.GEMINI_API_KEY", "test-key")
    @patch("services.lyrics_service.requests.post")
    def test_romanize_lyrics_trusts_non_latin_gemini_output(self, post):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "candidates": [
                        {"content": {"parts": [{"text": "Konnichiwa こんにちは\nSekai"}]}}
                    ]
                }

        post.return_value = FakeResponse()

        result = romanize_lyrics("こんにちは\n世界")

        self.assertEqual(result, "Konnichiwa こんにちは\nSekai")
        self.assertIn("Do not translate them into English", post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.GEMINI_API_KEY", "test-key")
    @patch("services.lyrics_service.requests.post")
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_displays_gemini_output_even_with_non_latin_characters(
        self, get, post, resolve_track
    ):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse(
                [
                    {
                        "syncedLyrics": "[00:01.00] 正しさとは\n[00:04.00] 愚かさとは",
                        "plainLyrics": "正しさとは\n愚かさとは",
                    }
                ]
            ),
            FakeResponse({"lyrics": "正しさとは\n愚かさとは"}),
        ]
        post.return_value = FakeResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Tadashisa to wa 正しさとは\nOrokasa to wa"}
                            ]
                        }
                    }
                ]
            }
        )
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa", 0)

        self.assertEqual(
            post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"].split("LYRICS:\n", 1)[1],
            "正しさとは\n愚かさとは\n",
        )
        self.assertEqual(
            service.current_lyrics,
            [(1.0, "Tadashisa to wa 正しさとは"), (4.0, "Orokasa to wa")],
        )
        self.assertEqual(service.lyrics_source, "Gemini")
        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.GEMINI_API_KEY", None)
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_falls_back_to_lyrica_when_gemini_is_unavailable(
        self, get, resolve_track
    ):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse([{"plainLyrics": "正しさとは", "syncedLyrics": "[00:01.00] 正しさとは"}]),
            FakeResponse({"lyrics": "正しさとは"}),
        ]
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa", 0)

        self.assertEqual(service.current_lyrics, [(1.0, "正しさとは")])
        self.assertEqual(service.lyrics_source, "Lyrica")
        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.GEMINI_API_KEY", "test-key")
    @patch("services.lyrics_service.requests.post", side_effect=requests.Timeout)
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_falls_back_to_lyrica_after_gemini_error(
        self, get, post, resolve_track
    ):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse(
                [
                    {
                        "plainLyrics": "正しさとは",
                        "syncedLyrics": "[00:01.00] 正しさとは",
                    }
                ]
            ),
            FakeResponse({"lyrics": "Lyrica fallback line"}),
        ]
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa", 0)

        self.assertEqual(service.current_lyrics, [(1.0, "Lyrica fallback line")])
        self.assertEqual(service.lyrics_source, "Lyrica")
        post.assert_called_once()
        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.GEMINI_API_KEY", None)
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_falls_back_to_lrclib_when_lyrica_has_no_result(
        self, get, resolve_track
    ):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse(
                [
                    {
                        "plainLyrics": "正しさとは",
                        "syncedLyrics": "[00:01.00] 正しさとは",
                    }
                ]
            ),
            FakeResponse({"status": "error", "lyrics": ""}),
        ]
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa", 0)

        self.assertEqual(service.current_lyrics, [(1.0, "正しさとは")])
        self.assertEqual(service.lyrics_source, "LRCLIB")
        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)

    @patch("services.lyrics_service.resolve_track", return_value=("Ado", "Ussewa"))
    @patch("services.lyrics_service.LASTFM_KEY", "test-key")
    @patch("services.lyrics_service.GEMINI_API_KEY", None)
    @patch("services.lyrics_service.requests.get")
    def test_load_lyrics_is_empty_when_all_providers_fail(self, get, resolve_track):
        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        get.side_effect = [
            FakeResponse([]),
            FakeResponse({"status": "error", "lyrics": ""}),
        ]
        service = LyricsService()

        service._load_lyrics("Ado - Ussewa", 0)

        self.assertEqual(service.current_lyrics, [])
        self.assertIsNone(service.lyrics_source)
        resolve_track.assert_called_once_with("Ado - Ussewa", ANY)


if __name__ == "__main__":
    unittest.main()
