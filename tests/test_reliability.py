"""Tests for the 16 Sep 2026 reliability fixes: missing rain is unknown, not dry;
forecasts are scored as of a decision time and only where the overflow was
observed; a forecast issued after a sample does not count; the live forecast
looks back far enough for the longest travel time; a mostly-failed build does
not publish."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

TZ = "Europe/London"


# ---------------------------------------------------------------- missing rain
def _hourly(values, start="2025-06-01", n=None):
    n = n or len(values)
    t = pd.date_range(start, periods=n, freq="h", tz=TZ)
    return pd.Series(values, index=t, dtype=float)


def test_rain_windows_all_nan_is_unknown_not_zero():
    from dipcast.model.ecoli import rain_windows
    hourly = _hourly([np.nan] * 96)
    ends = pd.DatetimeIndex([pd.Timestamp("2025-06-03 12:00", tz=TZ)])
    r48, r24 = rain_windows(hourly, ends)
    assert np.isnan(r48[0]) and np.isnan(r24[0])


def test_rain_windows_partial_coverage_and_boundaries():
    from dipcast.model.ecoli import rain_windows
    vals = np.ones(96)
    hourly = _hourly(vals)
    t = pd.Timestamp("2025-06-03 12:00", tz=TZ)
    r48, r24, c48, c24 = rain_windows(hourly, pd.DatetimeIndex([t]), return_coverage=True)
    assert r48[0] == 48.0 and r24[0] == 24.0          # (t - 48 h, t]: exactly 48 and 24 hour stamps
    assert c48[0] == 1.0 and c24[0] == 1.0
    # 8 of the 48 hours missing (83% coverage) -> unknown; 3 missing (94%) -> a total, coverage reported
    gappy = hourly.copy(); gappy.iloc[40:48] = np.nan
    assert np.isnan(rain_windows(gappy, pd.DatetimeIndex([t]))[0][0])
    gappy = hourly.copy(); gappy.iloc[40:43] = np.nan
    r48, _, c48, _ = rain_windows(gappy, pd.DatetimeIndex([t]), return_coverage=True)
    assert r48[0] == 45.0 and abs(c48[0] - 45 / 48) < 1e-9
    # a window that starts before the series does is not covered
    early = pd.Timestamp("2025-06-02 06:00", tz=TZ)   # only 30 h of data before it
    r48, r24 = rain_windows(hourly, pd.DatetimeIndex([early]))
    assert np.isnan(r48[0]) and r24[0] == 24.0
    # a window ending after the series is not covered either
    late = pd.Timestamp("2025-06-05 12:00", tz=TZ)
    assert np.isnan(rain_windows(hourly, pd.DatetimeIndex([late]))[1][0])


def test_rain_windows_duplicates_and_off_hour_sample_time():
    from dipcast.model.ecoli import rain_windows
    hourly = _hourly(np.ones(96))
    dup = pd.concat([hourly, hourly.iloc[:60]])   # duplicated stamps must not double-count
    t = pd.Timestamp("2025-06-03 10:37", tz=TZ)          # EA samples are not on the hour
    r48, r24 = rain_windows(dup, pd.DatetimeIndex([t]))
    assert r48[0] == 48.0 and r24[0] == 24.0


def test_daily_rain_features_short_day_is_nan():
    from dipcast.model.features import daily_rain_features
    t = pd.date_range("2025-01-01", periods=24 * 3, freq="h", tz="UTC")
    p = np.ones(len(t)); p[24:24 + 10] = np.nan       # day 2 has only 14 finite hours
    df = pd.DataFrame({"cell_lat": 54.2, "cell_lon": -2.6, "time": t, "precip_mm": p})
    d = daily_rain_features(df).set_index("day")
    assert d.loc["2025-01-01", "rain_d"] == 24.0
    assert np.isnan(d.loc["2025-01-02", "rain_d"]) and np.isnan(d.loc["2025-01-02", "max3h_d"])
    assert d.loc["2025-01-03", "rain_d"] == 24.0 and np.isnan(d.loc["2025-01-03", "rain_d1"])


# ---------------------------------------------------------------- transport history
def test_history_window_covers_travel_time():
    from dipcast.model.transport import combine_daily, history_days, missing_share
    # The review's example: 48 h travel, constant spill probability. With the grid
    # starting only yesterday, today has no contribution; with the shared history
    # rule it does.
    travel = np.array([48.0]); w = np.array([0.5]); p_const = 0.4
    days_short = 1
    r = combine_daily(np.full((1, days_short + 5), p_const), w, travel)
    assert r[days_short] == 0.0
    hist = history_days(travel)
    assert hist == 3
    r = combine_daily(np.full((1, hist + 5), p_const), w, travel)
    assert abs(r[hist] - 0.2) < 1e-9
    assert history_days(np.array([])) == 1 and history_days(None) == 1
    # missing_share follows the same shift: a missing source day two days back hits today
    avail = np.ones((1, hist + 5), dtype=bool); avail[0, hist - 2] = False
    ms = missing_share(avail, w, travel)
    assert ms[hist] == 1.0 and ms[hist + 1] == 0.0


# ---------------------------------------------------------------- live scoring
def _state(tmp_path, monkeypatch):
    """Point both the state directory and its read fallback at an empty temp dir."""
    from dipcast import config
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(config, "PROCESSED", tmp_path)
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "t.duckdb")
    return config


def _log(fl, issued, days, ov, p, version="v-test"):
    fl.log_forecast(issued, 54.0, -2.0, "river", "R", 0.1, days, np.full(len(days), 0.1), ov,
                    np.full((len(ov), len(days)), p), np.full((len(ov), len(days)), p), version=version)


def _mask(hours):
    """Slot bitmask for observations on the hour at the given hours."""
    return int(sum(1 << (h * 2) for h in hours))


def test_verify_live_uses_decision_time_and_coverage(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    ov = pd.DataFrame({"site_id": ["A", "B"], "has_live": [True, True], "weight": [0.5, 0.5]})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)     # yesterday, today (14th), tomorrow
    # Two issues on the 14th: one before the 08:00 decision time, one late at night.
    _log(fl, pd.Timestamp("2026-09-14 07:30", tz=TZ), days, ov, 0.3)
    _log(fl, pd.Timestamp("2026-09-14 23:00", tz=TZ), days, ov, 0.9)
    hist = pd.DataFrame({"site_id": ["A", "B"], "company": ["X", "X"], "status": [0, 0],
                         "status_start": pd.to_datetime(["2026-09-01", "2026-09-01"], utc=True),
                         "latest_event_start": pd.to_datetime([None, None], utc=True),
                         "latest_event_end": pd.to_datetime([None, None], utc=True),
                         "fetched_at": pd.to_datetime(["2026-09-14 12:00", "2026-09-14 12:00"], utc=True)})
    hist.to_parquet(tmp_path / "live_history.parquet", index=False)
    # Coverage: A observed every 2 h on the 14th (12 polls, max gap 2 h), every 7 h on the
    # 15th (gap 7 h: passes the 8 h rule, fails the stricter 6 h one), once on the 16th;
    # B observed 12 times but all within 08:00-13:00 (gap from 13:00 to midnight: 11 h).
    cov = pd.DataFrame({"site_id": ["A", "A", "A", "B"],
                        "day": pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-14"]),
                        "n_known": [12, 6, 1, 12], "n_unknown": [0, 0, 0, 0], "n_stale": [0, 0, 0, 0],
                        "slots": [_mask(range(0, 24, 2)), _mask(range(0, 24, 7)), _mask([6]),
                                  int(sum(1 << s for s in range(16, 28)))]})
    cov.to_parquet(tmp_path / fl.COVERAGE_FILE, index=False)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    # A is scored for the 14th and 15th (lead 0 and 1) with the 07:30 forecast (0.3); B is not covered.
    assert out["n_candidates"] == 4 and out["n_uncovered"] == 2 and out["n_scored"] == 2
    assert out["missed_deadline"]["n"] == 0
    assert abs(out["overall"]["calibrated"]["brier"] - 0.09) < 1e-9     # (0.3 - 0)^2, not (0.9 - 0)^2
    assert {r["lead"] for r in out["by_lead"]} == {0, 1} and out["in_advance"]["n"] == 1
    # Strict rule keeps only the 14th (gap 2 h); the 15th's 7 h gap fails it.
    assert out["coverage_sensitivity"]["default"]["n"] == 2 and out["coverage_sensitivity"]["strict"]["n"] == 1
    assert out["rules"]["max_gap_h"] == 8.0 and out["rules"]["strict_gap_h"] == 6.0
    assert out["by_version"][0]["version"] == "v-test" and out["by_version"][0]["n"] == 2


def test_verify_live_reports_level_by_company_and_rain(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    ov = pd.DataFrame({"site_id": ["A", "B"], "has_live": [True, True], "weight": [0.5, 0.5]})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)
    rain = np.array([[0.0, 0.2, 12.0], [0.0, 3.0, np.nan]])     # B's rain for the 15th is missing
    fl.log_forecast(pd.Timestamp("2026-09-14 07:30", tz=TZ), 54.0, -2.0, "river", "R", 0.1, days, np.full(3, 0.1), ov,
                    np.full((2, 3), 0.4), np.full((2, 3), 0.4), version="v-test", rain_mm=rain)
    hist = pd.DataFrame({"site_id": ["A", "B"], "company": ["X", "Y"], "status": [0, 0],
                         "status_start": pd.to_datetime(["2026-09-01", "2026-09-01"], utc=True),
                         "latest_event_start": pd.to_datetime(["2026-09-14 10:00", None], utc=True),
                         "latest_event_end": pd.to_datetime(["2026-09-14 12:00", None], utc=True),
                         "fetched_at": pd.to_datetime(["2026-09-14 13:00", "2026-09-14 13:00"], utc=True)})
    hist.to_parquet(tmp_path / "live_history.parquet", index=False)
    pd.DataFrame({"site_id": ["A", "B"], "company": ["X", "Y"], "lta_spills": [36.5, 36.5]}).to_parquet(
        tmp_path / "overflows.parquet", index=False)
    every2h = _mask(range(0, 24, 2))
    pd.DataFrame({"site_id": ["A", "A", "A", "B", "B", "B"],
                  "day": pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16"] * 2),
                  "n_known": 12, "n_unknown": 0, "n_stale": 0, "slots": every2h}).to_parquet(tmp_path / fl.COVERAGE_FILE, index=False)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    # A spilled on the 14th; A on the 15th and B on both days did not. Every forecast was 0.4.
    assert out["n_scored"] == 4 and abs(out["overall"]["mean_forecast"] - 0.4) < 1e-9
    assert abs(out["overall"]["period_base_rate_brier"] - 0.1875) < 1e-9     # flat 0.25 against y = 1, 0, 0, 0
    comp = {r["company"]: r for r in out["by_company"]}
    assert comp["X"]["n_spill_days"] == 1 and abs(comp["X"]["flat_brier"] - 0.25) < 1e-9
    assert comp["Y"]["n_spill_days"] == 0 and comp["Y"]["flat_brier"] == 0.0 and abs(comp["Y"]["mean_forecast"] - 0.4) < 1e-9
    bands = {r["band"]: r for r in out["by_rain"]}
    assert set(bands) == {"under 1 mm", "1-5 mm", "10 mm or more"} and out["n_rain_unlogged"] == 1
    assert bands["under 1 mm"]["base_rate"] == 1.0 and bands["1-5 mm"]["base_rate"] == 0.0 and bands["10 mm or more"]["n"] == 1


def test_verify_live_missed_deadline_is_reported_not_scored(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    ov = pd.DataFrame({"site_id": ["A"], "has_live": [True], "weight": [0.5]})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)
    _log(fl, pd.Timestamp("2026-09-14 09:30", tz=TZ), days, ov, 0.3)     # first issue after 08:00
    hist = pd.DataFrame({"site_id": ["A"], "company": ["X"], "status": [0],
                         "status_start": pd.to_datetime(["2026-09-01"], utc=True),
                         "latest_event_start": pd.to_datetime([None], utc=True),
                         "latest_event_end": pd.to_datetime([None], utc=True),
                         "fetched_at": pd.to_datetime(["2026-09-14 12:00"], utc=True)})
    hist.to_parquet(tmp_path / "live_history.parquet", index=False)
    cov = pd.DataFrame({"site_id": ["A", "A", "A"], "day": pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16"]),
                        "n_known": [12, 12, 1], "n_unknown": 0, "n_stale": 0, "slots": [_mask(range(0, 24, 2))] * 3})
    cov.to_parquet(tmp_path / fl.COVERAGE_FILE, index=False)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    assert out["n_candidates"] == 2 and out["missed_deadline"]["n"] == 2 and out["n_scored"] == 0


def test_slot_stats_gaps():
    from dipcast.forecast_log import slot_stats
    st = slot_stats(np.array([0, _mask(range(0, 24, 2)), int(sum(1 << s for s in range(16, 28))), 1 << 47]))
    assert st.loc[0, "max_gap_h"] == 24.0 and st.loc[0, "n_slots"] == 0
    assert st.loc[1, "max_gap_h"] == 2.0 and st.loc[1, "first_h"] == 0.0 and st.loc[1, "last_h"] == 22.5
    assert st.loc[2, "max_gap_h"] == 10.0 and st.loc[2, "n_slots"] == 12     # last slot ends 14:00; 10 h to midnight
    assert st.loc[3, "max_gap_h"] == 23.5


def test_verify_live_scores_nothing_without_coverage(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    ov = pd.DataFrame({"site_id": ["A"], "has_live": [True], "weight": [0.5]})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)
    _log(fl, pd.Timestamp("2026-09-14 07:30", tz=TZ), days, ov, 0.3)
    hist = pd.DataFrame({"site_id": ["A"], "company": ["X"], "status": [0],
                         "status_start": pd.to_datetime(["2026-09-01"], utc=True),
                         "latest_event_start": pd.to_datetime([None], utc=True),
                         "latest_event_end": pd.to_datetime([None], utc=True),
                         "fetched_at": pd.to_datetime(["2026-09-14 12:00"], utc=True)})
    hist.to_parquet(tmp_path / "live_history.parquet", index=False)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    assert out["n_candidates"] == 2 and out["n_scored"] == 0 and out["n_uncovered"] == 2 and out["missed_deadline"]["n"] == 0


def test_verify_ecoli_ignores_forecasts_issued_after_the_sample(tmp_path, monkeypatch):
    import json
    config = _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    s = json.loads((config.RAW / "bathing_waters_inland.json").read_text())[0]
    days = pd.date_range("2026-09-09", periods=3, freq="D", tz=TZ)
    for issued, p in ((pd.Timestamp("2026-09-10 07:00", tz=TZ), 0.6), (pd.Timestamp("2026-09-10 15:00", tz=TZ), 0.95)):
        fl.log_forecast(issued, round(s["lat"], 5), round(s["lon"], 5), "river", "R", 0.1, days, np.array([0.1, 0.2, 0.3]),
                        pd.DataFrame(), np.zeros((0, 3)), np.zeros((0, 3)),
                        p_ecoli=np.array([np.nan, p, p / 2]), rain_48h=np.array([np.nan, 12.0, 3.0]))
    samples = pd.DataFrame({"bw_id": [s["id"]], "name": s["name"], "kind": s["kind"],
                            "sample_time": pd.to_datetime(["2026-09-10 11:00"]).tz_localize(TZ), "ecoli": [1500]})
    samples.to_parquet(tmp_path / fl.ECOLI_SAMPLES, index=False)
    out = fl.verify_ecoli(as_of=date(2026, 9, 12))
    assert out["n_scored"] == 1 and out["n_issued_after_sample"] == 1
    assert abs(out["by_lead"][0]["brier"] - 0.16) < 1e-9      # 07:00 forecast 0.6 scored, 15:00 forecast 0.95 not


def test_save_live_accumulates_coverage(tmp_path, monkeypatch):
    config = _state(tmp_path, monkeypatch)
    from dipcast.ingest import live
    monkeypatch.setattr(config, "LIVE_FEEDS", {"X": "u"})

    def poll(ts, statuses, updated=None):
        return pd.DataFrame({"site_id": ["A", "B"], "company": "X", "status": statuses,
                             "status_start": pd.Timestamp("2026-09-01", tz="UTC"), "latest_event_start": pd.NaT,
                             "latest_event_end": pd.NaT, "lat": 54.0, "lon": -2.0, "receiving_watercourse": "r",
                             "last_updated": pd.Timestamp(updated or ts, tz="UTC"), "fetched_at": pd.Timestamp(ts, tz="UTC")})
    live.save_live(poll("2026-09-14 06:00", [0, -1]))
    live.save_live(poll("2026-09-14 07:00", [0, 0]))
    live.save_live(poll("2026-09-14 07:10", [0, 0]))                       # same half-hour slot: no new slot
    live.save_live(poll("2026-09-14 09:00", [0, 0], updated="2026-09-13 12:00"))   # feed 21 h stale
    live.save_live(poll("2026-09-15 06:00", [1, 0]))
    cov = pd.read_parquet(tmp_path / live.COVERAGE_FILE)
    cov["day"] = pd.to_datetime(cov["day"]).dt.date
    a14 = cov[(cov.site_id == "A") & (cov.day == date(2026, 9, 14))].iloc[0]
    b14 = cov[(cov.site_id == "B") & (cov.day == date(2026, 9, 14))].iloc[0]
    assert a14.n_known == 4 and a14.n_stale == 1 and b14.n_known == 3 and b14.n_unknown == 1
    # fetched_at is UTC; slots are local (BST): 06:00Z -> 07:00 (slot 14), 07:00Z -> 08:00 (slot 16); the stale poll adds none
    assert int(a14.slots) == (1 << 14) | (1 << 16)
    assert int(b14.slots) == (1 << 16)
    hist = pd.read_parquet(tmp_path / "live_history.parquet")
    assert len(hist) == 4     # the event log keeps one row per (site, status, status_start); observations live in coverage
    pl = pd.read_parquet(tmp_path / live.POLL_LOG_FILE)
    assert len(pl) == 5 and pl.n_rows.tolist() == [2] * 5 and round(pl.feed_age_h.iloc[3]) == 21
    from dipcast.forecast_log import covered_site_days
    # Two known slots at 06:00 and 07:00 leave a 17 h gap to midnight: not covered under any gap rule
    assert covered_site_days(cov, min_known=2).empty
    # stale polls do not count towards min_known: A has 4 - 1 = 3, B has 3 - 1 = 2
    assert covered_site_days(cov, min_known=3, max_gap_h=24.0)["site_id"].tolist() == ["A"]


def test_save_live_upgrades_a_coverage_file_from_before_17_sep(tmp_path, monkeypatch):
    config = _state(tmp_path, monkeypatch)
    from dipcast.forecast_log import covered_site_days
    from dipcast.ingest import live
    monkeypatch.setattr(config, "LIVE_FEEDS", {"X": "u"})
    # The 16 Sep poller wrote counts only: no stale count, no slot mask.
    pd.DataFrame({"site_id": ["A", "A"], "day": pd.to_datetime(["2026-09-13", "2026-09-14"]),
                  "n_known": [12, 5], "n_unknown": [0, 0]}).to_parquet(tmp_path / live.COVERAGE_FILE, index=False)
    t = pd.Timestamp("2026-09-14 06:00", tz="UTC")
    live.save_live(pd.DataFrame({"site_id": ["A"], "company": "X", "status": [0], "status_start": pd.Timestamp("2026-09-01", tz="UTC"),
                                 "latest_event_start": pd.NaT, "latest_event_end": pd.NaT, "lat": 54.0, "lon": -2.0,
                                 "receiving_watercourse": "r", "last_updated": t, "fetched_at": t}))
    cov = pd.read_parquet(tmp_path / live.COVERAGE_FILE)
    cov["day"] = pd.to_datetime(cov["day"]).dt.date
    a13 = cov[cov.day == date(2026, 9, 13)].iloc[0]
    a14 = cov[cov.day == date(2026, 9, 14)].iloc[0]
    assert cov["slots"].dtype == "int64"
    # old rows keep their counts with an empty mask; the new poll adds its slot (06:00Z = 07:00 BST, slot 14)
    assert a13.n_known == 12 and a13.n_stale == 0 and int(a13.slots) == 0
    assert a14.n_known == 6 and int(a14.slots) == 1 << 14
    # an empty mask is an unobserved day: days scored under the old rule are withdrawn, not rescored
    assert covered_site_days(cov).empty


# ---------------------------------------------------------------- EA sample fetch
def _refuse(point, since):
    """What GitHub's runners get from the bathing-water service (28 Sep 2026)."""
    import httpx
    req = httpx.Request("GET", "https://environment.data.gov.uk/doc/bathing-water-quality/in-season/sample.json")
    httpx.Response(403, request=req).raise_for_status()


