import argparse
import csv
import json
import re
import signal
import sys
import time
import webbrowser
from pathlib import Path

from tokenwatt import __version__, analyze, report, specs, store, tokens


def parse_when(value):
    """'90m', '24h', '7d' (ago) or an ISO date/time."""
    if value is None:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*", value)
    if m:
        mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[m.group(2)]
        return time.time() - float(m.group(1)) * mult
    ts = tokens.parse_ts(value)
    if ts is None:
        raise argparse.ArgumentTypeError("can't read time '{}'; try 24h, 7d or 2026-11-21".format(value))
    return ts


def load_specs(con, refresh=False):
    cached = store.get_meta(con, "specs")
    if cached and not refresh:
        return json.loads(cached)
    info = specs.collect()
    store.set_meta(con, "specs", json.dumps(info))
    return info


def cmd_specs(args, con):
    info = load_specs(con, refresh=True)
    if args.json:
        print(json.dumps(info, indent=2))
        return 0
    print("OS          {} ({})".format(info["os"], info["machine"]))
    print("CPU         {}".format(info["cpu"]))
    print("Cores       {} physical / {} logical".format(info["cores_physical"], info["cores_logical"]))
    if info["cpu_max_mhz"]:
        print("Max clock   {} MHz".format(info["cpu_max_mhz"]))
    print("CPU power   ~{:.0f} W (guessed from the model name, override with --tdp)".format(info["cpu_tdp_w"]))
    print("RAM         {} GB".format(info["ram_total_gb"]))
    for g in info["gpus"] or [{"name": "none detected"}]:
        print("GPU         {}{}".format(g["name"], "  ({})".format(g["vram"]) if g.get("vram") else ""))
    print("Battery     {}".format("yes" if info["has_battery"] else "no"))
    return 0


def cmd_record(args, con):
    from tokenwatt.sampler import Sampler

    info = load_specs(con)
    tdp = args.tdp or info["cpu_tdp_w"]
    sampler = Sampler(tdp=tdp, laptop=info["has_battery"])

    stop = {"now": False}

    def handle(sig, frame):
        stop["now"] = True

    signal.signal(signal.SIGINT, handle)
    try:
        signal.signal(signal.SIGTERM, handle)
    except (AttributeError, ValueError):
        pass

    end = time.monotonic() + args.duration if args.duration else None
    if not args.quiet:
        print("Recording every {}s to {}. Ctrl+C to stop.".format(args.interval, store.default_db_path()
              if not args.db else args.db))
        print("{:>8}  {:>5}  {:>6}  {:>5}  {:>5}  {:>8}".format(
            "time", "cpu%", "claude", "ram%", "gpu%", "watts"))
    count = 0
    next_at = time.monotonic() + args.interval
    while not stop["now"]:
        while not stop["now"] and time.monotonic() < next_at:
            time.sleep(min(0.2, max(0.0, next_at - time.monotonic())))
        if stop["now"]:
            break
        next_at += args.interval
        if time.monotonic() > next_at:  # we fell behind (laptop slept); don't burst
            next_at = time.monotonic() + args.interval
        s = sampler.sample()
        store.insert_sample(con, s)
        count += 1
        if not args.quiet:
            w = "{:.1f}".format(s["watts"]) if s["watts"] is not None else "~{:.1f}".format(s["watts_est"])
            print("{:>8}  {:>5.1f}  {:>6.1f}  {:>5.1f}  {:>5}  {:>8}".format(
                time.strftime("%H:%M:%S"), s["cpu_pct"], s["claude_cpu"] or 0, s["ram_pct"],
                "-" if s["gpu_pct"] is None else "{:.0f}".format(s["gpu_pct"]), w), flush=True)
        if end and time.monotonic() >= end:
            break
    if not args.quiet:
        print("\nSaved {} samples.".format(count))
    return 0


