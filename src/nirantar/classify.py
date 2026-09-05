"""
Phase 5 (predictor half): given features observable pre-debit, predict
WHICH decline cause is most likely, conditional on the attempt failing.
This is only ever consulted by policy.py when predict.py's calibrated
model already says the cycle is at risk -- classify.py does not decide
whether to act, only what the most probable reason would be if it fails,
which the cause -> action table (docs/TAXONOMY.md section 4) then maps to
an action.

Trained only on attempts that DID fail (label = true_cause from labels.csv,
joined on attempt_id), using the exact same feature set as predict.py
(features.py), with the same temporal holdout (cycles < 9 train, >= 9 test).

Honesty note: this synthetic environment's attempt-level physics
(environment.simulate_attempt) only ever produces seven of the twelve
causes in config.DECLINE_CAUSES -- MANDATE_REVOKED, PRE_DEBIT_OPT_OUT,
TOKEN_REISSUED, and MANDATE_PAUSED are modelled as mandate-STATE events,
not attempt-level failure causes, in this build. The classifier is trained
on and can only output the seven causes that actually occur in the data
(config.DECLINE_CAUSES minus those four, plus never predicting
UNCLASSIFIED since nothing in the synthetic world is unclassified by
construction). A production classifier facing real data would need to
handle all twelve, including a genuine UNCLASSIFIED residue -- this is a
known, stated gap between the simulation and production, not a hidden one.
(BANK_TECHNICAL_ERROR was dead code until a repo audit found and fixed it
-- see docs/METRICS.md's third bug entry -- which is why this count is
seven, not the six an earlier revision of this file claimed.)

Usage:
    python -m nirantar.classify --data data/seed7_v1 --model-out models/classify_v1.joblib
"""

from __future__ import annotations

import argparse
import os

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from nirantar.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, build_examples, load_csv


def build_pipeline() -> Pipeline:
    preprocess = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", StandardScaler(), NUMERIC_FEATURES),
    ])
    clf = RandomForestClassifier(n_estimators=200, max_depth=8, class_weight="balanced", random_state=7)
    return Pipeline([("preprocess", preprocess), ("model", clf)])


def build_cause_examples(data_dir: str) -> list[dict]:
    """Same feature rows as features.build_examples, but restricted to
    failed first attempts, with the label replaced by true_cause (joined
    from labels.csv -- attempts.csv itself never carries true_cause, same
    boundary predict.py respects)."""
    examples = build_examples(data_dir)
    labels = load_csv(os.path.join(data_dir, "labels.csv"))
    # labels.csv rows are attempt-level; we only have mandate/cycle here, so
    # rebuild the attempt_id the same way generate.py does for attempt_number==1.
    cause_by_key = {}
    for r in labels:
        # attempt_id format: f"{mandate_id}-C{cycle}-A{attempt_number}"
        parts = r["attempt_id"].rsplit("-A", 1)
        if len(parts) != 2 or parts[1] != "1":
            continue
        mandate_cycle = parts[0]  # "{mandate_id}-C{cycle}"
        cause_by_key[mandate_cycle] = r["true_cause"]

    out = []
    for e in examples:
        if e["label"] != 1:
            continue
        key = f"{e['mandate_id']}-C{e['cycle_index']}"
        cause = cause_by_key.get(key)
        if cause is None or cause == "NONE":
            continue
        row = dict(e)
        row["cause"] = cause
        out.append(row)
    return out


def to_xy(examples: list[dict]):
    X = [
        {**{k: e[k] for k in CATEGORICAL_FEATURES}, **{k: e[k] for k in NUMERIC_FEATURES}}
        for e in examples
    ]
    y = np.array([e["cause"] for e in examples])
    return X, y


def dicts_to_frame(X: list[dict]):
    import pandas as pd
    return pd.DataFrame(X)


def main():
    ap = argparse.ArgumentParser(description="Train/evaluate the pre-debit decline-cause classifier.")
    ap.add_argument("--data", type=str, required=True)
    ap.add_argument("--model-out", type=str, default=None)
    ap.add_argument("--split-cycle", type=int, default=9)
    args = ap.parse_args()

    examples = build_cause_examples(args.data)
    train_ex = [e for e in examples if e["cycle_index"] < args.split_cycle]
    test_ex = [e for e in examples if e["cycle_index"] >= args.split_cycle]

    X_train, y_train = to_xy(train_ex)
    X_test, y_test = to_xy(test_ex)

    pipe = build_pipeline()
    pipe.fit(dicts_to_frame(X_train), y_train)
    y_pred = pipe.predict(dicts_to_frame(X_test))

    print(f"Train (failed first-attempts, cycles < {args.split_cycle}): {len(train_ex)}")
    print(f"Test  (failed first-attempts, cycles >= {args.split_cycle}): {len(test_ex)}")
    print()
    print("Causes present in this synthetic build (see module docstring for")
    print("the four causes this environment never generates at attempt level):")
    print(sorted(set(y_train) | set(y_test)))
    print()
    print(classification_report(y_test, y_pred, zero_division=0))

    if args.model_out:
        import joblib
        os.makedirs(os.path.dirname(args.model_out) or ".", exist_ok=True)
        joblib.dump(pipe, args.model_out)
        print(f"Saved model to {args.model_out}")


if __name__ == "__main__":
    main()