def _archive_down(points, since, purposes=None):
    import httpx
    req = httpx.Request("POST", "https://environment.data.gov.uk/water-quality/data/observation")
    httpx.Response(502, request=req).raise_for_status()


def _log_ecoli(fl, s, p=0.6):
    """One E. coli forecast at bathing water `s`, issued 10 Sep 07:00 for 10-11 Sep."""
    days = pd.date_range("2026-09-09", periods=3, freq="D", tz=TZ)
    fl.log_forecast(pd.Timestamp("2026-09-10 07:00", tz=TZ), round(s["lat"], 5), round(s["lon"], 5), "river", "R", 0.1,
                    days, np.array([0.1, 0.2, 0.3]), pd.DataFrame(), np.zeros((0, 3)), np.zeros((0, 3)),
                    p_ecoli=np.array([np.nan, p, p / 2]), rain_48h=np.array([np.nan, 12.0, 3.0]))


def test_total_sample_fetch_failure_is_flagged_not_silent(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    from dipcast.ingest import bwq, wqa
    asked = []

    def refuse(point, since):
        asked.append(point)
        _refuse(point, since)
    monkeypatch.setattr(bwq, "fetch_point", refuse)
    monkeypatch.setattr(wqa, "fetch_ecoli", _archive_down)
    sites = fl._bathing_sites()
    _log_ecoli(fl, sites.iloc[0])
    out = fl.verify_ecoli(as_of=date(2026, 9, 12))
    st = out["samples"]
    assert out["n_scored"] == 0 and out["n_forecasts"] == 2
    assert st["all_failed"] and st["n_failed"] == st["n_sites"] == len(sites)
    assert st["sources"]["bathing_water"] == {"answered": 0, "errors": {"HTTP 403": 1}, "refused": True}
    assert st["sources"]["archive"]["error"] == "HTTP 502" and st["sources"]["archive"]["answered"] == 0
    assert len(asked) == 1                                   # refused once: the other sites were not asked
    assert st["last_ok_at"] is None and st["n_samples"] == 0
    assert not (tmp_path / fl.ECOLI_SAMPLES).exists()        # nothing written, so the next build retries
    good = {"name": "ok", "days": [{"data_status": "ok"}]}
    h = _build_site().build_health([good] * 5, fl.samples_status())   # flagged, but the site still publishes
    assert h["ecoli_samples"]["n_failed"] == len(sites) and len(h["warnings"]) == 1
    assert "no EA source answered" in h["warnings"][0] and "HTTP 403" in h["warnings"][0] and "HTTP 502" in h["warnings"][0]
    assert _build_site().build_health([good] * 5, None)["warnings"] == []


def test_refused_bathing_water_service_falls_back_to_the_archive(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    from dipcast.ingest import bwq, wqa
    sites = fl._bathing_sites()
    s = sites.iloc[0]
    monkeypatch.setattr(bwq, "fetch_point", _refuse)
    monkeypatch.setattr(wqa, "fetch_ecoli", lambda points, since, purposes=None: [
        {"wqa_point": s["wqa_point"], "sample_time": "2026-09-10T11:00:00", "ecoli": 1500.0, "ecoli_qual": "=", "purpose": "MS"},
        {"wqa_point": "XX-NOT-OURS", "sample_time": "2026-09-10T11:00:00", "ecoli": 5.0, "ecoli_qual": "=", "purpose": "MS"}])
    _log_ecoli(fl, s)                                        # issued 07:00, before the 11:00 sample
    out = fl.verify_ecoli(as_of=date(2026, 9, 12))
    st = out["samples"]
    assert out["n_scored"] == 1 and abs(out["by_lead"][0]["brier"] - 0.16) < 1e-9     # (0.6 - 1)^2
    assert not st["all_failed"] and st["n_failed"] == 0 and st["sources"]["bathing_water"]["refused"]
    assert st["sources"]["archive"] == {"answered": len(sites), "error": None, "n_samples": 1}   # the stray point is dropped
    saved = pd.read_parquet(tmp_path / fl.ECOLI_SAMPLES)
    assert saved["source"].tolist() == ["archive"] and saved["bw_id"].tolist() == [s["bw_id"]]
    assert _build_site().build_health([{"name": "ok", "days": [{"data_status": "ok"}]}], st)["warnings"] == []


def test_partial_sample_fetch_failure_keeps_earlier_samples(tmp_path, monkeypatch):
    import httpx
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    from dipcast.ingest import bwq, wqa
    sites = fl._bathing_sites().head(2)
    yr = pd.Timestamp.now(tz=TZ).year
    old = pd.DataFrame({"bw_id": sites["bw_id"], "name": sites["name"], "kind": sites["kind"], "ecoli": [100.0, 200.0],
                        "sample_time": pd.to_datetime([f"{yr}-06-01 10:00", f"{yr}-06-01 11:00"]).tz_localize(TZ)})
    old.to_parquet(tmp_path / fl.ECOLI_SAMPLES, index=False)
    down = sites["bw_id"].iloc[0].split("-")[-1]

    def fetch(point, since):
        if point == down:
            raise httpx.ConnectTimeout("timed out")
        return [{"point": point, "sample_time": f"{yr}-06-08T10:00:00", "ecoli": 300}]
    monkeypatch.setattr(bwq, "fetch_point", fetch)
    monkeypatch.setattr(wqa, "fetch_ecoli", lambda points, since, purposes=None: [])   # answered, nothing new
    df = fl.refresh_ecoli_samples(force=True)
    assert df[df["bw_id"] == sites["bw_id"].iloc[0]]["ecoli"].tolist() == [100.0]   # timed out: keeps its June 1 sample
    assert df[df["bw_id"] == sites["bw_id"].iloc[1]]["ecoli"].tolist() == [300.0]   # answered: replaced by the answer
    st = fl.samples_status()
    bw = st["sources"]["bathing_water"]
    assert not st["all_failed"] and bw["errors"] == {"ConnectTimeout": 1} and not bw["refused"]
    assert bw["answered"] == len(fl._bathing_sites()) - 1 and st["last_ok_at"] == st["checked_at"]
    assert _build_site().build_health([{"name": "ok", "days": [{"data_status": "ok"}]}], st)["warnings"] == []


def test_archive_fetch_pages_batches_and_parses(monkeypatch):
    import httpx

    from dipcast.ingest import wqa
    asked = []

    def handler(request):
        q = dict(request.url.params)
        asked.append(q)
        pts = q["pointNotation"].split(",")
        n = {"0": wqa.PAGE, str(wqa.PAGE): 1}.get(q["skip"], 0) if len(pts) == wqa.MAX_POINTS else 1
        member = [{"hasSamplingPoint": {"notation": pts[0]}, "phenomenonTime": "2026-09-14T09:31:00",
                   "hasSimpleResult": "<10" if i == 0 else "620",
                   "hasSample": {"isResultOf": {"samplingPurpose": {"notation": "MS"}}}} for i in range(n)]
        return httpx.Response(200, json={"member": member})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(wqa.time, "sleep", lambda s: None)
    rows = wqa.fetch_ecoli([f"P-{i}" for i in range(wqa.MAX_POINTS + 10)], "2026-05-01T00:00:00")
    assert len(rows) == wqa.PAGE + 2 and [q["skip"] for q in asked] == ["0", str(wqa.PAGE), "0"]   # 2 pages, then batch 2
    assert {q["determinand"] for q in asked} == {"2348"} and {q["samplingPurpose"] for q in asked} == {"MS"}
    assert asked[0]["dateFrom"] == "2026-05-01" and len(asked[2]["pointNotation"].split(",")) == 10
    assert rows[0] == {"wqa_point": "P-0", "sample_time": "2026-09-14T09:31:00", "ecoli": 10.0, "ecoli_qual": "<", "purpose": "MS"}
    assert (rows[1]["ecoli"], rows[1]["ecoli_qual"]) == (620.0, "=")
    assert wqa._count(">15000") == (15000.0, ">") and wqa._count(None) == (None, "=")


def test_ea_requests_send_a_contact_user_agent(monkeypatch):
    import httpx

    from dipcast import config
    from dipcast.ingest import bwq, flows, wqa
    seen = []

    def handler(request):
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"result": {"items": []}, "items": []})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(httpx, "get", lambda url, **kw: real_client(transport=httpx.MockTransport(handler)).get(url, **kw))
    assert bwq.fetch_point("08901", "2026-05-01T00:00:00") == []
    assert flows._nearest_level_station(54.2, -2.6) is None
    assert wqa.fetch_ecoli(["NE-49705000"], "2026-09-01") == []
    assert len(seen) == 3 and set(seen) == {config.USER_AGENT}
    assert "github.com/ethanbuckley/swimsignal" in config.USER_AGENT and "python-httpx" not in seen[0]


