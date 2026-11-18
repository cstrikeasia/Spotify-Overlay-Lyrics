"""Frameless, always-on-top karaoke window for Windows 10/11."""
from __future__ import annotations

import logging
from functools import lru_cache
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, Property, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizeGrip, QSlider, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget, QSizePolicy,
)

from .animated_lyrics import AnimatedLyricsView
from .dynamic_island import DynamicIslandWindow
from .lyric_script import display_lyrics
from .toolbar_icons import VectorToolButton
from .artwork import Artwork, verified_artwork_bytes
from .i18n import LANGUAGES, current_language, set_language, tr
from .song_theme import DEFAULT_THEME, theme_from_pixels
from .lyrics import LyricsService
from .lyric_timeline import active_lyric_index
from .media import MediaMonitor
from .models import NO_LYRICS_MESSAGE, LyricLine, LyricsResult, Playback
from .obs_source import ObsOverlayServer, make_overlay_state
from .settings import Settings, data_dir, load_settings, save_settings, update_windows_startup

LOG = logging.getLogger(__name__)

STYLE = """
/* Spotify-inspired desktop palette. Near-black shells, muted chrome and one accent. */
QWidget { color:#F7F7F7; font-family:'Segoe UI Variable','Segoe UI','Microsoft JhengHei UI',sans-serif; font-size:12px; }
QToolTip { color:#FFFFFF; background:#282828; border:1px solid #424242; padding:7px 11px; }
QMenu { background:#202020; color:#F5F5F5; border:1px solid #353535; padding:6px; border-radius:8px; }
QMenu::item { padding:9px 28px 9px 14px; border-radius:5px; }
QMenu::item:selected { background:#363636; color:#FFFFFF; }
QMenu::item:disabled { color:#777777; }
QMenu::separator { height:1px; background:#393939; margin:6px 9px; }
QMessageBox, QColorDialog { background:#1B1B1B; }
QToolButton#toolbarIcon { color:#B3B3B3; background:#232323; border:0; border-radius:16px; padding:0; }
QToolButton#toolbarIcon:hover { color:#FFFFFF; background:#2D2D2D; }
QToolButton#toolbarIcon:pressed { background:#383838; }
QToolButton#toolbarIcon:disabled { color:#626262; }
QToolButton#toolbarIcon[variant="accent"] { color:#E7E7E7; }
QToolButton#toolbarIcon[variant="accent"]:hover { color:#1ED760; background:#29332C; }
QToolButton#toolbarIcon[variant="danger"]:hover { color:#FFFFFF; background:#B32935; }
QToolButton#toolbarIcon[variant="transport"] { background:#FFFFFF; border-radius:19px; }
QToolButton#toolbarIcon[variant="transport"]:hover { background:#F0F0F0; }
QToolButton#toolbarIcon[variant="transport"]:disabled { background:#484848; }
QToolButton#toolbarIcon[variant="skip"] { background:transparent; border:0; }
QToolButton#toolbarIcon[variant="skip"]:hover { background:#303030; }
QLabel#playerBrand { color:#FFFFFF; font-size:14px; font-weight:800; }
QLabel#playerSubtitle { color:#777777; font-size:10px; font-weight:700; }
QLabel#playerLive { color:#A7A7A7; background:#242424; border-radius:10px; padding:3px 9px; font-size:10px; font-weight:700; }
QFrame#trackPanel { background:#232323; border:0; border-radius:12px; }
QLabel#eyebrow { color:#1ED760; font-size:10px; font-weight:800; }
QLabel#trackTitle { color:#FFFFFF; font-size:21px; font-weight:850; }
QLabel#trackArtist { color:#B3B3B3; font-size:12px; font-weight:550; }
QLabel#trackTime { color:#A1A1A1; font-size:10px; font-weight:600; }
QFrame#lyricsPanel { background:#202226; border:none; border-radius:12px; }
QLabel#lyricHeading { color:#FFFFFF; font-size:15px; font-weight:800; }
QLabel#lyricEyebrow { color:#C4D6C9; font-size:10px; font-weight:600; }
QSlider#playProgress { background:transparent; min-height:18px; max-height:18px; }
QSlider#playProgress::groove:horizontal { background:#535353; height:4px; border-radius:2px; }
QSlider#playProgress::sub-page:horizontal { background:#FFFFFF; border-radius:2px; }
QSlider#playProgress::add-page:horizontal { background:#535353; border-radius:2px; }
QSlider#playProgress::handle:horizontal { background:#FFFFFF; width:12px; margin:-4px 0; border-radius:6px; }
QSlider#playProgress::handle:horizontal:hover { width:14px; margin:-5px 0; border-radius:7px; }
QDialog#settingsDialog { background:transparent; }
QFrame#settingsShell { background:#121212; border:1px solid #333333; border-radius:17px; }
QFrame#settingsSidebar { background:#0B0B0B; border-right:1px solid #272727; border-top-left-radius:17px; border-bottom-left-radius:17px; }
QWidget#settingsContent { background:transparent; }
QLabel#settingsBrand { color:#FFFFFF; font-size:16px; font-weight:850; }
QLabel#settingsBrandSubtitle { color:#777777; font-size:10px; font-weight:700; }
QLabel#settingsSideCaption { color:#797979; font-size:10px; font-weight:800; }
QLabel#settingsSideBottom { color:#6B6B6B; font-size:10px; }
QPushButton#settingsNav { text-align:left; color:#A7A7A7; background:transparent; border:0; border-radius:7px; padding:11px 13px; font-size:13px; font-weight:700; }
QPushButton#settingsNav:hover { background:#212121; color:#FFFFFF; }
QPushButton#settingsNav:checked { background:#282828; color:#FFFFFF; border-left:3px solid #1ED760; padding-left:10px; }
QLabel#settingsEyebrow { color:#1ED760; font-size:10px; font-weight:800; }
QLabel#settingsTitle { color:#FFFFFF; font-size:26px; font-weight:850; }
QLabel#settingsSubtitle { color:#9B9B9B; font-size:12px; }
QLabel#settingsGroupTitle { color:#F5F5F5; font-size:15px; font-weight:800; }
QLabel#settingsGroupCaption { color:#9F9F9F; font-size:11px; }
QLabel#settingsFieldLabel { color:#F1F1F1; font-size:12px; font-weight:700; }
QLabel#settingsFieldHint { color:#929292; font-size:11px; }
QLabel#settingsValue { color:#1ED760; font-size:12px; font-weight:800; }
QLabel#settingsUrl { color:#BDEECD; font-size:11px; }
QLabel#settingsAbout { color:#AFAFAF; font-size:12px; }
QLabel#settingsFooterHint { color:#808080; font-size:11px; }
QFrame#settingsGroup { background:#202020; border:1px solid #2A2A2A; border-radius:11px; }
QFrame#settingsUrlBox { background:#161616; border:1px solid #343434; border-radius:8px; }
QScrollArea#settingsScroll, QWidget#settingsPage { background:transparent; border:0; }
QScrollBar:vertical { width:7px; background:transparent; margin:1px 0; }
QScrollBar::handle:vertical { background:#525252; border-radius:3px; min-height:23px; }
QScrollBar::handle:vertical:hover { background:#747474; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; background:transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QSpinBox, QComboBox { color:#FFFFFF; background:#353535; border:1px solid #454545; border-radius:6px; padding:8px 10px; min-height:20px; selection-background-color:#1ED760; }
QSpinBox:hover, QComboBox:hover { border-color:#767676; }
QSpinBox:focus, QComboBox:focus { border-color:#1ED760; }
QSpinBox::up-button, QSpinBox::down-button { width:18px; }
QComboBox::drop-down { border:0; width:23px; }
QComboBox QAbstractItemView { color:#FFFFFF; background:#272727; border:1px solid #404040; selection-background-color:#454545; }
QPushButton { color:#FFFFFF; background:#303030; border:0; border-radius:18px; padding:8px 16px; font-size:12px; font-weight:750; min-height:21px; }
QPushButton:hover { background:#414141; }
QPushButton#primary { color:#07130A; background:#1ED760; border-radius:19px; padding:9px 22px; }
QPushButton#primary:hover { background:#3BE477; }
QPushButton#secondary { background:#303030; color:#FFFFFF; }
QPushButton#secondary:hover { background:#454545; }
QSlider::groove:horizontal { background:#505050; height:4px; border-radius:2px; }
QSlider::sub-page:horizontal { background:#1ED760; border-radius:2px; }
QSlider::handle:horizontal { background:#FFFFFF; width:13px; margin:-5px 0; border-radius:6px; }
QCheckBox { color:#F5F5F5; spacing:10px; font-size:12px; }
QCheckBox::indicator { width:19px; height:19px; background:#282828; border:1px solid #777777; border-radius:5px; }
QCheckBox::indicator:checked { background:#1ED760; border-color:#1ED760; }
QCheckBox:disabled { color:#686868; }
"""


_BRAND_MARK_FILE = Path(__file__).resolve().parent.parent / "assets" / "spotify_mark.png"
_BRAND_MARK_VECTOR_FILE = Path(__file__).resolve().parent.parent / "assets" / "spotify_mark.svg"


