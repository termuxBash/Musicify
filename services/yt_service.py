"""services.yt_service.py - Service for search and stream resolution
The YTService class provides methods for searching music videos based on a text query,
resolving direct audio stream URLs from YouTube video links using yt_dlp, and enqueuing search results into the playback queue.
The service also implements a heuristic scoring system to select the best matching video from search results, prioritizing official music videos and channels while penalizing live performances, covers, and remixes.
This design ensures that the most relevant and high-quality audio streams are selected for playback while maintaining resilience against API limitations.

"""

from flask import current_app
import requests
from yt_dlp import YoutubeDL
import logging
import re
from urllib.parse import parse_qs, urlparse
from core.settings import YOUTUBE_API_KEY, BACKUP_YOUTUBE_API_KEY
from core.history_db import is_blacklisted_rick_roll, record_rick_roll_timestamp


logger = logging.getLogger(__name__)
YOUTUBE_API_KEYS = [
    YOUTUBE_API_KEY,
    BACKUP_YOUTUBE_API_KEY
]

_YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _youtube_thumbnail(info):
    thumbnail = info.get("thumbnail")
    if isinstance(thumbnail, str) and thumbnail:
        return thumbnail

    thumbnails = info.get("thumbnails") or []
    for candidate in reversed(thumbnails):
        if isinstance(candidate, dict) and candidate.get("url"):
            return candidate["url"]
    return ""


