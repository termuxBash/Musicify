# Musicify
Music streaming service to stream music directly to aireplay via icecast2 stream

# Setup
1. Clone the repository and navigate to the project directory.

2. Make sure Icecast2 is installed and configured on your system. You can follow the official Icecast documentation for installation instructions. 
the default configuration is set to stream on `http://localhost:8000/stream` with the username `source` and password `password`. You can modify these settings in the `config/icecast.xml` file.


## Environment

Set these in your `.env` file to override runtime defaults:

- `BOSE_IP` for the Bose SoundTouch device IP address.
- `STREAM_URL` for the primary Icecast stream URL.
- `STREAM_FALLBACK_URLS` for comma-separated fallback stream URLs.
- `ROOT_DIR` for the root music directory.
- `PLAYLIST_DIR` for the playlist text-files directory.
- `LYRICS_ENABLED` to control lyrics on startup (`true` / `false`).
- `AUTOPLAY_ENABLED` to control autoplay on startup (`true` / `false`).
- `APP_HOST` and `APP_PORT` for the Flask server bind address.
- `YOUTUBE_API_KEY` and `MUSIC_ATLAS_KEY` for the external API integrations.
- `BACKUP_YOUTUBE_API_KEY` is a backup api key if the 1st one fails put a random characters here