# ---------------------------------------------------------------- build guard
def _build_site():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_site
    return build_site


def test_home_screen_files_match_what_the_page_declares(tmp_path):
    import json
    import struct
    bs = _build_site()
    src, html = bs.TEMPLATE.parent, bs.TEMPLATE.read_text()
    man = json.loads((src / "manifest.webmanifest").read_text())
    # One name everywhere a phone shows it: the manifest, the iOS home-screen title and the build's brand.
    assert man["name"] == man["short_name"] == bs.BRAND and f'name="apple-mobile-web-app-title" content="{bs.BRAND}"' in html
    assert man["display"] == "standalone" and man["start_url"] == "./"
    for ref in ("manifest.webmanifest", "icons/apple-touch-icon.png", "icons/icon.svg", "icons/icon-192.png"):
        assert f'href="{ref}"' in html and (src / ref).exists(), ref
    size = lambda p: "{}x{}".format(*struct.unpack(">II", p.read_bytes()[16:24]))   # the PNG header's width and height
    for icon in man["icons"]:
        assert (src / icon["src"]).exists() and (icon["type"] != "image/png" or size(src / icon["src"]) == icon["sizes"])
    touch = src / "icons" / "apple-touch-icon.png"
    assert size(touch) == "180x180" and touch.read_bytes()[25] == 2   # RGB, no alpha: iOS paints transparency black
    bs.copy_app_files(tmp_path)
    assert (tmp_path / "manifest.webmanifest").exists() and (tmp_path / "icons" / "icon-512.png").exists()


