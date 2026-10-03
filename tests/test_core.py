import numpy as np
import pandas as pd

from dipcast.model.features import daily_rain_features, spill_days
from dipcast.model.transport import combine_daily, risk_label, river_velocity


def test_spill_days_explodes_multi_day_events():
    ev = pd.DataFrame({
        "site_id": ["A", "A", "B"],
        "event_start": pd.to_datetime(["2025-01-01 23:00", "2025-01-01 23:30", "2025-03-05 10:00"], utc=True),
        "event_end": pd.to_datetime(["2025-01-03 01:00", "2025-01-01 23:45", None], utc=True),
    })
    sd = spill_days(ev)
    a = sd[sd.site_id == "A"].day.dt.strftime("%Y-%m-%d").tolist()
    assert a == ["2025-01-01", "2025-01-02", "2025-01-03"]
    assert sd[sd.site_id == "B"].day.dt.strftime("%Y-%m-%d").tolist() == ["2025-03-05"]


def test_daily_rain_features_windows_and_api():
    t = pd.date_range("2025-01-01", periods=24 * 5, freq="h", tz="UTC")
    p = np.zeros(len(t)); p[24 + 3] = 12.0; p[24 + 4] = 6.0  # one burst on day 2
    df = pd.DataFrame({"cell_lat": 54.2, "cell_lon": -2.6, "time": t, "precip_mm": p})
    d = daily_rain_features(df).set_index("day")
    assert d.loc["2025-01-02", "rain_d"] == 18.0
    assert d.loc["2025-01-02", "max3h_d"] == 18.0
    assert d.loc["2025-01-03", "rain_d1"] == 18.0
    assert d.loc["2025-01-04", "rain_3d"] == 18.0
    assert d.loc["2025-01-05", "rain_3d"] == 0.0
    assert abs(d.loc["2025-01-03", "api"] - 18.0 * 0.85) < 1e-9


def test_combine_daily_shifts_by_travel_time():
    p = np.array([[1.0, 0.0, 0.0]])            # spill certain on day 0
    w = np.array([0.5])
    r = combine_daily(p, w, np.array([0.0]))    # instant arrival
    assert np.allclose(r, [0.5, 0.0, 0.0])
    r = combine_daily(p, w, np.array([36.0]))   # 1.5 days: split across days 1 and 2
    assert np.allclose(r, [0.0, 0.25, 0.25])


def test_combine_daily_independent_sources():
    p = np.array([[0.5, 0.5], [0.5, 0.5]])
    w = np.array([1.0, 1.0])
    r = combine_daily(p, w, np.zeros(2))
    assert np.allclose(r, [0.75, 0.75])


def test_labels_and_velocity():
    assert risk_label(0.05) == "low" and risk_label(0.9) == "very high"
    assert river_velocity(None) == 0.5
    assert abs(river_velocity(0.0) - 0.3) < 1e-9 and abs(river_velocity(1.0) - 1.0) < 1e-9 and abs(river_velocity(5.0) - 1.35) < 1e-9


def test_ecoli_features_and_rain_windows():
    from dipcast.model.ecoli import FEATURES, EcoliModel, features, rain_windows

    t = pd.date_range("2025-06-01", periods=24 * 4, freq="h", tz="Europe/London")
    p = np.zeros(len(t)); p[30] = 5.0; p[60] = 7.0     # day 2 06:00 and day 3 12:00
    hourly = pd.Series(p, index=t)
    ends = pd.DatetimeIndex([pd.Timestamp("2025-06-03 12:00", tz="Europe/London"),
                             pd.Timestamp("2025-06-04 12:00", tz="Europe/London")])
    r48, r24 = rain_windows(hourly, ends)
    # windows are (t - 48 h, t]: a value stamped H is the hour ending at H, so the 12:00 burst on day 3
    # is the last hour of the windows ending 06-03 12:00 and falls just outside the 24 h window ending 06-04 12:00
    assert np.allclose(r48, [12.0, 7.0]) and np.allclose(r24, [7.0, 0.0])
    X = features([0.0, 20.0], [0.0, 10.0], [0.1, 0.1], [0.0, 0.0], ends)
    assert list(X.columns) == FEATURES
    # a positive rain coefficient must raise the probability with more rain, all else equal
    m = EcoliModel({f: 0.0 for f in FEATURES} | {"lrain48": 1.0}, -2.0,
                   {f: 0.0 for f in FEATURES}, {f: 1.0 for f in FEATURES}, {})
    q = m.predict(X)
    assert q[1] > q[0] and 0 < q[0] < 1


