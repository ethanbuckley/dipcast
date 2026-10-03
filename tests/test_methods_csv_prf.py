"""The trust layer (roadmap task 7): the method page, the scored overflow-days published as a CSV,
and the comparison with the Environment Agency's daily risk prediction at the inland bathing
waters (scripts/compare_prf.py)."""

import json
import re
import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dipcast" / "api" / "static"
PROCESSED = ROOT / "data" / "processed"
TZ = "Europe/London"


def _script(name):
    sys.path.insert(0, str(ROOT / "scripts"))
    return __import__(name)


# ------------------------------------------------------------------ the method page

VOID = {"meta", "link", "br", "img", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "track",
        "rect", "path", "circle"}   # the brand mark's SVG shapes are written without closing tags' children


class _Nesting(HTMLParser):
    """Every element closed, in order. Not a full validator: the page was also checked with the W3C
    Nu checker when it was written (no messages)."""

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1] if self.stack else 'nothing'} at {self.getpos()}")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def _rows(page: str, section: str) -> list[list[str]]:
    """The numeric cells of each row of the table in a section, in order."""
    body = re.search(rf'<section id="{section}">(.*?)</section>', page, re.DOTALL).group(1)
    return [re.findall(r'<td class="num">([^<]*)</td>', tr) for tr in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)][1:]


def test_the_method_page_is_well_formed_with_one_title_and_working_anchors():
    page = (STATIC / "methods.html").read_text()
    p = _Nesting()
    p.feed(page)
    assert not p.errors and p.stack == [], (p.errors, p.stack)
    assert "<title>Method · SwimSignal</title>" in page and page.count("<h1>") == 1
    ids = re.findall(r'\bid="([^"]+)"', page)
    assert len(ids) == len(set(ids))
    assert all(a in ids for a in re.findall(r'href="#([^"]+)"', page))
    for h in ("level", "how", "holdout", "leads", "ecoli", "live", "failures", "corrections"):
        assert f'<section id="{h}">' in page, h


def test_the_method_page_is_linked_from_accuracy_and_every_foot():
    assert 'href="/methods"' in (STATIC / "verification.html").read_text()
    for name in ("about.html", "feedback.html", "privacy.html", "terms.html", "testing.html", "verification.html", "methods.html"):
        foot = (STATIC / name).read_text().split('<footer class="site-foot">')[1]
        assert '<a href="/methods">Method</a>' in foot, name
    app = (ROOT / "src" / "dipcast" / "site" / "index.html").read_text().split('<footer class="fine">')[1]
    assert '<a href="methods.html">Method</a>' in app


def test_the_method_page_tables_are_the_committed_test_results():
    page = (STATIC / "methods.html").read_text()
    hold = pd.read_csv(PROCESSED / "verification_2025.csv").set_index("model")
    want = [hold.loc[m] for m in ("dipcast (pooled + site offsets)", "pooled only", "climatology (site rate)", "rain rule (>10mm/48h)")]
    assert _rows(page, "holdout") == [[f"{r.brier:.4f}", f"{r.auc:.3f}", f"{r.brier_skill_vs_clim:.2f}"] for r in want]
    assert f"{int(want[0].n):,} overflow-days, {100 * want[0].base_rate:.2f}% of them" in page
    leads = pd.read_csv(PROCESSED / "verification_leads_2025.csv").set_index("source")
    want = [leads.loc["reanalysis (upper bound)"]] + [leads.loc[f"forecast lead {k}, isotonic cross-fitted (odd/even months)"] for k in range(5)]
    assert _rows(page, "leads") == [[f"{r.brier:.4f}", f"{r.auc:.3f}", f"{r.brier_skill_vs_clim:.2f}"] for r in want]
    m = json.loads((PROCESSED / "ecoli_model_eval.json").read_text())
    by = lambda rows, name: next(r for r in rows if r["model"] == name)
    full = "rain + spill exposure + season (dipcast)"
    want = [by(m["loyo"], "climatology by type"), by(m["loyo"], "rain only"), by(m["loyo"], full),
            by(m["loso"], "rain only"), by(m["loso"], full), by(m["forward"]["table"], "rain only"), by(m["forward"]["table"], full)]
    assert _rows(page, "ecoli") == [[f"{r['brier']:.3f}", f"{r['auc']:.2f}", f"{r['auc_river']:.2f}", f"{r['auc_lake']:.2f}"] for r in want]
    assert f"{m['n_samples']:,} Environment Agency samples at {m['n_sites']} inland bathing waters" in page


