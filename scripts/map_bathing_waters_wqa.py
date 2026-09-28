"""Find each inland bathing water's sampling point in the EA Water Quality Archive and
record it as `wqa_point` in data/raw/bathing_waters_inland.json.

A point is accepted only if its E. coli results agree with the bathing-water service's
samples for the season: same minute, same count, for at least half of them. Distance
alone is not enough, since several bathing waters have investigation points (streams,
drains) within a few metres. Needs the bathing-water service, which refuses GitHub's
runners (see ingest/wqa.py), so run it from a machine that can reach it:

    uv run python scripts/map_bathing_waters_wqa.py [season year]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import httpx
import pandas as pd

from dipcast import config
from dipcast.ingest import bwq, wqa

RADIUS_KM = 1.5
MIN_AGREE = 0.5


def main(year: int) -> None:
    sites = json.loads(bwq.SITES.read_text())
    since = f"{year}-05-01T00:00:00"
    candidates: dict[str, list[str]] = {}
    with httpx.Client(timeout=120, headers={**config.EA_HEADERS, "Accept": "application/ld+json"}) as c:
        for s in sites:
            r = c.get(f"{wqa.API}/sampling-point", params={"latitude": s["lat"], "longitude": s["lon"],
                                                           "radius": RADIUS_KM, "limit": 100})
            r.raise_for_status()
            candidates[s["id"]] = [p["notation"] for p in r.json().get("member", [])]
    points = sorted({p for ps in candidates.values() for p in ps})
    archive: dict[str, dict[str, float]] = defaultdict(dict)
    for row in wqa.fetch_ecoli(points, since, purposes=None):
        archive[row["wqa_point"]][row["sample_time"][:16]] = row["ecoli"]
    out = []
    for s in sites:
        mine = {r["sample_time"][:16]: float(r["ecoli"]) for r in bwq.fetch_point(s["id"].split("-")[-1], since)
                if r.get("ecoli") is not None}
        agree = {p: sum(1 for t, v in mine.items() if archive[p].get(t) == v) for p in candidates[s["id"]]}
        best = max(agree, key=agree.get, default=None)
        ok = best is not None and mine and agree[best] >= MIN_AGREE * len(mine)
        s["wqa_point"] = best if ok else None
        out.append({"site": s["name"], "samples": len(mine), "point": best, "agree": agree.get(best, 0),
                    "accepted": bool(ok), "candidates": len(candidates[s["id"]])})
    print(pd.DataFrame(out).to_string(index=False))
    bwq.SITES.write_text(json.dumps(sites, indent=1, ensure_ascii=False))
    print(f"{sum(r['accepted'] for r in out)} of {len(out)} sites mapped -> {bwq.SITES}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp.now(tz="Europe/London").year)
