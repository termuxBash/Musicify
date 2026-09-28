# Musicify
Music streaming service to stream music directly to an AirPlay-compatible speaker via Icecast2.

## Setup
1. Clone the repository and navigate to the project directory.
```bash
git clone https://github.com/termuxBash/Musicify
cd Musicify
```
2. Run the setup helper on Debian or Ubuntu:
```bash
sudo python3 setup.py
```
It installs Icecast2 and FFmpeg, asks before installing the Python requirements (including yt-dlp) and Deno, detects the computer's local network IP, prompts for the Bose IP and required YouTube/Gemini API keys, creates `.env`, configures the `/mpv.ogg` Icecast mount, and creates a one-second silent fallback. It also creates `~/Music` and `~/Music/playlists` by default.

The helper backs up the Icecast configuration to `/etc/icecast2/icecast.xml.musicify-backup`. If setup is performed manually, configure `/etc/icecast2/icecast.xml` with:
### Linux configuration example
`/etc/icecast2/icecast.xml`:
```xml
<mount>
  <mount-name>/mpv.ogg</mount-name>
  <fallback-mount>/silent.mp3</fallback-mount>
  <fallback-override>1</fallback-override>
</mount>
```
The setup helper creates `/usr/share/icecast2/web/silent.mp3` automatically. For manual setup, copy a silent MP3 there and set the correct permissions. The Debian package commonly uses the `source` user with source password `hackme`; keep this consistent with the Icecast URL used by the FFmpeg service.

3. Get a YouTube Data API key and Gemini API key and set them in `.env`. Both are required for YouTube search and AI recommendations. Last.fm is optional.

4. If you are not using the setup helper, install the required Python dependencies using pip. `yt-dlp` is included in `requirements.txt`; Deno must also be installed because yt-dlp uses it for YouTube JavaScript challenges:
```bash
python3 -m pip install -r requirements.txt
# Install Deno separately if it is not already available:
curl -fsSL https://deno.land/install.sh | sh
```

5. Set `BOSE_IP` to the speaker's address. The setup helper detects the host address and uses it for `STREAM_URL` and the first `STREAM_FALLBACK_URLS` entry.
Configure the bose is_on() to return true always incase of incompatibility with the speaker api

6. Start Musicify:
```bash
python3 app.py
```

## Environment

Set these in your `.env` file to override runtime defaults:

- `BOSE_IP` for the Bose SoundTouch device IP address.
- `STREAM_URL` for the primary Icecast stream URL, normally `http://<host-ip>:8000/mpv.ogg`.
- `STREAM_FALLBACK_URLS` for comma-separated fallback stream URLs.
- `ROOT_DIR` for the root music directory. Defaults to `~/Music`.
- `PLAYLIST_DIR` for the playlist text-files directory. Defaults to `~/Music/playlists`.
- `HISTORY_FILE` for the active history filename. Defaults to `history.txt`; YouTube can switch it at `/youtube/<filename>`.
- `TIME_OFFSET` for the hexadecimal Unix timestamp baseline used in playback history.
- `LYRICS_ENABLED` to control lyrics on startup (`true` / `false`).
- `AUTOPLAY_ENABLED` to control autoplay on startup (`true` / `false`).
- `INCOGNI_MODE` to disable history recording by default (`true` / `false`).
- `SONG_NAME_CLEANUP` for comma-separated title suffixes to remove.
- `APP_HOST` and `APP_PORT` for the Flask server bind address.
- `YOUTUBE_API_KEY` for YouTube Data API access (required).
- `BACKUP_YOUTUBE_API_KEY` for an optional second YouTube key.
- `GEMINI_API_KEY` for AI recommendations (required for that feature).
- `GEMINI_MODEL` for the Gemini model name.
- `LASTFM_KEY` and `MUSIC_ATLAS_KEY` for optional integrations.

After setup, install Python dependencies and start Musicify:
```bash
python3 -m pip install -r requirements.txt
python3 app.py
```