def test_the_method_page_states_the_level_cuts_and_constants_the_code_uses():
    from dipcast import config
    page = (STATIC / "methods.html").read_text()
    rules = (ROOT / "src" / "dipcast" / "site" / "levels.js").read_text()
    spill = [round(100 * float(x)) for x in re.search(r"const SPILL_CUTS = \[([^\]]+)\]", rules).group(1).split(",")]
    ecoli = [round(100 * float(x)) for x in re.search(r"ECOLI_CUTS = \[([^\]]+)\]", rules).group(1).split(",")]
    cells = [re.findall(r'<td class="num">([^<]*)</td>', tr) for tr in re.findall(r"<tr>(.*?)</tr>", page.split('id="level"')[1].split("</table>")[0], re.DOTALL)][1:]
    assert [c[0] for c in cells] == [f"under {spill[0]}", f"{spill[0]} to {spill[1]}", f"{spill[1]} to {spill[2]}", f"{spill[2]} or more"]
    assert [c[1] for c in cells] == [f"under {ecoli[0]}%", f"{ecoli[0]}% to {ecoli[1]}%", f"{ecoli[1]}% to {ecoli[2]}%", f"{ecoli[2]}% or more"]
    assert f"up to {config.MAX_UPSTREAM_KM:g} km" in page and f"{config.RIVER_VELOCITY_MS:g} m a second on rivers" in page
    assert f"{config.LAKE_VELOCITY_MS:g} m a second across a lake" in page and f"90% in {config.T90_HOURS:g} hours" in page
    assert f"within the last {config.RECENT_SPILL_HOURS:g} hours" in page


# ------------------------------------------------------------------ the scored rows as a CSV

def _state(tmp_path, monkeypatch):
    from dipcast import config
    monkeypatch.setattr(config, "STATE", tmp_path)
    monkeypatch.setattr(config, "PROCESSED", tmp_path)
    monkeypatch.setattr(config, "DUCKDB_PATH", tmp_path / "t.duckdb")
    return config


def _two_overflows(tmp_path, fl):
    """Two overflows, A (company X) spilling on the 14th and B (company Y) never, both watched every
    two hours on the 14th to the 16th, with one forecast of 0.4 issued at 07:30 on the 14th."""
    ov = pd.DataFrame({"site_id": ["A", "B"], "has_live": [True, True], "weight": [0.5, 0.5]})
    days = pd.date_range("2026-09-13", periods=3, freq="D", tz=TZ)
    fl.log_forecast(pd.Timestamp("2026-09-14 07:30", tz=TZ), 54.0, -2.0, "river", "R", 0.1, days, np.full(3, 0.1), ov,
                    np.full((2, 3), 0.5), np.full((2, 3), 0.4), version="v-test")
    pd.DataFrame({"site_id": ["A", "B"], "company": ["X", "Y"], "status": [0, 0],
                  "status_start": pd.to_datetime(["2026-09-01", "2026-09-01"], utc=True),
                  "latest_event_start": pd.to_datetime(["2026-09-14 10:00", None], utc=True),
                  "latest_event_end": pd.to_datetime(["2026-09-14 12:00", None], utc=True),
                  "fetched_at": pd.to_datetime(["2026-09-14 13:00", "2026-09-14 13:00"], utc=True)}).to_parquet(
        tmp_path / "live_history.parquet", index=False)
    pd.DataFrame({"site_id": ["A", "B"], "company": ["X", "Y"], "lta_spills": [36.5, 36.5]}).to_parquet(
        tmp_path / "overflows.parquet", index=False)
    every2h = int(sum(1 << (h * 2) for h in range(0, 24, 2)))
    pd.DataFrame({"site_id": ["A", "A", "A", "B", "B", "B"], "day": pd.to_datetime(["2026-09-14", "2026-09-15", "2026-09-16"] * 2),
                  "n_known": 12, "n_unknown": 0, "n_stale": 0, "slots": every2h}).to_parquet(tmp_path / fl.COVERAGE_FILE, index=False)


