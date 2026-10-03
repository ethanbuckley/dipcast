"""Compare SwimSignal's E. coli estimate with the Environment Agency's daily risk prediction at
the 38 inland bathing waters, both scored against the EA's own samples, and write
data/processed/prf_comparison.json, which the Accuracy page shows.

The EA publishes a risk prediction for each designated bathing water every day of the season
(15 May to 30 September): `stp-risk-prediction.json?predictedOn=<day>`, one row per site and
prediction, "normal" or "increased". Where the EA runs a pollution risk forecast (PRF) for the site,
`prfOriginType` is PRF_PROVIDED and "increased" is that forecast's warning; elsewhere it is
NON_PRF_SITE and the level is "increased" only when the EA has posted a notice (a pollution incident,
an algal bloom). This script counts which inland sites had a PRF on any day of the season, then
scores the EA's level in force when each sample was taken against the sample (over 900 E. coli per
100 ml), beside SwimSignal's E. coli estimate for the same sample from the forecast log.

Run it by hand after the season, then commit the JSON. The EA's bathing-water service refuses
GitHub's runners (HTTP 403 from its gateway since 28 Sep 2026), so it is not part of the build:

    DIPCAST_STATE=<state dir> DIPCAST_CACHE=<state dir>/cache uv run python scripts/compare_prf.py

The state directory supplies the forecast log (dipcast.duckdb) and this season's samples
(ecoli_samples.parquet); `--refresh-samples` fetches the samples again first (it writes the state's
copy). One request per season day, with a pause between requests and a User-Agent that names the
project. Each day's raw response is cached under DIPCAST_CACHE/ea_prf/, so a rerun costs nothing.
Data: Environment Agency, Open Government Licence v3.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import httpx
import numpy as np
import pandas as pd

from dipcast import config
from dipcast.forecast_log import (
    ECOLI_SAMPLES,
    LOCAL_TZ,
    match_points_to_sites,
    refresh_ecoli_samples,
)

log = logging.getLogger("compare_prf")

URL = "https://environment.data.gov.uk/doc/bathing-water-quality/stp-risk-prediction.json"
SITES = config.RAW / "bathing_waters_inland.json"
OUT = config.PROCESSED / "prf_comparison.json"
CACHE_DIR = config.CACHE / "ea_prf"
SEASON = (date(2026, 5, 15), date(2026, 9, 30))   # the bathing season, England
THRESHOLD = 900          # E. coli per 100 ml: the inland "sufficient" limit, as everywhere else on the site
WARN_AT = 0.25           # SwimSignal's E. coli estimate at which a river spot's level is high (levels.js ECOLI_CUTS)
PAUSE_S = 2.0            # between requests to the EA
RETRY_WAIT_S = 90.0      # after a refusal, before the one retry
PAGE_SIZE = 500
CREDIT = ("Environment Agency bathing water risk predictions and samples © Environment Agency copyright and/or "
          "database right, Open Government Licence v3.0. Compared by SwimSignal; the EA does not endorse it.")


def _val(x):
    """A value from the EA's linked-data JSON: {'_value': ...} or a plain value."""
    return x.get("_value") if isinstance(x, dict) else x


def _today() -> date:
    """Today in England, where the EA dates its predictions."""
    return pd.Timestamp.now(tz=LOCAL_TZ).date()


def season_days(start: date = SEASON[0], end: date = SEASON[1]) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def fetch_day(day: date, client: httpx.Client, cache: Path = CACHE_DIR) -> tuple[list[dict], bool]:
    """All of the EA's predictions made on `day`: (items, from_cache). A past day's response is
    cached and never asked for again; today's is not, since more predictions may follow."""
    f = cache / f"{day.isoformat()}.json"
    if f.exists():
        return json.loads(f.read_text()), True
    items, page = [], 0
    while True:
        r = client.get(URL, params={"predictedOn": day.isoformat(), "_pageSize": PAGE_SIZE, "_page": page})
        r.raise_for_status()
        got = r.json()["result"].get("items", [])
        items += got
        if len(got) < PAGE_SIZE:
            break
        page += 1
        time.sleep(PAUSE_S)
    if day < _today():
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(items))
    return items, False


