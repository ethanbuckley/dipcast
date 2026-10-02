"""Tests for the 2 Oct 2026 live-feed fixes: a discharge seen by a feed with no event
times counts as a spill, and is kept in the history across days; a feed that stamps no
record is never current; a failed company feed keeps its last snapshot, marked down, and
stops the publish when every feed fails; why each unscored overflow-day was not scored."""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

TZ = "Europe/London"


def _state(tmp_path, monkeypatch):
    from dipcast import config
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(config, "PROCESSED", tmp_path)
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "t.duckdb")
    return config


def _build_site():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_site
    return build_site


def _mask(hours):
    return int(sum(1 << (h * 2) for h in hours))


def test_discharging_with_no_event_times_is_a_spill_on_the_day_polled():
    from dipcast.forecast_log import observed_spill_days
    t = lambda s: pd.Timestamp(s, tz="UTC")
    h = pd.DataFrame({"site_id": ["S", "S", "Q", "E"], "company": ["South West Water"] * 3 + ["X"],
                      "status": [1, 1, 0, 0],
                      "status_start": pd.NaT, "latest_event_start": [pd.NaT, pd.NaT, pd.NaT, t("2026-09-29 10:00")],
                      "latest_event_end": [pd.NaT, pd.NaT, pd.NaT, t("2026-09-29 11:00")],
                      # 23:30 UTC on the 29th is 00:30 on the 30th in London (BST)
                      "fetched_at": [t("2026-09-29 12:00"), t("2026-09-30 23:30"), t("2026-09-29 12:00"), t("2026-09-29 12:00")]})
    got = {(r.site_id, str(r.day)) for r in observed_spill_days(h).itertuples()}
    assert got == {("S", "2026-09-29"), ("S", "2026-10-01"), ("E", "2026-09-29")}   # Q was not discharging


def _poll(ts, rows, updated=True):
    """rows: (site, company, status, status_start or None)."""
    t = pd.Timestamp(ts, tz="UTC")
    return pd.DataFrame({"site_id": [r[0] for r in rows], "company": [r[1] for r in rows], "status": [r[2] for r in rows],
                         "status_start": pd.to_datetime([r[3] for r in rows], utc=True),
                         "latest_event_start": pd.NaT, "latest_event_end": pd.NaT, "lat": 54.0, "lon": -2.0,
                         "receiving_watercourse": "r",
                         "last_updated": [t if (updated and r[1] != "SW") else pd.NaT for r in rows], "fetched_at": t})


def test_save_live_keeps_blank_start_rows_per_day_and_carries_a_failed_feed(tmp_path, monkeypatch):
    config = _state(tmp_path, monkeypatch)
    from dipcast.forecast_log import covered_site_days, observed_spill_days
    from dipcast.ingest import live
    monkeypatch.setattr(config, "LIVE_FEEDS", {"SW": "u", "Y": "v"})
    sw = lambda st: [("A", "SW", st, None), ("B", "SW", 0, None)]
    live.save_live(_poll("2026-09-29 08:00", sw(1) + [("C", "Y", 0, "2026-09-01")]))
    live.save_live(_poll("2026-09-29 12:00", sw(1) + [("C", "Y", 0, "2026-09-01")]))
    live.save_live(_poll("2026-09-30 08:00", sw(1)))              # Y's feed failed: no rows
    live.save_live(_poll("2026-09-30 12:00", sw(0)))              # still failing
    hist = pd.read_parquet(tmp_path / "live_history.parquet")
    a1 = hist[(hist.site_id == "A") & (hist.status == 1)]
    assert len(a1) == 2      # one per day polled; before, the 30th's row replaced the 29th's
    assert {(r.site_id, str(r.day)) for r in observed_spill_days(hist).itertuples()} == {("A", "2026-09-29"), ("A", "2026-09-30")}
    assert len(hist[hist.site_id == "C"]) == 1     # a row with a status start keeps the old key
    latest = pd.read_parquet(tmp_path / "live_latest.parquet")
    c = latest[latest.site_id == "C"].iloc[0]
    assert c.status == live.FEED_DOWN and pd.Timestamp(c.feed_down_since) == pd.Timestamp("2026-09-29 12:00", tz="UTC")
    assert latest.loc[latest.site_id == "A", "feed_down_since"].isna().all()
    # Only fresh rows reach the poll log and the coverage file.
    pl = pd.read_parquet(tmp_path / live.POLL_LOG_FILE)
    assert pl[pl.company == "Y"].n_rows.tolist() == [1, 1, 0, 0]
    cov = pd.read_parquet(tmp_path / live.COVERAGE_FILE)
    assert set(cov[cov.site_id == "C"]["day"].astype(str).str[:10]) == {"2026-09-29"}
    # SW stamps no record, so its polls are stale: no slots, and no day of them is covered.
    sw_cov = cov[cov.site_id.isin(["A", "B"])]
    assert (sw_cov.slots == 0).all() and (sw_cov.n_stale == sw_cov.n_known).all()
    cov["day"] = pd.to_datetime(cov["day"]).dt.date
    assert covered_site_days(cov, min_known=1, max_gap_h=24).empty
    # Y answers again: nothing is carried.
    live.save_live(_poll("2026-09-30 16:00", sw(0) + [("C", "Y", 0, "2026-09-01")]))
    assert pd.read_parquet(tmp_path / "live_latest.parquet")["feed_down_since"].isna().all()