def _youtube_entry_video_id(entry):
    for field in ("id", "url", "webpage_url", "original_url"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            continue
        if _YOUTUBE_VIDEO_ID_RE.fullmatch(value):
            return value

        try:
            parsed = urlparse(
                value if "://" in value else f"https://www.youtube.com/{value.lstrip('/')}"
            )
        except ValueError:
            continue
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if _YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            return video_id
    return ""


class YTService:
    is_blacklisted_rick_roll = staticmethod(is_blacklisted_rick_roll)
    record_rick_roll_timestamp = staticmethod(record_rick_roll_timestamp)

    def resolve_stream_info(youtube_url):

        with YoutubeDL({
            "format": "bestaudio",
            "quiet": True,
            'nocheckcertificate': True,
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {"player_client": ["web_embedded"]}
            }
        }) as ydl:

            info = ydl.extract_info(
                youtube_url,
                download=False
            )

        return {
            "url": info["url"],
            "http_headers": info.get("http_headers", {})
        }

    def resolve_stream(youtube_url):
        return YTService.resolve_stream_info(youtube_url)["url"]

    def get_youtube_id_metadata(video_id="", playlist_id=""):
        """Fetch display metadata for direct YouTube video and playlist IDs."""
        results = []
        with YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "nocheckcertificate": True,
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {"player_client": ["web_embedded"]}
            },
        }) as ydl:
            if video_id:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}",
                    download=False,
                )
                results.append({
                    "kind": "video",
                    "title": info.get("title") or video_id,
                    "thumbnail": _youtube_thumbnail(info)
                    or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    "videoId": video_id,
                    "channel": info.get("channel") or info.get("uploader", ""),
                })

            if playlist_id:
                info = ydl.extract_info(
                    f"https://www.youtube.com/playlist?list={playlist_id}",
                    download=False,
                )
                entries = info.get("entries") or []
                first_entry = next(
                    (entry for entry in entries if isinstance(entry, dict)),
                    {},
                )
                results.append({
                    "kind": "playlist",
                    "title": info.get("title") or playlist_id,
                    "thumbnail": _youtube_thumbnail(info)
                    or _youtube_thumbnail(first_entry)
                    or "/static/favicon.svg",
                    "playlistId": playlist_id,
                })
        return results

    def enqueue_youtube_result(result, record_history=False):
        """
        Enqueue a YouTube search result into the playback queue.
        Expected keys:
            title
            thumbnail
            videoId
        """
        title = (result.get("title") or "").strip()
        video_id = result.get("videoId") or ""

        if is_blacklisted_rick_roll(video_id, title):
            logger.warning(
                f"Blacklisted video ID {video_id} detected for '{title}'. Recording timestamp and searching via auto_pick_song."
            )
            record_rick_roll_timestamp()
            picked = YTService.auto_pick_song(title)
            if not picked or not picked.get("videoId"):
                logger.error(f"Fallback search via auto_pick_song failed for blacklisted video '{title}'")
                return False
            if is_blacklisted_rick_roll(picked.get("videoId"), picked.get("title")):
                logger.error(f"Fallback auto_pick_song returned blacklisted ID for '{title}'")
                return False
            result = picked

        video_url = (
            f"https://www.youtube.com/watch?v={result['videoId']}"
        )

        stream_info = YTService.resolve_stream_info(video_url)

        if not stream_info.get("url"):
            return False

        return current_app.playback.enqueue(
            "youtube",
            {
                "title": result["title"],
                "thumbnail": result["thumbnail"],
                "url": stream_info["url"],
                "source_url": video_url,
                "videoId": result["videoId"],
                "record_history": record_history,
                "stream_headers": stream_info.get("http_headers", {})
            }
        )

    def enqueue_youtube_playlist(playlist_id, record_history=False):
        """Resolve a YouTube playlist with yt-dlp and enqueue its videos."""
        if not isinstance(playlist_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{10,}", playlist_id
        ):
            raise ValueError("Invalid YouTube playlist ID")

        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        with YoutubeDL({
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "nocheckcertificate": True,
            "remote_components": ["ejs:github"],
            "extractor_args": {
                "youtube": {"player_client": ["web_embedded"]}
            },
        }) as ydl:
            playlist = ydl.extract_info(playlist_url, download=False)

        entries = playlist.get("entries") if isinstance(playlist, dict) else None
        if not entries:
            raise ValueError("YouTube playlist contains no playable videos")

        songs = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            video_id = _youtube_entry_video_id(entry)
            if not video_id:
                continue
            songs.append({
                "title": entry.get("title") or video_id,
                "thumbnail": entry.get("thumbnail")
                or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                "url": None,
                "source_url": f"https://www.youtube.com/watch?v={video_id}",
                "videoId": video_id,
                "record_history": record_history,
            })

        if not songs:
            raise ValueError("YouTube playlist contains no playable videos")
        return current_app.playback.enqueue_many("youtube", songs)

    def check_for_quota_error(response_json):
        """
        Helper function to inspect Google's JSON payload for quota-specific errors.
        Google often embeds quota details inside an 'error' block.
        """
        if not isinstance(response_json, dict) or "error" not in response_json:
            return False
            
        error_data = response_json["error"]
        
        # Check 1: Direct structural check for standard quotaExceeded messages
        if "errors" in error_data and isinstance(error_data["errors"], list):
            for err in error_data["errors"]:
                if err.get("reason") in ["quotaExceeded", "rateLimitExceeded"]:
                    return True
                    
        # Check 2: Top-level message text fallback match
        message = error_data.get("message", "").lower()
        if "quota" in message or "exceeded" in message:
            return True
            
        return False


    def get_youtube_search_results(query, max_results=12):
        fields_filter = "items(id/videoId,snippet(title,thumbnails/high/url,channelTitle))"
        url = "https://www.googleapis.com/youtube/v3/search"
        
        for idx, api_key in enumerate(YOUTUBE_API_KEYS):
            # --- CRITICAL DEBUGGING SECTION ---
            if not api_key:
                logger.error(f"Key #{idx+1} is entirely EMPTY or None!")
                continue
                
            # Clean any accidental linebreaks or spaces from your configuration file
            cleaned_key = str(api_key).strip().replace('"', '').replace("'", "")
            
            logger.info(f"Testing YouTube API key #{idx+1}.")
            # ----------------------------------

            params = {
                "part": "snippet",
                "q": query + " music",
                "type": "video",
                "maxResults": max_results,
                "fields": fields_filter,
                "key": cleaned_key # Use the cleaned key
            }
            
            try:
                response = requests.get(url, params=params, timeout=10)
                
                try:
                    res_data = response.json()
                except Exception:
                    res_data = {}

                if response.status_code == 403 or YTService.check_for_quota_error(res_data):
                    logger.warning(f"Key #{idx+1} failed due to quota limit.")
                    continue  
                    
                if response.status_code != 200:
                    logger.error(f"YouTube API Error Status {response.status_code}: {response.text[:200]}")
                    continue 
                    
                return res_data, 200
                
            except Exception as e:
                logger.error(f"Network exception using key #{idx+1}: {e}")
                continue

        logger.error("All compiled fallback API keys have exhausted their current daily quotas.")
        return {"error": "All available fallback quotas exceeded for today"}, 403



    def auto_pick_song(query):
        # Fetch data using our new robust key rotation function
        res, status_code = YTService.get_youtube_search_results(query, max_results=10)
        
        if not res or "items" not in res:
            return None

        items = res.get("items", [])
        if not items:
            return None

        # Filter out blacklisted items (dQw4w9WgXcQ not accompanied by 'Never Gonna Give You Up')
        items = [
            item for item in items
            if not is_blacklisted_rick_roll(item.get("id", {}).get("videoId"), query)
            and not is_blacklisted_rick_roll(item.get("id", {}).get("videoId"), item.get("snippet", {}).get("title"))
        ]
        if not items:
            return None

        bad_words = [
            "live", "cover", "slowed", "reverb", "nightcore", "8d", "remix", 
            "bass boosted", "megamix", "mix", "compilation", "full album", 
            "greatest hits", "non stop", "non-stop"
        ]

        first_item = items[0]
        first_title = first_item["snippet"]["title"].lower()
        if not any(word in first_title for word in bad_words):
            best_item = first_item
            return {
                "title": best_item["snippet"]["title"],
                "thumbnail": best_item["snippet"]["thumbnails"]["high"]["url"],
                "videoId": best_item["id"]["videoId"],
                "channel": best_item["snippet"]["channelTitle"]
            }

        best_score = -999
        best_item = None

        for item in items:
            title = item["snippet"]["title"].lower()
            channel = item["snippet"]["channelTitle"].lower()
            score = 0

            if "official" in title: score += 5
            if "topic" in channel: score += 5
            if "vevo" in channel: score += 4
            if "music" in channel: score += 2

            for word in bad_words:
                if word in title:
                    score -= 20  

            score -= len(title) // 40

            if score > best_score:
                best_score = score
                best_item = item

        if not best_item or best_score < -10:
            return None

        return {
            "title": best_item["snippet"]["title"],
            "thumbnail": best_item["snippet"]["thumbnails"]["high"]["url"],
            "videoId": best_item["id"]["videoId"],
            "channel": best_item["snippet"]["channelTitle"]
        }