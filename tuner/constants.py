# ---------------------------------------------------------------------------
# Video extensions MPV supports
# ---------------------------------------------------------------------------
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".vob", ".ogv", ".3gp",
    ".3g2", ".f4v", ".asf", ".rm", ".rmvb", ".divx", ".xvid", ".hevc",
    ".h264", ".h265", ".avchd", ".mxf", ".dv", ".wtv", ".m2v",
}
# TODO, Make this a constant
# MPV_CMD = [
#             "mpv",
#             "--idle=yes",
#             "--loop-file=yes",
#             "--no-terminal",
#             "--input-ipc-server={}".format(self.socket_path),
#             "--input-conf={}".format(self.input_conf_path),
#             "--script={}".format(self.lua_script_path),
#             "--osd-level=1",
#             "--osd-font-size=42",
#             "--osd-align-x=left",
#             "--osd-align-y=bottom",
#             "--osd-margin-x=40",
#             "--osd-margin-y=50",
#             "--osd-back-color=#AA000000",
#             "--osd-color=#FFFFFFFF",
#             "--osd-border-size=0",
#             "--cache-secs=10",
#             "--cache=yes",
#             "--cache-pause=no",
#             "--force-window=yes",
#             "--hwdec=auto-safe",
#             "--demuxer-max-bytes=250M",
#             "--demuxer-readahead-secs=10",
#             # "--stream-lavf-o=fflags=nobuffer", # Disable for NFS
#             "--stream-buffer-size=512K",
#             "--vd-lavc-threads=5",
#             "--demuxer-seekable-cache=no",
#             "--hr-seek=no",
#             "--hr-seek-demuxer-offset=0",
#             "--vd-lavc-fast=yes",
#             "--opengl-pbo=yes",
#             "--metadata-codepage=utf-8"
#         ]
