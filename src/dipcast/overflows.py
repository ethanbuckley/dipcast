"""The unified overflow table: live status + annual-return covariates + network snap."""

from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd
from shapely.geometry import Point

from dipcast import config
from dipcast.network.rivers import _TO_BNG, RiverNetwork

log = logging.getLogger(__name__)

NAME = "overflows.parquet"

# Outfalls to the sea or an estuary are outside the inland network by design.
MARINE = re.compile(r"\b(?:sea|channel|solent|estuary|estuarine|harbour|harbor|firth|bay|the wash|coast|coastal|"
                    r"marine|ocean|sound|beach|foreshore|mudflat|saltmarsh|creek|haven|offshore)\b", re.IGNORECASE)
WIDE_SNAP_M = 1_500.0
GENERIC = {"river", "brook", "beck", "stream", "dyke", "dike", "drain", "water", "burn", "canal", "trib",
           "tributary", "of", "the", "ditch", "cut", "sewer", "main", "new", "old", "north", "south", "east",
           "west", "little", "great", "upper", "lower", "unnamed", "culverted", "via", "and", "to", "from",
           "a", "an", "at", "in", "on", "onto", "land", "stw", "cso", "outfall", "ordinary", "watercourse",
           "surface", "freshwater", "controlled", "waters", "mill", "leat", "branch", "arm", "sluice"}


def _tokens(name) -> set[str]:
    if not isinstance(name, str):
        return set()
    name = re.sub(r"\(.*?\)", " ", name.lower())
    return {t for t in re.findall(r"[a-z]{3,}", name) if t not in GENERIC}


def _second_pass(net: RiverNetwork, df: pd.DataFrame) -> pd.DataFrame:
    """Try a wider radius for inland outfalls that missed the first pass, preferring
    a link whose name shares a distinctive word with the recorded receiving water."""
    todo = df[df["link_id"].isna()]
    marine = todo["receiving_watercourse"].fillna("").str.contains(MARINE)
    df.loc[todo.index[marine], "snap_confidence"] = "marine"
    xs, ys = _TO_BNG.transform(todo.loc[~marine, "lon"].to_numpy(), todo.loc[~marine, "lat"].to_numpy())
    hits = {"medium": 0, "low": 0}
    for (i, row), x, y in zip(todo[~marine].iterrows(), xs, ys, strict=True):
        cand = net.candidates_xy(x, y, WIDE_SNAP_M)
        if cand.empty:
            continue
        want = _tokens(row["receiving_watercourse"])
        pick, conf = None, "low"
        if want:
            named = cand[cand["watercourse_name"].map(lambda n, want=want: bool(_tokens(n) & want))]
            if not named.empty:
                pick, conf = named.iloc[0], "medium"
        if pick is None:
            pick = cand.iloc[0]
        pt = Point(x, y)
        df.loc[i, ["link_id", "frac", "snap_dist_m", "form", "snap_confidence"]] = [
            pick["id"], float(pick.geometry.project(pt, normalized=True)), float(pick["dist_m"]), pick["form"], conf]
        hits[conf] += 1
    log.info("second-pass snapping: %d name-matched (medium), %d nearest-only (low), %d marine excluded",
             hits["medium"], hits["low"], int(marine.sum()))
    return df


def latest_annual_returns(ar: pd.DataFrame) -> pd.DataFrame:
    a = ar.dropna(subset=["site_id"]).sort_values("year")
    a = a.groupby("site_id", as_index=False).last()
    return a[["site_id", "company", "site_name", "asset_type", "lat", "lon", "lta_spills",
              "spill_hours", "edm_operational_pct", "wfd_waterbody_name", "receiving_water",
              "bathing_water", "year"]].rename(columns={"lat": "ar_lat", "lon": "ar_lon",
                                                        "year": "ar_year", "company": "ar_company"})


def build_overflows(net: RiverNetwork) -> pd.DataFrame:
    live = pd.read_parquet(config.state_read("live_latest.parquet"))
    ar = latest_annual_returns(pd.read_parquet(config.PROCESSED / "annual_returns.parquet"))
    df = live.merge(ar, on="site_id", how="outer")
    df["company"] = df["company"].fillna(df["ar_company"])
    df["lat"] = df["lat"].fillna(df["ar_lat"])
    df["lon"] = df["lon"].fillna(df["ar_lon"])
    df["receiving_watercourse"] = df["receiving_watercourse"].fillna(df["receiving_water"])
    df["has_live"] = df["status"].notna()
    df["status"] = df["status"].fillna(-2).astype(int)   # -2 = no live feed
    df = df.dropna(subset=["lat", "lon"]).drop(columns=["ar_lat", "ar_lon", "ar_company", "receiving_water"])
    df = df[(df.lat.between(49, 61)) & (df.lon.between(-9, 3))].reset_index(drop=True)

    snap = net.snap_many(df["lon"].to_numpy(), df["lat"].to_numpy(), max_m=config.SNAP_MAX_M)
    df["link_id"] = snap["link_id"].to_numpy()
    df["frac"] = snap["frac"].to_numpy()
    df["snap_dist_m"] = snap["dist_m"].to_numpy()
    df["form"] = snap["form"].to_numpy()
    df["snap_confidence"] = np.where(df["link_id"].notna(), "high", None)
    df = _second_pass(net, df)
    snapped = df["link_id"].notna()
    df.loc[snapped, "start_node"] = net.links.loc[df.loc[snapped, "link_id"], "start_node"].to_numpy()
    df.loc[snapped, "end_node"] = net.links.loc[df.loc[snapped, "link_id"], "end_node"].to_numpy()
    df.loc[snapped, "link_length"] = net.links.loc[df.loc[snapped, "link_id"], "length"].to_numpy()
    log.info("overflows: %d total, %d with live feed, %d snapped to network (%.0f%%); confidence %s",
             len(df), int(df.has_live.sum()), int(snapped.sum()), 100 * snapped.mean(),
             df["snap_confidence"].value_counts(dropna=False).to_dict())
    df.to_parquet(config.state_write(NAME), index=False)
    return df


def load_overflows(net: RiverNetwork | None = None, rebuild: bool = False) -> pd.DataFrame:
    path = config.state_read(NAME)
    if path.exists() and not rebuild:
        return pd.read_parquet(path)
    assert net is not None
    return build_overflows(net)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    net = RiverNetwork.load()
    df = build_overflows(net)
    print(df.groupby("company").agg(n=("site_id", "size"), live=("has_live", "sum"),
                                    snapped=("link_id", lambda s: s.notna().sum()),
                                    lake=("form", lambda s: (s == "lake").sum())))
    print("snap distance quantiles (m):", df.snap_dist_m.quantile([.5, .9, .99]).round(0).to_dict())
    print(df.groupby("snap_confidence", dropna=False).size())
