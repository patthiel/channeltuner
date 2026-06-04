import os
import sys
import time
import random
import threading
import signal
import tempfile
import json
import concurrent.futures
import socket as _socket
import tuner.sources

from http.server import HTTPServer
from pathlib import Path
from typing import List, Optional
from tuner.channel import Channel, YouTubeChannel, StreamChannel
from tuner.mpv import MPVController, _make_handler


# ---------------------------------------------------------------------------
# TV simulator
# ---------------------------------------------------------------------------
class TVSimulator:
    def __init__(self, port, video_dir: Optional[str] = None, config_path: Optional[str] = None):
        all_paths: List[Path] = []
        youtube_entries: list = []

        # ── Load from config file if provided ────────────────────────────
        stream_channels: List[StreamChannel] = []

        if config_path:
            self.config_path = config_path
            try:
                cfg = tuner.sources.load_config(config_path)
            except Exception as e:
                print("Error loading config: {}".format(e))
                sys.exit(1)

            # Split sources by type so we can handle them appropriately
            local_sources   = []
            live_sources    = []   # type: "stream" → direct URL live channels
            stream_sources  = []   # youtube with no cache_dir → stream
            cached_sources  = []   # youtube with cache_dir → download first

            for source in cfg.get("sources", []):
                src_type = source.get("type")
                if src_type == "local":
                    local_sources.append(source)
                elif src_type == "stream":
                    live_sources.append(source)
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
                    local_paths = []
                    local_paths.extend(tuner.sources.find_videos(path))
                else:
                    print("WARNING: local path not found: {}".format(path))
                max_videos = source.get("max_videos")
            
                # Trim our local paths if we have defined max:
                if max_videos:
                    local_paths = local_paths[:max_videos]

                all_paths.extend(local_paths)


            # Live stream sources — instantiate directly, no network fetch needed
            for source in live_sources:
                url  = source.get("url", "").strip()
                name = source.get("channel_name", url)
                if url:
                    stream_channels.append(StreamChannel(
                        index=0,   # re-indexed after shuffle
                        url=url,
                        channel_name=name,
                    ))
                else:
                    print("WARNING: stream source missing url: {}".format(source))

            # Streaming YouTube sources — fetch metadata concurrently
            if stream_sources:
                stream_lock = threading.Lock()
                def fetch_stream(source):
                    url      = source.get("url", "")
                    max_vids = source.get("max_videos", 20)
                    if url:
                        entries = tuner.sources.fetch_youtube_videos(url, max_vids)
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
                        entries = tuner.sources.fetch_youtube_videos(url, max_vids)
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
                            path = tuner.sources.download_youtube_video(
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
            all_paths.extend(tuner.sources.find_videos(video_dir))
            

        self.user_defined_port = port

        if not all_paths and not youtube_entries and not stream_channels:
            print("No video sources found. Provide a directory or a config file.")
            sys.exit(1)

        # ── Build channel list: local, YouTube, live streams, then shuffle ─
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
        for sc in stream_channels:
            sc.index = len(channels)
            channels.append(sc)
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

        self.control_port = self._free_port()
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
        # (skip for live streams — they have no meaningful position)
        departing = self.channels[self.current_index]
        if not isinstance(departing, StreamChannel):
            try:
                departing.previous_position = self.mpv.get_pos_from_mpv()
                departing.time_of_departure = time.time()
            except Exception as e:
                print(e)
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
        favorites_file = "{}-favs.txt"
        with open(favorites_file.format(self.config_path), "a") as f:
            vid_file = str(ch.url) if isinstance(ch, YouTubeChannel) else str(ch.path)
            f.write(vid_file + "\n")
            

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
