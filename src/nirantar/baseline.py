"""
Phase 3: summarise the no-intervention (native-retry-only) dataset. This is
the fixed reference point every later lift number is measured against --
"build the baseline before the model."

Reads the CSVs generate.py writes (mandates.csv, attempts.csv, labels.csv,
registration_failures.csv) and reports, per docs/METRICS.md's eventual
format:
  - funnel: mandates -> registered -> active cycles -> successful cycles
  - money recovered vs money at risk, in rupees (paise / 100), NOT floats
    for anything that gets summed -- only the final printed rupee value is
    a float, all summation stays in integer paise.
  - attempts-per-successful-collection (the "how much retry effort per
    rupee" number a judge will ask about)
  - decline-cause breakdown (from labels.csv's true_cause, which a real
    production system would NOT have -- classify.py/predict.py exist
    precisely because true_cause is not observable outside this simulator)

Usage:
    python -m nirantar.baseline --data data/seed7_v1
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter, defaultdict


def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def summarise(data_dir: str) -> dict:
    mandates = load_csv(os.path.join(data_dir, "mandates.csv"))
    attempts = load_csv(os.path.join(data_dir, "attempts.csv"))
    labels = load_csv(os.path.join(data_dir, "labels.csv"))
    reg_failures = load_csv(os.path.join(data_dir, "registration_failures.csv"))

    n_mandates = len(mandates)
    n_reg_failed = len(reg_failures)
    n_registered = n_mandates - n_reg_failed
    n_revoked = sum(1 for m in mandates if m["state"] == "REVOKED")

    true_cause_by_attempt = {r["attempt_id"]: r["true_cause"] for r in labels}

    # Group attempts by (mandate_id, cycle_index) to find one "cycle" = one
    # billing month's worth of T+1/T+2/T+3 attempts, and whether it ever
    # succeeded and how many attempts it took.
    cycles: dict[tuple, list[dict]] = defaultdict(list)
    for a in attempts:
        cycles[(a["mandate_id"], a["cycle_index"])].append(a)

    amount_by_mandate = {m["mandate_id"]: int(m["amount_paise"]) for m in mandates}

    n_cycles = len(cycles)
    n_cycles_succeeded = 0
    attempts_in_succeeded_cycles = 0
    paise_recovered = 0
    paise_at_risk = 0

    for (mandate_id, _cycle_idx), cycle_attempts in cycles.items():
        cycle_attempts.sort(key=lambda a: int(a["attempt_number"]))
        amount = amount_by_mandate[mandate_id]
        paise_at_risk += amount
        succeeded = any(a["outcome"] == "SUCCESS" for a in cycle_attempts)
        if succeeded:
            n_cycles_succeeded += 1
            paise_recovered += amount
            # attempts up to and including the first SUCCESS
            for i, a in enumerate(cycle_attempts, start=1):
                if a["outcome"] == "SUCCESS":
                    attempts_in_succeeded_cycles += i
                    break

    cause_counts = Counter(true_cause_by_attempt.values())
    cause_counts.pop("NONE", None)  # successes have true_cause "NONE"

    n_failed_attempts = sum(1 for a in attempts if a["outcome"] == "FAILED")

    return {
        "mandates_total": n_mandates,
        "mandates_registered": n_registered,
        "registration_dropoff_rate": round(n_reg_failed / n_mandates, 4),
        "mandates_revoked": n_revoked,
        "revoke_rate_of_registered": round(n_revoked / max(n_registered, 1), 4),
        "billing_cycles_total": n_cycles,
        "billing_cycles_recovered": n_cycles_succeeded,
        "cycle_recovery_rate": round(n_cycles_succeeded / max(n_cycles, 1), 4),
        "rupees_at_risk": paise_at_risk / 100.0,
        "rupees_recovered": paise_recovered / 100.0,
        "rupees_lost_to_non_recovery": (paise_at_risk - paise_recovered) / 100.0,
        "attempts_total": len(attempts),
        "attempts_failed": n_failed_attempts,
        "attempt_failure_rate": round(n_failed_attempts / max(len(attempts), 1), 4),
        "avg_attempts_per_recovered_cycle": round(
            attempts_in_succeeded_cycles / max(n_cycles_succeeded, 1), 3
        ),
        "decline_cause_breakdown": dict(cause_counts.most_common()),
    }


def print_report(stats: dict) -> None:
    print("=== Nirantar baseline (native retry only, no intervention) ===")
    print(f"Mandates created:            {stats['mandates_total']}")
    print(f"Registered (post-dropoff):   {stats['mandates_registered']} "
          f"(dropoff {stats['registration_dropoff_rate']:.1%})")
    print(f"Revoked during observation:  {stats['mandates_revoked']} "
          f"({stats['revoke_rate_of_registered']:.1%} of registered)")
    print()
    print(f"Billing cycles attempted:    {stats['billing_cycles_total']}")
    print(f"Billing cycles recovered:    {stats['billing_cycles_recovered']} "
          f"({stats['cycle_recovery_rate']:.1%})")
    print()
    print(f"Rupees at risk:              Rs {stats['rupees_at_risk']:,.2f}")
    print(f"Rupees recovered:            Rs {stats['rupees_recovered']:,.2f}")
    print(f"Rupees lost (unrecovered):   Rs {stats['rupees_lost_to_non_recovery']:,.2f}")
    print()
    print(f"Attempts total:              {stats['attempts_total']} "
          f"(failed {stats['attempt_failure_rate']:.1%})")
    print(f"Avg attempts/recovered cycle:{stats['avg_attempts_per_recovered_cycle']:>6.3f}")
    print()
    print("Decline cause breakdown (ground truth -- not observable in production):")
    total_fail = sum(stats["decline_cause_breakdown"].values())
    for cause, n in stats["decline_cause_breakdown"].items():
        print(f"  {cause:<28} {n:>6}  ({n/max(total_fail,1):.1%})")


def main():
    ap = argparse.ArgumentParser(description="Summarise a Nirantar baseline dataset.")
    ap.add_argument("--data", type=str, required=True)
    args = ap.parse_args()
    stats = summarise(args.data)
    print_report(stats)


if __name__ == "__main__":
    main()
