import math
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from tokenwatt.gui import theme as T


def nice_max(v):
    if not v or v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= v:
            return m * exp
    return 10 * exp


def short_num(v, decimals=0):
    if v is None:
        return "–"
    a = abs(v)
    if a >= 1e9:
        return "{:.1f}B".format(v / 1e9)
    if a >= 1e6:
        return "{:.1f}M".format(v / 1e6)
    if a >= 1e4:
        return "{:.1f}k".format(v / 1e3)
    return "{:,.{d}f}".format(v, d=decimals)


def anim(parent, start, end, ms, on_value, curve=QEasingCurve.OutCubic):
    a = QVariantAnimation(parent)
    a.setStartValue(float(start))
    a.setEndValue(float(end))
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.valueChanged.connect(on_value)
    return a


def draw_tooltip(p, bounds, anchor, title, rows):
    """rows: list of (color or None, label, value)."""
    f_title = T.ui_font(10, QFont.DemiBold)
    f_row = T.ui_font(10)
    fm_t, fm = QFontMetricsF(f_title), QFontMetricsF(f_row)
    line_h = fm.height() + 3
    width = fm_t.horizontalAdvance(title)
    for _, label, value in rows:
        width = max(width, 18 + fm.horizontalAdvance(label) + 18 + fm.horizontalAdvance(value))
    w = width + 22
    h = fm_t.height() + 10 + line_h * len(rows) + 8
    x = anchor.x() + 14
    if x + w > bounds.right():
        x = anchor.x() - 14 - w
    y = max(bounds.top(), min(anchor.y() - h / 2, bounds.bottom() - h))
    r = QRectF(x, y, w, h)
    p.setPen(QPen(T.qc(T.BORDER), 1))
    p.setBrush(T.qc("#0e1424", 240))
    p.drawRoundedRect(r, 8, 8)
    p.setFont(f_title)
    p.setPen(T.qc(T.TEXT))
    p.drawText(QPointF(x + 11, y + 8 + fm_t.ascent()), title)
    p.setFont(f_row)
    yy = y + 10 + fm_t.height()
    for color, label, value in rows:
        if color is not None:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x + 11, yy + fm.ascent() / 2 - 1, 9, 9), 2, 2)
        p.setPen(T.qc(T.MUTED))
        p.drawText(QPointF(x + 29, yy + fm.ascent() + 2), label)
        p.setPen(T.qc(T.TEXT))
        p.drawText(QPointF(r.right() - 11 - fm.horizontalAdvance(value), yy + fm.ascent() + 2), value)
        yy += line_h


class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(8)


class CountLabel(QLabel):
    """A number that rolls to its new value instead of jumping."""

    def __init__(self, fmt=lambda v: short_num(v), parent=None):
        super().__init__("–", parent)
        self._fmt = fmt
        self._value = None
        self._anim = None

    def set_value(self, v):
        if v is None:
            self._value = None
            self.setText("–")
            return
        start = self._value if self._value is not None else 0.0
        self._value = float(v)
        if self._anim:
            self._anim.stop()
        self._anim = anim(self, start, v, 700, lambda x: self.setText(self._fmt(x)))
        self._anim.start()


class StatCard(Card):
    def __init__(self, title, fmt=lambda v: short_num(v), accent=T.ACCENT, parent=None):
        super().__init__(parent)
        self.lay.setSpacing(2)
        t = QLabel(title.upper())
        t.setStyleSheet("color:{}; font-size:10px; font-weight:600; letter-spacing:1px;".format(T.MUTED))
        self.value = CountLabel(fmt)
        self.value.setStyleSheet("font-size:24px; font-weight:700;")
        self.caption = QLabel("")
        self.caption.setStyleSheet("color:{}; font-size:11px;".format(T.MUTED))
        bar = QFrame()
        bar.setFixedHeight(3)
        bar.setFixedWidth(28)
        bar.setStyleSheet("background:{}; border-radius:1px;".format(accent))
        self.lay.addWidget(bar)
        self.lay.addSpacing(6)
        self.lay.addWidget(t)
        self.lay.addWidget(self.value)
        self.lay.addWidget(self.caption)

    def set(self, value, caption=None):
        self.value.set_value(value)
        if caption is not None:
            self.caption.setText(caption)


class PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self._active = False
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1400)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v):
        self._phase = v
        self.update()

    def set_active(self, on):
        self._active = on
        if on:
            self._anim.start()
        else:
            self._anim.stop()
            self._phase = 0.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(9, 9)
        color = QColor(T.BAD) if self._active else QColor(T.FAINT)
        if self._active:
            ring = QColor(color)
            ring.setAlphaF(0.55 * (1 - self._phase))
            p.setPen(Qt.NoPen)
            p.setBrush(ring)
            r = 4 + 5 * self._phase
            p.drawEllipse(c, r, r)
        p.setBrush(color)
        p.drawEllipse(c, 4, 4)


class Gauge(QWidget):
    """Arc gauge. The arc eases to each new reading and colours itself by load."""

    SWEEP = 250.0

    def __init__(self, title, unit, max_value=100.0, decimals=0, fixed_color=None, parent=None):
        super().__init__(parent)
        self.title, self.unit, self.max_value, self.decimals = title, unit, max_value, decimals
        self.fixed_color = fixed_color
        self.sub = ""
        self._shown = 0.0
        self._target = None
        self._recent = deque(maxlen=60)
        self._anim = None
        self.setMinimumWidth(180)
        self.setFixedHeight(236)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(220, 236)

    def set_value(self, v, sub=None):
        if sub is not None:
            self.sub = sub
        if v is None:
            self._target = None
            self.update()
            return
        self._recent.append(v)
        if v > self.max_value:
            self.max_value = nice_max(v)
        start = self._shown
        self._target = v
        if self._anim:
            self._anim.stop()
        self._anim = anim(self, start, v, 650, self._step)
        self._anim.start()

    def _step(self, v):
        self._shown = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # leave room under the arc's opening for the title and the detail line
        side = min(self.width() - 28, self.height() - 40)
        rect = QRectF((self.width() - side) / 2, 16, side, side)
        start_angle = 90 + self.SWEEP / 2  # Qt angles: counter-clockwise from 3 o'clock

        track = QPen(T.qc(T.GRID), 12, Qt.SolidLine, Qt.RoundCap)
        p.setPen(track)
        p.drawArc(rect, int(start_angle * 16), int(-self.SWEEP * 16))

        # tick marks
        p.setPen(QPen(T.qc(T.FAINT), 1.2))
        cx, cy, rad = rect.center().x(), rect.center().y(), side / 2
        for i in range(11):
            a = math.radians(start_angle - self.SWEEP * i / 10)
            r1, r2 = rad - 13, rad - (19 if i % 5 == 0 else 16)
            p.drawLine(QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)),
                       QPointF(cx + r2 * math.cos(a), cy - r2 * math.sin(a)))

        frac = 0.0 if self._target is None else max(0.0, min(1.0, self._shown / self.max_value))
        color = QColor(self.fixed_color) if self.fixed_color else T.level_color(frac)
        if frac > 0.002:
            glow = QColor(color)
            glow.setAlpha(45)
            p.setPen(QPen(glow, 22, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, int(start_angle * 16), int(-self.SWEEP * frac * 16))
            p.setPen(QPen(color, 12, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, int(start_angle * 16), int(-self.SWEEP * frac * 16))

        # peak over the last minute or so
        if self._recent and self._target is not None:
            pk = max(0.0, min(1.0, max(self._recent) / self.max_value))
            a = math.radians(start_angle - self.SWEEP * pk)
            p.setPen(QPen(T.qc(T.TEXT, 180), 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + (rad + 9) * math.cos(a), cy - (rad + 9) * math.sin(a)),
                       QPointF(cx + (rad + 3) * math.cos(a), cy - (rad + 3) * math.sin(a)))

        p.setPen(T.qc(T.TEXT))
        p.setFont(T.ui_font(25, QFont.Bold))
        txt = "–" if self._target is None else "{:,.{d}f}".format(self._shown, d=self.decimals)
        fm = QFontMetricsF(p.font())
        p.drawText(QPointF(cx - fm.horizontalAdvance(txt) / 2, cy + fm.ascent() / 3), txt)
        p.setFont(T.ui_font(10))
        p.setPen(T.qc(T.MUTED))
        fm2 = QFontMetricsF(p.font())
        p.drawText(QPointF(cx - fm2.horizontalAdvance(self.unit) / 2, cy + fm.ascent() / 3 + fm2.height() + 2),
                   self.unit)

        p.setFont(T.ui_font(11, QFont.DemiBold))
        p.setPen(T.qc(T.TEXT))
        fm3 = QFontMetricsF(p.font())
        by = rect.bottom() - 4
        p.drawText(QPointF(cx - fm3.horizontalAdvance(self.title) / 2, by), self.title)
        if self.sub:
            p.setFont(T.ui_font(9.5))
            p.setPen(T.qc(T.MUTED))
            fm4 = QFontMetricsF(p.font())
            sub = fm4.elidedText(self.sub, Qt.ElideRight, self.width() - 10)
            p.drawText(QPointF(cx - fm4.horizontalAdvance(sub) / 2, by + fm4.height() + 2), sub)


class LiveChart(QWidget):
    """Scrolling time chart. Every reading is drawn as a point you can hover."""

    M_L, M_R, M_T, M_B = 52, 58, 40, 30

    def __init__(self, span_s=600, left_unit="%", left_max=100.0, parent=None):
        super().__init__(parent)
        self.span_s = span_s
        self.left_unit = left_unit
        self.left_max = left_max
        self.series = []
        self.data = {}
        self.bars = {}
        self._now = time.time()
        self._scroll = None
        self._hover = None
        self._birth = {}
        self._legend_hits = []
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_series(self, key, label, color, axis="left", kind="line", unit="", decimals=1):
        self.series.append({"key": key, "label": label, "color": color, "axis": axis, "kind": kind,
                            "unit": unit, "decimals": decimals, "visible": True})
        self.data[key] = deque(maxlen=20000)

    def clear(self):
        for d in self.data.values():
            d.clear()
        self.update()

    def add_point(self, ts, values, animate=True):
        for key, v in values.items():
            if key in self.data and v is not None:
                self.data[key].append((ts, float(v)))
                self._birth[key] = (ts, time.time())
        if animate:
            start = self._now
            if self._scroll:
                self._scroll.stop()
            self._scroll = anim(self, start, max(ts, start), 550, self._set_now, QEasingCurve.InOutCubic)
            self._scroll.start()
        else:
            self._now = max(self._now, ts)
            self.update()

    def set_bars(self, key, rows):
        """rows: list of (start_ts, width_s, value)."""
        self.data[key] = deque(((t + w / 2, v) for t, w, v in rows), maxlen=20000)
        self.bars[key] = rows
        self.update()

    def _set_now(self, v):
        self._now = v
        self.update()

    def _plot_rect(self):
        return QRectF(self.M_L, self.M_T, self.width() - self.M_L - self.M_R,
                      self.height() - self.M_T - self.M_B)

    def _visible(self, key, t0, t1):
        return [(t, v) for t, v in self.data[key] if t0 - 30 <= t <= t1 + 5]

    def _axis_max(self, axis, t0, t1):
        if axis == "left" and self.left_max:
            mx = self.left_max
            for s in self.series:
                if s["axis"] == "left" and s["visible"]:
                    for _, v in self._visible(s["key"], t0, t1):
                        mx = max(mx, v)
            return nice_max(mx)
        mx = 0.0
        for s in self.series:
            if s["axis"] == axis and s["visible"]:
                for _, v in self._visible(s["key"], t0, t1):
                    mx = max(mx, v)
        return nice_max(mx * 1.12) if mx else 1.0

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        for rect, s in self._legend_hits:
            if rect.contains(e.position()):
                s["visible"] = not s["visible"]
                self.update()
                return

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pr = self._plot_rect()
        t1 = self._now
        t0 = t1 - self.span_s
        lmax = self._axis_max("left", t0, t1)
        has_right = any(s["axis"] == "right" for s in self.series)
        rmax = self._axis_max("right", t0, t1) if has_right else 1.0

        def X(t):
            return pr.left() + (t - t0) / self.span_s * pr.width()

        def Y(v, axis):
            m = lmax if axis == "left" else rmax
            return pr.bottom() - (v / m) * pr.height()

        # grid + axes
        p.setFont(T.ui_font(9))
        fm = QFontMetricsF(p.font())
        for i in range(5):
            y = pr.bottom() - pr.height() * i / 4
            p.setPen(QPen(T.qc(T.GRID), 1))
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y))
            p.setPen(T.qc(T.MUTED))
            lbl = short_num(lmax * i / 4, 0 if lmax >= 10 else 1)
            if i == 4:
                lbl += " " + self.left_unit
            p.drawText(QPointF(pr.left() - 8 - fm.horizontalAdvance(lbl), y + fm.ascent() / 2 - 1), lbl)
            if has_right:
                rl = short_num(rmax * i / 4, 0 if rmax >= 10 else 1)
                p.drawText(QPointF(pr.right() + 8, y + fm.ascent() / 2 - 1), rl)
        step = 60 if self.span_s <= 900 else 300 if self.span_s <= 3600 else 1800
        t = math.ceil(t0 / step) * step
        while t <= t1:
            x = X(t)
            p.setPen(QPen(T.qc(T.GRID), 1, Qt.DotLine))
            p.drawLine(QPointF(x, pr.top()), QPointF(x, pr.bottom()))
            lbl = datetime.fromtimestamp(t).strftime("%H:%M")
            p.setPen(T.qc(T.MUTED))
            p.drawText(QPointF(x - fm.horizontalAdvance(lbl) / 2, pr.bottom() + fm.height() + 4), lbl)
            t += step

        p.save()
        p.setClipRect(pr.adjusted(-6, -6, 6, 2))

        # bars first so lines sit on top
        for s in self.series:
            if s["kind"] != "bars" or not s["visible"]:
                continue
            col = QColor(s["color"])
            for start, width, v in self.bars.get(s["key"], []):
                if start + width < t0 or start > t1 or v <= 0:
                    continue
                x0, x1 = X(start), X(start + width)
                y = Y(v, s["axis"])
                r = QRectF(x0 + 1, y, max(1.5, x1 - x0 - 2), pr.bottom() - y)
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                c1, c2 = QColor(col), QColor(col)
                c1.setAlpha(210)
                c2.setAlpha(70)
                g.setColorAt(0, c1)
                g.setColorAt(1, c2)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(g))
                p.drawRoundedRect(r, 2, 2)

        first_line = True
        now_wall = time.time()
        for s in self.series:
            if s["kind"] != "line" or not s["visible"]:
                continue
            pts = self._visible(s["key"], t0, t1)
            if not pts:
                continue
            col = QColor(s["color"])
            path = QPainterPath()
            prev_t = None
            segs = []
            for tt, v in pts:
                pt = QPointF(X(tt), Y(v, s["axis"]))
                if prev_t is None or tt - prev_t > 30:  # recorder was off: break the line
                    path.moveTo(pt)
                    segs.append([pt])
                else:
                    path.lineTo(pt)
                    segs[-1].append(pt)
                prev_t = tt
            if first_line:
                for seg in segs:
                    if len(seg) < 2:
                        continue
                    area = QPainterPath(seg[0])
                    for q in seg[1:]:
                        area.lineTo(q)
                    area.lineTo(seg[-1].x(), pr.bottom())
                    area.lineTo(seg[0].x(), pr.bottom())
                    area.closeSubpath()
                    g = QLinearGradient(0, pr.top(), 0, pr.bottom())
                    c1, c2 = QColor(col), QColor(col)
                    c1.setAlpha(70)
                    c2.setAlpha(0)
                    g.setColorAt(0, c1)
                    g.setColorAt(1, c2)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QBrush(g))
                    p.drawPath(area)
                first_line = False
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            # every reading as a point; the newest one pops in
            p.setPen(Qt.NoPen)
            birth = self._birth.get(s["key"])
            for i, (tt, v) in enumerate(pts):
                r = 2.3
                if birth and tt == birth[0]:
                    age = min(1.0, (now_wall - birth[1]) / 0.5)
                    r = 2.3 + 3.0 * (1 - age)
                    halo = QColor(col)
                    halo.setAlpha(int(90 * (1 - age)))
                    p.setBrush(halo)
                    p.drawEllipse(QPointF(X(tt), Y(v, s["axis"])), r + 4, r + 4)
                p.setBrush(col)
                p.drawEllipse(QPointF(X(tt), Y(v, s["axis"])), r, r)
        p.restore()

        self._draw_legend(p)
        if self._hover and pr.contains(self._hover):
            self._draw_hover(p, pr, X, Y, t0, t1)

    def _draw_legend(self, p):
        p.setFont(T.ui_font(10))
        fm = QFontMetricsF(p.font())
        x = self.M_L
        self._legend_hits = []
        for s in self.series:
            label = s["label"]
            w = 22 + fm.horizontalAdvance(label) + 10
            r = QRectF(x, 6, w, 22)
            p.setPen(QPen(T.qc(T.BORDER), 1))
            p.setBrush(T.qc(T.PANEL_HI) if s["visible"] else Qt.NoBrush)
            p.drawRoundedRect(r, 11, 11)
            c = QColor(s["color"])
            if not s["visible"]:
                c.setAlpha(70)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(x + 12, 17), 4, 4)
            p.setPen(T.qc(T.TEXT if s["visible"] else T.FAINT))
            p.drawText(QPointF(x + 22, 17 + fm.ascent() / 2 - 1), label)
            self._legend_hits.append((r, s))
            x += w + 6

    def _draw_hover(self, p, pr, X, Y, t0, t1):
        hx = self._hover.x()
        t_at = t0 + (hx - pr.left()) / pr.width() * self.span_s
        best = None
        for s in self.series:
            if not s["visible"]:
                continue
            for tt, _ in self._visible(s["key"], t0, t1):
                d = abs(tt - t_at)
                if best is None or d < best[0]:
                    best = (d, tt)
        if best is None or abs(X(best[1]) - hx) > 40:
            return
        tt = best[1]
        x = X(tt)
        p.setPen(QPen(T.qc(T.MUTED, 150), 1, Qt.DashLine))
        p.drawLine(QPointF(x, pr.top()), QPointF(x, pr.bottom()))
        rows = []
        ys = []
        for s in self.series:
            if not s["visible"]:
                continue
            near = [(abs(t - tt), t, v) for t, v in self._visible(s["key"], t0, t1)]
            if not near:
                continue
            d, t, v = min(near)
            limit = 8 if s["kind"] == "line" else 30
            if d > limit:
                continue
            col = QColor(s["color"])
            if s["kind"] == "line":
                pt = QPointF(X(t), Y(v, s["axis"]))
                ys.append(pt.y())
                p.setPen(QPen(T.qc(T.BG), 2))
                p.setBrush(col)
                p.drawEllipse(pt, 5, 5)
            rows.append((s["color"], s["label"],
                         "{:,.{d}f}{}".format(v, s["unit"] and " " + s["unit"] or "", d=s["decimals"])))
        if rows:
            anchor = QPointF(x, sum(ys) / len(ys) if ys else pr.center().y())
            title = datetime.fromtimestamp(tt).strftime("%H:%M:%S")
            draw_tooltip(p, QRectF(self.rect()), anchor, title, rows)


