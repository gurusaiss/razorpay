"""
Frozen constants for Nirantar.

Every threshold here is referenced in docs/TAXONOMY.md with its rationale.
Nothing in the policy or coordination engine should hardcode a number that
isn't defined here — that is the point of this file.
"""

from __future__ import annotations

# --- Policy gates (docs/TAXONOMY.md section 5) ---

# Calibrated failure probability above which RETIME is worth the intervention.
THRESHOLD_RETIME: float = 0.55

# Calibrated probability of full-amount success below which SPLIT_AMOUNT
# is considered (only relevant where partial collection is contractually
# permitted, which the synthetic build treats as a per-mandate flag).
THRESHOLD_SPLIT: float = 0.35

# Our engine's own maximum additional attempts per mandate per cycle, on
# top of / instead of whatever the native gateway retry schedule already
# does. This is NOT the total attempt count — coordinate.py suppresses
# duplication against the native schedule.
MAX_ATTEMPTS_PER_CYCLE: int = 2

# Below this rupee value (in paise), the cost of intervening approaches
# the value protected, so the policy engine holds rather than acts.
ECONOMIC_FLOOR_PAISE: int = 15_000  # Rs 150

# Added in Phase 5 (policy.py) once "always attempted when a risk is
# flagged" (docs/TAXONOMY.md section 3, PERSONALISE_NOTIFICATION's gate)
# needed a concrete number: the minimum calibrated failure probability
# below which a cycle isn't flagged as at-risk at all, so no action --
# not even the notification rewrite -- is worth taking. Distinct from and
# lower than THRESHOLD_RETIME (0.55), which gates the stronger RETIME
# action specifically.
THRESHOLD_NOTIFY_FLOOR: float = 0.20

# RBI Digital Payments E-Mandate Framework, 2026 (circular
# RBI/CO.DPSS.POLC.No.S56/02.14.003/2026-27, dated 21 April 2026):
# minimum notice before a recurring debit.
PRE_DEBIT_NOTICE_HOURS: int = 24

# AFA (Additional Factor of Authentication) exemption thresholds under the
# same framework. Debits below these do not require an explicit AFA step;
# above them, AFA_NOT_COMPLETED becomes a live failure cause.
AFA_EXEMPT_LIMIT_GENERAL_PAISE: int = 15_00_00   # Rs 15,000
AFA_EXEMPT_LIMIT_SPECIAL_PAISE: int = 1_00_00_00  # Rs 1,00,000 (insurance, MF, credit card bills)

# --- Closed taxonomies (docs/TAXONOMY.md sections 2-4) ---

DECLINE_CAUSES: list[str] = [
    "INSUFFICIENT_FUNDS",
    "MANDATE_PAUSED",
    "MANDATE_REVOKED",
    "TOKEN_REISSUED",
    "PSP_APP_UNAVAILABLE",
    "BANK_TECHNICAL_ERROR",
    "VELOCITY_LIMIT_EXCEEDED",
    "FUNDS_BLOCKED_BY_MANDATE",
    "AFA_NOT_COMPLETED",
    "PRE_DEBIT_OPT_OUT",
    "GATEWAY_TECHNICAL_ERROR",
    "UNCLASSIFIED",
]

# Causes that must never trigger any action other than HOLD, regardless of
# predicted probability. Acting against these is either futile (the
# instrument/mandate is gone) or a violation of stated customer intent.
NON_ACTIONABLE_CAUSES: set[str] = {"MANDATE_REVOKED", "PRE_DEBIT_OPT_OUT"}

PERMITTED_ACTIONS: list[str] = [
    "RETIME",
    "SWITCH_RAIL",
    "SPLIT_AMOUNT",
    "PERSONALISE_NOTIFICATION",
    "HOLD",
]

# The fixed order in which classify.py evaluates causes. Documented in
# TAXONOMY.md section 4 — do not reorder without updating that document.
CAUSE_EVAL_ORDER: list[str] = [
    "MANDATE_REVOKED",
    "PRE_DEBIT_OPT_OUT",
    "TOKEN_REISSUED",
    "AFA_NOT_COMPLETED",
    "INSUFFICIENT_FUNDS",
    "VELOCITY_LIMIT_EXCEEDED",
    "FUNDS_BLOCKED_BY_MANDATE",
    "PSP_APP_UNAVAILABLE",
    "BANK_TECHNICAL_ERROR",
    "GATEWAY_TECHNICAL_ERROR",
    "MANDATE_PAUSED",
]

MANDATE_STATES: list[str] = [
    "CREATED",
    "REGISTERED",
    "ACTIVE",
    "PENDING",
    "HALTED",
    "RESUMED",
    "REVOKED",
]

RAILS: list[str] = ["UPI_AUTOPAY", "CARD_EMANDATE", "NETBANKING_EMANDATE"]

RULE_VERSION: str = "2026.09.taxonomy-v1"
