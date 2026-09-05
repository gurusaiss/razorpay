# Decline Taxonomy and Mandate State Machine

This document is the contract every other module in Nirantar is built against.
It is frozen before any model or policy code is written, per the project's
own principle: the closed taxonomy is the product, not an implementation detail.

## 1. Mandate lifecycle (state machine)

```
CREATED --> REGISTERED --> ACTIVE --> PENDING --> HALTED --> REVOKED
                              ^           |
                              |           v
                              +------ RESUMED
```

| State | Meaning | Entered when |
|---|---|---|
| `CREATED` | Mandate initiated, not yet confirmed by the customer's bank/PSP | Registration flow started |
| `REGISTERED` | Customer completed AFA (or was exempt) and the mandate is confirmed | Bank/NPCI confirms |
| `ACTIVE` | At least one successful debit has occurred | First successful charge |
| `PENDING` | A scheduled debit failed but retry attempts remain | Any failed attempt, count < MAX_ATTEMPTS_PER_CYCLE |
| `HALTED` | Retry attempts exhausted for this cycle | Failed attempt count reaches MAX_ATTEMPTS_PER_CYCLE |
| `RESUMED` | A halted mandate collected successfully in a later cycle | Successful charge after HALTED |
| `REVOKED` | Customer explicitly cancelled, or mandate expired | Customer action or validity end |

Registration drop-off (CREATED never reaching REGISTERED) and cycle-level
failure (ACTIVE/RESUMED cycling to PENDING/HALTED) are tracked as separate
funnel stages — see docs/METRICS.md (added in Phase 7).

## 2. Decline cause codes (closed set)

