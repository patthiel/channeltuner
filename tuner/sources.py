import hashlib
import json
import os
import random
import subprocess
import threading
import time
import concurrent.futures
from pathlib import Path
from typing import List, Optional

PLAYLIST_CACHE_TTL = 12 * 60 * 60  # 12 hours in seconds

from tuner.constants import VIDEO_EXTENSIONS


# Helper functions for finding videos and handling video metadata

# Find videos and shuffle
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

def process_file_sources(file_paths: list):
    files = []
    for f in file_paths:
        if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
            files.append(Path(f))

    files.sort()
    random.shuffle(files)
    return files

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
def _playlist_cache_path(channel_url: str, cache_dir: str) -> Path:
    key = hashlib.md5(channel_url.encode()).hexdigest()
    return Path(cache_dir) / "{}.json".format(key)


def purge_playlist_cache(cache_dir: str = ".yt_cache", ttl: float = PLAYLIST_CACHE_TTL):
    """Delete playlist cache files whose mtime is older than ttl seconds."""
    cache_path = Path(cache_dir)
    if not cache_path.exists():
        return
    now = time.time()
    purged = 0
    for f in cache_path.glob("*.json"):
        try:
            if now - f.stat().st_mtime > ttl:
                f.unlink()
                purged += 1
        except Exception:
            pass
    if purged:
        print("  [playlist cache] purged {} expired file(s) from {}".format(purged, cache_dir))


def fetch_youtube_videos(
    channel_url: str,
    max_videos: int = 20,
    is_live: bool = False,
    playlist_cache_dir: str = ".yt_cache",
    ttl: float = PLAYLIST_CACHE_TTL,
) -> list:
    """
    Use yt-dlp --flat-playlist to list videos from a YouTube channel URL.
    Returns a list of dicts with keys: url, title, duration, is_live.

    Results are cached to playlist_cache_dir for PLAYLIST_CACHE_TTL seconds
    (12 hours) so repeated startups skip the yt-dlp call entirely.
    The full fetched list is stored; max_videos is applied at load time so
    changing it never requires a cache bust.
    """
    # ── Cache read ────────────────────────────────────────────────────────
    if playlist_cache_dir:
        cache_file = _playlist_cache_path(channel_url, playlist_cache_dir)
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < ttl:
                try:
                    cached = json.loads(cache_file.read_text())
                    videos = cached.get("videos", [])
                    random.shuffle(videos)
                    pick = min(max_videos, len(videos))
                    print("  [playlist cache] hit: {} ({} videos, picking {})".format(
                        channel_url, len(videos), pick))
                    return videos[:pick]
                except Exception:
                    pass  # corrupt cache — fall through to fetch

    # ── Fetch from yt-dlp ─────────────────────────────────────────────────
    print("  Fetching YouTube channel: {}".format(channel_url))
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--playlist-end", "500",
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
                    if not url.startswith("http"):
                        url = "https://www.youtube.com/watch?v=" + url
                    videos.append({"url": url, "title": title, "duration": duration, "is_live": is_live})
            except Exception:
                continue

        # ── Cache write (store full list before shuffle/trim) ─────────────
        if playlist_cache_dir and videos:
            try:
                cache_file = _playlist_cache_path(channel_url, playlist_cache_dir)
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"videos": videos}))
            except Exception as e:
                print("  WARNING: could not write playlist cache: {}".format(e))

        random.shuffle(videos)
        pick = min(max_videos, len(videos))
        print("    {} videos found, picking {}".format(len(videos), pick))
        return videos[:pick]

    except FileNotFoundError:
        print("  WARNING: yt-dlp not found — skipping YouTube source")
        return []
    except Exception as e:
        print("  WARNING: Could not fetch YouTube channel: {}".format(e))
        return []


def fetch_youtube_live_info(url: str) -> dict:
    """
    Fetch the title of a single YouTube live stream URL.
    Returns {"url": url, "title": title}. Falls back to the URL as title on failure.
    """
    print("  Fetching YouTube live stream info: {}".format(url))
    try:
        result = subprocess.run(
            ["yt-dlp", "--no-playlist", "--dump-json", "--no-warnings", url],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout.strip().splitlines()[0])
        return {"url": url, "title": data.get("title", url)}
    except Exception:
        print("  WARNING: could not fetch live stream info for: {}".format(url))
        return {"url": url, "title": url}


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
