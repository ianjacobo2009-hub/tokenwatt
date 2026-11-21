import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

import psutil

# Intel U-series chips that run at 28 W instead of the usual 15 W.
_INTEL_28W = {
    "7267U", "7287U", "7567U", "8259U", "8279U", "8559U", "8569U",
    "1038NG7", "1068NG7",
}


def run(cmd, timeout=5):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def cpu_name():
    system = platform.system()
    if system == "Darwin":
        name = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if name:
            return name
    elif system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif system == "Windows":
        out = run(["powershell", "-NoProfile", "-Command",
                   "(Get-CimInstance Win32_Processor).Name"])
        if out.strip():
            return out.strip().splitlines()[0]
    return platform.processor() or "unknown CPU"


def guess_tdp(name):
    """Rough package power in watts, from the CPU's marketing name."""
    n = name.upper()
    if "APPLE M" in n:
        if "ULTRA" in n:
            return 60.0
        if "MAX" in n:
            return 40.0
        if "PRO" in n:
            return 30.0
        return 20.0
    m = re.search(r"\b(\d{4,5})([A-Z][A-Z0-9]{0,2})?\b", n)
    if m:
        model, suffix = m.group(1), m.group(2) or ""
        if (model + suffix) in _INTEL_28W:
            return 28.0
        if suffix.startswith("U"):
            return 15.0
        if suffix.startswith("Y"):
            return 7.0
        if suffix in ("HS",):
            return 35.0
        if suffix.startswith("H"):
            return 45.0
        if suffix.startswith("K") or suffix == "X" or suffix == "X3D":
            return 125.0
        if suffix.startswith("T"):
            return 35.0
        if suffix in ("", "F"):
            return 65.0
    return 35.0


def gpus():
    system = platform.system()
    found = []
    if shutil.which("nvidia-smi"):
        out = run(["nvidia-smi", "--query-gpu=name,memory.total,power.limit",
                   "--format=csv,noheader,nounits"])
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 3:
                found.append({"name": parts[0], "vram": parts[1] + " MiB",
                              "power_limit_w": _num(parts[2])})
    if system == "Darwin":
        out = run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=15)
        try:
            for g in json.loads(out).get("SPDisplaysDataType", []):
                vram = g.get("_spdisplays_vram") or g.get("spdisplays_vram")
                if not vram and g.get("sppci_cores"):
                    vram = "{} GPU cores, shared memory".format(g["sppci_cores"])
                found.append({
                    "name": g.get("sppci_model") or g.get("_name", "GPU"),
                    "vram": vram,
                    "power_limit_w": None,
                })
        except ValueError:
            pass
    elif system == "Linux" and not found:
        for line in run(["lspci"]).splitlines():
            if "VGA" in line or "3D controller" in line:
                found.append({"name": line.split(":", 2)[-1].strip(), "vram": None,
                              "power_limit_w": None})
    elif system == "Windows" and not found:
        out = run(["powershell", "-NoProfile", "-Command",
                   "(Get-CimInstance Win32_VideoController).Name"])
        for line in out.strip().splitlines():
            found.append({"name": line.strip(), "vram": None, "power_limit_w": None})
    return found


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def collect():
    name = cpu_name()
    freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    battery = None
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        pass
    return {
        "os": "{} {}".format(platform.system(), platform.release()),
        "machine": platform.machine(),
        "cpu": name,
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "cpu_max_mhz": round(freq.max) if freq and freq.max else None,
        "cpu_tdp_w": guess_tdp(name),
        "ram_total_gb": round(mem.total / 1024 ** 3, 1),
        "gpus": gpus(),
        "has_battery": battery is not None,
        "python": platform.python_version(),
    }
