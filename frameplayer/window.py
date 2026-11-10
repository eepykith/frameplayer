"""Headset-sized window: large targets, a laser-pointer friendly layout, gamepad keys."""

from __future__ import annotations

import math
import threading

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractSlider,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from frameplayer.gamepad import GamepadReader, actions_for
from frameplayer.glvideo import VideoWidget
from frameplayer.hwdec import HwdecPlan, resolution_tier
from frameplayer.library import Library
from frameplayer.presentation import Presentation, layout_label, projection_label, resolve_presentation
from frameplayer.resolve import SAMPLE_HLS, SAMPLE_YOUTUBE, PlayItem, ResolveError, Resolved, resolve
from frameplayer.session import Playback, start_session
from frameplayer.settings import SettingsStore

STYLE = """
* { font-size: 16px; color: #f2efe9; }
QMainWindow, QWidget#root { background: #0c0f14; }
QFrame#panel, QScrollArea, QListWidget { background: #161b22; border: none; }
QLineEdit, QComboBox {
    background: #11161d;
    border: 2px solid #2c3644;
    border-radius: 12px;
    padding: 8px 14px;
    min-height: 44px;
}
QPushButton {
    background: #243041;
    border: 2px solid #334155;
    border-radius: 12px;
    padding: 8px 16px;
    min-height: 48px;
}
QPushButton:hover { background: #2d3c50; }
QPushButton:checked, QPushButton#primary {
    background: #ff7a3c;
    color: #1a0e06;
    border-color: #ff7a3c;
}
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSlider:focus, QListWidget:focus, QCheckBox:focus {
    border: 3px solid #ffb088;
}
QLabel#stats {
    background: rgba(8, 10, 14, 210);
    border-radius: 12px;
    padding: 12px 14px;
    color: #f4f1ea;
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 15px;
}
QLabel#emptyTitle { font-size: 32px; font-weight: 700; }
QLabel#hint { color: #93a0b0; font-size: 16px; }
QLabel#badgeHw { background: #145c3a; color: #b6f3d4; border-radius: 10px; padding: 6px 10px; }
QLabel#badgeSw { background: #6a4a12; color: #ffe3a8; border-radius: 10px; padding: 6px 10px; }
QLabel#badgeOff { background: #243041; color: #d5dbe3; border-radius: 10px; padding: 6px 10px; }
QLabel#error {
    background: #4a1d1d;
    color: #ffd0d0;
    border-radius: 12px;
    padding: 10px 14px;
}
QSlider::groove:horizontal { height: 10px; background: #2a3442; border-radius: 5px; }
QSlider::handle:horizontal {
    width: 28px; margin: -10px 0; border-radius: 14px; background: #ff7a3c;
}
QListWidget::item { min-height: 52px; padding: 8px; }
QListWidget::item:selected { background: #314158; }
"""

HELP = """
Pointer
  Aim and click. Buttons are large on purpose.
  Drag the picture to look around in 360° and 180° video.

Keyboard
  Space          Play or pause
  Left / Right   Seek 10 seconds (Shift seeks 30)
  Up / Down      Volume
  F              Full screen
  Esc            Leave full screen
  M              Mute
  I              Stats
  Enter          Play the URL, or activate the focused control

Controller (Steam Input gamepad)
  A              Play or pause. Activates a focused button while controls are open.
  B              Leave full screen, or show and hide the controls
  X              Stats
  Y              Favorite
  Start          Full screen
  Select         Library
  D-pad          Move between controls while they are open.
                 Seek and volume while they are hidden.
  LB / RB        Activate the focused control, or seek 10 seconds
  LT / RT        Seek 30 seconds
  Left stick     Seek and volume
  Right stick    Look around in 360° and 180° video
  Right stick click   Reset the view

8K and VR
  The decoder sees the whole frame, including both eyes of a 3D video.
  Pick an eye, or 360° / 180°, if Auto does not guess the packing.
  Hardware decode on Steam Frame covers H.264, HEVC, and VP9 up to 8192 pixels.
"""


class ResolveWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str, bool)

    def __init__(self, url: str, ceiling: int, plan: HwdecPlan, prefer_hw: bool) -> None:
        super().__init__()
        self._url = url
        self._ceiling = ceiling
        self._plan = plan
        self._prefer_hw = prefer_hw

    def run(self) -> None:
        try:
            result = resolve(self._url, self._plan, ceiling=self._ceiling, prefer_hw=self._prefer_hw)
        except ResolveError as exc:
            self.failed.emit(str(exc), exc.drm)
        except Exception as exc:
            self.failed.emit(str(exc) or exc.__class__.__name__, False)
        else:
            self.succeeded.emit(result)


class MainWindow(QMainWindow):
    pad_event = Signal(object)

    def __init__(self, plan: HwdecPlan, initial_url: str = "") -> None:
        super().__init__()
        self.plan = plan
        self.store = SettingsStore()
        self.settings = self.store.settings
        self.library = Library()
        self.playback: Playback | None = None
        self.player = None
        self.current: PlayItem | None = None
        self.detected = Presentation()
        self._chips: list[QPushButton] = []
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        self._worker: ResolveWorker | None = None
        self._token = 0
        self._chrome = True
        self._pending_seek: float | None = None
        self._applied_token = None
        self._axes = {"lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0}
        self.yaw_deg = 0.0
        self.pitch_deg = 0.0
        self._playing = False
        self._format_attempt = 0
        self.setWindowTitle("Frame Player")
        self.resize(1280, 800)
        self.setMinimumSize(860, 560)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._build()
        self._open_player()
        self._reload_lists()
        self._apply_settings_to_widgets()
        self.pad_event.connect(self._on_pad)
        self._gamepad = GamepadReader(self.pad_event.emit)
        self._pad_thread = threading.Thread(target=self._gamepad.loop, name="gamepad", daemon=True)
        self._pad_thread.start()
        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._poll_player)
        self._poll.start()
        self._stick = QTimer(self)
        self._stick.setInterval(50)
        self._stick.timeout.connect(self._stick_tick)
        self._stick.start()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_hide)
        if initial_url:
            self.play_url(initial_url)
        else:
            self._show_empty(True)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Frame Player")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.badge = QLabel("Starting")
        self.badge.setObjectName("badgeOff")
        header.addWidget(title)
        header.addSpacing(12)
        header.addWidget(self.badge)
        header.addStretch(1)
        self.stats_button = self._button("Stats", self._toggle_stats)
        self.library_button = self._button("Library", self._toggle_library)
        self.help_button = self._button("Controls", self._show_help)
        header.addWidget(self.stats_button)
        header.addWidget(self.library_button)
        header.addWidget(self.help_button)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.library_panel = self._build_library()
        body.addWidget(self.library_panel, 0)
        self.stage = QFrame()
        self.stage.setObjectName("panel")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.video = VideoWidget()
        self.video.setMouseTracking(True)
        self.video.installEventFilter(self)
        self.video.clicked.connect(self._on_video_click)
        self.video.double_clicked.connect(self._toggle_fullscreen)
        self.video.look_dragged.connect(self._on_look_drag)
        self.video.failed.connect(lambda text: self._set_error(text))
        stage_layout.addWidget(self.video, 1)
        self.empty = QLabel("Paste a video link")
        self.empty.setObjectName("emptyTitle")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        # The empty state sits in the status area so it never covers the GL surface.
        self.stats = QLabel("")
        self.stats.setObjectName("stats")
        self.stats.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.stats.setParent(self.video)
        self.stats.move(16, 16)
        self.stats.hide()
        body.addWidget(self.stage, 1)
        outer.addLayout(body, 1)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("YouTube, Twitch, HLS, DASH, MP4, or a local file")
        self.url_edit.returnPressed.connect(self._play_from_box)
        self.paste_button = self._button("Paste and play", self._paste_and_play)
        self.paste_button.setObjectName("primary")
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.paste_button)
        outer.addLayout(url_row)

        samples = QHBoxLayout()
        samples.addWidget(self._button("Sample HLS", lambda: self.play_url(SAMPLE_HLS)))
        samples.addWidget(self._button("Sample YouTube", lambda: self.play_url(SAMPLE_YOUTUBE)))
        samples.addStretch(1)
        outer.addLayout(samples)

        self.quality_host = QWidget()
        self.quality_row = QHBoxLayout(self.quality_host)
        self.quality_row.setContentsMargins(0, 0, 0, 0)
        self.quality_row.addStretch(1)
        self.quality_scroll = QScrollArea()
        self.quality_scroll.setWidgetResizable(True)
        self.quality_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.quality_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.quality_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.quality_scroll.setFixedHeight(108)
        self.quality_scroll.setWidget(self.quality_host)
        outer.addWidget(self.quality_scroll)

        vr = QHBoxLayout()
        vr.addWidget(QLabel("Eye"))
        self.eye = QComboBox()
        self.eye.addItem("Auto", "auto")
        for code, label in (
            ("packed", "Packed frame"),
            ("sbs-left", "Left eye"),
            ("sbs-right", "Right eye"),
            ("tb-top", "Top eye"),
            ("tb-bottom", "Bottom eye"),
        ):
            self.eye.addItem(label, code)
        self.eye.currentIndexChanged.connect(self._presentation_changed)
        vr.addWidget(self.eye, 1)
        vr.addWidget(QLabel("VR"))
        self.projection = QComboBox()
        self.projection.addItem("Auto", "auto")
        self.projection.addItem("Flat", "flat")
        self.projection.addItem("360°", "equirect-360")
        self.projection.addItem("180°", "equirect-180")
        self.projection.currentIndexChanged.connect(self._presentation_changed)
        vr.addWidget(self.projection, 1)
        self.detected_label = QLabel("2D")
        self.detected_label.setObjectName("hint")
        vr.addWidget(self.detected_label, 1)
        self.reset_view = self._button("Reset view", self._reset_view)
        vr.addWidget(self.reset_view)
        vr.addWidget(QLabel("FOV"))
        self.fov = QSlider(Qt.Orientation.Horizontal)
        self.fov.setRange(50, 120)
        self.fov.setValue(90)
        self.fov.setFixedWidth(160)
        self.fov.valueChanged.connect(lambda _v: self._push_view())
        vr.addWidget(self.fov)
        outer.addLayout(vr)

        transport = QHBoxLayout()
        self.play_button = self._button("Play", self.toggle_pause)
        self.play_button.setObjectName("primary")
        self.time_label = QLabel("0:00")
        self.time_label.setMinimumWidth(72)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 10000)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_seeking", True))
        self.seek.sliderReleased.connect(self._seek_released)
        self._seeking = False
        self.duration_label = QLabel("0:00")
        self.duration_label.setMinimumWidth(72)
        transport.addWidget(self.play_button)
        transport.addWidget(self.time_label)
        transport.addWidget(self.seek, 1)
        transport.addWidget(self.duration_label)
        outer.addLayout(transport)

        audio = QHBoxLayout()
        audio.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 150)
        self.volume.setValue(self.settings.volume)
        self.volume.valueChanged.connect(self._volume_changed)
        self.mute_button = self._button("Mute", self._toggle_mute)
        self.favorite_button = self._button("Favorite", self._toggle_favorite)
        self.full_button = self._button("Full screen", self._toggle_fullscreen)
        audio.addWidget(self.volume, 1)
        audio.addWidget(self.mute_button)
        audio.addWidget(self.favorite_button)
        audio.addWidget(self.full_button)
        outer.addLayout(audio)

        self.error = QLabel("")
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        self.error.hide()
        outer.addWidget(self.error)
        self.status = QLabel(self.plan.reason)
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        outer.addWidget(self.empty)

        self._chrome_widgets = [
            self.url_edit,
            self.paste_button,
            self.quality_scroll,
            self.eye,
            self.projection,
            self.detected_label,
            self.reset_view,
            self.fov,
            self.play_button,
            self.time_label,
            self.seek,
            self.duration_label,
            self.volume,
            self.mute_button,
            self.favorite_button,
            self.full_button,
        ]
        self.installEventFilter(self)

    def _build_library(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Favorites"))
        self.favorites = QListWidget()
        self.favorites.itemClicked.connect(self._open_entry)
        layout.addWidget(self.favorites, 1)
        layout.addWidget(QLabel("Recent"))
        self.history = QListWidget()
        self.history.itemClicked.connect(self._open_entry)
        layout.addWidget(self.history, 1)
        self.hw_check = QCheckBox("Hardware decoding")
        self.hw_check.toggled.connect(self._hw_toggled)
        layout.addWidget(self.hw_check)
        layout.addWidget(QLabel("Quality ceiling"))
        self.ceiling = QComboBox()
        for value, label in (
            (1080, "Up to 1080p"),
            (1440, "Up to 1440p"),
            (2160, "Up to 4K"),
            (4320, "Up to 8K"),
            (0, "Best, any size"),
        ):
            self.ceiling.addItem(label, value)
        self.ceiling.currentIndexChanged.connect(self._ceiling_changed)
        layout.addWidget(self.ceiling)
        self.stats_check = QCheckBox("Show decoder stats")
        self.stats_check.toggled.connect(self._stats_toggled)
        layout.addWidget(self.stats_check)
        return panel

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _apply_settings_to_widgets(self) -> None:
        self.hw_check.setChecked(self.settings.hwdec_enabled)
        self.stats_check.setChecked(self.settings.stats)
        index = self.ceiling.findData(self.settings.ceiling)
        if index < 0:
            index = self.ceiling.findData(4320)
        self.ceiling.setCurrentIndex(index)
        self._set_combo(self.eye, self.settings.layout)
        self._set_combo(self.projection, self.settings.projection)
        self._update_badge()

    def _set_combo(self, box: QComboBox, value: str) -> None:
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)

    def _open_player(self) -> None:
        try:
            self.player, self.playback = start_session(
                vo=None,
                ao="pipewire,pulse,alsa",
                plan=self.plan,
                enabled=self.settings.hwdec_enabled,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Frame Player", f"Could not start libmpv.\n\n{exc}")
            raise
        self.player.volume = self.settings.volume
        self.video.attach_player(self.player)
        if self.plan.platform in {"steam-frame", "qualcomm-v4l2"}:
            try:
                self.player.hwdec_extra_frames = 8
            except Exception:
                pass

    def play_url(self, url: str) -> None:
        self.url_edit.setText(url)
        self._token += 1
        self._format_attempt = 0
        token = self._token
        self._set_error("")
        self.status.setText("Resolving the stream…")
        self._show_empty(True, "Resolving the stream…")
        worker = ResolveWorker(url, int(self.settings.ceiling), self.plan, self.settings.prefer_hw)
        worker.succeeded.connect(lambda result, token=token: self._on_resolved(token, result))
        worker.failed.connect(lambda message, drm, token=token: self._on_failed(token, message, drm))
        self._worker = worker
        worker.start()

    def _on_resolved(self, token: int, result: Resolved) -> None:
        if token != self._token or self.playback is None:
            return
        self._fill_qualities(result)
        saved = self.library.get(result.page_url)
        if saved and saved.layout != "auto":
            self._set_combo(self.eye, saved.layout)
        if saved and saved.projection != "auto":
            self._set_combo(self.projection, saved.projection)
        self.detected = result.detected
        self._show_empty(False)
        if result.message:
            self.status.setText(result.message)
        self._play_item(result.items[result.selected_index], resume=False)
        self.library.remember(result.page_url, result.title, quality=result.selected.menu_label())
        self._reload_lists()
        self._refresh_favorite()

    def _on_failed(self, token: int, message: str, drm: bool) -> None:
        if token != self._token:
            return
        self._set_error(message)
        self._show_empty(True, "Could not open that link" if not drm else "DRM video is not supported")
        self.status.setText("Try another URL, or use a direct HLS, DASH, or MP4 link.")

    def _fill_qualities(self, result: Resolved) -> None:
        while self.quality_row.count():
            item = self.quality_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._chips = []
        for index, item in enumerate(result.items):
            button = QPushButton(item.chip_text())
            button.setCheckable(True)
            button.setMinimumWidth(128)
            button.setMinimumHeight(88)
            button.item = item  # type: ignore[attr-defined]
            button.clicked.connect(lambda _checked=False, button=button: self._quality_clicked(button))
            if index == result.selected_index:
                button.setChecked(True)
            self._chip_group.addButton(button)
            self.quality_row.addWidget(button)
            self._chips.append(button)
        self.quality_row.addStretch(1)

    def _quality_clicked(self, button: QPushButton) -> None:
        if self.playback is None:
            return
        position = self.playback.snapshot().time_pos
        self._pending_seek = position if position > 1 else None
        self._play_item(button.item, resume=True)

    def _play_item(self, item: PlayItem, *, resume: bool) -> None:
        if self.playback is None:
            return
        self.current = item
        if not resume:
            self._pending_seek = None
            self.yaw_deg = 0
            self.pitch_deg = 0
        self._applied_token = None
        self.playback.frame_width = 0
        self.playback.frame_height = 0
        self.video.set_frame_size(0, 0)
        self._playing = True
        self.playback.software_forced = False
        self.playback.fatal = False
        self.playback.load(item)
        self.play_button.setText("Pause")
        self.setWindowTitle(item.title or "Frame Player")
        tier = resolution_tier(item.width, item.height)
        extra = ""
        if item.over_iris:
            extra = " This frame is over Iris's 8192-pixel limit, so Steam Frame will decode it in software."
        elif tier == "8K":
            extra = " 8K uses the full Iris decoder. AV1 still falls back to the CPU on current SteamOS."
        self.status.setText(f"Playing {item.menu_label()}.{extra}")
        self._push_view()
        self.show_chrome()

    def _next_untried_item(self) -> PlayItem | None:
        if self.current is None:
            return None
        seen = False
        for button in self._chips:
            item = getattr(button, "item", None)
            if item is None:
                continue
            if item is self.current or item.format_id == self.current.format_id:
                seen = True
                continue
            if seen:
                return item
        return None

    def _effective(self) -> Presentation:
        return resolve_presentation(
            self.detected,
            str(self.eye.currentData() or "auto"),
            str(self.projection.currentData() or "auto"),
        )

    def _presentation_changed(self) -> None:
        self.settings.layout = str(self.eye.currentData() or "auto")
        self.settings.projection = str(self.projection.currentData() or "auto")
        self.store.save()
        if self.playback is not None and self._playing:
            self.playback.frame_width = 0
            self.playback.frame_height = 0
            self._applied_token = None
        self._push_view()

    def _push_view(self) -> None:
        presentation = self._effective()
        self.detected_label.setText(f"{presentation.label()}  ·  {self.detected.reason}")
        spherical = presentation.spherical
        self.fov.setEnabled(spherical)
        self.reset_view.setEnabled(spherical)
        self.video.set_view(
            spherical=spherical and not self.video.shader_error,
            layout=presentation.layout,
            projection=presentation.projection,
            yaw=math.radians(self.yaw_deg),
            pitch=math.radians(self.pitch_deg),
            fov_deg=self.fov.value(),
        )
        if spherical and self.video.shader_error:
            self._set_error(f"VR projection is unavailable ({self.video.shader_error}). Showing the packed frame.")

    def _reset_view(self) -> None:
        self.yaw_deg = 0
        self.pitch_deg = 0
        self.fov.setValue(90)
        self._push_view()

    def _on_look_drag(self, dx: float, dy: float) -> None:
        self.yaw_deg += dx * 0.15
        self.pitch_deg = max(-85, min(85, self.pitch_deg + dy * 0.12))
        self._push_view()

    def _poll_player(self) -> None:
        if self.playback is None:
            return
        if self.playback.poll_fallback():
            self.status.setText("Hardware decode failed. Reloaded with software decode.")
            self._applied_token = None
        if self.playback.fatal:
            nxt = self._next_untried_item()
            if nxt is not None and self._format_attempt < 3:
                self._format_attempt += 1
                self.status.setText("That rendition was refused. Trying another quality…")
                self._play_item(nxt, resume=False)
                return
            self._set_error(self.playback.last_error_line())
        stats = self.playback.snapshot()
        if self._playing and stats.decoded_width > 0 and self._applied_token != id(self.current):
            self.video.set_frame_size(stats.decoded_width, stats.decoded_height)
            presentation = self._effective()
            # Re-detect with the real frame size when the URL had no size yet.
            if self.detected.reason == "no 3D or VR markers" and self.current is not None:
                from frameplayer.presentation import detect_presentation

                self.detected = detect_presentation(
                    title=self.current.title,
                    url=self.current.page_url,
                    note=self.current.note,
                    width=stats.decoded_width,
                    height=stats.decoded_height,
                )
                presentation = self._effective()
            self.playback.apply_presentation(presentation)
            self._push_view()
            self._applied_token = id(self.current)
            if self._pending_seek and not (self.current and self.current.is_live):
                try:
                    self.player.seek(self._pending_seek, reference="absolute", precision="keyframes")
                except Exception:
                    pass
                self._pending_seek = None
        self._paint_stats(stats)
        self._paint_transport(stats)
        self._update_badge()

    def _paint_stats(self, stats) -> None:
        if not self.settings.stats or not self._playing:
            self.stats.hide()
            return
        active = self.playback.active()
        tier = resolution_tier(stats.decoded_width, stats.decoded_height)
        fps = stats.estimated_fps or stats.container_fps
        lines = [
            active.summary,
            active.detail,
            f"{stats.codec or 'video'} · {stats.pixfmt or 'unknown'}",
            f"{stats.decoded_width}×{stats.decoded_height}" + (f" · {tier}" if tier else ""),
            f"{fps:.2f} fps · dropped {stats.dropped} (decoder {stats.decoder_dropped})",
            self._effective().label(),
        ]
        self.stats.setText("\n".join(line for line in lines if line))
        self.stats.adjustSize()
        self.stats.show()
        self.stats.raise_()

    def _paint_transport(self, stats) -> None:
        self.time_label.setText(_fmt_time(stats.time_pos))
        live = bool(self.current and self.current.is_live and stats.duration <= 0)
        if live:
            self.duration_label.setText("LIVE")
            self.seek.setEnabled(False)
        else:
            self.duration_label.setText(_fmt_time(stats.duration) if stats.duration else "--:--")
            self.seek.setEnabled(stats.duration > 0)
            if stats.duration > 0 and not self._seeking:
                self.seek.setValue(int(max(0, min(1, stats.time_pos / stats.duration)) * 10000))
        if self._playing:
            self.play_button.setText("Play" if stats.paused else "Pause")

    def _update_badge(self) -> None:
        if self.playback is None:
            return
        active = self.playback.active() if self._playing else None
        if active and active.hardware:
            self.badge.setText(active.summary)
            self.badge.setObjectName("badgeHw")
        elif active and active.fallback:
            self.badge.setText(active.summary)
            self.badge.setObjectName("badgeSw")
        elif not self.settings.hwdec_enabled:
            self.badge.setText("Software")
            self.badge.setObjectName("badgeOff")
        else:
            text = {
                "steam-frame": "Iris ready",
                "qualcomm-v4l2": "Venus ready",
                "vaapi": "VA-API ready",
                "nvdec": "NVDEC ready",
                "software": "Software only",
            }.get(self.plan.platform, "Hardware ready")
            self.badge.setText(text)
            self.badge.setObjectName("badgeHw" if self.plan.chain else "badgeOff")
        self.badge.style().unpolish(self.badge)
        self.badge.style().polish(self.badge)

    def toggle_pause(self) -> None:
        if self.player is None or not self._playing:
            self._play_from_box()
            return
        self.player.pause = not bool(self.player.pause)
        self.show_chrome()

    def _seek_released(self) -> None:
        self._seeking = False
        if self.player is None or self.playback is None:
            return
        duration = self.playback.snapshot().duration
        if duration <= 0:
            return
        self.player.seek(duration * (self.seek.value() / 10000), reference="absolute", precision="keyframes")

    def seek_relative(self, seconds: float) -> None:
        if self.player is None or not self._playing:
            return
        if self.current and self.current.is_live and self.playback and self.playback.snapshot().duration <= 0:
            self.status.setText("This live stream has no seek bar.")
            return
        self.player.seek(seconds, reference="relative", precision="keyframes")
        self.show_chrome()

    def _volume_changed(self, value: int) -> None:
        if self.player is not None:
            self.player.volume = value
        self.settings.volume = value
        self.store.save()

    def add_volume(self, delta: float) -> None:
        self.volume.setValue(int(max(0, min(150, self.volume.value() + delta))))

    def _toggle_mute(self) -> None:
        if self.player is None:
            return
        self.player.mute = not bool(self.player.mute)
        self.mute_button.setText("Unmute" if self.player.mute else "Mute")

    def _toggle_favorite(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            return
        favorite = not self.library.is_favorite(url)
        title = self.current.title if self.current else url
        self.library.set_favorite(url, favorite, title)
        self._reload_lists()
        self._refresh_favorite()

    def _refresh_favorite(self) -> None:
        url = self.url_edit.text().strip()
        self.favorite_button.setText("Favorited" if self.library.is_favorite(url) else "Favorite")

    def _toggle_stats(self) -> None:
        self.settings.stats = not self.settings.stats
        self.stats_check.setChecked(self.settings.stats)
        self.store.save()

    def _toggle_library(self) -> None:
        self.library_panel.setVisible(not self.library_panel.isVisible())

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.full_button.setText("Full screen")
        else:
            self.showFullScreen()
            self.full_button.setText("Exit full screen")
        self.show_chrome()

    def _hw_toggled(self, enabled: bool) -> None:
        self.settings.hwdec_enabled = enabled
        self.store.save()
        if self.playback is None:
            return
        from frameplayer.session import hwdec_for

        self.playback.set_hwdec(hwdec_for(self.plan, enabled))
        self.playback.software_forced = False
        if self.current is not None:
            self._applied_token = None
            self.playback.load(self.current)
        self._update_badge()

    def _ceiling_changed(self) -> None:
        self.settings.ceiling = int(self.ceiling.currentData())
        self.store.save()

    def _stats_toggled(self, enabled: bool) -> None:
        self.settings.stats = enabled
        self.store.save()

    def _play_from_box(self) -> None:
        self.play_url(self.url_edit.text().strip())

    def _paste_and_play(self) -> None:
        from PySide6.QtWidgets import QApplication

        text = QApplication.clipboard().text().strip()
        if not text:
            self.status.setText("The clipboard is empty.")
            return
        self.play_url(text.splitlines()[0].strip())

    def _open_entry(self, item: QListWidgetItem) -> None:
        url = item.data(Qt.ItemDataRole.UserRole)
        if url:
            self.play_url(url)

    def _reload_lists(self) -> None:
        self._fill_list(self.favorites, self.library.favorites())
        self._fill_list(self.history, self.library.history())

    def _fill_list(self, widget: QListWidget, entries) -> None:
        widget.clear()
        for entry in entries:
            item = QListWidgetItem(entry.title or entry.url)
            item.setData(Qt.ItemDataRole.UserRole, entry.url)
            item.setToolTip(entry.url)
            item.setSizeHint(QSize(260, 56))
            widget.addItem(item)

    def _show_help(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Controls")
        dialog.resize(720, 640)
        layout = QVBoxLayout(dialog)
        label = QLabel(HELP)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        close = self._button("Close", dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _on_video_click(self) -> None:
        if not self._chrome:
            self.show_chrome()
            return
        if self._playing:
            self.toggle_pause()
        else:
            self.show_chrome()

    def show_chrome(self) -> None:
        self._chrome = True
        for widget in self._chrome_widgets:
            widget.setVisible(True)
        self._hide_timer.start(4500)

    def _auto_hide(self) -> None:
        if not self._playing or not self._chrome:
            return
        if isinstance(self.focusWidget(), QLineEdit):
            return
        if self.playback and self.playback.snapshot().paused:
            return
        self._chrome = False
        for widget in self._chrome_widgets:
            widget.hide()
        self.setFocus()

    def _show_empty(self, visible: bool, text: str = "Paste a video link") -> None:
        self.empty.setVisible(visible)
        if visible:
            self.empty.setText(text + "\n\nYouTube, Twitch, HLS, DASH, MP4, 8K, side-by-side 3D, and 360/180 VR.")

    def _set_error(self, text: str) -> None:
        self.error.setVisible(bool(text))
        self.error.setText(text)

    def _focusables(self) -> list[QWidget]:
        widgets: list[QWidget] = [
            self.url_edit,
            self.paste_button,
            *self._chips,
            self.eye,
            self.projection,
            self.reset_view,
            self.play_button,
            self.seek,
            self.volume,
            self.mute_button,
            self.favorite_button,
            self.stats_button,
            self.library_button,
            self.full_button,
            self.help_button,
        ]
        return [widget for widget in widgets if widget.isVisible() and widget.isEnabled()]

    def _move_focus(self, delta: int) -> None:
        self.show_chrome()
        widgets = self._focusables()
        if not widgets:
            return
        current = self.focusWidget()
        index = widgets.index(current) if current in widgets else -1
        widgets[(index + delta) % len(widgets)].setFocus()

    def _activate_focus(self) -> None:
        widget = self.focusWidget()
        if isinstance(widget, QPushButton):
            widget.click()
        elif isinstance(widget, QComboBox):
            widget.showPopup()
        elif isinstance(widget, QAbstractSlider):
            return

    def _on_pad(self, event) -> None:
        control = event.control
        if control in self._axes or control in {"lx", "ly", "rx", "ry"}:
            if control in self._axes:
                self._axes[control] = event.value
            return
        focus = self.focusWidget()
        focused_button = self._chrome and isinstance(focus, (QPushButton, QComboBox)) and focus is not self.play_button
        for action in actions_for(
            event,
            chrome_visible=self._chrome,
            spherical=self._effective().spherical,
            focus_is_button=focused_button,
        ):
            self._run_action(action)

    def _run_action(self, action: str) -> None:
        if action == "play_pause":
            self.toggle_pause()
        elif action == "back":
            if self.isFullScreen():
                self._toggle_fullscreen()
            elif self._chrome:
                self._auto_hide()
            else:
                self.show_chrome()
        elif action == "toggle_stats":
            self._toggle_stats()
        elif action == "favorite":
            self._toggle_favorite()
        elif action == "fullscreen":
            self._toggle_fullscreen()
        elif action == "library":
            self._toggle_library()
        elif action == "mute":
            self._toggle_mute()
        elif action == "reset_view":
            self._reset_view()
        elif action == "activate":
            self._activate_focus()
        elif action == "seek_left":
            self.seek_relative(-10)
        elif action == "seek_right":
            self.seek_relative(10)
        elif action == "seek_left_far":
            self.seek_relative(-30)
        elif action == "seek_right_far":
            self.seek_relative(30)
        elif action == "volume_up":
            self.add_volume(5)
        elif action == "volume_down":
            self.add_volume(-5)
        elif action == "focus_next":
            self._move_focus(1)
        elif action == "focus_prev":
            self._move_focus(-1)

    def _stick_tick(self) -> None:
        lx, ly = self._axes.get("lx", 0.0), self._axes.get("ly", 0.0)
        rx, ry = self._axes.get("rx", 0.0), self._axes.get("ry", 0.0)
        spherical = self._effective().spherical
        if spherical and (abs(rx) > 0.18 or abs(ry) > 0.18):
            self.yaw_deg += rx * 4.5
            self.pitch_deg = max(-85, min(85, self.pitch_deg - ry * 3.2))
            self._push_view()
        if abs(ly) > 0.25:
            self.add_volume(-ly * 1.5)
        if abs(lx) > 0.25 and not (self._chrome and isinstance(self.focusWidget(), QAbstractSlider)):
            self.seek_relative(lx * 0.4)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.MouseMove:
            self.show_chrome()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if isinstance(self.focusWidget(), QLineEdit) and key not in {
            Qt.Key.Key_Escape,
            Qt.Key.Key_F,
        }:
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Space:
            self.toggle_pause()
        elif key == Qt.Key.Key_Left:
            self.seek_relative(-30 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else -10)
        elif key == Qt.Key.Key_Right:
            self.seek_relative(30 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 10)
        elif key == Qt.Key.Key_Up:
            self.add_volume(5)
        elif key == Qt.Key.Key_Down:
            self.add_volume(-5)
        elif key == Qt.Key.Key_F:
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self._toggle_fullscreen()
        elif key == Qt.Key.Key_M:
            self._toggle_mute()
        elif key == Qt.Key.Key_I:
            self._toggle_stats()
        elif key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self._activate_focus()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            self.play_url(url.toLocalFile() or url.toString())
        elif event.mimeData().hasText():
            self.play_url(event.mimeData().text().strip().splitlines()[0])

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.width() < 980 and self.library_panel.isVisible() and event.oldSize().width() >= 980:
            self.library_panel.hide()

    def closeEvent(self, event) -> None:
        self._poll.stop()
        self._stick.stop()
        self._hide_timer.stop()
        self._gamepad.stop()
        self._pad_thread.join(timeout=1)
        if self.playback is not None:
            self.playback.close()
        self.video.shutdown()
        super().closeEvent(event)


def _fmt_time(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "0:00"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def load_icon() -> QIcon:
    from pathlib import Path

    path = Path(__file__).with_name("assets") / "frameplayer.svg"
    return QIcon(str(path)) if path.exists() else QIcon()