class ScatterChart(QWidget):
    """Every analysis window as a point. Hover for the details."""

    M_L, M_R, M_T, M_B = 60, 20, 18, 42

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = []
        self.fit = None
        self.x_label = "x"
        self.y_label = "y"
        self._progress = 1.0
        self._hover = None
        self._anim = None
        self.setMouseTracking(True)
        self.setMinimumHeight(300)

    def set_data(self, points, x_label, y_label, fit=None):
        """points: list of dicts with x, y, title, rows."""
        self.points, self.x_label, self.y_label, self.fit = points, x_label, y_label, fit
        if self._anim:
            self._anim.stop()
        self._anim = anim(self, 0.0, 1.0, 1100, self._set_progress, QEasingCurve.OutCubic)
        self._anim.start()

    def _set_progress(self, v):
        self._progress = v
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pr = QRectF(self.M_L, self.M_T, self.width() - self.M_L - self.M_R,
                    self.height() - self.M_T - self.M_B)
        p.setFont(T.ui_font(9))
        fm = QFontMetricsF(p.font())
        if not self.points:
            p.setPen(T.qc(T.MUTED))
            p.drawText(pr, Qt.AlignCenter, "Run an analysis to see each window here.")
            return
        xs = [pt["x"] for pt in self.points]
        ys = [pt["y"] for pt in self.points]
        xmax = nice_max(max(xs) * 1.05) if max(xs) > 0 else 1.0
        ylo = min(ys)
        ylo = max(0.0, math.floor(ylo * 0.9))
        yhi = nice_max(max(ys) * 1.04)
        if yhi <= ylo:
            yhi = ylo + 1

        def X(v):
            return pr.left() + v / xmax * pr.width()

        def Y(v):
            return pr.bottom() - (v - ylo) / (yhi - ylo) * pr.height()

        for i in range(5):
            y = pr.bottom() - pr.height() * i / 4
            p.setPen(QPen(T.qc(T.GRID), 1))
            p.drawLine(QPointF(pr.left(), y), QPointF(pr.right(), y))
            lbl = short_num(ylo + (yhi - ylo) * i / 4, 1 if yhi - ylo < 10 else 0)
            p.setPen(T.qc(T.MUTED))
            p.drawText(QPointF(pr.left() - 8 - fm.horizontalAdvance(lbl), y + fm.ascent() / 2 - 1), lbl)
            xv = xmax * i / 4
            xl = short_num(xv)
            x = X(xv)
            p.drawText(QPointF(x - fm.horizontalAdvance(xl) / 2, pr.bottom() + fm.height() + 4), xl)
        p.setPen(T.qc(T.MUTED))
        p.drawText(QPointF(pr.center().x() - fm.horizontalAdvance(self.x_label) / 2, self.height() - 6),
                   self.x_label)
        p.save()
        p.translate(14, pr.center().y() + fm.horizontalAdvance(self.y_label) / 2)
        p.rotate(-90)
        p.drawText(QPointF(0, 0), self.y_label)
        p.restore()

        if self.fit and self.fit[1] == self.fit[1] and self._progress > 0.6:
            a, b = self.fit
            alpha = int(255 * min(1.0, (self._progress - 0.6) / 0.4))
            p.save()
            p.setClipRect(pr)
            p.setPen(QPen(T.qc(T.TEXT, int(alpha * 0.8)), 1.6, Qt.DashLine))
            p.drawLine(QPointF(X(0), Y(a)), QPointF(X(xmax), Y(a + b * xmax)))
            p.restore()

        n = len(self.points)
        hover_i, best = None, 14.0
        for i, pt in enumerate(self.points):
            appear = (self._progress * 1.4 - (i / max(1, n)) * 0.4)
            k = max(0.0, min(1.0, appear))
            if k <= 0:
                continue
            c = QColor(T.TOKENS if pt["x"] > 0 else T.FAINT)
            c.setAlpha(int(200 * k))
            pos = QPointF(X(pt["x"]), Y(pt["y"]))
            p.setPen(QPen(T.qc(T.BG, int(160 * k)), 1))
            p.setBrush(c)
            p.drawEllipse(pos, 3.6 * k, 3.6 * k)
            if self._hover is not None:
                d = math.hypot(pos.x() - self._hover.x(), pos.y() - self._hover.y())
                if d < best:
                    best, hover_i = d, i
        if hover_i is not None:
            pt = self.points[hover_i]
            pos = QPointF(X(pt["x"]), Y(pt["y"]))
            p.setPen(QPen(T.qc(T.TEXT), 2))
            p.setBrush(T.qc(T.ACCENT))
            p.drawEllipse(pos, 6, 6)
            draw_tooltip(p, QRectF(self.rect()), pos, pt["title"], pt["rows"])


