import html
import math
from datetime import datetime

from tokenwatt import __version__, stats
from tokenwatt.analyze import headline

W, H = 860, 260
PAD_L, PAD_R, PAD_T, PAD_B = 56, 56, 16, 34


def esc(v):
    return html.escape(str(v), quote=True)


def fmt(v, digits=2, unit=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, int) or (isinstance(v, float) and abs(v) >= 1000 and digits <= 2):
        s = "{:,.0f}".format(v)
    else:
        s = "{:,.{d}f}".format(v, d=digits)
    return s + unit


def fmt_p(p):
    if p is None:
        return "–"
    if p < 0.001:
        return "&lt;0.001"
    return "{:.3f}".format(p)


def when(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "–"


def span(a, b):
    if not a:
        return "–"
    da, db = datetime.fromtimestamp(a), datetime.fromtimestamp(b)
    if da.date() == db.date():
        return '{}<div class="k">{}</div>'.format(
            esc(da.strftime("%H:%M") + "–" + db.strftime("%H:%M")), esc(da.strftime("%b %d, %Y")))
    return '{}<div class="k">to {}</div>'.format(esc(da.strftime("%b %d")), esc(db.strftime("%b %d, %Y")))


def nice_max(v):
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if m * exp >= v:
            return m * exp
    return 10 * exp


def downsample(windows, limit=600):
    if len(windows) <= limit:
        return windows
    step = math.ceil(len(windows) / limit)
    out = []
    for i in range(0, len(windows), step):
        chunk = windows[i:i + step]
        out.append({
            "start": chunk[0]["start"],
            "bin": chunk[0]["bin"],
            "span": len(chunk),
            "power_w": stats.mean([w["power_w"] for w in chunk]),
            "output": sum(w["output"] for w in chunk),
        })
    return out


def timeline_svg(windows, window_s):
    pts = downsample(windows)
    if len(pts) < 2:
        return '<p class="muted">Not enough recorded windows to draw a timeline yet.</p>'
    t0, t1 = pts[0]["start"], pts[-1]["start"] + window_s
    pmax = nice_max(max(p["power_w"] for p in pts) * 1.1)
    omax = nice_max(max(p["output"] for p in pts) or 1)
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(t):
        return PAD_L + (t - t0) / (t1 - t0) * iw

    parts = [_grid(pmax, "W", left=True), _axis_right(omax)]
    bar_w = max(1.0, iw / len(pts) * 0.8)
    for p in pts:
        if p["output"]:
            h = p["output"] / omax * ih
            parts.append('<rect class="bar" x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}">'
                         '<title>{} · {:,} output tokens</title></rect>'.format(
                             x(p["start"]), PAD_T + ih - h, bar_w, h, when(p["start"]), p["output"]))
    # break the line wherever the recorder was off
    step = window_s * pts[1].get("span", 1) if len(pts) > 1 else window_s
    path, prev = [], None
    for p in pts:
        cmd = "L" if prev is not None and p["start"] - prev <= step * 1.5 else "M"
        y = PAD_T + ih - p["power_w"] / pmax * ih
        path.append("{}{:.1f},{:.1f}".format(cmd, x(p["start"] + window_s / 2), y))
        prev = p["start"]
    parts.append('<path class="line" d="{}"/>'.format(" ".join(path)))
    parts.append('<text class="tick" x="{}" y="{}">{}</text>'.format(PAD_L, H - 8, when(t0)))
    parts.append('<text class="tick" x="{}" y="{}" text-anchor="end">{}</text>'.format(
        W - PAD_R, H - 8, when(t1)))
    return _svg(parts, "Power and output tokens over time")


def scatter_svg(windows):
    pts = [(w["output"], w["power_w"]) for w in windows]
    if len(pts) < 3:
        return '<p class="muted">Not enough data for a scatter plot yet.</p>'
    xmax = nice_max(max(p[0] for p in pts) or 1)
    ymin = min(p[1] for p in pts)
    ymin = max(0.0, math.floor(ymin * 0.9))
    ymax = nice_max(max(p[1] for p in pts) * 1.05)
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def sx(v):
        return PAD_L + v / xmax * iw

    def sy(v):
        return PAD_T + ih - (v - ymin) / (ymax - ymin) * ih

    parts = [_grid(ymax, "W", left=True, lo=ymin)]
    for i in range(5):
        v = xmax * i / 4
        parts.append('<text class="tick" x="{:.1f}" y="{}" text-anchor="middle">{}</text>'.format(
            sx(v), H - 8, fmt(v, 0)))
    for xv, yv in pts:
        parts.append('<circle class="dot" cx="{:.1f}" cy="{:.1f}" r="3"/>'.format(sx(xv), sy(yv)))
    fit = stats.linregress([p[0] for p in pts], [p[1] for p in pts])
    if fit["slope"] == fit["slope"]:
        y0, y1 = fit["intercept"], fit["intercept"] + fit["slope"] * xmax
        parts.append('<line class="fit" x1="{:.1f}" y1="{:.1f}" x2="{:.1f}" y2="{:.1f}"/>'.format(
            sx(0), sy(min(max(y0, ymin), ymax)), sx(xmax), sy(min(max(y1, ymin), ymax))))
    return _svg(parts, "Output tokens per window vs average power")


def lag_svg(lag_rows):
    rows = [r for r in lag_rows if r["r"] is not None]
    if not rows:
        return '<p class="muted">Not enough consecutive windows for a lag analysis.</p>'
    h = 180
    iw, ih = W - PAD_L - PAD_R, h - PAD_T - PAD_B
    mid = PAD_T + ih / 2
    bw = iw / len(lag_rows)
    parts = ['<line class="axis" x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}"/>'.format(
        PAD_L, mid, W - PAD_R, mid)]
    for lbl, val in (("+1", PAD_T), ("0", mid), ("−1", PAD_T + ih)):
        parts.append('<text class="tick" x="{}" y="{:.1f}" text-anchor="end">{}</text>'.format(
            PAD_L - 6, val + 4, lbl))
    for i, r in enumerate(lag_rows):
        cx = PAD_L + bw * i
        if r["r"] is not None:
            bh = abs(r["r"]) * ih / 2
            y = mid - bh if r["r"] > 0 else mid
            parts.append('<rect class="{}" x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}">'
                         '<title>lag {} · r = {:.2f} · n = {}</title></rect>'.format(
                             "bar" if r["lag"] else "bar hot", cx + bw * 0.15, y, bw * 0.7, bh,
                             r["lag"], r["r"], r["n"]))
        parts.append('<text class="tick" x="{:.1f}" y="{}" text-anchor="middle">{:+d}</text>'.format(
            cx + bw / 2, h - 8, r["lag"]))
    return _svg(parts, "Correlation by lag", height=h)


def _grid(top, unit, left=True, lo=0.0):
    ih = H - PAD_T - PAD_B
    out = []
    for i in range(5):
        v = lo + (top - lo) * i / 4
        y = PAD_T + ih - ih * i / 4
        out.append('<line class="grid" x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}"/>'.format(
            PAD_L, y, W - PAD_R, y))
        out.append('<text class="tick" x="{}" y="{:.1f}" text-anchor="end">{}{}</text>'.format(
            PAD_L - 6, y + 4, fmt(v, 1 if top < 10 else 0), " " + unit if i == 4 else ""))
    return "".join(out)


def _axis_right(top):
    ih = H - PAD_T - PAD_B
    out = []
    for i in (0, 2, 4):
        y = PAD_T + ih - ih * i / 4
        out.append('<text class="tick tok" x="{}" y="{:.1f}">{}</text>'.format(
            W - PAD_R + 6, y + 4, fmt(top * i / 4, 0)))
    return "".join(out)


def _svg(parts, label, height=H):
    return ('<svg viewBox="0 0 {w} {h}" role="img" aria-label="{l}">{body}</svg>'
            .format(w=W, h=height, l=esc(label), body="".join(parts)))


def heat_cell(c):
    r = c["spearman"]
    if r is None:
        return '<td class="na">–</td>'
    a = min(1.0, abs(r))
    hue = "var(--pos)" if r >= 0 else "var(--neg)"
    return ('<td style="background:color-mix(in srgb, {hue} {pct:.0f}%, transparent)" '
            'title="Pearson {p:.2f} · p {pv} · n {n} (effective {ne})"><b>{r:+.2f}</b>'
            '<small>{s}</small></td>').format(
        hue=hue, pct=a * 70, p=c["pearson"] if c["pearson"] is not None else float("nan"),
        pv=fmt_p(c["p"]), n=c["n"], ne=c["n_eff"], r=r, s=esc(c["strength"]))


def corr_table(corrs):
    tks = []
    hws = []
    for c in corrs:
        if (c["token"], c["token_label"]) not in tks:
            tks.append((c["token"], c["token_label"]))
        if (c["hw"], c["hw_label"]) not in hws:
            hws.append((c["hw"], c["hw_label"]))
    idx = {(c["token"], c["hw"]): c for c in corrs}
    head = "".join("<th>{}</th>".format(esc(l)) for _, l in hws)
    body = []
    for tk, tl in tks:
        cells = "".join(heat_cell(idx[(tk, hk)]) for hk, _ in hws)
        body.append("<tr><th>{}</th>{}</tr>".format(esc(tl), cells))
    return ('<div class="scroll"><table class="heat"><thead><tr><th></th>{}</tr></thead>'
            '<tbody>{}</tbody></table></div>').format(head, "".join(body))


def verdict(result):
    h = headline(result)
    s = result["summary"]
    if not result["windows"]:
        return "No recorded hardware data yet. Run <code>tokenwatt record</code> while you use Claude."
    if s["active_windows"] < 5:
        return ("Only {} recorded window(s) had Claude activity, so there isn't enough overlap "
                "to judge yet. Keep the recorder running during a few work sessions.").format(
                    s["active_windows"])
    if h is None or h["spearman"] is None:
        return "Not enough paired data for a verdict."
    sig = h["p"] is not None and h["p"] < 0.05
    if h["strength"] == "none":
        txt = "Output tokens and power draw show <b>no clear relationship</b> (Spearman ρ = {:+.2f}{}).".format(
            h["spearman"], ", statistically significant" if sig else ", not significant")
    else:
        txt = "Output tokens and power draw show a <b>{}</b> relationship (Spearman ρ = {:+.2f}{}).".format(
            esc(h["strength"]), h["spearman"], ", statistically significant" if sig else ", not significant")
    if s.get("extra_energy_wh") is not None and s["output_tokens"]:
        txt += (" Windows with Claude activity used about <b>{}</b> more energy on this machine "
                "than idle windows, about <b>{} Wh</b> per 1,000 output tokens.").format(
                    fmt(s["extra_energy_wh"], 2, " Wh"), fmt(s["wh_per_1k_output_avg"], 3))
    return txt


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1d1f;--muted:#6b6f76;--line:#e3e3df;--accent:#d2691e;
--tok:#3b6fd8;--pos:#d2691e;--neg:#3b6fd8;--grid:#ecece8}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1d1f22;--ink:#e9e9e6;--muted:#9a9ea6;
--line:#2c2f33;--accent:#f08a3c;--tok:#6d98ff;--pos:#f08a3c;--neg:#6d98ff;--grid:#26292d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
main{max-width:940px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 10px}
.muted{color:var(--muted)}.small{font-size:13px}
.lead{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
padding:14px 16px;border-radius:8px;margin:20px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card .v{font-size:22px;font-weight:650}.card .k{font-size:12px;color:var(--muted)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--grid)}.axis{stroke:var(--muted)}.tick{fill:var(--muted);font-size:11px}
.tick.tok{fill:var(--tok)}
.line{fill:none;stroke:var(--accent);stroke-width:2}.bar{fill:var(--tok);opacity:.55}
.bar.hot{fill:var(--accent);opacity:.9}.dot{fill:var(--accent);opacity:.55}
.fit{stroke:var(--ink);stroke-width:1.5;stroke-dasharray:5 4}
.legend span{display:inline-block;margin-right:14px;font-size:13px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:5px;vertical-align:-1px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.heat td{text-align:center;min-width:92px}.heat td b{display:block}.heat td small{color:var(--muted)}
.scroll{overflow-x:auto}code{font-size:13px;background:var(--grid);padding:1px 5px;border-radius:4px}
footer{margin-top:40px;font-size:12px;color:var(--muted)}
"""


def render(result, specs):
    s = result["summary"]
    ws = result["windows"]
    pk = s["power_kinds"]
    total_s = sum(pk.values()) or 1
    measured_pct = 100 * pk.get("measured", 0) / total_s
    if pk.get("measured") and pk.get("calibrated"):
        power_note = ("{:.0f}% of samples measured directly; the rest predicted from a model "
                      "fitted to those measurements (R² {:.2f}).").format(
                          measured_pct, s["calibration"]["r2"])
    elif pk.get("measured") == total_s:
        power_note = "All power readings were measured directly."
    elif pk.get("calibrated"):
        power_note = "Power predicted from a model fitted to earlier measurements."
    else:
        power_note = ("Power is <b>estimated</b> from CPU/GPU load and the CPU's rated power "
                      "({} W), because this machine doesn't expose a power meter while "
                      "plugged in. Unplug for a while during recording to calibrate.").format(
                          fmt(specs.get("cpu_tdp_w"), 0))

    cards = [
        ("Recorded", span(s["start"], s["end"])),
        ("Windows with data", fmt(s["windows"], 0)),
        ("Windows with Claude", fmt(s["active_windows"], 0)),
        ("Output tokens", fmt(s["output_tokens"], 0)),
        ("Energy used", fmt(s["energy_wh"], 1, " Wh")),
        ("Avg power", fmt(s["avg_power_w"], 1, " W")),
        ("Idle power", fmt(s["idle_power_w"], 1, " W")),
        ("Extra energy w/ Claude", fmt(s["extra_energy_wh"], 2, " Wh")),
    ]
    if s.get("remote_energy_wh") is not None:
        cards.append(("Datacenter est.*", fmt(s["remote_energy_wh"], 1, " Wh")))
    card_html = "".join('<div class="card"><div class="k">{}</div><div class="v">{}</div></div>'
                        .format(esc(k), v) for k, v in cards)

    m = result["marginal"]
    marg_rows = "".join(
        "<tr><td>{}</td><td class='num'>{}</td><td class='num'>± {}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td></tr>".format(
            esc(v["unit"]), fmt(v["slope"], 4), fmt(v["ci95"], 4), fmt(v["r2"], 3), fmt_p(v["p"]))
        for v in m.values())

    avi = result["active_vs_idle"]
    avi_rows = "".join(
        "<tr><td>{}</td><td class='num'>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td></tr>".format(
            esc(r["label"]), fmt(r["active"]), fmt(r["idle"]),
            ("+" if (r["diff"] or 0) > 0 else "") + fmt(r["diff"]), fmt_p(r["p"]))
        for r in avi["rows"])

    model_rows = "".join(
        "<tr><td>{}</td><td class='num'>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td><td class='num'>{}</td></tr>".format(
            esc(name), fmt(v["requests"], 0), fmt(v["input"], 0), fmt(v["output"], 0),
            fmt(v["cache_write"], 0), fmt(v["cache_read"], 0))
        for name, v in sorted(result["per_model"].items(), key=lambda kv: -kv[1]["output"]))
    if not model_rows:
        model_rows = "<tr><td colspan='6' class='muted'>No Claude requests inside recorded time.</td></tr>"

    gpu_txt = "; ".join("{}{}".format(g["name"], " ({})".format(g["vram"]) if g.get("vram") else "")
                        for g in specs.get("gpus", [])) or "none detected"
    spec_rows = [
        ("OS", "{} ({})".format(specs.get("os"), specs.get("machine"))),
        ("CPU", specs.get("cpu")),
        ("Cores", "{} physical / {} logical".format(specs.get("cores_physical"), specs.get("cores_logical"))),
        ("Max clock", fmt(specs.get("cpu_max_mhz"), 0, " MHz")),
        ("Rated CPU power", fmt(specs.get("cpu_tdp_w"), 0, " W (estimated from model name)")),
        ("RAM", fmt(specs.get("ram_total_gb"), 1, " GB")),
        ("GPU", gpu_txt),
        ("Battery", "yes" if specs.get("has_battery") else "no"),
    ]
    spec_html = "".join("<tr><th>{}</th><td>{}</td></tr>".format(esc(k), esc(v)) for k, v in spec_rows)

    best_lag = max((r for r in result["lags"] if r["r"] is not None),
                   key=lambda r: r["r"], default=None)
    lag_note = ""
    if best_lag:
        lag_note = ("Strongest at lag {:+d} ({:+.2f}). A positive lag means power rises "
                    "<i>after</i> tokens arrive.").format(best_lag["lag"], best_lag["r"])

    remote_note = ""
    if s.get("remote_energy_wh") is not None:
        remote_note = ("<p class='small muted'>* Datacenter figure uses your own assumption of "
                       "{} Wh per 1,000 output tokens. Anthropic doesn't publish per-token "
                       "energy, so treat it as a what-if, not a measurement.</p>").format(
                           fmt(s["remote_wh_assumed_per_1k"], 4))

    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tokenwatt report</title><style>{css}</style></head>
<body><main>
<h1>tokenwatt report</h1>
<div class="muted small">Generated {now} · {win}-second windows · tokenwatt {ver}</div>
<div class="lead">{verdict}</div>
<div class="cards">{cards}</div>
<p class="small muted">{power_note}</p>
{remote_note}

<h2>Timeline</h2>
<div class="panel">{timeline}
<div class="legend"><span><i style="background:var(--accent)"></i>Power (W, left)</span>
<span><i style="background:var(--tok);opacity:.55"></i>Output tokens (right)</span></div></div>

<h2>Tokens vs power</h2>
<div class="panel">{scatter}<div class="legend small muted">Each dot is one window. Dashed line: least-squares fit.</div></div>

<h2>Correlation matrix</h2>
<p class="small muted">Spearman rank correlation per window (hover for Pearson, p-value and sample size).
p-values use an effective sample size that accounts for neighbouring windows being alike.</p>
{heat}

<h2>With Claude vs without</h2>
<p class="small muted">{aw} windows with at least one request, {iw} without. Welch's t-test.</p>
<div class="scroll"><table><thead><tr><th>Metric</th><th class="num">With Claude</th><th class="num">Without</th>
<th class="num">Difference</th><th class="num">p</th></tr></thead><tbody>{avi}</tbody></table></div>

<h2>Marginal energy</h2>
<p class="small muted">Slope of energy per window against token volume, on this computer only.</p>
<div class="scroll"><table><thead><tr><th>Measure</th><th class="num">Slope</th><th class="num">95% CI</th>
<th class="num">R²</th><th class="num">p</th></tr></thead><tbody>{marg}</tbody></table></div>

<h2>Lag</h2>
<div class="panel">{lag}<p class="small muted">{lag_note}</p></div>

<h2>Models</h2>
<div class="scroll"><table><thead><tr><th>Model</th><th class="num">Requests</th><th class="num">Input</th>
<th class="num">Output</th><th class="num">Cache write</th><th class="num">Cache read</th></tr></thead>
<tbody>{models}</tbody></table></div>

<h2>This computer</h2>
<table>{specs}</table>

<h2>How to read this</h2>
<ul class="small">
<li>Claude runs in Anthropic's datacenters. The energy here is what <b>your</b> computer spends while you use it
(the app or terminal, network, rendering, tools it runs), not the cost of the model itself.</li>
<li>Correlation is not causation. Other programs you run at the same time push power up too; the
"Claude apps CPU" column isolates processes named Claude.</li>
<li>Token counts come from Claude Code's local logs. Usage in the claude.ai website or other apps
isn't logged on disk and won't appear unless you import a CSV.</li>
</ul>
<footer>tokenwatt {ver} · data stays on your computer</footer>
</main></body></html>""".format(
        css=CSS, now=datetime.now().strftime("%Y-%m-%d %H:%M"), win=s["window_s"], ver=__version__,
        verdict=verdict(result), cards=card_html, power_note=power_note, remote_note=remote_note,
        timeline=timeline_svg(ws, s["window_s"]), scatter=scatter_svg(ws),
        heat=corr_table(result["correlations"]), aw=avi["active_windows"], iw=avi["idle_windows"],
        avi=avi_rows, marg=marg_rows, lag=lag_svg(result["lags"]), lag_note=lag_note,
        models=model_rows, specs=spec_html)
