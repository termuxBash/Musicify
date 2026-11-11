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
