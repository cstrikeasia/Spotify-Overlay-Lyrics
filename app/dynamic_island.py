"""Fixed-position iOS-inspired media island for the Windows desktop.

The island shares playback/artwork/lyrics with the existing app; it does not
connect to Spotify or a lyric service on its own.  The collapsed pill shows album artwork, artist - title and the active lyric;
the five audio bars still use genuine Windows Core Audio levels.
The primary monitor's top-center is the only allowed position: neither a
mouse drag nor a legacy saved window position can relocate this window.
"""
from __future__ import annotations

from functools import lru_cache
import ctypes
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize,
    Qt, QPropertyAnimation, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontDatabase, QFontMetrics, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QTextLayout, QTextOption,
)
from PySide6.QtWidgets import QApplication, QMenu, QSlider, QWidget

from .i18n import current_language, tr
from .lyric_timeline import active_lyric_index
from .island_compact_text import compact_display_lines
from .island_marquee import (
    MarqueeClock, marquee_offset, synced_lyric_window, timed_lyric_offset,
    two_line_vertical_offset,
)
from .models import LyricsResult, Playback
from .toolbar_icons import VectorToolButton
from .island_media_buttons import IslandMediaButton
from .island_input import has_new_button_press, should_collapse_from_outside_press
from .audio_meter import RealPeakMotion, SpotifyAudioMeter


