"""Tests for the EA sampler's visual algae check at bathing waters: fetched from the Water
Quality Archive, kept a season at a time, a failed or empty fetch never wipes what is held,
and each bathing-water spot gets its latest check and the season's tally."""

import pandas as pd

TZ = "Europe/London"


def _state(tmp_path, monkeypatch):
    from dipcast import config
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(config, "PROCESSED", tmp_path)
    return config


def _build_site():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_site
    return build_site


def _rows(point, *checks):
    return [{"wqa_point": point, "sample_time": t, "result": r, "purpose": "MS"} for t, r in checks]


NONE, TRACE, SOME, OBJ = ("No material present", "Trace present - 1 or 2 items",
                          "Some at intervals - 3 to 6 items", "Sufficient to be objectionable >6 items")
NOW = pd.Timestamp("2026-09-20 12:00", tz=TZ)


def test_fetch_algae_asks_for_4824_at_every_purpose_and_keeps_the_wording(monkeypatch):
    import httpx

    from dipcast.ingest import wqa
    asked = []

    def handler(request):
        asked.append(dict(request.url.params))
        return httpx.Response(200, json={"member": [{
            "hasSamplingPoint": {"notation": "TH-X"}, "phenomenonTime": "2026-09-15T10:05:00", "hasSimpleResult": OBJ,
            "hasSample": {"isResultOf": {"samplingPurpose": {"notation": "PI"}}}}]})
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    rows = wqa.fetch_algae(["TH-X"], "2026-05-01T00:00:00+01:00")
    assert rows == [{"wqa_point": "TH-X", "sample_time": "2026-09-15T10:05:00", "purpose": "PI", "result": OBJ}]
    assert asked[0]["determinand"] == "4824" and asked[0]["dateFrom"] == "2026-05-01" and "samplingPurpose" not in asked[0]


def test_season_starts_on_the_latest_first_of_may():
    from dipcast.algae import season_start
    assert season_start(pd.Timestamp("2026-09-20", tz=TZ)) == pd.Timestamp("2026-05-01", tz=TZ)
    assert season_start(pd.Timestamp("2027-02-10", tz=TZ)) == pd.Timestamp("2026-05-01", tz=TZ)   # last season, dated
    assert season_start(pd.Timestamp("2027-05-01 00:30", tz=TZ)) == pd.Timestamp("2027-05-01", tz=TZ)


def test_refresh_keeps_this_season_at_our_points_and_never_wipes_on_failure(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import algae
    from dipcast.forecast_log import _bathing_sites
    from dipcast.ingest import wqa
    s = _bathing_sites().iloc[0]
    monkeypatch.setattr(wqa, "fetch_algae", lambda points, since: _rows(s["wqa_point"],
        ("2026-04-20T10:00:00", OBJ),                                   # before the season: dropped
        ("2026-09-08T10:00:00", SOME), ("2026-09-08T10:00:00", SOME),   # the same visit twice: one kept
        ("2026-09-15T10:00:00", NONE)) + _rows("XX-NOT-OURS", ("2026-09-15T10:00:00", OBJ)))
    df = algae.refresh_algae(force=True, now=NOW)
    assert df["result"].tolist() == [SOME, NONE] and set(df["bw_id"]) == {s["bw_id"]}
    assert (tmp_path / algae.ALGAE_CHECKS).exists()

    def down(points, since):
        raise ConnectionError("archive down")
    monkeypatch.setattr(wqa, "fetch_algae", down)
    assert algae.refresh_algae(force=True, now=NOW)["result"].tolist() == [SOME, NONE]      # failure: kept
    monkeypatch.setattr(wqa, "fetch_algae", lambda points, since: [])
    assert algae.refresh_algae(force=True, now=NOW)["result"].tolist() == [SOME, NONE]      # empty answer: kept
    assert len(pd.read_parquet(tmp_path / algae.ALGAE_CHECKS)) == 2
    monkeypatch.setattr(wqa, "fetch_algae", lambda points, since: 1 / 0)
    assert len(algae.refresh_algae(fetch=False, now=NOW)) == 2                               # read only: not asked


def test_by_site_gives_the_latest_check_and_the_season_tally():
    from dipcast.algae import by_site
    t = pd.to_datetime(["2026-06-01 10:00", "2026-07-01 10:00", "2026-08-01 10:00", "2026-09-01 10:00",
                        "2026-09-10 10:00"]).tz_localize(TZ)
    df = pd.DataFrame({"bw_id": ["a"] * 5, "sample_time": t, "result": [NONE, TRACE, OBJ, "Something new", SOME]})
    a = by_site(df)["a"]
    assert (a["date"], a["level"], a["phrase"]) == ("2026-09-10", 2, "some at intervals (3 to 6 items)")
    assert (a["n_checks"], a["n_seen"], a["n_objectionable"], a["season"]) == (5, 3, 1, 2026)   # unknown wording not counted
    assert by_site(pd.DataFrame()) == {}


def test_bathing_water_spots_get_the_check_and_a_failure_does_not_stop_the_build(monkeypatch):
    bs = _build_site()
    monkeypatch.setattr(bs, "refresh_algae", lambda fetch=True: "held")
    monkeypatch.setattr(bs, "by_site", lambda df: {"uke4100-08901": {"date": "2026-09-14", "level": 0}})
    spots = [{"id": "bw-uke4100-08901"}, {"id": "bw-ukj0000-00000"}, {"id": "ilkley-lido"}]
    assert bs.attach_algae(spots) == 1
    assert spots[0]["algae"]["date"] == "2026-09-14" and "algae" not in spots[1] and "algae" not in spots[2]

    def broken(fetch=True):
        raise RuntimeError("bad parquet")
    monkeypatch.setattr(bs, "refresh_algae", broken)
    assert bs.attach_algae([{"id": "bw-uke4100-08901"}]) == 0
