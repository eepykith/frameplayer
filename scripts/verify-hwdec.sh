#!/bin/sh
# Show whether this machine can hardware-decode, then print Frame Player's plan.
set -eu
echo "== V4L2 nodes =="
found=0
for node in /sys/class/video4linux/video*; do
  if [ -r "$node/name" ]; then
    found=1
    echo "$(basename "$node"): $(cat "$node/name")"
  fi
done
if [ "$found" -eq 0 ]; then
  echo "No /sys/class/video4linux nodes."
fi
if [ -e /dev/video-dec0 ]; then
  echo
  echo "== /dev/video-dec0 output formats =="
  v4l2-ctl -d /dev/video-dec0 --list-formats-out || true
fi
echo
echo "== Frame Player plan =="
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ -x "$root/.venv/bin/python" ]; then
  "$root/.venv/bin/python" -m frameplayer --probe
else
  python3 -m frameplayer --probe
fi
echo
echo "Play any H.264, HEVC, or VP9 file and read the stats overlay."
echo "Hardware decode is active when the badge says v4l2m2m-copy, not Software fallback."
echo "The mpv log is in \${XDG_DATA_HOME:-~/.local/share}/frameplayer/mpv.log"