def do_import(args, con, quiet=False):
    roots = [Path(p).expanduser() for p in args.path] if getattr(args, "path", None) else None
    res = tokens.import_claude_code(con, roots=roots, force=getattr(args, "force", False))
    if not quiet:
        print("Claude Code logs: {} files, {} changed, {} rows written.".format(
            res["files"], res["scanned"], res["rows"]))
    return res


def cmd_import(args, con):
    if args.csv:
        n = tokens.import_csv(con, args.csv)
        print("Imported {} rows from {}.".format(n, args.csv))
        if not args.path:
            return 0
    do_import(args, con)
    return 0


def _analysis(args, con):
    if not args.no_import:
        do_import(args, con, quiet=True)
    samples = store.samples_between(con, args.since, args.until)
    toks = store.tokens_between(con, args.since, args.until)
    return analyze.run(samples, toks, window=args.window, remote_wh_per_1k=args.remote_wh_per_1k)


def cmd_analyze(args, con):
    res = _analysis(args, con)
    if args.json:
        res = dict(res)
        if not args.windows:
            res.pop("windows")
        print(json.dumps(res, indent=2, default=str))
        return 0
    s = res["summary"]
    if not s["windows"]:
        print("No hardware samples in that range. Start one with: tokenwatt record")
        return 1
    print("Windows: {} x {}s  ({} with Claude activity)".format(s["windows"], s["window_s"], s["active_windows"]))
    print("Output tokens: {:,}   All tokens: {:,}".format(s["output_tokens"], s["all_tokens"]))
    print("Energy: {:.2f} Wh   avg {:.1f} W   idle {}".format(
        s["energy_wh"], s["avg_power_w"],
        "{:.1f} W".format(s["idle_power_w"]) if s["idle_power_w"] is not None else "n/a"))
    pk = s["power_kinds"]
    print("Power source: " + ", ".join("{} {}".format(v, k) for k, v in sorted(pk.items())))
    if s["extra_energy_wh"] is not None:
        print("Extra energy while Claude was active: {:.3f} Wh".format(s["extra_energy_wh"]))
    if s["wh_per_1k_output_avg"] is not None:
        print("  = {:.4f} Wh per 1k output tokens on this machine".format(s["wh_per_1k_output_avg"]))
    print()
    print("{:<26} {:<20} {:>8} {:>8} {:>8}  {}".format("tokens", "hardware", "spearman", "pearson", "p", "strength"))
    for c in res["correlations"]:
        if c["spearman"] is None:
            continue
        print("{:<26} {:<20} {:>+8.2f} {:>+8.2f} {:>8}  {}".format(
            c["token_label"], c["hw_label"], c["spearman"], c["pearson"] or 0,
            "<0.001" if c["p"] is not None and c["p"] < 0.001 else
            ("{:.3f}".format(c["p"]) if c["p"] is not None else "-"), c["strength"]))
    return 0


def cmd_report(args, con):
    res = _analysis(args, con)
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(res, load_specs(con)), encoding="utf-8")
    print("Report written to {}".format(out.resolve()))
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_export(args, con):
    res = _analysis(args, con)
    cols = ["start", "cpu", "claude_cpu", "ram", "gpu", "power_w", "measured_share", "energy_wh",
            "requests", "input", "output", "cache_write", "cache_read", "fresh", "total"]
    out = Path(args.output).expanduser()
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_iso"] + cols)
        for row in res["windows"]:
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(row["start"]))]
                       + [row[c] for c in cols])
    print("Wrote {} windows to {}".format(len(res["windows"]), out.resolve()))
    return 0


def cmd_status(args, con):
    path = args.db or store.default_db_path()
    s = con.execute("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b, "
                    "SUM(watts IS NOT NULL) m FROM samples").fetchone()
    t = con.execute("SELECT COUNT(*) n, MIN(ts) a, MAX(ts) b, SUM(output) o FROM tokens").fetchone()

    def ts(v):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(v)) if v else "-"

    print("Database   {}".format(path))
    print("Samples    {:,}  ({} → {}), {:,} with measured power".format(s["n"], ts(s["a"]), ts(s["b"]), s["m"] or 0))
    print("Requests   {:,}  ({} → {}), {:,} output tokens".format(t["n"], ts(t["a"]), ts(t["b"]), t["o"] or 0))
    return 0


