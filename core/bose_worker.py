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
            requests.post(url, data=press_xml, headers=headers, timeout=3)
            time.sleep(0.05)  # small buffer to simulate human click cadence
            requests.post(url, data=release_xml, headers=headers, timeout=3)
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
            r = requests.post(url, data=volume_xml, headers=headers, timeout=3)
            return r.status_code == 200
        except Exception as e:
            print(f"[Bose Worker] Volume set exception: {e}")
            return False

    def get_volume(self):
        """Fetches current volume state by parsing system XML returns."""
        url = f"{self.base_url}/volume"
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                actual_vol = root.find("actualvolume")
                if actual_vol is not None:
                    return int(actual_vol.text)
        except Exception as e:
            print(f"[Bose Worker] Failed get volume") #: {e}")
        return 0

    def toggle_mute(self):
        """Sends the discrete MUTE keystroke sequence."""
        return self.send_key("MUTE")

    def get_now_playing(self):
        """
        Retrieves the current source and metadata mapping.
        Useful to verify if the speaker is actively on your UPnP stream.
        """
        url = f"{self.base_url}/now_playing"
        try:
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                source_attr = root.get("source")
                
                # Extract track details if available
                track = root.find("track")
                artist = root.find("artist")
                track_name = track.text if track is not None else ""
                artist_name = artist.text if artist is not None else ""
                
                return {
                    "source": source_attr,
                    "track": track_name,
                    "artist": artist_name
                }
        except Exception as e:
            print(f"[Bose Worker] Failed fetching now_playing node: {e}")
        return {"source": "UNKNOWN", "track": "", "artist": ""}

    def is_on(self):
        """
        Queries the device to check its current power state.
        Returns True if the device is active, False if it is in STANDBY or unreachable.
        """
        url = f"{self.base_url}/now_playing"
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                source_attr = root.get("source")
                
                # If the source is 'STANDBY', the speaker is off.
                if source_attr == "STANDBY":
                    return False
                return True
        except Exception as e:
            print(f"[Bose Worker] Failed checking power state (device may be offline): {e}")
        return False
        
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