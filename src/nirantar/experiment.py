"""
Phase 7: the holdout experiment. Generates the SAME population (same seed,
same mandate count) twice -- once with policy_fn=None (baseline) and once
with the real policy engine active on the treatment arm only -- and
reports incremental lift as a same-population, same-seed comparison,
never a before/after on different data.

Three numbers, per docs/METRICS.md's own stated plan:
  1. Incremental cycle-recovery lift (treatment arm, policy run vs
     baseline run, same mandates).
  2. Attempts saved (avg attempts/recovered cycle, same comparison).
  3. Rupees protected (the recovery lift converted to rupees), reported
     against rupees_lost_to_non_recovery so the ceiling is stated, not
     implied.

Also reports the CONTROL arm's before/after difference, which must be
~zero (mandate-count differences of zero, cycle-recovery differences of
zero) -- this is the same guarantee tests/test_rng_isolation.py checks
with an adversarial synthetic policy, now re-checked at full scale with
the REAL trained policy, as the integrity gate this file's headline
numbers are allowed to be trusted after.

Usage:
    python -m nirantar.experiment --seed 7 --mandates 4000 --months 12 \
        --predict-model models/predict_v1.joblib \
        --classify-model models/classify_v1.joblib \
        --artifacts models/artifacts_v1.json
"""

from __future__ import annotations

import argparse
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from nirantar.population import make_mandates, Mandate
from nirantar.simulate import run_generation, Attempt
from nirantar.policy import PolicyModels, make_policy_fn


@dataclass
class CycleStats:
    n_cycles: int
    n_recovered: int
    attempts_in_recovered_cycles: int
    paise_at_risk: int
    paise_recovered: int

    @property
    def recovery_rate(self) -> float:
        return self.n_recovered / self.n_cycles if self.n_cycles else 0.0

    @property
    def avg_attempts_per_recovered_cycle(self) -> float:
        return self.attempts_in_recovered_cycles / self.n_recovered if self.n_recovered else 0.0


def cycle_stats(attempts: list[Attempt], amount_by_mandate: dict[str, int],
                 mandate_ids: set[str] | None = None) -> CycleStats:
    by_cycle: dict[tuple, list[Attempt]] = defaultdict(list)
    for a in attempts:
        if mandate_ids is not None and a.mandate_id not in mandate_ids:
            continue
        by_cycle[(a.mandate_id, a.cycle_index)].append(a)

    n_cycles = len(by_cycle)
    n_recovered = 0
    attempts_in_recovered = 0
    paise_at_risk = 0
    paise_recovered = 0

    for (mandate_id, _cycle), cycle_attempts in by_cycle.items():
        cycle_attempts.sort(key=lambda a: a.attempt_number)
        amount = amount_by_mandate[mandate_id]
        paise_at_risk += amount
        for i, a in enumerate(cycle_attempts, start=1):
            if a.outcome == "SUCCESS":
                n_recovered += 1
                attempts_in_recovered += i
                paise_recovered += amount
                break

    return CycleStats(n_cycles, n_recovered, attempts_in_recovered, paise_at_risk, paise_recovered)


def run_pair(seed: int, n_mandates: int, n_months: int, start: date,
             models: PolicyModels) -> tuple[list[Mandate], list[Attempt], list[Mandate], list[Attempt]]:
    rng_a = random.Random(seed)
    mandates_baseline = make_mandates(rng_a, n_mandates, start, arm_salt=f"seed{seed}")
    mandates_baseline, attempts_baseline, _ = run_generation(
        mandates_baseline, n_months, start, seed, policy_fn=None
    )

    rng_b = random.Random(seed)
    mandates_policy = make_mandates(rng_b, n_mandates, start, arm_salt=f"seed{seed}")
    policy_fn = make_policy_fn(models)
    mandates_policy, attempts_policy, _ = run_generation(
        mandates_policy, n_months, start, seed, policy_fn=policy_fn
    )

    return mandates_baseline, attempts_baseline, mandates_policy, attempts_policy