def test_ecoli_season_term_is_held_outside_the_sampling_season():
    from dipcast.model.ecoli import features, season_day

    days = pd.to_datetime(["2026-05-01", "2026-07-15", "2026-09-30", "2026-10-15", "2026-12-15", "2027-01-20",
                           "2027-02-10", "2027-04-30", "2028-11-01"]).tz_localize("Europe/London")
    doy = season_day(days)
    assert list(doy[:3]) == [121, 196, 273]              # inside the season: unchanged
    assert list(doy[3:6]) == [273, 273, 273]             # October to January: 30 September
    assert list(doy[6:8]) == [121, 121]                  # February to April: 1 May
    assert doy[8] == 274                                 # 2028 is a leap year: 30 September is day 274
    X = features(np.full(len(days), 5.0), np.full(len(days), 2.0), np.full(len(days), 0.02), np.zeros(len(days)), days)
    assert X.loc[3:5, "doy_sin"].nunique() == 1 and X.loc[3, "doy_cos"] == X.loc[2, "doy_cos"]


def test_observed_spill_days_and_date_join():
    """A forecast log day (timestamp, as DuckDB returns DATE) must join to observed spill days (date)."""
    from dipcast.forecast_log import observed_spill_days

    hist = pd.DataFrame({
        "site_id": ["A", "B"], "status": [0, 1],
        "latest_event_start": pd.to_datetime(["2026-09-12 22:00", "2026-09-14 09:00"], utc=True),
        "latest_event_end": [pd.Timestamp("2026-09-13 03:00", tz="UTC"), pd.NaT],
        "fetched_at": pd.to_datetime(["2026-09-13 06:00", "2026-09-14 12:00"], utc=True),
    })
    obs = observed_spill_days(hist)
    assert sorted(map(str, obs[obs.site_id == "A"].day)) == ["2026-09-12", "2026-09-13"]
    assert sorted(map(str, obs[obs.site_id == "B"].day)) == ["2026-09-14"]   # open-ended: ends at poll time
    fc = pd.DataFrame({"site_id": ["A", "A"], "target_day": pd.to_datetime(["2026-09-13", "2026-09-14"])})
    fc["target_day"] = pd.to_datetime(fc["target_day"]).dt.date
    m = fc.merge(obs.assign(y=1), left_on=["site_id", "target_day"], right_on=["site_id", "day"], how="left")
    assert m["y"].fillna(0).tolist() == [1, 0]


def test_ecoli_live_scoring_round_trip(tmp_path, monkeypatch):
    """Log a point forecast with an E. coli probability, plant a matching EA sample, score it."""
    import json

    from dipcast import config
    from dipcast import forecast_log as fl

    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "t.duckdb")
    sites = json.loads((config.RAW / "bathing_waters_inland.json").read_text())
    s = sites[0]
    issued = pd.Timestamp("2026-09-10 08:00", tz="Europe/London")
    days = pd.date_range("2026-09-09", periods=3, freq="D", tz="Europe/London")   # yesterday, today, tomorrow
    fl.log_forecast(issued, round(s["lat"], 5), round(s["lon"], 5), "river", "R", 0.1, days, np.array([0.1, 0.2, 0.3]),
                    pd.DataFrame(), np.zeros((0, 3)), np.zeros((0, 3)),
                    p_ecoli=np.array([np.nan, 0.6, 0.4]), rain_48h=np.array([np.nan, 12.0, 3.0]))
    samples = pd.DataFrame({"bw_id": [s["id"], s["id"]], "name": s["name"], "kind": s["kind"],
                            "sample_time": pd.to_datetime(["2026-09-10 11:00", "2026-09-11 11:00"]).tz_localize("Europe/London"),
                            "ecoli": [1500, 200]})
    samples.to_parquet(tmp_path / fl.ECOLI_SAMPLES, index=False)   # fresh file: no network fetch
    out = fl.verify_ecoli(as_of=pd.Timestamp("2026-09-12").date())
    assert out["n_scored"] == 2 and out["n_samples"] == 2
    by_lead = {r["lead"]: r for r in out["by_lead"]}
    assert by_lead[0]["base_rate"] == 1.0 and abs(by_lead[0]["brier"] - 0.16) < 1e-9     # 0.6 vs exceedance
    assert by_lead[1]["base_rate"] == 0.0 and abs(by_lead[1]["brier"] - 0.16) < 1e-9     # 0.4 vs clean
    assert out["recent"][0]["ecoli"] == 200 and out["recent"][1]["forecast"] == 0.6


def _repeated_overflow() -> pd.DataFrame:
    """Three upstream rows for two overflows: ST1 is listed twice. The ids repeated in the 3 Oct 2026
    12:06 UTC build were not captured; they are inferred to be Severn Trent and Yorkshire Water rows."""
    return pd.DataFrame({
        "site_id": ["ST1", "ST2", "ST1"], "company": "Severn Trent Water", "site_name": ["Weir CSO", "Bridge SO", "Weir CSO"],
        "receiving_watercourse": "RIVER SEVERN", "lat": [52.71, 52.74, 52.71], "lon": [-2.75, -2.80, -2.75],
        "status": 0, "has_live": True, "latest_event_start": pd.NaT, "latest_event_end": pd.NaT,
        "lta_spills": [30.0, 10.0, 30.0], "spill_hours": 100.0, "edm_operational_pct": 95.0, "snap_confidence": "high",
        "distance_m": [2000.0, 5000.0, 2000.0], "lake_distance_m": 0.0, "travel_h": [1.0, 3.0, 1.0],
        "weight": [0.5, 0.3, 0.5]})


