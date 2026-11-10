"""Frame Player command line."""

from __future__ import annotations

import argparse
import json
import locale
import os
import sys
import time

os.environ.setdefault("LC_NUMERIC", "C")
try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass

from frameplayer import __version__
from frameplayer.hwdec import probe_hwdec
from frameplayer.presentation import resolve_presentation
from frameplayer.resolve import resolve
from frameplayer.session import start_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="frameplayer", description="Hardware-accelerated player for Steam Frame")
    parser.add_argument("url", nargs="?", help="URL or file to open in the window")
    parser.add_argument("--probe", action="store_true", help="Print the hardware-decoder plan as JSON and exit")
    parser.add_argument("--play-test", metavar="URL", help="Decode a URL without a window and print stats JSON")
    parser.add_argument("--seconds", type=float, default=4, help="How long --play-test should play")
    parser.add_argument("--ceiling", type=int, default=4320, help="Max video height. 4320 is 8K. 0 is unlimited.")
    parser.add_argument("--hwdec", default=None, help="mpv hwdec list, or 'no' for software")
    parser.add_argument("--layout", default="auto", help="packed, sbs-left, sbs-right, tb-top, tb-bottom, or auto")
    parser.add_argument("--projection", default="auto", help="flat, equirect-360, equirect-180, or auto")
    parser.add_argument("--version", action="version", version=f"frameplayer {__version__}")
    args = parser.parse_args(argv)
    if args.hwdec:
        os.environ["FRAMEPLAYER_HWDEC"] = args.hwdec
    if args.probe:
        print(json.dumps(probe_hwdec().as_dict(), indent=2))
        return 0
    if args.play_test:
        return play_test(
            args.play_test,
            seconds=args.seconds,
            ceiling=args.ceiling,
            layout=args.layout,
            projection=args.projection,
        )
    return run_window(args.url or "")


def play_test(url: str, *, seconds: float, ceiling: int, layout: str, projection: str) -> int:
    plan = probe_hwdec()
    print(f"decoder plan: {plan.platform} {plan.mpv_hwdec}", file=sys.stderr)
    try:
        resolved = resolve(url, plan, ceiling=ceiling, prefer_hw=True)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    presentation = resolve_presentation(resolved.detected, layout, projection)
    player, playback = start_session(vo="null", ao="null", plan=plan, enabled=plan.mpv_hwdec != "no")
    payload = None
    try:
        for item in resolved.items[:4]:
            print(f"playing {item.menu_label()} {item.video_url[:120]}", file=sys.stderr)
            playback.software_forced = False
            playback.load(item)
            deadline = time.time() + max(30.0, seconds + 20.0)
            started = None
            while time.time() < deadline:
                if playback.poll_fallback():
                    print("hardware decode failed; reloaded with software", file=sys.stderr)
                stats = playback.snapshot()
                if stats.decoded_width > 0 and not presentation.spherical:
                    playback.apply_presentation(presentation)
                if playback.fatal:
                    break
                if stats.decoded_width > 0 and (stats.time_pos > 0.3 or playback.eof):
                    if started is None:
                        started = time.time()
                    if playback.eof or stats.time_pos >= seconds or (time.time() - started) >= seconds:
                        break
                time.sleep(0.2)
            stats = playback.snapshot()
            active = playback.active()
            payload = {
                "ok": stats.decoded_width > 0 and (stats.time_pos > 0.2 or playback.eof) and not playback.fatal,
                "title": item.title,
                "source": item.source,
                "requested": item.menu_label(),
                "hardware_candidate": item.hw,
                "over_iris": item.over_iris,
                "width": item.width,
                "height": item.height,
                "vcodec": item.vcodec,
                "detected": resolved.detected.label(),
                "detected_reason": resolved.detected.reason,
                "plan": plan.as_dict(),
                "active": {
                    "hardware": active.hardware,
                    "fallback": active.fallback,
                    "hwdec": active.hwdec,
                    "summary": active.summary,
                    "detail": active.detail,
                },
                "stats": stats.as_dict(),
                "error": "" if not playback.fatal else _friendly_open_error(playback.last_error_line()),
            }
            if payload["ok"]:
                break
            print(f"rendition failed: {payload['error'][:180]}", file=sys.stderr)
    finally:
        playback.close()
        player.terminate()
    print(json.dumps(payload, indent=2))
    return 0 if payload and payload["ok"] else 1


def _friendly_open_error(line: str) -> str:
    if "403" in line or "Failed to open" in line:
        return (
            "The host refused the media URL (often HTTP 403). "
            "YouTube does this from some networks unless cookies are available. " + line
        )
    return line


def run_window(url: str) -> int:
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication

    from frameplayer.window import MainWindow, load_icon

    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv)
    app.setApplicationName("Frame Player")
    app.setWindowIcon(load_icon())
    plan = probe_hwdec()
    window = MainWindow(plan, url)
    window.setStyleSheet(_style())
    window.show()
    return app.exec()


def _style() -> str:
    from frameplayer.window import STYLE

    return STYLE


if __name__ == "__main__":
    raise SystemExit(main())