def test_the_scorer_writes_one_row_per_scored_overflow_day(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    _two_overflows(tmp_path, fl)
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))
    rows = pd.read_csv(tmp_path / fl.SCORED_CSV)
    assert len(rows) == out["n_scored"] == out["scored_csv"]["rows"] == 4
    assert list(rows.columns) == fl.SCORED_COLUMNS == out["scored_csv"]["columns"]
    assert rows[["overflow_id", "company", "day", "lead", "observed"]].values.tolist() == [
        ["A", "X", "2026-09-14", 0, 1], ["B", "Y", "2026-09-14", 0, 0], ["A", "X", "2026-09-15", 1, 0], ["B", "Y", "2026-09-15", 1, 0]]
    assert set(rows["issued_at"]) == {"2026-09-14T07:30+01:00"}
    assert rows["forecast_raw"].eq(0.5).all() and rows["forecast_calibrated"].eq(0.4).all()
    assert rows["climatology"].eq(0.1).all()   # 36.5 spills a year / 365
    # The file recomputes the published scores.
    y, p = rows["observed"].to_numpy(), rows["forecast_calibrated"].to_numpy()
    assert abs(np.mean((p - y) ** 2) - out["overall"]["calibrated"]["brier"]) < 1e-9
    assert abs(np.mean((rows["climatology"].to_numpy() - y) ** 2) - out["overall"]["climatology_brier"]) < 1e-9


