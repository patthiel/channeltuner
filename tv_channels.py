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
import argparse

from tuner.simulator import TVSimulator


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
