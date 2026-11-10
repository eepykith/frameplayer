"""libmpv playback shared by the window and the headless play test."""

from __future__ import annotations

import locale
import os
from collections import deque

try:
    locale.setlocale(locale.LC_NUMERIC, "C")
except locale.Error:
    pass
from dataclasses import dataclass

from frameplayer.hwdec import ActiveDecoder, HwdecPlan, describe_active
from frameplayer.paths import log_path
from frameplayer.presentation import Presentation, crop_option
from frameplayer.resolve import PlayItem

try:
    import mpv
except ImportError:  # pragma: no cover - exercised when the package is missing
    mpv = None


@dataclass
class Stats:
    hwdec: str = ""
    codec: str = ""
    pixfmt: str = ""
    width: int = 0
    height: int = 0
    decoded_width: int = 0
    decoded_height: int = 0
    container_fps: float = 0
    estimated_fps: float = 0
    dropped: int = 0
    decoder_dropped: int = 0
    time_pos: float = 0
    duration: float = 0
    paused: bool = False
    presentation: str = ""

    def as_dict(self) -> dict:
        return {
            "hwdec": self.hwdec,
            "codec": self.codec,
            "pixfmt": self.pixfmt,
            "width": self.width,
            "height": self.height,
            "decoded_width": self.decoded_width,
            "decoded_height": self.decoded_height,
            "container_fps": self.container_fps,
            "estimated_fps": self.estimated_fps,
            "dropped": self.dropped,
            "decoder_dropped": self.decoder_dropped,
            "time_pos": self.time_pos,
            "duration": self.duration,
            "paused": self.paused,
            "presentation": self.presentation,
        }


def open_mpv(*, vo: str | None, ao: str, hwdec: str, log_handler) -> "mpv.MPV":
    # mpv mis-parses numbers and can crash if LC_NUMERIC is not C.
    # Qt may reset the locale when the application starts, so set it here.
    try:
        locale.setlocale(locale.LC_NUMERIC, "C")
    except locale.Error:
        pass
    if mpv is None:
        raise RuntimeError("python-mpv is not installed, or libmpv is missing.")
    options = {
        "hwdec": hwdec or "no",
        "ao": ao,
        "keep_open": "yes",
        "idle": "yes",
        "osd_level": "0",
        "input_default_bindings": "no",
        "input_vo_keyboard": "no",
        "ytdl": "no",
        "cache": "yes",
        "hr_seek": "yes",
        "network_timeout": "30",
        "hwdec_codecs": "h264,hevc,vp9,av1,vp8,mpeg2video,mpeg4",
        "log_handler": log_handler,
        "loglevel": "info",
    }
    if vo:
        options["vo"] = vo
    try:
        return mpv.MPV(**options)
    except Exception:
        options.pop("ytdl", None)
        if options.get("ao") not in {None, "null"}:
            options["ao"] = "null"
            return mpv.MPV(**options)
        raise


def _prop(player, name: str, default=None):
    try:
        value = player._get_property(name)
    except Exception:
        return default
    return default if value is None else value


