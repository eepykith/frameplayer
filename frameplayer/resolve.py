"""Turn a URL into a list of playable streams, including 8K and stereo renditions."""

from __future__ import annotations

import shutil
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from frameplayer.hwdec import HwdecPlan, codec_family, codec_is_hardware, exceeds_iris, resolution_tier
from frameplayer.presentation import Presentation, detect_presentation

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 FramePlayer/0.1"
)

SAMPLE_HLS = (
    "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
    "img_bipbop_adv_example_fmp4/master.m3u8"
)
SAMPLE_YOUTUBE = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

_DIRECT_SUFFIXES = (".m3u8", ".mpd", ".mp4", ".webm", ".mkv", ".mov", ".ts", ".m4v", ".avi", ".m4s")
_DRM_HOSTS = (
    ("netflix.com", "Netflix"),
    ("disneyplus.com", "Disney+"),
    ("primevideo.com", "Prime Video"),
    ("hulu.com", "Hulu"),
    ("max.com", "Max"),
    ("hbomax.com", "Max"),
    ("tv.apple.com", "Apple TV"),
    ("paramountplus.com", "Paramount+"),
    ("peacocktv.com", "Peacock"),
)


class ResolveError(Exception):
    def __init__(self, message: str, *, drm: bool = False) -> None:
        super().__init__(message)
        self.drm = drm


@dataclass
class PlayItem:
    video_url: str
    audio_url: str | None
    title: str
    vcodec: str
    width: int
    height: int
    fps: float | None
    tbr: float | None
    headers: dict[str, str]
    is_live: bool
    page_url: str
    format_id: str
    note: str
    hw: bool
    over_iris: bool
    source: str

    def chip_text(self) -> str:
        tier = resolution_tier(self.width, self.height) or "Auto"
        family = codec_family(self.vcodec) or "video"
        mark = "HW" if self.hw else "SW"
        if self.over_iris:
            mark = "SW · over 8192"
        lines = [f"{tier} · {mark}", family.upper()]
        if self.fps:
            lines.append(f"{self.fps:g} fps")
        return "\n".join(lines)

    def menu_label(self) -> str:
        tier = resolution_tier(self.width, self.height) or "source"
        family = (codec_family(self.vcodec) or "video").upper()
        mark = "hardware" if self.hw else "software"
        return f"{tier} {family} ({mark})"


@dataclass
class Resolved:
    page_url: str
    title: str
    is_live: bool
    items: list[PlayItem]
    selected_index: int
    message: str = ""
    detected: Presentation = field(default_factory=Presentation)

    @property
    def selected(self) -> PlayItem:
        return self.items[self.selected_index]


