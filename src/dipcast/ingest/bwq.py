"""Environment Agency bathing-water lab samples (E. coli, intestinal enterococci).

List endpoint: /doc/bathing-water-quality/in-season/sample.json filtered by
bwq_samplingPoint.notation. The point notation is the numeric suffix of the
bathing water's EU id (ukd1203-45650 -> 45650). Weekly samples May-September.
"""

from __future__ import annotations

import json
import logging
import time

import httpx
import pandas as pd

from dipcast import config

log = logging.getLogger(__name__)

LIST = "https://environment.data.gov.uk/doc/bathing-water-quality/in-season/sample.json"
SITES = config.RAW / "bathing_waters_inland.json"
OUT = config.RAW / "bwq_samples.parquet"


def _val(x):
    if isinstance(x, dict):
        return x.get("_value", x.get("inXSDDateTime", {}).get("_value") if isinstance(x.get("inXSDDateTime"), dict) else None)
    return x


def fetch_point(point: str, since: str = "2023-01-01T00:00:00") -> list[dict]:
    rows, page = [], 0
    with httpx.Client(timeout=120) as c:
        while True:
            params = {"bwq_samplingPoint.notation": point, "_pageSize": 500, "_page": page,
                      "_sort": "-sampleDateTime.inXSDDateTime",
                      "min-sampleDateTime.inXSDDateTime": since}
            r = c.get(LIST, params=params)
            if r.status_code == 400:  # min- filter unsupported: fall back to plain paging
                params.pop("min-sampleDateTime.inXSDDateTime")
                r = c.get(LIST, params=params)
            r.raise_for_status()
            res = r.json()["result"]
            items = res.get("items", [])
            for x in items:
                t = _val(x.get("sampleDateTime"))
                if t is None:
                    continue
                if t < since:
                    items = []  # sorted newest first: everything after this is older
                    break
                q = x.get("escherichiaColiQualifier") or {}
                qi = x.get("intestinalEnterococciQualifier") or {}
                rows.append({
                    "point": point, "sample_time": t,
                    "ecoli": x.get("escherichiaColiCount"), "ecoli_qual": q.get("countQualifierNotation"),
                    "ie": x.get("intestinalEnterococciCount"), "ie_qual": qi.get("countQualifierNotation"),
                    "abnormal_weather": _val(x.get("abnormalWeatherException")),
                    "discountable": _val(x.get("discountable")),
                    "classification": (x.get("sampleClassification") or {}).get("name", {}).get("_value")
                    if isinstance((x.get("sampleClassification") or {}).get("name"), dict) else None,
                })
            if not items or not res.get("next"):
                break
            page += 1
            time.sleep(0.3)
    return rows


def fetch_all(since: str = "2023-01-01T00:00:00") -> pd.DataFrame:
    sites = json.loads(SITES.read_text())
    frames = []
    for s in sites:
        point = s["id"].split("-")[-1]
        try:
            rows = fetch_point(point, since)
        except httpx.HTTPError as e:
            log.warning("%s (%s): %s", s["name"], point, e)
            continue
        df = pd.DataFrame(rows)
        if df.empty:
            log.info("%s: no samples since %s", s["name"], since[:10])
            continue
        df["bw_id"], df["name"], df["kind"] = s["id"], s["name"], s["kind"]
        df["lat"], df["lon"], df["undertaker"] = s["lat"], s["lon"], s["undertaker"]
        frames.append(df)
        log.info("%s: %d samples", s["name"], len(df))
    out = pd.concat(frames, ignore_index=True)
    out["sample_time"] = pd.to_datetime(out["sample_time"]).dt.tz_localize("Europe/London", ambiguous="NaT", nonexistent="shift_forward")
    for c in ["ecoli", "ie"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out.to_parquet(OUT, index=False)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    df = fetch_all()
    print(df.groupby(["kind"]).agg(sites=("bw_id", "nunique"), samples=("ecoli", "size"),
                                   first=("sample_time", "min"), last=("sample_time", "max")))
    print("E. coli quantiles:", df.ecoli.quantile([.1, .5, .9, .99]).round(0).to_dict())
    print("qualifiers:", df.ecoli_qual.value_counts(dropna=False).to_dict())
    print("by year:", df.sample_time.dt.year.value_counts().sort_index().to_dict())