def cmd_app(args, con):
    con.close()
    try:
        from tokenwatt.gui.app import main as app_main
    except ImportError:
        print('The desktop app needs PySide6: pip install "tokenwatt[app]"', file=sys.stderr)
        return 1
    return app_main([sys.argv[0]])


def build_parser():
    p = argparse.ArgumentParser(
        prog="tokenwatt",
        description="Correlate Claude token usage with CPU, RAM, GPU and power on your computer.")
    p.add_argument("--version", action="version", version="tokenwatt " + __version__)
    p.add_argument("--db", help="database file (default ~/.tokenwatt/tokenwatt.db or $TOKENWATT_DB)")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    sp = sub.add_parser("specs", help="show this computer's hardware")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_specs)

    rp = sub.add_parser("record", help="sample CPU, RAM, GPU and power until stopped")
    rp.add_argument("-i", "--interval", type=float, default=5.0, help="seconds between samples (default 5)")
    rp.add_argument("-d", "--duration", type=float, help="stop after this many seconds")
    rp.add_argument("--tdp", type=float, help="your CPU's rated power in watts, if the guess is off")
    rp.add_argument("-q", "--quiet", action="store_true")
    rp.set_defaults(func=cmd_record)

    ip = sub.add_parser("import", help="read token usage from Claude Code logs or a CSV")
    ip.add_argument("--path", action="append", help="logs folder (default ~/.claude/projects); repeatable")
    ip.add_argument("--csv", help="CSV with a timestamp column and token columns")
    ip.add_argument("--force", action="store_true", help="re-read files even if unchanged")
    ip.set_defaults(func=cmd_import)

    def window_opts(x):
        x.add_argument("--since", type=parse_when, help="e.g. 24h, 7d or 2026-11-01")
        x.add_argument("--until", type=parse_when)
        x.add_argument("-w", "--window", type=int, default=60, help="window size in seconds (default 60)")
        x.add_argument("--remote-wh-per-1k", type=float,
                       help="optional: your assumption of datacenter Wh per 1k output tokens")
        x.add_argument("--no-import", action="store_true", help="skip refreshing token logs first")
        x.add_argument("--path", action="append", help=argparse.SUPPRESS)

    ap = sub.add_parser("analyze", help="print correlations in the terminal")
    window_opts(ap)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--windows", action="store_true", help="include per-window rows in --json output")
    ap.set_defaults(func=cmd_analyze)

    hp = sub.add_parser("report", help="write an HTML report with charts")
    window_opts(hp)
    hp.add_argument("-o", "--output", default="tokenwatt-report.html")
    hp.add_argument("--open", action="store_true", help="open it in the browser")
    hp.set_defaults(func=cmd_report)

    ep = sub.add_parser("export", help="write the per-window table as CSV")
    window_opts(ep)
    ep.add_argument("-o", "--output", default="tokenwatt-windows.csv")
    ep.set_defaults(func=cmd_export)

    gp = sub.add_parser("app", help="open the desktop app")
    gp.set_defaults(func=cmd_app)

    st = sub.add_parser("status", help="what's in the database")
    st.set_defaults(func=cmd_status)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0
    if getattr(args, "window", 60) < 5:
        parser.error("--window must be at least 5 seconds")
    if getattr(args, "interval", 5) < 0.5:
        parser.error("--interval must be at least 0.5 seconds")
    con = store.connect(args.db)
    try:
        return args.func(args, con)
    except BrokenPipeError:
        return 0
    except ValueError as e:
        print("error: {}".format(e), file=sys.stderr)
        return 2
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
