import json
import time
import traceback
from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, QTimer, Signal, Slot

from tokenwatt import analyze, specs, store, tokens
from tokenwatt.sampler import Sampler


def midnight_ts():
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class LiveWorker(QObject):
    """Lives on its own thread. Samples the hardware for the gauges all the time,
    saves samples only while recording, and follows the Claude Code logs."""

    specs_ready = Signal(dict)
    sample = Signal(dict)
    token_update = Signal(dict)
    status = Signal(str)
    failed = Signal(str)

    TOKEN_EVERY_MS = 4000

    def __init__(self, db_path=None):
        super().__init__()
        self.db_path = db_path
        self.recording = False
        self.interval = 2.0
        self.tdp_override = None
        self.con = None
        self.sampler = None
        self.timer = None
        self.token_timer = None
        self.first_import = True
        self.recorded_since = None
        self.saved = 0

    @Slot()
    def start(self):
        try:
            self.con = store.connect(self.db_path)
            cached = store.get_meta(self.con, "specs")
            if cached:
                self.specs = json.loads(cached)
                self.specs_ready.emit(self.specs)
            # system_profiler can take a second or two, so this runs here, off the UI thread
            fresh = specs.collect()
            store.set_meta(self.con, "specs", json.dumps(fresh))
            if fresh != (json.loads(cached) if cached else None):
                self.specs_ready.emit(fresh)
            self.specs = fresh
            self._make_sampler()
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(int(self.interval * 1000))
        self.token_timer = QTimer(self)
        self.token_timer.timeout.connect(self._tokens)
        self.token_timer.start(self.TOKEN_EVERY_MS)
        QTimer.singleShot(50, self._tokens)

    def _make_sampler(self):
        tdp = self.tdp_override or self.specs.get("cpu_tdp_w") or 35.0
        self.sampler = Sampler(tdp=tdp, laptop=bool(self.specs.get("has_battery")))

    @Slot(float)
    def set_interval(self, seconds):
        self.interval = max(0.5, float(seconds))
        if self.timer:
            self.timer.start(int(self.interval * 1000))

    @Slot(float)
    def set_tdp(self, watts):
        self.tdp_override = watts if watts and watts > 0 else None
        if self.sampler:
            self.sampler.tdp = self.tdp_override or self.specs.get("cpu_tdp_w") or 35.0

    @Slot(bool)
    def set_recording(self, on):
        self.recording = on
        if on:
            self.recorded_since = time.time()
            self.saved = 0

    def _tick(self):
        try:
            s = self.sampler.sample()
            if self.recording:
                store.insert_sample(self.con, s)
                self.saved += 1
            s = dict(s)
            s["saved"] = self.saved
            s["recording"] = self.recording
            self.sample.emit(s)
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _tokens(self):
        try:
            if self.first_import:
                self.status.emit("Reading Claude Code logs…")
            res = tokens.import_claude_code(self.con)
            if self.first_import:
                self.status.emit("Found {:,} Claude Code log files".format(res["files"]))
                self.first_import = False
            now = time.time()
            recent = store.tokens_between(self.con, now - 3 * 3600, None)
            today = self.con.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(output),0) o, COALESCE(SUM(input+cache_write),0) i, "
                "COALESCE(SUM(cache_read),0) c FROM tokens WHERE ts >= ?", (midnight_ts(),)).fetchone()
            total = self.con.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]
            self.token_update.emit({
                "recent": recent,
                "today_requests": today["n"],
                "today_output": today["o"],
                "today_input": today["i"],
                "today_cache": today["c"],
                "all_requests": total,
                "changed": res["rows"],
            })
        except Exception:
            self.failed.emit(traceback.format_exc())

    @Slot()
    def stop(self):
        for t in (self.timer, self.token_timer):
            if t:
                t.stop()
        if self.con:
            self.con.close()
            self.con = None


class _AnalysisSignals(QObject):
    done = Signal(dict)
    failed = Signal(str)


class AnalysisJob(QRunnable):
    def __init__(self, db_path, since, window, specs_info):
        super().__init__()
        self.db_path, self.since, self.window, self.specs_info = db_path, since, window, specs_info
        self.signals = _AnalysisSignals()

    def run(self):
        try:
            con = store.connect(self.db_path)
            try:
                samples = store.samples_between(con, self.since, None)
                toks = store.tokens_between(con, self.since, None)
            finally:
                con.close()
            res = analyze.run(samples, toks, window=self.window)
            res["specs"] = self.specs_info
            self.signals.done.emit(res)
        except Exception:
            self.signals.failed.emit(traceback.format_exc())
