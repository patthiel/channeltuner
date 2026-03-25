#!/usr/bin/env python3
"""
tv_channels.py — MPV-based fake TV channel simulator

Usage:
    python3 tv_channels.py /path/to/video/directory

Controls (all handled inside the MPV window — terminal does NOT need focus):
    UP    arrow  → next channel
    DOWN  arrow  → previous channel
    B            → last-watched channel (toggle back)
    Q / ESC      → quit

How it works:
    • Every video file found recursively in the source directory becomes a channel.
    • Each channel tracks a wall-clock anchor so the video always progresses in
      real time, even while you're watching a different channel.
    • On first visit a random start offset is chosen; subsequent visits land
      exactly where the channel "would be" now.
    • A tiny HTTP server (localhost only) receives commands from MPV keybindings
      written to a temp input.conf, so the MPV window itself captures all keys.
"""

import os
import sys
import time
import random
import subprocess
import threading
import signal
import argparse
import tempfile
import json
import socket as _socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Video extensions MPV supports
# ---------------------------------------------------------------------------
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".vob", ".ogv", ".3gp",
    ".3g2", ".f4v", ".asf", ".rm", ".rmvb", ".divx", ".xvid", ".hevc",
    ".h264", ".h265", ".avchd", ".mxf", ".dv", ".wtv", ".m2v",
}


def find_videos(root: str) -> List[Path]:
    videos = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if Path(fname).suffix.lower() in VIDEO_EXTENSIONS:
                videos.append(Path(dirpath) / fname)
    videos.sort()
    return videos


