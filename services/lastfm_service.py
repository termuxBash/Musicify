import requests


def resolve_track(query, api_key):
    if not query or not api_key:
        return None, None

    response = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
            "method": "track.search",
            "track": query,
            "api_key": api_key,
            "format": "json",
            "limit": 5,
        },
        timeout=10,
    )
    data = response.json()
    matches = (
        data.get("results", {})
        .get("trackmatches", {})
        .get("track", [])
    )

    if isinstance(matches, dict):
        matches = [matches]
    if not matches:
        return None, None

    best = matches[0]
    artist = best.get("artist", "")
    track = best.get("name", "")
    artist = artist.split(" - ", 1)[0].strip()
    track = track.rsplit(" - ", 1)[-1].strip()
    return artist, track


def get_track_info(artist, track, api_key):
    """Fetch canonical track metadata from Last.fm."""
    if not artist or not track or not api_key:
        return None

    response = requests.get(
        "https://ws.audioscrobbler.com/2.0/",
        params={
            "method": "track.getInfo",
            "artist": artist,
            "track": track,
            "api_key": api_key,
            "format": "json",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    track_data = data.get("track")
    if not isinstance(track_data, dict):
        return None

    album = track_data.get("album") or {}
    artist_data = track_data.get("artist") or {}
    tags_data = (
        track_data.get("toptags", {}).get("tag", [])
        if isinstance(track_data.get("toptags"), dict)
        else []
    )
    if isinstance(tags_data, dict):
        tags_data = [tags_data]

    duration = track_data.get("duration")
    try:
        duration_ms = int(duration) if duration not in (None, "") else None
    except (TypeError, ValueError):
        duration_ms = None
    try:
        listeners = int(track_data.get("listeners") or 0)
    except (TypeError, ValueError):
        listeners = 0

    return {
        "track_name": track_data.get("name") or track,
        "artist_name": (
            artist_data.get("name")
            if isinstance(artist_data, dict)
            else artist
        ),
        "album_name": album.get("title") if isinstance(album, dict) else None,
        "duration_ms": duration_ms,
        "listeners": listeners,
        "tags": [
            tag.get("name")
            for tag in tags_data
            if isinstance(tag, dict) and tag.get("name")
        ],
    }
