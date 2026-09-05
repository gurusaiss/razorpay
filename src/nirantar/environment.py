"""
The simulated world's "physics": how a single debit attempt succeeds or
fails, and why. This module is the ground-truth mechanism and must NEVER
be imported by predict.py, policy.py, or classify.py -- those modules only
see what simulate.py exposes as observable features and outcomes.

Kept separate from simulate.py (the cycle/mandate loop) and population.py
(mandate creation) so each has one job.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

FIXED_HOLIDAYS_MMDD = {
    (1, 26),   # Republic Day
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti
    (5, 1),    # representative spring holiday
    (11, 1),   # representative autumn regional holiday
}


def is_holiday(d: date) -> bool:
    if d.weekday() >= 5:
        return True
    return (d.month, d.day) in FIXED_HOLIDAYS_MMDD


def next_business_day(d: date) -> date:
    while is_holiday(d):
        d += timedelta(days=1)
    return d


@dataclass(frozen=True)
class PlanProfile:
    name: str
    amount_paise: int
    rail_weights: dict
    partial_allowed: bool


PLAN_PROFILES: list[PlanProfile] = [
    PlanProfile("OTT_BASIC", 149_00, {"UPI_AUTOPAY": 0.75, "CARD_EMANDATE": 0.20, "NETBANKING_EMANDATE": 0.05}, partial_allowed=False),
    PlanProfile("OTT_PREMIUM", 499_00, {"UPI_AUTOPAY": 0.65, "CARD_EMANDATE": 0.30, "NETBANKING_EMANDATE": 0.05}, partial_allowed=False),
    PlanProfile("INSURANCE_PREMIUM", 2_499_00, {"UPI_AUTOPAY": 0.35, "CARD_EMANDATE": 0.45, "NETBANKING_EMANDATE": 0.20}, partial_allowed=False),
    PlanProfile("EDTECH_EMI", 1_999_00, {"UPI_AUTOPAY": 0.40, "CARD_EMANDATE": 0.55, "NETBANKING_EMANDATE": 0.05}, partial_allowed=True),
    PlanProfile("SAAS_SEAT", 999_00, {"UPI_AUTOPAY": 0.20, "CARD_EMANDATE": 0.75, "NETBANKING_EMANDATE": 0.05}, partial_allowed=False),
    PlanProfile("D2C_REFILL", 349_00, {"UPI_AUTOPAY": 0.80, "CARD_EMANDATE": 0.15, "NETBANKING_EMANDATE": 0.05}, partial_allowed=True),
]

BANKS = ["HDFC", "ICICI", "SBI", "AXIS", "KOTAK", "PNB", "BOB", "YES"]
PSP_APPS = ["GPAY", "PHONEPE", "PAYTM", "BHIM"]


def liquidity_score(day_of_month: int, salary_day: int) -> float:
    """0..1 'how likely is there enough balance', peaking after salary_day."""
    days_since = (day_of_month - salary_day) % 30
    if days_since <= 3:
        return 0.95
    if days_since <= 10:
        return 0.80
    if days_since <= 20:
        return 0.55
    return 0.30


def bank_psp_downtime(bank: str, psp_app: str, hour: int, seed_key: str) -> bool:
    """Deterministic-per-key 2-hour daily outage window for a (bank, psp) pair."""
    key_hash = sum(ord(c) for c in f"{bank}{psp_app}{seed_key}")
    outage_start = key_hash % 22
    return outage_start <= hour < outage_start + 2


@dataclass
class AttemptResult:
    outcome: str       # "SUCCESS" or "FAILED"
    true_cause: str    # a code from config.DECLINE_CAUSES, or "NONE"
    hour: int
    day_of_month: int
    days_since_salary_credit: int


def simulate_attempt(
    rng: random.Random,
    *,
    amount_paise: int,
    plan: str,
    rail: str,
    bank: str,
    psp_app: str,
    salary_day: int,
    scheduled: date,
    attempt_number: int,
    seed_key: str,
    hour: int | None = None,
) -> AttemptResult:
    """
    The one function that decides whether a debit attempt succeeds. Called
    identically whether the caller is the baseline (native retry) path or
    the treatment (Nirantar-intervened) path -- the only difference between
    arms is WHICH (scheduled, attempt_number, hour) gets passed in.

    `hour` defaults to the native schedule (10:00 for the first attempt,
    a random retry slot afterwards) when not given -- this is a REAL,
    legitimate lever, not a backdoor: UPI Autopay/e-NACH presentment time
    within a day is something a merchant's payment stack actually
    configures, so a policy engine choosing to present at a specific,
    empirically safer hour is the same kind of decision as choosing a
    date, not a peek at this function's own internals.
    """
    if hour is None:
        hour = 10 if attempt_number == 1 else rng.choice([9, 10, 11, 14, 22, 23])
    liq = liquidity_score(scheduled.day, salary_day)

    base_fail_prob = (1 - liq) * 0.55 + 0.04

    downtime = rail == "UPI_AUTOPAY" and bank_psp_downtime(bank, psp_app, hour, seed_key)
    if downtime:
        base_fail_prob = max(base_fail_prob, 0.85)

    afa_risk = 0.0
    if amount_paise > 15_00_00 and plan != "INSURANCE_PREMIUM":
        afa_risk = 0.12

    fail_prob = min(0.97, base_fail_prob + afa_risk)
    days_since = (scheduled.day - salary_day) % 30

    if rng.random() > fail_prob:
        return AttemptResult("SUCCESS", "NONE", hour, scheduled.day, days_since)

    if downtime:
        cause = "PSP_APP_UNAVAILABLE" if rail == "UPI_AUTOPAY" else "BANK_TECHNICAL_ERROR"
    elif afa_risk > 0 and rng.random() < 0.4:
        cause = "AFA_NOT_COMPLETED"
    elif liq < 0.5:
        cause = "INSUFFICIENT_FUNDS"
    elif rng.random() < 0.06:
        cause = "VELOCITY_LIMIT_EXCEEDED"
    elif rng.random() < 0.04:
        cause = "FUNDS_BLOCKED_BY_MANDATE"
    elif rng.random() < 0.03:
        cause = "GATEWAY_TECHNICAL_ERROR"
    else:
        cause = "INSUFFICIENT_FUNDS"

    return AttemptResult("FAILED", cause, hour, scheduled.day, days_since)


def best_liquidity_day_near(salary_day: int, around_day: int, month_length: int = 28) -> int:
    """
    Oracle-free heuristic a real policy would use: the day in [1, month_length]
    with the highest liquidity_score, preferring a day close to (but not
    before) around_day, since RETIME must move a debit later within the
    mandate window, never earlier than originally scheduled.
    """
    candidates = [d for d in range(around_day, month_length + 1)]
    if not candidates:
        candidates = [month_length]
    return max(candidates, key=lambda d: liquidity_score(d, salary_day))
