"""
Mandate population creation: who exists, on what plan, what rail, and
whether they even complete registration. Separate from environment.py
(per-attempt physics) and simulate.py (the cycle loop).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from nirantar.environment import PLAN_PROFILES, BANKS, PSP_APPS


@dataclass
class Mandate:
    mandate_id: str
    customer_id: str
    plan: str
    amount_paise: int
    rail: str
    bank: str
    psp_app: str
    salary_day: int
    registered_on: str
    arm: str = "control"  # "control" or "treatment" -- stable holdout assignment
    state: str = "REGISTERED"
    consecutive_failures: int = 0
    partial_allowed: bool = False


def assign_arm(mandate_id: str, salt: str) -> str:
    """
    Stable, deterministic control/treatment assignment via hashing --
    computed once per mandate, independent of whether a policy is actually
    applied in a given run. This is what lets a "policy_fn=None" baseline
    run and a "policy_fn=nirantar_policy" run share the same population
    and the same arm labels, so control-arm mandates are directly
    comparable across both runs.
    """
    h = sum(ord(c) for c in f"{mandate_id}:{salt}")
    return "treatment" if (h % 2 == 0) else "control"


def choose_rail(rng: random.Random, weights: dict) -> str:
    rails = list(weights.keys())
    probs = list(weights.values())
    return rng.choices(rails, weights=probs, k=1)[0]


def make_mandates(rng: random.Random, n: int, start_date: date, arm_salt: str) -> list[Mandate]:
    mandates = []
    for i in range(n):
        plan = rng.choice(PLAN_PROFILES)
        rail = choose_rail(rng, plan.rail_weights)
        salary_day = rng.choice([1, 1, 1, 7, 7, 15, 25, 28])
        registered_offset = rng.randint(0, 20)
        mandate_id = f"MND{i:06d}"
        m = Mandate(
            mandate_id=mandate_id,
            customer_id=f"CUST{i:06d}",
            plan=plan.name,
            amount_paise=plan.amount_paise,
            rail=rail,
            bank=rng.choice(BANKS),
            psp_app=rng.choice(PSP_APPS) if rail == "UPI_AUTOPAY" else "",
            salary_day=salary_day,
            registered_on=(start_date + timedelta(days=registered_offset)).isoformat(),
            arm=assign_arm(mandate_id, arm_salt),
            partial_allowed=plan.partial_allowed,
        )
        mandates.append(m)
    return mandates
