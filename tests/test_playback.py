"""Decode real files through the same libmpv session the window uses."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from frameplayer.hwdec import probe_hwdec
from frameplayer.presentation import Presentation
from frameplayer.resolve import PlayItem
from frameplayer.session import start_session


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _item(path: Path, width: int, height: int) -> PlayItem:
    return PlayItem(
        video_url=str(path),
        audio_url=None,
        title=path.name,
        vcodec="h264",
        width=width,
        height=height,
        fps=10,
        tbr=None,
        headers={},
        is_live=False,
        page_url=str(path),
        format_id="file",
        note="",
        hw=False,
        over_iris=max(width, height) > 8192,
        source="file",
    )


def _play(path: Path, width: int, height: int, layout: str = "packed"):
    plan = probe_hwdec()
    player, playback = start_session(vo="null", ao="null", plan=plan, enabled=True)
    try:
        playback.load(_item(path, width, height))
        import time

        deadline = time.time() + 30
        while time.time() < deadline:
            playback.poll_fallback()
            stats = playback.snapshot()
            if stats.decoded_width > 0 and (stats.time_pos > 0.2 or playback.eof):
                playback.apply_presentation(Presentation(layout=layout))
                stats = playback.snapshot()
                return stats, playback.active()
            if playback.fatal:
                raise AssertionError(playback.last_error_line())
            time.sleep(0.1)
        raise AssertionError("timed out waiting for frames")
    finally:
        playback.close()
        player.terminate()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("mpv"), "ffmpeg and mpv are required")
class PlaybackTests(unittest.TestCase):
    def test_sbs_and_software_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sbs.mp4"
            _ffmpeg(
                "-f", "lavfi", "-i", "color=c=red:s=320x180:r=10:d=1",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=10:d=1",
                "-filter_complex", "hstack",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
            )
            stats, active = _play(path, 640, 180, "sbs-left")
            self.assertGreaterEqual(stats.decoded_width, 640)
            self.assertTrue(stats.time_pos > 0 or stats.decoded_width > 0)
            # This environment has no Iris node. v4l2/vaapi either works or falls back.
            self.assertIn(active.hwdec, {"", "no", "vaapi", "vaapi-copy", "nvdec", "nvdec-copy", "vulkan", "vulkan-copy", "auto-copy", "v4l2m2m-copy"})

    def test_forced_v4l2_falls_back_without_a_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.mp4"
            _ffmpeg(
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10",
                "-t", "0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
            )
            os.environ["FRAMEPLAYER_HWDEC"] = "v4l2m2m-copy"
            try:
                stats, active = _play(path, 320, 180)
            finally:
                os.environ.pop("FRAMEPLAYER_HWDEC", None)
            self.assertGreater(stats.decoded_width, 0)
            self.assertFalse(active.hardware)
            self.assertTrue(active.fallback)

    def test_8k_frame_decodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "8k.mp4"
            _ffmpeg(
                "-f", "lavfi", "-i", "testsrc2=size=7680x4320:rate=2:duration=1",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an", str(path),
            )
            stats, _active = _play(path, 7680, 4320)
            self.assertEqual(stats.decoded_width, 7680)
            self.assertEqual(stats.decoded_height, 4320)


if __name__ == "__main__":
    unittest.main()
