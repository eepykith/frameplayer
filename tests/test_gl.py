"""Open a window and check stereo crop plus the 360 shader."""

import locale
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("LC_NUMERIC", "C")
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

from PySide6.QtCore import QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from frameplayer.hwdec import probe_hwdec
from frameplayer.session import start_session
from frameplayer.window import STYLE, MainWindow


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _center(image):
    return image.pixelColor(image.width() // 2, image.height() // 2)


@unittest.skipUnless(shutil.which("ffmpeg") and os.environ.get("DISPLAY"), "needs ffmpeg and a display")
class GLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        fmt = QSurfaceFormat()
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        QSurfaceFormat.setDefaultFormat(fmt)
        cls.app = QApplication.instance() or QApplication([])

    def test_left_eye_and_360_center(self):
        with tempfile.TemporaryDirectory() as tmp:
            sbs = Path(tmp) / "sbs.mp4"
            sphere = Path(tmp) / "360.mp4"
            _ffmpeg(
                "-f", "lavfi", "-i", "color=c=red:s=320x180:r=8:d=2",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=8:d=2",
                "-filter_complex", "hstack",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(sbs),
            )
            _ffmpeg(
                "-f", "lavfi", "-i", "color=c=red:s=640x320:r=8:d=2",
                "-vf", "drawbox=x=280:y=40:w=80:h=240:color=green:t=fill",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(sphere),
            )
            plan = probe_hwdec()
            window = MainWindow(plan)
            window.setStyleSheet(STYLE)
            window.resize(960, 600)
            window.show()
            seen = {}

            def sample():
                image = window.video.grabFramebuffer()
                color = _center(image)
                return (color.red(), color.green(), color.blue(), window.video.shader_error)

            def watch(name, ready, then, deadline):
                def step():
                    color = sample()
                    seen[name] = color
                    if ready(color) or time.time() > deadline:
                        then()
                    else:
                        QTimer.singleShot(200, step)
                step()

            def start_sphere():
                window.eye.setCurrentIndex(window.eye.findData("packed"))
                window.projection.setCurrentIndex(window.projection.findData("equirect-360"))
                window.play_url(str(sphere))
                watch(
                    "sphere",
                    lambda color: color[1] > color[0] and color[1] > color[2] and color[1] > 40,
                    window.close,
                    time.time() + 8,
                )

            window.eye.setCurrentIndex(window.eye.findData("sbs-left"))
            window.projection.setCurrentIndex(window.projection.findData("flat"))
            window.play_url(str(sbs))
            watch(
                "sbs",
                lambda color: color[0] > 150 and color[2] < 80,
                start_sphere,
                time.time() + 8,
            )
            QTimer.singleShot(20000, window.close)
            self.app.exec()
            self.assertIn("sbs", seen, "window closed before the side-by-side frame")
            red, green, blue, _err = seen["sbs"]
            self.assertGreater(red, 150, seen)
            self.assertLess(blue, 80, seen)
            self.assertIn("sphere", seen, seen)
            sred, sgreen, sblue, shader_error = seen["sphere"]
            self.assertEqual(shader_error, "", shader_error)
            self.assertGreater(sgreen, sred, seen)
            self.assertGreater(sgreen, sblue, seen)


if __name__ == "__main__":
    unittest.main()