def fetch_season(days: list[date], cache: Path = CACHE_DIR, client: httpx.Client | None = None,
                 retry_wait_s: float = RETRY_WAIT_S) -> dict:
    """Every day's predictions, from the cache where held. An HTTP 403 is the gateway refusing this
    machine. From a home connection on 3 Oct 2026 it refused after 55 requests 1.3 s apart, answered
    again within a minute, and refused once more after 62 requests 2.3 s apart, then answered after
    90 s: a limit on requests over a few minutes, it seems. So a refusal is retried once after
    `retry_wait_s`; a second one stops the run, since every later day would be refused too (as
    GitHub's runners are, since 28 Sep 2026)."""
    out = {"items": {}, "failed": {}, "refused": False, "n_requests": 0, "n_refusals": 0}
    own = client is None
    client = client or httpx.Client(timeout=60, headers=config.EA_HEADERS)
    try:
        for d in days:
            try:
                try:
                    items, cached = fetch_day(d, client, cache)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 403:
                        raise
                    out["n_requests"] += 1
                    out["n_refusals"] += 1
                    log.warning("EA gateway refused the request for %s (HTTP 403); trying again in %.0f s", d, retry_wait_s)
                    time.sleep(retry_wait_s)
                    items, cached = fetch_day(d, client, cache)
            except httpx.HTTPStatusError as e:
                out["n_requests"] += 1
                out["failed"][d.isoformat()] = f"HTTP {e.response.status_code}"
                if e.response.status_code == 403:
                    out["n_refusals"] += 1
                    out["refused"] = True
                    log.error("EA gateway refused the request for %s again (HTTP 403); not asking for the other days", d)
                    break
                log.warning("%s: %s", d, e)
                time.sleep(PAUSE_S)
                continue
            except httpx.HTTPError as e:
                out["n_requests"] += 1
                out["failed"][d.isoformat()] = type(e).__name__
                log.warning("%s: %s", d, e)
                time.sleep(PAUSE_S)
                continue
            out["items"][d.isoformat()] = items
            if not cached:
                out["n_requests"] += 1
                log.info("%s: %d predictions", d, len(items))
                time.sleep(PAUSE_S)
    finally:
        if own:
            client.close()
    return out


def parse(items: list[dict]) -> pd.DataFrame:
    """One row per prediction: bathing water id, when it was made, published and expires, the
    PRF origin and the level ('normal' or 'increased'), and the EA's comment."""
    rows = []
    for x in items:
        bw = str(x.get("stp_bathingWater") or "").rsplit("/", 1)[-1]
        if not bw:
            continue
        rows.append({"bw_id": bw, "predicted_on": _val(x.get("predictedOn")),
                     "predicted_at": _val(x.get("predictedAt")), "published_at": _val(x.get("publishedAt")),
                     "expires_at": _val(x.get("expiresAt")), "origin": x.get("prfOriginType"),
                     "level": str(x.get("riskLevel") or "").rsplit("/", 1)[-1] or None,
                     "comment": _val(x.get("comment"))})
    df = pd.DataFrame(rows, columns=["bw_id", "predicted_on", "predicted_at", "published_at", "expires_at",
                                     "origin", "level", "comment"])
    for c in ("predicted_at", "published_at", "expires_at"):   # published as local time with no offset
        df[c] = pd.to_datetime(df[c]).dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="shift_forward")
    return df


def prf_sites(pred: pd.DataFrame, sites: pd.DataFrame) -> dict:
    """Which inland sites appear in the predictions at all, and which had a PRF on any day."""
    p = pred[pred["bw_id"].isin(sites["bw_id"])]
    names = sites.set_index("bw_id")["name"]
    origin = p.groupby("bw_id")["origin"].agg(lambda s: sorted({str(v) for v in s}))
    with_prf = sorted(b for b, o in origin.items() if "PRF_PROVIDED" in o)
    return {"n_inland": len(sites), "n_in_predictions": int(p["bw_id"].nunique()),
            "n_with_prf": len(with_prf), "with_prf": [{"bw_id": b, "name": names.get(b)} for b in with_prf],
            "not_in_predictions": [{"bw_id": b, "name": names.get(b)} for b in sites["bw_id"] if b not in set(p["bw_id"])],
            "origin_rows": {str(k): int(v) for k, v in p["origin"].fillna("none").value_counts().items()},
            "days_with_any_prediction": int(p["predicted_on"].nunique())}


