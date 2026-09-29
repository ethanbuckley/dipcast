"""Fetch the Environment Agency's annual classification of every inland bathing water in
data/raw/bathing_waters_inland.json (excellent, good, sufficient or poor, with the years
before it) and write data/raw/bathing_water_classifications.json, which the site build reads.

Run it by hand after each year's classifications are published (the 2026 statistics are
listed for December 2026), then commit the file:

    uv run python scripts/fetch_classifications.py

A file in the repository rather than a fetch in the build, because the EA's bathing-water
service refuses GitHub's runners (HTTP 403 since 28 Sep 2026) and a classification changes
once a year. The page links to each bathing water's EA page for advice that changes within a
season (pollution incidents, algae), which this file does not hold.

The classification is the statutory one (Bathing Water Regulations 2013, schedule 5): from the
E. coli and intestinal enterococci samples of that season and up to three before it. A poor
classification means the EA advises against bathing for the following season (regulation 13).
Data: Environment Agency, Open Government Licence v3.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

from dipcast import config

ROOT = Path(__file__).resolve().parents[1]
SITES = config.RAW / "bathing_waters_inland.json"
OUT = config.RAW / "bathing_water_classifications.json"
HISTORY = "https://environment.data.gov.uk/doc/bathing-water-quality/compliance-rBWD/bathing-water/{id}.json"
PROFILE = "https://environment.data.gov.uk/bwq/profiles/profile.html?site={id}"
CLASSES = {"excellent", "good", "sufficient", "poor"}


def _year(item: dict) -> int | None:
    y = (item.get("sampleYear") or {}).get("ordinalYear")
    if y is None:   # the assessment's own address ends /year/2025
        m = re.search(r"/year/(\d{4})$", item.get("_about", ""))
        y = m and m.group(1)
    return int(y) if y else None


def classifications(eubwid: str, client: httpx.Client) -> list[tuple[int, str]]:
    """[(year, class)], newest first: every rBWD assessment the EA holds for the bathing water.
    Empty for a water designated too recently to have one."""
    r = client.get(HISTORY.format(id=eubwid), params={"_sort": "-sampleYear.ordinalYear", "_pageSize": 50})
    r.raise_for_status()
    out = []
    for item in r.json()["result"]["items"]:
        name = ((item.get("complianceClassification") or {}).get("name") or {}).get("_value", "")
        year = _year(item)
        if year and name.strip().lower() in CLASSES:
            out.append((year, name.strip().lower()))
    return sorted(set(out), reverse=True)


def main() -> None:
    sites = json.loads(SITES.read_text())
    out, missing = {}, []
    with httpx.Client(timeout=60, headers=config.EA_HEADERS) as c:
        for s in sites:
            try:
                hist = classifications(s["id"], c)
            except Exception as e:  # noqa: BLE001 - report every failure, then stop without writing
                missing.append(f"{s['name']}: {e}")
                continue
            entry = {"name": s["name"], "url": PROFILE.format(id=s["id"])}
            if hist:
                entry.update({"class": hist[0][1], "year": hist[0][0], "history": [[y, k] for y, k in hist]})
            out[s["id"]] = entry
            time.sleep(0.3)   # a polite pace for a public service
    if missing:
        sys.exit("not written: no answer for " + "; ".join(missing))
    OUT.write_text(json.dumps({
        "fetched_at": pd.Timestamp.now(tz="Europe/London").isoformat(timespec="seconds"),
        "source": "Environment Agency bathing water quality, compliance-rBWD (environment.data.gov.uk/bwq)",
        "licence": "Open Government Licence v3.0",
        "sites": out}, indent=1) + "\n")
    rated = [v for v in out.values() if "class" in v]
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rated)} of {len(out)} classified, "
          + ", ".join(f"{sum(v['class'] == k for v in rated)} {k}" for k in ("excellent", "good", "sufficient", "poor")))


if __name__ == "__main__":
    main()