def main():
    ap = argparse.ArgumentParser(description="Phase 7: measure Nirantar's incremental lift via a holdout comparison.")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--mandates", type=int, default=4000)
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--start-date", type=str, default="2025-09-01")
    ap.add_argument("--predict-model", type=str, default="models/predict_v1.joblib")
    ap.add_argument("--classify-model", type=str, default="models/classify_v1.joblib")
    ap.add_argument("--artifacts", type=str, default="models/artifacts_v1.json")
    args = ap.parse_args()

    start = date.fromisoformat(args.start_date)
    models = PolicyModels.load(args.predict_model, args.classify_model, args.artifacts)

    mandates_b, attempts_b, mandates_p, attempts_p = run_pair(
        args.seed, args.mandates, args.months, start, models
    )

    amount_by_mandate = {m.mandate_id: m.amount_paise for m in mandates_b}
    control_ids = {m.mandate_id for m in mandates_b if m.arm == "control"}
    treatment_ids = {m.mandate_id for m in mandates_b if m.arm == "treatment"}
    assert control_ids == {m.mandate_id for m in mandates_p if m.arm == "control"}
    assert treatment_ids == {m.mandate_id for m in mandates_p if m.arm == "treatment"}

    # --- Integrity gate: control arm must be unchanged ---
    control_b = cycle_stats(attempts_b, amount_by_mandate, control_ids)
    control_p = cycle_stats(attempts_p, amount_by_mandate, control_ids)

    control_attempts_b = sorted(
        (a.attempt_id, a.outcome, a.true_cause, a.scheduled_date)
        for a in attempts_b if a.mandate_id in control_ids
    )
    control_attempts_p = sorted(
        (a.attempt_id, a.outcome, a.true_cause, a.scheduled_date)
        for a in attempts_p if a.mandate_id in control_ids
    )
    integrity_ok = control_attempts_b == control_attempts_p

    print("=== Integrity gate: control arm must be byte-identical ===")
    print(f"Control-arm attempt records identical: {integrity_ok}")
    print(f"  baseline: n_cycles={control_b.n_cycles} recovered={control_b.n_recovered} "
          f"({control_b.recovery_rate:.4%})")
    print(f"  policy:   n_cycles={control_p.n_cycles} recovered={control_p.n_recovered} "
          f"({control_p.recovery_rate:.4%})")
    if not integrity_ok:
        print("FAIL: control arm changed between runs -- the holdout comparison below is NOT valid.")
        raise SystemExit(1)
    print()

    # --- Headline: treatment arm, same mandates, baseline vs policy ---
    treat_b = cycle_stats(attempts_b, amount_by_mandate, treatment_ids)
    treat_p = cycle_stats(attempts_p, amount_by_mandate, treatment_ids)

    lift_pp = (treat_p.recovery_rate - treat_b.recovery_rate) * 100
    attempts_delta = treat_p.avg_attempts_per_recovered_cycle - treat_b.avg_attempts_per_recovered_cycle
    rupees_lift = (treat_p.paise_recovered - treat_b.paise_recovered) / 100.0

    print("=== Treatment arm: same mandates, same seed, baseline vs with-policy ===")
    print(f"Cycles (treatment arm):        {treat_b.n_cycles}")
    print(f"Recovery rate, baseline:       {treat_b.recovery_rate:.4%}")
    print(f"Recovery rate, with policy:    {treat_p.recovery_rate:.4%}")
    print(f"Incremental lift:              {lift_pp:+.3f} percentage points")
    print()
    print(f"Avg attempts/recovered cycle, baseline:    {treat_b.avg_attempts_per_recovered_cycle:.3f}")
    print(f"Avg attempts/recovered cycle, with policy: {treat_p.avg_attempts_per_recovered_cycle:.3f}")
    print(f"Attempts saved per recovered cycle:        {-attempts_delta:+.3f}")
    print()
    print(f"Rupees recovered, baseline:     Rs {treat_b.paise_recovered/100:,.2f}")
    print(f"Rupees recovered, with policy:  Rs {treat_p.paise_recovered/100:,.2f}")
    print(f"Rupees protected (lift):        Rs {rupees_lift:,.2f}")
    print(f"Rupees at risk (ceiling):       Rs {treat_b.paise_at_risk/100:,.2f}")
    print(f"Rupees lost even w/ policy:     Rs {(treat_p.paise_at_risk - treat_p.paise_recovered)/100:,.2f}")


if __name__ == "__main__":
    main()
