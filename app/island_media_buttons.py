"""iOS-inspired, vector-drawn playback controls used only by Dynamic Island.

Separating this from toolbar_icons ensures that changes to the island's icon
proportions do not restyle the desktop application's normal toolbar.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolButton


class IslandMediaButton(QToolButton):
    """Real transport buttons, visually matched to a monochrome iOS media card."""

    SYMBOLS = {"skip-back", "skip-forward", "play", "pause", "airplay-audio"}

    def __init__(self, symbol: str, tooltip: str, *, size: int, glyph_size: float,
                 secondary: bool = False, route: bool = False) -> None:
        super().__init__()
        if symbol not in self.SYMBOLS:
            raise ValueError(f"Unknown media symbol: {symbol}")
        self.icon_name = symbol
        self._glyph_size = float(glyph_size)
        self._secondary = bool(secondary)
        self._route = bool(route)
        self.setFixedSize(int(size), int(size))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setText("")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # No native QToolButton frame; the screenshot's iOS media symbols are
        # white shapes directly on the black material, not white discs.
        self.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")

    @staticmethod
    def _triangle(points: tuple[tuple[float, float], ...]) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        path.closeSubpath()
        return path

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.isEnabled() and self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 17 if not self._route else 10))
            painter.drawEllipse(QRectF(3.0, 3.0, self.width() - 6.0, self.height() - 6.0))
        if not self.isEnabled():
            color = QColor("#66666B")
        elif self._route:
            color = QColor("#88888E")
        else:
            color = QColor("#FCFCFE")
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(self._glyph_size / 24.0, self._glyph_size / 24.0)
        painter.translate(-12.0, -12.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)

        if self.icon_name == "play":
            painter.drawPath(self._triangle(((6.8, 4.4), (20.0, 12.0), (6.8, 19.6))))
        elif self.icon_name == "pause":
            painter.drawRoundedRect(QRectF(6.5, 4.1, 4.15, 15.8), 1.10, 1.10)
            painter.drawRoundedRect(QRectF(13.35, 4.1, 4.15, 15.8), 1.10, 1.10)
        elif self.icon_name == "skip-back":
            painter.drawRoundedRect(QRectF(2.9, 5.0, 2.35, 14.0), 0.45, 0.45)
            painter.drawPath(self._triangle(((13.55, 4.5), (5.8, 12.0), (13.55, 19.5))))
            painter.drawPath(self._triangle(((21.4, 4.5), (13.65, 12.0), (21.4, 19.5))))
        elif self.icon_name == "skip-forward":
            painter.drawRoundedRect(QRectF(18.75, 5.0, 2.35, 14.0), 0.45, 0.45)
            painter.drawPath(self._triangle(((2.6, 4.5), (10.35, 12.0), (2.6, 19.5))))
            painter.drawPath(self._triangle(((10.45, 4.5), (18.2, 12.0), (10.45, 19.5))))
        elif self.icon_name == "airplay-audio":
            # Three gentle upward wireless arcs over the filled speaker triangle.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(color)
            pen.setWidthF(1.65)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(QRectF(3.0, 2.3, 18.0, 18.0), 25 * 16, 130 * 16)
            painter.drawArc(QRectF(6.15, 5.8, 11.7, 11.7), 28 * 16, 124 * 16)
            painter.drawArc(QRectF(9.0, 9.1, 6.0, 6.0), 33 * 16, 114 * 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(self._triangle(((12.0, 14.8), (18.3, 22.0), (5.7, 22.0))))
        painter.end()
