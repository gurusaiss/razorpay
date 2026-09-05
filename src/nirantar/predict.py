"""
Phase 4: calibrated pre-debit failure predictor.

Predicts P(first attempt of a billing cycle fails) using ONLY features
available before that attempt fires (see features.py for the exact
boundary and why each excluded field is excluded).

Temporal holdout, not a random split: examples from cycle_index <= 8
(months 1-9) train the model; examples from cycle_index >= 9 (months
10-12) are held out for evaluation. This mirrors real deployment -- the
model only ever sees a mandate's past when scoring its future -- and is
why generate.py's docstring says "temporal holdout enforced at training
time, not at generation time": the same single dataset serves both roles,
split by time, rather than needing a separate held-out dataset.

The model outputs a CALIBRATED probability, not just a ranking score,
because config.THRESHOLD_RETIME/THRESHOLD_SPLIT are absolute probability
thresholds the policy engine compares against -- an uncalibrated score
would make those thresholds meaningless. Calibration quality is reported
via Brier score and a reliability table, not just AUC, because a model can
rank well (high AUC) while being badly calibrated (e.g. systematically
overconfident), and a badly-calibrated model silently breaks every gate in
docs/TAXONOMY.md section 5.

Usage:
    python -m nirantar.predict --data data/seed7_v1 --model-out models/predict_v1.joblib
"""

from __future__ import annotations

import argparse
import os

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.calibration import CalibratedClassifierCV

from nirantar.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, build_examples


def to_xy(examples: list[dict]):
    X = [
        {**{k: e[k] for k in CATEGORICAL_FEATURES}, **{k: e[k] for k in NUMERIC_FEATURES}}
        for e in examples
    ]
    y = np.array([e["label"] for e in examples])
    return X, y


def build_pipeline() -> Pipeline:
    preprocess = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", StandardScaler(), NUMERIC_FEATURES),
    ])
    base = LogisticRegression(max_iter=1000, class_weight="balanced")
    # Calibrate on top of the base classifier via internal 3-fold CV on
    # whatever data .fit() receives -- so calibration itself never touches
    # the temporal test split, only the temporal train split.
    calibrated = CalibratedClassifierCV(base, method="isotonic", cv=3)
    return Pipeline([("preprocess", preprocess), ("model", calibrated)])


def dicts_to_frame(X: list[dict]):
    import pandas as pd
    return pd.DataFrame(X)


def reliability_table(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> list[dict]:
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (y_prob >= lo) & (y_prob < hi if i < n_bins - 1 else y_prob <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        rows.append({
            "bucket": f"[{lo:.1f}, {hi:.1f}{']' if i == n_bins - 1 else ')'}",
            "n": n,
            "mean_predicted": round(float(y_prob[mask].mean()), 4),
            "actual_fail_rate": round(float(y_true[mask].mean()), 4),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="Train/evaluate the calibrated pre-debit failure predictor.")
    ap.add_argument("--data", type=str, required=True)
    ap.add_argument("--model-out", type=str, default=None)
    ap.add_argument("--split-cycle", type=int, default=9,
                     help="cycle_index >= this goes to the temporal test split")
    args = ap.parse_args()

    examples = build_examples(args.data)
    train_ex = [e for e in examples if e["cycle_index"] < args.split_cycle]
    test_ex = [e for e in examples if e["cycle_index"] >= args.split_cycle]

    X_train, y_train = to_xy(train_ex)
    X_test, y_test = to_xy(test_ex)

    pipe = build_pipeline()
    pipe.fit(dicts_to_frame(X_train), y_train)

    y_prob_test = pipe.predict_proba(dicts_to_frame(X_test))[:, 1]

    auc = roc_auc_score(y_test, y_prob_test)
    brier = brier_score_loss(y_test, y_prob_test)
    baseline_brier = brier_score_loss(y_test, np.full_like(y_prob_test, y_train.mean()))

    print(f"Train examples: {len(train_ex)} (cycles < {args.split_cycle})")
    print(f"Test examples:  {len(test_ex)} (cycles >= {args.split_cycle})")
    print(f"Test base rate (failure): {y_test.mean():.4f}")
    print()
    print(f"ROC-AUC:              {auc:.4f}")
    print(f"Brier score (model):  {brier:.4f}")
    print(f"Brier score (const. base-rate predictor): {baseline_brier:.4f}  "
          f"(model must beat this to be worth deploying)")
    print()
    print("Reliability table (predicted probability vs actual failure rate,")
    print("on the held-out temporal test split -- this is what makes the")
    print("THRESHOLD_RETIME/THRESHOLD_SPLIT gates in config.py meaningful):")
    for row in reliability_table(y_test, y_prob_test):
        print(f"  {row['bucket']:<14} n={row['n']:>5}  "
              f"predicted={row['mean_predicted']:.3f}  actual={row['actual_fail_rate']:.3f}")

    if args.model_out:
        import joblib
        os.makedirs(os.path.dirname(args.model_out) or ".", exist_ok=True)
        joblib.dump(pipe, args.model_out)
        print(f"\nSaved model to {args.model_out}")


if __name__ == "__main__":
    main()
