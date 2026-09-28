#!/usr/bin/env python3
"""Create a Musicify .env file and configure a local Icecast2 service."""

from __future__ import annotations

import getpass
import ipaddress
import os
import pwd
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from xml.etree import ElementTree


PROJECT_DIR = Path(__file__).resolve().parent
ENV_FILE = PROJECT_DIR / ".env"
EXAMPLE_ENV_FILE = PROJECT_DIR / "example.env"
ICECAST_CONFIG = Path("/etc/icecast2/icecast.xml")
STREAM_MOUNT = "/mpv.ogg"
USER_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
if os.environ.get("SUDO_USER"):
    USER_HOME = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)


def detect_ip() -> str:
    """Return the address this machine uses on the local network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
            ipaddress.ip_address(address)
            return address
    except (OSError, ValueError):
        return "127.0.0.1"


def prompt(name: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = (getpass.getpass if secret else input)(f"{name}{suffix}: ").strip()
    return value or default


def required_prompt(name: str, secret: bool = False) -> str:
    while True:
        value = prompt(name, secret=secret)
        if value:
            return value
        print(f"{name} is required.")


def confirm(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    answer = input(f"{question}{suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def install_icecast() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run setup with sudo: sudo python3 setup.py")

    if shutil.which("apt-get"):
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "icecast2", "ffmpeg"])
    elif not shutil.which("icecast2"):
        raise RuntimeError("Icecast2 is not installed and this setup script requires apt-get.")

    if confirm("Install Python requirements, including yt-dlp?"):
        install_python_dependencies()
    else:
        print("Skipped Python requirements installation.")

    if confirm("Install Deno for yt-dlp?"):
        install_deno()
    else:
        print("Skipped Deno installation.")


def install_python_dependencies() -> None:
    pip_command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-r",
        str(PROJECT_DIR / "requirements.txt"),
    ]
    externally_managed = (
        Path(sys.prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "EXTERNALLY-MANAGED"
    )
    if externally_managed.exists():
        pip_command.append("--break-system-packages")
    run([
        *pip_command,
    ])


def install_deno() -> None:
    if shutil.which("deno"):
        return

    if shutil.which("apt-get"):
        result = run(["apt-get", "install", "-y", "deno"], check=False)
        if result.returncode == 0 and shutil.which("deno"):
            return

    try:
        with urllib.request.urlopen("https://deno.land/install.sh", timeout=30) as response:
            installer = response.read()
        environment = os.environ.copy()
        environment["DENO_INSTALL"] = "/usr/local"
        subprocess.run(["sh"], input=installer, check=True, env=environment)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("Could not install Deno. Install it from https://deno.com/manual/getting_started/installation") from error

    if not shutil.which("deno"):
        raise RuntimeError("Deno installation completed but the deno command is not available.")


def configure_icecast() -> None:
    if not ICECAST_CONFIG.exists():
        raise RuntimeError(f"Icecast configuration not found: {ICECAST_CONFIG}")

    tree = ElementTree.parse(ICECAST_CONFIG)
    root = tree.getroot()
    mount = next((node for node in root.findall("mount")
                  if node.findtext("mount-name") == STREAM_MOUNT), None)
    if mount is None:
        mount = ElementTree.SubElement(root, "mount")
        ElementTree.SubElement(mount, "mount-name").text = STREAM_MOUNT

    fallback = mount.find("fallback-mount")
    if fallback is None:
        fallback = ElementTree.SubElement(mount, "fallback-mount")
    fallback.text = "/silent.mp3"

    override = mount.find("fallback-override")
    if override is None:
        override = ElementTree.SubElement(mount, "fallback-override")
    override.text = "1"

    backup = ICECAST_CONFIG.with_suffix(".xml.musicify-backup")
    shutil.copy2(ICECAST_CONFIG, backup)
    tree.write(ICECAST_CONFIG, encoding="utf-8", xml_declaration=True)
    silent_file = Path("/usr/share/icecast2/web/silent.mp3")
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "1", "-q:a", "9", str(silent_file),
    ])
    run(["systemctl", "enable", "--now", "icecast2"])


def write_env() -> None:
    local_ip = detect_ip()
    print(f"Detected local network IP: {local_ip}")
    bose_ip = prompt("Bose device IP", "192.168.29.234")
    youtube_key = required_prompt("YouTube API key", secret=True)
    gemini_key = required_prompt("Gemini API key", secret=True)
    playlist_dir = prompt("Playlist directory", str(USER_HOME / "Music" / "playlists"))
    root_dir = prompt("Music root directory", str(USER_HOME / "Music"))
    history_file = prompt("History filename", "history.txt")

    values = {
        "APP_HOST": "0.0.0.0",
        "APP_PORT": "5000",
        "BOSE_IP": bose_ip,
        "STREAM_URL": f"http://{local_ip}:8000{STREAM_MOUNT}",
        "STREAM_FALLBACK_URLS": f"http://{local_ip}:8000{STREAM_MOUNT},http://127.0.0.1:8000{STREAM_MOUNT}",
        "PLAYLIST_DIR": playlist_dir,
        "HISTORY_FILE": history_file,
        "ROOT_DIR": root_dir,
        "TIME_OFFSET": "0",
        "LYRICS_ENABLED": "false",
        "AUTOPLAY_ENABLED": "false",
        "INCOGNI_MODE": "false",
        "SONG_NAME_CLEANUP": "live,acoustic,performance",
        "YOUTUBE_API_KEY": youtube_key,
        "BACKUP_YOUTUBE_API_KEY": "",
        "MUSIC_ATLAS_KEY": "",
        "LASTFM_KEY": "",
        "GEMINI_API_KEY": gemini_key,
        "GEMINI_MODEL": "gemini-2.0-flash",
    }

    rendered = ["# Generated by setup.py"]
    rendered_keys = set()
    for line in EXAMPLE_ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rendered.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            rendered.append(f"{key}={values[key]}")
            rendered_keys.add(key)
        else:
            rendered.append(line)

    rendered.extend(
        f"{key}={value}"
        for key, value in values.items()
        if key not in rendered_keys
    )
    ENV_FILE.write_text("\n".join(rendered) + "\n", encoding="utf-8")
    Path(playlist_dir).expanduser().mkdir(parents=True, exist_ok=True)
    Path(root_dir).expanduser().mkdir(parents=True, exist_ok=True)
    if os.environ.get("SUDO_USER"):
        user = pwd.getpwnam(os.environ["SUDO_USER"])
        for path in (ENV_FILE, Path(playlist_dir).expanduser(), Path(root_dir).expanduser()):
            os.chown(path, user.pw_uid, user.pw_gid)


def main() -> int:
    try:
        install_icecast()
        configure_icecast()
        write_env()
    except (OSError, RuntimeError, ElementTree.ParseError, subprocess.CalledProcessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1
    print(f"Created {ENV_FILE}")
    print("Install Python dependencies with: python3 -m pip install -r requirements.txt")
    print("Start Musicify with: python3 app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())