Every failed attempt is assigned exactly one cause from this list. This is
the taxonomy the classifier is allowed to output — nothing else. Codes are
grounded in Razorpay's own published error taxonomy
(https://razorpay.com/docs/errors/payments/list/) plus mandate-specific
causes documented in Razorpay's subscription and e-mandate guides.

| Code | Description | Typical source | Actionable? |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | Customer's account balance too low at debit time | customer | Yes — retime |
| `MANDATE_PAUSED` | Mandate exists but is temporarily paused (bank-side or customer-side) | bank | Yes — notify |
| `MANDATE_REVOKED` | Customer has withdrawn the mandate | customer | No — stop billing |
| `TOKEN_REISSUED` | Underlying card/token reissued (fraud reissue, expiry, bank migration) | bank | Yes — request re-registration |
| `PSP_APP_UNAVAILABLE` | UPI PSP app (GPay/PhonePe/Paytm) technical failure at debit time | gateway | Yes — retime, short window |
| `BANK_TECHNICAL_ERROR` | Issuing/acquiring bank technical failure | bank | Yes — retime |
| `VELOCITY_LIMIT_EXCEEDED` | NPCI/bank transaction frequency or daily limit tripped | razorpay/bank | Yes — retime to next window |
| `FUNDS_BLOCKED_BY_MANDATE` | Another mandate already holds a block on the same funds | bank | Yes — retime, coordinate |
| `AFA_NOT_COMPLETED` | Additional Factor of Authentication step not completed by customer | customer | Yes — notify with action |
| `PRE_DEBIT_OPT_OUT` | Customer used the RBI-mandated pre-debit notification to skip this debit | customer | No — respect, do not retry same cycle |
| `GATEWAY_TECHNICAL_ERROR` | Generic PG-side failure not covered above | gateway | Yes — retime |
| `UNCLASSIFIED` | Attempt failed for a reason outside this taxonomy | unknown | No — escalate, never guess |

`UNCLASSIFIED` is the honest residue. A classifier reporting 0% unclassified
on real data is a red flag, not a result.

## 3. Permitted actions (closed set)

The policy engine may select exactly one of these per at-risk debit. This
list is intentionally short — it is the entire intervention surface.

| Action | What it does | Gate |
|---|---|---|
| `RETIME` | Move the scheduled debit to a later date/time within the mandate's permitted window | predicted failure probability >= THRESHOLD_RETIME and cause in {INSUFFICIENT_FUNDS, PSP_APP_UNAVAILABLE, BANK_TECHNICAL_ERROR, VELOCITY_LIMIT_EXCEEDED, FUNDS_BLOCKED_BY_MANDATE} |
| `SWITCH_RAIL` | Route the collection attempt through a different rail (e.g. card e-mandate instead of UPI Autopay) for merchants with more than one rail on file | cause in {PSP_APP_UNAVAILABLE, BANK_TECHNICAL_ERROR} and an alternate rail exists |
| `SPLIT_AMOUNT` | Offer a split/partial collection when the full amount is unlikely to clear | predicted probability of full-amount success below a lower threshold, AND partial collection is contractually permitted |
| `PERSONALISE_NOTIFICATION` | Rewrite the mandatory pre-debit notification with the specific cause and remedy, instead of the bare legal template | Always attempted when a risk is flagged, subject to the notification composer's field-immutability rule (see docs/ARCHITECTURE.md) |
| `HOLD` | Take no action this cycle | expected value of any other action is below ECONOMIC_FLOOR_PAISE, or cause is in {MANDATE_REVOKED, PRE_DEBIT_OPT_OUT} |

`RETRY` is deliberately absent from this list. Native gateway retry (T+1,
T+2, T+3) already exists; Nirantar's job is to decide whether to suppress it
(see `coordinate.py`, Phase 5), retime it, or replace it with a different
action — never to add a duplicate retry on top of it.

## 4. Cause -> action mapping (the order matters)

Classification tries causes in this fixed order; the first match wins. This
order is a documented decision, not an implementation detail buried in code.

1. `MANDATE_REVOKED` -> always `HOLD` (never act against an explicit revocation)
2. `PRE_DEBIT_OPT_OUT` -> always `HOLD` (respect the customer's stated choice this cycle)
3. `TOKEN_REISSUED` -> `PERSONALISE_NOTIFICATION` (ask for re-registration; retiming a dead token achieves nothing)
4. `AFA_NOT_COMPLETED` -> `PERSONALISE_NOTIFICATION` (the fix is a customer action, not a schedule change)
5. `INSUFFICIENT_FUNDS` -> `RETIME` if a better window is predicted, else `PERSONALISE_NOTIFICATION`
6. `VELOCITY_LIMIT_EXCEEDED` -> `RETIME` to next eligible window
7. `FUNDS_BLOCKED_BY_MANDATE` -> `RETIME`
8. `PSP_APP_UNAVAILABLE` / `BANK_TECHNICAL_ERROR` -> `RETIME`, shifted to an
   empirically safer presentment **hour** for that (bank, PSP app) pair —
   not `SWITCH_RAIL`. The original design here was `SWITCH_RAIL` if an
   alternate rail exists, else `RETIME`; Phase 7 (`docs/METRICS.md`) found
   this build cannot actually re-simulate a mandate on a different rail,
   so `SWITCH_RAIL` was a no-op that still cost a retry slot. This is the
   current frozen decision, not a stopgap: `docs/ARCHITECTURE.md`'s
   known-limitations section has the full reasoning, and it applies to
   both causes uniformly since `bank_psp_downtime` (the shared root cause)
   is keyed on hour for either rail family.
9. `GATEWAY_TECHNICAL_ERROR` -> `RETIME`, date-shifted to a better
   liquidity window (this cause is not modelled as hour-keyed, unlike 8)
10. `MANDATE_PAUSED` -> `PERSONALISE_NOTIFICATION`
11. `UNCLASSIFIED` -> `HOLD`, flagged for manual review

`SWITCH_RAIL` and `SPLIT_AMOUNT` remain in section 3's permitted-actions
list as the intended design surface, but neither is currently selected by
`policy.py` — see `docs/ARCHITECTURE.md`'s known-limitations section for
exactly why each is gated off, and what would need to change to activate it.

## 5. Constants (frozen defaults, overridable in config.py)

| Constant | Default | Rationale |
|---|---|---|
| `THRESHOLD_RETIME` | 0.55 | Calibrated probability above which retiming is worth the intervention cost |
| `MAX_ATTEMPTS_PER_CYCLE` | 2 (our engine) + native gateway retries, coordinated not stacked | Prevents duplicate-retry issuer flagging |
| `ECONOMIC_FLOOR_PAISE` | 15000 (Rs 150) | Below this, the cost of intervention approaches the value protected |
| `PRE_DEBIT_NOTICE_HOURS` | 24 | RBI Digital Payments E-Mandate Framework, 2026 (21 Apr 2026) minimum |

These numbers are starting points for the synthetic build, calibrated
against held-out data in Phase 7 (docs/METRICS.md) — not asserted as
correct without evidence.
