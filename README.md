# Musicify
Music streaming service to stream music directly to aireplay via icecast2 stream

## Environment

Set these in your `.env` file to override runtime defaults:

- `BOSE_IP` for the Bose SoundTouch device IP address.
- `STREAM_URL` for the primary Icecast stream URL.
- `STREAM_FALLBACK_URLS` for comma-separated fallback stream URLs.
- `PLAYLIST_DIR` for the playlist text-file directory.
- `LYRICS_ENABLED` to control lyrics on startup (`true` / `false`).
- `AUTOPLAY_ENABLED` to control autoplay on startup (`true` / `false`).
- `APP_HOST` and `APP_PORT` for the Flask server bind address.
- `YOUTUBE_API_KEY` and `MUSIC_ATLAS_KEY` for the external API integrations.
