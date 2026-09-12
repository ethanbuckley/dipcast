"""Rainfall archive for the E. coli validation: cells of the 38 inland bathing
waters and of every overflow upstream of them, for 2023-2026."""
import json
import logging

import pandas as pd

from dipcast import config
from dipcast.ingest.rainfall import fetch_archive, grid_cell
from dipcast.model.transport import locate_pin, upstream_overflows
from dipcast.network.rivers import RiverNetwork
from dipcast.overflows import load_overflows

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

net = RiverNetwork.load(); ov = load_overflows(net)
sites = json.loads((config.RAW / "bathing_waters_inland.json").read_text())
cells = set()
rows = []
for s in sites:
    pin = locate_pin(net, s["lon"], s["lat"])
    up = upstream_overflows(net, pin, ov, velocity_ms=config.RIVER_VELOCITY_MS)
    cl, cn = grid_cell(s["lat"], s["lon"]); cells.add((float(cl), float(cn)))
    for a, b in zip(*grid_cell(up["lat"].to_numpy(), up["lon"].to_numpy())) if len(up) else []:
        cells.add((float(a), float(b)))
    rows.append({"name": s["name"], "mode": pin.mode, "watercourse": pin.watercourse, "upstream": len(up),
                 "sum_weight": round(float(up.weight.sum()), 3) if len(up) else 0.0,
                 "companies": sorted(up.company.unique()) if len(up) else []})
pd.DataFrame(rows).to_csv(config.PROCESSED / "validation_sites.csv", index=False)
print(pd.DataFrame(rows).to_string())
cells = sorted(cells)
print(f"\n{len(cells)} rain cells needed")
for y in [2023, 2024, 2025, 2026]:
    df = fetch_archive(cells, y)
    print(y, len(df), "hourly rows")
print("VALIDATION RAIN DONE")
