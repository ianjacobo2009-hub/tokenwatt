import glob
import os
import platform
import re
import shutil
import time

import psutil

from tokenwatt.specs import cpu_freq, run

SYSTEM = platform.system()


def _unsigned_to_signed(n):
    # ioreg prints negative currents as 64-bit unsigned numbers
    return n - 2 ** 64 if n >= 2 ** 63 else n


def parse_ioreg_battery(text):
    """Pull voltage (mV), current (mA) and charger state out of `ioreg -rn AppleSmartBattery`."""
    def top(key):
        m = re.search(r'^\s*"{}" = (\S+)'.format(key), text, re.M)
        return m.group(1) if m else None

    volt, amp, ext = top("Voltage"), top("InstantAmperage") or top("Amperage"), top("ExternalConnected")
    if volt is None or amp is None:
        return None
    try:
        return {
            "voltage_mv": int(volt),
            "current_ma": _unsigned_to_signed(int(amp)),
            "external": ext == "Yes",
        }
    except ValueError:
        return None


def parse_ioreg_gpu(text):
    vals = [int(v) for v in re.findall(r'"Device Utilization %"=(\d+)', text)]
    return float(max(vals)) if vals else None


class GpuReader:
    def __init__(self):
        self.nvidia = shutil.which("nvidia-smi") is not None
        self.amd_paths = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")

    def read(self):
        """Returns (utilisation %, measured GPU watts or None)."""
        if self.nvidia:
            out = run(["nvidia-smi", "--query-gpu=utilization.gpu,power.draw",
                       "--format=csv,noheader,nounits"])
            utils, watts = [], []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                try:
                    utils.append(float(parts[0]))
                    watts.append(float(parts[1]))
                except (IndexError, ValueError):
                    continue
            if utils:
                return max(utils), (sum(watts) if watts else None)
        if SYSTEM == "Darwin":
            return parse_ioreg_gpu(run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"])), None
        if self.amd_paths:
            vals = []
            for p in self.amd_paths:
                try:
                    with open(p) as f:
                        vals.append(float(f.read().strip()))
                except (OSError, ValueError):
                    pass
            if vals:
                return max(vals), None
        return None, None


class PowerReader:
    """Measured whole-system (or CPU package) power, when the platform exposes it."""

    def __init__(self):
        self._rapl = {}
        self.rapl_zones = []
        if SYSTEM == "Linux":
            for zone in glob.glob("/sys/class/powercap/intel-rapl:[0-9]"):
                try:
                    with open(os.path.join(zone, "energy_uj")) as f:
                        int(f.read())
                    self.rapl_zones.append(zone)
                except (OSError, ValueError):
                    pass  # usually root-only since 2020

    def read(self):
        """Returns (watts or None, source name, on_battery or None)."""
        if SYSTEM == "Darwin":
            info = parse_ioreg_battery(run(["ioreg", "-rn", "AppleSmartBattery"]))
            if info is None:
                return None, None, None
            if not info["external"] and info["current_ma"] < 0:
                w = info["voltage_mv"] / 1000 * -info["current_ma"] / 1000
                return round(w, 3), "battery", True
            return None, None, not info["external"]
        if SYSTEM == "Linux":
            w, on_batt = self._linux_battery()
            if w is not None:
                return w, "battery", True
            w = self._rapl_watts()
            if w is not None:
                return w, "rapl", on_batt
            return None, None, on_batt
        try:
            b = psutil.sensors_battery()
            return None, None, (None if b is None else not b.power_plugged)
        except (AttributeError, NotImplementedError):
            return None, None, None

    def _linux_battery(self):
        on_batt = None
        for bat in glob.glob("/sys/class/power_supply/BAT*"):
            status = _read(os.path.join(bat, "status"))
            if status is None:
                continue
            on_batt = status == "Discharging"
            if not on_batt:
                continue
            p = _read_int(os.path.join(bat, "power_now"))
            if p is None:
                cur = _read_int(os.path.join(bat, "current_now"))
                volt = _read_int(os.path.join(bat, "voltage_now"))
                if cur is not None and volt is not None:
                    p = cur * volt / 1e6
            if p:
                return round(p / 1e6, 3), True
        return None, on_batt

    def _rapl_watts(self):
        if not self.rapl_zones:
            return None
        now = time.monotonic()
        total, ready = 0.0, True
        for zone in self.rapl_zones:
            e = _read_int(os.path.join(zone, "energy_uj"))
            if e is None:
                return None
            prev = self._rapl.get(zone)
            self._rapl[zone] = (now, e)
            if prev is None:
                ready = False
                continue
            dt, de = now - prev[0], e - prev[1]
            if de < 0:  # counter wrapped
                de += _read_int(os.path.join(zone, "max_energy_range_uj")) or 0
            if dt > 0:
                total += de / 1e6 / dt
        return round(total, 3) if ready else None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path):
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


