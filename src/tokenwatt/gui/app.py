import csv
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMetaObject, QSettings, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QStackedWidget,
                               QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from tokenwatt import __version__, report, stats, store
from tokenwatt.gui import theme as T
from tokenwatt.gui.icon import app_icon
from tokenwatt.gui.widgets import (Card, CompareBars, Gauge, Heatmap, LiveChart, PulseDot,
                                   ScatterChart, StatCard, nice_max, short_num)
from tokenwatt.gui.workers import AnalysisJob, LiveWorker

BAR_S = 15  # live token bars are 15-second buckets


def page_header(title, sub):
    box = QVBoxLayout()
    box.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    s = QLabel(sub)
    s.setObjectName("pageSub")
    s.setWordWrap(True)
    box.addWidget(t)
    box.addWidget(s)
    return box, s


def section(title, widget, extra=None):
    card = Card()
    head = QHBoxLayout()
    lbl = QLabel(title)
    lbl.setObjectName("sectionTitle")
    head.addWidget(lbl)
    head.addStretch(1)
    if extra is not None:
        head.addWidget(extra)
    card.lay.addLayout(head)
    card.lay.addWidget(widget)
    return card


def scroll_page(inner):
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setWidget(inner)
    inner.setObjectName("root")
    return sa


def make_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.setFocusPolicy(Qt.NoFocus)
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return t


def fill_table(t, rows, right_from=1):
    for j in range(t.columnCount()):
        h = t.horizontalHeaderItem(j)
        if h is not None:
            h.setTextAlignment((Qt.AlignRight if j >= right_from else Qt.AlignLeft) | Qt.AlignVCenter)
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            it = QTableWidgetItem(str(v))
            if j >= right_from:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            t.setItem(i, j, it)
    t.resizeRowsToContents()
    body = sum(t.rowHeight(i) for i in range(t.rowCount())) or 34
    t.setFixedHeight(t.horizontalHeader().sizeHint().height() + body + 6)


# ---------------------------------------------------------------- Live

