"""
CLI: generate the baseline synthetic dataset (no Nirantar intervention --
pure native T+1/T+2/T+3 retry behaviour). This is both (a) the dataset
docs/METRICS.md's baseline.py summarises, and (b) the training data for
predict.py's classifier -- with a temporal holdout enforced at training
time, not at generation time (see predict.py).

Usage:
    python -m nirantar.generate --seed 7 --mandates 4000 --months 12 --out data/seed7
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from dataclasses import asdict
from datetime import date

from nirantar.population import make_mandates
from nirantar.simulate import run_generation


def write_outputs(out_dir: str, mandates, attempts, registration_failures) -> None:
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "mandates.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(mandates[0]).keys()))
        w.writeheader()
        for m in mandates:
            w.writerow(asdict(m))

    with open(os.path.join(out_dir, "attempts.csv"), "w", newline="") as f:
        fieldnames = [k for k in asdict(attempts[0]).keys() if k != "true_cause"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for a in attempts:
            row = asdict(a)
            row.pop("true_cause")
            w.writerow(row)

    with open(os.path.join(out_dir, "labels.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["attempt_id", "mandate_id", "outcome", "true_cause"])
        w.writeheader()
        for a in attempts:
            w.writerow({"attempt_id": a.attempt_id, "mandate_id": a.mandate_id,
                        "outcome": a.outcome, "true_cause": a.true_cause})

    with open(os.path.join(out_dir, "registration_failures.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["mandate_id", "customer_id", "stage"])
        w.writeheader()
        for r in registration_failures:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic Nirantar mandate/attempt data (baseline, no intervention).")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--mandates", type=int, default=4000)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--start-date", type=str, default="2025-09-01")
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()

    start = date.fromisoformat(args.start_date)
    rng = random.Random(args.seed)
    mandates = make_mandates(rng, args.mandates, start, arm_salt=f"seed{args.seed}")

    mandates, attempts, reg_failures = run_generation(
        mandates, args.months, start, args.seed, policy_fn=None
    )
    write_outputs(args.out, mandates, attempts, reg_failures)

    n_active = sum(1 for m in mandates if m.state != "CREATED")
    n_failed_attempts = sum(1 for a in attempts if a.outcome == "FAILED")
    n_revoked = sum(1 for m in mandates if m.state == "REVOKED")
    print(f"mandates={len(mandates)} registered={n_active} registration_dropoff={len(reg_failures)}")
    print(f"attempts={len(attempts)} failed={n_failed_attempts} "
          f"failure_rate={n_failed_attempts/len(attempts):.3f}")
    print(f"revoked={n_revoked} revoke_rate={n_revoked/max(n_active,1):.3f}")


if __name__ == "__main__":
    main()
