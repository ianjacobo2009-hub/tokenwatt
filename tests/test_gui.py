import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tokenwatt import analyze  # noqa: E402
from tokenwatt.gui import widgets  # noqa: E402
from tokenwatt.gui.icon import render  # noqa: E402

from test_analyze import make_data  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_widgets_paint(qapp):
    g = widgets.Gauge("CPU", "%")
    g.resize(220, 236)
    g.set_value(42, "detail")
    g.grab()
    chart = widgets.LiveChart(span_s=120)
    chart.add_series("cpu", "CPU", "#fff")
    chart.add_series("tok", "Tokens", "#00f", axis="right", kind="bars")
    for i in range(30):
        chart.add_point(1000 + i * 2, {"cpu": i}, animate=False)
    chart.set_bars("tok", [(1000, 15, 500)])
    chart.resize(800, 300)
    chart.grab()


def test_icon_renders(qapp):
    img = render(64)
    assert img.width() == 64 and not img.isNull()


def test_main_window_runs_analysis(qapp, tmp_path):
    from PySide6.QtCore import QThreadPool
    from tokenwatt.gui.app import MainWindow

    win = MainWindow(db_path=tmp_path / "gui.db")
    samples, toks = make_data(minutes=30)
    res = analyze.run(samples, toks)
    res["specs"] = {}
    win.analysis._done(res)
    assert win.analysis.scatter.points
    assert win.analysis.heat.cells
    win.grab()
    win.quit()
    QThreadPool.globalInstance().waitForDone(3000)