class LivePage(QWidget):
    def __init__(self):
        super().__init__()
        self.specs = {}
        self.energy_wh = 0.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(16)
        head, self.sub = page_header("Live", "Starting sensors…")
        top = QHBoxLayout()
        top.addLayout(head, 1)
        self.span = QComboBox()
        for label, secs in (("Last 2 min", 120), ("Last 5 min", 300), ("Last 10 min", 600), ("Last 30 min", 1800)):
            self.span.addItem(label, secs)
        self.span.setCurrentIndex(1)
        self.span.currentIndexChanged.connect(self._span_changed)
        top.addWidget(self.span, 0, Qt.AlignTop)
        lay.addLayout(top)

        gauges = Card()
        g = QHBoxLayout()
        g.setSpacing(4)
        self.g_cpu = Gauge("CPU", "%")
        self.g_ram = Gauge("Memory", "%")
        self.g_gpu = Gauge("GPU", "%")
        self.g_pow = Gauge("Power", "W", max_value=40, decimals=1)
        for w in (self.g_cpu, self.g_ram, self.g_gpu, self.g_pow):
            g.addWidget(w)
        gauges.lay.addLayout(g)
        lay.addWidget(gauges)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.c_claude = StatCard("Claude apps CPU", lambda v: "{:.1f}%".format(v), T.CLAUDE)
        self.c_rate = StatCard("Output tokens / min", lambda v: short_num(v), T.TOKENS)
        self.c_today = StatCard("Output tokens today", lambda v: short_num(v), T.TOKENS)
        self.c_req = StatCard("Requests today", lambda v: short_num(v), T.RAM)
        self.c_energy = StatCard("Energy this recording", lambda v: "{:.2f} Wh".format(v), T.ACCENT)
        for i, c in enumerate((self.c_claude, self.c_rate, self.c_today, self.c_req, self.c_energy)):
            cards.addWidget(c, 0, i)
        lay.addLayout(cards)

        self.load = LiveChart(span_s=300, left_unit="%", left_max=100)
        self.load.add_series("cpu", "CPU", T.CPU, unit="%")
        self.load.add_series("ram", "Memory", T.RAM, unit="%")
        self.load.add_series("gpu", "GPU", T.GPU, unit="%")
        self.load.add_series("claude", "Claude apps", T.CLAUDE, unit="%", decimals=2)
        self.load.setMinimumHeight(270)
        lay.addWidget(section("System load", self.load))

        self.power = LiveChart(span_s=300, left_unit="W", left_max=None)
        self.power.add_series("power", "Power", T.ACCENT, unit="W", decimals=2)
        self.power.add_series("tokens", "Output tokens (15 s)", T.TOKENS, axis="right", kind="bars", decimals=0)
        self.power.setMinimumHeight(270)
        lay.addWidget(section("Power and Claude tokens", self.power))

        self.requests = make_table(["Time", "Project", "Model", "Input", "Output", "Cache read"])
        lay.addWidget(section("Latest Claude requests", self.requests))
        fill_table(self.requests, [])

    def _span_changed(self):
        secs = self.span.currentData()
        self.load.span_s = secs
        self.power.span_s = secs
        self.load.update()
        self.power.update()

    def set_specs(self, info):
        self.specs = info
        tdp = info.get("cpu_tdp_w") or 35
        self.g_pow.max_value = nice_max(tdp * 1.25 + (5 if info.get("has_battery") else 25))
        gpus = info.get("gpus") or []
        if gpus:
            self.g_gpu.sub = gpus[0]["name"]
        self.g_ram.sub = "of {} GB".format(info.get("ram_total_gb", "?"))

    def preload(self, samples):
        for s in samples:
            self._push(s, animate=False)

    def _push(self, s, animate=True):
        ts = s["ts"]
        self.load.add_point(ts, {"cpu": s["cpu_pct"], "ram": s["ram_pct"], "gpu": s["gpu_pct"],
                                 "claude": s["claude_cpu"]}, animate)
        w = s["watts"] if s["watts"] is not None else s["watts_est"]
        self.power.add_point(ts, {"power": w}, animate)

    def on_sample(self, s):
        self._push(s)
        cores = self.specs.get("cores_logical")
        freq = "{:.2f} GHz".format(s["freq_mhz"] / 1000) if s.get("freq_mhz") else ""
        self.g_cpu.set_value(s["cpu_pct"], " · ".join(x for x in (freq, cores and "{} threads".format(cores)) if x))
        total = self.specs.get("ram_total_gb")
        # psutil's "used" leaves out compressed/wired memory on macOS; derive it from the % so the two agree
        detail = "{:.1f} of {} GB".format(s["ram_pct"] / 100 * total, total) if total else ""
        self.g_ram.set_value(s["ram_pct"], detail)
        self.g_gpu.set_value(s["gpu_pct"], None if s["gpu_pct"] is not None else "not available")
        if s["watts"] is not None:
            self.g_pow.set_value(s["watts"], "measured · {}".format(s["watts_src"]))
        else:
            self.g_pow.set_value(s["watts_est"], "estimated from load")
        self.c_claude.set(s["claude_cpu"] or 0.0, "share of the whole machine")
        if s["recording"]:
            w = s["watts"] if s["watts"] is not None else s["watts_est"]
            self.energy_wh += w * s["interval_s"] / 3600
            self.c_energy.set(self.energy_wh, "{:,} samples saved".format(s["saved"]))
        src = "measured from the battery" if s["watts"] is not None else "estimated from CPU and GPU load"
        self.sub.setText("Sampling every {:.0f} s · power {}".format(round(s["interval_s"]) or 1, src))

    def reset_energy(self):
        self.energy_wh = 0.0
        self.c_energy.set(0.0, "recording…")

    def on_tokens(self, d):
        now = time.time()
        recent = d["recent"]
        buckets = defaultdict(int)
        for t in recent:
            buckets[int(t["ts"] // BAR_S) * BAR_S] += t["output"]
        self.power.set_bars("tokens", [(b, BAR_S, v) for b, v in sorted(buckets.items())])
        last5 = sum(t["output"] for t in recent if t["ts"] >= now - 300)
        self.c_rate.set(last5 / 5.0, "average over the last 5 min")
        self.c_today.set(d["today_output"], "{} input+cache write".format(short_num(d["today_input"])))
        self.c_req.set(d["today_requests"], "{:,} in the database".format(d["all_requests"]))
        rows = []
        for t in sorted(recent, key=lambda r: -r["ts"])[:8]:
            proj = t["project"].replace("-Users-", "").split("-")[-1] if t["project"] else "–"
            rows.append((datetime.fromtimestamp(t["ts"]).strftime("%H:%M:%S"), proj,
                         t["model"] or "–", "{:,}".format(t["input"] + t["cache_write"]),
                         "{:,}".format(t["output"]), "{:,}".format(t["cache_read"])))
        fill_table(self.requests, rows, right_from=3)


# ---------------------------------------------------------------- Analysis

Y_METRICS = [("power_w", "Power (W)", "W"), ("cpu", "CPU %", "%"),
             ("claude_cpu", "Claude apps CPU %", "%"), ("ram", "Memory %", "%"), ("gpu", "GPU %", "%")]


class AnalysisPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.result = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(16)
        head, self.sub = page_header("Analysis", "How your Claude usage lines up with what the computer did.")
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.range = QComboBox()
        for label, secs in (("Last hour", 3600), ("Last 6 hours", 6 * 3600), ("Last 24 hours", 86400),
                            ("Last 7 days", 7 * 86400), ("Everything", None)):
            self.range.addItem(label, secs)
        self.range.setCurrentIndex(4)
        self.window = QComboBox()
        for label, secs in (("30 s windows", 30), ("1 min windows", 60), ("2 min windows", 120),
                            ("5 min windows", 300)):
            self.window.addItem(label, secs)
        self.window.setCurrentIndex(1)
        self.run_btn = QPushButton("Analyze")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run)
        self.html_btn = QPushButton("Export report…")
        self.html_btn.setObjectName("ghost")
        self.html_btn.clicked.connect(self.save_report)
        self.csv_btn = QPushButton("Export CSV…")
        self.csv_btn.setObjectName("ghost")
        self.csv_btn.clicked.connect(self.save_csv)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setFixedWidth(90)
        self.busy.setTextVisible(False)
        self.busy.hide()
        for w in (self.range, self.window, self.run_btn, self.busy):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.html_btn)
        bar.addWidget(self.csv_btn)
        lay.addLayout(bar)

        self.verdict = QLabel("Press Analyze. Results get better the longer you record.")
        self.verdict.setWordWrap(True)
        self.verdict.setTextFormat(Qt.RichText)
        self.verdict.setStyleSheet("font-size:14px; line-height:150%;")
        vcard = Card()
        vcard.setStyleSheet("#card {{ border-left: 3px solid {}; }}".format(T.ACCENT))
        vcard.lay.addWidget(self.verdict)
        lay.addWidget(vcard)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.k_windows = StatCard("Windows recorded", lambda v: short_num(v), T.CPU)
        self.k_active = StatCard("With Claude activity", lambda v: short_num(v), T.CLAUDE)
        self.k_tokens = StatCard("Output tokens", lambda v: short_num(v), T.TOKENS)
        self.k_energy = StatCard("Energy", lambda v: "{:.2f} Wh".format(v), T.ACCENT)
        self.k_extra = StatCard("Extra energy with Claude", lambda v: "{:.3f} Wh".format(v), T.ACCENT)
        self.k_per = StatCard("Wh per 1k output tokens", lambda v: "{:.4f}".format(v), T.GPU)
        for i, c in enumerate((self.k_windows, self.k_active, self.k_tokens, self.k_energy, self.k_extra, self.k_per)):
            grid.addWidget(c, i // 3, i % 3)
        lay.addLayout(grid)

        self.ymetric = QComboBox()
        for key, label, _ in Y_METRICS:
            self.ymetric.addItem(label, key)
        self.ymetric.currentIndexChanged.connect(self._draw_scatter)
        self.scatter = ScatterChart()
        self.scatter.setMinimumHeight(340)
        lay.addWidget(section("Output tokens vs hardware, one point per window", self.scatter, self.ymetric))

        self.heat = Heatmap()
        self.heat.setMinimumHeight(260)
        lay.addWidget(section("Correlation matrix (Spearman)", self.heat))

        self.compare = CompareBars()
        lay.addWidget(section("With Claude vs without", self.compare))

        self.marginal = make_table(["Measure", "Slope", "95% CI", "R²", "p"])
        lay.addWidget(section("Marginal energy on this computer", self.marginal))
        fill_table(self.marginal, [])

        note = QLabel("Claude runs in Anthropic's datacenters, so these numbers cover your computer's side only. "
                      "p-values account for neighbouring windows being alike. Correlation is not causation: "
                      "other programs running at the same time count too.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)

    def run(self):
        since = self.range.currentData()
        since = time.time() - since if since else None
        self.run_btn.setEnabled(False)
        self.busy.show()
        job = AnalysisJob(self.main.db_path, since, self.window.currentData(), self.main.specs)
        job.signals.done.connect(self._done)
        job.signals.failed.connect(self._failed)
        QThreadPool.globalInstance().start(job)

    def _failed(self, tb):
        self.run_btn.setEnabled(True)
        self.busy.hide()
        QMessageBox.warning(self, "Analysis failed", tb[-1500:])

    def _done(self, res):
        self.run_btn.setEnabled(True)
        self.busy.hide()
        self.result = res
        s = res["summary"]
        self.verdict.setText(report.verdict(res).replace("<code>", "<b>").replace("</code>", "</b>"))
        kinds = s["power_kinds"]
        total = sum(kinds.values()) or 1
        self.sub.setText("{:,} samples · power {:.0f}% measured, {:.0f}% calibrated, {:.0f}% estimated".format(
            s["samples"], 100 * kinds.get("measured", 0) / total, 100 * kinds.get("calibrated", 0) / total,
            100 * kinds.get("estimated", 0) / total))
        self.k_windows.set(s["windows"], "{} s each".format(s["window_s"]))
        self.k_active.set(s["active_windows"], "{} idle".format(s["windows"] - s["active_windows"]))
        self.k_tokens.set(s["output_tokens"], "{} incl. cache".format(short_num(s["all_tokens"])))
        self.k_energy.set(s["energy_wh"] or 0.0, "avg {:.1f} W".format(s["avg_power_w"]) if s["avg_power_w"] else "")
        self.k_extra.set(s["extra_energy_wh"] or 0.0, "above idle power")
        self.k_per.set(s["wh_per_1k_output_avg"] or 0.0, "local machine only")
        self._draw_scatter()

        corrs = res["correlations"]
        rows, cols, cells = [], [], {}
        for c in corrs:
            if (c["token"], c["token_label"]) not in rows:
                rows.append((c["token"], c["token_label"]))
            if (c["hw"], c["hw_label"]) not in cols:
                cols.append((c["hw"], c["hw_label"]))
            p = c["p"]
            cells[(c["token"], c["hw"])] = {
                "value": c["spearman"],
                "title": "{} × {}".format(c["token_label"], c["hw_label"]),
                "rows": [(None, "Spearman", _f(c["spearman"], 3, sign=True)),
                         (None, "Pearson", _f(c["pearson"], 3, sign=True)),
                         (None, "p-value", "<0.001" if p is not None and p < 0.001 else _f(p, 3)),
                         (None, "Windows (effective)", "{} ({})".format(c["n"], c["n_eff"])),
                         (None, "Reading", c["strength"])],
            }
        self.heat.set_data(rows, cols, cells)

        units = {"power_w": "W", "cpu": "%", "claude_cpu": "%", "ram": "%", "gpu": "%"}
        self.compare.set_rows([(r["label"].replace(" (W)", ""), r["active"], r["idle"], units[r["metric"]], r["p"])
                               for r in res["active_vs_idle"]["rows"]])
        fill_table(self.marginal, [
            (v["unit"], _f(v["slope"], 4), "± " + _f(v["ci95"], 4), _f(v["r2"], 3),
             "<0.001" if v["p"] is not None and v["p"] < 0.001 else _f(v["p"], 3))
            for v in res["marginal"].values()])

    def _draw_scatter(self):
        if not self.result:
            return
        key = self.ymetric.currentData()
        label = self.ymetric.currentText()
        pts = []
        for w in self.result["windows"]:
            if w.get(key) is None:
                continue
            pts.append({
                "x": w["output"], "y": w[key],
                "title": datetime.fromtimestamp(w["start"]).strftime("%a %d %b, %H:%M:%S"),
                "rows": [(T.TOKENS, "Output tokens", "{:,}".format(w["output"])),
                         (T.TOKENS, "All tokens", short_num(w["total"])),
                         (T.RAM, "Requests", str(w["requests"])),
                         (T.ACCENT, "Power", "{:.2f} W".format(w["power_w"])),
                         (T.CPU, "CPU", "{:.1f}%".format(w["cpu"])),
                         (T.CLAUDE, "Claude apps", "{:.2f}%".format(w["claude_cpu"] or 0))],
            })
        fit = None
        if len(pts) >= 3:
            r = stats.linregress([p["x"] for p in pts], [p["y"] for p in pts])
            fit = (r["intercept"], r["slope"])
        self.scatter.set_data(pts, "Output tokens per window", label, fit)

    def save_report(self):
        if not self.result:
            self.run()
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save report", str(Path.home() / "tokenwatt-report.html"),
                                              "HTML (*.html)")
        if not path:
            return
        Path(path).write_text(report.render(self.result, self.main.specs), encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save_csv(self):
        if not self.result:
            self.run()
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save windows as CSV", str(Path.home() / "tokenwatt-windows.csv"),
                                              "CSV (*.csv)")
        if not path:
            return
        cols = ["start", "cpu", "claude_cpu", "ram", "gpu", "power_w", "energy_wh", "requests",
                "input", "output", "cache_write", "cache_read", "total"]
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time"] + cols)
            for row in self.result["windows"]:
                w.writerow([datetime.fromtimestamp(row["start"]).isoformat(timespec="seconds")]
                           + [row[c] for c in cols])


def _f(v, d=2, sign=False):
    if v is None:
        return "–"
    return ("{:+.%df}" % d if sign else "{:,.%df}" % d).format(v)


# ---------------------------------------------------------------- Hardware & settings

class HardwarePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 28)
        lay.setSpacing(16)
        head, _ = page_header("Hardware & settings", "What tokenwatt found on this computer.")
        lay.addLayout(head)

        self.specs_table = make_table(["Component", "Details"])
        self.specs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        lay.addWidget(section("This computer", self.specs_table))
        fill_table(self.specs_table, [("Loading…", "")], right_from=9)

        self.power_note = QLabel("")
        self.power_note.setWordWrap(True)
        self.power_note.setObjectName("muted")
        lay.addWidget(section("How power is measured here", self.power_note))

        st = main.settings
        form = QGridLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        self.interval = QDoubleSpinBox()
        self.interval.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.interval.setRange(0.5, 30)
        self.interval.setSingleStep(0.5)
        self.interval.setSuffix(" s")
        self.interval.setValue(float(st.value("interval", 2.0)))
        self.tdp = QDoubleSpinBox()
        self.tdp.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.tdp.setRange(0, 400)
        self.tdp.setSuffix(" W")
        self.tdp.setSpecialValueText("Automatic")
        self.tdp.setValue(float(st.value("tdp", 0.0)))
        self.autostart = QCheckBox("Start recording when the app opens")
        self.autostart.setChecked(st.value("autostart", "true") == "true")
        self.tray = QCheckBox("Keep recording in the background when the window is closed")
        self.tray.setChecked(st.value("tray", "true") == "true")
        form.addWidget(QLabel("Sample every"), 0, 0)
        form.addWidget(self.interval, 0, 1)
        form.addWidget(QLabel("CPU rated power"), 1, 0)
        form.addWidget(self.tdp, 1, 1)
        form.addWidget(self.autostart, 2, 0, 1, 3)
        form.addWidget(self.tray, 3, 0, 1, 3)
        form.setColumnStretch(2, 1)
        holder = QWidget()
        holder.setLayout(form)
        lay.addWidget(section("Settings", holder))

        self.interval.valueChanged.connect(self._interval)
        self.tdp.valueChanged.connect(self._tdp)
        self.autostart.toggled.connect(lambda v: st.setValue("autostart", "true" if v else "false"))
        self.tray.toggled.connect(lambda v: st.setValue("tray", "true" if v else "false"))

        data = QWidget()
        dl = QHBoxLayout(data)
        dl.setContentsMargins(0, 0, 0, 0)
        path = QLabel(str(main.db_path))
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path.setObjectName("muted")
        open_btn = QPushButton("Show in folder")
        open_btn.setObjectName("ghost")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(main.db_path).parent))))
        dl.addWidget(path, 1)
        dl.addWidget(open_btn)
        lay.addWidget(section("Your data (stays on this computer)", data))

        about = QLabel("tokenwatt {} · MIT license · github.com/ianjacobo2009-hub/tokenwatt<br>"
                       "Token counts come from Claude Code's local logs. The command-line version "
                       "(<b>tokenwatt</b> in a terminal) shares the same database.".format(__version__))
        about.setWordWrap(True)
        about.setObjectName("muted")
        about.setTextFormat(Qt.RichText)
        lay.addWidget(about)
        lay.addStretch(1)

    def _interval(self, v):
        self.main.settings.setValue("interval", v)
        self.main.set_interval.emit(v)

    def _tdp(self, v):
        self.main.settings.setValue("tdp", v)
        self.main.set_tdp.emit(v)

    def set_specs(self, info):
        gpus = "\n".join("{}{}".format(g["name"], " · {}".format(g["vram"]) if g.get("vram") else "")
                         for g in info.get("gpus") or []) or "none detected"
        rows = [
            ("Operating system", "{} · {}".format(info.get("os"), info.get("machine"))),
            ("Processor", info.get("cpu")),
            ("Cores", "{} physical · {} logical".format(info.get("cores_physical"), info.get("cores_logical"))),
            ("Max clock", "{} MHz".format(info["cpu_max_mhz"]) if info.get("cpu_max_mhz") else "–"),
            ("Rated CPU power", "~{:.0f} W (from the model name)".format(info.get("cpu_tdp_w") or 0)),
            ("Memory", "{} GB".format(info.get("ram_total_gb"))),
            ("Graphics", gpus),
            ("Battery", "yes" if info.get("has_battery") else "no"),
        ]
        fill_table(self.specs_table, rows, right_from=9)
        if sys.platform == "darwin" and info.get("has_battery"):
            txt = ("On battery, power is measured directly from the battery's voltage and current. "
                   "Plugged in, macOS doesn't report it, so tokenwatt estimates it from CPU and GPU load. "
                   "Record unplugged for 15–20 minutes once and the analysis calibrates the estimate "
                   "to your machine.")
        elif sys.platform.startswith("linux"):
            txt = ("On battery, power comes from the battery. Otherwise Intel RAPL (CPU package) and "
                   "nvidia-smi are used when readable, with an estimate from load as the fallback.")
        else:
            txt = ("Windows doesn't expose a power meter to normal apps, so power is estimated from CPU and "
                   "GPU load and the CPU's rated power. NVIDIA cards report their real draw, which is used.")
        self.power_note.setText(txt)


