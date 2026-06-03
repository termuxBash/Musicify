import time
import requests
import socket
import logging
import upnpclient
from bosesoundtouchapi import SoundTouchDevice, SoundTouchClient
from bosesoundtouchapi.models import SoundTouchKeys, KeyStates

logger = logging.getLogger("BoseWorker")

class BoseWorker:
    def __init__(self, ip="192.168.29.234", upnp_port=8091, xml_uuid="BO5EBO5E-F00D-F00D-FEED-38D2697D7E7B"):
        self.ip = ip
        self.upnp_xml_url = f"http://{ip}:{upnp_port}/XD/{xml_uuid}.xml"
        
        self.bose_client = None
        self.upnp_device = None
        
        # Fallback cache values for when network configurations drop out
        self.cached_stream_url = None

    def get_client(self):
        """Retrieves or reconnects the SoundTouch API Client."""
        if self.bose_client:
            return self.bose_client
        try:
            device = SoundTouchDevice(self.ip)
            self.bose_client = SoundTouchClient(device)
            logger.info(f"Connected successfully to Bose API at {self.ip}")
        except Exception as e:
            logger.error(f"Bose API connection failed: {e}")
            self.bose_client = None
        return self.bose_client

    def resolve_stream_url(self, preferred_ips=None, fallback_port=8000, fallback_path="mpv.ogg"):
        """
        Dynamically finds the correct local Icecast stream URL.
        Ported from your original blueprint logic.
        """
        if preferred_ips is None:
            preferred_ips = ["192.168.29.157", "192.168.29.229"]

        for ip in preferred_ips:
            url = f"http://{ip}:{fallback_port}/{fallback_path}"
            try:
                r = requests.get(url, timeout=2, stream=True)
                if r.status_code == 200:
                    self.cached_stream_url = url
                    return url
            except Exception:
                pass

        # Fallback to local network IP auto-detection
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            self.cached_stream_url = f"http://{local_ip}:{fallback_port}/{fallback_path}"
        except Exception:
            self.cached_stream_url = f"http://127.0.0.1:{fallback_port}/{fallback_path}"
            
        return self.cached_stream_url

    def listen(self, stream_url=None):
        """Forces the speaker to intercept and play the current Icecast stream via UPnP."""
        # Fallback to resolving standard URL setups if none provided
        target_url = stream_url or self.cached_stream_url or self.resolve_stream_url()
        
        try:
            if self.upnp_device is None:
                self.upnp_device = upnpclient.Device(self.upnp_xml_url)

            av = self.upnp_device.AVTransport
            try:
                av.Stop(InstanceID=0)
            except Exception:
                pass

            time.sleep(0.5)

            av.SetAVTransportURI(
                InstanceID=0,
                CurrentURI=target_url,
                CurrentURIMetaData=""
            )

            time.sleep(0.5)
            av.Play(InstanceID=0, Speed="1")
            logger.info(f"Bose actively streaming target: {target_url}")
            return True

        except Exception as e:
            logger.error(f"Bose UPnP rendering failed: {e}")
            self.upnp_device = None  # Force rediscovery on next crash
            return False

    # --- CONTROL API MAPPINGS ---

    def volume_up(self):
        client = self.get_client()
        if client:
            client.VolumeUp()

    def volume_down(self):
        client = self.get_client()
        if client:
            client.VolumeDown()

    def set_volume(self, level):
        client = self.get_client()
        if client:
            client.SetVolumeLevel(max(0, min(100, level)))

    def get_volume(self):
        client = self.get_client()
        if client:
            try:
                return client.GetVolume(True).Actual
            except Exception:
                pass
        return 0

    def toggle_power(self):
        client = self.get_client()
        if client:
            client.Action(SoundTouchKeys.POWER, KeyStates.Both)