def test_forecast_says_which_feeds_are_down():
    from dipcast.ingest.live import FEED_DOWN
    from dipcast.model.forecast import feed_down_summary
    ov = pd.DataFrame({"company": ["Y", "Y", "Z"], "status": [FEED_DOWN, FEED_DOWN, 0],
                       "feed_down_since": [pd.Timestamp("2026-09-29 12:00", tz="UTC"), pd.Timestamp("2026-09-29 12:00", tz="UTC"), pd.NaT]})
    assert feed_down_summary(ov) == [{"company": "Y", "overflows": 2, "since": "2026-09-29T12:00:00+00:00"}]
    assert feed_down_summary(ov.iloc[2:]) == []


def test_build_health_warns_on_a_dead_feed_and_refuses_when_all_are_dead():
    bs = _build_site()
    good = {"name": "ok", "days": [{"data_status": "ok"}]}
    t1, t2 = pd.Timestamp("2026-10-02 09:00", tz="UTC"), pd.Timestamp("2026-10-02 14:00", tz="UTC")
    pl = lambda rows2: pd.DataFrame({"fetched_at": [t1, t1, t2, t2], "company": ["X", "Y"] * 2,
                                     "n_rows": [100, 50, *rows2], "n_known": 0, "feed_age_h": 0.1})
    h = bs.build_health([good] * 5, None, pl([100, 50]))
    assert h["warnings"] == [] and h["live_feeds"]["down"] == []
    h = bs.build_health([good] * 5, None, pl([100, 0]))
    assert h["live_feeds"]["down"] == ["Y"] and len(h["warnings"]) == 1
    assert "Y's live overflow feed returned no rows" in h["warnings"][0] and "previous poll: 50 rows" in h["warnings"][0]
    with pytest.raises(bs.BuildUnhealthy):
        bs.build_health([good] * 5, None, pl([0, 0]))
    assert "live_feeds" not in bs.build_health([good] * 5, None, None)


def test_uncovered_overflow_days_are_split_by_reason(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    sites = ["A", "B", "C", "D", "E"]
    ov = pd.DataFrame({"site_id": sites, "has_live": True, "weight": 0.5})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)          # 13th (history), 14th, 15th
    fl.log_forecast(pd.Timestamp("2026-09-14 07:30", tz=TZ), 54.0, -2.0, "river", "R", 0.1, days, np.full(3, 0.1), ov,
                    np.full((5, 3), 0.2), np.full((5, 3), 0.2), version=None)
    fl.log_forecast(pd.Timestamp("2026-09-14 09:00", tz=TZ), 54.0, -2.0, "river", "R", 0.1, days, np.full(3, 0.1), ov,
                    np.full((5, 3), 0.3), np.full((5, 3), 0.3), version="v1")
    pd.DataFrame({"site_id": sites, "company": ["X", "X", "X", "X", "SW"], "status": 0, "status_start": pd.NaT,
                  "latest_event_start": pd.NaT, "latest_event_end": pd.NaT,
                  "fetched_at": pd.Timestamp("2026-09-15 12:00", tz="UTC")}).to_parquet(tmp_path / "live_history.parquet", index=False)
    # Masks begin on the 15th: every 14th is before observations. On the 15th A passes; B has a
    # long gap; C too few polls (gap 8 h); D no poll on the 16th; E's feed (SW) stamps nothing.
    every2h, three = _mask(range(0, 24, 2)), _mask([4, 12, 20])
    rows = [("A", "2026-09-14", 12, 0)] + [(s, "2026-09-15", 12, every2h) for s in ("A", "D", "E")] + [
        ("B", "2026-09-15", 12, int(sum(1 << s for s in range(16, 28)))), ("C", "2026-09-15", 3, three)] + [
        (s, "2026-09-16", 1, _mask([6])) for s in ("A", "B", "C", "E")]
    pd.DataFrame({"site_id": [r[0] for r in rows], "day": pd.to_datetime([r[1] for r in rows]), "n_known": [r[2] for r in rows],
                  "n_unknown": 0, "n_stale": 0, "slots": [r[3] for r in rows]}).to_parquet(tmp_path / fl.COVERAGE_FILE, index=False)
    t = pd.Timestamp("2026-09-15 11:00", tz="UTC")
    pd.DataFrame({"fetched_at": [t, t], "company": ["X", "SW"], "n_rows": [4, 1], "n_known": [4, 1],
                  "feed_age_h": [0.1, np.nan]}).to_parquet(tmp_path / fl.POLL_LOG_FILE, index=False)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    assert out["n_candidates"] == 10 and out["n_scored"] == 1 and out["missed_deadline"]["n"] == 0
    assert out["n_uncovered"] == out["n_candidates"] - out["missed_deadline"]["n"] - out["n_scored"] == 9
    assert out["uncovered_by_reason"] == {"before_observations": 5, "feed_not_current": 1, "max_gap": 1,
                                          "min_known_polls": 1, "next_day_unobserved": 1}
    assert sum(out["uncovered_by_reason"].values()) == out["n_uncovered"]
    w = out["scoring_window"]
    assert w["from_day"] == "2026-09-15" and w["to_day"] == "2026-09-15" and w["n_before_window"] == 5
    assert w["n_candidates"] == w["n_missed_deadline"] + w["n_uncovered"] + w["n_scored"] == 5
    assert w["first_scored_day"] == w["last_scored_day"] == "2026-09-15"
    assert out["coverage_from"] == "2026-09-15"     # E's day is covered by the masks but not by a current feed
    assert "overflow-days" in out["units"]["n_candidates"]
    # The 07:30 forecast was unstamped: the label comes from the log's first stamped issue day.
    assert [r["version"] for r in out["by_version"]] == ["unstamped (stamps begin 2026-09-14)"]
