import unittest
from unittest.mock import ANY, patch

from services.lyrics_service import LyricsService, align_lyrics, parse_synced_lyrics


class LyricsServiceTests(unittest.TestCase):
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

    def test_lyrics_state_selects_the_active_lrclib_line(self):
        service = LyricsService(enabled=True)
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


if __name__ == "__main__":
    unittest.main()