def test_a_repeated_overflow_id_does_not_fail_the_forecast(monkeypatch):
    """On 3 Oct 2026 12:06 UTC four spots failed with "Index contains duplicate entries, cannot reshape":
    an overflow upstream of each was listed twice and spill_probabilities pivots on site_id. A repeated id
    must still give a forecast, and each of its rows the same spill probabilities."""
    from dipcast.model import forecast
    from dipcast.model.transport import PinLocation

    ov = _repeated_overflow()

    def rain(cells):
        t = pd.date_range(pd.Timestamp.now(tz="UTC").floor("D") - pd.Timedelta(days=4), periods=24 * 12, freq="h")
        return pd.concat([pd.DataFrame({"cell_lat": a, "cell_lon": b, "time": t, "precip_mm": 0.4}) for a, b in cells],
                         ignore_index=True)

    monkeypatch.setattr(forecast, "_net", lambda: None)
    monkeypatch.setattr(forecast, "_overflows", lambda: ov)
    monkeypatch.setattr(forecast, "_model", lambda: None)   # climatology: no spill model file needed
    monkeypatch.setattr(forecast, "locate_pin", lambda *a, **k: PinLocation(mode="river", x=0.0, y=0.0, snap=None,
                                                                             watercourse="River Severn"))
    monkeypatch.setattr(forecast, "upstream_overflows", lambda *a, **k: ov.copy())
    monkeypatch.setattr(forecast, "fetch_forecast", rain)
    monkeypatch.setattr(forecast.ecoli, "load", lambda: None)
    out = forecast.forecast_point(52.70, -2.75, days_ahead=4, gauge=False, log_to_store=False)

    assert "error" not in out and len(out["days"]) == 5
    assert all(d["risk"] is not None and d["data_status"] == "ok" for d in out["days"])
    assert out["upstream_summary"]["overflows"] == 3
    st1 = [c["p_spill_days"] for c in out["contributors"] if c["site_id"] == "ST1"]
    assert len(st1) == 2 and st1[0] == st1[1]


def test_overflow_table_keeps_the_most_recent_row_of_a_repeated_id(caplog):
    """The 3 Oct 2026 02:59 snapshot listed Anglian Water's AWS00528 twice. One row each, the latest
    fetched_at (ST9 has an older row and a newer one)."""
    from dipcast.overflows import duplicate_site_ids, one_row_per_overflow

    t = pd.Timestamp("2026-10-03 02:59", tz="UTC")
    live = pd.DataFrame({"site_id": ["AWS00528", "X1", "AWS00528", "ST9", "ST9"],
                         "status": [0, 0, 0, -3, 1],
                         "fetched_at": [t, t, t, t - pd.Timedelta(hours=3), t],
                         "last_updated": [t, t, t, pd.NaT, t]})
    assert duplicate_site_ids(live) == ["AWS00528", "ST9"]
    with caplog.at_level("WARNING"):
        one = one_row_per_overflow(live)
    assert one["site_id"].tolist() == ["X1", "AWS00528", "ST9"]   # snapshot order kept
    assert one.set_index("site_id").loc["ST9", "status"] == 1        # the fresh row, not the carried one
    assert "AWS00528, ST9" in caplog.text
    assert one_row_per_overflow(one) is one                          # nothing repeated: unchanged


def test_build_health_counts_repeated_overflow_ids():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_site

    good = {"name": "ok", "days": [{"data_status": "ok"}]}
    h = build_site.build_health([good] * 5)
    assert h["duplicate_overflow_ids"] == 0 and h["warnings"] == []
    h = build_site.build_health([good] * 5, duplicate_overflow_ids=["AWS00528"])
    assert h["duplicate_overflow_ids"] == 1 and len(h["warnings"]) == 1 and "AWS00528" in h["warnings"][0]


def test_a_cached_overflow_table_with_a_repeated_id_loads_one_row_each(tmp_path, monkeypatch):
    """--no-refresh reads the cached overflow table. One written before build_overflows dropped repeats
    (the 3 Oct 2026 02:59 state lists AWS00528 twice) must not count that overflow twice."""
    from dipcast import config, overflows

    t = pd.Timestamp("2026-10-03 02:59", tz="UTC")
    pd.DataFrame({"site_id": ["AWS00528", "X1", "AWS00528"], "weight": [0.5, 0.3, 0.5],
                  "fetched_at": [t, t, t]}).to_parquet(tmp_path / overflows.NAME, index=False)
    monkeypatch.setattr(config, "state_read", lambda name: tmp_path / name)
    assert sorted(overflows.load_overflows()["site_id"]) == ["AWS00528", "X1"]