@lru_cache(maxsize=48)
def _brand_mark_pixmap(physical_size: int) -> QPixmap:
    """Create a sharp Spotify mark at the *physical* monitor pixel size.

    A single 256px PNG shrunk to 29px with Qt's default interpolation was
    producing jagged/misshapen Spotify wave lines on Windows. Render SVG at
    4x of the physical target and downsample only once. The cache also keeps
    this inexpensive when Qt repaints an always-on-top translucent window.
    """
    physical_size = max(1, int(physical_size))
    oversampling = 4
    raster_size = physical_size * oversampling
    image = QImage(raster_size, raster_size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    svg = QSvgRenderer(str(_BRAND_MARK_VECTOR_FILE))
    if svg.isValid():
        svg.render(painter, QRectF(0, 0, raster_size, raster_size))
    else:
        # Keep backwards compatibility with packages missing the SVG asset.
        image_fallback = QImage(str(_BRAND_MARK_FILE))
        if not image_fallback.isNull():
            painter.drawImage(QRectF(0, 0, raster_size, raster_size), image_fallback)
    painter.end()
    target = image.scaled(
        physical_size, physical_size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QPixmap.fromImage(target)


def _paint_spotify_mark(painter: QPainter, size: float, dpr: float = 1.0) -> None:
    """Paint 1:1 at the monitor's actual pixel density, including 125/150%."""
    physical_size = max(1, round(size * max(float(dpr), 1.0)))
    mark = _brand_mark_pixmap(physical_size)
    if not mark.isNull():
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(QRectF(0, 0, size, size), mark, QRectF(mark.rect()))


class SpotifyMark(QWidget):
    """A clean brand mark for main and settings windows at every Windows DPI."""

    def __init__(self, size: int = 32, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        _paint_spotify_mark(painter, float(side), self.devicePixelRatioF())
        painter.end()


class SpotifySwitch(QCheckBox):
    """Accessible, keyboard-operable preference switch with native Spotify styling."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(36)

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        return QSize(max(base.width() + 48, 230), max(36, base.height()))

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        available = max(16, w - 65)
        font = QFont(self.font())
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        painter.setPen(QColor("#F5F5F5") if self.isEnabled() else QColor("#696969"))
        painter.drawText(QRect(0, 0, available, h),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, available))
        x, y, width, height = w - 45, (h - 23) // 2, 43, 23
        track = QColor("#1ED760") if self.isChecked() else QColor("#5B5B5B")
        if not self.isEnabled():
            track = QColor("#333333")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(x, y, width, height), 11.5, 11.5)
        knob = (x + width - 20) if self.isChecked() else x + 3
        painter.setBrush(QColor("#0B0B0B") if self.isChecked() and self.isEnabled()
                         else QColor("#F4F4F4") if self.isEnabled() else QColor("#838383"))
        painter.drawEllipse(QRectF(knob, y + 3, 17, 17))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#E3E3E3"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(x - 2, y - 2, width + 4, height + 4), 14, 14)
        painter.end()


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Use individually rasterized icon sizes instead of Windows scaling a PNG."""
    result = QIcon()
    for side in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        result.addPixmap(_brand_mark_pixmap(side))
    return result


def rounded_cover(pixmap: QPixmap, size: int = 78) -> QPixmap:
    """Scale album art to a centered square, clipping corners cleanly."""
    output = QPixmap(size, size)
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), 7, 7)
    painter.setClipPath(path)
    scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
    offset_x = (scaled.width() - size) / 2
    offset_y = (scaled.height() - size) / 2
    painter.drawPixmap(QRectF(0, 0, size, size), scaled,
                       QRectF(offset_x, offset_y, size, size))
    painter.end()
    return output


class ArtworkCover(QWidget):
    """Paint artwork in its *own* fixed layout slot, never an effect child.

    A QGraphicsOpacityEffect on a child QLabel inside a parent
    QGraphicsDropShadowEffect can reuse an obsolete source offset after a
    native hide/show, window resize or transparent/opaque mode change.  Qt
    then composites the correct artwork at an unrelated screen coordinate.
    Painting the cached pixmap directly in this widget avoids that path.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._artwork = QPixmap()
        self._artwork_opacity = 0.0
        self.setFixedSize(78, 78)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setToolTip(tr("目前的 Spotify 專輯封面"))

    @Property(float)
    def artworkOpacity(self) -> float:
        return self._artwork_opacity

    @artworkOpacity.setter
    def artworkOpacity(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if self._artwork_opacity != value:
            self._artwork_opacity = value
            self.update()

    def set_artwork(self, pixmap: QPixmap) -> None:
        # Always use a pre-scaled 78 x 78 pixmap; the QLayout owns geometry.
        self._artwork = pixmap
        self.update()

    def clear_artwork(self) -> None:
        self._artwork = QPixmap()
        self._artwork_opacity = 0.0
        self.update()

    def has_artwork(self) -> bool:
        return not self._artwork.isNull()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setClipRect(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#353535"))
        painter.drawRoundedRect(QRectF(self.rect()), 7.0, 7.0)
        if self._artwork.isNull():
            # Vector music note rather than an OS-dependent emoji glyph.
            painter.save()
            painter.scale(self.width() / 78.0, self.height() / 78.0)
            pen = QPen(QColor("#A2A2A2"), 2.6, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(32, 29, 47, 25)
            painter.drawLine(32, 29, 32, 50)
            painter.drawLine(47, 25, 47, 46)
            painter.drawEllipse(QRectF(24, 46, 8, 6))
            painter.drawEllipse(QRectF(39, 42, 8, 6))
            painter.restore()
        else:
            painter.save()
            painter.setOpacity(self._artwork_opacity)
            # Small responsive windows scale the whole artwork, never crop it.
            painter.drawPixmap(self.rect(), self._artwork)
            painter.restore()
        painter.end()


def tool_button(icon: str, tooltip: str, *, width: int = 34) -> VectorToolButton:
    """Emoji-free SVG-like Qt vector button for every desktop toolbar action."""
    return VectorToolButton(icon, tooltip, width=width)


class SeekSlider(QSlider):
    """A click-to-seek and drag-to-scrub slider, without page-step jumps.

    The slot listening to sliderReleased sends the position to Spotify exactly
    once. A polling update never overwrites the thumb while it is held down.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._scrubbing = False
        self._keyboard_scrubbing = False
        self.setTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _value_at_x(self, x: float) -> int:
        # Account for half of the handle, so the endpoints are actually
        # reachable. Keep this calculation independent of text-label widths.
        edge = 6.0
        usable = max(1.0, self.width() - edge * 2)
        fraction = max(0.0, min(1.0, (x - edge) / usable))
        if self.invertedAppearance():
            fraction = 1.0 - fraction
        return self.minimum() + round(fraction * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._scrubbing = True
            self.setSliderDown(True)  # Emits sliderPressed once.
            self.setSliderPosition(self._value_at_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._scrubbing:
            self.setSliderPosition(self._value_at_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._scrubbing and event.button() == Qt.MouseButton.LeftButton:
            self.setSliderPosition(self._value_at_x(event.position().x()))
            self._scrubbing = False
            self.setSliderDown(False)  # Emits sliderReleased once.
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        # Keyboard navigation is a real seek operation too; commit once after
        # the user releases the key, rather than creating an inert fake thumb.
        seek_keys = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up,
                     Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                     Qt.Key.Key_Home, Qt.Key.Key_End}
        if self.isEnabled() and event.key() in seek_keys and not self._keyboard_scrubbing:
            self._keyboard_scrubbing = True
            self.setSliderDown(True)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if self._keyboard_scrubbing and not event.isAutoRepeat():
            self._keyboard_scrubbing = False
            self.setSliderDown(False)
        super().keyReleaseEvent(event)


class ElidedLabel(QLabel):
    """Keep long album and track metadata inside the fixed-size media row."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        self._full_text = ""
        super().__init__(parent)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text)
        self._sync_elision()

    def _sync_elision(self) -> None:
        available = max(1, self.contentsRect().width() - 3)
        displayed = (self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, available
        ) if available > 25 else self._full_text)
        super().setText(displayed)
        self.setToolTip(self._full_text if displayed != self._full_text else "")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_elision()


class DragHandle(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class EventBridge(QObject):
    playback = Signal(object)
    lyric = Signal(str, object)
    error = Signal(str)
    control = Signal(str, bool, str)


class SettingsDialog(QDialog):
    """Modern, accessible preference center; the parent overlay cannot cover it."""

    _PAGES = (
        ("外觀", "外觀", "設定懸浮播放器的版面與顏色"),
        ("歌詞", "歌詞", "同步顯示與轉場速度"),
        ("OBS", "OBS 整合", "管理直播與錄影的歌詞輸出"),
        ("系統", "系統", "視窗層級與開機啟動"),
    )

    def __init__(self, current: Settings, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle(tr("設定 · Spotify Lyrics"))
        flags = (Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowSystemMenuHint)
        # Keep the settings dialog usable in front of the always-on-top island,
        # without hiding or disabling the island's visual presentation.
        island = getattr(parent, 'island', None)
        if (current.topmost or bool(parent.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                or (island is not None and island.isVisible())):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(710, 510)
        screen = parent.screen() or QApplication.primaryScreen()
        if screen is not None:
            bounds = screen.availableGeometry()
            self.resize(min(840, max(710, bounds.width() - 72)),
                        min(638, max(510, bounds.height() - 72)))
        else:
            self.resize(840, 618)
        self.setStyleSheet(STYLE)
        self.values = replace(current)

        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(9, 9, 9, 9)
        window_layout.setSpacing(0)
        shell_frame = QFrame(self)
        shell_frame.setObjectName("settingsShell")
        window_layout.addWidget(shell_frame)
        shadow = QGraphicsDropShadowEffect(shell_frame)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 110))
        shadow.setOffset(0, 5)
        shell_frame.setGraphicsEffect(shadow)
        shell = QHBoxLayout(shell_frame)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame(shell_frame)
        sidebar.setObjectName("settingsSidebar")
        sidebar.setFixedWidth(194)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(17, 24, 15, 22)
        side_layout.setSpacing(6)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.addWidget(SpotifyMark(32, sidebar))
        brand = QLabel("Spotify Lyrics")
        brand.setObjectName("settingsBrand")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        side_layout.addLayout(brand_row)
        side_layout.addSpacing(35)
        side_label = QLabel(tr("設定選單"))
        side_label.setObjectName("settingsSideCaption")
        side_layout.addWidget(side_label)
        side_layout.addSpacing(9)
        self.nav_buttons: list[QPushButton] = []
        for i, title in enumerate((tr("外觀"), tr("歌詞"), "OBS", tr("系統"))):
            button = QPushButton(title)
            button.setObjectName("settingsNav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(43)
            button.clicked.connect(lambda _checked=False, index=i: self._show_page(index))
            self.nav_buttons.append(button)
            side_layout.addWidget(button)
        side_layout.addStretch(1)
        sidebar_note = QLabel(tr("本機偏好設定\n非 Spotify 官方應用程式"))
        sidebar_note.setObjectName("settingsSideBottom")
        side_layout.addWidget(sidebar_note)
        shell.addWidget(sidebar)

        content = QWidget(shell_frame)
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(26, 20, 27, 20)
        content_layout.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(8)
        header_handle = DragHandle(content)
        header_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        title_col = QVBoxLayout(header_handle)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)
        eyebrow = QLabel("SPOTIFY LYRICS  /  PREFERENCES")
        eyebrow.setObjectName("settingsEyebrow")
        eyebrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_col.addWidget(eyebrow)
        self.page_title = QLabel()
        self.page_title.setObjectName("settingsTitle")
        self.page_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_col.addWidget(self.page_title)
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("settingsSubtitle")
        self.page_subtitle.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_col.addWidget(self.page_subtitle)
        header.addWidget(header_handle, 1)
        close_button = tool_button("x", tr("關閉設定"))
        close_button.setProperty("variant", "danger")
        close_button.clicked.connect(self.reject)
        header.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header)
        content_layout.addSpacing(23)

        self.page_stack = QStackedWidget(content)
        self.page_stack.setObjectName("settingsPages")
        self.page_stack.addWidget(self._create_appearance_page(current))
        self.page_stack.addWidget(self._create_lyrics_page(current))
        self.page_stack.addWidget(self._create_obs_page(current))
        self.page_stack.addWidget(self._create_system_page(current))
        content_layout.addWidget(self.page_stack, 1)
        content_layout.addSpacing(13)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        note = QLabel(tr("變更將儲存至這台電腦"))
        note.setObjectName("settingsFooterHint")
        footer.addWidget(note, 1)
        cancel = QPushButton(tr("取消"))
        cancel.setObjectName("secondary")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        save = QPushButton(tr("儲存設定"))
        save.setObjectName("primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.setDefault(True)
        save.clicked.connect(self.accept)
        footer.addWidget(save)
        grip = QSizeGrip(shell_frame)
        grip.setFixedSize(15, 15)
        grip.setToolTip(tr("拖曳以調整設定視窗大小"))
        footer.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom)
        content_layout.addLayout(footer)
        shell.addWidget(content, 1)
        self._show_page(0)

    def _new_page(self):
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("settingsPage")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 1, 8, 8)
        layout.setSpacing(13)
        scroll.setWidget(body)
        return scroll, layout

    @staticmethod
    def _card(title: str, caption: str = ""):
        frame = QFrame()
        frame.setObjectName("settingsGroup")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 17, 18, 18)
        layout.setSpacing(14)
        label = QLabel(title)
        label.setObjectName("settingsGroupTitle")
        layout.addWidget(label)
        if caption:
            text = QLabel(caption)
            text.setWordWrap(True)
            text.setObjectName("settingsGroupCaption")
            layout.addWidget(text)
        return frame, layout

    @staticmethod
    def _field_row(title: str, widget: QWidget, caption: str = "") -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        label = QLabel(title)
        label.setObjectName("settingsFieldLabel")
        text_col.addWidget(label)
        if caption:
            hint = QLabel(caption)
            hint.setObjectName("settingsFieldHint")
            hint.setWordWrap(True)
            text_col.addWidget(hint)
        row.addLayout(text_col, 1)
        row.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _create_appearance_page(self, current: Settings) -> QScrollArea:
        scroll, layout = self._new_page()
        card, group = self._card(tr("桌面模式"), tr("在應用模式與工具列的浮動模式之間切換。"))
        self.desktop_cover = SpotifySwitch(tr("完整視窗顯示專輯封面"))
        self.desktop_cover.setChecked(current.desktop_cover_enabled)
        group.addWidget(self.desktop_cover)
        self.adaptive_background = SpotifySwitch(tr("依專輯封面自動配色歌詞背景"))
        self.adaptive_background.setChecked(current.adaptive_lyrics_background)
        group.addWidget(self.adaptive_background)
        note = QLabel(tr("每首歌曲使用不同深色調，並維持白色歌詞可讀性"))
        note.setObjectName("settingsFieldHint")
        group.addWidget(note)
        layout.addWidget(card)

        card, group = self._card(tr("視覺調整"))
        self.font = QSpinBox()
        self.font.setRange(17, 44)
        self.font.setSuffix(" px")
        self.font.setValue(current.font_size)
        self.font.setFixedWidth(120)
        group.addLayout(self._field_row(tr("歌詞字體大小"), self.font, tr("桌面與 OBS 歌詞的主要字級")))

        self._selected_color = current.desktop_text_color
        self.color_preview = QLabel("Aa")
        self.color_preview.setObjectName("settingsColorPreview")
        self.color_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.color_preview.setFixedSize(44, 36)
        self.color_button = QPushButton(tr("更換顏色"))
        self.color_button.setObjectName("secondary")
        self.color_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.color_button.clicked.connect(self._choose_color)
        color_selector = QWidget()
        color_line = QHBoxLayout(color_selector)
        color_line.setContentsMargins(0, 0, 0, 0)
        color_line.setSpacing(7)
        color_line.addWidget(self.color_preview)
        color_line.addWidget(self.color_button)
        self._update_color_preview()
        group.addLayout(self._field_row(tr("純文字歌詞顏色"), color_selector, tr("透明背景模式的文字色彩")))

        # Distinct controls: full player opacity never changes the overlay
        # lyric window, and vice versa.
        self.opacity_label = QLabel(f"{current.opacity}%")
        self.opacity_label.setObjectName("settingsValue")
        self.opacity_label.setFixedWidth(40)
        self.opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(45, 100)
        self.opacity.setValue(current.opacity)
        self.opacity.setFixedWidth(150)
        self.opacity.valueChanged.connect(lambda val: self.opacity_label.setText(f"{val}%"))
        opacity_widget = QWidget()
        opacity_row = QHBoxLayout(opacity_widget)
        opacity_row.setContentsMargins(0, 0, 0, 0)
        opacity_row.setSpacing(9)
        opacity_row.addWidget(self.opacity)
        opacity_row.addWidget(self.opacity_label)
        group.addLayout(self._field_row(tr("應用外框透明度"), opacity_widget,
                                        tr("只調整外框背景；歌曲、歌詞與按鈕保持不透明")))

        self.overlay_opacity_label = QLabel(f"{current.overlay_opacity}%")
        self.overlay_opacity_label.setObjectName("settingsValue")
        self.overlay_opacity_label.setFixedWidth(40)
        self.overlay_opacity_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.overlay_opacity = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity.setRange(45, 100)
        self.overlay_opacity.setValue(current.overlay_opacity)
        self.overlay_opacity.setFixedWidth(150)
        self.overlay_opacity.valueChanged.connect(
            lambda val: self.overlay_opacity_label.setText(f"{val}%"))
        overlay_opacity_widget = QWidget()
        overlay_row = QHBoxLayout(overlay_opacity_widget)
        overlay_row.setContentsMargins(0, 0, 0, 0)
        overlay_row.setSpacing(9)
        overlay_row.addWidget(self.overlay_opacity)
        overlay_row.addWidget(self.overlay_opacity_label)
        group.addLayout(self._field_row(tr("浮動歌詞透明度"), overlay_opacity_widget,
                                        tr("只調整純文字浮動模式，不影響應用視窗")))
        hint = QLabel(tr("兩種模式的位置與透明度分開儲存，拖曳後會自動記住"))
        hint.setWordWrap(True)
        hint.setObjectName("settingsFieldHint")
        group.addWidget(hint)
        layout.addWidget(card)
        layout.addStretch(1)
        return scroll

    def _create_lyrics_page(self, current: Settings) -> QScrollArea:
        scroll, layout = self._new_page()
        card, group = self._card(tr("時間軸設定"), tr("使畫面上的字句與音樂節拍維持一致。"))
        self.offset = QSpinBox()
        self.offset.setRange(-10000, 10000)
        self.offset.setSingleStep(100)
        self.offset.setSuffix(" ms")
        self.offset.setValue(current.offset_ms)
        self.offset.setFixedWidth(145)
        group.addLayout(self._field_row(tr("時間補償"), self.offset, tr("正值提前顯示，負值延後顯示；預設 +300 ms")))
        self.lines = QComboBox()
        self.lines.addItem(tr("3 行 · 精簡"), 3)
        self.lines.addItem(tr("5 行 · 完整"), 5)
        self.lines.setCurrentIndex(0 if current.line_count == 3 else 1)
        self.lines.setMinimumWidth(145)
        group.addLayout(self._field_row(tr("同步歌詞行數"), self.lines, tr("純文字模式固定只顯示目前一句")))
        layout.addWidget(card)

        card, group = self._card(tr("歌詞文字"), tr("與介面語言獨立；不會修改下載的原始 LRC 或同步時間。"))
        self.lyrics_script = QComboBox()
        self.lyrics_script.setObjectName("lyricsScript")
        self.lyrics_script.addItem(tr("自動（依歌曲名稱判斷）"), "auto")
        self.lyrics_script.addItem(tr("強制繁體中文"), "traditional")
        self.lyrics_script.addItem(tr("強制簡體中文"), "simplified")
        self.lyrics_script.addItem(tr("保留來源原文"), "original")
        self.lyrics_script.setMinimumWidth(195)
        index = self.lyrics_script.findData(current.lyrics_script)
        self.lyrics_script.setCurrentIndex(max(0, index))
        group.addLayout(self._field_row(
            tr("歌詞繁簡設定"), self.lyrics_script,
            tr("所有歌詞視窗、靈動島與 OBS 同步套用；切換不必重新搜尋。")))
        layout.addWidget(card)

        card, group = self._card(
            tr("浮動歌詞顯示效果"), tr("只影響浮動歌詞；完整應用模式固定不使用動畫。"))
        self.overlay_effect = QComboBox()
        self.overlay_effect.setObjectName("overlayLyricEffect")
        self.overlay_effect.addItem(tr("無動畫"), "none")
        self.overlay_effect.addItem(tr("動態滾動"), "scroll")
        self.overlay_effect.addItem(tr("淡出淡入"), "fade")
        self.overlay_effect.setMinimumWidth(185)
        for i in range(self.overlay_effect.count()):
            if self.overlay_effect.itemData(i) == current.overlay_lyric_effect:
                self.overlay_effect.setCurrentIndex(i)
                break
        group.addLayout(self._field_row(
            tr("歌詞顯示方式"), self.overlay_effect,
            tr("無動畫立即切換；動態滾動平滑移動；淡出淡入原地交替顯示")))

        self.transition_speed = QComboBox()
        self.transition_speed.addItem(tr("快速 · 380 ms"), 380)
        self.transition_speed.addItem(tr("標準 · 620 ms"), 620)
        self.transition_speed.addItem(tr("柔順 · 850 ms"), 850)
        self.transition_speed.setMinimumWidth(170)
        if current.transition_ms not in (380, 620, 850):
            self.transition_speed.addItem(f"自訂 · {current.transition_ms} ms", current.transition_ms)
        self.transition_speed.setCurrentIndex(1)
        for i in range(self.transition_speed.count()):
            if self.transition_speed.itemData(i) == current.transition_ms:
                self.transition_speed.setCurrentIndex(i)
                break
        group.addLayout(self._field_row(
            tr("歌詞切換速度"), self.transition_speed,
            tr("調整浮動歌詞動畫與 OBS 平滑切換速度；無動畫模式不受影響")))
        self.overlay_effect.currentIndexChanged.connect(
            self._update_transition_speed_availability)
        layout.addWidget(card)
        layout.addStretch(1)
        return scroll

    def _create_obs_page(self, current: Settings) -> QScrollArea:
        scroll, layout = self._new_page()
        card, group = self._card(tr("瀏覽器來源"), tr("將下方網址新增為 OBS 的「瀏覽器」來源。"))
        self.obs_browser = SpotifySwitch(tr("啟用 OBS 瀏覽器來源"))
        self.obs_browser.setChecked(current.obs_browser_enabled)
        group.addWidget(self.obs_browser)
        self.obs_cover = SpotifySwitch(tr("在 OBS 畫面顯示專輯封面"))
        self.obs_cover.setChecked(current.obs_cover_enabled)
        group.addWidget(self.obs_cover)
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1024, 65535)
        self.obs_port.setValue(current.obs_browser_port)
        self.obs_port.setFixedWidth(126)
        group.addLayout(self._field_row(tr("本機服務連接埠"), self.obs_port))
        url_box = QFrame()
        url_box.setObjectName("settingsUrlBox")
        url_row = QHBoxLayout(url_box)
        url_row.setContentsMargins(12, 10, 10, 10)
        url_row.setSpacing(8)
        self.obs_url = QLabel()
        self.obs_url.setObjectName("settingsUrl")
        self.obs_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        url_row.addWidget(self.obs_url, 1)
        copy_url = QPushButton(tr("複製"))
        copy_url.setObjectName("secondary")
        copy_url.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_url.clicked.connect(lambda: QApplication.clipboard().setText(self.obs_url.text()))
        url_row.addWidget(copy_url)
        group.addWidget(url_box)
        self.obs_port.valueChanged.connect(self._update_obs_url)
        self._update_obs_url()
        for child in (self.obs_cover, self.obs_port, copy_url):
            child.setEnabled(current.obs_browser_enabled)
            self.obs_browser.toggled.connect(child.setEnabled)
        layout.addWidget(card)

        card, group = self._card(
            tr("OBS 歌詞動畫"), tr("OBS 的平滑切換獨立於浮動歌詞顯示方式。"))
        self.smooth_lyrics = SpotifySwitch(tr("OBS 歌詞啟用平滑切換"))
        self.smooth_lyrics.setChecked(current.smooth_lyrics)
        self.smooth_lyrics.toggled.connect(self._update_transition_speed_availability)
        group.addWidget(self.smooth_lyrics)
        layout.addWidget(card)
        self._update_transition_speed_availability()

        card, group = self._card(tr("擷取相容性"))
        self.obs_window = SpotifySwitch(tr("啟用 OBS 視窗擷取相容模式"))
        self.obs_window.setChecked(current.obs_window_capture)
        group.addWidget(self.obs_window)
        hint = QLabel(tr("可使用 ?cover=1 顯示封面、?cover=0 隱藏封面，或 ?track=0&cover=0 輸出純歌詞。"))
        hint.setWordWrap(True)
        hint.setObjectName("settingsFieldHint")
        group.addWidget(hint)
        layout.addWidget(card)
        layout.addStretch(1)
        return scroll

    def _create_system_page(self, current: Settings) -> QScrollArea:
        scroll, layout = self._new_page()
        card, group = self._card(tr("視窗行為"), tr("設定視窗與 Windows 桌面的互動方式。"))
        self.topmost = SpotifySwitch(tr("讓懸浮歌詞始終顯示在其他視窗上方"))
        self.topmost.setChecked(current.topmost)
        group.addWidget(self.topmost)
        self.game_overlay = SpotifySwitch(tr("遊戲浮動模式（置頂、滑鼠穿透）"))
        self.game_overlay.setChecked(current.game_overlay_enabled)
        group.addWidget(self.game_overlay)
        game_hint = QLabel(tr("適用於視窗化與無邊框全螢幕遊戲；透過系統匣可隨時關閉並移動歌詞。獨佔全螢幕不保證顯示。"))
        game_hint.setObjectName("settingsFieldHint")
        game_hint.setWordWrap(True)
        group.addWidget(game_hint)
        self.start_with_windows = SpotifySwitch(tr("登入 Windows 時自動啟動"))
        self.start_with_windows.setChecked(current.start_with_windows)
        group.addWidget(self.start_with_windows)
        layout.addWidget(card)

        card, group = self._card(tr("桌面靈動島"), tr("在螢幕頂端顯示迷你播放器，不影響原本的浮動歌詞與 OBS。"))
        self.dynamic_island = SpotifySwitch(tr("啟用桌面靈動島"))
        self.dynamic_island.setChecked(current.dynamic_island_enabled)
        group.addWidget(self.dynamic_island)
        self.island_hover = SpotifySwitch(tr("滑鼠移入時自動展開"))
        self.island_hover.setChecked(current.dynamic_island_auto_expand)
        group.addWidget(self.island_hover)
        self.island_lyrics = SpotifySwitch(tr("在靈動島顯示同步歌詞"))
        self.island_lyrics.setChecked(current.dynamic_island_show_lyrics)
        group.addWidget(self.island_lyrics)
        # These options apply only when the island has been explicitly enabled.
        # Keep their saved values while disabled so re-enabling restores them.
        for option in (self.island_hover, self.island_lyrics):
            option.setEnabled(current.dynamic_island_enabled)
            self.dynamic_island.toggled.connect(option.setEnabled)
        island_hint = QLabel(tr("固定於主螢幕頂端中央，不可拖曳。收合時顯示封面、歌手 - 歌名與當前歌詞；點擊展開，右鍵可進入設定或關閉。"))
        island_hint.setObjectName("settingsFieldHint")
        island_hint.setWordWrap(True)
        group.addWidget(island_hint)
        layout.addWidget(card)

        card, group = self._card(tr("語言"), tr("預設跟隨作業系統，修改後立即套用"))
        self.language = QComboBox()
        for code, name in LANGUAGES:
            display_name = tr("系統預設") if code == "system" else name
            self.language.addItem(display_name, code)
        for index in range(self.language.count()):
            if self.language.itemData(index) == current.language:
                self.language.setCurrentIndex(index)
                break
        self.language.setMinimumWidth(180)
        group.addLayout(self._field_row(tr("介面語言"), self.language))
        layout.addWidget(card)

        card, group = self._card(tr("關於本應用"))
        about = QLabel(tr(
            "從 Windows 媒體工作階段取得 Spotify 播放資訊，透過公開歌詞來源提供同步顯示。"
            "不需要 Spotify 登入授權。\n\n"
            "設定儲存於目前 Windows 使用者的本機資料夾。"
        ))
        about.setWordWrap(True)
        about.setObjectName("settingsAbout")
        group.addWidget(about)
        layout.addWidget(card)
        layout.addStretch(1)
        return scroll

    def _update_transition_speed_availability(self, *_args) -> None:
        """Enable speed only when the overlay effect or OBS uses animation."""
        overlay_animated = self.overlay_effect.currentData() in {"scroll", "fade"}
        obs_animated = (hasattr(self, "smooth_lyrics") and self.smooth_lyrics.isChecked())
        self.transition_speed.setEnabled(bool(overlay_animated or obs_animated))

    def _show_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        self.page_title.setText(tr(self._PAGES[index][1]))
        self.page_subtitle.setText(tr(self._PAGES[index][2]))

    def _update_obs_url(self, *_args) -> None:
        self.obs_url.setText(f"http://127.0.0.1:{self.obs_port.value()}/overlay")

    def _choose_color(self) -> None:
        dialog = QColorDialog(QColor(self._selected_color), self)
        dialog.setWindowTitle(tr("選擇歌詞文字顏色"))
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,
                             bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            chosen = dialog.selectedColor()
            if chosen.isValid():
                self._selected_color = chosen.name().upper()
                self._update_color_preview()

    def _update_color_preview(self) -> None:
        self.color_preview.setStyleSheet(
            f"color: {self._selected_color}; font-size:19px; font-weight:750;"
            "background:#333333; border:1px solid #555555; border-radius:7px;"
        )
        self.color_button.setToolTip(self._selected_color)

    def selected(self) -> Settings:
        self.values.font_size = self.font.value()
        self.values.adaptive_lyrics_background = self.adaptive_background.isChecked()
        self.values.language = str(self.language.currentData())
        self.values.lyrics_script = str(self.lyrics_script.currentData())
        self.values.desktop_text_color = self._selected_color
        self.values.desktop_cover_enabled = self.desktop_cover.isChecked()
        self.values.obs_cover_enabled = self.obs_cover.isChecked()
        self.values.offset_ms = self.offset.value()
        self.values.opacity = self.opacity.value()
        self.values.overlay_opacity = self.overlay_opacity.value()
        self.values.overlay_lyric_effect = str(self.overlay_effect.currentData())
        self.values.line_count = int(self.lines.currentData())
        self.values.smooth_lyrics = self.smooth_lyrics.isChecked()
        self.values.transition_ms = int(self.transition_speed.currentData())
        self.values.topmost = self.topmost.isChecked()
        self.values.game_overlay_enabled = self.game_overlay.isChecked()
        self.values.start_with_windows = self.start_with_windows.isChecked()
        self.values.dynamic_island_enabled = self.dynamic_island.isChecked()
        self.values.dynamic_island_auto_expand = self.island_hover.isChecked()
        self.values.dynamic_island_show_lyrics = self.island_lyrics.isChecked()
        self.values.obs_browser_enabled = self.obs_browser.isChecked()
        self.values.obs_browser_port = self.obs_port.value()
        self.values.obs_window_capture = self.obs_window.isChecked()
        self.values.validate()
        return self.values


class LyricsWindow(QWidget):
    def __init__(self, *, demo: bool = False) -> None:
        super().__init__()
        self.settings = load_settings()
        set_language(self.settings.language)
        self.demo = demo
        self.service = LyricsService(data_dir() / "my_lyrics")
        self.bridge = EventBridge()
        self.bridge.playback.connect(self._got_playback)
        self.bridge.lyric.connect(self._got_lyric)
        self.bridge.error.connect(self._got_error)
        self.bridge.control.connect(self._got_control_result)
        self.monitor: MediaMonitor | None = None
        self.playback: Playback | None = None
        self.lyrics = LyricsResult(kind="missing")
        self._raw_lyrics = self.lyrics  # keep source unchanged for instant language toggles
        self.current_index: int | None = None
        # Overlay mode shows no loading text while searching lyrics.
        self._lyrics_search_pending = False
        # A genuinely missing or temporarily unavailable lyric may appear
        # later in another public catalogue. Retry with a bounded schedule.
        self._lyrics_retry_generation = 0
        self._lyrics_auto_retry_attempts = 0
        # Game overlay is always reversible from the tray. Do not start in
        # click-through mode on systems with no tray, where it could be stuck.
        if self.settings.game_overlay_enabled and not QSystemTrayIcon.isSystemTrayAvailable():
            self.settings.game_overlay_enabled = False
        self.click_through = bool(self.settings.game_overlay_enabled)
        self._game_overlay_suspended = False
        self._compact_state: tuple[bool, bool, bool, bool] | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._text_drag_offset: QPoint | None = None
        self._closing = False
        self._source_error = ""
        self.obs_server: ObsOverlayServer | None = None
        self._obs_error = ""
        self._cover_id_displayed = ""
        self._obs_registered_cover_id = ""
        self._obs_failed_cover_id = ""
        self._obs_failed_cover_retry_at = 0.0
        self._visual_refresh_pending = False
        self._theme_cover_id = ""
        self._song_theme = DEFAULT_THEME
        self._seeking = False
        self._seeking_track = ""
        self._seek_preview_ms = 0
        self._pending_seek_ms: int | None = None
        self._pending_seek_started = 0.0
        self._pending_seek_track = ""

        self.setObjectName("overlayLyrics")
        self.setWindowTitle("Spotify Lyrics")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Each mode has independent size and position. Keep automatic geometry
        # persistence disabled until the first restore has completed.
        self._geometry_ready = False
        self._geometry_transition = False
        self._geometry_save_timer = QTimer(self)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.setInterval(450)
        self._geometry_save_timer.timeout.connect(self._persist)
        self._game_zorder_timer = QTimer(self)
        self._game_zorder_timer.setInterval(1800)
        self._game_zorder_timer.timeout.connect(self._ensure_game_overlay_zorder)
        if self.settings.game_overlay_enabled:
            self._game_zorder_timer.start()
        if self.settings.desktop_text_only:
            self.setMinimumSize(220, 62)
        else:
            self.setMinimumSize(325, 265)
        self.setWindowFlags(self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                          self.settings.game_overlay_enabled)
        _, _, initial_width, initial_height = self.settings.geometry_for_mode(
            self.settings.desktop_text_only)
        self.resize(initial_width, initial_height)
        self.setStyleSheet(STYLE)
        self._build_interface()
        self._create_tray()
        self._create_island()
        self._configure_obs_server()
        self._apply_display_mode()
        self._apply_line_styles()
        self._restore_position()
        self._geometry_ready = True

        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        if self.demo:
            self._load_demo()
        else:
            self.monitor = MediaMonitor(self.bridge.playback.emit, self.bridge.error.emit,
                                        self.bridge.control.emit)
            self.monitor.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "brand_title"):
            self.brand_title.setText("LYRICS")
        if hasattr(self, "lyrics_panel"):
            self._update_compact_layout()
        self._queue_geometry_save()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._queue_geometry_save()

    def _queue_geometry_save(self) -> None:
        """Debounce drag/resize writes; ignore intermediate mode geometry."""
        if (getattr(self, "_geometry_ready", False)
                and not self._geometry_transition
                and not self._closing
                and not self.isMinimized()):
            self._geometry_save_timer.start()

    def _capture_active_geometry(self) -> None:
        if not self.isMinimized():
            self.settings.remember_geometry(
                self.settings.desktop_text_only, self.x(), self.y(),
                self.width(), self.height())

    def paintEvent(self, event) -> None:
        """Draw a lightweight outer shadow without caching all child pixels.

        QGraphicsDropShadowEffect on the *entire* live player causes Qt to
        repaint child artwork through a transformed off-screen pixmap.  On
        Windows this can leave an old album image below its layout slot after
        tray restore or a text/full mode switch.  An outer-only painted shadow
        never changes a child widget's drawing coordinate system.
        """
        super().paintEvent(event)
        if not hasattr(self, "card") or self.settings.desktop_text_only:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        bounds = QRectF(self.card.geometry())
        for distance in range(8, 0, -1):
            painter.setPen(QPen(QColor(0, 0, 0, max(3, 24 - distance * 2)), 1.0))
            edge = bounds.adjusted(-distance, -distance, distance, distance)
            painter.drawRoundedRect(edge, 16.0 + distance, 16.0 + distance)
        painter.end()

    def hideEvent(self, event) -> None:
        # A hidden window must never resume with half-rendered album artwork.
        if hasattr(self, "cover_fade"):
            self.cover_fade.stop()
            if self.cover_label.has_artwork():
                self.cover_label.artworkOpacity = 1.0
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._queue_visual_refresh()
        if self.settings.game_overlay_enabled and not self._game_overlay_suspended:
            QTimer.singleShot(0, self._ensure_game_overlay_zorder)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Also handles a native taskbar minimize/restore, not just hide().
            self._queue_visual_refresh()

    def _queue_visual_refresh(self) -> None:
        """Coalesce native expose/resize updates after the layout is visible."""
        if self._closing or self._visual_refresh_pending or not hasattr(self, "card"):
            return
        self._visual_refresh_pending = True
        QTimer.singleShot(0, self._refresh_visual_layout)

    def _refresh_visual_layout(self) -> None:
        self._visual_refresh_pending = False
        # Preserve artwork-derived colors across minimize/restore.  The artwork
        # cache only changes when Spotify reports a different cover ID.
        if self._closing or not self.isVisible() or self.isMinimized():
            return
        # Let Qt recalculate children only *after* native visibility and size
        # have settled. Never restore old child positions with move()/geometry.
        for widget in (self.card, self.full_header, self.track_panel, self.lyrics_panel):
            if widget.layout() is not None:
                widget.layout().invalidate()
        self._update_compact_layout()
        self.outer_layout.invalidate()
        self.outer_layout.activate()
        self.cover_label.update()
        self.lyric_view.update()
        self.card.update()
        self.update()

    def _window_flags(self):
        # Qt.Tool adds WS_EX_TOOLWINDOW on Windows; OBS may omit such windows
        # from the capture list. Ordinary Qt.Window improves discoverability.
        game = self.settings.game_overlay_enabled and not self._game_overlay_suspended
        kind = (Qt.WindowType.Tool if game or not self.settings.obs_window_capture
                else Qt.WindowType.Window)
        flags = Qt.WindowType.FramelessWindowHint | kind
        if self.settings.topmost or game:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self.click_through or game:
            flags |= Qt.WindowType.WindowTransparentForInput
        if game:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        return flags

    def _build_interface(self) -> None:
        outer = QVBoxLayout(self)
        self.outer_layout = outer
        outer.setContentsMargins(9, 9, 9, 9)
        outer.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        outer.addWidget(self.card)
        # Do not attach a QGraphicsEffect to the card: an off-screen cache of
        # its full subtree breaks child image offsets on Windows after restore.
        # LyricsWindow.paintEvent draws the exterior shadow instead.
        root = QVBoxLayout(self.card)
        self.card_layout = root
        root.setContentsMargins(17, 14, 17, 12)
        root.setSpacing(0)

        # Spotify-style window chrome and the entire track hero disappear in
        # no-background text mode, leaving only the live lyric canvas.
        self.full_header = QWidget(self.card)
        header_root = QVBoxLayout(self.full_header)
        header_root.setContentsMargins(0, 0, 0, 0)
        header_root.setSpacing(0)

        title_row = QHBoxLayout()
        title_row.setSpacing(3)
        self.drag = DragHandle(self.card)
        drag_layout = QHBoxLayout(self.drag)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setSpacing(9)
        self.spotify_dot = SpotifyMark(32, self.drag)
        drag_layout.addWidget(self.spotify_dot)
        brand_column = QVBoxLayout()
        brand_column.setSpacing(0)
        self.brand_title = QLabel("LYRICS")
        self.brand_title.setObjectName("playerBrand")
        brand_column.addWidget(self.brand_title)
        drag_layout.addLayout(brand_column)
        drag_layout.addStretch(1)
        self.drag.setToolTip(tr("按住這裡拖曳懸浮視窗"))
        title_row.addWidget(self.drag, 1)
        self.text_button = tool_button("type", tr("切換純文字懸浮模式"))
        self.text_button.clicked.connect(lambda: self._toggle_text_mode(True))
        self.settings_button = tool_button("settings", tr("開啟設定"))
        self.settings_button.setProperty("variant", "accent")
        self.settings_button.clicked.connect(self._open_settings)
        self.hide_button = tool_button("minus", tr("最小化至系統匣"))
        self.hide_button.clicked.connect(self.hide)
        self.exit_button = tool_button("x", tr("結束應用程式"))
        self.exit_button.setProperty("variant", "danger")
        self.exit_button.clicked.connect(self._exit)
        for button in (self.text_button,
                       self.settings_button, self.hide_button, self.exit_button):
            title_row.addWidget(button)
        header_root.addLayout(title_row)
        self.header_gap = QWidget(self.full_header)
        self.header_gap.setFixedHeight(10)
        header_root.addWidget(self.header_gap)

        # Album artwork and timing share a single unoutlined, warm-graphite card.
        self.track_panel = QFrame(self.card)
        self.track_panel.setObjectName("trackPanel")
        track_layout = QVBoxLayout(self.track_panel)
        self.track_layout = track_layout
        track_layout.setContentsMargins(14, 11, 14, 9)
        track_layout.setSpacing(0)
        track_row = QHBoxLayout()
        self.track_row_layout = track_row
        track_row.setSpacing(14)
        self.cover_label = ArtworkCover(self.track_panel)
        self.cover_label.setFixedSize(78, 78)
        # Artwork itself animates opacity, without creating another child
        # paint device or graphics effect with stale global coordinates.
        self.cover_fade = QPropertyAnimation(self.cover_label, b"artworkOpacity", self)
        self.cover_fade.setDuration(340)
        self.cover_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        track_row.addWidget(self.cover_label)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 3, 0, 3)
        texts.setSpacing(5)
        self.track_label = ElidedLabel(tr("等待 Spotify 播放"))
        self.track_label.setObjectName("trackTitle")
        self.track_label.setTextFormat(Qt.TextFormat.PlainText)
        self.track_label.setMinimumWidth(0)
        self.track_label.setWordWrap(False)
        self.track_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.track_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.track_label.setToolTip(tr("Spotify 播放歌曲後自動更新"))
        self.artist_label = ElidedLabel(tr("啟動 Spotify，播放你喜歡的音樂"))
        self.artist_label.setObjectName("trackArtist")
        self.artist_label.setTextFormat(Qt.TextFormat.PlainText)
        self.artist_label.setMinimumWidth(0)
        self.artist_label.setWordWrap(False)
        self.artist_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        texts.addWidget(self.track_label)
        texts.addWidget(self.artist_label)
        texts.addStretch(1)
        track_row.addLayout(texts, 1)
        track_layout.addLayout(track_row)
        self.track_gap = QWidget(self.track_panel)
        self.track_gap.setFixedHeight(10)
        track_layout.addWidget(self.track_gap)
        progress_row = QHBoxLayout()
        progress_row.setSpacing(9)
        self.elapsed = QLabel("0:00")
        self.total = QLabel("--:--")
        for label in (self.elapsed, self.total):
            label.setObjectName("trackTime")
            # The slider must not resize when e.g. 9:59 becomes 10:00.
            label.setFixedWidth(48)
        self.elapsed.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.elapsed)
        self.progress = SeekSlider(self.track_panel)
        self.progress.setObjectName("playProgress")
        self.progress.setFixedHeight(18)
        self.progress.setRange(0, 1000)
        self.progress.setToolTip(tr("拖曳進度條調整播放位置"))
        self.progress.setAccessibleName(tr("拖曳進度條調整播放位置"))
        self.progress.setEnabled(False)
        self.progress.sliderPressed.connect(self._on_seek_pressed)
        self.progress.sliderMoved.connect(self._on_seek_moved)
        self.progress.sliderReleased.connect(self._on_seek_released)
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.total)
        track_layout.addLayout(progress_row)
        self.controls_gap = QWidget(self.track_panel)
        self.controls_gap.setFixedHeight(3)
        track_layout.addWidget(self.controls_gap)
        controls_row = QHBoxLayout()
        self.controls_row_layout = controls_row
        controls_row.setSpacing(18)
        controls_row.addStretch(1)
        self.previous_button = tool_button("skip-back", tr("上一首"), width=34)
        self.previous_button.setProperty("variant", "skip")
        self.play_button = tool_button("play", tr("播放"), width=38)
        self.play_button.setFixedSize(38, 38)
        self.play_button.setProperty("variant", "transport")
        self.next_button = tool_button("skip-forward", tr("下一首"), width=34)
        self.next_button.setProperty("variant", "skip")
        for button, action in ((self.previous_button, "previous"),
                               (self.play_button, "toggle"),
                               (self.next_button, "next")):
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, command=action: self._request_transport(command))
            controls_row.addWidget(button)
        controls_row.addStretch(1)
        track_layout.addLayout(controls_row)
        header_root.addWidget(self.track_panel)
        root.addWidget(self.full_header)
        self.lyrics_gap = QWidget(self.card)
        self.lyrics_gap.setFixedHeight(8)
        root.addWidget(self.lyrics_gap)

        # Artwork-derived lyrics background; no permanent green fill.
        self.lyrics_panel = QFrame(self.card)
        self.lyrics_panel.setObjectName("lyricsPanel")
        lyric_panel_layout = QVBoxLayout(self.lyrics_panel)
        self.lyric_panel_layout = lyric_panel_layout
        lyric_panel_layout.setContentsMargins(18, 12, 16, 5)
        lyric_panel_layout.setSpacing(0)
        self.lyric_section_bar = QWidget(self.lyrics_panel)
        lyric_row = QHBoxLayout(self.lyric_section_bar)
        lyric_row.setContentsMargins(0, 0, 0, 0)
        lyric_row.setSpacing(10)
        heading = QLabel(tr("歌詞"))
        self.lyric_heading = heading
        heading.setObjectName("lyricHeading")
        lyric_row.addWidget(heading)
        lyric_row.addStretch(1)
        lyric_panel_layout.addWidget(self.lyric_section_bar)
        self.lyric_heading_gap = QWidget(self.lyrics_panel)
        self.lyric_heading_gap.setFixedHeight(5)
        lyric_panel_layout.addWidget(self.lyric_heading_gap)

        self.stack = QStackedWidget(self.lyrics_panel)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.lyric_view = AnimatedLyricsView()
        self.synced_page = self.lyric_view
        self.stack.addWidget(self.synced_page)
        self.message_page = QWidget(self.stack)
        message_layout = QVBoxLayout(self.message_page)
        message_layout.setContentsMargins(10, 6, 10, 6)
        self.big_message = QLabel(tr("啟動 Spotify 並播放歌曲"))
        self.big_message.setTextFormat(Qt.TextFormat.PlainText)
        self.big_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.big_message.setWordWrap(True)
        self.big_message.setStyleSheet("color:#C0D0C4; font-size:16px; font-weight:650;")
        message_layout.addStretch(1)
        message_layout.addWidget(self.big_message)
        message_layout.addStretch(1)
        self.stack.addWidget(self.message_page)
        self.stack.setCurrentIndex(1)
        lyric_panel_layout.addWidget(self.stack, 1)
        root.addWidget(self.lyrics_panel, 1)

        # The requested provider and playback status labels have been removed.
        # Keep just the native resize handle, unobtrusively aligned bottom-right.
        self.resize_grip = QSizeGrip(self.card)
        self.resize_grip.setFixedSize(16, 16)
        self.resize_grip.setToolTip(tr("拖曳這個角落調整視窗大小"))
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip_row.addWidget(self.resize_grip)
        root.addLayout(grip_row)

        self._text_targets = (self.card, self.lyrics_panel, self.stack,
                              self.lyric_view, self.message_page, self.big_message)
        for target in self._text_targets:
            target.installEventFilter(self)

    def _update_compact_layout(self, *, force: bool = False) -> None:
        """Keep the current lyric visible at genuinely small window sizes.

        The player has two responsive breakpoints. At the smallest size only
        secondary chrome is removed; song title, transport, and a usable lyric
        canvas remain visible. This never changes saved font preferences.
        """
        if not hasattr(self, "lyrics_panel"):
            return
        overlay = self.settings.desktop_text_only
        compact = not overlay and (self.width() < 500 or self.height() < 390)
        tiny = compact and (self.width() < 360 or self.height() < 305)
        state = (overlay, compact, tiny, self.settings.desktop_cover_enabled)
        if not force and state == self._compact_state:
            return
        self._compact_state = state
        if overlay:
            return
        self.outer_layout.setContentsMargins(*((6, 6, 6, 6) if compact else (9, 9, 9, 9)))
        self.card_layout.setContentsMargins(*((11, 9, 11, 6) if compact else (17, 14, 17, 12)))
        self.track_layout.setContentsMargins(*((9, 6, 9, 6) if compact else (14, 11, 14, 9)))
        self.track_row_layout.setSpacing(8 if compact else 14)
        self.controls_row_layout.setSpacing(15 if compact else 18)
        self.lyric_panel_layout.setContentsMargins(*((10, 7, 10, 3) if compact else (18, 12, 16, 5)))
        self.header_gap.setFixedHeight(4 if compact else 10)
        self.track_gap.setFixedHeight(4 if compact else 10)
        self.controls_gap.setFixedHeight(1 if compact else 3)
        self.lyrics_gap.setFixedHeight(5 if compact else 8)
        self.lyric_heading_gap.setFixedHeight(0 if compact else 5)
        self.lyric_section_bar.setVisible(not compact)
        self.lyrics_panel.setMinimumHeight(65 if compact else 0)
        self.stack.setMinimumHeight(45 if compact else 0)
        self.spotify_dot.setFixedSize(*( (26, 26) if compact else (32, 32)))
        self.brand_title.setStyleSheet("font-size:12px;" if compact else "")
        for button in (self.text_button, self.settings_button, self.hide_button,
                       self.exit_button):
            button.setFixedSize(*( (30, 28) if compact else (34, 32)))
        self.cover_label.setFixedSize(*( (48, 48) if compact else (78, 78)))
        self.cover_label.setVisible(self.settings.desktop_cover_enabled and not tiny)
        self.artist_label.setVisible(not (tiny and self.height() < 285))
        self.track_label.setStyleSheet("font-size:15px; font-weight:800;" if compact else "")
        self.artist_label.setStyleSheet("font-size:11px;" if compact else "")
        for label in (self.elapsed, self.total):
            label.setFixedWidth(40 if compact else 48)
        for button in (self.previous_button, self.next_button):
            button.setFixedSize(*( (30, 30) if compact else (34, 32)))
        self.play_button.setFixedSize(*( (32, 32) if compact else (38, 38)))
        self.resize_grip.setFixedSize(*( (13, 13) if compact else (16, 16)))
        self.card_layout.invalidate()
        self.outer_layout.invalidate()

    def _create_island(self) -> None:
        """Create one persistent media capsule; it shares the existing monitor."""
        self.island = DynamicIslandWindow(
            auto_expand=self.settings.dynamic_island_auto_expand,
            show_lyrics=self.settings.dynamic_island_show_lyrics,
            demo=self.demo,
        )
        self.island.transport_requested.connect(self._request_transport)
        self.island.seek_requested.connect(self._request_island_seek)
        self.island.show_main_requested.connect(self._bring_back)
        self.island.settings_requested.connect(self._open_settings)
        self.island.disable_requested.connect(lambda: self._toggle_island(False))
        if self.settings.dynamic_island_enabled:
            self.island.show()

    def _toggle_island(self, enabled: bool) -> None:
        self.settings.dynamic_island_enabled = bool(enabled)
        self._apply_island_settings()
        self._persist()

    def _apply_island_settings(self) -> None:
        self.island.configure(
            auto_expand=self.settings.dynamic_island_auto_expand,
            show_lyrics=self.settings.dynamic_island_show_lyrics,
        )
        if self.settings.dynamic_island_enabled:
            self.island.show()
        else:
            self.island.hide()
            self.island.set_expanded(False, pin=False)
        if self.tray is not None:
            self.action_island.setChecked(self.settings.dynamic_island_enabled)

    def _create_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.hide_button.setDisabled(True)
            self.hide_button.setToolTip(tr("此系統目前沒有可用的系統匣"))
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Spotify 浮動歌詞")
        menu = QMenu(self)
        self.action_show = QAction(tr("顯示懸浮歌詞"), self)
        self.action_show.triggered.connect(self._bring_back)
        menu.addAction(self.action_show)
        self.action_island = QAction(tr("啟用桌面靈動島"), self)
        self.action_island.setCheckable(True)
        self.action_island.setChecked(self.settings.dynamic_island_enabled)
        self.action_island.triggered.connect(self._toggle_island)
        menu.addAction(self.action_island)
        self.action_text = QAction(tr("純文字模式（無背景）"), self)
        self.action_text.setCheckable(True)
        self.action_text.setChecked(self.settings.desktop_text_only)
        self.action_text.triggered.connect(self._toggle_text_mode)
        menu.addAction(self.action_text)
        self.action_game = QAction(tr("遊戲浮動模式（置頂、滑鼠穿透）"), self)
        self.action_game.setCheckable(True)
        self.action_game.setChecked(self.settings.game_overlay_enabled)
        self.action_game.triggered.connect(self._toggle_game_overlay)
        menu.addAction(self.action_game)
        self.action_color = QAction(tr("更換歌詞顏色…"), self)
        self.action_color.triggered.connect(self._choose_text_color)
        menu.addAction(self.action_color)
        self.action_cover = QAction(tr("完整視窗顯示專輯封面"), self)
        self.action_cover.setCheckable(True)
        self.action_cover.setChecked(self.settings.desktop_cover_enabled)
        self.action_cover.triggered.connect(self._toggle_desktop_cover)
        menu.addAction(self.action_cover)
        self.action_click = QAction(tr("滑鼠穿透（由系統匣解除）"), self)
        self.action_click.setCheckable(True)
        self.action_click.setChecked(self.click_through)
        self.action_click.triggered.connect(self._toggle_click_through)
        menu.addAction(self.action_click)
        self.action_top = QAction(tr("永遠置頂"), self)
        self.action_top.setCheckable(True)
        self.action_top.setChecked(self.settings.topmost)
        self.action_top.triggered.connect(self._toggle_topmost)
        menu.addAction(self.action_top)
        self.action_obs_window = QAction(tr("OBS 視窗擷取相容模式"), self)
        self.action_obs_window.setCheckable(True)
        self.action_obs_window.setChecked(self.settings.obs_window_capture)
        self.action_obs_window.triggered.connect(self._toggle_obs_window_capture)
        menu.addAction(self.action_obs_window)
        self.action_copy_obs = QAction(tr("複製 OBS 瀏覽器來源網址"), self)
        self.action_copy_obs.triggered.connect(self._copy_obs_url)
        self.action_copy_obs.setEnabled(False)
        menu.addAction(self.action_copy_obs)
        menu.addSeparator()
        refresh_action = QAction(tr("重新搜尋歌詞"), self)
        refresh_action.triggered.connect(self._refresh_lyrics)
        menu.addAction(refresh_action)
        settings_action = QAction(tr("設定"), self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        exit_action = QAction(tr("結束 Spotify 浮動歌詞"), self)
        exit_action.triggered.connect(self._exit)
        menu.addAction(exit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activation)
        self.tray.show()

    def _restore_position(self) -> None:
        """Restore active-mode position, including valid negative monitor X/Y."""
        x, y, _, _ = self.settings.geometry_for_mode(self.settings.desktop_text_only)
        screens = QApplication.screens()
        if not screens:
            return
        stored = QRect(x, y, self.width(), self.height())
        # -1/-1 is the uninitialized position sentinel. Other negative
        # coordinates are legitimate on a left/top secondary monitor.
        if (x, y) != (-1, -1):
            for screen in screens:
                overlap = stored.intersected(screen.availableGeometry())
                if overlap.width() >= min(50, self.width()) and overlap.height() >= min(40, self.height()):
                    self.move(x, y)
                    return
        desktop = (QApplication.primaryScreen() or screens[0]).availableGeometry()
        if self.settings.desktop_text_only:
            # A fresh overlay overlay starts near the top center, independently
            # from the full player that defaults near the lower-right corner.
            x = desktop.left() + (desktop.width() - self.width()) // 2
            y = desktop.top() + 60
        else:
            x = desktop.right() - self.width() - 40
            y = desktop.bottom() - self.height() - 45
        self.move(
            max(desktop.left(), min(x, desktop.right() - self.width() + 1)),
            max(desktop.top(), min(y, desktop.bottom() - self.height() + 1)),
        )

    def _set_card_opacity(self) -> None:
        """Fade only the full player's outer shell, never the child controls.

        Window-wide opacity is applied *after* Qt composites all children, so
        even solid song/lyric panels and icons were faded by the old code.
        Instead, paint alpha into the parent card background. Its children
        (track card, lyric card, artwork and controls) keep independent, fully
        opaque rendering on the translucent top-level backing surface.
        """
        if self.settings.desktop_text_only:
            self.card.setStyleSheet("QFrame#card { background: transparent; border: 0px; }")
        else:
            shell_alpha = round(255 * self.settings.opacity / 100)
            self.card.setStyleSheet(
                f"QFrame#card {{ background:rgba(17,17,17,{shell_alpha}); "
                "border:1px solid rgba(255,255,255,0.11); border-radius:16px; }"
            )
        self.card.update()
        self.update()

    def _apply_window_opacity(self) -> None:
        """Global opacity is reserved for the overlay, text-only overlay.

        On the full player it MUST be 1.0: otherwise Windows fades every
        child widget regardless of its individual background alpha.
        """
        if self.settings.desktop_text_only:
            self.setWindowOpacity(self.settings.overlay_opacity / 100.0)
        else:
            self.setWindowOpacity(1.0)

    def _apply_line_styles(self) -> None:
        self.lyric_view.configure(
            font_size=self.settings.font_size,
            line_count=self.settings.line_count,
            smooth=(self.settings.desktop_text_only and
                    self.settings.overlay_lyric_effect != "none"),
            duration_ms=self.settings.transition_ms,
            text_only=self.settings.desktop_text_only,
            effect=self.settings.overlay_lyric_effect,
            text_color=self.settings.desktop_text_color,
        )

    def _apply_display_mode(self, *, previous_mode: bool | None = None) -> None:
        """Switch modes atomically, restoring each mode's geometry and opacity."""
        text_only = self.settings.desktop_text_only
        switching = previous_mode is not None and previous_mode != text_only
        was_transition = self._geometry_transition
        self._geometry_transition = True
        self.setUpdatesEnabled(False)
        try:
            self.full_header.setVisible(not text_only)
            self.cover_label.setVisible(not text_only and self.settings.desktop_cover_enabled)
            self.resize_grip.setVisible(not text_only)
            self.lyric_section_bar.setVisible(not text_only)
            self._apply_song_palette()
            self.outer_layout.setContentsMargins(*((0, 0, 0, 0) if text_only else (9, 9, 9, 9)))
            self.card_layout.setContentsMargins(*((0, 0, 0, 0) if text_only else (17, 14, 17, 12)))
            self.lyric_panel_layout.setContentsMargins(*((0, 0, 0, 0) if text_only else (18, 12, 16, 5)))
            self._apply_window_opacity()
            if text_only:
                self.big_message.setStyleSheet(
                    f"color:{self.settings.desktop_text_color}; "
                    f"font-size:{self.settings.font_size}px; font-weight:650;"
                )
            else:
                self.big_message.setStyleSheet("color:#C0D0C4; font-size:16px; font-weight:650;")
            self._set_card_opacity()
            if switching:
                self.setMinimumSize(*( (220, 62) if text_only else (325, 265)))
                _, _, width, height = self.settings.geometry_for_mode(text_only)
                self.resize(width, height)
                self._restore_position()
            self._update_compact_layout(force=True)
            self._sync_pending_search_page()
            if self.tray is not None:
                self.action_text.setChecked(text_only)
        finally:
            self.setUpdatesEnabled(True)
            self._geometry_transition = was_transition
        self._queue_visual_refresh()

    def _apply_song_palette(self) -> None:
        if self.settings.desktop_text_only:
            self.lyrics_panel.setStyleSheet("QFrame#lyricsPanel { background:transparent; border:0px; }")
            return
        colors = self._song_theme if self.settings.adaptive_lyrics_background else DEFAULT_THEME
        self.lyrics_panel.setStyleSheet(
            "QFrame#lyricsPanel { background:qlineargradient(x1:0,y1:0,x2:1,y2:1, "
            f"stop:0 {colors[0]}, stop:0.55 {colors[1]}, stop:1 {colors[2]}); "
            "border:0; border-radius:12px; }"
        )

    def _update_song_palette(self, artwork: QPixmap | None, image_id: str = "") -> None:
        if image_id == self._theme_cover_id:
            return
        self._theme_cover_id = image_id
        if artwork is None or artwork.isNull():
            self._song_theme = DEFAULT_THEME
        else:
            image = artwork.toImage().scaled(36, 36,
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._song_theme = theme_from_pixels(
                (image.pixelColor(x, y).red(), image.pixelColor(x, y).green(),
                 image.pixelColor(x, y).blue())
                for y in range(image.height()) for x in range(image.width())
            )
        self._apply_song_palette()

    def _toggle_desktop_cover(self, checked: bool) -> None:
        self.settings.desktop_cover_enabled = bool(checked)
        self._apply_display_mode()
        self._persist()

    def _update_cover(self, item: Playback | None) -> None:
        image_id = item.cover_id if item is not None else ""
        if image_id == self._cover_id_displayed:
            return  # Do not re-decode the same image on every media poll.
        self._cover_id_displayed = image_id
        self.cover_fade.stop()
        if image_id and item is not None:
            pixmap = QPixmap(item.cover_path)
            if not pixmap.isNull():
                self._update_song_palette(pixmap, image_id)
                self.cover_label.set_artwork(rounded_cover(pixmap))
                if self.isVisible() and not self.settings.desktop_text_only:
                    self.cover_label.artworkOpacity = 0.0
                    self.cover_fade.setStartValue(0.0)
                    self.cover_fade.setEndValue(1.0)
                    self.cover_fade.start()
                else:
                    # If Spotify changes tracks while minimized or in text
                    # mode, the image is immediately ready on restoration.
                    self.cover_label.artworkOpacity = 1.0
                return
        # A missing Spotify image never flashes the preceding song's cover.
        self._update_song_palette(None)
        self.cover_label.clear_artwork()

    def _toggle_text_mode(self, checked: bool) -> None:
        if not checked and self.settings.game_overlay_enabled:
            # Normal application mode always restores mouse interaction.
            self._toggle_game_overlay(False)
        previous_mode = self.settings.desktop_text_only
        if previous_mode == bool(checked):
            return
        # Save the departing mode *before* changing the active mode flag.
        self._geometry_save_timer.stop()
        self._capture_active_geometry()
        self.settings.desktop_text_only = bool(checked)
        self._text_drag_offset = None
        self._apply_display_mode(previous_mode=previous_mode)
        self._apply_line_styles()
        self._persist()

    def _set_text_color(self, color: str) -> None:
        value = QColor(color)
        if not value.isValid():
            return
        self.settings.desktop_text_color = value.name().upper()
        self._apply_display_mode()
        self._apply_line_styles()
        self._persist()

    def _choose_text_color(self) -> None:
        if self.click_through:
            self._toggle_click_through(False)
        choice = QColorDialog.getColor(
            QColor(self.settings.desktop_text_color), self, tr("更換歌詞顏色")
        )
        if choice.isValid():
            self._set_text_color(choice.name())

    def _show_text_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        normal = menu.addAction(tr("切回完整視窗"))
        retry = menu.addAction(tr("重新搜尋同步歌詞"))
        retry.setEnabled(self.playback is not None)
        color_menu = menu.addMenu(tr("歌詞顏色"))
        preset_actions = {}
        for title, hex_color in (
            (tr("白色"), "#F7FFF9"), (tr("Spotify 綠"), "#1ED760"),
            (tr("暖黃色"), "#FFD166"), (tr("粉紅色"), "#FF8EBB"),
            (tr("天空藍"), "#7DD3FC"),
        ):
            preset_actions[color_menu.addAction(title)] = hex_color
        custom = color_menu.addAction(tr("自訂顏色…"))
        settings_action = menu.addAction(tr("設定…"))
        exit_action = menu.addAction(tr("結束程式"))
        chosen = menu.exec(global_pos)
        if chosen == normal:
            self._toggle_text_mode(False)
        elif chosen == retry:
            self._refresh_lyrics()
        elif chosen in preset_actions:
            self._set_text_color(preset_actions[chosen])
        elif chosen == custom:
            self._choose_text_color()
        elif chosen == settings_action:
            self._open_settings()
        elif chosen == exit_action:
            self._exit()

    def eventFilter(self, watched: QObject, event) -> bool:
        """Left-drag and right-click work when the visible chrome is hidden."""
        if (not getattr(self, "_closing", False)
                and self.settings.desktop_text_only
                and not self.click_through
                and watched in getattr(self, "_text_targets", ())):
            kind = event.type()
            if kind == QEvent.Type.ContextMenu:
                self._show_text_context_menu(event.globalPos())
                return True
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._text_drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if kind == QEvent.Type.MouseMove and self._text_drag_offset is not None:
                if event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._text_drag_offset)
                return True
            if kind == QEvent.Type.MouseButtonRelease and self._text_drag_offset is not None:
                self._text_drag_offset = None
                self._persist()
                return True
        return super().eventFilter(watched, event)

    def _set_transport_enabled(self, enabled: bool, *, is_playing: bool = False) -> None:
        for button in (self.previous_button, self.play_button, self.next_button):
            button.setEnabled(enabled and not self.demo)
        if self.playback and self.playback.duration_ms > 0:
            # Arrow keys seek ~5 seconds, PageUp/PageDown ~15 seconds.
            self.progress.setSingleStep(max(1, round(5_000 * 1000 / self.playback.duration_ms)))
            self.progress.setPageStep(max(1, round(15_000 * 1000 / self.playback.duration_ms)))
        self.progress.setEnabled(bool(enabled and not self.demo and self.playback
                                      and self.playback.duration_ms > 0))
        self.play_button.icon_name = "pause" if is_playing else "play"
        label = tr("暫停") if is_playing else tr("播放")
        self.play_button.setToolTip(label)
        self.play_button.setAccessibleName(label)
        self.play_button.update()

    def _on_seek_pressed(self) -> None:
        playback = self.playback
        if playback is None or playback.duration_ms <= 0 or self.monitor is None:
            return
        self._seeking = True
        self._seeking_track = playback.key
        self._pending_seek_ms = None
        self._on_seek_moved(self.progress.value())

    def _on_seek_moved(self, slider_value: int) -> None:
        """Preview the requested time without sending commands during a drag."""
        playback = self.playback
        if not self._seeking or playback is None:
            return
        self._seek_preview_ms = round(playback.duration_ms * slider_value / 1000)
        self.elapsed.setText(self._format_time(self._seek_preview_ms))

    def _on_seek_released(self) -> None:
        if not self._seeking:
            return
        self._seeking = False
        playback = self.playback
        if (playback is None or playback.key != self._seeking_track
                or playback.duration_ms <= 0 or self.monitor is None):
            self._tick(force=True)
            return
        target_ms = round(playback.duration_ms * self.progress.value() / 1000)
        if not self.monitor.submit_seek(target_ms):
            self._tick(force=True)
            QMessageBox.warning(self, tr("控制失敗"), tr("找不到正在執行的 Spotify 播放工作階段。"))
            return
        # Spotify can report its pre-seek timeline for a few media polls.
        # Briefly retain the user's chosen position until the timeline catches
        # up, so the slider and lyric highlight do not snap backwards.
        self._pending_seek_ms = target_ms
        self._pending_seek_track = playback.key
        self._pending_seek_started = time.monotonic()
        self._tick(force=True)

    def _request_island_seek(self, target_ms: int) -> None:
        """Use the same queued WinRT seek and anti-snapback as the main bar."""
        item = self.playback
        if item is None or item.duration_ms <= 0 or self.monitor is None:
            return
        target_ms = max(0, min(int(target_ms), item.duration_ms))
        if not self.monitor.submit_seek(target_ms):
            QMessageBox.warning(self, tr("控制失敗"), tr("找不到正在執行的 Spotify 播放工作階段。"))
            return
        self._seeking = False
        self._pending_seek_ms = target_ms
        self._pending_seek_track = item.key
        self._pending_seek_started = time.monotonic()
        self._tick(force=True)

    def _request_transport(self, command: str) -> None:
        if self.monitor is None or self.playback is None:
            return
        if command == "toggle":
            command = "pause" if self.playback.is_playing else "play"
        if not self.monitor.submit_control(command):
            QMessageBox.warning(self, tr("控制失敗"), tr("找不到正在執行的 Spotify 播放工作階段。"))

    def _got_control_result(self, action: str, accepted: bool, message: str) -> None:
        if self._closing:
            return
        if action == "seek" and not accepted:
            self._pending_seek_ms = None
            self._tick(force=True)
        if accepted:
            return
        LOG.warning("SMTC transport %s was rejected: %s", action, message)
        if "No active" in message:
            body = tr("找不到正在執行的 Spotify 播放工作階段。")
        else:
            body = tr("Spotify 不支援此媒體控制指令。")
        QMessageBox.warning(self, tr("控制失敗"), body)

    @staticmethod
    def _format_time(ms: int) -> str:
        sec = max(0, ms // 1000)
        return f"{sec // 60}:{sec % 60:02d}"

    def _got_playback(self, item: Playback | None) -> None:
        if self.demo or self._closing:
            return
        if item is None:
            if self.playback is not None:
                self._lyrics_retry_generation += 1
                self._lyrics_auto_retry_attempts = 0
            self.island.set_playback(None)
            self._lyrics_search_pending = False
            self._seeking = False
            self._pending_seek_ms = None
            if self.playback is not None:
                self.playback = None
                self._update_cover(None)
                self.lyrics = LyricsResult(kind="missing")
                self._raw_lyrics = self.lyrics
                self.current_index = None
                self.lyric_view.set_lyrics(())
                self._message(tr("沒有偵測到 Spotify 桌面版的播放工作階段"))
                self.track_label.setText(tr("等待 Spotify 播放"))
                self.artist_label.setText(tr("請啟動 Spotify Windows 桌面版並播放歌曲"))
            self._set_transport_enabled(False)
            self._tick()
            return

        is_new = self.playback is None or self.playback.key != item.key
        if is_new:
            self._pending_seek_ms = None
            self._lyrics_retry_generation += 1
            self._lyrics_auto_retry_attempts = 0
        self.playback = item
        self.island.set_playback(item)
        self._update_cover(item)
        self.track_label.setText(item.title)
        self.track_label.setToolTip(item.title)
        self.artist_label.setText(item.artist)
        self.artist_label.setToolTip(self.artist_label.text())
        self._set_transport_enabled(True, is_playing=item.is_playing)
        if is_new:
            self._lyrics_search_pending = True
            self.lyrics = LyricsResult(kind="missing")
            self._raw_lyrics = self.lyrics
            self.current_index = None
            self.lyric_view.set_lyrics(())
            self._message(tr("正在搜尋這首歌的歌詞…"))
            self._start_lyrics_lookup(item)
        self._tick()

    def _start_lyrics_lookup(self, item: Playback, *, force: bool = False) -> None:
        def run():
            result = self.service.fetch(item, force=force)
            self.bridge.lyric.emit(item.key, result)
        threading.Thread(target=run, daemon=True, name="LyricsLookup").start()

    def _got_lyric(self, key: str, result: LyricsResult) -> None:
        if result.kind == "cancelled":
            return  # superseded by a newer search; never flash an old result
        if self._closing or self.playback is None or self.playback.key != key:
            return
        if self.service.has_synced_local(self.playback) and not result.source.startswith("本機"):
            return  # Only a timed local override can supersede an online lookup.
        self._lyrics_search_pending = False
        self._raw_lyrics = result
        self.lyrics = display_lyrics(
            result, self.playback, mode=self.settings.lyrics_script,
            ui_language=current_language())
        self.current_index = None
        # Make decisions from the *visible* result, not the raw provider
        # response: some synced files consist entirely of credits/notices.
        if self.lyrics.is_synced:
            self.lyric_view.set_lyrics(self.lyrics.lines)
            self.stack.setCurrentIndex(0)
            self._tick(force=True)
        elif self.lyrics.kind in {"missing", "plain"}:
            # Older provider results may still carry static text; never show it.
            # The center of the window should contain only the short message.
            # No editing instructions or manual-sync controls appear here.
            self._message(tr(NO_LYRICS_MESSAGE))
        else:
            self._message(tr(result.message) if result.message else tr("歌詞查詢失敗，請稍後重試"))
        if not self.lyrics.is_synced and self.lyrics.kind in {"missing", "plain", "error"}:
            self._schedule_missing_lyrics_retry(key)

    def _update_lyrics_script(self) -> None:
        """Re-render cached raw lyrics for all consumers without a refetch."""
        self.lyrics = display_lyrics(
            self._raw_lyrics, self.playback, mode=self.settings.lyrics_script,
            ui_language=current_language())
        self.current_index = None
        self.lyric_view.set_lyrics(self.lyrics.lines if self.lyrics.is_synced else ())
        self._tick(force=True)

    def _schedule_missing_lyrics_retry(self, track_key: str) -> None:
        """Bound retries (25s, 90s) to the current song and lookup generation."""
        if self.demo or self._closing or self._lyrics_auto_retry_attempts >= 2:
            return
        generation = self._lyrics_retry_generation
        delay = (25_000, 90_000)[self._lyrics_auto_retry_attempts]
        QTimer.singleShot(delay, lambda: self._retry_missing_lyrics(track_key, generation))

    def _retry_missing_lyrics(self, track_key: str, generation: int) -> None:
        if (self.demo or self._closing or generation != self._lyrics_retry_generation or
                self.playback is None or self.playback.key != track_key or
                self._lyrics_search_pending or self.lyrics.is_synced or
                self.service.has_synced_local(self.playback)):
            return
        self._lyrics_auto_retry_attempts += 1
        self._lyrics_search_pending = True
        LOG.info("Retrying unavailable lyrics (%d/2) for %r / %r",
                 self._lyrics_auto_retry_attempts, self.playback.title, self.playback.artist)
        self._start_lyrics_lookup(self.playback, force=True)

    def _got_error(self, message: str) -> None:
        self._source_error = message
        if message and not self.playback:
            self._message(tr(message))

    def _sync_pending_search_page(self) -> None:
        """Show a blank transparent canvas only while overlay lyrics load.

        This is intentionally mode-aware: full-player mode keeps its loading
        information, and actual errors / missing lyrics remain visible.
        """
        if self._lyrics_search_pending:
            self.stack.setCurrentIndex(0 if self.settings.desktop_text_only else 1)

    def _message(self, text: str) -> None:
        self.big_message.setText(text)
        self.stack.setCurrentIndex(1)
        self._sync_pending_search_page()

    def _tick(self, *, force: bool = False) -> None:
        playback = self.playback
        if playback is None:
            self.island.set_timeline(0, 0)
            self.island.set_lyrics(self.lyrics, 0, self.settings.offset_ms)
            if not self._seeking:
                self.progress.setValue(0)
            self.elapsed.setText("0:00")
            self.total.setText("--:--")
            self._publish_obs_state()
            return
        pos = playback.position_at()
        if self._seeking and playback.key == self._seeking_track:
            # Background media polls must not move the knob under the cursor.
            self.elapsed.setText(self._format_time(self._seek_preview_ms))
            self.total.setText(self._format_time(playback.duration_ms) if playback.duration_ms else "--:--")
            self.island.set_timeline(self._seek_preview_ms, playback.duration_ms)
            self.island.set_lyrics(self.lyrics, self._seek_preview_ms, self.settings.offset_ms)
            self._publish_obs_state()
            return
        if self._pending_seek_ms is not None:
            age = time.monotonic() - self._pending_seek_started
            if playback.key != self._pending_seek_track or age >= 2.5:
                self._pending_seek_ms = None
            else:
                predicted = self._pending_seek_ms + (int(age * 1000) if playback.is_playing else 0)
                predicted = min(playback.duration_ms, predicted)
                if abs(pos - predicted) <= 1000:
                    self._pending_seek_ms = None
                else:
                    pos = predicted
        self.elapsed.setText(self._format_time(pos))
        self.total.setText(self._format_time(playback.duration_ms) if playback.duration_ms else "--:--")
        self.progress.setValue(int(pos / playback.duration_ms * 1000) if playback.duration_ms else 0)
        self.island.set_timeline(pos, playback.duration_ms)
        self.island.set_lyrics(self.lyrics, pos, self.settings.offset_ms)
        if not self.lyrics.is_synced:
            self._publish_obs_state()
            return
        current = active_lyric_index(self.lyrics.lines, pos, self.settings.offset_ms)
        if current != self.current_index:
            self.current_index = current
            self.lyric_view.set_active_index(current)
        self._publish_obs_state()

    def _publish_obs_state(self) -> None:
        if self.obs_server is None:
            return
        obs_playback = self.playback
        if obs_playback is not None and obs_playback.cover_id:
            if obs_playback.cover_id != self._obs_registered_cover_id:
                # A missing/corrupted local thumbnail used to be re-read and
                # SHA-256-checked on every 120ms UI tick (8 times/second).
                # Retry a failed registration at most once per 8 seconds.
                retry_due = (obs_playback.cover_id != self._obs_failed_cover_id
                             or time.monotonic() >= self._obs_failed_cover_retry_at)
                if retry_due:
                    artwork = Artwork(obs_playback.cover_id, obs_playback.cover_mime,
                                      Path(obs_playback.cover_path))
                    contents = verified_artwork_bytes(artwork)
                    if contents and self.obs_server.register_artwork(
                            artwork.image_id, contents, artwork.mime_type):
                        self._obs_registered_cover_id = artwork.image_id
                        self._obs_failed_cover_id = ""
                        self._obs_failed_cover_retry_at = 0.0
                    else:
                        self._obs_failed_cover_id = artwork.image_id
                        self._obs_failed_cover_retry_at = time.monotonic() + 8.0
            if obs_playback.cover_id != self._obs_registered_cover_id:
                # Never advertise a URL before its bytes can be served.
                obs_playback = replace(obs_playback, cover_id="", cover_path="", cover_mime="")
        self.obs_server.publish(make_overlay_state(
            obs_playback, self.lyrics,
            font_size=self.settings.font_size,
            line_count=self.settings.line_count,
            offset_ms=self.settings.offset_ms,
            smooth_lyrics=self.settings.smooth_lyrics,
            transition_ms=self.settings.transition_ms,
            show_cover=self.settings.obs_cover_enabled,
            message=self.big_message.text(),
        ))

    def _refresh_lyrics(self) -> None:
        if not self.playback:
            QMessageBox.information(self, tr("沒有歌曲"), tr("請先在 Spotify 桌面版播放一首歌曲。"))
            return
        if self.service.has_synced_local(self.playback):
            QMessageBox.information(self, tr("歌詞"), tr("目前使用已儲存的本機 LRC 歌詞"))
            return
        self._lyrics_retry_generation += 1
        self._lyrics_auto_retry_attempts = 0
        self.service.clear_cache(self.playback)
        self._lyrics_search_pending = True
        self.lyrics = LyricsResult(kind="missing")
        self._raw_lyrics = self.lyrics
        self.current_index = None
        self.lyric_view.set_lyrics(())
        self._message(tr("重新搜尋多來源同步歌詞中…"))
        self._start_lyrics_lookup(self.playback, force=True)

    def _position_settings_dialog(self, dialog: QDialog) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        bounds = screen.availableGeometry()
        if self.isVisible():
            x = self.geometry().center().x() - dialog.width() // 2
            y = self.geometry().center().y() - dialog.height() // 2
        else:
            x = bounds.center().x() - dialog.width() // 2
            y = bounds.center().y() - dialog.height() // 2
        dialog.move(
            max(bounds.left(), min(x, bounds.right() - dialog.width() + 1)),
            max(bounds.top(), min(y, bounds.bottom() - dialog.height() + 1)),
        )

    @staticmethod
    def _raise_modal_dialog(dialog: QDialog) -> None:
        if not dialog.isVisible():
            return
        dialog.raise_()
        dialog.activateWindow()
        # On Windows HWND_TOPMOST is explicit: Qt.Tool + always-on-top parent
        # must never overtake the settings dialog in the native Z-order.
        if sys.platform == "win32" and dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            try:
                import ctypes
                set_pos = ctypes.windll.user32.SetWindowPos
                set_pos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_uint]
                set_pos.restype = ctypes.c_int
                set_pos(ctypes.c_void_p(int(dialog.winId())),
                        ctypes.c_void_p(-1), 0, 0, 0, 0,
                        0x0001 | 0x0002)  # SWP_NOSIZE | SWP_NOMOVE
                dialog.activateWindow()
            except (AttributeError, OSError):
                LOG.exception("Unable to enforce settings dialog Z-order")

    def _open_settings(self) -> None:
        if self._settings_dialog is not None:
            self._raise_modal_dialog(self._settings_dialog)
            return
        was_game_overlay = self.settings.game_overlay_enabled
        if was_game_overlay:
            # Temporarily allow interaction with the modal preferences without
            # silently changing the saved game-mode preference.
            self._game_overlay_suspended = True
            self.click_through = False
            self._reapply_flags()
        elif self.click_through:
            self._toggle_click_through(False)
        if not self.isVisible():
            self._bring_back()
        # Keep the island visible throughout the modal dialog. The settings
        # dialog itself is promoted above the island when necessary.
        # SettingsDialog copies the current settings. Include any drag/resize
        # still waiting for the debounce timer before taking that snapshot.
        self._geometry_save_timer.stop()
        self._capture_active_geometry()
        dialog = SettingsDialog(self.settings, self)
        self._settings_dialog = dialog
        self._position_settings_dialog(dialog)
        # A topmost Qt.Tool overlay can otherwise obscure a normal child dialog
        # on Windows. Modal + topmost + activation fixes this for tray and toolbar.
        QTimer.singleShot(0, lambda: self._raise_modal_dialog(dialog))
        try:
            result = dialog.exec()
        finally:
            self._settings_dialog = None
            if was_game_overlay:
                self._game_overlay_suspended = False
                self.click_through = True
                self._reapply_flags()
        if result != QDialog.DialogCode.Accepted:
            dialog.deleteLater()
            return
        old = self.settings
        new = dialog.selected()
        dialog.deleteLater()
        if new.game_overlay_enabled and self.tray is None:
            new.game_overlay_enabled = False
            QMessageBox.warning(self, tr("遊戲浮動模式"),
                                tr("遊戲模式需要 Windows 系統匣，才能安全解除滑鼠穿透。"))
        if new.start_with_windows != old.start_with_windows:
            try:
                update_windows_startup(new.start_with_windows)
            except OSError as exc:
                new.start_with_windows = old.start_with_windows
                QMessageBox.warning(self, tr("無法設定開機啟動"), str(exc))
        self.settings = new
        self._apply_island_settings()
        self.click_through = bool(new.game_overlay_enabled)
        if new.game_overlay_enabled:
            self._game_zorder_timer.start()
        else:
            self._game_zorder_timer.stop()
        locale_changed = new.language != old.language
        if locale_changed:
            set_language(new.language)
            self.island.retranslate()
            self._retranslate_main()
        self._apply_display_mode(previous_mode=old.desktop_text_only)
        self._apply_line_styles()
        if self.tray is not None:
            self.action_cover.setChecked(new.desktop_cover_enabled)
            self.action_top.setChecked(new.topmost)
            self.action_game.setChecked(new.game_overlay_enabled)
            self.action_click.setChecked(self.click_through)
            self.action_obs_window.setChecked(new.obs_window_capture)
        if (new.topmost != old.topmost or new.obs_window_capture != old.obs_window_capture
                or new.game_overlay_enabled != old.game_overlay_enabled):
            self._reapply_flags()
        if (new.obs_browser_enabled != old.obs_browser_enabled
                or new.obs_browser_port != old.obs_browser_port):
            self._configure_obs_server()
        if new.lyrics_script != old.lyrics_script or locale_changed:
            self._update_lyrics_script()
        self._tick(force=True)
        self._persist()
        if self._obs_error and new.obs_browser_enabled:
            QMessageBox.warning(
                self, tr("OBS 來源尚未啟動"),
                f"無法啟用本機 OBS 瀏覽器來源：\n{self._obs_error}\n\n可在設定中修改連接埠。",
            )

    def _retranslate_main(self) -> None:
        self.lyric_heading.setText(tr("歌詞"))
        for button, text in ((self.text_button, "切換純文字懸浮模式"),
                             (self.settings_button, "開啟設定"),
                             (self.hide_button, "最小化至系統匣"),
                             (self.exit_button, "結束應用程式"),
                             (self.previous_button, "上一首"),
                             (self.next_button, "下一首")):
            button.setToolTip(tr(text))
            button.setAccessibleName(tr(text))
        self.progress.setToolTip(tr("拖曳進度條調整播放位置"))
        self.progress.setAccessibleName(tr("拖曳進度條調整播放位置"))
        self._set_transport_enabled(self.playback is not None,
                                    is_playing=bool(self.playback and self.playback.is_playing))
        if self.playback is None:
            self.track_label.setText(tr("等待 Spotify 播放"))
            self.artist_label.setText(tr("啟動 Spotify，播放你喜歡的音樂"))
            self._message(tr("啟動 Spotify 並播放歌曲"))
        if self.tray is not None:
            old_tray = self.tray
            old_tray.hide()
            old_tray.deleteLater()
            self.tray = None
            self._create_tray()

    def _configure_obs_server(self) -> None:
        self._obs_registered_cover_id = ""
        self._obs_failed_cover_id = ""
        self._obs_failed_cover_retry_at = 0.0
        if self.obs_server is not None:
            self.obs_server.stop()
            self.obs_server = None
        self._obs_error = ""
        if self.settings.obs_browser_enabled:
            server = ObsOverlayServer(port=self.settings.obs_browser_port)
            try:
                server.start()
            except (OSError, RuntimeError) as exc:
                # OBS is optional; broken/missing web assets must not close Qt.
                self._obs_error = str(exc)
                LOG.warning("Unable to start OBS browser source: %s", exc)
            else:
                self.obs_server = server
                self._publish_obs_state()
        if self.tray is not None:
            self.action_copy_obs.setEnabled(self.obs_server is not None)

    def _copy_obs_url(self) -> None:
        if self.obs_server is None:
            QMessageBox.warning(self, tr("OBS 來源尚未啟動"), self._obs_error or tr("請先在設定中啟用 OBS 瀏覽器來源。"))
            return
        QApplication.clipboard().setText(self.obs_server.url)
        if self.tray is not None:
            self.tray.showMessage(
                tr("OBS 來源網址已複製"),
                tr("將下方網址新增為 OBS 的「瀏覽器」來源。") + " " + self.obs_server.url,
                QSystemTrayIcon.MessageIcon.Information, 3500,
            )

    def _toggle_obs_window_capture(self, checked: bool) -> None:
        self.settings.obs_window_capture = bool(checked)
        self._reapply_flags()
        self._persist()

    def _toggle_topmost(self, checked: bool) -> None:
        self.settings.topmost = checked
        self._reapply_flags()
        self._persist()

    def _toggle_click_through(self, checked: bool) -> None:
        if self.tray is None:
            return
        if not checked and self.settings.game_overlay_enabled:
            self._toggle_game_overlay(False)
            return
        self.click_through = bool(checked)
        self.action_click.setChecked(self.click_through)
        self._reapply_flags()
        if self.click_through:
            self.tray.showMessage(
                tr("滑鼠穿透已啟用"), tr("右鍵點選右下角系統匣圖示可取消滑鼠穿透。"),
                QSystemTrayIcon.MessageIcon.Information, 3500,
            )

    def _toggle_game_overlay(self, checked: bool) -> None:
        """One-action game overlay; tray remains an unconditional escape hatch."""
        if self.tray is None:
            return
        enabled = bool(checked)
        if enabled and not self.settings.desktop_text_only:
            # The geometry of both modes must remain independent.
            self._toggle_text_mode(True)
        self.settings.game_overlay_enabled = enabled
        self.click_through = enabled
        self.action_game.setChecked(enabled)
        self.action_click.setChecked(enabled)
        if enabled:
            self._game_zorder_timer.start()
        else:
            self._game_zorder_timer.stop()
        self._reapply_flags()
        self._persist()
        if enabled:
            self.tray.showMessage(
                tr("遊戲浮動模式"), tr("歌詞將置頂且不阻擋滑鼠。從系統匣取消遊戲模式即可拖曳位置。"),
                QSystemTrayIcon.MessageIcon.Information, 4500,
            )

    def _ensure_game_overlay_zorder(self) -> None:
        """Keep above windowed/borderless games without stealing game focus.

        This is intentionally not a DirectX hook or an exclusive-fullscreen
        overlay. Those scenarios cannot be guaranteed by a normal Qt window.
        """
        if (sys.platform != "win32" or self._closing or not self.isVisible()
                or self.isMinimized() or self._game_overlay_suspended
                or not self.settings.game_overlay_enabled):
            return
        try:
            import ctypes
            # HWND_TOPMOST, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE |
            # SWP_NOOWNERZORDER. No foreground activation or focus switching.
            ctypes.windll.user32.SetWindowPos(
                ctypes.c_void_p(int(self.winId())), ctypes.c_void_p(-1),
                0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010 | 0x0200,
            )
        except (AttributeError, OSError, TypeError, ValueError):
            LOG.exception("Unable to maintain game overlay Z-order")

    def _reapply_flags(self) -> None:
        was_visible = self.isVisible()
        pos = self.pos()
        size = self.size()
        was_transition = self._geometry_transition
        self._geometry_transition = True
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,
                              self.settings.game_overlay_enabled and
                              not self._game_overlay_suspended)
            self.setWindowFlags(self._window_flags())
            self.resize(size)
            self.move(pos)
            self._apply_window_opacity()
            if was_visible:
                self.show()
                if self.settings.game_overlay_enabled and not self._game_overlay_suspended:
                    self._ensure_game_overlay_zorder()
                else:
                    self.raise_()
        finally:
            self._geometry_transition = was_transition

    def _on_tray_activation(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._bring_back()

    def _bring_back(self) -> None:
        if self.click_through and not self.settings.game_overlay_enabled:
            self._toggle_click_through(False)
        self.show()
        if self.settings.game_overlay_enabled:
            self._ensure_game_overlay_zorder()
        else:
            self.raise_()
            self.activateWindow()
        if self.settings.dynamic_island_enabled and self.island.isVisible():
            # The main window can also be topmost. Keep its mini player above it.
            self.island.raise_()

    def _persist(self) -> None:
        if getattr(self, "_geometry_ready", False) and not self._geometry_transition:
            self._capture_active_geometry()
        try:
            save_settings(self.settings)
        except OSError:
            LOG.exception("Unable to save settings")

    def _exit(self) -> None:
        self._geometry_save_timer.stop()
        self._game_zorder_timer.stop()
        self._persist()
        self.island.hide()
        self._closing = True
        if self.monitor:
            self.monitor.stop()
        if self.obs_server:
            self.obs_server.stop()
            self.obs_server = None
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event) -> None:
        if not self._closing:
            self._exit()
        event.accept()

    def _load_demo(self) -> None:
        lines = (
            LyricLine(0, "城市甦醒，光穿過街道"),
            LyricLine(3500, "讓每個微小心願都有訊號"),
            LyricLine(7500, "跟著節奏，向新的世界奔跑"),
            LyricLine(11500, "把今天寫成最閃亮的篇章"),
            LyricLine(16000, "音樂還在，我們就不會停靠"),
            LyricLine(21000, "此刻每一秒，都值得收藏"),
        )
        fake = Playback(
            title="浮動歌詞 · 展示模式", artist="Spotify Overlay Lyrics",
            album="UI Preview", duration_ms=30000, position_ms=0, is_playing=True,
            cover_path=str(Path(__file__).resolve().parent.parent /
                           "assets" / "island_demo_cover.png"),
            observed_monotonic=time.monotonic(),
        )
        self.playback = fake
        self.island.set_playback(fake)
        self.track_label.setText(fake.title)
        self.artist_label.setText(fake.artist)
        self._got_lyric(fake.key, LyricsResult(kind="synced", lines=lines, source="範例資料"))


def launch(*, demo: bool = False) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Spotify Overlay Lyrics")
    app.setOrganizationName("DesktopLyrics")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    window = LyricsWindow(demo=demo)
    window.show()
    if window.settings.dynamic_island_enabled:
        window.island.raise_()
    return app.exec()