def get_video_duration(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------
class Channel:
    def __init__(self, index: int, path: Path):
        self.index = index
        self.path = path
        self.name = path.stem
        self.duration: Optional[float] = None
        self._wall_start: Optional[float] = None

    def _ensure_duration(self) -> float:
        if self.duration is None:
            self.duration = get_video_duration(self.path) or 3600.0
        return self.duration

    def current_position(self) -> float:
        dur = self._ensure_duration()
        if self._wall_start is None:
            offset = random.uniform(0, dur)
            self._wall_start = time.time() - offset
        return (time.time() - self._wall_start) % dur

    def display_name(self) -> str:
        return "CH {:02d}  {}".format(self.index + 1, self.name)

    def epg_banner(self) -> str:
        """
        Plain text banner for MPV show-text.
        Two lines separated by a literal newline character.
        """
        ch_label = "CH {:02d}".format(self.index + 1)
        title = self.name.replace("_", " ").replace(".", " ")
        return "● {}\n{}".format(ch_label, title)


# ---------------------------------------------------------------------------
# MPV controller
# ---------------------------------------------------------------------------
class MPVController:
    def __init__(self, socket_path: str, input_conf_path: str):
        self.socket_path = socket_path
        self.input_conf_path = input_conf_path
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
            "--osd-level=1",
            "--osd-font-size=42",
            "--osd-align-x=left",
            "--osd-align-y=bottom",
            "--osd-margin-x=40",
            "--osd-margin-y=50",
            "--osd-back-color=#AA000000",
            "--osd-color=#FFFFFFFF",
            "--osd-border-size=0",
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
        except Exception:
            pass

    def load_channel(self, channel: Channel):
        pos = channel.current_position()
        with self._lock:
            self._send(["loadfile", str(channel.path), "replace"])
            time.sleep(0.45)
            self._send(["set_property", "pause", False])
            self._send(["seek", pos, "absolute"])
            self._send(["show-text", channel.epg_banner(), 3500])

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
            elif cmd == "quit":
                tv_ref._quit.set()
            self.send_response(204)
            self.end_headers()

        def log_message(self, fmt, *args):
            pass  # silence request log

    return Handler


# ---------------------------------------------------------------------------
# TV simulator
# ---------------------------------------------------------------------------
class TVSimulator:
    def __init__(self, video_dir: str):
        videos = find_videos(video_dir)
        if not videos:
            print("No video files found under: {}".format(video_dir))
            sys.exit(1)

        self.channels: List[Channel] = [Channel(i, p) for i, p in enumerate(videos)]
        self.current_index: int = 0
        self.previous_index: Optional[int] = None
        self._quit = threading.Event()

        self.control_port = self._free_port()
        self._input_conf = self._write_input_conf()
        self._socket_path = "/tmp/mpv_tv_{}.sock".format(os.getpid())

        self.mpv = MPVController(
            socket_path=self._socket_path,
            input_conf_path=self._input_conf,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _free_port() -> int:
        with _socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _write_input_conf(self) -> str:
        p = self.control_port
        # MPV's `run` command fires a subprocess; curl hits our local server.
        # We keep all other default MPV bindings intact (pause, volume, etc.)
        # by only overriding the specific keys we need.
        lines = [
            "UP     run curl -sf http://127.0.0.1:{}/next\n".format(p),
            "DOWN   run curl -sf http://127.0.0.1:{}/prev\n".format(p),
            "b      run curl -sf http://127.0.0.1:{}/back\n".format(p),
            "B      run curl -sf http://127.0.0.1:{}/back\n".format(p),
            "q      run curl -sf http://127.0.0.1:{}/quit\n".format(p),
            "ESC    run curl -sf http://127.0.0.1:{}/quit\n".format(p),
        ]
        fd, path = tempfile.mkstemp(suffix=".conf", prefix="mpv_tv_input_")
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
        return path

    # ------------------------------------------------------------------
    def _tune(self, index: int):
        index = index % len(self.channels)
        if index == self.current_index:
            return
        self.previous_index = self.current_index
        self.current_index = index
        ch = self.channels[self.current_index]
        print("\r  \u25b6  {:<60}".format(ch.display_name()), end="", flush=True)
        # Run in a thread so the HTTP handler returns immediately
        threading.Thread(target=self.mpv.load_channel, args=(ch,), daemon=True).start()

    def _tune_next(self):
        self._tune(self.current_index + 1)

    def _tune_prev(self):
        self._tune(self.current_index - 1)

    def _tune_back(self):
        if self.previous_index is not None:
            self._tune(self.previous_index)

    # ------------------------------------------------------------------
    def _start_http_server(self):
        handler = _make_handler(self)
        server = HTTPServer(("127.0.0.1", self.control_port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    # ------------------------------------------------------------------
    def run(self):
        print("\n" + "=" * 60)
        print("  \U0001f4fa  MPV TV Channel Simulator")
        print("=" * 60)
        print("  {} channels loaded".format(len(self.channels)))
        print()
        print("  Controls inside the MPV window (terminal can be minimised):")
        print("  UP    \u2192 next channel")
        print("  DOWN  \u2192 previous channel")
        print("  B     \u2192 last-watched channel (toggle)")
        print("  Q/ESC \u2192 quit")
        print("=" * 60 + "\n")

        # Pre-fetch durations in background
        def prefetch():
            for ch in self.channels:
                ch._ensure_duration()
        threading.Thread(target=prefetch, daemon=True).start()

        self._start_http_server()
        self.mpv.start()

        # Start on a random channel
        self.current_index = random.randrange(len(self.channels))
        ch = self.channels[self.current_index]
        print("\r  \u25b6  {:<60}".format(ch.display_name()), end="", flush=True)
        self.mpv.load_channel(ch)

        # Ctrl-C in terminal still works as a fallback
        def _sigint(_s, _f):
            self._quit.set()
        signal.signal(signal.SIGINT, _sigint)

        # Quit automatically if the user closes the MPV window
        def _watch_mpv():
            if self.mpv._proc:
                self.mpv._proc.wait()
            self._quit.set()
        threading.Thread(target=_watch_mpv, daemon=True).start()

        self._quit.wait()

        print("\n\n  Shutting down...")
        self.mpv.stop()
        print("  Goodbye.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="MPV-powered fake TV channel simulator.")
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Root directory to scan recursively for video files (default: current dir)",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print("Error: '{}' is not a directory.".format(args.directory))
        sys.exit(1)

    TVSimulator(args.directory).run()


if __name__ == "__main__":
    main()