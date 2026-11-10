"""How a decoded frame is shown: flat, stereo, 360, or 180.

3D and VR streams are ordinary frames with a packing convention. An 8K
side-by-side video is one 7680-wide picture with a left eye and a right eye.
A 360 video is one equirectangular picture. Iris decodes that whole picture.
This module only decides which region to show and how to project it.

The desktop window on Steam Frame is a flat panel, so both of the user's eyes
see the same panel. Binocular stereo would require an OpenXR submit, which a
2D window cannot do. The player instead shows one eye at a time, or the packed
frame, and projects 360/180 video onto that panel with look-around controls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

LAYOUTS = ("packed", "sbs-left", "sbs-right", "tb-top", "tb-bottom")
PROJECTIONS = ("flat", "equirect-360", "equirect-180")

_LAYOUT_LABELS = {
    "packed": "Packed frame",
    "sbs-left": "Left eye",
    "sbs-right": "Right eye",
    "tb-top": "Top eye",
    "tb-bottom": "Bottom eye",
}
_PROJECTION_LABELS = {
    "flat": "Flat",
    "equirect-360": "360°",
    "equirect-180": "180°",
}


@dataclass(frozen=True)
class Presentation:
    layout: str = "packed"
    projection: str = "flat"
    reason: str = ""

    def label(self) -> str:
        parts = []
        if self.projection != "flat":
            parts.append(_PROJECTION_LABELS.get(self.projection, self.projection))
        if self.layout != "packed":
            parts.append(_LAYOUT_LABELS.get(self.layout, self.layout))
        elif self.projection == "flat":
            parts.append("2D")
        return " · ".join(parts)

    @property
    def spherical(self) -> bool:
        return self.projection in {"equirect-360", "equirect-180"}


def layout_label(layout: str) -> str:
    return _LAYOUT_LABELS.get(layout, layout)


def projection_label(projection: str) -> str:
    return _PROJECTION_LABELS.get(projection, projection)


def crop_rect(layout: str, width: int, height: int) -> tuple[int, int, int, int] | None:
    """Pixel crop ``(x, y, w, h)`` with a top-left origin, or None for the full frame."""
    if width <= 0 or height <= 0 or layout in {"packed", "mono", "auto", ""}:
        return None
    if layout == "sbs-left":
        return (0, 0, width // 2, height)
    if layout == "sbs-right":
        return (width // 2, 0, width - width // 2, height)
    if layout == "tb-top":
        return (0, 0, width, height // 2)
    if layout == "tb-bottom":
        return (0, height // 2, width, height - height // 2)
    return None


def crop_option(layout: str, width: int, height: int) -> str:
    """mpv ``video-crop`` geometry, or an empty string for the full frame."""
    rect = crop_rect(layout, width, height)
    if rect is None:
        return ""
    x, y, w, h = rect
    return f"{w}x{h}+{x}+{y}"


def _keyword_text(*parts: str | None) -> str:
    return " ".join(part.lower() for part in parts if part)


def _has_360(text: str) -> bool:
    return any(token in text for token in ("360", "equirect", "spherical", "panorama"))


def _has_180(text: str) -> bool:
    return any(token in text for token in ("vr180", "vr 180", "180 vr", "180°", "hemisphere"))


def _has_sbs(text: str) -> bool:
    return any(
        token in text
        for token in ("sbs", "side-by-side", "side by side", "left/right", "left-right", "3dhs", "half-sbs", "full-sbs")
    )


def _has_tb(text: str) -> bool:
    return any(
        token in text
        for token in ("over-under", "over under", "top-bottom", "top/bottom", "top bottom", "3dv", "ou 3d")
    )


def detect_presentation(
    *,
    title: str = "",
    url: str = "",
    note: str = "",
    width: int = 0,
    height: int = 0,
    spherical: str = "",
    stereo: str = "",
) -> Presentation:
    """Guess packing and projection. The user can always override the guess."""
    text = _keyword_text(title, url, note)
    projection = "flat"
    layout = "packed"
    reasons: list[str] = []

    spherical_name = (spherical or "").lower()
    if "equirect" in spherical_name or spherical_name == "spherical":
        projection = "equirect-360"
        reasons.append("spherical metadata")
    elif "cube" in spherical_name:
        reasons.append("cubemap metadata is shown packed")

    stereo_name = (stereo or "").lower()
    if "side" in stereo_name:
        layout = "sbs-left"
        reasons.append("stereo metadata")
    elif "top" in stereo_name or "bottom" in stereo_name or "over" in stereo_name:
        layout = "tb-top"
        reasons.append("stereo metadata")

    if projection == "flat" and _has_180(text) and not _has_360(text):
        projection = "equirect-180"
        reasons.append("title says 180 VR")
    elif projection == "flat" and _has_360(text):
        projection = "equirect-360"
        reasons.append("title says 360")

    if layout == "packed" and _has_tb(text):
        layout = "tb-top"
        reasons.append("title says over-under")
    elif layout == "packed" and _has_sbs(text):
        layout = "sbs-left"
        reasons.append("title says side-by-side")

    aspect = (width / height) if width and height else 0
    if layout == "packed" and aspect >= 3.2:
        layout = "sbs-left"
        reasons.append("very wide frame")
    elif layout == "packed" and 0 < aspect <= 0.55:
        layout = "tb-top"
        reasons.append("very tall frame")

    if "3d" in text and layout == "packed" and projection == "flat" and aspect >= 2.8:
        layout = "sbs-left"
        reasons.append("3D title with a wide frame")

    if not reasons:
        reasons.append("no 3D or VR markers")
    return Presentation(layout=layout, projection=projection, reason="; ".join(reasons))


def resolve_presentation(detected: Presentation, layout_mode: str, projection_mode: str) -> Presentation:
    """Apply the user's Auto / explicit choice on top of detection."""
    layout = detected.layout if layout_mode in {"", "auto"} else layout_mode
    projection = detected.projection if projection_mode in {"", "auto"} else projection_mode
    if layout not in LAYOUTS:
        layout = "packed"
    if projection not in PROJECTIONS:
        projection = "flat"
    return Presentation(layout=layout, projection=projection, reason=detected.reason)


