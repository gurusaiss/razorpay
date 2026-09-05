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
| Revoked during 12-month observation | 506 (18.3% of registered) |
| Billing cycles attempted | 30,301 |
| Billing cycles recovered (native T+1/T+2/T+3 retry) | 29,735 (98.13%) |
| Rupees at risk | Rs 3,30,39,549.00 |
| Rupees recovered | Rs 3,23,21,865.00 |
| Rupees lost (unrecovered) | Rs 7,17,684.00 |
| Attempts total | 38,100 |
| Attempts failed | 22.0% |
| Avg attempts per recovered cycle | 1.224 |

Decline cause breakdown (ground truth from the simulator — **not**
observable in a real production system, which is exactly why
`predict.py`/`classify.py` exist rather than reading this column directly):

| Cause | Count | Share of failures |
|---|---|---|
| PSP_APP_UNAVAILABLE | 3,004 | 35.9% |
| BANK_TECHNICAL_ERROR | 2,352 | 28.1% |
| INSUFFICIENT_FUNDS | 2,223 | 26.6% |
| AFA_NOT_COMPLETED | 490 | 5.9% |
| VELOCITY_LIMIT_EXCEEDED | 151 | 1.8% |
| FUNDS_BLOCKED_BY_MANDATE | 83 | 1.0% |
| GATEWAY_TECHNICAL_ERROR | 62 | 0.7% |

`BANK_TECHNICAL_ERROR` appears in this table for the first time as of this
revision — it was dead code in `environment.py` until a repo audit found
it (see the third bug entry below). Its presence here, and its 100%
precision/recall in `classify.py` immediately below, are direct evidence
the fix worked, not just a code-review claim.

### Reading these numbers honestly

98.1% of cycles already recover via native retry alone — this is expected
and is not a flaw in the simulation. Recurring-debit failure in India is a
long-tail problem: the vast majority of debits succeed on T+1 through T+3
without any intervention. Nirantar's entire value case is in the remaining
~1.9% of cycles (566 out of 30,301 in this run) that native retry alone
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

| Metric (treatment arm, 2,000 mandates, ~15,193 cycles) | Baseline | With policy | Lift |
|---|---|---|---|
| Cycle recovery rate | 98.0057% | 98.9930% | **+0.987 pp** |
| Avg attempts / recovered cycle | 1.229 | 1.114 | **-0.115** (fewer attempts) |
| Rupees recovered | Rs 1,61,34,460 | Rs 1,64,28,909 | **+Rs 2,94,449** |
| Rupees lost (unrecovered, each arm's own ceiling) | Rs 3,84,197 | Rs 1,89,197 | **-50.8%** |

(Treatment-arm cycle count differs by exactly one between the baseline and
with-policy runs — 15,193 vs 15,194. An hour-shifting `RETIME` can move a
first attempt across a calendar-month boundary for a mandate scheduled
right at the edge of the 12-month window, which changes how many distinct
cycles that one mandate contributes within the observation period. This is
a real, explainable side effect of the intervention, not a data integrity
problem — the control arm above is still proven byte-identical, and this
is why the "rupees lost" comparison uses each arm's own total at-risk
figure as its ceiling rather than assuming the two are identical.)

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

### A third bug, found after the numbers above already looked good

The three previous bugs were all caught because they made the *measured
lift* wrong — a strong forcing function. This one didn't: it was found by
auditing `SPLIT_AMOUNT` (never selected by `policy.py`, see
`docs/ARCHITECTURE.md`'s known-limitations section) for the same class of
issue, which led to checking every other cause in `config.DECLINE_CAUSES`
for whether it was actually reachable. `BANK_TECHNICAL_ERROR` was not:
`environment.py`'s `downtime` flag — the thing that ever produces this
cause — was gated to `rail == "UPI_AUTOPAY"` only, so the `else
"BANK_TECHNICAL_ERROR"` branch immediately below it could never execute
for any rail. An entire taxonomy cause was dead code, silently, with
nothing in the previous run's numbers pointing at it.

The fix: `downtime` is no longer gated to UPI Autopay. `bank_psp_downtime`
is keyed on `(bank, psp_app, seed_key)`, and `psp_app` is already `""` for
non-UPI rails, so the same function now models a bank-side outage window
for card e-mandate and netbanking e-mandate too — a real, plausible cause
(the bank's own e-NACH/card-emandate processing has an outage), not a
hack to force a number up.

Effect of the fix, same seed, full regeneration: `BANK_TECHNICAL_ERROR`
now accounts for 28.1% of all decline causes (2,352 of 8,365 failures) and
classifies at 100% precision/recall — immediately learnable once it
existed in the data at all. The headline lift **improved** as a direct
result — from +0.542pp to +0.987pp — because `policy.py` already had a
correct `RETIME`/hour-shift branch for this exact cause (grouped with
`PSP_APP_UNAVAILABLE` under the same `hour_outage` gate); it simply had
nothing to act on before. This is stated as what it is: a bug fix that
happened to help the number, found by auditing for silent gaps rather
than by the number itself complaining — the opposite failure mode from
the first two bugs, and worth naming so this file doesn't read as if
every bug conveniently announces itself via a bad measurement.
