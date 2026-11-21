import random

import pytest

from tokenwatt import analyze, report

T0 = 1_795_000_000 - (1_795_000_000 % 60)


def make_data(minutes=240, seed=1, watts_measured=True, link=True):
    """Synthetic session: Claude is busy in some minutes, and busy minutes draw more power."""
    rnd = random.Random(seed)
    samples, toks = [], []
    for m in range(minutes):
        busy = (m // 10) % 3 == 0
        out = rnd.randint(200, 2000) if busy else 0
        for k in range(12):
            ts = T0 + m * 60 + k * 5
            cpu = 8 + (25 * out / 2000 if link else 0) + rnd.uniform(-3, 3)
            w = 6 + 0.25 * cpu + rnd.uniform(-0.3, 0.3)
            samples.append({
                "ts": ts, "interval_s": 5.0, "cpu_pct": cpu, "claude_cpu": cpu / 3,
                "ram_pct": 60.0, "ram_used": 1, "swap_pct": 0.0, "freq_mhz": 3000.0,
                "gpu_pct": 5.0, "watts": w if watts_measured else None,
                "watts_src": "battery" if watts_measured else None,
                "watts_est": 5 + 28 * (0.08 + 0.92 * cpu / 100), "on_battery": 1,
            })
        if out:
            toks.append({"msg_id": "m%d" % m, "ts": T0 + m * 60 + 7, "model": "claude-opus-5-5",
                         "session": "s", "project": "p", "input": 10, "output": out,
                         "cache_write": 50, "cache_read": 5000, "source": "test"})
    return samples, toks


def test_detects_real_link():
    samples, toks = make_data()
    res = analyze.run(samples, toks)
    h = analyze.headline(res)
    assert h["spearman"] > 0.7
    assert h["p"] < 0.001
    assert res["summary"]["windows"] == 240
    assert res["summary"]["active_windows"] == 80
    assert res["summary"]["extra_energy_wh"] > 0
    assert res["marginal"]["output"]["slope"] > 0


def test_no_link_means_no_correlation():
    samples, toks = make_data(link=False)
    h = analyze.headline(analyze.run(samples, toks))
    assert abs(h["spearman"]) < 0.2


def test_calibration_used_when_power_partly_measured():
    samples, toks = make_data()
    for s in samples[len(samples) // 2:]:
        s["watts"] = None
        s["watts_src"] = None
    res = analyze.run(samples, toks)
    kinds = res["summary"]["power_kinds"]
    assert kinds["measured"] == kinds["calibrated"] == len(samples) // 2
    cal = res["summary"]["calibration"]
    assert cal["r2"] > 0.9
    assert cal["coef"][1] == pytest.approx(0.25, abs=0.03)


def test_estimate_fallback():
    samples, toks = make_data(watts_measured=False)
    res = analyze.run(samples, toks)
    assert res["summary"]["power_kinds"] == {"estimated": len(samples)}


def test_gaps_and_sparse_windows_dropped():
    samples, toks = make_data(minutes=10)
    # a lone sample an hour later covers too little of its minute to count
    lone = dict(samples[-1], ts=samples[-1]["ts"] + 3600)
    res = analyze.run(samples + [lone], toks)
    assert res["summary"]["windows"] == 10


def test_tokens_outside_recording_ignored():
    samples, toks = make_data(minutes=30)
    toks.append(dict(toks[0], msg_id="late", ts=T0 + 10 * 3600))
    res = analyze.run(samples, toks)
    assert res["summary"]["requests_in_windows"] == len(toks) - 1


def test_empty_inputs_do_not_crash():
    res = analyze.run([], [])
    assert res["summary"]["windows"] == 0
    html = report.render(res, {"gpus": []})
    assert "tokenwatt report" in html


def test_report_renders_and_escapes():
    samples, toks = make_data(minutes=60)
    toks[0]["model"] = "<script>alert(1)</script>"
    res = analyze.run(samples, toks, remote_wh_per_1k=0.3)
    html = report.render(res, {"os": "Test", "cpu": "CPU <x>", "gpus": [{"name": "G", "vram": "1 GB"}],
                               "cpu_tdp_w": 28, "has_battery": True})
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "<svg" in html
    assert "Datacenter est." in html
