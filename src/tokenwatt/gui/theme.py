import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase

BG = "#0b0f17"
PANEL = "#111725"
PANEL_HI = "#161e2f"
BORDER = "#1f2a3f"
TEXT = "#e6ebf5"
MUTED = "#8090ab"
FAINT = "#3a4760"
GRID = "#1a2335"

ACCENT = "#ff8a3d"     # power
CPU = "#4cc9f0"
RAM = "#a78bfa"
GPU = "#34d399"
TOKENS = "#5b8cff"
CLAUDE = "#ff8a3d"
GOOD = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"

SERIES = {"cpu": CPU, "ram": RAM, "gpu": GPU, "power": ACCENT, "tokens": TOKENS, "claude": CLAUDE}


def qc(hex_color, alpha=255):
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def level_color(fraction):
    """Green → amber → red as a gauge fills up."""
    f = max(0.0, min(1.0, fraction))
    if f < 0.5:
        return _mix(GOOD, WARN, f / 0.5)
    return _mix(WARN, BAD, (f - 0.5) / 0.5)


def _mix(a, b, t):
    ca, cb = QColor(a), QColor(b)
    return QColor(
        int(ca.red() + (cb.red() - ca.red()) * t),
        int(ca.green() + (cb.green() - ca.green()) * t),
        int(ca.blue() + (cb.blue() - ca.blue()) * t),
    )


def ui_font(size=10, weight=QFont.Normal):
    if sys.platform == "darwin":
        f = QFontDatabase.systemFont(QFontDatabase.GeneralFont)  # San Francisco
    else:
        f = QFont("Segoe UI" if sys.platform == "win32" else "Sans Serif")
    f.setPointSizeF(size if sys.platform == "darwin" else size * 0.8)
    f.setWeight(weight)
    f.setStyleHint(QFont.SansSerif)
    return f


def mono_font(size=10):
    f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    f.setPointSizeF(size if sys.platform == "darwin" else size * 0.8)
    f.setStyleHint(QFont.Monospace)
    return f


STYLESHEET = """
* {{ color: {text}; }}
QMainWindow, #root {{ background: {bg}; }}
QToolTip {{ background: {panel_hi}; color: {text}; border: 1px solid {border}; padding: 6px; }}

#sidebar {{ background: #0d1320; border-right: 1px solid {border}; }}
#brand {{ font-size: 17px; font-weight: 700; padding: 4px 0 0 2px; }}
#brandSub {{ color: {muted}; font-size: 11px; padding-left: 2px; }}
QPushButton#nav {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    color: {muted}; font-size: 13px; background: transparent;
}}
QPushButton#nav:hover {{ background: {panel}; color: {text}; }}
QPushButton#nav:checked {{ background: {panel_hi}; color: {text}; font-weight: 600; }}

#pageTitle {{ font-size: 22px; font-weight: 700; }}
#pageSub {{ color: {muted}; font-size: 12px; }}
#sectionTitle {{ font-size: 13px; font-weight: 600; color: {text}; }}
#muted {{ color: {muted}; font-size: 12px; }}

#card {{ background: {panel}; border: 1px solid {border}; border-radius: 12px; }}

QPushButton#primary {{
    background: {accent}; color: #111; border: none; border-radius: 8px;
    padding: 8px 16px; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #ffa05f; }}
QPushButton#primary[recording="true"] {{ background: {panel_hi}; color: {text}; border: 1px solid {border}; }}
QPushButton#ghost {{
    background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 7px 14px;
}}
QPushButton#ghost:hover {{ background: {panel_hi}; }}
QPushButton:disabled {{ color: {faint}; }}

QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {panel}; border: 1px solid {border}; border-radius: 7px; padding: 5px 9px; min-width: 90px;
}}
QComboBox QAbstractItemView {{ background: {panel_hi}; border: 1px solid {border}; selection-background-color: {border}; }}
QCheckBox {{ spacing: 8px; }}

QTableWidget {{
    background: {panel}; border: 1px solid {border}; border-radius: 10px; gridline-color: transparent;
    alternate-background-color: #0f1522;
}}
QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {grid}; }}
QHeaderView::section {{
    background: {panel}; color: {muted}; border: none; border-bottom: 1px solid {border};
    padding: 8px 6px; font-weight: 600;
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QProgressBar {{ background: {panel}; border: 1px solid {border}; border-radius: 4px; height: 6px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
""".format(bg=BG, panel=PANEL, panel_hi=PANEL_HI, border=BORDER, text=TEXT, muted=MUTED,
           faint=FAINT, grid=GRID, accent=ACCENT)