# ---------------------------------------------------------------- Window

class MainWindow(QMainWindow):
    set_interval = Signal(float)
    set_tdp = Signal(float)
    set_recording = Signal(bool)

    def __init__(self, db_path=None):
        super().__init__()
        self.settings = QSettings("tokenwatt", "tokenwatt")
        self.db_path = str(db_path or store.default_db_path())
        self.specs = {}
        self.recording = False
        self.rec_started = None
        self._really_quit = False
        self._tray_hint_shown = False
        self.setWindowTitle("tokenwatt")
        self.setWindowIcon(app_icon())
        self.resize(1280, 860)
        self.setMinimumSize(1080, 700)

        root = QWidget()
        root.setObjectName("root")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        self.live = LivePage()
        self.analysis = AnalysisPage(self)
        self.hardware = HardwarePage(self)
        for page in (self.live, self.analysis, self.hardware):
            self.stack.addWidget(scroll_page(page))
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self._start_worker()
        self._setup_tray()
        self._preload()
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._update_clock)
        self.clock.start(1000)
        if self.settings.value("autostart", "true") == "true":
            QTimer.singleShot(400, lambda: self.toggle_recording(True))

    def _sidebar(self):
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 20, 16, 18)
        v.setSpacing(4)
        brand = QLabel("tokenwatt")
        brand.setObjectName("brand")
        sub = QLabel("Claude usage × your hardware")
        sub.setObjectName("brandSub")
        v.addWidget(brand)
        v.addWidget(sub)
        v.addSpacing(22)
        self.nav = QButtonGroup(self)
        for i, name in enumerate(("Live", "Analysis", "Hardware && settings")):
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            v.addWidget(b)
        self.nav.button(0).setChecked(True)
        self.nav.idClicked.connect(self._go)
        v.addStretch(1)

        status = Card()
        status.lay.setSpacing(6)
        row = QHBoxLayout()
        self.dot = PulseDot()
        self.rec_label = QLabel("Not recording")
        self.rec_label.setStyleSheet("font-weight:600;")
        row.addWidget(self.dot)
        row.addWidget(self.rec_label, 1)
        status.lay.addLayout(row)
        self.rec_detail = QLabel("Live view only")
        self.rec_detail.setObjectName("muted")
        self.rec_detail.setWordWrap(True)
        status.lay.addWidget(self.rec_detail)
        self.rec_btn = QPushButton("Start recording")
        self.rec_btn.setObjectName("primary")
        self.rec_btn.setCursor(Qt.PointingHandCursor)
        self.rec_btn.clicked.connect(lambda: self.toggle_recording(not self.recording))
        status.lay.addWidget(self.rec_btn)
        v.addWidget(status)
        self.status_line = QLabel("")
        self.status_line.setObjectName("muted")
        self.status_line.setWordWrap(True)
        v.addSpacing(6)
        v.addWidget(self.status_line)
        return side

    def _go(self, i):
        self.stack.setCurrentIndex(i)
        if i == 1 and self.analysis.result is None:
            self.analysis.run()

    def _start_worker(self):
        self._thread = QThread(self)
        self.worker = LiveWorker(self.db_path)
        self.worker.interval = float(self.settings.value("interval", 2.0))
        tdp = float(self.settings.value("tdp", 0.0))
        self.worker.tdp_override = tdp or None
        self.worker.moveToThread(self._thread)
        self._thread.started.connect(self.worker.start)
        self.worker.specs_ready.connect(self._on_specs)
        self.worker.sample.connect(self.live.on_sample)
        self.worker.sample.connect(self._on_sample)
        self.worker.token_update.connect(self.live.on_tokens)
        self.worker.status.connect(self.status_line.setText)
        self.worker.failed.connect(self._worker_failed)
        self.set_interval.connect(self.worker.set_interval)
        self.set_tdp.connect(self.worker.set_tdp)
        self.set_recording.connect(self.worker.set_recording)
        self._thread.start()

    def _preload(self):
        try:
            con = store.connect(self.db_path)
            rows = store.samples_between(con, time.time() - 1800, None)
            con.close()
        except Exception:
            return
        self.live.preload(rows)

    def _on_specs(self, info):
        self.specs = info
        self.live.set_specs(info)
        self.hardware.set_specs(info)

    def _on_sample(self, s):
        if s["recording"]:
            self.rec_detail.setText("{:,} samples saved".format(s["saved"]))

    def _worker_failed(self, tb):
        self.status_line.setText("Sensor error: " + tb.strip().splitlines()[-1][:160])

    def toggle_recording(self, on):
        self.recording = on
        self.set_recording.emit(on)
        self.dot.set_active(on)
        self.rec_btn.setText("Stop recording" if on else "Start recording")
        self.rec_btn.setProperty("recording", "true" if on else "false")
        self.rec_btn.style().unpolish(self.rec_btn)
        self.rec_btn.style().polish(self.rec_btn)
        if on:
            self.rec_started = time.time()
            self.live.reset_energy()
            self.rec_detail.setText("Saving samples…")
        else:
            self.rec_started = None
            self.rec_label.setText("Not recording")
            self.rec_detail.setText("Live view only")
        if hasattr(self, "tray_toggle"):
            self.tray_toggle.setText("Stop recording" if on else "Start recording")
        self._update_clock()

    def _update_clock(self):
        if self.recording and self.rec_started:
            secs = int(time.time() - self.rec_started)
            self.rec_label.setText("Recording · {:d}:{:02d}:{:02d}".format(secs // 3600, secs // 60 % 60, secs % 60))

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("tokenwatt")
        menu = QMenu()
        show = QAction("Open tokenwatt", self)
        show.triggered.connect(self._show)
        self.tray_toggle = QAction("Start recording", self)
        self.tray_toggle.triggered.connect(lambda: self.toggle_recording(not self.recording))
        quit_ = QAction("Quit", self)
        quit_.triggered.connect(self.quit)
        menu.addAction(show)
        menu.addAction(self.tray_toggle)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._show() if r == QSystemTrayIcon.Trigger and
                                    sys.platform != "darwin" else None)
        self.tray.show()
        self._tray_menu = menu

    def _show(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit(self):
        self._really_quit = True
        self.close()

    def closeEvent(self, e):
        keep = (self.tray is not None and self.recording and not self._really_quit
                and self.settings.value("tray", "true") == "true")
        if keep:
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self.tray.showMessage("tokenwatt is still recording",
                                      "It keeps running from the menu bar / tray. Choose Quit there to stop.",
                                      QSystemTrayIcon.Information, 4000)
                self._tray_hint_shown = True
            return
        _call_in_thread(self.worker, "stop")
        self._thread.quit()
        self._thread.wait(3000)
        if self.tray:
            self.tray.hide()
        e.accept()
        QApplication.instance().quit()


def _call_in_thread(obj, method):
    QMetaObject.invokeMethod(obj, method, Qt.BlockingQueuedConnection)


def main(argv=None):
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tokenwatt.app")
        except Exception:
            pass
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("tokenwatt")
    app.setOrganizationName("tokenwatt")
    app.setApplicationDisplayName("tokenwatt")
    app.setWindowIcon(app_icon())
    app.setFont(T.ui_font(10))
    app.setStyle("Fusion")
    app.setStyleSheet(T.STYLESHEET)
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
