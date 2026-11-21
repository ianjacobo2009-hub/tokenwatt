import json

from tokenwatt import store, tokens


def line(msg_id, ts, out, model="claude-sonnet-5-5", kind="assistant", **usage):
    u = {"input_tokens": 3, "output_tokens": out,
         "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000}
    u.update(usage)
    return json.dumps({"type": kind, "timestamp": ts, "sessionId": "s1", "requestId": "r-" + msg_id,
                       "message": {"id": msg_id, "model": model, "usage": u}})


def test_parse_ts_variants():
    assert tokens.parse_ts("2026-11-21T19:46:20.996Z") == tokens.parse_ts("2026-11-21T19:46:20.996+00:00")
    assert tokens.parse_ts(1795291231000) == 1795291231
    assert tokens.parse_ts("1795291231") == 1795291231
    assert tokens.parse_ts("not a date") is None


def test_streaming_duplicates_keep_latest_counts(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join([
        line("m1", "2026-11-21T10:00:00Z", 10),
        line("m1", "2026-11-21T10:00:02Z", 250),
        line("m2", "2026-11-21T10:01:00Z", 40),
        json.dumps({"type": "user", "message": {"content": "hi"}}),
        "{broken json",
        line("m3", "2026-11-21T10:02:00Z", 0, model="<synthetic>"),
    ]))
    rows = {r["msg_id"]: r for r in tokens.read_file(f, "proj")[0]}
    assert set(rows) == {"m1", "m2"}
    assert rows["m1"]["output"] == 250
    assert rows["m1"]["ts"] == tokens.parse_ts("2026-11-21T10:00:00Z")
    assert rows["m1"]["cache_read"] == 1000
    assert rows["m2"]["project"] == "proj"


def test_import_is_incremental(tmp_path):
    root = tmp_path / "projects"
    (root / "projA").mkdir(parents=True)
    log = root / "projA" / "a.jsonl"
    log.write_text(line("m1", "2026-11-21T10:00:00Z", 5) + "\n")
    con = store.connect(tmp_path / "db.sqlite")

    first = tokens.import_claude_code(con, roots=[root])
    assert first["scanned"] == 1
    again = tokens.import_claude_code(con, roots=[root])
    assert again["scanned"] == 0

    with open(log, "a") as f:
        f.write(line("m2", "2026-11-21T10:05:00Z", 7) + "\n")
    tokens.import_claude_code(con, roots=[root])
    assert con.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 2
    assert con.execute("SELECT project FROM tokens LIMIT 1").fetchone()[0] == "projA"


def test_subagent_logs_are_found(tmp_path):
    root = tmp_path / "projects"
    sub = root / "projA" / "session" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text(line("m9", "2026-11-21T10:00:00Z", 9) + "\n")
    con = store.connect(tmp_path / "db.sqlite")
    tokens.import_claude_code(con, roots=[root])
    row = con.execute("SELECT project, output FROM tokens").fetchone()
    assert tuple(row) == ("projA", 9)


def test_csv_import(tmp_path):
    p = tmp_path / "usage.csv"
    p.write_text("Timestamp,Model,Input_Tokens,Output_Tokens\n"
                 "2026-11-21T10:00:00Z,claude-opus-5-5,\"1,200\",300\n"
                 ",x,1,1\n")
    con = store.connect(tmp_path / "db.sqlite")
    assert tokens.import_csv(con, p) == 1
    r = con.execute("SELECT input, output, model, source FROM tokens").fetchone()
    assert tuple(r) == (1200, 300, "claude-opus-5-5", "csv")


def test_csv_without_columns_raises(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n")
    con = store.connect(tmp_path / "db.sqlite")
    try:
        tokens.import_csv(con, p)
    except ValueError as e:
        assert "timestamp" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_growing_file_read_incrementally_and_split_stream_merged(tmp_path):
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    log = root / "p" / "a.jsonl"
    con = store.connect(tmp_path / "db.sqlite")
    log.write_text(line("m1", "2026-11-21T10:00:00Z", 10) + "\n" + line("m1", "2026-11-21T10:00:01Z", 50)[:20])
    tokens.import_claude_code(con, roots=[root])
    assert con.execute("SELECT output FROM tokens").fetchone()[0] == 10

    # finish the half-written line and add the final streamed copy
    log.write_text(line("m1", "2026-11-21T10:00:00Z", 10) + "\n"
                   + line("m1", "2026-11-21T10:00:01Z", 50) + "\n"
                   + line("m1", "2026-11-21T10:00:03Z", 90) + "\n")
    tokens.import_claude_code(con, roots=[root])
    row = con.execute("SELECT output, ts FROM tokens").fetchone()
    assert row[0] == 90
    assert row[1] == tokens.parse_ts("2026-11-21T10:00:00Z")
    offset = con.execute("SELECT offset FROM seen_files").fetchone()[0]
    assert offset == log.stat().st_size


def test_truncated_file_is_reread(tmp_path):
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    log = root / "p" / "a.jsonl"
    con = store.connect(tmp_path / "db.sqlite")
    log.write_text(line("m1", "2026-11-21T10:00:00Z", 10) + "\n" + line("m2", "2026-11-21T10:01:00Z", 10) + "\n")
    tokens.import_claude_code(con, roots=[root])
    log.write_text(line("m3", "2026-11-21T10:02:00Z", 5) + "\n")
    tokens.import_claude_code(con, roots=[root])
    assert con.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 3