def test_build_health_refuses_mostly_failed_builds():
    BuildUnhealthy, build_health = _build_site().BuildUnhealthy, _build_site().build_health
    good = {"name": "ok", "days": [{"data_status": "ok"}]}
    bad = {"name": "bad", "error": "forecast failed"}
    isolated = {"name": "tarn", "error": "An isolated lake", "days": []}   # an answer, not a failure
    h = build_health([good] * 8 + [isolated] + [bad])
    assert h["forecast_ok"] == 9 and h["forecast_failed"] == 1 and h["failed_spots"] == ["bad"]
    assert h["no_forecast_possible"] == 1
    with pytest.raises(BuildUnhealthy):
        build_health([good] * 7 + [bad] * 3)
    nodata = {"name": "nd", "days": [{"data_status": "rain unavailable"}]}
    with pytest.raises(BuildUnhealthy):
        build_health([good] * 4 + [nodata] * 6)


def test_page_view_counter_is_off_by_default_and_disclosed_when_on():
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    from build_site import NO_COUNTER, WITH_COUNTER, with_counter
    privacy = (root / "src" / "dipcast" / "api" / "static" / "privacy.html").read_text()
    assert all(s in privacy for s in NO_COUNTER)          # the swap targets still exist in the page
    assert with_counter(privacy, "") == privacy and with_counter(privacy, None) == privacy
    assert with_counter(privacy, "not-a-token'><script>") == privacy     # a malformed token cannot inject markup
    on = with_counter(privacy, "0123456789abcdef0123456789abcdef")
    assert on.count("static.cloudflareinsights.com/beacon.min.js") == 1 and '"token": "0123456789abcdef0123456789abcdef"' in on
    assert '<script id="page-counter">' in on and "dipcast.count" in on and "__TOKEN__" not in on   # loads only without an objection
    assert "Don't count my visits" in on                  # the notice says how to object
    assert all(s in on for s in WITH_COUNTER) and not any(s in on for s in NO_COUNTER)
