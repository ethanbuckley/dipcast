"""Forecast verification: Brier score, log loss, reliability, skill vs baselines."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score


def scores(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p)),
        "auc": float(roc_auc_score(y, p)) if 0 < y.mean() < 1 else float("nan"),
        "base_rate": float(y.mean()),
        "n": len(y),
    }


def brier_skill(brier: float, brier_ref: float) -> float:
    return 1.0 - brier / brier_ref if brier_ref > 0 else float("nan")


def reliability_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    df = pd.DataFrame({"y": y, "p": p, "bin": idx})
    t = df.groupby("bin").agg(n=("y", "size"), forecast=("p", "mean"), observed=("y", "mean"))
    t["lo"], t["hi"] = edges[t.index], edges[t.index + 1]
    return t.reset_index(drop=True)


def rain_rule(df: pd.DataFrame, threshold_mm: float = 10.0) -> np.ndarray:
    """The naive competitor: 'it rained more than X mm in the last 24-48 h'.
    Turned into a probability by using the empirical spill rate within each side
    of the rule on the same data (an upper bound on how good the rule can be)."""
    wet = (df["rain_d"].fillna(0) + df["rain_d1"].fillna(0)) > threshold_mm
    p_wet = df.loc[wet, "y"].mean() if wet.any() else 0.0
    p_dry = df.loc[~wet, "y"].mean() if (~wet).any() else 0.0
    return np.where(wet, p_wet, p_dry)


def climatology(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    """Per-site spill-day frequency in the training period, global rate fallback."""
    rate = train.groupby("site_id")["y"].mean()
    return test["site_id"].map(rate).fillna(train["y"].mean()).to_numpy()
