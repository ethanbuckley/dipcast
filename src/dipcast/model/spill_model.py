"""Rainfall -> spill probability, in two layers.

Layer 1 (pooled): a gradient-boosted classifier on rainfall, season and the
site's annual-return covariates, with monotone constraints so more rain can
never lower the predicted probability. It works for any overflow that has an
annual return, including companies with no live or historical feed.

Layer 2 (site calibration): for overflows with event history, a shrunken
observed/expected ratio corrects the pooled model's bias for that site. With
`n` observed spill-days, `e` expected under layer 1, and prior pseudo-counts
`a` (spills) and `b` (expected), the multiplier is (n + a) / (e + b) applied on
the odds scale. This is the posterior mean of a Gamma-Poisson model, so sites
with little data stay near the pooled prediction and sites with lots of data
get their own level.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from dipcast import config
from dipcast.model.features import ALL_FEATURES, MONOTONE

log = logging.getLogger(__name__)

MODEL_PATH = config.PROCESSED / "spill_model.pkl"
PRIOR_STRENGTH = 15.0   # pseudo spill-days in the site calibration prior
EPS = 1e-4


@dataclass
class SpillModel:
    features: list[str] = field(default_factory=lambda: list(ALL_FEATURES))
    clf: HistGradientBoostingClassifier | None = None
    site_offset: dict[str, float] = field(default_factory=dict)   # log odds multiplier
    site_n: dict[str, int] = field(default_factory=dict)
    trained_on: str = ""

    # ------------------------------------------------------------ layer 1
    def fit_pooled(self, df: pd.DataFrame, neg_frac: float = 0.25, seed: int = 0) -> SpillModel:
        """Fit layer 1. Dry (y=0) days are subsampled at `neg_frac` and up-weighted
        by 1/neg_frac, which leaves the fitted probabilities unbiased and cuts the
        fit time roughly fourfold."""
        rng = np.random.default_rng(seed)
        y_all = df["y"].to_numpy()
        keep = (y_all == 1) | (rng.random(len(y_all)) < neg_frac)
        X = df.loc[keep, self.features].to_numpy(dtype=np.float32)
        y = y_all[keep]
        w = np.where(y == 1, 1.0, 1.0 / neg_frac)
        mono = [MONOTONE.get(f, 0) for f in self.features]
        self.clf = HistGradientBoostingClassifier(
            loss="log_loss", max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
            min_samples_leaf=200, l2_regularization=1.0, monotonic_cst=mono,
            early_stopping=True, validation_fraction=0.1, n_iter_no_change=15,
            random_state=seed, verbose=1,
        )
        self.clf.fit(X, y, sample_weight=w)
        log.info("pooled model: %d iters, %d of %d rows used, base rate %.3f",
                 self.clf.n_iter_, len(y), len(y_all), y_all.mean())
        return self

    def predict_pooled(self, df: pd.DataFrame) -> np.ndarray:
        assert self.clf is not None, "model not fitted"
        X = df[self.features].to_numpy(dtype=np.float32)
        return np.clip(self.clf.predict_proba(X)[:, 1], EPS, 1 - EPS)

    # ------------------------------------------------------------ layer 2
    def fit_site_offsets(self, df: pd.DataFrame, prior_strength: float = PRIOR_STRENGTH,
                         recency: float = 1.0) -> SpillModel:
        """`recency` < 1 down-weights older years geometrically (a year k years older
        than the newest counts recency**k), so a site's level tracks recent behaviour:
        overflows get fixed, sewers get connected, climates shift."""
        p = self.predict_pooled(df)
        tmp = pd.DataFrame({"site_id": df["site_id"].to_numpy(), "y": df["y"].to_numpy(), "p": p})
        if recency < 1.0 and "year" in df:
            age = df["year"].max() - df["year"].to_numpy()
            w = np.power(recency, age)
            tmp["y"] = tmp["y"] * w
            tmp["p"] = tmp["p"] * w
        agg = tmp.groupby("site_id").agg(n=("y", "sum"), e=("p", "sum"), days=("y", "size"))
        # Gamma-Poisson posterior mean of the observed/expected ratio, prior mean 1.
        ratio = (agg["n"] + prior_strength) / (agg["e"] + prior_strength)
        self.site_offset = np.log(ratio).to_dict()
        self.site_n = agg["days"].astype(int).to_dict()
        log.info("site offsets: %d sites, ratio median %.2f, IQR %.2f-%.2f",
                 len(ratio), ratio.median(), ratio.quantile(0.25), ratio.quantile(0.75))
        return self

    # ------------------------------------------------------------ predict
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        p = self.predict_pooled(df)
        off = df["site_id"].map(self.site_offset).fillna(0.0).to_numpy()
        logit = np.log(p / (1 - p)) + off
        return 1.0 / (1.0 + np.exp(-logit))

    # ------------------------------------------------------------ io
    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f, protocol=5)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> SpillModel:
        with open(path, "rb") as f:
            return pickle.load(f)
