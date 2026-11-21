import math
from collections import defaultdict

from tokenwatt import stats

MIN_CALIBRATION = 30

TOKEN_METRICS = [
    ("output", "Output tokens"),
    ("total", "All tokens (incl. cache)"),
    ("fresh", "Uncached tokens"),
    ("requests", "Requests"),
]
HW_METRICS = [
    ("power_w", "Power (W)"),
    ("cpu", "CPU %"),
    ("claude_cpu", "Claude apps CPU %"),
    ("ram", "RAM %"),
    ("gpu", "GPU %"),
]


def calibrate(samples):
    """Fit power ~ CPU (+ GPU) on samples where power was actually measured."""
    measured = [s for s in samples if s["watts"] is not None]
    if len(measured) < MIN_CALIBRATION:
        return None
    # a GPU column that never moves is just a second intercept and makes the fit singular
    use_gpu = (all(s["gpu_pct"] is not None for s in measured)
               and stats.stdev([s["gpu_pct"] for s in measured]) >= 1)
    cpus = [s["cpu_pct"] for s in measured]
    if stats.stdev(cpus) < 2:  # machine sat idle the whole time, nothing to learn from
        return None

    def feats(s):
        f = [1.0, s["cpu_pct"]]
        if use_gpu:
            f.append(s["gpu_pct"] or 0.0)
        return f

    ys = [s["watts"] for s in measured]
    coef = stats.lstsq([feats(s) for s in measured], ys)
    if coef is None:
        return None
    preds = [sum(c * f for c, f in zip(coef, feats(s))) for s in measured]
    sst = sum((y - stats.mean(ys)) ** 2 for y in ys)
    sse = sum((y - p) ** 2 for y, p in zip(ys, preds))
    return {
        "n": len(measured),
        "coef": coef,
        "use_gpu": use_gpu,
        "r2": 1 - sse / sst if sst else float("nan"),
        "mae": stats.mean([abs(y - p) for y, p in zip(ys, preds)]),
        "predict": lambda s: max(0.0, sum(c * f for c, f in zip(coef, feats(s)))),
    }


def attach_power(samples, calib):
    kinds = defaultdict(int)
    for s in samples:
        if s["watts"] is not None:
            s["power_w"], kind = s["watts"], "measured"
        elif calib:
            s["power_w"], kind = calib["predict"](s), "calibrated"
        else:
            s["power_w"], kind = s["watts_est"], "estimated"
        s["power_kind"] = kind
        kinds[kind] += 1
    return dict(kinds)