def test_a_scorer_run_with_nothing_scored_leaves_a_header_and_no_old_rows(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    from dipcast import forecast_log as fl
    (tmp_path / fl.SCORED_CSV).write_text("overflow_id\nstale\n")
    monkeypatch.setattr(fl, "verify_ecoli", lambda as_of: {"n_scored": 0})
    out = fl.verify_live(as_of=date(2026, 9, 16))   # no forecasts logged at all
    rows = pd.read_csv(tmp_path / fl.SCORED_CSV)
    assert out["n_scored"] == 0 and len(rows) == 0 and list(rows.columns) == fl.SCORED_COLUMNS


def test_the_published_csv_carries_the_credits_and_only_rows_that_match_the_scores(tmp_path, monkeypatch):
    bs = _script("build_site")   # before the state is moved: build_site binds config.PROCESSED in a default argument
    config = _state(tmp_path / "state", monkeypatch)
    config.STATE.mkdir()
    from dipcast.forecast_log import SCORED_COLUMNS, SCORED_CSV
    pd.DataFrame([["A", "X", "2026-09-14", 0, "2026-09-14T07:30+01:00", 0.5, 0.4, 0.1, 1]] * 2, columns=SCORED_COLUMNS).to_csv(
        config.STATE / SCORED_CSV, index=False)
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    ver = site / "data" / "verification.json"
    ver.write_text(json.dumps({"live": {"n_scored": 2, "scored_csv": {"rows": 2}}}))
    credits = bs.data_credits("https://example.org/")
    assert bs.publish_scored_csv(site, credits, "https://example.org/") == 2
    text = (site / "data" / "verification_live.csv").read_text()
    head = [x for x in text.splitlines() if x.startswith("#")]
    assert credits["attribution"] in "\n".join(head) and credits["full"] in "\n".join(head)
    assert all(u in "\n".join(head) for u in credits["licences"].values())
    assert "https://example.org/methods.html" in "\n".join(head)
    assert len(pd.read_csv(site / "data" / "verification_live.csv", comment="#")) == 2
    # Rows that disagree with the published count are not published, and an old copy goes.
    ver.write_text(json.dumps({"live": {"n_scored": 3, "scored_csv": {"rows": 2}}}))
    assert bs.publish_scored_csv(site, credits, "https://example.org/") is None
    assert not (site / "data" / "verification_live.csv").exists()
    # A state from before the scorer wrote rows: nothing to publish, and no warning needed.
    ver.write_text(json.dumps({"live": {"n_scored": 2}}))
    assert bs.publish_scored_csv(site, credits, "https://example.org/") is None


def test_the_accuracy_page_links_the_csv_only_where_it_is_published():
    page = (STATIC / "verification.html").read_text()
    assert "if (L.scored_csv && d.credits) live += `<p class=\"small muted\"><a href=\"data/verification_live.csv\" download>" in page
    src = (ROOT / "scripts" / "build_site.py").read_text()
    assert 'health["scored_rows_published"] = publish_scored_csv(SITE, credits, site_url())' in src
    assert '("prf", "prf_comparison.json")' in (ROOT / "src" / "dipcast" / "forecast_log.py").read_text()


# ------------------------------------------------------------------ the Environment Agency comparison

def _item(bw, at, level, origin="NON_PRF_SITE", hours=24, comment="No pollution incidents reported"):
    t = pd.Timestamp(at)
    v = lambda x: {"_value": x.strftime("%Y-%m-%dT%H:%M:%S")}
    out = {"stp_bathingWater": f"http://environment.data.gov.uk/id/bathing-water/{bw}", "predictedAt": v(t),
           "publishedAt": v(t + pd.Timedelta(minutes=11)), "expiresAt": v(t + pd.Timedelta(hours=hours) - pd.Timedelta(minutes=1)),
           "predictedOn": {"_value": t.date().isoformat()}, "riskLevel": f"http://environment.data.gov.uk/def/bwq-stp/{level}",
           "comment": {"_value": comment}}
    if origin:
        out["prfOriginType"] = origin
    return out


SITES = pd.DataFrame({"bw_id": ["uka-1", "ukb-2", "ukc-3"], "name": ["River A", "Lake B", "River C"],
                      "kind": ["river", "lake", "river"], "lat": [54.0, 54.1, 54.2], "lon": [-2.0, -2.1, -2.2]})


def test_parse_reads_the_eas_linked_data_as_local_times():
    c = _script("compare_prf")
    p = c.parse([_item("uka-1", "2026-07-01 08:30", "normal"), _item("ukb-2", "2026-07-01 16:00", "increased", origin=None)])
    assert p["bw_id"].tolist() == ["uka-1", "ukb-2"] and p["level"].tolist() == ["normal", "increased"]
    assert p["origin"].tolist()[0] == "NON_PRF_SITE" and p["origin"].isna().tolist()[1]
    assert str(p["published_at"].iloc[0]) == "2026-07-01 08:41:00+01:00"


def test_the_ea_prediction_scored_is_the_one_in_force_when_the_sample_was_taken():
    c = _script("compare_prf")
    pred = c.parse([_item("uka-1", "2026-07-01 08:30", "normal"),
                    _item("uka-1", "2026-07-01 16:00", "increased", origin=None, comment="Risk of reduced water quality due to sewage"),
                    _item("uka-1", "2026-07-02 08:30", "normal")])
    t = lambda s: pd.Timestamp(s, tz=TZ)
    samples = pd.DataFrame({"bw_id": ["uka-1"] * 4 + ["ukc-3"],
                            "sample_time": [t("2026-07-01 12:00"), t("2026-07-01 17:00"), t("2026-07-02 08:35"), t("2026-07-02 09:00"), t("2026-07-01 12:00")]})
    got = c.ea_at_samples(samples, pred)
    # 08:35 on the 2nd: that day's 08:30 prediction was published at 08:41, so the notice of the 1st is in force.
    assert got["ea_level"].tolist()[:4] == ["normal", "increased", "increased", "normal"]
    assert pd.isna(got["ea_level"].iloc[4])   # a site with no prediction is not read as "normal"


def test_swimsignal_scored_is_the_latest_estimate_issued_before_the_sample():
    c = _script("compare_prf")
    t = lambda s: pd.Timestamp(s, tz=TZ)
    d = date(2026, 9, 20)
    points = pd.DataFrame({"issued_at": [t("2026-09-19 20:00"), t("2026-09-20 07:30"), t("2026-09-20 13:00")],
                           "lat": 54.0, "lon": -2.0, "target_day": d, "lead": [1, 0, 0], "p_ecoli": [0.1, 0.3, 0.9]})
    samples = pd.DataFrame({"bw_id": ["uka-1", "ukb-2"], "sample_time": [t("2026-09-20 11:00"), t("2026-09-20 11:00")]})
    got = c.swimsignal_at_samples(samples, points, SITES)
    assert got["ss_p_ecoli"].tolist()[0] == 0.3 and got["ss_lead"].tolist()[0] == 0
    assert pd.isna(got["ss_p_ecoli"].iloc[1])   # no forecast logged for that spot


def test_a_site_counts_as_having_a_prf_if_it_had_one_on_any_day():
    c = _script("compare_prf")
    pred = c.parse([_item("uka-1", "2026-07-01 08:30", "normal"), _item("uka-1", "2026-07-02 08:30", "increased", origin="PRF_PROVIDED"),
                    _item("ukb-2", "2026-07-01 08:30", "normal"), _item("ukz-9", "2026-07-01 08:30", "normal", origin="PRF_PROVIDED")])
    s = c.prf_sites(pred, SITES)
    assert s["n_inland"] == 3 and s["n_in_predictions"] == 2 and s["n_with_prf"] == 1
    assert s["with_prf"] == [{"bw_id": "uka-1", "name": "River A"}] and s["not_in_predictions"] == [{"bw_id": "ukc-3", "name": "River C"}]


def test_the_verdict_names_the_better_forecast_and_the_number_of_samples():
    c = _script("compare_prf")
    df = pd.DataFrame({"bw_id": ["uka-1", "uka-1", "ukb-2", "ukb-2"], "kind": ["river", "river", "lake", "lake"],
                       "ecoli": [1200, 100, 50, 40], "ea_level": ["normal", "increased", "normal", "normal"],
                       "ss_p_ecoli": [0.6, 0.1, 0.02, 0.02]})
    both = {**c.score(df, {"river": 0.2, "lake": 0.03}), "first": "2026-09-16", "last": "2026-09-28"}
    assert both["ea"]["brier"] == 0.5 and both["ea"]["hits"] == 0 and both["ea"]["false_alarms"] == 1
    assert abs(both["swimsignal"]["brier"] - (0.16 + 0.01 + 0.0004 + 0.0004) / 4) < 1e-12 and both["swimsignal"]["hits"] == 1
    v = c.verdict(both)
    assert v.startswith("SwimSignal's E. coli estimate did better than the Environment Agency's daily risk prediction on the 4 samples both covered, at 2 sites, 16 to 28 September 2026")
    assert "Brier score 0.043 against 0.500" in v and "Too few samples to judge" in v
    assert "The Agency's prediction did not warn before the one sample over 900; SwimSignal's estimate did." in v
    assert c.verdict({"n_samples": 0}) == "No sample had both forecasts, so there is nothing to compare."


def test_the_season_is_fetched_once_and_a_second_refusal_stops_the_run(tmp_path, monkeypatch):
    c = _script("compare_prf")
    monkeypatch.setattr(c, "PAUSE_S", 0.0)
    asked = []
    refused_once = set()

    def handler(request):
        day = request.url.params["predictedOn"]
        asked.append(day)
        if day == "2026-07-02" and day not in refused_once:
            refused_once.add(day)
            return httpx.Response(403)
        if day == "2026-07-03":
            return httpx.Response(403)
        return httpx.Response(200, json={"result": {"items": [_item("uka-1", f"{day} 08:30", "normal")]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    days = c.season_days(date(2026, 7, 1), date(2026, 7, 4))
    got = c.fetch_season(days, cache=tmp_path, client=client, retry_wait_s=0)
    assert sorted(got["items"]) == ["2026-07-01", "2026-07-02"] and got["refused"] and got["failed"] == {"2026-07-03": "HTTP 403"}
    assert asked == ["2026-07-01", "2026-07-02", "2026-07-02", "2026-07-03", "2026-07-03"]   # 07-04 never asked
    assert got["n_refusals"] == 3
    asked.clear()
    again = c.fetch_season(days[:2], cache=tmp_path, client=client, retry_wait_s=0)
    assert asked == [] and again["n_requests"] == 0 and sorted(again["items"]) == ["2026-07-01", "2026-07-02"]


@pytest.mark.skipif(not (PROCESSED / "prf_comparison.json").exists(), reason="the comparison has not been run")
def test_the_committed_comparison_says_who_did_better_and_adds_up():
    d = json.loads((PROCESSED / "prf_comparison.json").read_text())
    s, b = d["sites"], d["both"]
    assert s["n_inland"] == 38 and 0 <= s["n_with_prf"] <= s["n_in_predictions"] <= 38
    assert f"on the {b['n_samples']} samples both covered" in d["verdict"]
    assert ("did better" in d["verdict"]) or ("scored the same" in d["verdict"])
    rows = pd.DataFrame(d["rows"])
    assert len(rows) == b["n_samples"] and rows["bw_id"].nunique() == b["n_sites"]
    y = (rows["ecoli"] > 900).astype(int).to_numpy()
    assert y.sum() == b["n_over_threshold"]
    assert abs(np.mean(((rows["ea_level"] == "increased").astype(int) - y) ** 2) - b["ea"]["brier"]) < 1e-9
    assert abs(np.mean((rows["swimsignal"].to_numpy() - y) ** 2) - b["swimsignal"]["brier"]) < 2e-3   # rows keep 3 decimals
    f = d["fetch"]
    assert f["days_fetched"] == f["days_requested"] or f["refused"] or f["failed"]
    assert d["season"] == ["2026-05-15", "2026-09-30"]
