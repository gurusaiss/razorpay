"""
Policy-side domain heuristics.

IMPORTANT: this module must NEVER import from environment.py. environment.py
is the simulator's ground-truth mechanism (the exact liquidity/outage
formulas that decide real outcomes); policy.py, classify.py, and predict.py
are only allowed to see what a real production system could plausibly
know. That boundary would be worthless if this "policy heuristics" module
just re-imported the ground truth under a different name.

So the logic here is written independently, from the same general domain
knowledge a real payments ops team has (salaries cluster around the 1st/7th,
balances are typically healthiest in the days right after), WITHOUT sharing
code or constants with environment.liquidity_score(). It is expected to be
a good heuristic, not a perfect oracle -- if it happened to be exactly
right every time, that would itself be evidence of an accidental leak.
"""

from __future__ import annotations

from datetime import date, timedelta

# Independently-chosen "typically high liquidity" window: a few days after
# salary credit, before month-end drawdown sets in. Deliberately not the
# same bucketing as environment.liquidity_score.
GOOD_WINDOW_MIN_DAYS_AFTER_SALARY = 1
GOOD_WINDOW_MAX_DAYS_AFTER_SALARY = 9


def days_since_salary(day_of_month: int, salary_day: int) -> int:
    return (day_of_month - salary_day) % 30


def is_in_good_window(day_of_month: int, salary_day: int) -> bool:
    d = days_since_salary(day_of_month, salary_day)
    return GOOD_WINDOW_MIN_DAYS_AFTER_SALARY <= d <= GOOD_WINDOW_MAX_DAYS_AFTER_SALARY


def suggest_retime_date(current: date, salary_day: int, max_push_days: int = 10) -> date | None:
    """
    Suggest a later date (RETIME must only move a debit LATER, never
    earlier -- the customer has already been sent the pre-debit notice for
    the original date) that falls in the heuristic "good liquidity" window.
    Returns None if no better date is found within max_push_days, in which
    case the caller should fall back to PERSONALISE_NOTIFICATION instead of
    RETIME (there's no point retiming into an equally bad window).
    """
    if is_in_good_window(current.day, salary_day):
        # Already scheduled in a decent window -- pushing further out only
        # adds delay without improving the odds. Not this heuristic's job
        # to recommend a retime here.
        return None

    for push in range(1, max_push_days + 1):
        candidate = current + timedelta(days=push)
        if is_in_good_window(candidate.day, salary_day):
            return candidate
    return None
