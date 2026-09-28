"""The Environment Agency sampler's visual algae check at the inland bathing waters:
an observation shown beside the forecast, not a forecast.

At each sampling visit the sampler records one of four levels ("Bathing Water Profile :
Algal Bloom", determinand 4824 in the Water Quality Archive). On 28 Sep 2026 the archive
held 2,925 of them at the 38 bathing waters since 2020; from 2023, algae was seen in 310 of
1,304 lake checks (63 objectionable) and 64 of 1,012 river checks (11). It is a look at the
water: it does not tell blue-green algae (cyanobacteria, which can be toxic) from harmless
kinds, and there are no cell counts or toxin tests at these sites. Too few objectionable
checks to fit a model on, so dipcast reports the latest one and this season's tally.
"""

from __future__ import annotations

import logging

import pandas as pd

from dipcast import config
from dipcast.forecast_log import LOCAL_TZ, _bathing_sites, _local_times

log = logging.getLogger(__name__)

ALGAE_CHECKS = "algae_checks.parquet"
REFRESH_H = 24
# The EA's wording, as published, to a level and a phrase for the page.
LEVELS = {
    "No material present": (0, "none seen"),
    "Trace present - 1 or 2 items": (1, "a trace (1 or 2 items)"),
    "Some at intervals - 3 to 6 items": (2, "some at intervals (3 to 6 items)"),
    "Sufficient to be objectionable >6 items": (3, "enough to be objectionable (more than 6 items)"),
}
OBJECTIONABLE = 3


def season_start(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """1 May of the latest season to have started: the EA samples bathing waters from May
    to September, so in February this is last May and the page shows last season's final
    check, dated, rather than nothing."""
    now = now or pd.Timestamp.now(tz=LOCAL_TZ)
    y = now.year if now.month >= 5 else now.year - 1
    return pd.Timestamp(f"{y}-05-01", tz=LOCAL_TZ)


def refresh_algae(force: bool = False, fetch: bool = True, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """This season's checks at the mapped bathing waters, refetched at most once a day and
    kept in the state directory. A failed fetch keeps the checks already held (the page
    dates every check, so an old one reads as old); nothing is raised, because this is an
    observation beside the forecast and must not stop a build. fetch=False only reads."""
    p = config.state_read(ALGAE_CHECKS)
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    if not fetch:
        return old
    if p.exists() and not force:
        age_h = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(p.stat().st_mtime, unit="s", tz="UTC")).total_seconds() / 3600
        if age_h < REFRESH_H:
            return old
    from dipcast.ingest.wqa import fetch_algae
    mapped = _bathing_sites().dropna(subset=["wqa_point"]).set_index("wqa_point")[["bw_id", "name", "kind"]]
    start = season_start(now)
    try:
        rows = fetch_algae(list(mapped.index), start.isoformat())
    except Exception as e:  # noqa: BLE001 - an observation beside the forecast must not sink the build
        log.warning("EA algae checks: %s; keeping the %d held", e, len(old))
        return old
    df = pd.DataFrame(rows, columns=["wqa_point", "sample_time", "result", "purpose"])
    df = df[df["wqa_point"].isin(mapped.index)].join(mapped, on="wqa_point")
    if df.empty:   # an empty answer is not written over checks already held; the next build asks again
        return old[old["sample_time"] >= start] if len(old) else df
    df = df.assign(sample_time=_local_times(df["sample_time"])).dropna(subset=["sample_time", "result"])
    df = df[df["sample_time"] >= start].drop_duplicates(["bw_id", "sample_time"]).reset_index(drop=True)
    df.to_parquet(config.state_write(ALGAE_CHECKS), index=False)
    log.info("EA algae checks refreshed: %d this season at %d sites", len(df), df["bw_id"].nunique())
    return df


def by_site(df: pd.DataFrame) -> dict[str, dict]:
    """bw_id -> the latest check and this season's tally, for spots.json."""
    out = {}
    if df.empty:
        return out
    for bw_id, g in df.sort_values("sample_time").groupby("bw_id"):
        levels = g["result"].map(lambda r: LEVELS.get(r, (float("nan"),))[0])   # unknown wording: not counted
        last = g.iloc[-1]
        level, phrase = LEVELS.get(last["result"], (None, str(last["result"]).lower()))
        out[bw_id] = {"date": last["sample_time"].date().isoformat(), "level": level, "phrase": phrase,
                      "result": last["result"], "n_checks": len(g), "n_seen": int((levels >= 1).sum()),
                      "n_objectionable": int((levels >= OBJECTIONABLE).sum()),
                      "season": int(g["sample_time"].iloc[0].year)}
    return out
