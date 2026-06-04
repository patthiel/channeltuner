import time
import random
import threading
from pathlib import Path
from typing import Optional
from tuner.sources import get_video_duration, resolve_youtube_url

# Channel, YouTubeChannel, and StreamChannel classes

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


class StreamChannel(Channel):
    """
    A live stream channel (HLS, DASH, etc.).

    Loaded directly by URL with no seeking — the stream is always at the
    live edge, so random-offset and wall-clock position logic is skipped.
    """
    def __init__(self, index: int, url: str, channel_name: str):
        safe_name = channel_name.replace("/", "-").replace("\\", "-")
        super().__init__(index, Path(safe_name))
        self.url = url
        self.channel_name = channel_name
        self.duration = 0.0

    def _ensure_duration(self) -> float:
        return 0.0

    def current_position(self) -> float:
        return 0.0

    def epg_info(self):
        ch_label = "CH {:02d}".format(self.index + 1)
        return ch_label, self.channel_name

