"""core.bose_worker.py - Lightweight wrapper for Bose SoundTouch REST API
This module defines the BoseSoundTouchWorker class, which provides a simple interface for sending commands to Bose SoundTouch speakers using their native HTTP REST API over port 8090. It includes methods for
adjusting volume, toggling power and mute states, and triggering playback of a network stream via UPnP. The worker is designed to be stateless and can be instantiated as a shared object for use across multiple Flask routes without maintaining any internal state between calls.
The class handles the necessary XML formatting for the API requests and includes error handling to manage communication issues with the speaker. It also provides a method to retrieve the current playing track and source information, which can be used to verify if the speaker is actively playing the intended stream.

This may vary depending on the specific model and firmware version of the Bose SoundTouch device, so some adjustments may be necessary for compatibility with different hardware revisions.
This file is based on the official API docs at https://assets.bosecreative.com/m/496577402d128874/original/SoundTouch-Web-API.pdf

"""
import time
import requests
import xml.etree.ElementTree as ET
import upnpclient
from core.settings import BOSE_IP

class BoseSoundTouchWorker:
    """
    A lightweight, stateless alternative wrapper for Bose SoundTouch devices
    utilizing native HTTP REST API definitions directly over port 8090.
    """
    def __init__(self, ip_address=BOSE_IP, upnp_xml_url=None):
        self.ip = ip_address
        self.base_url = f"http://{ip_address}:8090"
        self.upnp_xml = upnp_xml_url or f"http://{ip_address}:8091/XD/BO5EBO5E-F00D-F00D-FEED-38D2697D7E7B.xml"
        self._upnp_device = None
        self.session = requests.Session()

    def send_key(self, key_value):
        """
        Simulates physical remote button interaction. 
        Requires separate back-to-back POST calls for 'press' and 'release'.
        """
        url = f"{self.base_url}/key"
        headers = {'Content-Type': 'application/xml'}
        
        # 1. Send press state
        press_xml = f'<key state="press" sender="Gabbo">{key_value}</key>'
        # 2. Send release state
        release_xml = f'<key state="release" sender="Gabbo">{key_value}</key>'
        
        try:
            self.session.post(url, data=press_xml, headers=headers, timeout=0.5)
            time.sleep(0.05)  # small buffer to simulate human click cadence
            self.session.post(url, data=release_xml, headers=headers, timeout=0.5)
            return True
        except Exception as e:
            print(f"[Bose Worker] Failed sending key {key_value}: {e}")
            return False

    def toggle_power(self):
        """Sends the standard POWER toggle keystroke definition."""
        return self.send_key("POWER")

    def volume_up(self):
        """Sends a single VOLUME_UP command step."""
        return self.send_key("VOLUME_UP")

    def volume_down(self):
        """Sends a single VOLUME_DOWN command step."""
        return self.send_key("VOLUME_DOWN")

    def set_volume(self, level):
        """Sets target volume strictly between 0 and 100."""
        url = f"{self.base_url}/volume"
        headers = {'Content-Type': 'application/xml'}
        level = max(0, min(100, level))
        volume_xml = f'<volume>{level}</volume>'
        try:
            r = self.session.post(url, data=volume_xml, headers=headers, timeout=0.5)
            return r.status_code == 200
        except Exception as e:
            print(f"[Bose Worker] Volume set exception: {e}")
            return False

    def get_volume(self):
        """Fetches current volume state by parsing system XML returns."""
        url = f"{self.base_url}/volume"
        try:
            r = self.session.get(url, timeout=0.5)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                actual_vol = root.find("actualvolume")
                if actual_vol is not None:
                    return int(actual_vol.text)
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
            print("[Bose Offline] Failed to get volume.")
        except Exception as e:
            print(f"[Bose Worker] Failed get volume: {e}")
        return 0

    def toggle_mute(self):
        """Sends the discrete MUTE keystroke sequence."""
        return self.send_key("MUTE")

    def get_status(self):
        """Fetch current playback metadata and power state in one request."""
        url = f"{self.base_url}/now_playing"
        try:
            r = self.session.get(url, timeout=0.5)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                source_attr = root.get("source")
                
                # Extract track details if available
                track = root.find("track")
                artist = root.find("artist")
                track_name = track.text if track is not None else ""
                artist_name = artist.text if artist is not None else ""
                
                now_playing = {
                    "source": source_attr,
                    "track": track_name,
                    "artist": artist_name
                }
                power_state = "off" if source_attr == "STANDBY" else "on"
                return {
                    "now_playing": now_playing,
                    "power_state": power_state,
                    "is_on": power_state == "on",
                }
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError):
            print("[Bose Offline] Failed to fetch now_playing node.")
        except Exception as e:
            print(f"[Bose Worker] Failed fetching status: {e}")
        return {
            "now_playing": {"source": "UNKNOWN", "track": "NA", "artist": "NA"},
            "power_state": "disconnected",
            "is_on": False,
        }


    def is_on(self):
        """Returns True only when the speaker is powered on."""
        return self.get_status()["is_on"]
        
    def trigger_upnp_stream(self, stream_url):
        """
        Manages DLNA/UPnP presentation mapping over port 8091.
        Forces connection reinitialization upon delivery dropouts.
        """
        try:
            if self._upnp_device is None:
                self._upnp_device = upnpclient.Device(self.upnp_xml)

            av = self._upnp_device.AVTransport
            
            try:
                av.Stop(InstanceID=0)
            except:
                pass

            time.sleep(0.5)

            av.SetAVTransportURI(
                InstanceID=0,
                CurrentURI=stream_url,
                CurrentURIMetaData=""
            )

            time.sleep(0.5)
            av.Play(InstanceID=0, Speed="1")
            return True
        except Exception as e:
            print(f"[Bose Worker] DLNA/UPnP playback injection failed: {e}")
            self._upnp_device = None  # Reset tracking token for reconstruction retry
            return False
        
    def play_direct(self, stream_url):
        """
        Play HTTPS station URLs over HTTP because SoundTouch does not
        reliably support HTTPS streams.
        """
        if stream_url.lower().startswith("https://"):
            stream_url = "http://" + stream_url[8:]

        return self.trigger_upnp_stream(stream_url)
    
    def get_playing_url(self):
        """
        Retrieves the currently playing stream URL
        using UPnP GetPositionInfo.
        """
        import requests
        import xml.etree.ElementTree as ET

        url = f"http://{self.ip}:8091/AVTransport/Control"

        service = "urn:schemas-upnp-org:service:AVTransport:1"

        soap = f"""<?xml version="1.0" encoding="utf-8"?>
        <s:Envelope
            xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
            <s:Body>
                <u:GetPositionInfo xmlns:u="{service}">
                    <InstanceID>0</InstanceID>
                </u:GetPositionInfo>
            </s:Body>
        </s:Envelope>
        """

        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service}#GetPositionInfo"'
        }

        try:
            response = requests.post(
                url,
                data=soap.encode("utf-8"),
                headers=headers,
                timeout=5
            )

            response.raise_for_status()

            root = ET.fromstring(response.content)

            def get_value(name):
                element = next(
                    (
                        item for item in root.iter()
                        if item.tag.split("}")[-1] == name
                    ),
                    None
                )
                return element.text if element is not None else None

            return {
                "success": bool(get_value("TrackURI")),
                "url": get_value("TrackURI"),
                "track": get_value("Track"),
                "duration": get_value("TrackDuration"),
                "position": get_value("RelTime"),
                "metadata": get_value("TrackMetaData")
            }

        except Exception as e:
            print(f"[Bose Worker] Get playing URL failed: {e}")

            return {
                "success": False,
                "url": None,
                "track": None,
                "duration": None,
                "position": None,
                "metadata": None
            }

        
    def get_stream_metadata(self, stream_url=None):
        """
        Retrieves radio station and currently playing song
        using ICY metadata from the internet radio stream.
        """
        import requests
        import re

        if not stream_url:
            playing = self.get_playing_url()
            stream_url = playing.get("url") if playing else None

        if not stream_url:
            return {
                "success": False,
                "error": "No playing stream URL found"
            }

        response = None

        try:
            response = requests.get(
                stream_url,
                headers={
                    "Icy-MetaData": "1",
                    "User-Agent": "Mozilla/5.0"
                },
                stream=True,
                timeout=(5, 15)
            )

            response.raise_for_status()

            headers = response.headers

            station = headers.get("icy-name")
            genre = headers.get("icy-genre")
            bitrate = headers.get("icy-br")
            metaint = headers.get("icy-metaint")

            result = {
                "success": True,
                "url": stream_url,
                "station": station,
                "genre": genre,
                "bitrate": bitrate,
                "song": None,
                "artist": None,
                "stream_title": None
            }

            if not metaint:
                result["metadata_available"] = False
                result["message"] = (
                    "Stream does not expose ICY metadata"
                )
                return result

            metaint = int(metaint)

            def read_exact(stream, size):
                data = bytearray()

                while len(data) < size:
                    chunk = stream.read(size - len(data))

                    if not chunk:
                        return None

                    data.extend(chunk)

                return bytes(data)

            # Try several metadata intervals because
            # some stations initially send empty metadata.
            for _ in range(5):

                audio = read_exact(response.raw, metaint)

                if audio is None:
                    break

                length_byte = read_exact(response.raw, 1)

                if length_byte is None:
                    break

                metadata_length = length_byte[0] * 16

                if metadata_length == 0:
                    continue

                metadata_bytes = read_exact(
                    response.raw,
                    metadata_length
                )

                if metadata_bytes is None:
                    break

                metadata = metadata_bytes.decode(
                    "utf-8",
                    errors="replace"
                ).rstrip("\x00")

                match = re.search(
                    r"StreamTitle='(.*?)';",
                    metadata
                )

                if match:
                    title = match.group(1).strip()

                    result["stream_title"] = title
                    result["metadata_available"] = True

                    if " - " in title:
                        artist, song = title.split(" - ", 1)
                        result["artist"] = artist.strip()
                        result["song"] = song.strip()
                    else:
                        result["song"] = title

                    return result

            result["metadata_available"] = False
            result["message"] = (
                "No song title received in the sampled metadata blocks"
            )

            return result

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "url": stream_url
            }

        finally:
            if response is not None:
                response.close()
