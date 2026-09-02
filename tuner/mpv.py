import json
import os
import subprocess
import threading
import time
import socket as _socket

from http.server import BaseHTTPRequestHandler, HTTPServer
from tuner.channel import Channel, YouTubeChannel, StreamChannel
from typing import Optional

# ---------------------------------------------------------------------------
# MPV controller
# ---------------------------------------------------------------------------
class MPVController:
    def __init__(self, socket_path: str, input_conf_path: str, lua_script_path: str):
        self.socket_path = socket_path
        self.input_conf_path = input_conf_path
        self.lua_script_path = lua_script_path
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self):
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        cmd = [
            "mpv",
            "--idle=yes",
            "--loop-file=yes",
            "--no-terminal",
            "--input-ipc-server={}".format(self.socket_path),
            "--input-conf={}".format(self.input_conf_path),
            "--script={}".format(self.lua_script_path),
            "--osd-level=1",
            "--osd-font-size=42",
            "--osd-align-x=left",
            "--osd-align-y=bottom",
            "--osd-margin-x=40",
            "--osd-margin-y=50",
            "--osd-back-color=#AA000000",
            "--osd-color=#FFFFFFFF",
            "--osd-border-size=0",
            "--cache-secs=10",
            "--cache=yes",
            "--cache-pause=no",
            "--force-window=yes",
            "--hwdec=auto-safe",
            "--demuxer-max-bytes=250M",
            "--demuxer-readahead-secs=10",
            # "--stream-lavf-o=fflags=nobuffer", # Disable for NFS
            "--stream-buffer-size=512K",
            "--vd-lavc-threads=5",
            "--demuxer-seekable-cache=no",
            "--hr-seek=no",
            "--hr-seek-demuxer-offset=0",
            "--vd-lavc-fast=yes",
            "--opengl-pbo=yes",
            "--metadata-codepage=utf-8",
            "--video-aspect-override=no"
        ]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(80):
            if os.path.exists(self.socket_path):
                break
            time.sleep(0.1)

    def _send(self, command: list):
        payload = json.dumps({"command": command}) + "\n"
        try:
            with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
                s.settimeout(2)
                s.connect(self.socket_path)
                s.sendall(payload.encode())
                
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    # Process all newline-delimited JSON objects in the buffer
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        try:
                            data = json.loads(line.decode())
                            # Skip unsolicited event messages, only return command responses
                            if "error" in data:
                                return data.get("data")
                        except json.JSONDecodeError:
                            continue
                    # If we got a response already, stop reading
                    if not buf and chunk:
                        break
        except Exception:
            print('Error sending to MPV')
            # pass


    def load_channel(self, channel: Channel):
        ch_label, title = channel.epg_info()
        with self._lock:
            if isinstance(channel, StreamChannel):
                # Live stream — reduced readahead so playback starts on the
                # first segment rather than waiting for 10 s of buffer.
                self._send(["loadfile", channel.url, "replace", 0,
                            "demuxer-readahead-secs=2,cache-secs=2"])

            elif isinstance(channel, YouTubeChannel):
                if channel.is_live:
                    # YouTube live stream — let MPV's yt-dlp resolve at play time.
                    # No seeking; reduced readahead matches StreamChannel behaviour.
                    self._send(["loadfile", channel.url, "replace", 0,
                                "demuxer-readahead-secs=2,cache-secs=2"])

                elif channel.is_url_fresh():
                    # Resolved HLS URLs ready — load video stream directly and
                    # attach audio via audio-add for full seeking support.
                    pos = channel.current_position()
                    video_url = channel.resolved_url["video"]
                    audio_url = channel.resolved_url.get("audio")
                    self._send(["loadfile", video_url, "replace", 0,
                                "start={},pause=yes".format(pos)])
                    for _ in range(60):
                        result = self._send(["get_property", "playback-time"])
                        if result is not None:
                            break
                        time.sleep(0.05)
                    if audio_url:
                        self._send(["audio-add", audio_url, "select"])
                    for _ in range(40):
                        result = self._send(["get_property", "playback-time"])
                        if result is not None:
                            break
                        time.sleep(0.05)
                    self._send(["set_property", "pause", False])

                else:
                    # URL not yet resolved — fall back to watch URL without seeking.
                    # The background resolve() thread will update it for next time.
                    pos = channel.current_position()
                    self._send(["loadfile", channel.url, "replace", 0,
                                "start={},pause=yes".format(pos)])
                    for _ in range(40):
                        result = self._send(["get_property", "playback-time"])
                        if result is not None:
                            break
                        time.sleep(0.05)
                    self._send(["set_property", "pause", False])

            else:
                pos = channel.current_position()
                self._send(["loadfile", str(channel.path), "replace", 0,
                            "start={},pause=yes".format(pos)])
                # Poll until MPV has opened the file and is paused at the right position
                for _ in range(40):
                    result = self._send(["get_property", "playback-time"])
                    if result is not None:
                        break
                    time.sleep(0.05)
                self._send(["set_property", "pause", False])

            self._send(["script-message", "cache-epg-info", ch_label, title])
            self._send(["script-message", "show-epg", ch_label, title])

    def stop(self):
        if self._proc:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        for p in (self.socket_path, self.input_conf_path):
            try:
                os.remove(p)
            except OSError:
                pass
    
    # Get the video's current position from MPV
    def get_pos_from_mpv(self):
        result = self._send(["get_property", "time-pos"])
        if result is None:
            raise RuntimeError("MPV did not return playback position")
        return float(result)
    
    def display_epg(self, channel: Channel):
        ch_label, title = channel.epg_info()
        self._send(["script-message", "cache-epg-info", ch_label, title])
        self._send(["script-message", "show-epg", ch_label, title])



# ---------------------------------------------------------------------------
# Tiny HTTP control server — receives commands fired by MPV keybindings
# ---------------------------------------------------------------------------
def _make_handler(tv_ref):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            cmd = self.path.lstrip("/")
            if cmd == "next":
                tv_ref._tune_next()
            elif cmd == "prev":
                tv_ref._tune_prev()
            elif cmd == "back":
                tv_ref._tune_back()
            elif cmd == "unpause":
                tv_ref._show_epg()
            elif cmd == "path":
                tv_ref._current_video_path()
            elif cmd == "quit":
                tv_ref._quit.set()  
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass  # silence request log

    return Handler
