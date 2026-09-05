"""
Regression test for the per-mandate RNG isolation fix.

The whole holdout/control-group experimental design (docs promised, dossier
promised, user confirmed) depends on one guarantee: a control-arm mandate's
Attempt records must be identical whether run_generation() is called with
policy_fn=None or with a real policy_fn that intervenes on treatment-arm
mandates. If mandates ever shared one RNG stream, a treatment intervention
(e.g. HOLD skipping simulate_attempt calls) would shift the draw sequence
for every mandate processed afterward -- including unrelated control-arm
ones -- and this test would catch that by failing.

Run with:  PYTHONPATH=src python3 tests/test_rng_isolation.py
"""

from __future__ import annotations

import random
from dataclasses import asdict
from datetime import date, timedelta

from nirantar.population import make_mandates
from nirantar.simulate import run_generation, Intervention


def aggressive_policy(mandate, cycle_index, scheduled_date, history):
    """
    A deliberately hyperactive policy: HOLDs on odd cycles, RETIMEs on even
    cycles by pushing the date forward, and suppresses native retries. This
    is designed to maximally perturb the treatment arm's random-draw count
    relative to baseline, so it's a strong test of isolation.
    """
    if mandate.arm != "treatment":
        return None
    if cycle_index % 2 == 1:
        return Intervention(action="HOLD")
    return Intervention(
        action="RETIME",
        new_scheduled_date=scheduled_date + timedelta(days=3),
        suppress_further_native_retries=True,
    )


def main():
    seed = 7
    start = date(2025, 9, 1)

    rng_a = random.Random(seed)
    mandates_a = make_mandates(rng_a, 500, start, arm_salt=f"seed{seed}")
    mandates_a, attempts_a, _ = run_generation(mandates_a, 12, start, seed, policy_fn=None)

    rng_b = random.Random(seed)
    mandates_b = make_mandates(rng_b, 500, start, arm_salt=f"seed{seed}")
    mandates_b, attempts_b, _ = run_generation(mandates_b, 12, start, seed, policy_fn=aggressive_policy)

    control_ids = {m.mandate_id for m in mandates_a if m.arm == "control"}
    assert control_ids == {m.mandate_id for m in mandates_b if m.arm == "control"}, \
        "control-arm membership itself differed between runs -- arm assignment is not stable"

    control_a = [a for a in attempts_a if a.mandate_id in control_ids]
    control_b = [a for a in attempts_b if a.mandate_id in control_ids]

    def key(a):
        return a.attempt_id

    control_a.sort(key=key)
    control_b.sort(key=key)

    assert len(control_a) == len(control_b), (
        f"control-arm attempt COUNT differs: baseline={len(control_a)} "
        f"vs with-policy={len(control_b)} -- RNG isolation is broken"
    )

    mismatches = []
    for a, b in zip(control_a, control_b):
        da, db = asdict(a), asdict(b)
        # intervention bookkeeping fields are allowed to differ trivially
        # (they should both be NONE/False for control anyway); compare the
        # physics-determined fields that must be identical.
        for field in ("outcome", "true_cause", "hour", "day_of_month",
                      "days_since_salary_credit", "scheduled_date"):
            if da[field] != db[field]:
                mismatches.append((a.attempt_id, field, da[field], db[field]))

    treatment_ids = {m.mandate_id for m in mandates_b if m.arm == "treatment"}
    assert treatment_ids, "treatment arm is empty -- test is vacuous"

    # aggressive_policy HOLDs every odd cycle for every treatment mandate --
    # HOLD's contract (simulate.py) is "no attempt at all this cycle," so a
    # real regression check is that no odd-cycle attempt record exists at
    # all for any treatment mandate, not just that the policy was consulted.
    odd_cycle_attempts = [
        a for a in attempts_b
        if a.mandate_id in treatment_ids and a.cycle_index % 2 == 1
    ]
    assert not odd_cycle_attempts, (
        f"found {len(odd_cycle_attempts)} attempt(s) on a HOLD-ed odd cycle "
        f"for a treatment mandate -- HOLD is supposed to skip the attempt "
        f"entirely, e.g. {odd_cycle_attempts[0].attempt_id!r}"
    )

    if mismatches:
        print(f"FAIL: {len(mismatches)} control-arm field mismatches. First 5:")
        for m in mismatches[:5]:
            print("  ", m)
        raise SystemExit(1)

    print(f"PASS: {len(control_a)} control-arm attempts byte-identical "
          f"between policy_fn=None and an aggressive HOLD/RETIME policy "
          f"on the treatment arm ({len(control_ids)} control mandates, "
          f"{len(mandates_a) - len(control_ids)} treatment mandates).")
    print("PASS: no attempt record exists for any HOLD-ed odd cycle "
          "on a treatment mandate (HOLD skips the attempt entirely).")


if __name__ == "__main__":
    main()