def apply_layout_uv(u: float, v: float, layout: str) -> tuple[float, float]:
    """Map an eye-local UV into the packed frame. ``v`` is 0 at the top."""
    if layout == "sbs-left":
        return u * 0.5, v
    if layout == "sbs-right":
        return u * 0.5 + 0.5, v
    if layout == "tb-top":
        return u, v * 0.5
    if layout == "tb-bottom":
        return u, v * 0.5 + 0.5
    return u, v


def _rotate_yaw_pitch(x: float, y: float, z: float, yaw: float, pitch: float) -> tuple[float, float, float]:
    cp, sp = math.cos(pitch), math.sin(pitch)
    y2 = y * cp - z * sp
    z2 = y * sp + z * cp
    cy, sy = math.cos(yaw), math.sin(yaw)
    x3 = x * cy + z2 * sy
    z3 = -x * sy + z2 * cy
    return x3, y2, z3


def look_uv(
    nx: float,
    ny: float,
    *,
    yaw: float,
    pitch: float,
    fov_x: float,
    aspect: float,
    projection: str,
    layout: str,
) -> tuple[float, float] | None:
    """Project a screen position into the packed frame.

    ``nx`` and ``ny`` are in ``[-1, 1]``, ``+x`` right and ``+y`` up.
    ``yaw > 0`` looks right and ``pitch > 0`` looks up. Angles are radians.
    Returns None when a 180° source does not cover that ray.
    """
    if projection == "flat":
        u = (nx + 1) * 0.5
        v = (1 - ny) * 0.5
        return apply_layout_uv(u, v, layout)
    aspect = aspect if aspect > 0 else 1
    half = math.tan(max(0.2, fov_x) * 0.5)
    direction = (nx * half, ny * half / aspect, 1.0)
    length = math.sqrt(direction[0] ** 2 + direction[1] ** 2 + direction[2] ** 2) or 1
    x, y, z = _rotate_yaw_pitch(*(component / length for component in direction), yaw, pitch)
    lon = math.atan2(x, z)
    lat = math.asin(max(-1.0, min(1.0, y)))
    h_range = math.pi if projection == "equirect-180" else math.tau
    u = lon / h_range + 0.5
    v = 0.5 - lat / math.pi
    if projection == "equirect-180" and (u < 0.0 or u > 1.0):
        return None
    if projection == "equirect-360":
        u = u % 1.0
    if v < 0.0 or v > 1.0:
        return None
    return apply_layout_uv(u, v, layout)
