"""Environment Agency Water Quality Archive: the bathing-water E. coli results of
ingest.bwq, from a different EA service.

Why two sources: since at least 28 Sep 2026 the bathing-water service's gateway answers
GitHub's runners with HTTP 403 whatever the User-Agent, and this service does not. On
that day, of the 702 samples the bathing-water service listed for the season at the 38
inland bathing waters, it held 675 as statutory monitoring (purpose MS) with identical
counts and qualifiers, plus 9 MS samples the other service did not list. It lags: its
latest sample trailed the bathing-water service's by 3-7 days at 15 sites and 15 days at
one. Points are mapped per site in data/raw/bathing_waters_inland.json (`wqa_point`, from
scripts/map_bathing_waters_wqa.py).
"""

from __future__ import annotations

import time

import httpx

from dipcast import config

API = "https://environment.data.gov.uk/water-quality"
ECOLI = "2348"                  # determinand: Escherichia coli : Confirmed : MF
PAGE = 250                      # the service's page limit for JSON-LD
MAX_POINTS = 50                 # notations per request: the documented limit is 100, but 100 got a 502 on 28 Sep 2026
# Statutory monitoring only: the samples the bathing-water service lists (675 of its 677
# found here were MS). Follow-ups after a failure (SI) are taken because a result was bad,
# so scoring them would tilt the set towards bad days; investigations (PI) are left out too.
BATHING_PURPOSES = "MS"


def _count(s: str | None) -> tuple[float | None, str]:
    """'620' -> (620.0, '='); '<10' -> (10.0, '<'): the qualifier convention of ingest.bwq."""
    s = (s or "").strip()
    q = s[0] if s[:1] in ("<", ">") else "="
    try:
        return float(s.lstrip("<>= ")), q
    except ValueError:
        return None, q


def fetch_ecoli(points: list[str], since: str, purposes: str | None = BATHING_PURPOSES) -> list[dict]:
    """E. coli results at the given archive sampling points since `since` (ISO date or
    datetime), one row per sample: wqa_point, sample_time (local, as published), ecoli,
    ecoli_qual, purpose. About one request per 250 results."""
    rows = []
    params = {"determinand": ECOLI, "dateFrom": since[:10], "limit": PAGE}
    if purposes:
        params["samplingPurpose"] = purposes
    with httpx.Client(timeout=120, headers={**config.EA_HEADERS, "Accept": "application/ld+json"}) as c:
        for i in range(0, len(points), MAX_POINTS):
            skip = 0
            while True:
                r = c.post(f"{API}/data/observation",
                           params={**params, "pointNotation": ",".join(points[i:i + MAX_POINTS]), "skip": skip})
                r.raise_for_status()
                items = r.json().get("member") or []
                for o in items:
                    ecoli, qual = _count(o.get("hasSimpleResult"))
                    sampling = ((o.get("hasSample") or {}).get("isResultOf") or {})
                    rows.append({"wqa_point": (o.get("hasSamplingPoint") or {}).get("notation"),
                                 "sample_time": o.get("phenomenonTime"), "ecoli": ecoli, "ecoli_qual": qual,
                                 "purpose": (sampling.get("samplingPurpose") or {}).get("notation")})
                if len(items) < PAGE:
                    break
                skip += PAGE
                time.sleep(0.3)
    return rows
