from flask import current_app
import requests
from yt_dlp import YoutubeDL
import logging
import os


YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
logger = logging.getLogger(__name__)
YOUTUBE_API_KEYS = [
    YOUTUBE_API_KEY,
    os.getenv("BACKUP_YOUTUBE_API_KEY")
]


class YTService:

    def resolve_stream(youtube_url):

        with YoutubeDL({
            "format": "bestaudio",
            "quiet": True,
            'nocheckcertificate': True,
            "remote_components": ["ejs:github"]
        }) as ydl:

            info = ydl.extract_info(
                youtube_url,
                download=False
            )

        return info["url"]
    
    def enqueue_youtube_result( result):
        """
        Enqueue a YouTube search result into the playback queue.
        Expected keys:
            title
            thumbnail
            videoId
        """

        video_url = (
            f"https://www.youtube.com/watch?v={result['videoId']}"
        )

        stream_url = YTService.resolve_stream(video_url)

        if not stream_url:
            return False

        return current_app.playback.enqueue(
            "youtube",
            {
                "title": result["title"],
                "thumbnail": result["thumbnail"],
                "url": stream_url
            }
        )

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
            
            logger.info(f"Testing Key #{idx+1}. Length: {len(cleaned_key)}. Starts with: {cleaned_key[:4]}")
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

        bad_words = [
            "live", "cover", "slowed", "reverb", "nightcore", "8d", "remix", 
            "bass boosted", "megamix", "mix", "compilation", "full album", 
            "greatest hits", "non stop", "non-stop"
        ]

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