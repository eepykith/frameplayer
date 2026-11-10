"""Pick a hardware decoder for Steam Frame and fall back when it cannot run.

Steam Frame is a Snapdragon 8 Gen 3 (SM8650, Adreno 750) running SteamOS.
Its video engine is Qualcomm Iris, exposed as a stateful V4L2 mem2mem decoder
(typically ``/dev/video-dec0``, driver name ``qcom-iris-decoder``). Valve's
current driver advertises H.264, HEVC, and VP9 up to 8192×8192. It does not
advertise AV1, even though the VPU firmware contains an AV1 decoder. Adreno 750
under Turnip does not expose Vulkan Video decode, and SteamOS does not ship a
VA-API driver for Iris.

Upstream FFmpeg cannot export Iris frames as DRM-PRIME, so mpv's zero-copy
``v4l2m2m`` path is not usable. ``v4l2m2m-copy`` is the path that works: Iris
decodes the bitstream, then the NV12 frame is copied into the GL window.
That copy is still hardware decode. The CPU is not running the codec.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

IRIS_MAX_DIMENSION = 8192

# Preferred mpv hwdec APIs, most specific first. Copy-back modes are listed
# before direct modes because the embedded OpenGL renderer always accepts them.
V4L2_CHAIN = ("v4l2m2m-copy", "v4l2m2m")
VAAPI_CHAIN = ("vaapi-copy", "vaapi")
NVDEC_CHAIN = ("nvdec-copy", "nvdec")
VULKAN_CHAIN = ("vulkan-copy", "vulkan")

_CODEC_FAMILY = {
    "avc": "h264",
    "avc1": "h264",
    "avc3": "h264",
    "h264": "h264",
    "hev1": "hevc",
    "hvc1": "hevc",
    "hevc": "hevc",
    "h265": "hevc",
    "vp09": "vp9",
    "vp9": "vp9",
    "vp90": "vp9",
    "av01": "av1",
    "av1": "av1",
    "vp80": "vp8",
    "vp8": "vp8",
}

_FOURCC_FAMILY = {
    "H264": "h264",
    "AVC1": "h264",
    "HEVC": "hevc",
    "H265": "hevc",
    "HEV1": "hevc",
    "VP90": "vp9",
    "VP09": "vp9",
    "VP9": "vp9",
    "VP80": "vp8",
    "AV01": "av1",
    "AV1": "av1",
}

_FOURCC_RE = re.compile(r"'([A-Z0-9]{4})'")


def codec_family(name: str | None) -> str:
    """Collapse container codec tags (``avc1``, ``hev1.1``, ``vp09``) to a family."""
    if not name:
        return ""
    token = name.strip().lower().split()[0].split(".")[0].split("/")[0]
    if token in _CODEC_FAMILY:
        return _CODEC_FAMILY[token]
    for key, family in _CODEC_FAMILY.items():
        if token.startswith(key):
            return family
    return token


def exceeds_iris(width: int | None, height: int | None) -> bool:
    return (width or 0) > IRIS_MAX_DIMENSION or (height or 0) > IRIS_MAX_DIMENSION


def resolution_tier(width: int | None, height: int | None) -> str:
    longest = max(width or 0, height or 0)
    if longest >= 6000:
        return "8K"
    if longest >= 3400:
        return "4K"
    if longest >= 2400:
        return "1440p"
    if longest >= 1600:
        return "1080p"
    if longest >= 1100:
        return "720p"
    if longest >= 700:
        return "480p"
    if longest <= 0:
        return ""
    return f"{height}p" if height else f"{longest}px"


@dataclass(frozen=True)
class DecoderDevice:
    path: str
    name: str
    driver: str  # iris, venus, other
    codecs: frozenset[str]
    formats_known: bool


@dataclass(frozen=True)
class HwdecPlan:
    platform: str
    chain: tuple[str, ...]
    devices: tuple[DecoderDevice, ...]
    hw_codecs: frozenset[str]
    reason: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def mpv_hwdec(self) -> str:
        return ",".join(self.chain) if self.chain else "no"

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "chain": list(self.chain),
            "mpv_hwdec": self.mpv_hwdec,
            "hw_codecs": sorted(self.hw_codecs),
            "reason": self.reason,
            "iris_max_dimension": IRIS_MAX_DIMENSION,
            "devices": [
                {
                    "path": device.path,
                    "name": device.name,
                    "driver": device.driver,
                    "codecs": sorted(device.codecs),
                    "formats_known": device.formats_known,
                }
                for device in self.devices
            ],
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ActiveDecoder:
    hardware: bool
    fallback: bool
    hwdec: str
    summary: str
    detail: str


def parse_v4l2_formats(text: str) -> frozenset[str]:
    """Parse ``v4l2-ctl --list-formats-out`` and return codec families."""
    found: set[str] = set()
    for match in _FOURCC_RE.finditer(text or ""):
        family = _FOURCC_FAMILY.get(match.group(1))
        if family:
            found.add(family)
    return frozenset(found)


def parse_hwdec_help(text: str) -> frozenset[str]:
    """Parse ``mpv --hwdec=help`` into API names such as ``v4l2m2m-copy``."""
    names: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("valid"):
            continue
        token = line.split()[0]
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", token):
            names.add(token)
    return frozenset(names)


def device_from_identity(path: str, name: str, codecs: frozenset[str], formats_known: bool) -> DecoderDevice | None:
    lowered = f"{name} {path}".lower()
    if "iris" in lowered or "video-dec" in lowered:
        driver = "iris"
    elif "venus" in lowered:
        driver = "venus"
    elif codecs:
        driver = "other"
    else:
        return None
    resolved = codecs
    known = formats_known
    if driver in {"iris", "venus"} and not codecs:
        # Valve's Frame driver advertises these three. Used only when the
        # format list could not be read (no v4l2-ctl, or a permission error).
        resolved = frozenset({"h264", "hevc", "vp9"})
        known = False
    return DecoderDevice(path=path, name=name or path, driver=driver, codecs=resolved, formats_known=known)


def _filter_chain(chain: tuple[str, ...], compiled: frozenset[str] | None) -> tuple[str, ...]:
    if compiled is None:
        return chain
    return tuple(api for api in chain if api in compiled)


def select_hwdec_plan(
    devices: list[DecoderDevice] | tuple[DecoderDevice, ...],
    *,
    have_vaapi: bool = False,
    have_nvdec: bool = False,
    have_vulkan: bool = False,
    compiled: frozenset[str] | None = None,
) -> HwdecPlan:
    """Build an ordered mpv ``hwdec`` list from what the machine actually has."""
    notes = (
        "Iris is a stateful V4L2 decoder. mpv reaches it through v4l2m2m-copy.",
        "H.264, HEVC, and VP9 up to 8192px are the Steam Frame hardware codecs.",
        "AV1 is software on current SteamOS builds: the driver does not list it.",
        "Vulkan Video is not available on Adreno 750 / Turnip.",
        "VA-API is not shipped on SteamOS. It is used on PCs when present.",
    )
    qcom = [device for device in devices if device.driver in {"iris", "venus"}]
    other = [device for device in devices if device.driver == "other"]
    chain: list[str] = []
    if qcom:
        chain.extend(V4L2_CHAIN)
        if have_vaapi:
            chain.extend(VAAPI_CHAIN)
        if have_vulkan:
            chain.extend(VULKAN_CHAIN)
        chosen = _filter_chain(tuple(chain), compiled)
        if not chosen:
            chosen = _filter_chain(("auto-copy",), compiled) or ("no",)
        codecs: set[str] = set()
        for device in qcom:
            codecs.update(device.codecs)
        platform = "steam-frame" if any(device.driver == "iris" for device in qcom) else "qualcomm-v4l2"
        names = ", ".join(f"{device.name} ({device.path})" for device in qcom)
        return HwdecPlan(
            platform=platform,
            chain=chosen,
            devices=tuple(qcom),
            hw_codecs=frozenset(codecs),
            reason=f"Qualcomm decoder node: {names}",
            notes=notes,
        )
    if other:
        chain.extend(V4L2_CHAIN)
    if have_vaapi:
        chain.extend(VAAPI_CHAIN)
    if have_nvdec:
        chain.extend(NVDEC_CHAIN)
    if have_vulkan:
        chain.extend(VULKAN_CHAIN)
    chosen = _filter_chain(tuple(chain), compiled)
    if not chosen:
        chosen = _filter_chain(("auto-copy",), compiled)
    codecs = set()
    for device in other:
        codecs.update(device.codecs)
    if have_vaapi or have_nvdec or have_vulkan:
        codecs.update({"h264", "hevc", "vp9", "av1"})
    if other and not (have_vaapi or have_nvdec or have_vulkan):
        platform = "v4l2"
        reason = "Generic V4L2 mem2mem decoder"
    elif have_vaapi:
        platform = "vaapi"
        reason = "VA-API is available; no Qualcomm Iris node was found"
    elif have_nvdec:
        platform = "nvdec"
        reason = "NVDEC is available; no Qualcomm Iris node was found"
    elif have_vulkan:
        platform = "vulkan"
        reason = "Vulkan Video is available; no Qualcomm Iris node was found"
    elif chosen:
        platform = "auto"
        reason = "No decoder node was identified; mpv will probe copy-back hardware decode"
    else:
        platform = "software"
        reason = "No hardware decoder API is compiled into this mpv"
        chosen = ()
    return HwdecPlan(
        platform=platform,
        chain=chosen,
        devices=tuple(other),
        hw_codecs=frozenset(codecs),
        reason=reason,
        notes=notes,
    )


def codec_is_hardware(codec: str | None, width: int | None, height: int | None, plan: HwdecPlan) -> bool:
    if not plan.chain or plan.mpv_hwdec in {"", "no"} or not plan.hw_codecs:
        return False
    family = codec_family(codec)
    if family not in plan.hw_codecs:
        return False
    if plan.platform in {"steam-frame", "qualcomm-v4l2"} and exceeds_iris(width, height):
        return False
    if not family:
        return False
    return True


def describe_active(
    plan: HwdecPlan,
    hwdec_current: str | None,
    *,
    forced_software: bool = False,
    codec: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> ActiveDecoder:
    current = (hwdec_current or "").strip().lower()
    requested = bool(plan.chain) and plan.mpv_hwdec not in {"", "no"}
    if forced_software:
        current = "no"
    hardware = current not in {"", "no", "none"}
    if hardware:
        where = "Iris copy-back" if "v4l2m2m" in current else current
        summary = f"Hardware · {current}"
        detail = where
        if plan.devices:
            device = plan.devices[0]
            detail = f"{device.name} · {device.path} · {current}"
        return ActiveDecoder(True, False, current, summary, detail)
    if not requested:
        return ActiveDecoder(False, False, "no", "Software", "Hardware decode is off")
    detail = _fallback_detail(plan, codec, width, height)
    summary = "Software fallback"
    return ActiveDecoder(False, True, "no", summary, detail)


def _fallback_detail(plan: HwdecPlan, codec: str | None, width: int | None, height: int | None) -> str:
    family = codec_family(codec)
    if plan.platform in {"steam-frame", "qualcomm-v4l2"}:
        if exceeds_iris(width, height):
            return "This frame is larger than Iris's 8192-pixel limit, so the CPU is decoding it."
        if family == "av1":
            return "Valve's Iris driver does not advertise AV1. This stream is decoded on the CPU."
        if family and plan.hw_codecs and family not in plan.hw_codecs:
            return f"{family.upper()} is not advertised by the hardware decoder."
        return "Iris did not accept this stream. Playback continued on the CPU."
    return "The hardware decoder did not accept this stream. Playback continued on the CPU."


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def scan_sysfs_devices(sysfs: Path = Path("/sys/class/video4linux"), dev: Path = Path("/dev")) -> list[tuple[str, str]]:
    """Return ``(device node, name)`` pairs for video4linux nodes."""
    found: list[tuple[str, str]] = []
    if not sysfs.is_dir():
        return found
    for node in sorted(sysfs.iterdir()):
        if not node.name.startswith("video"):
            continue
        name = _read_text(node / "name")
        device_path = str(dev / node.name)
        found.append((device_path, name))
    alias = dev / "video-dec0"
    if alias.exists():
        target = str(alias.resolve())
        if not any(path == target or path == str(alias) for path, _name in found):
            found.append((str(alias), "qcom-iris-decoder"))
    return found


def _run(args: list[str], timeout: float) -> str:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return f"{completed.stdout}\n{completed.stderr}"


def probe_v4l2_codecs(device_path: str) -> tuple[frozenset[str], bool]:
    text = _run(["v4l2-ctl", "-d", device_path, "--list-formats-out"], timeout=2.5)
    if not text.strip():
        return frozenset(), False
    return parse_v4l2_formats(text), True


def probe_compiled_hwdecs() -> frozenset[str] | None:
    text = _run(["mpv", "--hwdec=help"], timeout=3)
    if not text.strip():
        return None
    return parse_hwdec_help(text)


def _command_ok(args: list[str], timeout: float) -> bool:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def probe_vaapi() -> bool:
    if _command_ok(["vainfo"], 2.5):
        return True
    return False


def probe_nvdec() -> bool:
    return Path("/dev/nvidiactl").exists() or Path("/dev/nvidia0").exists()


def probe_vulkan_video() -> bool:
    if os.environ.get("FRAMEPLAYER_PROBE_VULKAN") != "1":
        return False
    text = _run(["vulkaninfo", "--summary"], timeout=4)
    return "VK_KHR_video_decode_queue" in text


def probe_hwdec() -> HwdecPlan:
    """Inspect this machine and return the hwdec plan the player will use."""
    devices: list[DecoderDevice] = []
    for path, name in scan_sysfs_devices():
        interesting = any(token in f"{name} {path}".lower() for token in ("iris", "venus", "video-dec", "m2m", "decoder"))
        codecs: frozenset[str] = frozenset()
        known = False
        if interesting or "iris" in name.lower() or "venus" in name.lower():
            codecs, known = probe_v4l2_codecs(path)
        device = device_from_identity(path, name, codecs, known)
        if device is not None:
            devices.append(device)
    override = os.environ.get("FRAMEPLAYER_HWDEC", "").strip()
    plan = select_hwdec_plan(
        devices,
        have_vaapi=probe_vaapi(),
        have_nvdec=probe_nvdec(),
        have_vulkan=probe_vulkan_video(),
        compiled=probe_compiled_hwdecs(),
    )
    if override:
        chain = tuple(part.strip() for part in override.split(",") if part.strip() and part.strip() != "no")
        if override == "no":
            chain = ()
        plan = HwdecPlan(
            platform=plan.platform if chain else "software",
            chain=chain,
            devices=plan.devices,
            hw_codecs=plan.hw_codecs if chain else frozenset(),
            reason=f"FRAMEPLAYER_HWDEC={override}",
            notes=plan.notes,
        )
    return plan
