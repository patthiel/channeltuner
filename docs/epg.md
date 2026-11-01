# On-screen channel guide (EPG)

When you tune to a channel, or move the mouse over the MPV window, a cable-style guide
appears along the bottom of the screen for about 6 seconds. It is drawn by
`tuner/tv_epg.lua` as an MPV ASS overlay, styled after an old Comcast / TV Guide grid:
a "CHANNEL GUIDE" strip, a time bar, a blue channel cell, and a program cell (the current
program is highlighted light green).

The guide only shows the **current channel**. There are two display modes.

## Simple mode (default)

- The time bar shows the current time of day (e.g. `5:22 PM`).
- The channel is one program cell spanning the full width of the row.

This suits the current behaviour, where every channel is a single media item that loops.

## Schedule mode

A true EPG-style view, kept so it is ready if channels ever support more than one video.

- The time bar shows ticks every 30 minutes across a 90-minute window
  (now, +30, +60), with a `▶` at the right edge.
- The first cell is the program airing now, as wide as the time left in the video.
  After it, the same title repeats at its full length until the window is filled,
  since the channel loops.
- Live streams and channels with unknown duration get one cell across the whole row.

## Enabling schedule mode

Add a top-level key to your channels config file:

```json
{
  "epg_show_schedule": true,
  "sources": [ ... ]
}
```

Omit it or set it to `false` for simple mode. The setting is read at startup and passed to
MPV as the script option `epg_schedule` (`--script-opts=epg_schedule=yes`).

## How it works

- `TVSimulator._guide_payload()` (`tuner/simulator.py`) builds one tab-separated row for the
  current channel: `label`, `title`, `seconds_remaining` (`-1` if unknown or live),
  `duration` (`0` if live). Rows are separated by newlines.
- `MPVController` (`tuner/mpv.py`) sends it as a third argument on the `show-epg` and
  `cache-epg-info` script messages.
- `Channel.time_remaining()` (`tuner/channel.py`) computes the time left without changing
  playback state.
- The Lua overlay is anchored to the bottom of the window and sized to the number of rows
  it receives, so sending more rows (up to 4) draws a multi-channel grid. Schedule mode
  with real multi-video channels would require `_guide_payload` to send upcoming programs.
- The clock is the time when the guide is drawn; it does not tick while the overlay is up.
