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
