"""The EA gauge lookup behind the river level index. The station list links each stage
scale as http://, which answers 301 to https://; until 29 Sep 2026 that redirect made
every linked scale fail and the index went missing (seen near Pangbourne: station
2180TH, the Pang at Tidmarsh). Then the words beside the pollution level that come from
the gauges and the EA's flood alerts: "high", "rising fast" and the alerts in force."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from dipcast.ingest import flows

EA = "environment.data.gov.uk"
REF = "2180TH"


def _fake_ea(monkeypatch, stage_scale: str) -> list[str]:
    """Route flows' httpx.get through a real httpx client on a mock transport, so
    redirects behave as they do live (not followed unless asked). Returns the URLs asked for."""
    asked = []

    def handler(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        path = req.url.path
        if req.url.host != EA:
            return httpx.Response(404)
        if req.url.scheme == "http":   # what the live service does with every http:// link
            return httpx.Response(301, headers={"Location": str(req.url.copy_with(scheme="https"))})
        if path == "/flood-monitoring/id/stations":
            return httpx.Response(200, json={"items": [{
                "stationReference": REF, "label": "Tidmarsh", "riverName": "River Pang",
                "lat": 51.4856, "long": -1.0913, "stageScale": stage_scale}]})
        if path == f"/flood-monitoring/id/stations/{REF}/stageScale":
            return httpx.Response(200, json={"items": {"typicalRangeLow": 1.072, "typicalRangeHigh": 1.56}})
        if path == f"/flood-monitoring/id/stations/{REF}/readings":
            return httpx.Response(200, json={"items": [{
                "measure": f"https://{EA}/flood-monitoring/id/measures/{REF}-level-stage-i-15_min-mASD",
                "value": 1.316, "dateTime": "2026-09-29T09:00:00Z"}]})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(flows.httpx, "get", lambda url, **kw: client.get(url, **kw))
    return asked


def test_http_stage_scale_link_is_fetched_over_https(monkeypatch):
    asked = _fake_ea(monkeypatch, f"http://{EA}/flood-monitoring/id/stations/{REF}/stageScale")
    st = flows._nearest_level_station(51.4856, -1.0913)
    assert st is not None
    assert (st.typical_low, st.typical_high) == (1.072, 1.56)
    assert st.index == pytest.approx((1.316 - 1.072) / (1.56 - 1.072))
    assert st.label == "normal"
    assert any(u.startswith(f"https://{EA}/flood-monitoring/id/stations/{REF}/stageScale") for u in asked)


def test_stage_scale_link_off_the_ea_host_is_not_fetched(monkeypatch):
    asked = _fake_ea(monkeypatch, f"http://example.com/flood-monitoring/id/stations/{REF}/stageScale")
    st = flows._nearest_level_station(51.4856, -1.0913)
    assert st is not None and st.level_m == 1.316   # the reading still comes through
    assert st.typical_low is None and st.index is None
    assert not any("example.com" in u for u in asked)


def test_ea_link():
    path = f"/flood-monitoring/id/stations/{REF}/stageScale"
    assert flows._ea_link(f"http://{EA}{path}") == f"https://{EA}{path}"
    assert flows._ea_link(f"https://{EA}{path}") == f"https://{EA}{path}"
    assert flows._ea_link(f"http://{EA}.evil.example{path}") is None
    assert flows._ea_link(f"ftp://{EA}{path}") is None


def test_the_latest_reading_names_its_measure_for_the_trend(monkeypatch):
    _fake_ea(monkeypatch, f"https://{EA}/flood-monitoring/id/stations/{REF}/stageScale")
    st = flows._nearest_level_station(51.4856, -1.0913)
    assert st.measure == f"{REF}-level-stage-i-15_min-mASD"


# ------------------------------------------------------------------------------ too high to swim
# The words beside the pollution level (roadmap task 8): "high" above the gauge's usual range,
# "rising fast" up more than a fifth of that range in six hours, both only from a gauge on the
# spot's own river and a current reading; and the EA's flood alerts and warnings in force.
NOW = datetime(2026, 10, 3, 13, 30, tzinfo=UTC)


def _build_site():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_site
    return build_site


def test_flow_state_word_rules():
    assert flows.flow_state(1.01, True, None) == "high"
    assert flows.flow_state(1.0, True, None) is None            # at the top of the usual range, not above it
    assert flows.flow_state(0.5, True, 0.21) == "rising fast"
    assert flows.flow_state(0.5, True, 0.2) is None             # more than a fifth, not a fifth
    assert flows.flow_state(1.4, True, 0.5) == "high"           # above the range says more than rising
    assert flows.flow_state(0.5, True, -0.4) is None            # falling
    for other in (False, None):                                 # a tributary's gauge, or a lake's: not this river
        assert flows.flow_state(1.5, other, 0.5) is None
    assert flows.flow_state(None, True, None) is None           # a stale reading has no index
    assert flows.flow_state(None, True, 0.3) == "rising fast"   # no range position, but a rise in the last six hours


def _readings(start: datetime, values: list[float], step_min: int = 15) -> list[tuple[str, float]]:
    """Readings from `start`, `step_min` apart, newest first as the API sorts them."""
    pts = [((start + timedelta(minutes=step_min * i)).isoformat().replace("+00:00", "Z"), v) for i, v in enumerate(values)]
    return pts[::-1]


def test_the_rise_counts_only_the_last_six_hours():
    six = _readings(NOW - timedelta(hours=5, minutes=45), [0.30 + 0.02 * i for i in range(24)])   # 24 readings, 15 min apart
    assert flows.rise_over_window(six, NOW) == {"rise_m": 0.46, "from": "2026-10-03T07:45:00Z", "to": "2026-10-03T13:30:00Z"}
    # Readings from before the window do not count: a rise yesterday is not rising now.
    assert flows.rise_over_window(_readings(NOW - timedelta(hours=30), [0.3, 0.9, 1.5, 2.0]), NOW) is None
    # Two readings 15 minutes apart are too short a span to call a trend.
    assert flows.rise_over_window(_readings(NOW - timedelta(minutes=15), [0.3, 0.9]), NOW) is None
    assert flows.rise_over_window([], NOW) is None
    # A falling river rises by a negative amount; an unreadable time is skipped.
    fall = _readings(NOW - timedelta(hours=2), [1.0, 0.9, 0.8], step_min=60) + [("not a time", 5.0)]
    assert flows.rise_over_window(fall, NOW)["rise_m"] == -0.2


def _fake_ea_paths(monkeypatch, routes: dict) -> list[httpx.Request]:
    """The EA host over https only, answering the given paths; anything else is a 404."""
    asked = []

    def handler(req: httpx.Request) -> httpx.Response:
        asked.append(req)
        if req.url.host != EA or req.url.scheme != "https" or req.url.path not in routes:
            return httpx.Response(404)
        return httpx.Response(200, json=routes[req.url.path])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(flows.httpx, "get", lambda url, **kw: client.get(url, **kw))
    return asked


def test_recent_levels_reads_one_measure_newest_first(monkeypatch):
    m = "F1903-level-stage-i-15_min-m"
    asked = _fake_ea_paths(monkeypatch, {f"/flood-monitoring/id/measures/{m}/readings": {"items": [
        {"dateTime": "2026-10-03T13:15:00Z", "value": 0.339}, {"dateTime": "2026-10-03T13:00:00Z", "value": "bad"},
        {"dateTime": "2026-10-03T12:45:00Z", "value": 0.339}]}})
    assert flows.recent_levels(m) == [("2026-10-03T13:15:00Z", 0.339), ("2026-10-03T12:45:00Z", 0.339)]
    assert asked[0].url.params["_limit"] == "24" and "_sorted" in asked[0].url.params
    with pytest.raises(ValueError):
        flows.recent_levels("../stations/x")   # never a path out of the measures


# A response as the live service gives it (3 Oct 2026 it held one item, 011WAFED at severity 4).
FLOODS = {"items": [
    {"floodAreaID": "123WAF456", "description": "River Wharfe at Ilkley", "severityLevel": 3, "severity": "Flood alert",
     "timeRaised": "2026-10-03T09:20:22"},
    {"floodAreaID": "123FWF789", "description": "River Wharfe at Burley", "severityLevel": 2, "severity": "Flood warning"},
    {"floodAreaID": "011WAFED", "description": "Rivers Ehen, Calder, Irt and Esk", "severityLevel": 4,
     "severity": "Warning no longer in force"},
    {"floodAreaID": "bad id/../x", "description": "Odd", "severityLevel": 3},
    {"floodAreaID": "X", "severityLevel": None}]}


def test_flood_alerts_in_force_most_severe_first(monkeypatch):
    asked = _fake_ea_paths(monkeypatch, {"/flood-monitoring/id/floods": FLOODS})
    found = flows.flood_alerts(53.925, -1.82)
    assert [(a["severity_level"], a["severity"], a["area"]) for a in found] == [
        (2, "Flood warning", "River Wharfe at Burley"), (3, "Flood alert", "River Wharfe at Ilkley"), (3, "Flood alert", "Odd")]
    assert found[0]["url"] == "https://check-for-flooding.service.gov.uk/target-area/123FWF789"
    assert found[2]["url"] is None and found[2]["area_id"] is None   # an id that is not one makes no link
    assert dict(asked[0].url.params) == {"lat": "53.925", "long": "-1.82", "dist": "10"}
    assert "011WAFED" not in str(found)   # "no longer in force" is never shown as in force


def test_a_failed_flood_lookup_raises_so_none_and_unchecked_differ(monkeypatch):
    _fake_ea_paths(monkeypatch, {})
    with pytest.raises(httpx.HTTPError):
        flows.flood_alerts(53.9, -1.8)


def _state(level: float, observed: datetime, river: str = "River Wharfe") -> flows.RiverState:
    return flows.RiverState(station="Addingham", river=river, lat=53.94, lon=-1.88, level_m=level, typical_low=0.189,
                            typical_high=1.87, observed_at=observed.isoformat().replace("+00:00", "Z"), rloi="8001",
                            measure="F1903-level-stage-i-15_min-m")


def test_attach_flow_state_words_and_flood_alerts():
    """A river above its usual range, one rising fast, one high at a gauge on a tributary, a stale
    reading that was high, a lake under a live flood alert, and a spot where the EA did not answer."""
    bs = _build_site()
    states = {"high": _state(2.1, NOW - timedelta(minutes=20)),            # index 1.14
              "rising": _state(0.6, NOW - timedelta(minutes=15)),          # index 0.24, up 0.40 m in six hours
              "tributary": _state(2.1, NOW - timedelta(minutes=20), river="Town Beck"),
              "stale": _state(2.1, NOW - timedelta(hours=30)),
              "lake": None, "down": None}
    spots = [{"id": k, "name": k, "lat": 53.9 + i / 100, "lon": -1.8, "river": "River Wharfe"} for i, k in enumerate(states)]
    spots[4]["river"] = None
    at = {s["lat"]: states[s["id"]] for s in spots}
    bs.attach_river_levels(spots, lookup=lambda lat, lon, dist, river: at[lat], workers=1, now=NOW)
    asked_trend = []

    def trend(measure):
        asked_trend.append(measure)
        return _readings(NOW - timedelta(hours=5, minutes=45), [0.2 + 0.4 * i / 23 for i in range(24)])   # 0.40 m of a 1.68 m range

    def alerts(lat, lon):
        if lat == spots[5]["lat"]:
            raise httpx.ConnectTimeout("EA timed out")
        if lat == spots[4]["lat"]:
            return [{"severity_level": 3, "severity": "Flood alert", "area": "River Wharfe at Ilkley", "area_id": "123WAF456",
                     "url": "https://check-for-flooding.service.gov.uk/target-area/123WAF456", "raised": None}]
        return []

    counts = bs.attach_flow_state(spots, trend=trend, alerts=alerts, any_alerts=lambda: True, workers=1, now=NOW)
    by = {s["id"]: s for s in spots}
    assert by["high"]["flow_state"] == "high"
    assert by["rising"]["flow_state"] == "rising fast"
    assert by["rising"]["river_state"]["rise_6h_m"] == 0.4
    assert (by["rising"]["river_state"]["rise_from"], by["rising"]["river_state"]["rise_to"]) == ("2026-10-03T07:45:00Z", "2026-10-03T13:30:00Z")
    assert by["tributary"]["flow_state"] is None   # its gauge is on Town Beck, not the Wharfe
    assert by["stale"]["river_state"]["stale"] is True and by["stale"]["flow_state"] is None   # never "high" from a stale reading
    assert by["lake"]["flow_state"] is None and by["lake"]["flood_alerts"][0]["severity"] == "Flood alert"
    assert by["down"]["flood_alerts"] is None and by["high"]["flood_alerts"] == []
    assert counts == {"river_high": 1, "river_rising_fast": 1, "flood_alerts": 1, "flood_alerts_unchecked": 1}
    assert len(asked_trend) == 1   # asked only where it could set the word: current, own river, not already high
    assert all("days" not in s and "now" not in s for s in spots)   # nothing here touches the pollution level


def test_a_failed_trend_leaves_no_word_and_no_rise():
    bs = _build_site()
    spots = [{"id": "a", "name": "a", "lat": 53.9, "lon": -1.8, "river": "River Wharfe",
              "river_state": {**flows.reading_fields(_state(0.6, NOW), NOW), "same_river": True, "measure": "m"}}]

    def broken(measure):
        raise httpx.ReadTimeout("slow")

    bs.attach_flow_state(spots, trend=broken, alerts=lambda lat, lon: [], any_alerts=lambda: True, workers=1, now=NOW)
    assert spots[0]["flow_state"] is None and "rise_6h_m" not in spots[0]["river_state"]


def test_flood_alerts_are_asked_spot_by_spot_only_when_one_is_in_force_somewhere():
    bs = _build_site()
    asked = []

    def alerts(lat, lon):
        asked.append(lat)
        return []

    spots = [{"id": "a", "name": "a", "lat": 53.9, "lon": -1.8}, {"id": "b", "name": "b", "lat": 54.0, "lon": -1.9}]
    bs.attach_flow_state(spots, alerts=alerts, any_alerts=lambda: False, workers=1, now=NOW)
    assert asked == [] and [s["flood_alerts"] for s in spots] == [[], []]   # none in force in England: none here

    def refused():
        raise httpx.HTTPStatusError("403 Forbidden", request=httpx.Request("GET", f"https://{EA}/"), response=httpx.Response(403))

    counts = bs.attach_flow_state(spots, alerts=alerts, any_alerts=refused, workers=1, now=NOW)
    assert asked == [] and [s["flood_alerts"] for s in spots] == [None, None]   # unchecked, not "none"
    assert counts["flood_alerts_unchecked"] == 2

    bs.attach_flow_state(spots, alerts=alerts, any_alerts=lambda: True, workers=1, now=NOW)
    assert sorted(asked) == [53.9, 54.0]


def test_floods_in_force_anywhere_ignores_warnings_no_longer_in_force(monkeypatch):
    _fake_ea_paths(monkeypatch, {"/flood-monitoring/id/floods": {"items": [FLOODS["items"][2]]}})
    assert flows.floods_in_force_anywhere() is False   # 3 Oct 2026: one item, severity 4
    _fake_ea_paths(monkeypatch, {"/flood-monitoring/id/floods": FLOODS})
    assert flows.floods_in_force_anywhere() is True
