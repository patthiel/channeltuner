# ChannelTuner

Simple script to recreate the experience of live tv from your local video library and Youtube channels.

Every video in the sources gets turned into a channel which, when tuned to, will begin playing at a random offset and continue playing when tuned to other channels. Returning to the channel will start from however many seconds / minutes passed since you left, just like real tv.



## Install

* Tested with Python 3.10.16

* Requires MPV
* Youtube support requires yt-dlp

```bash
# Use homebrew to install mpv on the system
$ brew install mpv ffmpeg

# install python dependencies 
$ pip install mpv yt-dlp
```


## Using the script

```bash
$ python tv_channels.py /path/to/videos

# OR use a channel config file (see channels.json.example)
$ python ./tv_channels.py --config ./channels.json
```

### Controls

* Up and down keybindings for next and previous channels.
* `F` Fullscreen 
* `\` prints the path to the current video in the console (helpful in large video libraries, or wanting to know which stream is playing)
* `Q` Exits the MPV and the script
* `ESC` Exits the script
* All other standard MPV keybindings are applied.

## Dev Notes

* This is about 80% AI generated code. Therefore, some of the methods aren't as clean as i'd like them to be and readability varies pretty wildly. In the future, i'll break out the objects into a saner codebase that makes it easier to digest and refactor as needed.

* Network storage optimizations: a couple MPV flags have been enabled to optimize for "local" storage coming from a network mounted volume (eg: NFS/Samba). In my experience, NFS works best for this use case, but Samba performance has not been terrible (depending on your home networks bandwidth).

* Fast channel switching can result in some weird behavior. Some race conditions exist if you tune too fast that, are turning out to be harder to resolve without degrading performance. stay "tuned" for updates where i can hopefully fix this.

* Youtube attempts to resolve url's in a worker thread in the beginning, some URL's won't resolve. In my experience, the urls that won't resolve are paywall'ed videos on a youtube channel. Right now, instead of rebuilding the channel index while playing, i just have it check before tuning to the channel if the URL resolved. If it didn't resolve we just tune to the next avaialble channel. This may look like tuning from Channel 1 -> 4 skips a bunch of channels, but it has been the shortest way to resolve this issue for now.
