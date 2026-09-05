"""
The cycle loop: for each active mandate, run up to 12 monthly billing
cycles, each with up to 3 native-retry attempts (T+1/T+2/T+3), producing
ground-truth attempt records.

This is the ONE place both the baseline and treatment datasets are
produced from, controlled entirely by whether a `policy_fn` is supplied:

  - policy_fn=None            -> pure baseline: no mandate is ever
                                  intervened on, regardless of its arm.
                                  This is what generate.py uses.
  - policy_fn=<callable>      -> only mandates with arm == "treatment"
                                  are offered to the policy before their
                                  cycle's first attempt is scheduled;
                                  arm == "control" mandates are run
                                  exactly as in the baseline. This is
                                  what run_experiment.py (Phase 7) uses,
                                  and it is what makes the control arm in
                                  that experiment identical in mechanism
                                  to the plain baseline run.

policy_fn signature:
    policy_fn(mandate, cycle_index, scheduled_date, history) -> Intervention | None

`history` is a list of the mandate's own prior Attempt records only
(never future ones, never other mandates' records) -- this is what keeps
the predictor honest about not seeing the future.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from nirantar import config
from nirantar.environment import simulate_attempt, next_business_day
from nirantar.population import Mandate


@dataclass
class Attempt:
    attempt_id: str
    mandate_id: str
    arm: str
    cycle_index: int
    scheduled_date: str
    original_scheduled_date: str  # before any RETIME -- lets us measure the shift
    attempt_number: int
    amount_paise: int
    rail: str
    bank: str
    psp_app: str
    hour: int
    day_of_month: int
    days_since_salary_credit: int
    outcome: str
    true_cause: str
    intervention_action: str = "NONE"  # what the policy did, if anything
    suppressed_native_retry: bool = False


@dataclass
class Intervention:
    action: str            # one of config.PERMITTED_ACTIONS
    new_scheduled_date: Optional[date] = None   # for RETIME (date component)
    forced_hour: Optional[int] = None           # for RETIME (time-of-day component)
    suppress_further_native_retries: bool = False  # the coordination lock


PolicyFn = Callable[[Mandate, int, date, list[Attempt]], Optional[Intervention]]


def run_generation(
    mandates: list[Mandate],
    n_months: int,
    start_date: date,
    seed: int,
    policy_fn: Optional[PolicyFn] = None,
) -> tuple[list[Mandate], list[Attempt], list[dict]]:
    seed_key = str(seed)

    # CORRECTNESS: each mandate gets its OWN independent RNG stream, seeded
    # from (seed, mandate_id) rather than all mandates sharing one global
    # stream in iteration order. This is not a style choice -- it is what
    # makes a control-arm mandate's outcome identical whether this function
    # is called with policy_fn=None or with a real policy_fn. If mandates
    # shared one rng, a treatment-arm intervention earlier in the loop
    # would consume a different number of draws and silently shift every
    # later mandate's random outcomes, including control-arm ones,
    # invalidating the holdout comparison the whole experiment depends on.
    def mandate_rng(mandate_id: str) -> random.Random:
        return random.Random(f"{seed_key}:{mandate_id}")

    registration_failures: list[dict] = []
    for m in mandates:
        r = mandate_rng(m.mandate_id)
        if r.random() < 0.30:
            registration_failures.append({
                "mandate_id": m.mandate_id, "customer_id": m.customer_id,
                "stage": "CREATED_NEVER_REGISTERED",
            })
            m.state = "CREATED"

    active_mandates = [m for m in mandates if m.state != "CREATED"]
    attempts: list[Attempt] = []

    for m in active_mandates:
        # Fresh stream per mandate, offset from the registration-check draw
        # above by construction (a new Random() with a distinguishing salt),
        # so this mandate's cycle history depends only on its own id -- not
        # on iteration order, not on any other mandate's arm or policy.
        rng = random.Random(f"{seed_key}:{m.mandate_id}:cycles")
        m.state = "ACTIVE"
        mandate_history: list[Attempt] = []
        cycle_date = date(start_date.year, start_date.month, min(m.salary_day, 28))
        revoked = False

        for cycle in range(n_months):
            if revoked:
                break

            scheduled = next_business_day(cycle_date)
            original_scheduled = scheduled
            intervention_action = "NONE"
            suppress_native = False
            forced_hour = None

            if policy_fn is not None and m.arm == "treatment":
                decision = policy_fn(m, cycle, scheduled, mandate_history)
                if decision is not None:
                    intervention_action = decision.action
                    if decision.action == "RETIME" and decision.new_scheduled_date is not None:
                        scheduled = next_business_day(decision.new_scheduled_date)
                    forced_hour = decision.forced_hour
                    if decision.action == "HOLD":
                        # No attempt at all this cycle. Mandate stays in its
                        # current state; nothing is scheduled.
                        m.state = m.state  # explicit no-op, documented
                        continue
                    suppress_native = decision.suppress_further_native_retries

            attempt_number = 1
            cycle_succeeded = False
            current_date = scheduled
            # When Nirantar suppresses native retry, it owns the attempt
            # budget for this cycle instead: config.MAX_ATTEMPTS_PER_CYCLE
            # (2), not 1 -- "coordinated, not stacked" (docs/TAXONOMY.md
            # section 5) means Nirantar's own schedule replaces the native
            # one, it does not simply take away retry shots. Capping at 1
            # here was an implementation bug relative to that already-
            # frozen constant, found via experiment.py showing a policy
            # that suppresses retries down to a single attempt can make
            # recovery WORSE than doing nothing, not better.
            max_attempts = config.MAX_ATTEMPTS_PER_CYCLE if suppress_native else 3

            while attempt_number <= max_attempts and not cycle_succeeded:
                # forced_hour (from a RETIME/hour-shift intervention) only
                # applies to the FIRST attempt of the cycle -- it represents
                # choosing this cycle's presentment time up front, not
                # pinning every retry to the same hour regardless of
                # outcome.
                result = simulate_attempt(
                    rng,
                    amount_paise=m.amount_paise,
                    plan=m.plan,
                    rail=m.rail,
                    bank=m.bank,
                    psp_app=m.psp_app,
                    salary_day=m.salary_day,
                    scheduled=current_date,
                    attempt_number=attempt_number,
                    seed_key=seed_key,
                    hour=forced_hour if attempt_number == 1 else None,
                )
                a = Attempt(
                    attempt_id=f"{m.mandate_id}-C{cycle}-A{attempt_number}",
                    mandate_id=m.mandate_id,
                    arm=m.arm,
                    cycle_index=cycle,
                    scheduled_date=current_date.isoformat(),
                    original_scheduled_date=original_scheduled.isoformat(),
                    attempt_number=attempt_number,
                    amount_paise=m.amount_paise,
                    rail=m.rail,
                    bank=m.bank,
                    psp_app=m.psp_app,
                    hour=result.hour,
                    day_of_month=result.day_of_month,
                    days_since_salary_credit=result.days_since_salary_credit,
                    outcome=result.outcome,
                    true_cause=result.true_cause,
                    intervention_action=intervention_action if attempt_number == 1 else "NONE",
                    suppressed_native_retry=suppress_native,
                )
                attempts.append(a)
                mandate_history.append(a)

                if result.outcome == "SUCCESS":
                    cycle_succeeded = True
                    m.consecutive_failures = 0
                    m.state = "RESUMED" if m.state == "HALTED" else "ACTIVE"
                else:
                    m.consecutive_failures += 1
                    current_date = next_business_day(current_date + timedelta(days=1))
                    attempt_number += 1

            if not cycle_succeeded:
                m.state = "HALTED"

            monthly_base_cancel = 0.0165
            cancel_prob = monthly_base_cancel * (2.0 if m.consecutive_failures >= 2 else 1.0)
            if rng.random() < cancel_prob:
                m.state = "REVOKED"
                revoked = True

            next_month = cycle_date.month + 1
            next_year = cycle_date.year + (1 if next_month > 12 else 0)
            next_month = next_month if next_month <= 12 else 1
            cycle_date = date(next_year, next_month, min(m.salary_day, 28))

    return mandates, attempts, registration_failures