def ea_at_samples(samples: pd.DataFrame, pred: pd.DataFrame) -> pd.DataFrame:
    """For each sample, the EA prediction in force when it was taken: the latest one for that site
    published before the sample time and not yet expired. A sample with none keeps NaN."""
    s = samples.reset_index(drop=True).assign(_i=lambda d: d.index)
    m = s[["_i", "bw_id", "sample_time"]].merge(pred, on="bw_id", how="inner")
    m = m[(m["published_at"] <= m["sample_time"]) & (m["expires_at"] > m["sample_time"])]
    m = m.sort_values("published_at").groupby("_i").last()
    out = s.join(m[["predicted_at", "published_at", "origin", "level", "comment"]], on="_i").drop(columns="_i")
    return out.rename(columns={"predicted_at": "ea_predicted_at", "published_at": "ea_published_at",
                               "origin": "ea_origin", "level": "ea_level", "comment": "ea_comment"})


def swimsignal_at_samples(samples: pd.DataFrame, points: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """For each sample, SwimSignal's E. coli estimate for that spot and day from the latest forecast
    issued before the sample was taken (the live scorer's rule), with its lead in days."""
    p = points.dropna(subset=["p_ecoli"]).copy()
    p["bw_id"] = match_points_to_sites(p, sites)
    p = p.dropna(subset=["bw_id"])
    s = samples.reset_index(drop=True).assign(_i=lambda d: d.index, _day=lambda d: d["sample_time"].dt.date)
    m = s[["_i", "bw_id", "_day", "sample_time"]].merge(p, left_on=["bw_id", "_day"], right_on=["bw_id", "target_day"])
    m = m[m["issued_at"] < m["sample_time"]].sort_values("issued_at").groupby("_i").last()
    out = s.join(m[["issued_at", "lead", "p_ecoli"]], on="_i").drop(columns=["_i", "_day"])
    return out.rename(columns={"issued_at": "ss_issued_at", "lead": "ss_lead", "p_ecoli": "ss_p_ecoli"})


def load_points(db: Path) -> pd.DataFrame:
    """The spot forecast log's E. coli estimates (forecast_points), with issue times as local timestamps."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        p = con.execute("""SELECT epoch_ms(issued_at) AS issued_ms, lat, lon, target_day, lead, p_ecoli
                           FROM forecast_points WHERE p_ecoli IS NOT NULL""").df()
    finally:
        con.close()
    p["issued_at"] = pd.to_datetime(p["issued_ms"], unit="ms", utc=True).dt.tz_convert(LOCAL_TZ)
    p["target_day"] = pd.to_datetime(p["target_day"]).dt.date
    return p.drop(columns="issued_ms")


def contingency(warned: np.ndarray, y: np.ndarray) -> dict:
    """Hits, misses, false alarms and correct quiet days for a yes/no warning against exceedances."""
    w, y = warned.astype(bool), y.astype(bool)
    return {"hits": int((w & y).sum()), "misses": int((~w & y).sum()),
            "false_alarms": int((w & ~y).sum()), "quiet_correct": int((~w & ~y).sum())}


def score(df: pd.DataFrame, base_rate: dict[str, float]) -> dict:
    """Both forecasts on the same samples: the EA's level read as 1 (increased) or 0 (normal), and
    SwimSignal's estimate as a probability, each by Brier score (the mean squared difference from
    what happened, lower is better), beside always forecasting the training-period exceedance rate
    for rivers and lakes; and each as a yes/no warning (the EA's "increased"; SwimSignal's estimate
    at WARN_AT or more, where a river spot's level turns high)."""
    y = (df["ecoli"] > THRESHOLD).to_numpy().astype(int)
    ea = (df["ea_level"] == "increased").to_numpy().astype(float)
    ss = df["ss_p_ecoli"].to_numpy().astype(float)
    clim = df["kind"].map(base_rate).astype(float).fillna(float(y.mean()) if len(y) else 0.0).to_numpy()
    out = {"n_samples": len(df), "n_sites": int(df["bw_id"].nunique()), "n_over_threshold": int(y.sum())}
    if not len(df):
        return out
    out["ea"] = {"brier": float(np.mean((ea - y) ** 2)), "n_increased": int(ea.sum()), **contingency(ea > 0, y)}
    out["swimsignal"] = {"brier": float(np.mean((ss - y) ** 2)), "mean_estimate": float(ss.mean()),
                         "warn_at": WARN_AT, "n_warned": int((ss >= WARN_AT).sum()), **contingency(ss >= WARN_AT, y)}
    out["climatology_brier"] = float(np.mean((clim - y) ** 2))
    return out


def _span(a: str | None, b: str | None) -> str:
    """'16 to 28 September 2026', '30 May to 2 June 2026', or one day."""
    if not a or not b:
        return ""
    x, y = pd.Timestamp(a), pd.Timestamp(b)
    if x == y:
        return f"{x.day} {x:%B %Y}"
    left = f"{x.day}" if (x.year, x.month) == (y.year, y.month) else f"{x.day} {x:%B}" if x.year == y.year else f"{x.day} {x:%B %Y}"
    return f"{left} to {y.day} {y:%B %Y}"


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def verdict(both: dict) -> str:
    """Plain sentences: which forecast did better, by Brier score, on how many samples, and what
    happened at the samples over the threshold. The long-run rate's score is given beside them,
    since beating a forecast is not the same as beating no forecast."""
    n = both.get("n_samples", 0)
    if not n:
        return "No sample had both forecasts, so there is nothing to compare."
    e, s, k = both["ea"]["brier"], both["swimsignal"]["brier"], both["n_over_threshold"]
    on = f"on the {_n(n, 'sample')} both covered, at {_n(both['n_sites'], 'site')}, {_span(both.get('first'), both.get('last'))}"
    ea_name, ss_name = "the Environment Agency's daily risk prediction", "SwimSignal's E. coli estimate"
    if abs(e - s) < 0.0005:
        first = f"The two scored the same {on}: Brier score {e:.3f}"
    else:
        better, worse = (ea_name, ss_name) if e < s else (ss_name, ea_name)
        first = f"{better[0].upper()}{better[1:]} did better than {worse} {on}: Brier score {min(e, s):.3f} against {max(e, s):.3f}"
    first += f", where always forecasting the long-run rate scores {both['climatology_brier']:.3f}."
    if k == 0:
        second = f" No sample was over {THRESHOLD}, so this counts only warnings before clean samples."
    else:
        he, hs = both["ea"]["hits"], both["swimsignal"]["hits"]
        if he == 0 and hs == 0:
            second = (f" Neither warned before the one sample over {THRESHOLD}." if k == 1 else
                      f" Neither warned before either of the 2 samples over {THRESHOLD}." if k == 2 else
                      f" Neither warned before any of the {k} samples over {THRESHOLD}.")
        elif k == 1:
            second = (f" The Agency's prediction {'warned' if he else 'did not warn'} before the one sample over {THRESHOLD}; "
                      f"SwimSignal's estimate {'did' if hs else 'did not'}.")
        else:
            second = (f" Of the {k} samples over {THRESHOLD}, the Agency's prediction was \"increased\" before {he} "
                      f"and SwimSignal's estimate was {WARN_AT:.0%} or more before {hs}.")
    few = " Too few samples to judge either forecast." if n < 100 or k < 10 else ""
    return first + second + few


def _rows(df: pd.DataFrame) -> list[dict]:
    def t(x):
        return None if pd.isna(x) else pd.Timestamp(x).isoformat(timespec="minutes")
    return [{"site": r.name, "bw_id": r.bw_id, "kind": r.kind, "sampled": t(r.sample_time), "ecoli": int(r.ecoli),
             "ea_level": r.ea_level, "ea_origin": r.ea_origin, "ea_comment": r.ea_comment,
             "swimsignal": None if pd.isna(r.ss_p_ecoli) else round(float(r.ss_p_ecoli), 3),
             "swimsignal_lead": None if pd.isna(r.ss_lead) else int(r.ss_lead), "swimsignal_issued": t(r.ss_issued_at)}
            for r in df.sort_values("sample_time").itertuples(index=False)]


def compare(pred: pd.DataFrame, samples: pd.DataFrame, points: pd.DataFrame, sites: pd.DataFrame,
            base_rate: dict[str, float]) -> dict:
    """The comparison, from parsed predictions, this season's samples, the forecast log and the sites."""
    smp = samples[samples["bw_id"].isin(sites["bw_id"])].dropna(subset=["sample_time", "ecoli"])
    lo, hi = pd.Timestamp(SEASON[0], tz=LOCAL_TZ), pd.Timestamp(SEASON[1] + timedelta(days=1), tz=LOCAL_TZ)
    smp = smp[(smp["sample_time"] >= lo) & (smp["sample_time"] < hi)]
    if "kind" not in smp:
        smp = smp.merge(sites[["bw_id", "kind"]], on="bw_id")
    if "name" not in smp:
        smp = smp.merge(sites[["bw_id", "name"]], on="bw_id")
    df = swimsignal_at_samples(ea_at_samples(smp, pred), points, sites)
    has_ea = df["ea_level"].notna()
    both = df[has_ea & df["ss_p_ecoli"].notna()]
    ea_only = df[has_ea]
    y = ea_only["ecoli"] > THRESHOLD
    inc = ea_only["ea_level"] == "increased"
    reasons = ea_only.loc[inc, "ea_comment"].fillna("(no comment)").value_counts()
    where = ea_only.loc[inc, "name"].value_counts()
    return {
        "sites": prf_sites(pred, sites),
        "season_samples": {"n_samples": len(df), "n_with_ea_prediction": int(has_ea.sum()),
                           "n_over_threshold": int((df["ecoli"] > THRESHOLD).sum()),
                           "first": str(df["sample_time"].min().date()) if len(df) else None,
                           "last": str(df["sample_time"].max().date()) if len(df) else None,
                           "ea": {"n_increased": int(inc.sum()), **contingency(inc.to_numpy(), y.to_numpy()),
                                  "increased_because": {str(k): int(v) for k, v in reasons.items()},
                                  "increased_at": {str(k): int(v) for k, v in where.items()}}},
        "both": {**score(both, base_rate), "first": str(both["sample_time"].min().date()) if len(both) else None,
                 "last": str(both["sample_time"].max().date()) if len(both) else None},
        "both_rivers": score(both[both["kind"] == "river"], base_rate),
        "rows": _rows(both),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh-samples", action="store_true", help="fetch this season's EA samples into the state first")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    sites = pd.DataFrame(json.loads(SITES.read_text())).rename(columns={"id": "bw_id"})
    days = season_days(SEASON[0], min(SEASON[1], _today() - timedelta(days=1)))
    got = fetch_season(days)
    pred = parse([x for d in sorted(got["items"]) for x in got["items"][d]])
    samples = refresh_ecoli_samples(force=True) if args.refresh_samples else pd.read_parquet(config.state_read(ECOLI_SAMPLES))
    points = load_points(config.state_read("dipcast.duckdb"))
    from dipcast.model.ecoli import load as load_ecoli
    em = load_ecoli()
    base = (em.meta.get("base_rate_by_type") if em else None) or {}
    res = compare(pred, samples, points, sites, base)
    fetched = sorted(got["items"])
    res = {"generated_at": pd.Timestamp.now(tz=LOCAL_TZ).isoformat(timespec="seconds"),
           "season": [SEASON[0].isoformat(), SEASON[1].isoformat()],
           "fetch": {"days_requested": len(days), "days_fetched": len(fetched), "first_day": fetched[0] if fetched else None,
                     "last_day": fetched[-1] if fetched else None, "failed": got["failed"], "refused": got["refused"],
                     "requests_this_run": got["n_requests"], "refusals_this_run": got["n_refusals"], "user_agent": config.EA_HEADERS["User-Agent"], "source": URL},
           "rules": {"threshold": THRESHOLD, "swimsignal_warns_at": WARN_AT,
                     "ea": "the latest prediction for the site published before the sample was taken and not yet expired",
                     "swimsignal": "the latest forecast for the spot and day issued before the sample was taken",
                     "brier_ea": "the EA's level read as 1 (increased) or 0 (normal)",
                     "climatology": "the E. coli model's training-period exceedance rate for rivers and lakes"},
           **res, "credit": CREDIT}
    res["verdict"] = verdict(res["both"])
    args.out.write_text(json.dumps(res, indent=1, default=str) + "\n")
    s = res["sites"]
    log.info("%d of %d inland sites had a PRF on any day; %d appear in the predictions", s["n_with_prf"], s["n_inland"],
             s["n_in_predictions"])
    log.info("%s", res["verdict"])
    log.info("wrote %s", args.out)
    return 1 if got["refused"] and not fetched else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(main())