def drm_service(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.lower()
    for domain, name in _DRM_HOSTS:
        if host == domain or host.endswith("." + domain):
            return name
    if host.endswith("amazon.com") and any(part in path for part in ("/gp/video", "/primevideo", "/video")):
        return "Prime Video"
    return None


def classify_url(url: str) -> str:
    if url.startswith("/") or url.startswith("file:"):
        return "file"
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(_DIRECT_SUFFIXES) or ".m3u8" in parsed.query.lower():
        return "direct"
    return "extractor"


def _headers(raw: dict | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in (raw or {}).items():
        if value is None:
            continue
        headers[str(key)] = str(value)
    headers.setdefault("User-Agent", USER_AGENT)
    return headers


def _float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def rank_key(item: PlayItem, ceiling: int, prefer_hw: bool) -> tuple:
    """Higher sorts first. ``ceiling`` is a max video height; 0 means no cap."""
    height = item.height or 0
    fits = 1 if ceiling <= 0 or height <= ceiling or height == 0 else 0
    hw = 1 if prefer_hw and item.hw else 0
    family = codec_family(item.vcodec)
    # HEVC and H.264 are the most reliable Iris codecs. VP9 is also hardware.
    # AV1 is last because current SteamOS does not decode it in hardware.
    codec_score = {"hevc": 5, "h264": 4, "vp9": 3, "vp8": 2, "av1": 1}.get(family, 0)
    return (fits, hw, height, item.fps or 0, codec_score if item.hw else 0, item.tbr or 0)


def order_items(items: list[PlayItem], ceiling: int, prefer_hw: bool) -> list[PlayItem]:
    return sorted(items, key=lambda item: rank_key(item, ceiling, prefer_hw), reverse=True)


def _make_item(
    *,
    video_url: str,
    audio_url: str | None,
    title: str,
    vcodec: str,
    width: int,
    height: int,
    fps: float | None,
    tbr: float | None,
    headers: dict[str, str],
    is_live: bool,
    page_url: str,
    format_id: str,
    note: str,
    source: str,
    plan: HwdecPlan,
) -> PlayItem:
    over = exceeds_iris(width, height)
    return PlayItem(
        video_url=video_url,
        audio_url=audio_url,
        title=title,
        vcodec=vcodec or "",
        width=width,
        height=height,
        fps=fps,
        tbr=tbr,
        headers=headers,
        is_live=is_live,
        page_url=page_url,
        format_id=format_id,
        note=note,
        hw=codec_is_hardware(vcodec, width, height, plan),
        over_iris=over,
        source=source,
    )


def parse_m3u8(text: str, base_url: str, *, title: str, plan: HwdecPlan, headers: dict[str, str]) -> list[PlayItem]:
    """Parse an HLS master playlist. A media playlist becomes one item."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items: list[PlayItem] = []
    pending: dict | None = None
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF"):
            pending = _stream_inf(line)
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        url = urljoin(base_url, line)
        width, height = pending["resolution"]
        codecs = pending["codecs"]
        vcodec = _video_codec(codecs)
        items.append(
            _make_item(
                video_url=url,
                audio_url=None,
                title=title or "HLS stream",
                vcodec=vcodec,
                width=width,
                height=height,
                fps=pending["fps"],
                tbr=(pending["bandwidth"] / 1000) if pending["bandwidth"] else None,
                headers=dict(headers),
                is_live=False,
                page_url=base_url,
                format_id=f"hls-{width}x{height}-{vcodec or 'av'}",
                note="HLS",
                source="hls",
                plan=plan,
            )
        )
        pending = None
    if items:
        return items
    if any(line.startswith("#EXTM3U") or line.startswith("#EXTINF") for line in lines):
        return [
            _make_item(
                video_url=base_url,
                audio_url=None,
                title=title or "HLS stream",
                vcodec="",
                width=0,
                height=0,
                fps=None,
                tbr=None,
                headers=dict(headers),
                is_live=False,
                page_url=base_url,
                format_id="hls",
                note="HLS",
                source="hls",
                plan=plan,
            )
        ]
    return []


def _stream_inf(line: str) -> dict:
    attrs = line.split(":", 1)[-1]
    bandwidth = 0
    width = height = 0
    fps = None
    codecs = ""
    for part in _split_attr(attrs):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        value = value.strip().strip('"')
        key = key.strip().upper()
        if key == "BANDWIDTH":
            bandwidth = _int(value)
        elif key == "RESOLUTION" and "x" in value.lower():
            left, right = value.lower().split("x", 1)
            width, height = _int(left), _int(right)
        elif key == "FRAME-RATE":
            fps = _float(value)
        elif key == "CODECS":
            codecs = value
    return {"bandwidth": bandwidth, "resolution": (width, height), "fps": fps, "codecs": codecs}


def _split_attr(attrs: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    for char in attrs:
        if char == '"':
            quoted = not quoted
            current.append(char)
        elif char == "," and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _video_codec(codecs: str) -> str:
    for piece in codecs.split(","):
        token = piece.strip()
        if not token:
            continue
        if token.lower().startswith("mp4a") or token.lower().startswith("ac-3") or token.lower().startswith("ec-3"):
            continue
        return token
    return ""


def _fetch(url: str, headers: dict[str, str], timeout: float = 20) -> str:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            data = response.read(2_000_000)
            charset = response.headers.get_content_charset() or "utf-8"
            return data.decode(charset, errors="replace")
    except urllib.error.URLError as exc:
        raise ResolveError(f"Could not open the playlist: {exc.reason}") from exc


def _js_runtimes() -> dict:
    runtimes = {}
    deno = shutil.which("deno")
    node = shutil.which("node")
    if deno:
        runtimes["deno"] = {"path": deno}
    if node:
        runtimes["node"] = {"path": node}
    return runtimes or {"node": {}}


def _best_audio(formats: list[dict]) -> dict | None:
    audio = []
    for fmt in formats:
        vcodec = fmt.get("vcodec")
        if vcodec not in {None, "none", ""}:
            continue
        if not fmt.get("url") and not fmt.get("manifest_url"):
            continue
        if (fmt.get("format_note") or "").lower().find("storyboard") >= 0:
            continue
        audio.append(fmt)
    if not audio:
        return None
    audio.sort(key=lambda fmt: (_float(fmt.get("abr")) or _float(fmt.get("tbr")) or 0), reverse=True)
    return audio[0]


def _video_formats(info: dict) -> list[dict]:
    formats = []
    for fmt in info.get("formats") or []:
        vcodec = fmt.get("vcodec")
        if vcodec in {None, "none", ""}:
            continue
        url = fmt.get("url") or fmt.get("manifest_url")
        if not url:
            continue
        note = (fmt.get("format_note") or "").lower()
        if "storyboard" in note or "thumbnail" in note:
            continue
        ext = (fmt.get("ext") or "").lower()
        if ext in {"jpg", "png", "webp", "mhtml"}:
            continue
        formats.append(fmt)
    return formats


def items_from_info(info: dict, plan: HwdecPlan, *, page_url: str) -> tuple[list[PlayItem], str]:
    if info.get("_type") == "playlist" or (info.get("entries") and not info.get("formats")):
        entries = [entry for entry in info.get("entries") or [] if isinstance(entry, dict)]
        if not entries:
            raise ResolveError("That playlist has no videos.")
        first = entries[0]
        if first.get("formats"):
            info = first
        else:
            raise ResolveError("Open a single video URL. This playlist did not include stream formats.")
        message = "Playing the first video in the playlist."
    else:
        message = ""
    title = info.get("title") or page_url
    is_live = bool(info.get("is_live"))
    audio = _best_audio(info.get("formats") or [])
    audio_url = None
    if audio:
        audio_url = audio.get("url") or audio.get("manifest_url")
    items: list[PlayItem] = []
    for fmt in _video_formats(info):
        width, height = _int(fmt.get("width")), _int(fmt.get("height"))
        vcodec = str(fmt.get("vcodec") or "")
        tbr = _float(fmt.get("tbr")) or _float(fmt.get("vbr")) or 0
        acodec = fmt.get("acodec")
        has_audio = acodec not in {None, "none", ""}
        item_audio = None if has_audio else audio_url
        items.append(
            _make_item(
                video_url=fmt.get("url") or fmt.get("manifest_url"),
                audio_url=item_audio,
                title=title,
                vcodec=vcodec,
                width=width,
                height=height,
                fps=_float(fmt.get("fps")),
                tbr=tbr,
                headers=_headers(fmt.get("http_headers") or info.get("http_headers")),
                is_live=is_live,
                page_url=info.get("webpage_url") or page_url,
                format_id=str(fmt.get("format_id") or ""),
                note=str(fmt.get("format_note") or ""),
                source="yt-dlp",
                plan=plan,
            )
        )
    if not items:
        direct = info.get("url") or info.get("manifest_url")
        if not direct:
            raise ResolveError("No playable video format was found for that URL.")
        items.append(
            _make_item(
                video_url=direct,
                audio_url=None,
                title=title,
                vcodec=str(info.get("vcodec") or ""),
                width=_int(info.get("width")),
                height=_int(info.get("height")),
                fps=_float(info.get("fps")),
                tbr=_float(info.get("tbr")),
                headers=_headers(info.get("http_headers")),
                is_live=is_live,
                page_url=info.get("webpage_url") or page_url,
                format_id="direct",
                note="",
                source="yt-dlp",
                plan=plan,
            )
        )
    # Collapse to the best bitrate per (height, codec, fps).
    best: dict[tuple, PlayItem] = {}
    for item in items:
        key = (item.height, codec_family(item.vcodec), round(item.fps or 0))
        current = best.get(key)
        if current is None or (item.tbr or 0) > (current.tbr or 0):
            best[key] = item
    return list(best.values()), message or title


def extract_with_ytdlp(url: str) -> dict:
    try:
        import yt_dlp
    except ImportError as exc:
        raise ResolveError("yt-dlp is not installed, so site links cannot be resolved.") from exc
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "js_runtimes": _js_runtimes(),
        "remote_components": ["ejs:github"],
        "socket_timeout": 30,
        "retries": 2,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        text = str(exc)
        lowered = text.lower()
        if "drm" in lowered or "widevine" in lowered:
            raise ResolveError(
                "This video is DRM-protected. Frame Player cannot play Widevine catalogs "
                "such as Netflix, Disney+, or Max.",
                drm=True,
            ) from exc
        raise ResolveError(text or "Could not resolve that URL.") from exc
    if not isinstance(info, dict):
        raise ResolveError("The resolver returned nothing for that URL.")
    return info


def probe_media(path_or_url: str) -> dict:
    """Read width, height, spherical, and stereo metadata with ffprobe when it exists."""
    if not shutil.which("ffprobe"):
        return {}
    import json
    import subprocess

    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "v:0",
                path_or_url,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}
    streams = payload.get("streams") or []
    if not streams:
        return {}
    stream = streams[0]
    spherical = ""
    stereo = ""
    for side in stream.get("side_data_list") or []:
        kind = (side.get("side_data_type") or "").lower()
        if "spherical" in kind:
            spherical = side.get("projection") or "equirectangular"
        if "stereo" in kind:
            stereo = side.get("type") or side.get("side_data_type") or ""
    return {
        "width": _int(stream.get("width")),
        "height": _int(stream.get("height")),
        "vcodec": stream.get("codec_name") or "",
        "spherical": spherical,
        "stereo": stereo,
    }


def resolve(url: str, plan: HwdecPlan, *, ceiling: int = 4320, prefer_hw: bool = True) -> Resolved:
    cleaned = url.strip().strip("<>").strip()
    if not cleaned:
        raise ResolveError("Enter a video URL.")
    service = drm_service(cleaned)
    if service:
        raise ResolveError(
            f"Frame Player can't play {service}. That catalog is DRM-protected (Widevine), "
            "and there is no clear stream to decode. Use YouTube, Twitch, a direct HLS, DASH, "
            "or MP4 URL, or a local file.",
            drm=True,
        )
    kind = classify_url(cleaned)
    message = ""
    if kind == "file":
        path = cleaned[7:] if cleaned.startswith("file://") else cleaned
        meta = probe_media(path)
        item = _make_item(
            video_url=path,
            audio_url=None,
            title=path.rsplit("/", 1)[-1],
            vcodec=str(meta.get("vcodec") or ""),
            width=_int(meta.get("width")),
            height=_int(meta.get("height")),
            fps=None,
            tbr=None,
            headers={},
            is_live=False,
            page_url=path,
            format_id="file",
            note="",
            source="file",
            plan=plan,
        )
        items = [item]
        detected = detect_presentation(
            title=item.title,
            url=path,
            width=item.width,
            height=item.height,
            spherical=str(meta.get("spherical") or ""),
            stereo=str(meta.get("stereo") or ""),
        )
    elif kind == "direct" and urlparse(cleaned).path.lower().endswith(".m3u8"):
        headers = {"User-Agent": USER_AGENT}
        body = _fetch(cleaned, headers)
        items = parse_m3u8(body, cleaned, title="HLS stream", plan=plan, headers=headers)
        if not items:
            raise ResolveError("That HLS playlist has no video streams.")
        detected = detect_presentation(title="HLS stream", url=cleaned, width=items[0].width, height=items[0].height)
    elif kind == "direct":
        meta = probe_media(cleaned)
        item = _make_item(
            video_url=cleaned,
            audio_url=None,
            title=cleaned.rsplit("/", 1)[-1],
            vcodec=str(meta.get("vcodec") or ""),
            width=_int(meta.get("width")),
            height=_int(meta.get("height")),
            fps=None,
            tbr=None,
            headers={"User-Agent": USER_AGENT},
            is_live=cleaned.endswith(".m3u8"),
            page_url=cleaned,
            format_id="direct",
            note="DASH" if cleaned.split("?", 1)[0].lower().endswith(".mpd") else "",
            source="direct",
            plan=plan,
        )
        items = [item]
        detected = detect_presentation(
            title=item.title,
            url=cleaned,
            width=item.width,
            height=item.height,
            spherical=str(meta.get("spherical") or ""),
            stereo=str(meta.get("stereo") or ""),
        )
    else:
        info = extract_with_ytdlp(cleaned)
        items, note = items_from_info(info, plan, page_url=cleaned)
        message = note if note and note != (info.get("title") or "") else ""
        detected = detect_presentation(
            title=str(info.get("title") or ""),
            url=cleaned,
            note=" ".join(item.note for item in items),
            width=max((item.width for item in items), default=0),
            height=max((item.height for item in items), default=0),
        )
    ordered = order_items(items, ceiling, prefer_hw)
    if not ordered:
        raise ResolveError("No playable video format was found.")
    # Cap the on-screen list. The selected item is always kept.
    display = ordered[:12]
    return Resolved(
        page_url=cleaned,
        title=display[0].title,
        is_live=display[0].is_live,
        items=display,
        selected_index=0,
        message=message if isinstance(message, str) and message != display[0].title else "",
        detected=detected,
    )
