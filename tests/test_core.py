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
    # windows are closed at both ends, as in validate_ecoli.py, so the 12:00 burst sits on the edge of both
    assert np.allclose(r48, [12.0, 7.0]) and np.allclose(r24, [7.0, 7.0])
    X = features([0.0, 20.0], [0.0, 10.0], [0.1, 0.1], [0.0, 0.0], ends)
    assert list(X.columns) == FEATURES
    # a positive rain coefficient must raise the probability with more rain, all else equal
    m = EcoliModel({f: 0.0 for f in FEATURES} | {"lrain48": 1.0}, -2.0,
                   {f: 0.0 for f in FEATURES}, {f: 1.0 for f in FEATURES}, {})
    q = m.predict(X)
    assert q[1] > q[0] and 0 < q[0] < 1


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
