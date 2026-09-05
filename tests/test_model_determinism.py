"""
Two correctness properties predict.py/classify.py/artifacts.py needed but
had no direct test for (only policy.py/notify.py and the RNG-isolation
guarantee were covered before this file):

1. Training determinism: re-running predict.py's pipeline on the SAME
   data must produce bit-identical predictions. This matters because
   every headline number in docs/METRICS.md is only meaningful if a judge
   re-running the exact same command gets the exact same model -- an
   undetermined random component (e.g. an unseeded internal CV split)
   would silently make "reproduce this run" impossible.
2. artifacts.py's split_cycle actually restricts what data the reference
   tables are built from -- the same class of leakage risk features.py's
   own docstring warns about ("callers are responsible for passing ONLY a
   training split"). A silent bug that ignored split_cycle and used the
   whole dataset regardless would not show up as a crash, only as a
   quietly-too-optimistic number -- so this is checked directly rather
   than trusted from the docstring alone.

Uses a small in-memory synthetic dataset (not data/seed7_v1) so this file
runs standalone, the same way test_rng_isolation.py and
test_policy_and_notify.py do -- no dependency on the full pipeline having
been run first.

Run with:  PYTHONPATH=src python3 tests/test_model_determinism.py
"""

from __future__ import annotations

import random
import shutil
import tempfile
from datetime import date

from nirantar.population import make_mandates
from nirantar.simulate import run_generation
from nirantar.generate import write_outputs
from nirantar.features import build_examples
from nirantar import artifacts as artifacts_mod
from nirantar import predict


def _make_small_dataset(out_dir: str, seed: int = 3, n_mandates: int = 300, n_months: int = 12) -> None:
    start = date(2025, 9, 1)
    rng = random.Random(seed)
    mandates = make_mandates(rng, n_mandates, start, arm_salt=f"seed{seed}")
    mandates, attempts, reg_failures = run_generation(mandates, n_months, start, seed, policy_fn=None)
    write_outputs(out_dir, mandates, attempts, reg_failures)


def test_predict_pipeline_is_deterministic(data_dir: str) -> None:
    examples = build_examples(data_dir)
    train_ex = [e for e in examples if e["cycle_index"] < 9]
    test_ex = [e for e in examples if e["cycle_index"] >= 9]
    assert train_ex and test_ex, "test dataset too small to exercise the temporal split"

    X_train, y_train = predict.to_xy(train_ex)
    X_test, y_test = predict.to_xy(test_ex)

    probs = []
    for _ in range(2):
        pipe = predict.build_pipeline()
        pipe.fit(predict.dicts_to_frame(X_train), y_train)
        probs.append(pipe.predict_proba(predict.dicts_to_frame(X_test))[:, 1])

    assert len(probs[0]) == len(probs[1]) and len(probs[0]) > 0
    mismatches = sum(1 for a, b in zip(probs[0], probs[1]) if a != b)
    assert mismatches == 0, (
        f"{mismatches}/{len(probs[0])} predicted probabilities differed between two "
        f"fits of the SAME pipeline on the SAME data -- a headline lift number computed "
        f"from a non-deterministic model can't be reproduced by re-running the command"
    )
    print(f"PASS: predict.py's pipeline is bit-for-bit deterministic across two fits "
          f"({len(probs[0])} test predictions compared).")


def test_artifacts_split_cycle_actually_restricts_data(data_dir: str) -> None:
    from nirantar.features import load_csv
    import os
    attempts = load_csv(os.path.join(data_dir, "attempts.csv"))
    max_cycle = max(int(a["cycle_index"]) for a in attempts)
    assert max_cycle >= 9, "test dataset doesn't span enough cycles to test this"

    empty_split = artifacts_mod.build(data_dir, split_cycle=0)
    full_split = artifacts_mod.build(data_dir, split_cycle=max_cycle + 1)

    assert empty_split["bank_psp_fail_rates"] == {}, (
        "split_cycle=0 should train on ZERO cycles (cycle_index < 0 matches nothing), "
        "but bank_psp_fail_rates is non-empty -- split_cycle is not actually restricting "
        "what compute_bank_psp_fail_rates() sees"
    )
    assert len(full_split["bank_psp_fail_rates"]) > 0, (
        "split_cycle covering every cycle produced no rates at all -- something else is broken"
    )
    print(f"PASS: artifacts.build()'s split_cycle genuinely restricts the training data "
          f"(0 bank/psp pairs at split_cycle=0, {len(full_split['bank_psp_fail_rates'])} "
          f"at split_cycle={max_cycle + 1}).")


def main():
    tmp_dir = tempfile.mkdtemp(prefix="nirantar_test_")
    try:
        _make_small_dataset(tmp_dir)
        test_predict_pipeline_is_deterministic(tmp_dir)
        test_artifacts_split_cycle_actually_restricts_data(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print()
    print("All model-determinism tests passed.")


if __name__ == "__main__":
    main()