def build_windows(samples, tokens, window):
    if not samples:
        return []
    intervals = sorted(s["interval_s"] for s in samples if s["interval_s"] > 0)
    nominal = intervals[len(intervals) // 2] if intervals else window
    cap = max(nominal * 3, 1.0)

    bins = defaultdict(list)
    for s in samples:
        bins[int(s["ts"] // window)].append(s)

    tok = defaultdict(lambda: defaultdict(float))
    for t in tokens:
        b = int(t["ts"] // window)
        if b not in bins:
            continue
        d = tok[b]
        d["input"] += t["input"]
        d["output"] += t["output"]
        d["cache_write"] += t["cache_write"]
        d["cache_read"] += t["cache_read"]
        d["requests"] += 1
        d["model:" + (t["model"] or "unknown")] += t["output"]

    out = []
    for b in sorted(bins):
        ss = bins[b]
        covered = sum(min(s["interval_s"], cap) for s in ss)
        if covered < window * 0.5:
            continue
        gpus = [s["gpu_pct"] for s in ss if s["gpu_pct"] is not None]
        claude = [s["claude_cpu"] for s in ss if s["claude_cpu"] is not None]
        t = tok.get(b, {})
        w = {
            "bin": b,
            "start": b * window,
            "cpu": stats.mean([s["cpu_pct"] for s in ss]),
            "claude_cpu": stats.mean(claude) if claude else None,
            "ram": stats.mean([s["ram_pct"] for s in ss]),
            "gpu": stats.mean(gpus) if gpus else None,
            "power_w": stats.mean([s["power_w"] for s in ss]),
            "measured_share": sum(1 for s in ss if s["power_kind"] == "measured") / len(ss),
            "input": int(t.get("input", 0)),
            "output": int(t.get("output", 0)),
            "cache_write": int(t.get("cache_write", 0)),
            "cache_read": int(t.get("cache_read", 0)),
            "requests": int(t.get("requests", 0)),
            "models": {k[6:]: int(v) for k, v in t.items() if k.startswith("model:")},
        }
        w["fresh"] = w["input"] + w["cache_write"] + w["output"]
        w["total"] = w["fresh"] + w["cache_read"]
        w["energy_wh"] = w["power_w"] * window / 3600
        out.append(w)
    return out


def strength(r):
    if r is None or math.isnan(r):
        return "n/a"
    a = abs(r)
    word = "none" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong"
    if word == "none":
        return word
    return word + (" positive" if r > 0 else " negative")


def correlate(windows):
    result = []
    for tk, tlabel in TOKEN_METRICS:
        for hk, hlabel in HW_METRICS:
            pairs = [(w[tk], w[hk]) for w in windows if w[hk] is not None]
            n = len(pairs)
            if n < 5:
                result.append({"token": tk, "hw": hk, "token_label": tlabel, "hw_label": hlabel,
                               "n": n, "pearson": None, "spearman": None, "p": None,
                               "n_eff": n, "strength": "n/a"})
                continue
            xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
            r = stats.pearson(xs, ys)
            rho = stats.spearman(xs, ys)
            n_eff = stats.effective_n(n, xs, ys)
            result.append({
                "token": tk, "hw": hk, "token_label": tlabel, "hw_label": hlabel, "n": n,
                "pearson": _clean(r), "spearman": _clean(rho),
                "p": _clean(stats.corr_pvalue(rho, n_eff)),
                "n_eff": n_eff, "strength": strength(rho),
            })
    return result


def lags(windows, token_key="output", hw_key="power_w", max_lag=5):
    by_bin = {w["bin"]: w for w in windows}
    out = []
    for lag in range(-max_lag, max_lag + 1):
        xs, ys = [], []
        for b, w in by_bin.items():
            other = by_bin.get(b + lag)
            if other is not None and other[hw_key] is not None:
                xs.append(w[token_key])
                ys.append(other[hw_key])
        r = stats.spearman(xs, ys) if len(xs) >= 5 else float("nan")
        out.append({"lag": lag, "n": len(xs), "r": _clean(r)})
    return out


def active_vs_idle(windows):
    active = [w for w in windows if w["requests"] > 0]
    idle = [w for w in windows if w["requests"] == 0]
    rows = []
    for key, label in HW_METRICS:
        a = [w[key] for w in active if w[key] is not None]
        i = [w[key] for w in idle if w[key] is not None]
        diff, p = stats.welch(a, i)
        rows.append({"metric": key, "label": label,
                     "active": _clean(stats.mean(a)) if a else None,
                     "idle": _clean(stats.mean(i)) if i else None,
                     "diff": _clean(diff), "p": _clean(p)})
    return {"active_windows": len(active), "idle_windows": len(idle), "rows": rows}


def marginal_energy(windows):
    out = {}
    ys = [w["energy_wh"] for w in windows]
    for key, scale, unit in (("output", 1000, "Wh per 1k output tokens"),
                             ("total", 1_000_000, "Wh per 1M tokens (all)"),
                             ("requests", 1, "Wh per request")):
        xs = [w[key] / scale for w in windows]
        fit = stats.linregress(xs, ys)
        out[key] = {
            "unit": unit,
            "slope": _clean(fit["slope"]),
            "ci95": _clean(1.96 * fit["slope_se"]) if not math.isnan(fit["slope_se"]) else None,
            "baseline_wh": _clean(fit["intercept"]),
            "r2": _clean(fit["r2"]),
            "p": _clean(fit["p"]),
            "n": fit["n"],
        }
    return out


def _clean(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return v


def run(samples, tokens, window=60, remote_wh_per_1k=None):
    samples = [dict(s) for s in samples]
    calib = calibrate(samples)
    power_kinds = attach_power(samples, calib)
    windows = build_windows(samples, tokens, window)

    covered_bins = {w["bin"] for w in windows}
    tokens_in = [t for t in tokens if int(t["ts"] // window) in covered_bins]

    per_model = defaultdict(lambda: {"requests": 0, "input": 0, "output": 0,
                                     "cache_write": 0, "cache_read": 0})
    for t in tokens_in:
        m = per_model[t["model"] or "unknown"]
        m["requests"] += 1
        for k in ("input", "output", "cache_write", "cache_read"):
            m[k] += t[k]

    active = [w for w in windows if w["requests"] > 0]
    idle = [w for w in windows if w["requests"] == 0]
    idle_power = stats.mean([w["power_w"] for w in idle]) if idle else None
    total_wh = sum(w["energy_wh"] for w in windows)
    active_wh = sum(w["energy_wh"] for w in active)
    extra_wh = None
    if idle_power is not None and active:
        extra_wh = sum(max(0.0, w["power_w"] - idle_power) * window / 3600 for w in active)

    out_tokens = sum(w["output"] for w in windows)
    summary = {
        "window_s": window,
        "samples": len(samples),
        "windows": len(windows),
        "active_windows": len(active),
        "start": samples[0]["ts"] if samples else None,
        "end": samples[-1]["ts"] if samples else None,
        "tokens_total_all_time": len(tokens),
        "requests_in_windows": len(tokens_in),
        "output_tokens": out_tokens,
        "all_tokens": sum(w["total"] for w in windows),
        "energy_wh": total_wh,
        "active_energy_wh": active_wh,
        "extra_energy_wh": extra_wh,
        "avg_power_w": stats.mean([w["power_w"] for w in windows]) if windows else None,
        "idle_power_w": idle_power,
        "power_kinds": power_kinds,
        "calibration": None if not calib else {
            k: calib[k] for k in ("n", "coef", "use_gpu", "r2", "mae")},
        "wh_per_1k_output_avg": (extra_wh / out_tokens * 1000
                                 if extra_wh is not None and out_tokens else None),
    }
    if remote_wh_per_1k is not None:
        summary["remote_wh_assumed_per_1k"] = remote_wh_per_1k
        summary["remote_energy_wh"] = out_tokens / 1000 * remote_wh_per_1k

    return {
        "summary": summary,
        "per_model": dict(per_model),
        "correlations": correlate(windows),
        "lags": lags(windows),
        "active_vs_idle": active_vs_idle(windows),
        "marginal": marginal_energy(windows),
        "windows": windows,
    }


def headline(result):
    """The one correlation people usually care about: output tokens vs power."""
    for c in result["correlations"]:
        if c["token"] == "output" and c["hw"] == "power_w":
            return c
    return None
