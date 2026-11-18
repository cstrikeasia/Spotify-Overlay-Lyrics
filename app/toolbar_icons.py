"""Resolution-independent, emoji-free toolbar icons for the PySide6 desktop UI.

The icons are drawn using Qt vector paths; there are no external icon-font
or image dependencies. This keeps them crisp on Windows HiDPI displays.
"""
from __future__ import annotations

from math import cos, pi, sin

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolButton


class VectorToolButton(QToolButton):
    """Paint a consistent 20 px outline icon over the native styled button."""

    ICONS = frozenset(("type", "refresh-cw", "settings", "minus", "x", "skip-back", "skip-forward", "play", "pause", "airplay-audio"))

    def __init__(self, icon_name: str, tooltip: str, *, width: int = 34) -> None:
        super().__init__()
        if icon_name not in self.ICONS:
            raise ValueError(f"Unsupported toolbar icon: {icon_name}")
        self.icon_name = icon_name
        self.setText("")
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("toolbarIcon")
        self.setFixedSize(max(30, width), 32)

    def paintEvent(self, event) -> None:
        # Let Qt draw the button's background, hover and keyboard focus state.
        super().paintEvent(event)
        variant = str(self.property("variant") or "")
        if variant == "iosPrimary":
            color = QColor("#F4F4F6" if self.isEnabled() else "#8E8E93")
        elif variant in {"iosSecondary", "iosRoute"}:
            if not self.isEnabled():
                color = QColor("#67676D")
            elif self.underMouse():
                color = QColor("#FFFFFF")
            else:
                color = QColor("#F2F2F6" if variant == "iosSecondary" else "#A8A8AF")
        elif self.property("variant") == "transport":
            color = QColor("#111111" if self.isEnabled() else "#BBBBBB")
        elif not self.isEnabled():
            color = QColor("#626262")
        elif self.underMouse() and self.property("variant") == "accent":
            color = QColor("#1ED760")
        elif self.underMouse():
            color = QColor("#FFFFFF")
        else:
            color = QColor("#B3B3B3")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        icon_px = float(self.property("glyphSize") or 20.0)
        painter.translate((self.width() - icon_px) / 2.0,
                          (self.height() - icon_px) / 2.0)
        painter.scale(icon_px / 24.0, icon_px / 24.0)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(color)
        pen.setWidthF(1.85)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        self._draw_symbol(painter)
        painter.end()

    def _draw_symbol(self, painter: QPainter) -> None:
        if self.icon_name == "type":
            # Letter shapes are real vector paths, not glyphs/emoji.
            painter.drawLine(QPointF(4.0, 19.0), QPointF(9.0, 5.0))
            painter.drawLine(QPointF(9.0, 5.0), QPointF(14.0, 19.0))
            painter.drawLine(QPointF(6.1, 13.5), QPointF(11.9, 13.5))
            painter.drawEllipse(QRectF(15.5, 13.0, 4.6, 5.6))
            painter.drawLine(QPointF(20.1, 12.4), QPointF(20.1, 19.1))

        elif self.icon_name == "refresh-cw":
            upper = QPainterPath()
            upper.moveTo(20.0, 11.0)
            upper.cubicTo(19.6, 6.9, 16.3, 4.0, 12.0, 4.0)
            upper.cubicTo(8.3, 4.0, 5.6, 5.8, 4.0, 9.0)
            painter.drawPath(upper)
            painter.drawLine(QPointF(4.0, 4.0), QPointF(4.0, 9.0))
            painter.drawLine(QPointF(4.0, 9.0), QPointF(9.0, 9.0))
            lower = QPainterPath()
            lower.moveTo(4.0, 13.0)
            lower.cubicTo(4.4, 17.1, 7.7, 20.0, 12.0, 20.0)
            lower.cubicTo(15.7, 20.0, 18.4, 18.2, 20.0, 15.0)
            painter.drawPath(lower)
            painter.drawLine(QPointF(20.0, 20.0), QPointF(20.0, 15.0))
            painter.drawLine(QPointF(20.0, 15.0), QPointF(15.0, 15.0))

        elif self.icon_name == "settings":
            # Eight-tooth gear silhouette with a circular central cut-out.
            gear = QPainterPath()
            first_point = True
            for tooth in range(8):
                center = (-90.0 + tooth * 45.0) * pi / 180.0
                for degrees, radius in ((-22.5, 8.0), (-13.5, 8.0),
                                        (-10.0, 10.2), (10.0, 10.2),
                                        (13.5, 8.0), (22.5, 8.0)):
                    angle = center + degrees * pi / 180.0
                    point = QPointF(12.0 + radius * cos(angle),
                                    12.0 + radius * sin(angle))
                    if first_point:
                        gear.moveTo(point)
                        first_point = False
                    else:
                        gear.lineTo(point)
            gear.closeSubpath()
            painter.drawPath(gear)
            painter.drawEllipse(QRectF(9.0, 9.0, 6.0, 6.0))

        elif self.icon_name == "minus":
            painter.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))

        elif self.icon_name == "x":
            painter.drawLine(QPointF(6.0, 6.0), QPointF(18.0, 18.0))
            painter.drawLine(QPointF(18.0, 6.0), QPointF(6.0, 18.0))

        elif self.icon_name in {"skip-back", "skip-forward"}:
            painter.setBrush(painter.pen().color())
            painter.setPen(Qt.PenStyle.NoPen)
            if self.icon_name == "skip-back":
                a = QPainterPath()
                a.moveTo(17.4, 5.1)
                a.lineTo(10.9, 12.0)
                a.lineTo(17.4, 18.9)
                a.closeSubpath()
                b = QPainterPath()
                b.moveTo(11.0, 5.1)
                b.lineTo(4.5, 12.0)
                b.lineTo(11.0, 18.9)
                b.closeSubpath()
            else:
                a = QPainterPath()
                a.moveTo(6.6, 5.1)
                a.lineTo(13.1, 12.0)
                a.lineTo(6.6, 18.9)
                a.closeSubpath()
                b = QPainterPath()
                b.moveTo(13.0, 5.1)
                b.lineTo(19.5, 12.0)
                b.lineTo(13.0, 18.9)
                b.closeSubpath()
            painter.drawPath(a)
            painter.drawPath(b)

        elif self.icon_name == "play":
            path = QPainterPath()
            path.moveTo(8.5, 5.5)
            path.lineTo(19, 12)
            path.lineTo(8.5, 18.5)
            path.closeSubpath()
            painter.setBrush(painter.pen().color())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)

        elif self.icon_name == "pause":
            painter.setBrush(painter.pen().color())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(7, 5, 3.3, 14), 0.9, 0.9)
            painter.drawRoundedRect(QRectF(13.7, 5, 3.3, 14), 0.9, 0.9)

        elif self.icon_name == "airplay-audio":
            color = painter.pen().color()
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(color)
            pen.setWidthF(1.9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawArc(QRectF(4.5, 5.0, 15.0, 15.0), 25 * 16, 130 * 16)
            painter.drawArc(QRectF(7.8, 8.2, 8.4, 8.4), 32 * 16, 116 * 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            tri = QPainterPath()
            tri.moveTo(12.0, 16.0)
            tri.lineTo(16.8, 21.0)
            tri.lineTo(7.2, 21.0)
            tri.closeSubpath()
            painter.drawPath(tri)

