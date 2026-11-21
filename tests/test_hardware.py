import pytest

from tokenwatt import cli, sampler, specs, store

IOREG_DISCHARGING = '''
+-o AppleSmartBattery  <class AppleSmartBattery>
    {
      "ExternalConnected" = No
      "Voltage" = 11800
      "InstantAmperage" = 18446744073709550616
      "LegacyBatteryInfo" = {"Amperage"=18446744073709550616,"Voltage"=11800}
    }
'''

IOREG_PLUGGED = IOREG_DISCHARGING.replace("= No", "= Yes").replace(
    '"InstantAmperage" = 18446744073709550616', '"InstantAmperage" = 435')


def test_ioreg_battery_discharging():
    info = sampler.parse_ioreg_battery(IOREG_DISCHARGING)
    assert info == {"voltage_mv": 11800, "current_ma": -1000, "external": False}


def test_ioreg_battery_plugged():
    info = sampler.parse_ioreg_battery(IOREG_PLUGGED)
    assert info["external"] is True and info["current_ma"] == 435


def test_ioreg_gpu():
    text = '"PerformanceStatistics" = {"Device Utilization %"=7,"Renderer Utilization %"=3}\n' \
           '"PerformanceStatistics" = {"Device Utilization %"=31}'
    assert sampler.parse_ioreg_gpu(text) == 31.0
    assert sampler.parse_ioreg_gpu("nothing here") is None


@pytest.mark.parametrize("name,tdp", [
    ("Intel(R) Core(TM) i5-7267U CPU @ 3.10GHz", 28),
    ("Intel(R) Core(TM) i7-8550U CPU @ 1.80GHz", 15),
    ("Intel(R) Core(TM) i9-13900K", 125),
    ("AMD Ryzen 7 5800H with Radeon Graphics", 45),
    ("AMD Ryzen 9 7950X3D 16-Core Processor", 125),
    ("Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz", 65),
    ("Apple M2 Pro", 30),
    ("Apple M1", 20),
    ("something odd", 35),
])
def test_tdp_guess(name, tdp):
    assert specs.guess_tdp(name) == tdp


def test_estimate_watts_scales_with_load():
    idle = sampler.estimate_watts(0, 0, 28, True)
    busy = sampler.estimate_watts(100, 0, 28, True)
    assert idle == pytest.approx(5 + 28 * 0.08)
    assert busy == pytest.approx(5 + 28)
    assert sampler.estimate_watts(50, 50, 28, False, gpu_watts=120) > 120


def test_real_sampler_produces_a_row(tmp_path):
    s = sampler.Sampler(tdp=15, laptop=True)
    row = s.sample()
    con = store.connect(tmp_path / "db.sqlite")
    store.insert_sample(con, row)
    assert 0 <= row["cpu_pct"] <= 100
    assert row["watts_est"] > 0
    assert len(store.samples_between(con)) == 1


def test_works_without_cpu_freq(tmp_path, monkeypatch):
    # psutil has no cpu_freq on Apple Silicon Macs
    monkeypatch.delattr(sampler.psutil, "cpu_freq", raising=False)
    info = specs.collect()
    assert info["cpu_max_mhz"] is None
    row = sampler.Sampler(tdp=20, laptop=True).sample()
    assert row["freq_mhz"] is None


def test_cli_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "e2e.db")
    logs = tmp_path / "logs" / "p"
    logs.mkdir(parents=True)
    (logs / "x.jsonl").write_text("")
    assert cli.main(["--db", db, "record", "-i", "0.5", "-d", "1.2", "-q"]) == 0
    assert cli.main(["--db", db, "import", "--path", str(tmp_path / "logs")]) == 0
    out = tmp_path / "r.html"
    assert cli.main(["--db", db, "report", "--no-import", "-o", str(out)]) == 0
    assert out.read_text().startswith("<!doctype html>")
    assert cli.main(["--db", db, "status"]) == 0
    assert cli.main(["--db", db, "export", "--no-import", "-o", str(tmp_path / "w.csv")]) == 0
    capsys.readouterr()


def test_parse_when():
    import time
    assert abs(cli.parse_when("2h") - (time.time() - 7200)) < 2
    with pytest.raises(Exception):
        cli.parse_when("whenever")
