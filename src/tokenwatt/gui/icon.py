import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap


def render(size):
    """The app icon: a gauge arc around a lightning bolt, on a dark rounded square."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 1024.0

    bg = QRectF(64 * s, 64 * s, 896 * s, 896 * s)
    g = QLinearGradient(bg.topLeft(), bg.bottomRight())
    g.setColorAt(0, QColor("#18223a"))
    g.setColorAt(1, QColor("#070a12"))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(bg, 200 * s, 200 * s)

    arc = QRectF(212 * s, 212 * s, 600 * s, 600 * s)
    p.setPen(QPen(QColor("#1d2840"), 64 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(arc, 215 * 16, -250 * 16)
    ag = QLinearGradient(arc.bottomLeft(), arc.topRight())
    ag.setColorAt(0, QColor("#4cc9f0"))
    ag.setColorAt(1, QColor("#ff8a3d"))
    p.setPen(QPen(ag, 64 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(arc, 215 * 16, -175 * 16)

    # needle tip dot where the arc ends
    a = math.radians(215 - 175)
    c = arc.center()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(QPointF(c.x() + 300 * s * math.cos(a), c.y() - 300 * s * math.sin(a)), 22 * s, 22 * s)

    bolt = QPainterPath()
    pts = [(548, 300), (400, 540), (500, 540), (462, 730), (628, 470), (526, 470), (566, 300)]
    bolt.moveTo(pts[0][0] * s, pts[0][1] * s)
    for x, y in pts[1:]:
        bolt.lineTo(x * s, y * s)
    bolt.closeSubpath()
    bg2 = QLinearGradient(QPointF(0, 300 * s), QPointF(0, 730 * s))
    bg2.setColorAt(0, QColor("#ffb070"))
    bg2.setColorAt(1, QColor("#ff7a2a"))
    p.setBrush(bg2)
    p.drawPath(bolt)
    p.end()
    return img


def app_icon():
    icon = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        icon.addPixmap(QPixmap.fromImage(render(size)))
    return icon
