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
import concurrent.futures
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
    for dirpath, _, filenames in os.walk(root, followlinks=True):
        for fname in filenames:
            if Path(fname).suffix.lower() in VIDEO_EXTENSIONS:
                # full_path = str(Path(dirpath) / fname)
                if not fname.startswith("._") and not "sample" in fname:
                    videos.append(Path(dirpath) / fname)
    videos.sort()
    random.shuffle(videos)
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
# YouTube support — requires yt-dlp on PATH and MPV built with yt-dlp support
# ---------------------------------------------------------------------------
def fetch_youtube_videos(channel_url: str, max_videos: int = 20) -> list:
    """
    Use yt-dlp --flat-playlist to list videos from a YouTube channel URL.
    Returns a list of dicts with keys: url, title, duration.
    No downloading — metadata only.
    """
    print("  Fetching YouTube channel: {}".format(channel_url))
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--playlist-end", str(500), # Get 500 videos, we can shuffle and pick them later
                "--no-warnings",
                channel_url,
            ],
            capture_output=True, text=True, timeout=60,
        )
        videos = []

        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                url      = data.get("url") or data.get("webpage_url")
                title    = data.get("title", "Unknown")
                duration = float(data.get("duration") or 1800)
                if url:
                    # Ensure we have a full watch URL
                    if not url.startswith("http"):
                        url = "https://www.youtube.com/watch?v=" + url
                    videos.append({"url": url, "title": title, "duration": duration})
            except Exception:
                continue
        
        # Shuffle the big list
        random.shuffle(videos)

        # reduce the videos to what we defined in our max
        print("    {}  videos found, picking {}".format(len(videos), str(max_videos)))
        videos = videos[:max_videos]

        return videos
    except FileNotFoundError:
        print("  WARNING: yt-dlp not found — skipping YouTube source")
        return []
    except Exception as e:
        print("  WARNING: Could not fetch YouTube channel: {}".format(e))
        return []


def resolve_youtube_url(watch_url: str) -> Optional[dict]:
    """
    Resolve a YouTube watch URL to direct HLS stream URLs via yt-dlp.

    YouTube serves video and audio as separate HLS streams. We resolve both
    and return them as a dict so MPV can load video as the main file and
    add audio via audio-add. Both streams are HLS DVR so seeking works.

    Returns {"video": url, "audio": url_or_none} or None on failure.

    URLs expire after ~6 hours so callers should refresh periodically.
    """
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-f", "bestvideo+bestaudio/best",
                "--get-url",
                "--no-warnings",
                watch_url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        lines = [
            l.strip() for l in result.stdout.strip().splitlines()
            if l.strip().startswith("http")
        ]
        if len(lines) >= 2:
            return {"video": lines[0], "audio": lines[1]}
        elif len(lines) == 1:
            return {"video": lines[0], "audio": None}
        return None
    except Exception:
        return None


