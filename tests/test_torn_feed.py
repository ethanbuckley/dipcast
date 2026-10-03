"""Regression test for the 3 Oct 2026 13:05 BST build, where four spots failed with "forecast
failed: Index contains duplicate entries, cannot reshape". Severn Trent's and Yorkshire
Water's live layers are rewritten whole on every refresh; a rewrite between the two page
requests of one poll returned the second page from the new copy, in a new order, so it
repeated rows of the first page (410 Severn Trent, 29 Yorkshire) and left others out. A
repeated overflow upstream of a spot made spill_probabilities' pivot fail."""

import numpy as np
import pandas as pd

IDS = ["S1", "S2", "S3", "S4", "S5", "S6"]
REWRITTEN = ["S3", "S1", "S5", "S2", "S6", "S4"]   # the same overflows after the layer is rewritten
PAGE = 4
T0 = 1_791_000_000_000   # epoch ms, early on 3 Oct 2026


def _feature(site_id: str, i: int) -> dict:
    return {"attributes": {"Id": site_id, "Status": 0, "StatusStart": T0, "LatestEventStart": None,
                           "LatestEventEnd": None, "Latitude": 52.70 + 0.01 * i, "Longitude": -2.75,
                           "ReceivingWaterCourse": "RIVER SEVERN", "LastUpdated": T0},
            "geometry": {"x": -2.75, "y": 52.70 + 0.01 * i}}


def _torn_layer(monkeypatch):
    """A fake ArcGIS layer that is rewritten, in a new order, after the first page request."""
    from dipcast import arcgis
    calls = []

    def get(client, url, params):
        order = IDS if not calls else REWRITTEN
        calls.append(params["resultOffset"])
        off, n = params["resultOffset"], params["resultRecordCount"]
        feats = [_feature(s, IDS.index(s)) for s in order[off:off + n]]
        return {"features": feats, "exceededTransferLimit": off + n < len(order)}

    monkeypatch.setattr(arcgis, "_get", get)
    monkeypatch.setattr(arcgis.time, "sleep", lambda s: None)
    return calls


def test_a_layer_rewritten_between_pages_is_read_again(monkeypatch):
    from dipcast import arcgis
    _torn_layer(monkeypatch)
    torn = [r["Id"] for r in arcgis.fetch_all("https://x/0", page=PAGE)]
    assert sorted(torn) == ["S1", "S2", "S3", "S4", "S4", "S6"]   # S4 twice, S5 never: the 3 Oct failure
    calls = _torn_layer(monkeypatch)
    got = [r["Id"] for r in arcgis.fetch_all("https://x/0", page=PAGE, key="Id")]
    assert sorted(got) == IDS
    assert calls == [0, 4, 0, 4]   # one torn read, one clean one


def test_a_single_page_is_not_read_again(monkeypatch):
    from dipcast import arcgis
    calls = _torn_layer(monkeypatch)
    got = arcgis.fetch_all("https://x/0", page=10, key="Id")
    assert len(got) == 6 and calls == [0]


def test_live_snapshot_has_one_row_per_overflow(monkeypatch):
    """A feed that lists an overflow twice (Anglian Water's AWS00528 does) keeps the newest row."""
    from dipcast.ingest import live
    rows = [dict(_feature(s, i)["attributes"]) for i, s in enumerate(["A1", "A2", "A1"])]
    rows[2]["Status"], rows[2]["LastUpdated"] = 1, T0 + 60_000
    monkeypatch.setattr(live, "fetch_all", lambda url, **kw: [dict(r) for r in rows])
    df = live.fetch_live({"Anglian Water": "https://x/0"})
    assert sorted(df["site_id"]) == ["A1", "A2"]
    assert df.set_index("site_id").loc["A1", "status"] == 1


def _rain(cells):
    t = pd.date_range("2026-09-25", "2026-10-12", freq="h", tz="UTC", inclusive="left")
    return pd.concat([pd.DataFrame({"cell_lat": cl, "cell_lon": cn, "time": t, "precip_mm": 0.5})
                      for cl, cn in cells], ignore_index=True)


def _overflows(live_rows: pd.DataFrame) -> pd.DataFrame:
    return live_rows.assign(lta_spills=20.0, spill_hours=100.0, edm_operational_pct=95.0)


def test_spill_probabilities_with_a_torn_snapshot(monkeypatch):
    """The torn snapshot that failed the pivot now gives one row of probabilities per row, and the
    snapshot fetch_live now gives has one row per overflow."""
    from dipcast import arcgis
    from dipcast.ingest import live
    from dipcast.model import forecast
    monkeypatch.setattr(forecast, "fetch_forecast", _rain)
    days = pd.date_range("2026-10-01", periods=5, freq="D", tz=forecast.LOCAL_TZ)
    real = arcgis.fetch_all

    # fetch_live as it was before the fix: no key for fetch_all, no one_row_per_site.
    _torn_layer(monkeypatch)
    with monkeypatch.context() as m:
        m.setattr(live, "fetch_all", lambda url, **kw: real(url, page=PAGE))
        m.setattr(live, "one_row_per_site", lambda df: df)
        torn = live.fetch_live({"Severn Trent Water": "https://x/0"})
    assert sorted(torn["site_id"]) == ["S1", "S2", "S3", "S4", "S4", "S6"]
    # Until spill_probabilities computed a repeated id once, this raised "Index contains duplicate entries".
    p, ok = forecast.spill_probabilities(_overflows(torn), days, None)
    s4 = np.flatnonzero(torn["site_id"].to_numpy() == "S4")
    assert p.shape == (6, 5) and ok.all() and np.isfinite(p).all() and (p[s4[0]] == p[s4[1]]).all()

    _torn_layer(monkeypatch)
    monkeypatch.setattr(live, "fetch_all", lambda url, **kw: real(url, page=PAGE, key=kw["key"]))
    snap = live.fetch_live({"Severn Trent Water": "https://x/0"})
    assert sorted(snap["site_id"]) == IDS
    p, ok = forecast.spill_probabilities(_overflows(snap), days, None)
    assert p.shape == (6, 5) and ok.all() and np.isfinite(p).all()
