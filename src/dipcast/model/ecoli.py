"""E. coli exceedance model: P(E. coli > 900 cfu/100 ml) at a swim spot on a day.

Fitted on Environment Agency lab samples at the inland designated bathing waters
(scripts/train_ecoli.py) from things dipcast can compute anywhere: rainfall at the
spot in the 48 and 24 h before a midday sample, dipcast's overflow exposure for the
day, whether the spot is a lake, and season. A logistic regression on standardised
features, stored as plain JSON so the live site needs no pickle.

900 cfu/100 ml is the inland "sufficient" threshold in the Bathing Water Regulations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from dipcast import config

MODEL_PATH = config.PROCESSED / "ecoli_model.json"
THRESHOLD = 900
SAMPLE_HOUR = 12          # EA samples are taken 10:00-14:00; the model's rain window ends here
FEATURES = ["lrain48", "lrain24", "logit_risk", "lake", "lake_x_lrain48", "doy_sin", "doy_cos"]


def features(rain_48h, rain_24h, risk, lake, when) -> pd.DataFrame:
    """Array-like inputs of equal length; `when` is datetime-like (for day of year)."""
    r48 = np.log1p(np.clip(np.asarray(rain_48h, dtype=float), 0, None))
    r24 = np.log1p(np.clip(np.asarray(rain_24h, dtype=float), 0, None))
    p = np.clip(np.asarray(risk, dtype=float), 1e-3, 1 - 1e-3)
    lk = np.asarray(lake, dtype=float)
    doy = pd.DatetimeIndex(pd.to_datetime(when)).dayofyear.to_numpy()
    return pd.DataFrame({
        "lrain48": r48, "lrain24": r24, "logit_risk": np.log(p / (1 - p)), "lake": lk,
        "lake_x_lrain48": lk * r48,
        "doy_sin": np.sin(2 * np.pi * doy / 365.25), "doy_cos": np.cos(2 * np.pi * doy / 365.25),
    })


@dataclass
class EcoliModel:
    coef: dict[str, float]
    intercept: float
    mean: dict[str, float]
    scale: dict[str, float]
    meta: dict

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        z = np.full(len(X), self.intercept)
        for f, b in self.coef.items():
            z += b * (X[f].to_numpy(dtype=float) - self.mean[f]) / self.scale[f]
        return 1 / (1 + np.exp(-z))

    def save(self, path=MODEL_PATH) -> None:
        path.write_text(json.dumps({"coef": self.coef, "intercept": self.intercept, "mean": self.mean,
                                    "scale": self.scale, "meta": self.meta}, indent=1))

    @classmethod
    def from_json(cls, path=MODEL_PATH) -> EcoliModel:
        d = json.loads(path.read_text())
        return cls(d["coef"], d["intercept"], d["mean"], d["scale"], d.get("meta", {}))


def fit(X: pd.DataFrame, y: np.ndarray, cols: list[str], C: float = 1.0, meta: dict | None = None) -> EcoliModel:
    from sklearn.linear_model import LogisticRegression
    mean = {c: float(X[c].mean()) for c in cols}
    scale = {c: float(X[c].std(ddof=0)) or 1.0 for c in cols}
    Z = np.column_stack([(X[c].to_numpy(dtype=float) - mean[c]) / scale[c] for c in cols])
    lr = LogisticRegression(C=C, max_iter=1000).fit(Z, y)
    return EcoliModel({c: float(b) for c, b in zip(cols, lr.coef_[0], strict=True)}, float(lr.intercept_[0]),
                      mean, scale, meta or {})


@lru_cache(maxsize=1)
def load() -> EcoliModel | None:
    return EcoliModel.from_json() if MODEL_PATH.exists() else None


MIN_COVERAGE = 0.9   # share of a window's hourly values that must be finite


def rain_windows(hourly: pd.Series, ends: pd.DatetimeIndex, min_coverage: float = MIN_COVERAGE,
                 return_coverage: bool = False):
    """48 h and 24 h rainfall totals ending at each timestamp in `ends`, from an
    hourly series indexed by time. A value stamped H is the rain in the hour ending
    at H, so the window (t - 48 h, t] is exactly the 48 stamps from floor(t) - 47 h
    to floor(t) (24 for the 24 h window).

    The total is NaN unless at least `min_coverage` of the stamps carry a finite
    value: missing hours, a null-filled forecast, duplicate stamps or a series that
    does not span the window all count as missing, so a gap in the data can never
    read as dry weather. An accepted window with a few missing hours sums only the
    hours it has, which understates rain if the missing hours were wet; with
    `return_coverage=True` the finite fraction of each window comes back too, so a
    caller can require complete windows or report the sensitivity. (Before 16 Sep
    2026 this counted stamps rather than finite values and pandas summed an all-NaN
    window to 0.0; until 17 Sep it summed 49 stamps for 48 h.)"""
    s = hourly.astype(float)
    s = s[np.isfinite(s.to_numpy())]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    out = {48: [], 24: []}
    cov = {48: [], 24: []}
    for t in ends:
        t = pd.Timestamp(t).floor("h")
        for hours in (48, 24):
            idx = pd.date_range(t - pd.Timedelta(hours=hours - 1), t, freq="h")
            w = s.reindex(idx)
            frac = float(w.notna().sum()) / len(idx)
            cov[hours].append(frac)
            out[hours].append(float(w.sum()) if frac >= min_coverage else np.nan)
    r48, r24 = np.array(out[48]), np.array(out[24])
    if return_coverage:
        return r48, r24, np.array(cov[48]), np.array(cov[24])
    return r48, r24