def download_youtube_video(watch_url: str, title: str, cache_dir: str) -> Optional[Path]:
    """
    Download a single YouTube (or yt-dlp supported) video to cache_dir.
    Skips the download if a matching .mp4 already exists — safe to call
    repeatedly across restarts.
    Returns the Path to the file, or None on failure.
    """
    os.makedirs(cache_dir, exist_ok=True)
    safe_title = "".join(
        c if c.isalnum() or c in " -_." else "_" for c in title
    ).strip()[:120]   # cap length for filesystem safety

    # Check cache first
    existing = list(Path(cache_dir).glob("{}.mp4".format(safe_title)))
    if existing:
        print("  [YT cache] hit: {}".format(safe_title[:60]))
        return existing[0]

    print("  [YT cache] downloading: {}".format(safe_title[:60]))
    output_template = str(Path(cache_dir) / "{}.%(ext)s".format(safe_title))
    try:
        subprocess.run(
            [
                "yt-dlp",
                # Prefer mp4 video + m4a audio so ffmpeg merge is lossless
                "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "--no-warnings",
                "--no-playlist",
                "-o", output_template,
                watch_url,
            ],
            capture_output=True, text=True, timeout=600,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print("  [YT cache] ERROR downloading {}: {}".format(safe_title[:60], e))
        return None
    except subprocess.TimeoutExpired:
        print("  [YT cache] TIMEOUT downloading {}".format(safe_title[:60]))
        return None

    matches = list(Path(cache_dir).glob("{}.mp4".format(safe_title)))
    if matches:
        print("  [YT cache] ready: {}".format(safe_title[:60]))
        return matches[0]
    print("  [YT cache] WARNING: download finished but file not found: {}".format(safe_title))
    return None


def download_youtube_source_cached(entries: list, cache_dir: str,
                                   max_concurrent: int = 2) -> List[Path]:
    """
    Download all videos in entries to cache_dir, max_concurrent at a time.
    Returns list of Paths for successfully downloaded files.
    Already-cached files are returned immediately without re-downloading.
    """
    paths: List[Path] = []
    lock = threading.Lock()
    semaphore = threading.Semaphore(max_concurrent)

    def download_one(entry):
        with semaphore:
            path = download_youtube_video(
                entry["url"], entry["title"], cache_dir
            )
            if path:
                with lock:
                    paths.append(path)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as pool:
        futures = [pool.submit(download_one, e) for e in entries]
        concurrent.futures.wait(futures)

    return paths


def load_config(config_path: str) -> dict:
    """Load and validate a JSON channel config file."""
    with open(config_path) as f:
        return json.load(f)


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
        self.previous_position = None
        self.time_of_departure = None

    def _ensure_duration(self) -> float:
        if self.duration is None:
            self.duration = get_video_duration(self.path) or 3600.0
        return self.duration

    def current_position(self) -> float:
        dur = self._ensure_duration()

        if self.previous_position is not None:
            # Returning to this channel — advance by time spent away
            elapsed_since_departure = time.time() - self.time_of_departure
            adjusted = (self.previous_position + elapsed_since_departure) % dur
            self._wall_start = time.time() - adjusted
            self.previous_position = None
            self.time_of_departure = None
        elif self._wall_start is None:
            # First ever visit — pick a random starting point
            offset = random.uniform(0, dur)
            self._wall_start = time.time() - offset

        return (time.time() - self._wall_start) % dur


    def display_name(self) -> str:        
        return "CH {:02d}  {}".format(self.index + 1, self.name)

    def epg_info(self):
        """Return (ch_label, title) for the EPG Lua overlay."""
        ch_label = "CH {:02d}".format(self.index + 1)
        title = self.name.replace("_", " ").replace(".", " ")
        return ch_label, title


# How long resolved YouTube stream URLs stay valid before needing a refresh.
# YouTube HLS URLs typically expire after ~6 hours; we refresh at 5 to be safe.
YOUTUBE_URL_TTL = 5 * 60 * 60   # 5 hours in seconds


class YouTubeChannel(Channel):
    """
    A channel backed by a YouTube video streamed via yt-dlp.

    Stream URLs are resolved in a background thread at startup and refreshed
    automatically before they expire. When the resolved URL is ready, MPV
    loads the video HLS stream directly and attaches the audio HLS stream
    as a separate track via audio-add — this gives full seeking support
    since both streams are served as HLS DVR playlists.
    """
    def __init__(self, index: int, url: str, title: str, duration: float):
        # Use a sanitised title as a fake Path so display_name/epg_info work.
        safe_title = title.replace("/", "-").replace("\\", "-")
        super().__init__(index, Path(safe_title))
        self.url = url                    # YouTube watch URL
        self.duration = duration          # from yt-dlp metadata
        self.resolved_url: Optional[dict] = None   # {"video": ..., "audio": ...}
        self._resolve_lock = threading.Lock()
        self._resolved_at: Optional[float] = None  # time.time() when resolved

    def _ensure_duration(self) -> float:
        return self.duration

    def epg_info(self):
        ch_label = "CH {:02d}".format(self.index + 1)
        title = self.name.replace("_", " ").replace(".", " ").replace("-", " ")
        return ch_label, title

    def is_url_fresh(self) -> bool:
        """Return True if the resolved URL is present and not yet expired."""
        if self.resolved_url is None or self._resolved_at is None:
            return False
        return (time.time() - self._resolved_at) < YOUTUBE_URL_TTL

    def resolve(self):
        """Resolve (or refresh) the stream URL in the calling thread.
        Safe to call from multiple threads — uses a lock to prevent races."""
        with self._resolve_lock:
            resolved = resolve_youtube_url(self.url)
            if resolved:
                self.resolved_url = resolved
                self._resolved_at = time.time()
                print("  [YT] resolved: {}".format(self.name[:50]), flush=True)
            else:
                print("  [YT] WARNING: could not resolve: {}".format(self.url),
                      flush=True)


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
            "--metadata-codepage=utf-8"
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

    # def _send(self, command: list):
    #     payload = json.dumps({"command": command}) + "\n"
    #     try:
    #         with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
    #             s.settimeout(2)
    #             s.connect(self.socket_path)
    #             s.sendall(payload.encode())
                
    #             # Read the response from MPV
    #             response = s.recv(4096).decode()
    #             data = json.loads(response)
            
    #             # MPV returns {"data": <value>, "error": "success"}
    #             return data.get("data")
    #     except Exception:
    #         pass

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
            pos = channel.current_position()     # compute BEFORE loadfile

            if isinstance(channel, YouTubeChannel):
                if channel.is_url_fresh():
                    # Resolved HLS URLs ready — load video stream directly
                    # and attach the audio stream via audio-add for full seeking.
                    video_url = channel.resolved_url["video"]
                    audio_url = channel.resolved_url.get("audio")
                    self._send(["loadfile", video_url, "replace", 0,
                                "start={},pause=yes".format(pos)])
                    # Poll until MPV has the video stream open
                    for _ in range(60):
                        result = self._send(["get_property", "playback-time"])
                        if result is not None:
                            break
                        time.sleep(0.05)
                    if audio_url:
                        self._send(["audio-add", audio_url, "select"])
                else:
                    # URL not yet resolved — fall back to watch URL (no seeking).
                    # A refresh will happen in the background.
                    print("  [YT] stream URL not ready, loading watch URL: {}".format(
                        channel.name[:40]), flush=True)
                    self._send(["loadfile", channel.url, "replace"])
            else:
                self._send(["loadfile", str(channel.path), "replace", 0,
                            "start={},pause=yes".format(pos)])
            
            # # Poll until MPV has opened the file and is paused at the right position
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


# ---------------------------------------------------------------------------
# TV simulator
# ---------------------------------------------------------------------------
class TVSimulator:
    def __init__(self, port, video_dir: Optional[str] = None, config_path: Optional[str] = None):
        all_paths: List[Path] = []
        youtube_entries: list = []

        # ── Load from config file if provided ────────────────────────────
        if config_path:
            try:
                cfg = load_config(config_path)
            except Exception as e:
                print("Error loading config: {}".format(e))
                sys.exit(1)

            # Split sources by type so we can handle them appropriately
            local_sources   = []
            stream_sources  = []   # youtube with no cache_dir → stream
            cached_sources  = []   # youtube with cache_dir → download first

            for source in cfg.get("sources", []):
                src_type = source.get("type")
                if src_type == "local":
                    local_sources.append(source)
                elif src_type == "youtube":
                    if source.get("cache_dir"):
                        cached_sources.append(source)
                    else:
                        stream_sources.append(source)
                else:
                    print("WARNING: unknown source type: {}".format(src_type))

            # Local dirs — fast, run sequentially
            for source in local_sources:
                path = source.get("path", "")
                if os.path.isdir(path):
                    all_paths.extend(find_videos(path))
                else:
                    print("WARNING: local path not found: {}".format(path))

            # Streaming YouTube sources — fetch metadata concurrently
            if stream_sources:
                stream_lock = threading.Lock()
                def fetch_stream(source):
                    url      = source.get("url", "")
                    max_vids = source.get("max_videos", 20)
                    if url:
                        entries = fetch_youtube_videos(url, max_vids)
                        with stream_lock:
                            youtube_entries.extend(entries)
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(stream_sources))
                ) as pool:
                    concurrent.futures.wait(
                        [pool.submit(fetch_stream, s) for s in stream_sources]
                    )

            # Cached YouTube sources — fetch metadata then download concurrently
            if cached_sources:
                # Step 1: fetch metadata for all cached sources concurrently
                all_cached_entries = []   # list of (entry, cache_dir) tuples
                meta_lock = threading.Lock()
                def fetch_cached_meta(source):
                    url       = source.get("url", "")
                    max_vids  = source.get("max_videos", 20)
                    cache_dir = source.get("cache_dir", "")
                    if url and cache_dir:
                        entries = fetch_youtube_videos(url, max_vids)
                        with meta_lock:
                            for e in entries:
                                all_cached_entries.append((e, cache_dir))
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(4, len(cached_sources))
                ) as pool:
                    concurrent.futures.wait(
                        [pool.submit(fetch_cached_meta, s) for s in cached_sources]
                    )

                # Step 2: download all videos, grouped by cache_dir,
                # max 2 concurrent downloads total to avoid rate limits
                if all_cached_entries:
                    print("  Downloading {} cached YouTube video(s)...".format(
                        len(all_cached_entries)))
                    dl_lock = threading.Lock()
                    dl_semaphore = threading.Semaphore(2)
                    def download_one(entry, cache_dir):
                        with dl_semaphore:
                            path = download_youtube_video(
                                entry["url"], entry["title"], cache_dir
                            )
                            if path:
                                with dl_lock:
                                    all_paths.append(path)
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=4
                    ) as pool:
                        concurrent.futures.wait([
                            pool.submit(download_one, e, d)
                            for e, d in all_cached_entries
                        ])
                    print("  {} cached video(s) ready".format(
                        len([e for e, _ in all_cached_entries])))

        # ── Load from directory argument if provided ──────────────────────
        if video_dir:
            all_paths.extend(find_videos(video_dir))

        self.user_defined_port = port

        if not all_paths and not youtube_entries:
            print("No video sources found. Provide a directory or a config file.")
            sys.exit(1)

        # ── Build channel list: local first, then YouTube, then shuffle ───
        random.shuffle(all_paths)
        channels: List[Channel] = []
        for path in all_paths:
            channels.append(Channel(len(channels), path))
        for entry in youtube_entries:
            channels.append(YouTubeChannel(
                index    = len(channels),
                url      = entry["url"],
                title    = entry["title"],
                duration = entry["duration"],
            ))
        random.shuffle(channels)
        # Re-index after shuffle so channel numbers are sequential
        for i, ch in enumerate(channels):
            ch.index = i

        self.channels: List[Channel] = channels

        # Kick off background resolution for all YouTube channels.
        # Each channel gets its own thread so they resolve concurrently.
        yt_channels = [ch for ch in self.channels if isinstance(ch, YouTubeChannel)]
        if yt_channels:
            print("  Resolving {} YouTube stream URL(s) in background...".format(
                len(yt_channels)))
            for yt_ch in yt_channels:
                threading.Thread(
                    target=yt_ch.resolve, daemon=True
                ).start()
        self.current_index: int = 0
        self.previous_index: Optional[int] = None
        self._quit = threading.Event()

        self.control_port = self._free_port(self)
        self._input_conf = self._write_input_conf()
        self._socket_path = "/tmp/mpv_tv_{}.sock".format(os.getpid())
        # Lua script lives next to this .py file
        self._lua_script = str(
            Path(__file__).parent / "tv_epg.lua"
        )

        self.mpv = MPVController(
            socket_path=self._socket_path,
            input_conf_path=self._input_conf,
            lua_script_path=self._lua_script,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _free_port(self) -> int:
        if self.user_defined_port:
            return self.user_defined_port
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
            "\\      run curl -sf http://127.0.0.1:{}/path\n".format(p),
            "SPACE  cycle pause ; run curl -sf http://127.0.0.1:{}/unpause\n".format(p)
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
        # Save MPV's current position onto the channel we're LEAVING
        try:
            departing = self.channels[self.current_index]
            departing.previous_position = self.mpv.get_pos_from_mpv()
            departing.time_of_departure = time.time()   # ← record when we left
        except Exception as e:
            print(e)
            # pass
        self.previous_index = self.current_index
        self.current_index = index
        ch = self.channels[self.current_index]
        print("\r  \u25b6  {:<60}".format(ch.display_name()), end=None, flush=True)
        threading.Thread(target=self.mpv.load_channel, args=(ch,), daemon=True).start()

    def _tune_next(self):
        self._tune(self.current_index + 1)

    def _tune_prev(self):
        self._tune(self.current_index - 1)

    def _tune_back(self):
        if self.previous_index is not None:
            self._tune(self.previous_index)

    def _show_epg(self):
        try:
            is_paused = self.mpv._send(["get_property", "pause"])
            
            if not is_paused:
                ch = self.channels[self.current_index]
                self.mpv.display_epg(ch)
        except Exception:
            pass
    def _current_video_path(self):
        ch = self.channels[self.current_index]
        print("\n  \U0001f4c2  {}".format(ch.path), flush=True)

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

        # Pre-fetch durations for local channels in background
        def prefetch():
            for ch in self.channels:
                if not isinstance(ch, YouTubeChannel):
                    ch._ensure_duration()
        threading.Thread(target=prefetch, daemon=True).start()

        # Periodically refresh YouTube stream URLs before they expire
        def refresh_youtube_urls():
            while not self._quit.is_set():
                self._quit.wait(timeout=300)   # check every 5 minutes
                for ch in self.channels:
                    if isinstance(ch, YouTubeChannel) and not ch.is_url_fresh():
                        threading.Thread(
                            target=ch.resolve, daemon=True
                        ).start()
        threading.Thread(target=refresh_youtube_urls, daemon=True).start()

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
        default=None,
        help="Root directory to scan recursively for video files.",
    )
    parser.add_argument(
        "--config", "-c",
        default=None,
        metavar="FILE",
        help="JSON config file defining local and YouTube channel sources.",
    )

    parser.add_argument(
        "--port", "-p",
        type=int,
        default=7777,
        help="Port for MPV to listen for commands on"
    )

    args = parser.parse_args()

    if args.directory is None and args.config is None:
        parser.error("Provide a directory, a --config file, or both.")

    if args.directory is not None and not os.path.isdir(args.directory):
        print("Error: '{}' is not a directory.".format(args.directory))
        sys.exit(1)

    if args.config is not None and not os.path.isfile(args.config):
        print("Error: config file not found: {}".format(args.config))
        sys.exit(1)

    TVSimulator(video_dir=args.directory, port=args.port, config_path=args.config).run()


if __name__ == "__main__":
    main()