class Heatmap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows, self.cols, self.cells = [], [], {}
        self._progress = 1.0
        self._hover = None
        self._anim = None
        self.setMouseTracking(True)
        self.setMinimumHeight(250)

    def set_data(self, rows, cols, cells):
        """cells: {(row_key, col_key): {"value": float|None, "title": str, "rows": [...]}}"""
        self.rows, self.cols, self.cells = rows, cols, cells
        if self._anim:
            self._anim.stop()
        self._anim = anim(self, 0.0, 1.0, 900, self._set, QEasingCurve.OutCubic)
        self._anim.start()

    def _set(self, v):
        self._progress = v
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.rows:
            p.setPen(T.qc(T.MUTED))
            p.setFont(T.ui_font(10))
            p.drawText(self.rect(), Qt.AlignCenter, "No correlations yet.")
            return
        p.setFont(T.ui_font(10))
        fm = QFontMetricsF(p.font())
        left = max(fm.horizontalAdvance(lbl) for _, lbl in self.rows) + 18
        top = 30
        cw = (self.width() - left - 4) / len(self.cols)
        ch = min(54.0, (self.height() - top - 6) / len(self.rows))
        for j, (_, lbl) in enumerate(self.cols):
            p.setPen(T.qc(T.MUTED))
            el = fm.elidedText(lbl, Qt.ElideRight, cw - 6)
            p.drawText(QPointF(left + cw * j + (cw - fm.horizontalAdvance(el)) / 2, top - 10), el)
        hovered = None
        for i, (rk, rl) in enumerate(self.rows):
            y = top + ch * i
            p.setPen(T.qc(T.TEXT))
            p.drawText(QPointF(0, y + ch / 2 + fm.ascent() / 2 - 2), rl)
            for j, (ck, _) in enumerate(self.cols):
                cell = self.cells.get((rk, ck), {})
                v = cell.get("value")
                r = QRectF(left + cw * j + 2, y + 2, cw - 4, ch - 4)
                delay = (i * len(self.cols) + j) / (len(self.rows) * len(self.cols))
                k = max(0.0, min(1.0, self._progress * 1.6 - delay * 0.6))
                if v is None:
                    p.setPen(QPen(T.qc(T.GRID), 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(r, 7, 7)
                    p.setPen(T.qc(T.FAINT))
                    p.drawText(r, Qt.AlignCenter, "–")
                    continue
                base = QColor(T.ACCENT if v >= 0 else T.TOKENS)
                base.setAlpha(int((35 + 200 * min(1.0, abs(v))) * k))
                p.setPen(Qt.NoPen)
                p.setBrush(base)
                p.drawRoundedRect(r, 7, 7)
                p.setPen(T.qc(T.TEXT, int(255 * k)))
                p.setFont(T.ui_font(11, QFont.DemiBold))
                p.drawText(r, Qt.AlignCenter, "{:+.2f}".format(v * k))
                p.setFont(T.ui_font(10))
                if self._hover is not None and r.contains(self._hover):
                    hovered = (r, cell)
        if hovered:
            r, cell = hovered
            p.setPen(QPen(T.qc(T.TEXT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 7, 7)
            draw_tooltip(p, QRectF(self.rect()), r.center(), cell.get("title", ""), cell.get("rows", []))


class CompareBars(QWidget):
    """Side-by-side bars: with Claude active vs without."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self._progress = 1.0
        self._anim = None
        self.setMinimumHeight(220)

    def set_rows(self, rows):
        """rows: list of (label, active, idle, unit, p)."""
        self.rows = rows
        self.setMinimumHeight(30 + 44 * len(rows))
        if self._anim:
            self._anim.stop()
        self._anim = anim(self, 0.0, 1.0, 900, self._set)
        self._anim.start()

    def _set(self, v):
        self._progress = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(T.ui_font(10))
        fm = QFontMetricsF(p.font())
        if not self.rows:
            p.setPen(T.qc(T.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "No data yet.")
            return
        label_w = max(fm.horizontalAdvance(r[0]) for r in self.rows) + 16
        value_w = 150
        bar_w = self.width() - label_w - value_w
        # legend
        for i, (txt, col) in enumerate((("With Claude", T.ACCENT), ("Without", T.FAINT))):
            x = label_w + i * 120
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(x, 6, 10, 10), 2, 2)
            p.setPen(T.qc(T.MUTED))
            p.drawText(QPointF(x + 16, 15), txt)
        y = 30
        for label, active, idle, unit, pval in self.rows:
            mx = max(v for v in (active, idle, 1e-9) if v is not None)
            p.setPen(T.qc(T.TEXT))
            p.drawText(QPointF(0, y + 17), label)
            for k, (v, col) in enumerate(((active, T.ACCENT), (idle, T.FAINT))):
                r = QRectF(label_w, y + k * 15, bar_w, 11)
                p.setPen(Qt.NoPen)
                p.setBrush(T.qc(T.GRID))
                p.drawRoundedRect(r, 4, 4)
                if v is not None:
                    w = bar_w * (v / mx) * self._progress
                    p.setBrush(QColor(col))
                    p.drawRoundedRect(QRectF(r.left(), r.top(), max(4.0, w), r.height()), 4, 4)
            a_txt = "–" if active is None else "{:,.1f}".format(active)
            i_txt = "–" if idle is None else "{:,.1f}".format(idle)
            sig = "" if pval is None else ("p < 0.001" if pval < 0.001 else "p = {:.3f}".format(pval))
            p.setPen(T.qc(T.TEXT))
            p.drawText(QPointF(label_w + bar_w + 12, y + 11), "{} vs {} {}".format(a_txt, i_txt, unit))
            p.setPen(T.qc(T.MUTED))
            p.drawText(QPointF(label_w + bar_w + 12, y + 26), sig.strip())
            y += 44
