from flask import current_app
import requests
from yt_dlp import YoutubeDL

import os
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


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

    def auto_pick_song(query):

        url = "https://www.googleapis.com/youtube/v3/search"

        params = {
            "part": "snippet",
            "q": query + " music",
            "type": "video",
            "maxResults": 10,
            "key": YOUTUBE_API_KEY
        }

        res = requests.get(url, params=params).json()

        items = res.get("items", [])

        if not items:
            return None

        bad_words = [
            "live",
            "cover",
            "slowed",
            "reverb",
            "nightcore",
            "8d",
            "remix",
            "bass boosted"
        ]

        best_score = -999
        best_item = None

        for item in items:

            title = item["snippet"]["title"].lower()
            channel = item["snippet"]["channelTitle"].lower()

            score = 0

            # good signals

            if "official" in title:
                score += 5

            if "topic" in channel:
                score += 5

            if "vevo" in channel:
                score += 4

            if "music" in channel:
                score += 2

            # bad signals

            for word in bad_words:
                if word in title:
                    score -= 10

            # prefer shorter cleaner titles

            score -= len(title) // 40

            if score > best_score:
                best_score = score
                best_item = item

        if not best_item:
            return None

        return {
            "title": best_item["snippet"]["title"],
            "thumbnail": best_item["snippet"]["thumbnails"]["high"]["url"],
            "videoId": best_item["id"]["videoId"],
            "channel": best_item["snippet"]["channelTitle"]
        }