def _number(value, default: float = 0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class Playback:
    """Load a resolved stream, watch decoder failure, and read stats."""

    def __init__(self, player, plan: HwdecPlan) -> None:
        self.player = player
        self.plan = plan
        self.item: PlayItem | None = None
        self.presentation = Presentation()
        self.frame_width = 0
        self.frame_height = 0
        self.software_forced = False
        self.error_flag = False
        self.eof = False
        self.fatal = False
        self.log_lines: deque[str] = deque(maxlen=200)
        self._log_file = None
        try:
            path = log_path()
            if path.exists() and path.stat().st_size > 2_000_000:
                path.write_text("", encoding="utf-8")
            self._log_file = path.open("a", encoding="utf-8")
        except OSError:
            self._log_file = None
        player.event_callback("end-file")(self._on_end_file)

    def log_handler(self, level: str, prefix: str, text: str) -> None:
        line = f"{level} {prefix}: {text}".rstrip()
        self.log_lines.append(line)
        if self._log_file is not None:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                pass

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def _on_end_file(self, event) -> None:
        # Runs on mpv's thread. Only touch plain attributes.
        data = getattr(event, "data", None)
        reason = getattr(data, "reason", None)
        if reason == 4:
            self.error_flag = True
        elif reason == 0:
            self.eof = True

    def configure_cache(self, item: PlayItem) -> None:
        if item.is_live:
            self.player.demuxer_max_bytes = "128MiB"
            self.player.cache_secs = "4"
            return
        longest = max(item.width, item.height)
        if longest >= 6000:
            self.player.demuxer_max_bytes = "1024MiB"
            self.player.demuxer_max_back_bytes = "256MiB"
        elif longest >= 3000:
            self.player.demuxer_max_bytes = "512MiB"
            self.player.demuxer_max_back_bytes = "128MiB"
        else:
            self.player.demuxer_max_bytes = "256MiB"

    def apply_headers(self, item: PlayItem) -> None:
        headers = dict(item.headers or {})
        user_agent = headers.pop("User-Agent", None) or headers.pop("user-agent", None)
        referer = headers.pop("Referer", None) or headers.pop("referer", None)
        if user_agent:
            self.player.user_agent = user_agent
        if referer:
            self.player.referrer = referer
        fields = [f"{key}: {value}" for key, value in headers.items()]
        try:
            self.player._set_property("http-header-fields", fields)
        except Exception:
            pass

    def load(self, item: PlayItem) -> None:
        self.item = item
        self.error_flag = False
        self.fatal = False
        self.eof = False
        self.frame_width = 0
        self.frame_height = 0
        try:
            self.configure_cache(item)
            self.apply_headers(item)
        except Exception:
            self.fatal = True
            return
        try:
            if item.title:
                self.player.force_media_title = item.title
            if item.audio_url:
                self.player._set_property("audio-files", [item.audio_url])
            else:
                self.player._set_property("audio-files", [])
            self.player.loadfile(item.video_url)
        except Exception:
            self.fatal = True

    def set_hwdec(self, chain: str) -> None:
        try:
            self.player.hwdec = chain or "no"
        except Exception:
            self.fatal = True

    def use_software_and_reload(self) -> bool:
        if self.software_forced or self.item is None:
            self.fatal = True
            return False
        self.software_forced = True
        self.set_hwdec("no")
        self.load(self.item)
        return True

    def poll_fallback(self) -> bool:
        if not self.error_flag:
            return False
        self.error_flag = False
        if self.plan.mpv_hwdec in {"", "no"} or self.software_forced:
            self.fatal = True
            return False
        return self.use_software_and_reload()

    def capture_frame_size(self) -> tuple[int, int]:
        if self.frame_width and self.frame_height:
            return self.frame_width, self.frame_height
        width = int(_number(_prop(self.player, "video-dec-params/w", 0)))
        height = int(_number(_prop(self.player, "video-dec-params/h", 0)))
        if width <= 0 or height <= 0:
            width = int(_number(_prop(self.player, "width", 0)))
            height = int(_number(_prop(self.player, "height", 0)))
        if width > 0 and height > 0:
            self.frame_width = width
            self.frame_height = height
        return self.frame_width, self.frame_height

    def apply_presentation(self, presentation: Presentation) -> None:
        self.presentation = presentation
        width, height = self.capture_frame_size()
        crop = "" if presentation.spherical else crop_option(presentation.layout, width, height)
        try:
            self.player.video_crop = crop
        except Exception:
            if crop:
                self.log_lines.append(f"error crop: could not set video-crop={crop}")

    def snapshot(self) -> Stats:
        width, height = self.capture_frame_size()
        return Stats(
            hwdec=str(_prop(self.player, "hwdec-current", "") or ""),
            codec=str(_prop(self.player, "video-codec", "") or ""),
            pixfmt=str(
                _prop(self.player, "video-params/pixelformat", "")
                or _prop(self.player, "video-format", "")
                or ""
            ),
            width=int(_number(_prop(self.player, "width", 0))),
            height=int(_number(_prop(self.player, "height", 0))),
            decoded_width=width,
            decoded_height=height,
            container_fps=_number(_prop(self.player, "container-fps", 0)),
            estimated_fps=_number(_prop(self.player, "estimated-vf-fps", 0)),
            dropped=int(_number(_prop(self.player, "frame-drop-count", 0))),
            decoder_dropped=int(_number(_prop(self.player, "decoder-frame-drop-count", 0))),
            time_pos=_number(_prop(self.player, "time-pos", 0)),
            duration=_number(_prop(self.player, "duration", 0)),
            paused=bool(_prop(self.player, "pause", False)),
            presentation=self.presentation.label(),
        )

    def active(self) -> ActiveDecoder:
        stats = self.snapshot()
        return describe_active(
            self.plan,
            "no" if self.software_forced else stats.hwdec,
            forced_software=self.software_forced,
            codec=stats.codec,
            width=stats.decoded_width,
            height=stats.decoded_height,
        )

    def last_error_line(self) -> str:
        for line in reversed(self.log_lines):
            lowered = line.lower()
            if "error" in lowered or "failed" in lowered or "cannot" in lowered:
                return line
        return "Playback failed."


def start_session(*, vo: str | None, ao: str, plan: HwdecPlan, enabled: bool) -> tuple[object, Playback]:
    holder: dict[str, Playback] = {}

    def handler(level, prefix, text):
        playback = holder.get("playback")
        if playback is not None:
            playback.log_handler(level, prefix, text)

    player = open_mpv(vo=vo, ao=ao, hwdec=hwdec_for(plan, enabled), log_handler=handler)
    playback = Playback(player, plan)
    holder["playback"] = playback
    return player, playback


def hwdec_for(plan: HwdecPlan, enabled: bool) -> str:
    if not enabled:
        return "no"
    override = os.environ.get("FRAMEPLAYER_HWDEC", "").strip()
    if override:
        return "no" if override == "no" else override
    return plan.mpv_hwdec
