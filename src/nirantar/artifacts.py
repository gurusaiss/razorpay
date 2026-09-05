"""
Small reference tables the policy engine needs at scoring time, built ONCE
from the training split and persisted to JSON -- so policy.py never
recomputes anything from live data it's currently deciding on (that would
be the same leakage predict.py/classify.py's temporal holdout is designed
to avoid).

Two tables:
  - bank_psp_fail_rates: same empirical rate features.py uses as a
    training feature, keyed as "BANK|PSP_APP" (JSON can't key dicts by
    tuple).
  - rail_catalog: which rails have been observed in use for each plan,
    from mandates.csv. This is a data-driven stand-in for "which rails
    does this merchant have configured" -- deliberately NOT read from
    environment.PLAN_PROFILES (that would be importing the simulator's
    ground-truth configuration directly; a real system would instead read
    this from its own merchant-configuration database, which is exactly
    what an empirical table built from observed mandates approximates).

Usage:
    python -m nirantar.artifacts --data data/seed7_v1 --split-cycle 9 --out models/artifacts_v1.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

from nirantar.features import compute_bank_psp_fail_rates, load_csv

MIN_HOUR_SAMPLES = 20  # don't trust an hour's empirical rate on a handful of attempts


def compute_best_hour_by_bank_psp(attempts: list[dict]) -> dict[tuple, int]:
    """
    Empirical failure rate per (bank, psp_app, hour), using ALL attempt
    numbers (not just first attempts) -- native retries already sample a
    spread of hours, so their outcomes are exactly the historical evidence
    a policy can lean on to pick a genuinely safer presentment hour for
    the NEXT cycle's first attempt. This is what makes an hour-shifting
    RETIME a real lever against a bank/PSP outage window that a pure
    date-shift can't touch (environment.bank_psp_downtime is keyed on hour,
    not date).
    """
    fail = defaultdict(int)
    total = defaultdict(int)
    for a in attempts:
        key = (a["bank"], a["psp_app"], int(a["hour"]))
        total[key] += 1
        if a["outcome"] == "FAILED":
            fail[key] += 1

    by_pair: dict[tuple, list] = defaultdict(list)
    for (bank, psp, hour), n in total.items():
        if n < MIN_HOUR_SAMPLES:
            continue
        rate = fail[(bank, psp, hour)] / n
        by_pair[(bank, psp)].append((hour, rate, n))

    best_hour = {}
    for pair, rows in by_pair.items():
        rows.sort(key=lambda r: r[1])  # lowest fail rate first
        best_hour[pair] = rows[0][0]
    return best_hour


def build(data_dir: str, split_cycle: int) -> dict:
    attempts = load_csv(os.path.join(data_dir, "attempts.csv"))
    mandates = load_csv(os.path.join(data_dir, "mandates.csv"))

    train_attempts = [a for a in attempts if int(a["cycle_index"]) < split_cycle]
    rates = compute_bank_psp_fail_rates(train_attempts)
    bank_psp_fail_rates = {f"{b}|{p}": r for (b, p), r in rates.items()}

    best_hour = compute_best_hour_by_bank_psp(train_attempts)
    best_hour_json = {f"{b}|{p}": h for (b, p), h in best_hour.items()}

    # Rail catalog from the first attempt's rail per mandate/plan -- a
    # mandate only ever has one rail on file in this synthetic build, but
    # the CATALOG is built per PLAN across all mandates on that plan, so a
    # plan with several mandates each on a different rail correctly shows
    # up as multi-rail-capable (an alternate rail exists somewhere for that
    # plan) even though any single mandate itself is single-rail.
    rail_catalog: dict[str, set] = defaultdict(set)
    for m in mandates:
        rail_catalog[m["plan"]].add(m["rail"])
    rail_catalog_json = {plan: sorted(rails) for plan, rails in rail_catalog.items()}

    return {
        "split_cycle": split_cycle,
        "bank_psp_fail_rates": bank_psp_fail_rates,
        "rail_catalog": rail_catalog_json,
        "best_hour_by_bank_psp": best_hour_json,
    }


def save(artifacts: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(artifacts, f, indent=2)


def load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def lookup_bank_psp_rate(artifacts: dict, bank: str, psp_app: str, fallback: float = 0.15) -> float:
    return artifacts["bank_psp_fail_rates"].get(f"{bank}|{psp_app}", fallback)


def alternate_rail_exists(artifacts: dict, plan: str, current_rail: str) -> bool:
    rails = artifacts["rail_catalog"].get(plan, [])
    return any(r != current_rail for r in rails)


def lookup_best_hour(artifacts: dict, bank: str, psp_app: str) -> int | None:
    return artifacts.get("best_hour_by_bank_psp", {}).get(f"{bank}|{psp_app}")


def main():
    ap = argparse.ArgumentParser(description="Build policy-time reference tables from a training split.")
    ap.add_argument("--data", type=str, required=True)
    ap.add_argument("--split-cycle", type=int, default=9)
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()
    artifacts = build(args.data, args.split_cycle)
    save(artifacts, args.out)
    print(f"Saved artifacts to {args.out}: "
          f"{len(artifacts['bank_psp_fail_rates'])} bank/psp pairs, "
          f"{len(artifacts['rail_catalog'])} plans in rail catalog, "
          f"{len(artifacts['best_hour_by_bank_psp'])} bank/psp pairs with a recommended hour")


if __name__ == "__main__":
    main()
