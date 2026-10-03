"""Write tests/fixtures/anypoint_spot.json: one listed spot's forecast from forecast_point, with
the inputs the page's arithmetic takes (anypoint.js, forecast), for tests/site_anypoint.test.cjs.

    uv run python tests/fixtures/make_anypoint_spot.py [spot id]

Needs the river network (data/processed/river_network.pkl) and a state directory (DIPCAST_STATE).
The rain forecast is read from the cache when it is under a day old, so the inputs below and
forecast_point's own run see the same rain; otherwise it is fetched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from dipcast.ingest import rainfall
from dipcast.model import forecast as fc
from dipcast.model.transport import history_days, live_now_risk, locate_pin, river_velocity, upstream_overflows

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("anypoint_spot.json")


def main(spot_id: str = "bw-uke4100-08901") -> None:
    rainfall.FORECAST_TTL_S = 24 * 3600   # the same cached rain for both runs below
    spots = pd.read_csv(ROOT / "spots.csv").fillna("")
    s = spots.set_index("id").loc[spot_id]
    river = s["river"] if s["kind"] != "lake" and s["river"] else None
    kind = s["kind"] if s["kind"] in ("lake", "river") else None
    want = fc.forecast_point(float(s["lat"]), float(s["lon"]), gauge=False, kind_hint=kind, river_hint=river, log_to_store=False)
    # The same steps as forecast_point, kept apart: the rows, the spill probabilities on its day
    # grid before calibration, the live weights.
    net, ov_all, model = fc._net(), fc._overflows(), fc._model()
    pin = locate_pin(net, float(s["lon"]), float(s["lat"]), kind_hint=kind, river_hint=river)
    ov = upstream_overflows(net, pin, ov_all, velocity_ms=river_velocity(None))
    now = pd.Timestamp(want["query"]["issued_at"])
    hist = history_days(ov["travel_h"].to_numpy(dtype=float))
    days = pd.date_range(now.floor("D") - pd.Timedelta(days=hist), periods=hist + 5, freq="D")
    p, ok = fc.spill_probabilities(ov, days, model)
    _, live = live_now_risk(ov, now)
    extra = np.where(ov["weight"].to_numpy() > 0, np.asarray(live) / ov["weight"].to_numpy(), 0.0)
    companies = sorted(ov["company"].fillna("unknown").astype(str).unique())
    body = {
        "note": ("Made by tests/fixtures/make_anypoint_spot.py: forecast_point's result for a listed spot (expect) and its "
                 "inputs in the shape of the any-point files (rows, od; p unscaled, before calibration)."),
        "spot": {"id": spot_id, "name": s["name"]}, "issued_at": want["query"]["issued_at"],
        "rows": [{"i": i, "dist": float(r.distance_m), "dlake": float(r.lake_distance_m), "dil": float(r.dilution)}
                 for i, r in enumerate(ov.itertuples())],
        "od": {"today": int(hist), "days": [d.date().isoformat() for d in days], "scale": 1,
               "p": [[None if not k else float(v) for v, k in zip(row, okrow, strict=True)] for row, okrow in zip(p, ok, strict=True)],
               "live": [float(x) for x in extra], "status": [int(x) for x in ov["status"]],
               "company": [companies.index(str(c)) for c in ov["company"].fillna("unknown")], "companies": companies,
               "low": [int(i) for i in np.flatnonzero((ov["snap_confidence"] == "low").to_numpy())],
               "feed_down_since": {}, "rain_h": None,
               "assumptions": {"river_velocity_ms": river_velocity(None), "lake_velocity_ms": 0.05, "t90_hours": 30.0,
                               "low_confidence_factor": 0.7, "max_missing_share": fc.MAX_MISSING_SHARE}},
        "cal": {str(k): v for k, v in fc._lead_calibration().items()},
        "expect": {"now": want["now"], "days": want["days"], "upstream_summary": want["upstream_summary"]},
    }
    OUT.write_text(json.dumps(body, indent=1) + "\n")
    print(f"{spot_id}: {len(ov)} overflows upstream, {sum(d['risk'] is None for d in want['days'])} days without a figure -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:])
