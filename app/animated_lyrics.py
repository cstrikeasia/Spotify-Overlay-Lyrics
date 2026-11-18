"""60 fps animated lyric viewport for the transparent Qt desktop overlay.

The lines remain in a stable virtual layout. Instead of replacing the text in
five fixed QLabel slots, the viewport scrolls a pixel-valued camera between
line centers; a mid-animation update starts from the current displayed pixel.
"""
from __future__ import annotations

import bisect

from PySide6.QtCore import QEasingCurve, QPointF, Property, QPropertyAnimation, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPainter,
                           QTextLayout, QTextOption)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .models import LyricLine
from .lyric_layout import fit_wrapped_line


class AnimatedLyricsView(QWidget):
    """Paint high-DPI lyrics with motion, focus/size/opacity interpolation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lines: tuple[str, ...] = ()
        self._centers: list[float] = []
        self._heights: list[float] = []
        self._text_heights: list[float] = []
        self._fitted_sizes: list[int] = []
        self._overflow_scroll_y = 0.0
        self._overflow_height = 0.0
        # -1 is the unhighlighted intro before the first timed lyric.
        self._active_index: int = -1
        self._camera = 0.0
        self._intro_focus = 0.0
        self._font_size = 26
        self._line_count = 5
        self._smooth = True
        self._effect = "scroll"  # none / scroll / fade; app mode is always static
        self._fade_from_index: int | None = None
        self._fade_progress = 1.0
        self._duration_ms = 620
        self._text_only = False
        self._text_color = QColor("#F7FFF9")
        # Cache the same native text layout and a very soft shadow rendered
        # from it. Avoid outline paths: shaped CJK / fallback fonts may not
        # share the glyph positions used by manually converted path glyphs.
        self._overlay_layout_cache: dict[tuple[int, int, int, int], tuple[QTextLayout, QImage, float]] = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._motion = QPropertyAnimation(self, b"camera", self)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.setDuration(self._duration_ms)
        self._intro_fade = QPropertyAnimation(self, b"introFocus", self)
        self._intro_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro_fade.setDuration(260)
        self._line_fade = QPropertyAnimation(self, b"fadeProgress", self)
        self._line_fade.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._line_fade.setDuration(self._duration_ms)
        self._line_fade.finished.connect(self._finish_line_fade)

    def _finish_line_fade(self) -> None:
        self._fade_from_index = None
        self._fade_progress = 1.0
        self.update()

    @Property(float)
    def fadeProgress(self) -> float:
        return self._fade_progress

    @fadeProgress.setter
    def fadeProgress(self, value: float) -> None:
        self._fade_progress = max(0.0, min(1.0, float(value)))
        self.update()

    @Property(float)
    def introFocus(self) -> float:
        return self._intro_focus

    @introFocus.setter
    def introFocus(self, value: float) -> None:
        self._intro_focus = max(0.0, min(1.0, value))
        self.update()

    @Property(float)
    def camera(self) -> float:
        return self._camera

    @camera.setter
    def camera(self, y: float) -> None:
        self._camera = y
        self.update()

    def configure(self, *, font_size: int, line_count: int, smooth: bool,
                  duration_ms: int, text_only: bool = False,
                  text_color: str = "#F7FFF9", effect: str | None = None) -> None:
        """Apply live display preferences without scrolling to a stale center."""
        size = max(17, min(64, int(font_size)))
        # Text-only desktop mode intentionally shows just the active line.
        # This does not change the OBS browser source's line count.
        count = 1 if text_only else (3 if int(line_count) == 3 else 5)
        duration = max(200, min(1200, int(duration_ms)))
        geometry_changed = size != self._font_size or bool(text_only) != self._text_only
        self._font_size = size
        self._line_count = count
        # Complete-player mode is intentionally 100% static. Overlay mode
        # chooses exactly one independent effect; the legacy `smooth` argument
        # remains supported by existing callers and tests.
        self._text_only = bool(text_only)
        selected = (effect if effect is not None else ("scroll" if smooth else "none"))
        if selected not in {"none", "scroll", "fade"}:
            selected = "scroll"
        self._effect = selected if self._text_only else "none"
        self._smooth = self._effect == "scroll"
        self._duration_ms = duration
        self._text_color = QColor(text_color) if QColor(text_color).isValid() else QColor("#F7FFF9")
        self._motion.setDuration(duration)
        self._line_fade.setDuration(duration)
        # Never allow an effect from the previous mode to leak into this one.
        self._motion.stop()
        self._intro_fade.stop()
        self._line_fade.stop()
        self._fade_from_index = None
        self._fade_progress = 1.0
        self.introFocus = 1.0 if self._active_index >= 0 else 0.0
        if self._centers:
            self.camera = self._centers[max(0, self._active_index)]
        if geometry_changed:
            self._rebuild_geometry()
        self.update()

    def set_lyrics(self, lines: tuple[LyricLine, ...]) -> None:
        """Reset the viewport on track changes, including asynchronous lyrics."""
        self._motion.stop()
        self._intro_fade.stop()
        self._line_fade.stop()
        self._fade_from_index = None
        self._fade_progress = 1.0
        self._lines = tuple(line.text for line in lines)
        self._overlay_layout_cache.clear()
        self._active_index = -1
        self._overflow_scroll_y = 0.0
        self._overflow_height = 0.0
        self.setToolTip("")
        self.introFocus = 0.0
        self._rebuild_geometry()

    def set_active_index(self, index: int, *, animate: bool = True) -> None:
        if not self._centers:
            return
        target_index = -1 if index < 0 else min(index, len(self._centers) - 1)
        if target_index == self._active_index:
            return
        previous = self._active_index
        # If another line arrives halfway through a fade, take the line that
        # is actually visible rather than flashing the not-yet-shown line.
        visible_previous = previous
        if (self._effect == "fade" and self._fade_from_index is not None
                and self._fade_progress < 0.5):
            visible_previous = self._fade_from_index
        self._active_index = target_index
        self._overflow_scroll_y = 0.0
        self._overflow_height = 0.0
        # The tooltip remains a way to read exceptionally long single lines
        # even if the user has deliberately kept the window extremely small.
        self.setToolTip(self._lines[target_index] if target_index >= 0 else "")
        self._motion.stop()
        self._intro_fade.stop()
        self._line_fade.stop()
        self._fade_from_index = None
        self._fade_progress = 1.0
        if target_index == -1:
            # Seeking back into the intro should remove highlighting at once.
            self.introFocus = 0.0
            self.camera = self._centers[0]
            return
        target_y = self._centers[target_index]
        if self._text_only and self._effect == "fade":
            # Fade out the previous lyric, then fade in the next *at the same
            # position*. No camera animation or moving text in this mode.
            self.introFocus = 1.0
            self.camera = target_y
            is_next_line = previous >= 0 and 0 < abs(target_index - previous) <= 2
            first_line = previous == -1 and target_index == 0
            if animate and (is_next_line or first_line):
                self._fade_from_index = visible_previous
                self._fade_progress = 0.0
                self._line_fade.setStartValue(0.0)
                self._line_fade.setEndValue(1.0)
                self._line_fade.start()
            return
        if previous == -1 and target_index == 0 and animate and self._smooth:
            # The first lyric comes into focus gently, not while the song is
            # playing instrumental audio before its real LRC timestamp.
            self._intro_fade.setStartValue(self._intro_focus)
            self._intro_fade.setEndValue(1.0)
            self._intro_fade.start()
        else:
            self.introFocus = 1.0
        # A seek or a song switch is not a normal lyric transition. Snapping
        # prevents hundreds of unneeded lines from flying across the screen.
        should_animate = (
            animate and self._smooth and previous is not None
            and 0 < abs(target_index - previous) <= 2
        )
        if not should_animate:
            self.camera = target_y
            return
        self._motion.setStartValue(self._camera)
        self._motion.setEndValue(target_y)
        self._motion.start()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild_geometry()

    def _measure_wrapped_height(self, text: str, size: int, width: int) -> float:
        """Measure with the same font, wrapping and width as paintEvent."""
        font = QFont("Microsoft JhengHei UI")
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        bounds = metrics.boundingRect(
            QRect(0, 0, max(1, width), 100000),
            int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap),
            text or " ",
        )
        return float(max(metrics.height(), bounds.height()))

    def _rebuild_geometry(self) -> None:
        self._motion.stop()
        self._overlay_layout_cache.clear()
        # Resizes should display a single correctly laid-out line instead of
        # keeping text from the old dimensions in a partially drawn fade.
        self._line_fade.stop()
        self._fade_from_index = None
        self._fade_progress = 1.0
        if not self._lines:
            self._heights = []
            self._text_heights = []
            self._fitted_sizes = []
            self._centers = []
            self._overflow_height = 0.0
            self.camera = 0.0
            return
        # Precisely match the painter's 17px/16px desktop margins and
        # 12px/12px overlay margins.  The previous implementation measured
        # at a different width, allowing an extra line to wrap while drawing.
        width = max(1, self.width() - (24 if self._text_only else 33))
        vertical_padding = 26 if self._text_only else 18
        available = max(0, self.height() - vertical_padding)
        self._heights = []
        self._text_heights = []
        self._fitted_sizes = []
        for line in self._lines:
            size, measured = fit_wrapped_line(
                self._font_size,
                available,
                lambda candidate, line=line: self._measure_wrapped_height(line, candidate, width),
            )
            self._fitted_sizes.append(size)
            self._text_heights.append(measured)
            # Keep the existing virtual scroll layout.  A taller wrapped
            # line is allocated more space so it cannot overlap neighbours.
            self._heights.append(measured + (16 if self._text_only else 4))
        self._centers = [self._heights[0] / 2.0]
        gap = max(15.0, self._font_size * 0.66)
        for i in range(1, len(self._lines)):
            self._centers.append(
                self._centers[-1] + (self._heights[i - 1] + self._heights[i]) / 2.0 + gap
            )
        self._overflow_scroll_y = 0.0
        self.camera = self._centers[max(0, min(self._active_index, len(self._centers) - 1))]

    def _paint_compact_lyric(self, painter: QPainter, index: int) -> None:
        """Show the entire current line when there is no room for previews.

        No animation is introduced into application mode.  For unusually
        long lines that exceed even the 10px minimum, the mouse wheel can
        scroll to the remaining words without resizing the window.
        """
        font = QFont("Microsoft JhengHei UI")
        font.setPixelSize(self._fitted_sizes[index])
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        padding = 7.0
        usable_height = max(1.0, self.height() - padding * 2)
        text_height = self._text_heights[index]
        self._overflow_height = max(0.0, text_height - usable_height)
        self._overflow_scroll_y = min(self._overflow_scroll_y, self._overflow_height)
        if self._overflow_height:
            rect = QRectF(17.0, padding - self._overflow_scroll_y,
                          max(1.0, self.width() - 33.0), text_height + 2.0)
            alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        else:
            rect = QRectF(17.0, padding, max(1.0, self.width() - 33.0), usable_height)
            alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(rect, alignment | Qt.TextFlag.TextWordWrap, self._lines[index])
        if self._overflow_height:
            # A thin scroll thumb tells the user the full line can be read by
            # scrolling.  Never show a scrollbar on normally fitted lines.
            track_height = max(10.0, usable_height)
            thumb_height = max(14.0, usable_height * usable_height / text_height)
            travel = max(0.0, track_height - thumb_height)
            offset = travel * (self._overflow_scroll_y / self._overflow_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 105))
            painter.drawRoundedRect(QRectF(self.width() - 5.0, padding + offset,
                                            2.5, thumb_height), 1.25, 1.25)

    def wheelEvent(self, event) -> None:
        if not self._text_only and self._overflow_height > 0:
            degrees = event.angleDelta().y() / 8.0
            self._overflow_scroll_y = max(
                0.0, min(self._overflow_height,
                         self._overflow_scroll_y - degrees / 15.0 * 28.0)
            )
            self.update()
            event.accept()
            return
        super().wheelEvent(event)

    def _fractional_index(self) -> float:
        """A spatially interpolated focus avoids suddenly changing the highlight."""
        if not self._centers:
            return 0.0
        right = bisect.bisect_right(self._centers, self._camera)
        if right <= 0:
            return 0.0
        if right >= len(self._centers):
            return float(len(self._centers) - 1)
        left = right - 1
        span = self._centers[right] - self._centers[left]
        return left + max(0.0, min(1.0, (self._camera - self._centers[left]) / span))

    @staticmethod
    def _appearance(distance: float, count: int) -> tuple[float, float]:
        """Returns glyph scale and opacity, including faded edge lines."""
        d = abs(distance)
        if count == 1:
            # Blend the outgoing and incoming line as the camera moves.
            return 1.0, max(0.0, 1.0 - d)
        scale = 1.0 - 0.12 * min(d, 1.0) - 0.06 * min(max(d - 1.0, 0.0), 1.0)
        if d <= 1:
            opacity = 1.0 - 0.45 * d
        elif d <= 2:
            opacity = 0.55 - 0.28 * (d - 1.0)
        else:
            opacity = max(0.0, 0.28 * (3.0 - d))
        limit = 1.0 if count == 3 else 2.0
        if d > limit:
            opacity *= max(0.0, 1.0 - (d - limit) / 0.70)
        return max(0.65, scale), max(0.0, min(1.0, opacity))

    @staticmethod
    def _make_overlay_shadow(layout: QTextLayout, width: int, height: float,
                              dpr: float) -> QImage:
        """Cache a subtle diffused shadow using the *actual* shaped text.

        No solid black glyph outline or duplicate 2px-offset text is painted.
        Low-opacity samples around the original baseline create a smooth,
        soft edge, slightly weighted downward for readability on bright games.
        The shadow is rasterized only when text/size/width/DPI changes; active
        60 fps scroll/fade repaints use a single cached image draw.
        """
        margin = 5.0
        pixel_width = max(1, int(round((width + 2 * margin) * dpr)))
        pixel_height = max(1, int(round((height + 2 * margin) * dpr)))
        shadow = QImage(pixel_width, pixel_height,
                        QImage.Format.Format_ARGB32_Premultiplied)
        shadow.setDevicePixelRatio(dpr)
        shadow.fill(Qt.GlobalColor.transparent)
        p = QPainter(shadow)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        # Wider, fainter strokes build diffusion instead of a visible border.
        # All passes use QTextLayout.draw to match native CJK/emoji shaping.
        samples = (
            (-2.0, 0.7, 10), (2.0, 0.7, 10),
            (0.0, -1.7, 9), (0.0, 2.4, 13),
            (-1.1, -0.7, 13), (1.1, -0.7, 13),
            (-1.2, 1.5, 20), (1.2, 1.5, 20),
            (0.0, 1.0, 30),
        )
        for dx, dy, alpha in samples:
            p.setPen(QColor(0, 0, 0, alpha))
            layout.draw(p, QPointF(margin + dx, margin + dy))
        p.end()
        return shadow

    def _overlay_glyph_layout(self, index: int, pixel_size: int,
                               width: int) -> tuple[QTextLayout, QImage, float]:
        """Native wrapped text and cached soft shadow at the current DPI."""
        width = max(1, int(width))
        dpr = max(1.0, float(self.devicePixelRatioF()))
        # The DPR key prevents blurry cached shadows when dragging between
        # monitors with different Windows scaling (100/125/150/200%).
        key = (index, pixel_size, width, round(dpr * 100))
        cached = self._overlay_layout_cache.get(key)
        if cached is not None:
            return cached
        font = QFont("Microsoft JhengHei UI")
        font.setPixelSize(pixel_size)
        font.setWeight(QFont.Weight.Bold)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout = QTextLayout(self._lines[index], font)
        layout.setTextOption(option)
        layout.beginLayout()
        cursor_y = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(max(0.0, (width - line.naturalTextWidth()) / 2.0),
                                     cursor_y))
            cursor_y += line.height()
        layout.endLayout()
        shadow = self._make_overlay_shadow(layout, width, cursor_y, dpr)
        # Bound image memory during long sessions and repeated resizes.
        if len(self._overlay_layout_cache) >= 48:
            self._overlay_layout_cache.clear()
        result = layout, shadow, cursor_y
        self._overlay_layout_cache[key] = result
        return result

    def _paint_overlay_line(self, painter: QPainter, index: int, opacity: float,
                             rect: QRectF, pixel_size: int) -> None:
        """Paint clean, bold native text over a restrained, cached soft shadow."""
        if index < 0 or index >= len(self._lines) or opacity <= 0.001:
            return
        layout, shadow, line_height = self._overlay_glyph_layout(
            index, pixel_size, round(rect.width()))
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.translate(rect.x(), rect.y() + (rect.height() - line_height) / 2.0)
        # The mask includes a 5px safety margin so the soft shadow cannot be
        # clipped by its own image. The actual text is never moved or thickened.
        painter.drawImage(QPointF(-5.0, -5.0), shadow)
        painter.setPen(self._text_color)
        layout.draw(painter, QPointF(0.0, 0.0))
        painter.restore()

    def _paint_overlay_fade_line(self, painter: QPainter, index: int,
                                  opacity: float) -> None:
        """Stationary fade uses the same clear rendering as overlay scroll."""
        if index < 0 or index >= len(self._lines) or opacity <= 0.001:
            return
        rect = QRectF(12.0, 4.0, float(max(1, self.width() - 24)),
                      float(max(1, self.height() - 8)))
        self._paint_overlay_line(painter, index, opacity, rect,
                                  self._fitted_sizes[index])

    def paintEvent(self, event) -> None:
        if not self._lines or self.width() < 10 or self.height() < 10:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setClipRect(self.rect())
        if self._text_only and self._effect == "fade":
            # The intro remains completely empty until its first timestamp.
            if self._active_index >= 0:
                if self._fade_from_index is None:
                    self._paint_overlay_fade_line(painter, self._active_index, 1.0)
                elif self._fade_from_index < 0:
                    self._paint_overlay_fade_line(painter, self._active_index,
                                                   self._fade_progress)
                else:
                    # Sequential fade-out / fade-in: no overlapping sentences.
                    self._paint_overlay_fade_line(
                        painter, self._fade_from_index,
                        max(0.0, 1.0 - 2.0 * self._fade_progress))
                    self._paint_overlay_fade_line(
                        painter, self._active_index,
                        max(0.0, 2.0 * self._fade_progress - 1.0))
            painter.end()
            return
        if not self._text_only and self._active_index >= 0:
            index = self._active_index
            # A short player should prioritise the active lyric over previews.
            # In roomy windows the existing multiple-line layout is retained.
            if (self._fitted_sizes[index] < self._font_size
                    or self.height() < max(110, self._font_size * 4)):
                self._paint_compact_lyric(painter, index)
                painter.end()
                return
        self._overflow_height = 0.0
        focus = self._fractional_index()
        near = bisect.bisect_left(self._centers, self._camera)
        top = max(0, near - 6)
        bottom = min(len(self._lines), near + 7)
        mid = self.height() / 2.0
        for i in range(top, bottom):
            y = mid + self._centers[i] - self._camera
            if y < -self._heights[i] or y > self.height() + self._heights[i]:
                continue
            # Fade the preview into focus only when its first timestamp is
            # reached. In single-line mode the intro is entirely invisible.
            distance = abs(i - focus) + (1.0 - self._intro_focus)
            scale, opacity = self._appearance(distance, self._line_count)
            # Feather out the very top and bottom without a harsh clipping edge.
            edge = min(y, self.height() - y)
            opacity *= max(0.0, min(1.0, (edge + 14.0) / max(30.0, self._font_size * 1.3)))
            if opacity < 0.008:
                continue
            font = QFont("Microsoft JhengHei UI")
            font.setPixelSize(max(10, int(round(self._fitted_sizes[i] * scale))))
            font.setWeight(QFont.Weight.Bold)
            painter.setFont(font)
            # Color/opacity shift follows the *interpolated* camera, not an
            # abrupt active/inactive flag at the timecode boundary.
            shade = min(distance, 1.0)
            color = QColor(self._text_color) if self._text_only else QColor(
                round(255 - 43 * shade),
                round(255 - 37 * shade),
                round(255 - 42 * shade),
            )
            color.setAlphaF(opacity)
            h = self._heights[i] + 8
            target_rect = (QRectF(12.0, y - h / 2, float(self.width() - 24), h)
                           if self._text_only else
                           QRectF(17.0, y - h / 2, float(max(20, self.width() - 33)), h))
            if self._text_only:
                self._paint_overlay_line(painter, i, opacity, target_rect,
                                          font.pixelSize())
            else:
                painter.setPen(color)
                painter.drawText(target_rect,
                                 Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                                 | Qt.TextFlag.TextWordWrap, self._lines[i])
        painter.end()