class IslandSeekSlider(QSlider):
    """Custom-painted iOS-like timeline, with seek-on-click and dragging."""

    TRACK_H = 7
    PAD_X = 5

    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 1000)
        self.setObjectName("islandSeek")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._hovered = False
        self.setStyleSheet("QSlider#islandSeek { background: transparent; border: 0; }")

    def sizeHint(self):
        return QSize(200, 18)

    def minimumSizeHint(self):
        return QSize(80, 18)

    def _pointer_value(self, x: float) -> int:
        # Use the entire visible track as the seekable area (no circular thumb).
        travel = max(1.0, self.width() - self.PAD_X * 2)
        fraction = max(0.0, min(1.0, (x - self.PAD_X) / travel))
        return round(self.minimum() + (self.maximum() - self.minimum()) * fraction)

    def _progress_highlighted(self) -> bool:
        """The active timeline turns white on hover/drag; no thumb is drawn."""
        return self.isEnabled() and (self._hovered or self.isSliderDown())

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        groove = QRectF(self.PAD_X,
                        (self.height() - self.TRACK_H) / 2.0,
                        max(1.0, self.width() - self.PAD_X * 2),
                        self.TRACK_H)
        fraction = 0.0
        if self.maximum() > self.minimum():
            fraction = (self.sliderPosition() - self.minimum()) / (self.maximum() - self.minimum())
        fraction = max(0.0, min(1.0, fraction))
        active = QRectF(groove.left(), groove.top(), groove.width() * fraction, groove.height())

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#2E2E32') if self.isEnabled() else QColor('#222226'))
        painter.drawRoundedRect(groove, groove.height() / 2.0, groove.height() / 2.0)
        painter.setBrush(QColor('#F6F6FA') if self._progress_highlighted()
                         else QColor('#85858D') if self.isEnabled() else QColor('#65656A'))
        # The filled portion is a flush capsule; never render a ball/handle.
        if active.width() > 0.0:
            painter.drawRoundedRect(active, active.height() / 2.0, active.height() / 2.0)
        painter.end()

    def enterEvent(self, event) -> None:
        self._hovered = True
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        self._hovered = False
        super().leaveEvent(event)
        self.update()

    def hideEvent(self, event) -> None:
        # A slider hidden by the collapse animation must not retain hover
        # highlighting when the island is opened again later.
        self._hovered = False
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setSliderPosition(self._pointer_value(event.position().x()))
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isEnabled() and self.isSliderDown():
            self.setSliderPosition(self._pointer_value(event.position().x()))
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderPosition(self._pointer_value(event.position().x()))
            self.setSliderDown(False)
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class DynamicIslandWindow(QWidget):
    """Notch-like compact lyrics pill with an expandable Spotify controller."""

    transport_requested = Signal(str)
    seek_requested = Signal(int)
    show_main_requested = Signal()
    settings_requested = Signal()
    disable_requested = Signal()

    PAD = 7
    CLOSED_W, CLOSED_H = 396, 104
    OPEN_W, OPEN_H = 444, 250

    def __init__(self, *, auto_expand: bool = True, show_lyrics: bool = True,
                 demo: bool = False, saved_x: int = -1, saved_y: int = -1) -> None:
        super().__init__(None)
        self.setObjectName("dynamicIsland")
        self.setWindowTitle(tr("桌面靈動島"))
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.auto_expand = bool(auto_expand)
        self.show_lyrics = bool(show_lyrics)
        self.demo = bool(demo)
        self.playback: Playback | None = None
        self._artwork_id = ""
        self._artwork_path = ""
        self._artwork = QPixmap()
        self._lyric_primary = ""
        self._lyric_secondary = ""
        self._lyrics_kind = ""
        self._lyric_reveal = 1.0
        self._position_ms = 0
        self._duration_ms = 0
        self._expanded = False
        self._pinned = False
        self._scrubbing = False
        self._seeking_key = ""
        self._preview_position = 0
        self._suppress_auto_until = 0.0
        # Independent clocks: track metadata loops, synced lyrics stop at their
        # last character until a new lyric line becomes active.
        self._metadata_marquee = MarqueeClock()
        self._lyric_marquee = MarqueeClock()
        self._active_lyric_index = -1
        self._lyric_timing_window: tuple[int, int] | None = None
        self._lyric_position_sample_ms = 0
        self._lyric_sampled_at = time.monotonic()
        # Cached QTextLayout is reused on each audio-meter paint tick. It
        # shapes CJK/Latin text and wraps by actual glyph width, not by count.
        self._compact_lyric_layout_cache = None
        self._audio_meter = SpotifyAudioMeter()
        self._wave_motion = RealPeakMotion(bar_count=6)
        self._wave_history = self._wave_motion.bars
        self._meter_timer = QTimer(self)
        # Match the audio worker's sample cadence for fluid, audio-led motion.
        self._meter_timer.setInterval(33)
        self._meter_timer.timeout.connect(self._tick_audio_meter)
        self._meter_timer.start()

        self._expander = QPropertyAnimation(self, b"geometry", self)
        self._expander.setDuration(320)
        self._expander.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expander.finished.connect(self._animation_finished)
        self._lyric_anim = QVariantAnimation(self)
        self._lyric_anim.setDuration(210)
        self._lyric_anim.setStartValue(0.45)
        self._lyric_anim.setEndValue(1.0)
        self._lyric_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._lyric_anim.valueChanged.connect(self._set_lyric_reveal)

        self._expand_delay = QTimer(self)
        self._expand_delay.setSingleShot(True)
        self._expand_delay.setInterval(170)
        self._expand_delay.timeout.connect(self._auto_expand)
        self._collapse_delay = QTimer(self)
        self._collapse_delay.setSingleShot(True)
        self._collapse_delay.setInterval(480)
        self._collapse_delay.timeout.connect(self._auto_collapse)

        # An always-on-top, non-activating Tool window does not receive clicks
        # dispatched to other Windows applications. Poll only while expanded,
        # and also observe Qt events for instant handling inside this app.
        self._outside_click_timer = QTimer(self)
        self._outside_click_timer.setInterval(16)
        self._outside_click_timer.timeout.connect(self._poll_external_click)
        self._last_mouse_buttons: tuple[bool, ...] = ()

        self.prev_button = IslandMediaButton("skip-back", tr("上一首"),
                                            size=54, glyph_size=37)
        self.play_button = IslandMediaButton("play", tr("播放"),
                                            size=64, glyph_size=49)
        self.next_button = IslandMediaButton("skip-forward", tr("下一首"),
                                            size=54, glyph_size=37)
        self.route_button = IslandMediaButton("airplay-audio", "Windows 音訊輸出設定",
                                             size=44, glyph_size=28, route=True)
        # These are real child buttons, not independent top-level Qt windows.
        for media_button in (self.prev_button, self.play_button,
                             self.next_button, self.route_button):
            media_button.setParent(self)
        self.route_button.setToolTip("音訊輸出裝置設定（Windows）")
        self.route_button.setAccessibleName("音訊輸出裝置設定（Windows）")
        self.close_button = self._button("x", "收合靈動島", "close")
        self.prev_button.clicked.connect(lambda: self.transport_requested.emit("previous"))
        self.play_button.clicked.connect(lambda: self.transport_requested.emit("toggle"))
        self.next_button.clicked.connect(lambda: self.transport_requested.emit("next"))
        self.route_button.clicked.connect(self._open_audio_output_settings)
        self.close_button.clicked.connect(self._close_button_pressed)

        self.seek_slider = IslandSeekSlider(self)
        self.seek_slider.setToolTip(tr("拖曳進度條調整播放位置"))
        self.seek_slider.sliderPressed.connect(self._seek_started)
        self.seek_slider.sliderMoved.connect(self._seek_moved)
        self.seek_slider.sliderReleased.connect(self._seek_released)
        self._hide_controls()

        # Constructor arguments kept for compatibility with older callers.
        # Previous saved positions are intentionally ignored in fixed-island mode.
        del saved_x, saved_y
        self._center_x, self._top_y = self._fixed_anchor()
        self.setGeometry(self._rect_for(False))
        self._watched_screens = []
        app = QApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._monitor_configuration_changed)
            app.screenRemoved.connect(self._monitor_configuration_changed)
            app.primaryScreenChanged.connect(self._monitor_configuration_changed)
            self._watch_screen_geometry()
            app.installEventFilter(self)
            app.applicationStateChanged.connect(self._on_application_state_changed)

    def _button(self, icon: str, tooltip: str, variant: str) -> VectorToolButton:
        # Only the hidden collapse button still uses the shared desktop icon.
        button = VectorToolButton(icon, tr(tooltip), width=28)
        button.setParent(self)
        button.setFixedSize(28, 28)
        button.setStyleSheet("QToolButton { border: none; background: transparent; }")
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _open_audio_output_settings(self) -> None:
        """Desktop substitute for iPhone's route picker: Windows sound output."""
        if os.name == "nt":
            try:
                os.startfile("ms-settings:sound")
                return
            except OSError:
                pass
        self.show_main_requested.emit()

    @staticmethod
    def _fixed_anchor() -> tuple[int, int]:
        """Always use the primary display's available top-center; never saved XY."""
        primary = QApplication.primaryScreen()
        if primary is None:
            return 0, 2
        # Horizontal center is the physical screen center even with a side taskbar.
        # Respect the usable top edge if Windows reserves space for a top taskbar.
        return primary.geometry().center().x(), primary.availableGeometry().top() + 2

    def _watch_screen_geometry(self) -> None:
        for screen in QApplication.screens():
            if screen not in self._watched_screens:
                screen.geometryChanged.connect(self._monitor_configuration_changed)
                screen.availableGeometryChanged.connect(self._monitor_configuration_changed)
                self._watched_screens.append(screen)

    def _rect_for(self, expanded: bool) -> QRect:
        width, height = (self.OPEN_W, self.OPEN_H) if expanded else (self.CLOSED_W, self.CLOSED_H)
        return QRect(self._center_x - width // 2, self._top_y, width, height)

    def _monitor_configuration_changed(self, *_args) -> None:
        self._watch_screen_geometry()
        self._expand_delay.stop()
        self._collapse_delay.stop()
        self._expander.stop()
        self._center_x, self._top_y = self._fixed_anchor()
        self.setGeometry(self._rect_for(self._expanded))
        self._animation_finished()

    def showEvent(self, event) -> None:
        self._center_x, self._top_y = self._fixed_anchor()
        self.setGeometry(self._rect_for(self._expanded))
        super().showEvent(event)
        self._sync_audio_meter_state()
        if self._expanded:
            self._start_outside_click_watch()

    def hideEvent(self, event) -> None:
        self._outside_click_timer.stop()
        self._audio_meter.set_active(False)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._outside_click_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
            try:
                app.applicationStateChanged.disconnect(self._on_application_state_changed)
            except (RuntimeError, TypeError):
                pass
        self._meter_timer.stop()
        self._audio_meter.close()
        super().closeEvent(event)

    def _sync_audio_meter_state(self) -> None:
        self._audio_meter.set_active(
            bool(self.isVisible() and not self.demo and self.playback and self.playback.is_playing)
        )

    def configure(self, *, auto_expand: bool, show_lyrics: bool) -> None:
        self.auto_expand = bool(auto_expand)
        self.show_lyrics = bool(show_lyrics)
        self._expand_delay.stop()
        if not self.auto_expand and not self._pinned:
            self.set_expanded(False)
        self.update()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("桌面靈動島"))
        for button, key in ((self.prev_button, "上一首"),
                            (self.next_button, "下一首"),
                            (self.close_button, "收合靈動島")):
            button.setToolTip(tr(key))
            button.setAccessibleName(tr(key))
        self.seek_slider.setToolTip(tr("拖曳進度條調整播放位置"))
        self._update_controls()
        self.update()

    def set_playback(self, item: Playback | None) -> None:
        prior_key = self.playback.key if self.playback else None
        self.playback = item
        if item is None or item.key != prior_key:
            self._metadata_marquee.reset()
            self._lyric_marquee.reset()
            self._active_lyric_index = -1
            self._lyric_timing_window = None
            self._compact_lyric_layout_cache = None
            self._lyric_primary = ""
            self._lyric_secondary = ""
            self._lyrics_kind = ""
            self._scrubbing = False
        image_id, image_path = (item.cover_id, item.cover_path) if item else ("", "")
        if (image_id, image_path) != (self._artwork_id, self._artwork_path):
            self._artwork_id, self._artwork_path = image_id, image_path
            if image_path and Path(image_path).is_file():
                candidate = QPixmap(image_path)
                self._artwork = candidate if not candidate.isNull() else QPixmap()
            else:
                self._artwork = QPixmap()
        self._position_ms = item.position_at() if item else 0
        self._duration_ms = item.duration_ms if item else 0
        if not item or not item.is_playing or item.key != prior_key:
            self._wave_motion.reset()
            self._wave_history = self._wave_motion.bars
        self._sync_audio_meter_state()
        self._update_controls()
        self.update()

    def set_lyrics(self, result: LyricsResult, position_ms: int, offset_ms: int) -> None:
        primary, following = "", ""
        index = -1
        # Sample the actual playback position provided by the existing media
        # timeline. The paint loop only interpolates between UI ticks.
        self._lyric_position_sample_ms = max(0, int(position_ms))
        self._lyric_sampled_at = time.monotonic()
        self._lyric_timing_window = None
        if self.playback and result.is_synced:
            index = active_lyric_index(result.lines, position_ms, offset_ms)
            if index >= 0:
                primary = result.lines[index].text
                if primary.strip():
                    self._lyric_timing_window = synced_lyric_window(
                        result.lines, index, offset_ms=offset_ms,
                        track_duration_ms=self._duration_ms or self.playback.duration_ms,
                    )
            if 0 <= index + 1 < len(result.lines):
                following = result.lines[index + 1].text
        # Reset only when the currently active lyric line changes. Even when
        # adjacent LRC lines have identical text, each starts scrolling afresh.
        # Changes to next-line previews must not restart the active lyric.
        if (index != self._active_lyric_index or primary != self._lyric_primary
                or result.kind != self._lyrics_kind):
            self._lyric_marquee.reset()
        self._active_lyric_index = index
        changed = (primary, following, result.kind) != (
            self._lyric_primary, self._lyric_secondary, self._lyrics_kind
        )
        if changed:
            self._lyric_primary, self._lyric_secondary = primary, following
            self._lyrics_kind = result.kind
            self._lyric_anim.stop()
            self._lyric_reveal = 0.28
            self._lyric_anim.start()
            self.update()

    def _set_lyric_reveal(self, value) -> None:
        self._lyric_reveal = float(value)
        if self.isVisible():
            self.update()

    def set_timeline(self, position_ms: int, duration_ms: int) -> None:
        self._position_ms = max(0, int(position_ms))
        self._duration_ms = max(0, int(duration_ms))
        if not self._scrubbing:
            progress = round(self._position_ms / self._duration_ms * 1000) if self._duration_ms else 0
            self.seek_slider.setValue(max(0, min(1000, progress)))
        if self.isVisible():
            self.update()

    def _tick_audio_meter(self) -> None:
        playing = bool(self.playback and self.playback.is_playing and not self.demo
                       and self.isVisible())
        raw = self._audio_meter.snapshot().level if playing else 0.0
        # The five heights are recent measured audio peaks. Keep the actual
        # source as the only driver; make tiny variations more visible.
        self._wave_history = self._wave_motion.push(raw, active=playing)
        if self.isVisible():
            self.update()

    def _update_controls(self) -> None:
        active = self.playback is not None and not self.demo
        for button in (self.prev_button, self.play_button, self.next_button):
            button.setEnabled(active)
        self.route_button.setEnabled(True)
        self.seek_slider.setEnabled(active and bool(self.playback and self.playback.duration_ms > 0))
        playing = bool(self.playback and self.playback.is_playing)
        self.play_button.icon_name = "pause" if playing else "play"
        label = tr("暫停") if playing else tr("播放")
        self.play_button.setToolTip(label)
        self.play_button.setAccessibleName(label)
        self.play_button.update()

    def _seek_started(self) -> None:
        self._scrubbing = True
        self._seeking_key = self.playback.key if self.playback else ""
        self._preview_position = round(self.seek_slider.value() / 1000 * self._duration_ms)
        self._collapse_delay.stop()

    def _seek_moved(self, value: int) -> None:
        self._preview_position = round(value / 1000 * self._duration_ms)
        self.update()

    def _seek_released(self) -> None:
        if not self._scrubbing:
            return
        self._scrubbing = False
        if (self.playback and self.playback.key == self._seeking_key
                and self._duration_ms > 0 and not self.demo):
            self.seek_requested.emit(round(self.seek_slider.value() / 1000 * self._duration_ms))
        if not self._pointer_inside() and not self._pinned:
            self._collapse_delay.start()
        self.update()

    def _hide_controls(self) -> None:
        for item in (self.prev_button, self.play_button, self.next_button,
                     self.route_button, self.close_button, self.seek_slider):
            item.hide()

    def _layout_controls(self) -> None:
        if (not self._expanded or
                self._expander.state() != QAbstractAnimation.State.Stopped or
                self.width() < self.OPEN_W - 2 or self.height() < self.OPEN_H - 2):
            self._hide_controls()
            return
        mid = self.width() // 2
        # Apple Music expanded card: large glyphs with ample blank space.
        # All positions are relative to the fixed-width island surface.
        self.prev_button.move(mid - 112, 165)
        self.play_button.move(mid - 32, 159)
        self.next_button.move(mid + 57, 165)
        self.route_button.move(self.width() - 80, 170)
        self.close_button.hide()
        self.seek_slider.setGeometry(66, 132, self.width() - 132, 20)
        for item in (self.prev_button, self.play_button, self.next_button,
                     self.route_button, self.seek_slider):
            item.show()
            item.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_controls()
        self.update()

    def _animation_finished(self) -> None:
        self.setGeometry(self._rect_for(self._expanded))
        self._layout_controls()
        self.update()

    def set_expanded(self, expanded: bool, *, pin: bool | None = None) -> None:
        if pin is not None:
            self._pinned = bool(pin)
        expanded = bool(expanded)
        if expanded:
            self._collapse_delay.stop()
        else:
            self._expand_delay.stop()
        if self._expanded == expanded and self._expander.state() == QAbstractAnimation.State.Stopped:
            return
        self._expanded = expanded
        if expanded:
            self._start_outside_click_watch()
        else:
            self._outside_click_timer.stop()
        self._expander.stop()
        self._hide_controls()
        end = self._rect_for(expanded)
        if not self.isVisible():
            self.setGeometry(end)
            self._animation_finished()
            return
        self._expander.setStartValue(self.geometry())
        self._expander.setEndValue(end)
        self._expander.start()

    @staticmethod
    def _windows_mouse_buttons() -> tuple[bool, ...]:
        """Physical button-down states; works even if Chrome/desktop has focus."""
        if sys.platform != 'win32':
            return ()
        try:
            get_state = ctypes.windll.user32.GetAsyncKeyState
            return tuple(bool(get_state(code) & 0x8000) for code in (0x01, 0x02, 0x04))
        except (AttributeError, OSError):
            return ()

    def _start_outside_click_watch(self) -> None:
        if not self.isVisible() or not self._expanded:
            return
        # Treat a mouse button already held when expanding as a previous press;
        # otherwise an inside-click drag outside could immediately close it.
        if not self._outside_click_timer.isActive():
            self._last_mouse_buttons = self._windows_mouse_buttons()
            if self._last_mouse_buttons:
                self._outside_click_timer.start()

    def _poll_external_click(self) -> None:
        if not self._expanded or not self.isVisible():
            self._outside_click_timer.stop()
            return
        now = self._windows_mouse_buttons()
        if not now:
            return
        new_press = has_new_button_press(now, self._last_mouse_buttons)
        self._last_mouse_buttons = now
        if new_press and not self._pointer_inside():
            self._collapse_from_outside_click()

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        # Also covers quick clicks that deactivate the foreground Qt window.
        # The Win32 sampler handles the case where the app was already inactive.
        if state == Qt.ApplicationState.ApplicationInactive:
            self._collapse_from_outside_click()

    def _collapse_from_outside_click(self) -> None:
        if not should_collapse_from_outside_press(
                expanded=self._expanded, scrubbing=self._scrubbing,
                pointer_inside=False):
            return
        self._expand_delay.stop()
        self._collapse_delay.stop()
        # Avoid instantly reopening when the cursor briefly crosses the island.
        self._suppress_auto_until = time.monotonic() + 0.8
        self.set_expanded(False, pin=False)

    def eventFilter(self, watched, event) -> bool:
        # Qt only forwards events in this application. The timer above fills
        # the gap for mouse presses made in other Windows processes.
        if (self.isVisible() and self._expanded and not self._scrubbing
                and event.type() == QEvent.Type.MouseButtonPress
                and isinstance(watched, QWidget)
                and watched is not self and not self.isAncestorOf(watched)):
            self._collapse_from_outside_click()
        return super().eventFilter(watched, event)

    def _pointer_inside(self) -> bool:
        return self.frameGeometry().contains(QCursor.pos())

    def _auto_expand(self) -> None:
        if (self.auto_expand and time.monotonic() >= self._suppress_auto_until
                and self._pointer_inside()):
            self.set_expanded(True)

    def _auto_collapse(self) -> None:
        if not self._pinned and not self._scrubbing and not self._pointer_inside():
            self.set_expanded(False)

    def enterEvent(self, event) -> None:
        self._collapse_delay.stop()
        if self.auto_expand and not self._pinned and time.monotonic() >= self._suppress_auto_until:
            self._expand_delay.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._expand_delay.stop()
        if not self._pinned and not self._scrubbing:
            self._collapse_delay.start()
        super().leaveEvent(event)

    def _close_button_pressed(self) -> None:
        self._pinned = False
        self._suppress_auto_until = time.monotonic() + 0.8
        self.set_expanded(False)

    def mousePressEvent(self, event) -> None:
        # No drag state, no cursor-grab, no moving: only click-to-expand.
        if event.button() == Qt.MouseButton.LeftButton:
            self._expand_delay.stop()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._expanded and self._pinned:
                self.set_expanded(False, pin=False)
                self._suppress_auto_until = time.monotonic() + 0.7
            elif self._expanded:
                self._pinned = True
                self._collapse_delay.stop()
            else:
                self.set_expanded(True, pin=True)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.show_main_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._expand_delay.stop()
        self._collapse_delay.stop()
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #171719; color: #F8F8FA;
                    border: 1px solid #38383C; border-radius: 11px; padding: 6px; }
            QMenu::item { padding: 8px 23px; border-radius: 6px; }
            QMenu::item:selected { background: #343438; }
            QMenu::separator { height: 1px; background: #353539; margin: 5px 4px; }
        """)
        show = menu.addAction(tr("顯示主視窗"))
        pin = menu.addAction(tr("固定展開靈動島"))
        pin.setCheckable(True)
        pin.setChecked(self._pinned)
        config = menu.addAction(tr("開啟設定"))
        diagnose = menu.addAction(tr("複製音訊診斷"))
        menu.addSeparator()
        disable = menu.addAction(tr("關閉桌面靈動島"))
        selected = menu.exec(event.globalPos())
        if selected == show:
            self.show_main_requested.emit()
        elif selected == pin:
            self.set_expanded(pin.isChecked(), pin=pin.isChecked())
        elif selected == config:
            self.settings_requested.emit()
        elif selected == diagnose:
            QApplication.clipboard().setText(self._audio_meter.diagnostic_text())
        elif selected == disable:
            self.disable_requested.emit()
        elif not self._pinned and not self._pointer_inside():
            self._collapse_delay.start()
        event.accept()

    @staticmethod
    def _time_label(value_ms: int) -> str:
        seconds = max(0, int(value_ms) // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    @staticmethod
    @lru_cache(maxsize=8)
    def _installed_font(preferences: tuple[str, ...]) -> str:
        """Use a real installed UI font, not a guessed Apple or emoji font."""
        installed = set(QFontDatabase.families())
        for family in preferences:
            if family in installed:
                return family
        return QApplication.font().family()

    @staticmethod
    def _font(px: int, *, text: str = "", bold: bool = False) -> QFont:
        """Stable script-aware font matching (especially Traditional Chinese).

        Segoe UI Variable doesn't contain Chinese lyrics and can yield an
        inconsistent mixed-script fallback. Use a native CJK UI family for
        full lyric lines; use Segoe UI for Latin-only metadata.
        """
        if any("\uac00" <= ch <= "\ud7af" for ch in text):
            choices = ("Malgun Gothic", "Noto Sans CJK KR", "Segoe UI")
        elif any("\u3040" <= ch <= "\u30ff" for ch in text):
            choices = ("Yu Gothic UI", "Yu Gothic", "Noto Sans CJK JP", "Segoe UI")
        elif any("\u3400" <= ch <= "\u9fff" for ch in text):
            if current_language() == "zh_CN":
                choices = ("Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "Segoe UI")
            elif current_language() == "ja":
                choices = ("Yu Gothic UI", "Yu Gothic", "Noto Sans CJK JP", "Segoe UI")
            else:
                choices = ("Microsoft JhengHei UI", "Microsoft JhengHei",
                           "Noto Sans CJK TC", "PingFang TC", "Segoe UI")
        else:
            choices = ("Segoe UI", "Inter", "Arial", "DejaVu Sans")
        font = QFont(DynamicIslandWindow._installed_font(choices))
        font.setPixelSize(px)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Medium)
        # Explicit zero absolute tracking avoids platform-dependent values.
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.0)
        return font

    @staticmethod
    def _draw_text(painter: QPainter, text: str, rect: QRectF, *, px: int,
                   color: str, bold: bool = False,
                   align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   elide: bool = True) -> None:
        font = DynamicIslandWindow._font(px, text=str(text or ""), bold=bold)
        metrics = QFontMetrics(font)
        visible = str(text or "")
        if elide:
            visible = metrics.elidedText(visible, Qt.TextElideMode.ElideRight,
                                         max(0, int(rect.width())))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(rect, align, visible)

    @staticmethod
    def _edge_fade(painter: QPainter, rect: QRectF, *, left: bool, right: bool, strength: int = 210) -> None:
        if rect.width() <= 0 or rect.height() <= 0:
            return
        fade_w = min(18.0, rect.width() * 0.18)
        if left:
            grad = QLinearGradient(rect.left(), 0.0, rect.left() + fade_w, 0.0)
            grad.setColorAt(0.0, QColor(7, 7, 9, strength))
            grad.setColorAt(1.0, QColor(7, 7, 9, 0))
            painter.fillRect(QRectF(rect.left(), rect.top(), fade_w, rect.height()), grad)
        if right:
            grad = QLinearGradient(rect.right() - fade_w, 0.0, rect.right(), 0.0)
            grad.setColorAt(0.0, QColor(7, 7, 9, 0))
            grad.setColorAt(1.0, QColor(7, 7, 9, strength))
            painter.fillRect(QRectF(rect.right() - fade_w, rect.top(), fade_w, rect.height()), grad)

    def _draw_marquee_text(self, painter: QPainter, text: str, rect: QRectF, *, px: int,
                           color: str, bold: bool = False,
                           speed: float = 34.0, pause: float = 1.1,
                           clock: MarqueeClock, repeat: bool,
                           timed_window: tuple[int, int] | None = None) -> None:
        text = str(text or "")
        # Keep a distinct clock per lane. A paint event is not a lyric change,
        # so rapid redraws from the real audio meter cannot restart scrolling.
        elapsed = clock.elapsed(text, time.monotonic())
        if not text or rect.width() < 1:
            return
        font = self._font(px, text=text, bold=bold)
        metrics = QFontMetrics(font)
        width = float(metrics.horizontalAdvance(text))
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(font)
        painter.setPen(QColor(color))
        baseline_y = rect.y() + (rect.height() + metrics.ascent() - metrics.descent()) / 2.0
        if width <= rect.width() - 2:
            painter.drawText(QPointF(rect.x(), baseline_y), text)
            painter.restore()
            return

        if timed_window is not None and not repeat:
            start_ms, end_ms = timed_window
            # Interpolate at most 300 ms between SMTC/UI updates. Pausing and
            # scrubbing never advance the lyric independently of the player.
            forecast_ms = 0.0
            if self.playback and self.playback.is_playing and not self._scrubbing:
                forecast_ms = min(300.0, max(0.0, (time.monotonic() -
                                                    self._lyric_sampled_at) * 1000.0))
            played_ms = self._lyric_position_sample_ms + forecast_ms
            offset = timed_lyric_offset(
                elapsed_ms=played_ms - start_ms,
                duration_ms=end_ms - start_ms,
                text_width=width, viewport_width=rect.width(),
                preferred_speed=speed,
            )
        else:
            offset = marquee_offset(
                elapsed=elapsed, text_width=width, viewport_width=rect.width(),
                speed=speed, initial_pause=pause, end_pause=1.25, repeat=repeat,
            )
        # No duplicated trailing copy: the lyric scrolls to the exact end and
        # stays there. The song title can loop back to its starting position.
        painter.drawText(QPointF(rect.x() - offset, baseline_y), text)
        overflow = max(0.0, width - rect.width())
        self._edge_fade(painter, rect, left=offset > 0.5,
                        right=offset < overflow - 0.5)
        painter.restore()

    def _display_lyrics(self) -> tuple[str, str]:
        """Current line first. Fall back to track details if no synced LRC."""
        if not self.playback:
            return tr("等待 Spotify 播放"), tr("播放後自動顯示歌曲資訊")
        if not self.show_lyrics:
            return self.playback.title, self.playback.artist
        if self._lyric_primary:
            return self._lyric_primary, self._lyric_secondary
        if self._lyric_secondary:
            return self.playback.title, self._lyric_secondary
        fallback = {
            "instrumental": tr("純音樂，無同步歌詞"),
            "missing": "",
            "error": tr("歌詞暫時無法載入"),
            "plain": "",
        }.get(self._lyrics_kind, "")
        return self.playback.title, fallback

    def _draw_artwork(self, painter: QPainter, progress: float) -> None:
        # Compact mode is tuned closer to the iPhone island: a slightly smaller
        # cover with a little more breathing room around the text.
        x = 16.0 + 14.0 * progress
        y = 28.0 + 4.0 * progress
        side = 48.0 + 30.0 * progress
        radius = 11.0 + 4.0 * progress
        rect = QRectF(x, y, side, side)
        mask = QPainterPath()
        mask.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(mask)
        if not self._artwork.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            sq = min(self._artwork.width(), self._artwork.height())
            src = QRectF((self._artwork.width() - sq) / 2,
                         (self._artwork.height() - sq) / 2, sq, sq)
            painter.drawPixmap(rect, self._artwork, src)
        else:
            # Draw the placeholder using vector shapes, never an unreliable
            # musical-note glyph from a fallback system font.
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor("#565266"))
            gradient.setColorAt(0.55, QColor("#37374A"))
            gradient.setColorAt(1.0, QColor("#242431"))
            painter.fillPath(mask, gradient)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 39))
            painter.drawEllipse(QRectF(x + side*.38, y - side*.10,
                                       side*.80, side*.80))
            painter.setBrush(QColor("#F8F8FA"))
            head = QRectF(x + side*.28, y + side*.61, side*.26, side*.18)
            painter.drawEllipse(head)
            stroke = QPen(QColor("#F8F8FA"), max(1.4, side*.070))
            stroke.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(stroke)
            painter.drawLine(QPointF(x + side*.52, y + side*.69),
                             QPointF(x + side*.52, y + side*.27))
            painter.drawLine(QPointF(x + side*.52, y + side*.27),
                             QPointF(x + side*.73, y + side*.33))
        painter.restore()

    def _draw_waveform(self, painter: QPainter, progress: float) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        playing = bool(self.playback and self.playback.is_playing and not self.demo)
        bars = list(self._wave_history if playing else (0.0,) * 6)
        bar_width = 3.5 + 0.1 * progress
        gap = 2.8
        total_w = len(bars) * bar_width + (len(bars) - 1) * gap
        x0 = self.width() - 32.0 - total_w
        cy = 52.0 + 11.0 * progress
        max_h = 22.0 + 7.0 * progress
        min_h = 4.0
        base_color = QColor('#CACAD0' if progress < 0.5 else '#97979D') if playing else QColor('#56565C')
        for idx, level in enumerate(bars):
            level = max(0.0, min(1.0, float(level)))
            # These six samples all originate in the real Windows audio meter.
            # A gentle emphasis on the central bars matches the iOS material.
            center = (len(bars) - 1) / 2.0
            center_bias = 1.0 - abs(idx - center) / (center + 0.001)
            height = (min_h + (max_h - min_h) * (level ** 0.72)) * (0.80 + 0.20 * center_bias)
            color = QColor(base_color)
            color.setAlpha(238 if playing else 155)
            painter.setBrush(color)
            x = x0 + idx * (bar_width + gap)
            painter.drawRoundedRect(QRectF(x, cy - height / 2.0, bar_width, height),
                                    bar_width / 2.0, bar_width / 2.0)
        painter.restore()

    def _compact_lines(self) -> tuple[str, str]:
        """Top: artist - title; bottom: active lyric or an honest status."""
        return compact_display_lines(
            self.playback,
            current_lyric=self._lyric_primary,
            lyrics_kind=self._lyrics_kind,
            show_lyrics=self.show_lyrics,
        )

    def _compact_wrapped_layout(self, text: str, width: float, font: QFont):
        """Cache properly-shaped, word/CJK-wrapped text for the collapsed view.

        Qt's shaping engine handles Japanese and Chinese punctuation and never
        inserts ellipses. One active LRC line can occupy several visual rows;
        the painter reveals a maximum of two rows at any instant.
        """
        key = (text, round(width, 2), font.family(), font.pixelSize(), int(font.weight()))
        cached = self._compact_lyric_layout_cache
        if cached is not None and cached[0] == key:
            return cached[1], cached[2], cached[3]
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(Qt.AlignmentFlag.AlignLeft)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        count = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1.0, width))
            line.setPosition(QPointF(0.0, height))
            height += line.height()
            count += 1
        layout.endLayout()
        self._compact_lyric_layout_cache = (key, layout, height, count)
        return layout, height, count

    def _draw_compact_lyric(self, painter: QPainter, lyric: str, rect: QRectF) -> None:
        if not lyric or rect.width() <= 0 or rect.height() <= 0:
            return
        font = self._font(15, text=lyric, bold=True)
        layout, content_h, _ = self._compact_wrapped_layout(lyric, rect.width(), font)
        now = time.monotonic()
        elapsed = self._lyric_marquee.elapsed(lyric, now)
        timed_window = self._lyric_timing_window if self._lyric_primary else None
        elapsed_ms = duration_ms = None
        if timed_window is not None:
            start_ms, end_ms = timed_window
            # Use Spotify timeline; do not let a paused lyric keep scrolling.
            forecast_ms = 0.0
            if self.playback and self.playback.is_playing and not self._scrubbing:
                forecast_ms = min(300.0, max(0.0, (now - self._lyric_sampled_at) * 1000.0))
            elapsed_ms = self._lyric_position_sample_ms + forecast_ms - start_ms
            duration_ms = end_ms - start_ms
        travel = max(0.0, content_h - rect.height())
        offset = two_line_vertical_offset(
            content_height=content_h, viewport_height=rect.height(),
            elapsed=elapsed, elapsed_ms=elapsed_ms, duration_ms=duration_ms,
        )
        # One short lyric row is centered in the available two-line viewport.
        top = rect.top() + max(0.0, (rect.height() - content_h) * 0.5)
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(font)
        painter.setPen(QColor("#FAFAFD"))
        layout.draw(painter, QPointF(rect.left(), top - offset))
        if travel > 0:
            fade_h = min(5.0, rect.height() * 0.10)
            if offset > 0.5:
                fade = QLinearGradient(0.0, rect.top(), 0.0, rect.top() + fade_h)
                fade.setColorAt(0.0, QColor(0, 0, 0, 140))
                fade.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), fade_h), fade)
            if offset < travel - 0.5:
                fade = QLinearGradient(0.0, rect.bottom() - fade_h, 0.0, rect.bottom())
                fade.setColorAt(0.0, QColor(0, 0, 0, 0))
                fade.setColorAt(1.0, QColor(0, 0, 0, 140))
                painter.fillRect(QRectF(rect.left(), rect.bottom() - fade_h, rect.width(), fade_h), fade)
        painter.restore()

    def _draw_compact(self, painter: QPainter, alpha: float) -> None:
        if alpha <= 0:
            return
        metadata, lyric = self._compact_lines()
        # One metadata row + two rows of the active lyric, beside a centered
        # cover and real measured-audio waveform. Keep the expanded card intact.
        text_x = 78.0
        waveform_lane = 70.0
        text_width = max(0.0, self.width() - text_x - waveform_lane)
        metadata_rect = QRectF(text_x, 18.0, text_width, 19.0)
        lyric_rect = QRectF(text_x, 42.0, text_width, 47.0)
        painter.save()
        painter.setOpacity(alpha)
        self._draw_marquee_text(painter, metadata, metadata_rect,
                                px=12, color="#C8C8CE", speed=28.0, pause=1.35,
                                clock=self._metadata_marquee, repeat=True)
        painter.setOpacity(alpha * self._lyric_reveal)
        self._draw_compact_lyric(painter, lyric, lyric_rect)
        painter.restore()

    def _draw_expanded(self, painter: QPainter, alpha: float) -> None:
        if alpha <= 0:
            return
        painter.save()
        painter.setOpacity(alpha)
        title = self.playback.title if self.playback else tr("等待 Spotify 播放")
        artist = self.playback.artist if self.playback else tr("請先開啟 Spotify 桌面版")
        # Text scales and baseline offsets calibrated against the supplied
        # iPhone expanded-island screenshot. Album art is painted separately.
        self._draw_text(painter, title, QRectF(129, 48, self.width() - 222, 26),
                        px=19, bold=True, color="#F8F8FA")
        self._draw_text(painter, artist, QRectF(129, 77, self.width() - 222, 23),
                        px=15, color="#909098")

        position = self._preview_position if self._scrubbing else self._position_ms
        remain_ms = max(0, self._duration_ms - position)
        self._draw_text(painter, self._time_label(position), QRectF(36, 134, 35, 20),
                        px=12, color="#9B9BA1")
        total = f"-{self._time_label(remain_ms)}" if self._duration_ms else "--:--"
        self._draw_text(painter, total, QRectF(self.width() - 72, 134, 40, 20),
                        px=12, color="#9B9BA1",
                        align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        painter.restore()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        progress = max(0.0, min(1.0, (self.height() - self.CLOSED_H) /
                                (self.OPEN_H - self.CLOSED_H)))
        outer = QRectF(self.PAD + 0.5, self.PAD + 0.5,
                       self.width() - 2 * self.PAD - 1,
                       self.height() - 2 * self.PAD - 1)
        radius = ((self.CLOSED_H - 2 * self.PAD - 1) / 2.0) * (1 - progress) + 44.0 * progress

        # Keep the same almost-true-black surface in both modes. No visible
        # gradient, stacked borders or decorative glows: the artwork is the color.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 13))
        painter.drawRoundedRect(outer.adjusted(-1.0, -0.5, 1.0, 1.0),
                                radius + 0.5, radius + 0.5)
        painter.setBrush(QColor("#000000"))
        painter.drawRoundedRect(outer, radius, radius)

        clip = QPainterPath()
        clip.addRoundedRect(outer, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        self._draw_artwork(painter, progress)
        self._draw_waveform(painter, progress)
        self._draw_compact(painter, max(0.0, min(1.0, (0.45 - progress) / 0.35)))
        self._draw_expanded(painter, max(0.0, min(1.0, (progress - .35) / .5)))
        painter.restore()
        painter.end()
