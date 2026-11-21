"""Reads token usage from Claude Code's local session logs (and from CSV exports).

Claude Code writes one JSON object per line to ~/.claude/projects/<project>/<session>.jsonl.
Assistant messages carry a `usage` block. A single response can be logged several
times while it streams, so rows are keyed by message id and the last copy wins.
"""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tokenwatt import store


def default_roots():
    roots = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(os.pathsep):
            roots.append(Path(part).expanduser() / "projects")
    roots.append(Path.home() / ".claude" / "projects")
    roots.append(Path.home() / ".config" / "claude" / "projects")
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen and r.is_dir():
            seen.add(key)
            out.append(r)
    return out


def parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000 if v > 1e11 else v
    s = str(value).strip()
    try:
        return parse_ts(float(s))
    except ValueError:
        pass
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_line(line, project=""):
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant":
        return None
    msg = d.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    msg_id = msg.get("id") or d.get("requestId") or d.get("uuid")
    ts = parse_ts(d.get("timestamp"))
    if not msg_id or ts is None:
        return None
    model = msg.get("model") or ""
    if model == "<synthetic>":
        return None
    return {
        "msg_id": msg_id,
        "ts": ts,
        "model": model,
        "session": d.get("sessionId") or "",
        "project": project,
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "cache_write": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "source": "claude-code",
    }


def read_file(path, project="", start=0):
    """Parse a log from byte `start`. Returns (rows, offset just past the last full line)."""
    rows = {}
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    end = data.rfind(b"\n") + 1  # a half-written last line is left for next time
    for raw in data[:end].splitlines():
        if b'"usage"' not in raw:
            continue
        row = parse_line(raw.decode("utf-8", errors="replace"), project)
        if row:
            # same message logged again while streaming: keep the first
            # timestamp but the latest (largest) counts
            old = rows.get(row["msg_id"])
            if old:
                row["ts"] = min(old["ts"], row["ts"])
            rows[row["msg_id"]] = row
    return list(rows.values()), start + end


def import_claude_code(con, roots=None, force=False):
    roots = roots or default_roots()
    files = scanned = added = 0
    for root in roots:
        for path in sorted(Path(root).rglob("*.jsonl")):
            files += 1
            try:
                st = path.stat()
            except OSError:
                continue
            key = str(path)
            start = 0
            if not force:
                seen = con.execute("SELECT size, mtime, offset FROM seen_files WHERE path = ?",
                                   (key,)).fetchone()
                if seen:
                    if seen["size"] == st.st_size and seen["mtime"] == st.st_mtime:
                        continue
                    if st.st_size >= seen["offset"]:
                        start = seen["offset"]  # file grew: only read the new part
            project = path.relative_to(root).parts[0] if path.parent != Path(root) else ""
            try:
                rows, offset = read_file(path, project, start)
            except OSError:
                continue
            scanned += 1
            if rows:
                before = con.total_changes
                store.upsert_tokens(con, rows)
                added += con.total_changes - before
            con.execute("INSERT OR REPLACE INTO seen_files(path, size, mtime, offset) "
                        "VALUES (?, ?, ?, ?)", (key, st.st_size, st.st_mtime, offset))
            con.commit()
    return {"files": files, "scanned": scanned, "rows": added}


CSV_ALIASES = {
    "ts": ("timestamp", "time", "date", "created_at", "ts"),
    "input": ("input_tokens", "input", "prompt_tokens", "uncached_input_tokens"),
    "output": ("output_tokens", "output", "completion_tokens"),
    "cache_write": ("cache_creation_input_tokens", "cache_write", "cache_creation_tokens"),
    "cache_read": ("cache_read_input_tokens", "cache_read", "cache_read_tokens"),
    "model": ("model", "model_name"),
}


def import_csv(con, path):
    """Any CSV with a timestamp column and token columns, e.g. an API usage export."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in reader.fieldnames or []}
        pick = {}
        for field, names in CSV_ALIASES.items():
            for n in names:
                if n in cols:
                    pick[field] = cols[n]
                    break
        if "ts" not in pick or not ({"input", "output"} & pick.keys()):
            raise ValueError("CSV needs a timestamp column and input/output token columns "
                             "(found: {})".format(", ".join(reader.fieldnames or [])))
        rows = []
        for i, rec in enumerate(reader):
            ts = parse_ts(rec.get(pick["ts"]))
            if ts is None:
                continue
            row = {"msg_id": "csv:{}:{}".format(Path(path).name, i), "ts": ts,
                   "model": rec.get(pick.get("model", ""), "") or "", "session": "",
                   "project": "", "source": "csv"}
            for field in ("input", "output", "cache_write", "cache_read"):
                row[field] = _int(rec.get(pick[field])) if field in pick else 0
            rows.append(row)
    store.upsert_tokens(con, rows)
    return len(rows)


def _int(v):
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0
