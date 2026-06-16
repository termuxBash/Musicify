# Musicify
Music streaming service to stream music directly to aireplay via icecast2 stream

# Setup
1. Clone the repository and navigate to the project directory.
```bash
git clone https://github.com/termuxBash/Musicify
cd Musicify
```
2. Make sure Icecast2 is installed and configured on your system. You can follow the official Icecast documentation for installation instructions. 
the default configuration is set to stream on `http://localhost:8000/stream` with the username `source` and password `password`. You can modify these settings in the `config/icecast.xml` file.
# Linux configuration example
`/etc/icecast2/icecast.xml`:
```xml
<mount>
  <mount-name>/mpv.ogg</mount-name>
  <fallback-mount>/silent.mp3</fallback-mount>
  <fallback-override>1</fallback-override>
</mount>
```
Make sure to copy the `silent.mp3` file to the appropriate location (e.g., `/usr/share/icecast2/web/silent.mp3`) and set the correct permissions.

3. Get the required API keys for YouTube Data API and Last.fm API, and set them in the `.env` file.

4. Install the required Python dependencies using pip:
```bash
pip install -r requirements.txt
```

5. Setup the speaker api accordingly to your speaker and set the `BOSE_IP` in the `.env` file.
Configure the bose is_on() to return true always incase of incompatibility with the speaker api

6. Start with
```bash
python3 app.py
```

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
- `YOUTUBE_API_KEY` and `LASTFM_API_KEY` for the external API integrations.
- `BACKUP_YOUTUBE_API_KEY` is a backup api key if the 1st one fails put a random characters here