class ClaudeProcs:
    """CPU used by Claude apps and the Claude Code CLI, as a share of the whole machine."""

    def __init__(self):
        self._procs = {}
        self._me = os.getpid()
        self._ncpu = psutil.cpu_count() or 1

    @staticmethod
    def _is_claude(p):
        name = (p.info.get("name") or "").lower()
        if "claude" in name:
            return True
        cmd = p.info.get("cmdline") or []
        return any("claude" in part.lower() for part in cmd[:3])

    def read(self):
        alive = set()
        total = 0.0
        for p in psutil.process_iter(["name", "cmdline"]):
            if p.pid == self._me:
                continue
            try:
                if not self._is_claude(p):
                    continue
                proc = self._procs.setdefault(p.pid, p)
                alive.add(p.pid)
                total += proc.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        for pid in list(self._procs):
            if pid not in alive:
                del self._procs[pid]
        return round(total / self._ncpu, 2)


def estimate_watts(cpu_pct, gpu_pct, tdp, laptop, gpu_watts=None):
    """Simple load model for when nothing measures power directly.

    Platform floor (screen, RAM, disk, fans) plus a CPU package that idles at
    ~8% of TDP and scales linearly with load. Integrated GPUs share the
    package budget; a discrete card's measured draw is added as-is.
    """
    floor = 5.0 if laptop else 25.0
    cpu = tdp * (0.08 + 0.92 * cpu_pct / 100.0)
    if gpu_watts is not None:
        gpu = gpu_watts
    elif gpu_pct is not None:
        gpu = 0.3 * tdp * gpu_pct / 100.0
    else:
        gpu = 0.0
    return round(floor + cpu + gpu, 3)


class Sampler:
    def __init__(self, tdp, laptop):
        self.tdp = tdp
        self.laptop = laptop
        self.gpu = GpuReader()
        self.power = PowerReader()
        self.claude = ClaudeProcs()
        psutil.cpu_percent(None)
        self.claude.read()
        self.power.read()
        self._last = time.time()

    def sample(self):
        now = time.time()
        interval = now - self._last
        self._last = now
        cpu = psutil.cpu_percent(None)
        mem = psutil.virtual_memory()
        try:
            swap = psutil.swap_memory().percent
        except (OSError, RuntimeError):
            swap = None
        f = cpu_freq()
        freq = round(f.current, 1) if f and f.current else None
        gpu_pct, gpu_w = self.gpu.read()
        watts, src, on_batt = self.power.read()
        if watts is not None and src == "rapl" and gpu_w is not None:
            watts = round(watts + gpu_w, 3)
            src = "rapl+gpu"
        return {
            "ts": now,
            "interval_s": round(interval, 3),
            "cpu_pct": cpu,
            "claude_cpu": self.claude.read(),
            "ram_pct": mem.percent,
            "ram_used": mem.used,
            "swap_pct": swap,
            "freq_mhz": freq,
            "gpu_pct": gpu_pct,
            "watts": watts,
            "watts_src": src,
            "watts_est": estimate_watts(cpu, gpu_pct, self.tdp, self.laptop, gpu_w),
            "on_battery": None if on_batt is None else int(on_batt),
        }
