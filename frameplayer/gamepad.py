"""Steam Input / evdev joystick mapping for headset controls.

Steam Frame's desktop mode aims the controller as a laser pointer. The face
buttons and sticks are still a normal Linux joystick (``/dev/input/js*``) when
Steam Input is using a gamepad template. This module reads that device and
turns edges into player actions. The laser pointer clicks the on-screen controls.
"""

from __future__ import annotations

import glob
import os
import select
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass

_EVENT = struct.Struct("=IhBB")
_BUTTON = 0x01
_AXIS = 0x02
_INIT = 0x80

BUTTONS = {
    0: "a",
    1: "b",
    2: "x",
    3: "y",
    4: "lb",
    5: "rb",
    6: "select",
    7: "start",
    8: "guide",
    9: "l3",
    10: "r3",
}
AXES = {
    0: "lx",
    1: "ly",
    2: "lt",
    3: "rx",
    4: "ry",
    5: "rt",
    6: "dpad_x",
    7: "dpad_y",
}


@dataclass(frozen=True)
class PadEvent:
    control: str
    value: float
    pressed: bool
    edge: bool


def normalize_axis(number: int, raw: int) -> float:
    """Map a joystick axis to ``-1..1``. Triggers rest at 0."""
    value = max(-1.0, min(1.0, raw / 32767.0))
    if number in {2, 5}:
        if value < -0.15:
            return max(0.0, (value + 1.0) / 2.0)
        return max(0.0, value)
    return value


def parse_js_packet(packet: bytes) -> tuple[int, int, int, int] | None:
    if len(packet) < _EVENT.size:
        return None
    time_ms, value, typ, number = _EVENT.unpack_from(packet)
    return time_ms, value, typ, number


def actions_for(
    event: PadEvent,
    *,
    chrome_visible: bool,
    spherical: bool,
    focus_is_button: bool,
) -> tuple[str, ...]:
    """Map one edge event to player actions. Axes are handled by the window tick."""
    if not event.edge or not event.pressed:
        return ()
    control = event.control
    if control == "guide":
        return ()
    if control == "a":
        if chrome_visible and focus_is_button:
            return ("activate",)
        return ("play_pause",)
    if control == "b":
        return ("back",)
    if control == "x":
        return ("toggle_stats",)
    if control == "y":
        return ("favorite",)
    if control == "start":
        return ("fullscreen",)
    if control == "select":
        return ("library",)
    if control == "l3":
        return ("mute",)
    if control == "r3":
        return ("reset_view",) if spherical else ("activate",)
    if control == "lb":
        return ("activate",) if chrome_visible else ("seek_left",)
    if control == "rb":
        return ("seek_right",)
    if control == "dpad_left":
        return ("focus_prev",) if chrome_visible else ("seek_left",)
    if control == "dpad_right":
        return ("focus_next",) if chrome_visible else ("seek_right",)
    if control == "dpad_up":
        return ("focus_prev",) if chrome_visible else ("volume_up",)
    if control == "dpad_down":
        return ("focus_next",) if chrome_visible else ("volume_down",)
    if control == "lt":
        return ("seek_left_far",)
    if control == "rt":
        return ("seek_right_far",)
    return ()


class GamepadReader:
    """Blocking reader used from a thread. ``callback`` receives ``PadEvent``."""

    def __init__(self, callback: Callable[[PadEvent], None]) -> None:
        self._callback = callback
        self._stop = threading.Event()
        self._fds: dict[int, str] = {}
        self._trigger_high = {"lt": False, "rt": False}
        self._hat = {"dpad_x": 0, "dpad_y": 0}

    def stop(self) -> None:
        self._stop.set()

    def _open_devices(self) -> None:
        current = set(self._fds.values())
        for path in sorted(glob.glob("/dev/input/js*")):
            if path in current:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            self._fds[fd] = path

    def _close_all(self) -> None:
        for fd in list(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()

    def _emit(self, control: str, value: float, pressed: bool, edge: bool) -> None:
        self._callback(PadEvent(control, value, pressed, edge))

    def _handle(self, typ: int, number: int, raw: int) -> None:
        init = bool(typ & _INIT)
        kind = typ & ~_INIT
        if kind == _BUTTON:
            control = BUTTONS.get(number)
            if control is None:
                return
            pressed = raw != 0
            self._emit(control, 1.0 if pressed else 0.0, pressed, edge=pressed and not init)
            return
        if kind != _AXIS:
            return
        control = AXES.get(number)
        if control is None:
            return
        value = normalize_axis(number, raw)
        if control in {"lt", "rt"}:
            high = value > 0.65
            was = self._trigger_high[control]
            self._trigger_high[control] = high
            self._emit(control, value, high, edge=high and not was and not init)
            return
        if control in {"dpad_x", "dpad_y"}:
            direction = 0
            if value > 0.5:
                direction = 1
            elif value < -0.5:
                direction = -1
            previous = self._hat[control]
            self._hat[control] = direction
            if init or direction == 0 or direction == previous:
                self._emit(control, value, False, edge=False)
                return
            name = {
                ("dpad_x", -1): "dpad_left",
                ("dpad_x", 1): "dpad_right",
                ("dpad_y", -1): "dpad_up",
                ("dpad_y", 1): "dpad_down",
            }.get((control, direction))
            # Linux hat Y is often negative for up. Both signs are accepted:
            # the first non-zero edge moves focus, and the opposite edge moves back.
            if name is None and control == "dpad_y":
                name = "dpad_up" if direction < 0 else "dpad_down"
            if name:
                self._emit(name, float(direction), True, edge=True)
            return
        self._emit(control, value, abs(value) > 0.2, edge=False)

    def loop(self) -> None:
        while not self._stop.is_set():
            self._open_devices()
            if not self._fds:
                self._stop.wait(0.5)
                continue
            readable, _, _ = select.select(list(self._fds), [], [], 0.3)
            for fd in readable:
                try:
                    packet = os.read(fd, _EVENT.size * 8)
                except OSError:
                    path = self._fds.pop(fd, None)
                    if path:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                    continue
                offset = 0
                while offset + _EVENT.size <= len(packet):
                    parsed = parse_js_packet(packet[offset : offset + _EVENT.size])
                    offset += _EVENT.size
                    if parsed is None:
                        continue
                    _time_ms, value, typ, number = parsed
                    self._handle(typ, number, value)
        self._close_all()
