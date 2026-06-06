from yt_dlp import YoutubeDL


class YTService:

    def resolve_stream(self, youtube_url):

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