# Metrics

This document is the single place every recovery/lift number quoted in the
pitch, README, or demo must trace back to. It is regenerated, not hand-edited
— `python -m nirantar.baseline --data <dir>` produces the numbers below, and
`experiment.py` (Phase 7) will append the with-policy comparison in the same
format once the policy engine exists.

## Why a baseline section exists before any model or policy code

The bar this track sets is explicit: *"Show measured money recovered across
a batch, with compliant escalation, stopping rules, and an audit trail."*
"Measured" means measured against something. Every recovery-rate claim in
this space (including the ~55% headline numbers vendors publish, versus the
25–35% independent audits actually find) becomes meaningless without a
stated reference point and a stated population. This file fixes that
reference point first, before a single line of predictive or policy code is
written, so that any later lift number is a comparison against a number
that was never allowed to be tuned to make the comparison look good.

## Baseline run (native retry only, no Nirantar intervention)

Dataset: `data/seed7_v1/`, generated via
`python -m nirantar.generate --seed 7 --mandates 4000 --months 12 --start-date 2025-09-01`
(deterministic — reruns with the same seed reproduce this dataset
byte-for-byte, verified in `tests/test_rng_isolation.py`'s sibling
determinism check).

| Metric | Value |
|---|---|
| Mandates created | 4,000 |
| Registered (post-dropoff) | 2,764 (dropoff 30.9%) |
| Revoked during 12-month observation | 499 (18.1% of registered) |
| Billing cycles attempted | 30,366 |
| Billing cycles recovered (native T+1/T+2/T+3 retry) | 30,026 (98.9%) |
| Rupees at risk | Rs 3,31,64,234.00 |
| Rupees recovered | Rs 3,27,87,324.00 |
| Rupees lost (unrecovered) | Rs 3,76,910.00 |
| Attempts total | 36,095 |
| Attempts failed | 16.8% |
| Avg attempts per recovered cycle | 1.168 |

Decline cause breakdown (ground truth from the simulator — **not**
observable in a real production system, which is exactly why
`predict.py`/`classify.py` exist rather than reading this column directly):

| Cause | Count | Share of failures |
|---|---|---|
| PSP_APP_UNAVAILABLE | 3,004 | 49.5% |
| INSUFFICIENT_FUNDS | 2,253 | 37.1% |
| AFA_NOT_COMPLETED | 508 | 8.4% |
| VELOCITY_LIMIT_EXCEEDED | 151 | 2.5% |
| FUNDS_BLOCKED_BY_MANDATE | 89 | 1.5% |
| GATEWAY_TECHNICAL_ERROR | 64 | 1.1% |

### Reading these numbers honestly

98.9% of cycles already recover via native retry alone — this is expected
and is not a flaw in the simulation. Recurring-debit failure in India is a
long-tail problem: the vast majority of debits succeed on T+1 through T+3
without any intervention. Nirantar's entire value case is in the remaining
~1.1% of cycles (376 out of 30,366 in this run) that native retry alone
never recovers, plus reducing the *number of attempts* needed for cycles
that do recover late (T+2/T+3 instead of T+1) — because every failed
attempt has a cost: issuer-side authorization-rate degradation risk, retry
infrastructure load, and customer friction from repeated debit notifications.

## Phase 7 experiment: measured lift (`experiment.py`)

Same population, same seed (7), same 4,000 mandates as the baseline above —
generated twice, once with `policy_fn=None` and once with the real trained
policy engine (`predict.py` + `classify.py` + `artifacts.py` + `policy.py`)
active on the treatment arm only. This is a same-population, same-seed
comparison, never a before/after on different data.

**Integrity gate (checked before trusting anything below):** the 2,000
control-arm mandates' attempt records are asserted byte-identical between
the two runs. They are — `experiment.py` exits with a hard failure if they
are not, so this number is never silently skipped.

| Metric (treatment arm, 2,000 mandates, 15,248 cycles) | Baseline | With policy | Lift |
|---|---|---|---|
| Cycle recovery rate | 98.8195% | 99.3619% | **+0.542 pp** |
| Avg attempts / recovered cycle | 1.168 | 1.111 | **-0.057** (fewer attempts) |
| Rupees recovered | Rs 1,64,37,432 | Rs 1,65,18,345 | **+Rs 80,913** |
| Rupees lost (unrecovered) | Rs 1,90,670 | Rs 1,06,353 | **-44.2%** |

### Why the honest answer went through two negative results first

The first working version of the policy engine measured **-5.2 percentage
points** — the policy made recovery *worse*. Two real bugs were found and
fixed by trusting this measurement over the intended design, not by tuning
the experiment until the number looked right:

1. `simulate.py` capped Nirantar's own attempt budget at 1 when suppressing
   native retry, instead of `config.MAX_ATTEMPTS_PER_CYCLE` (2, frozen in
   Phase 1). Every intervention was trading away retry shots, not
   coordinating them. Fixing this alone took the lift to -1.26pp.
2. `SWITCH_RAIL` was being selected for the dominant failure cause
   (`PSP_APP_UNAVAILABLE`, ~50% of failures) and suppressing native retry
   in its favour — but this synthetic build cannot actually re-simulate a
   mandate on a different rail (`Mandate.rail` is fixed at creation), so
   the "switch" was a no-op that still cost a retry slot. Worse,
   `environment.bank_psp_downtime()` is keyed on **hour**, not date, so a
   date-only `RETIME` against this cause is provably useless — confirmed
   by measuring exactly 0.000pp lift once `SWITCH_RAIL` was honestly
   retired and replaced with a plain date-based `RETIME`.

The fix that produced the real +0.542pp above was extending `RETIME` to
also cover time-of-day (`docs/TAXONOMY.md`'s own definition of RETIME is
"a later date/**time**"), using an empirical best-presentment-hour table
per (bank, PSP app) built only from the training split
(`artifacts.compute_best_hour_by_bank_psp`). This is the real,
implementable lever against a bank/PSP outage window; a genuine
`SWITCH_RAIL` would need `environment.py` extended to model an actual
cross-rail retry, which is documented as future work rather than faked.

This progression — measure, find the real result is negative or zero,
find the actual mechanical reason, fix the reason, remeasure — is the
audit trail this track's own bar asks for, and is kept here rather than
edited out.
