"""
Phase 5c: the policy engine. Ties together predict.py (fail probability),
classify.py (most likely cause conditional on failure), artifacts.py
(empirical reference tables), heuristics.py (retime suggestion), and
coordinate.py (native-retry suppression) into one decision function
matching simulate.py's PolicyFn signature:

    policy_fn(mandate, cycle_index, scheduled_date, history) -> Intervention | None

Decision order is exactly docs/TAXONOMY.md section 4, cause -> action,
gated first by the economic floor and the notify floor (section 3 /
config.THRESHOLD_NOTIFY_FLOOR). The LLM/model layer only SCORES (predicts
probability, predicts cause) -- every branch below is deterministic
policy code, never a model output used directly as a decision. This is
the "model scores, policy decides" principle stated throughout the
project's own design docs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import joblib
import pandas as pd

from nirantar import artifacts as artifacts_mod
from nirantar import config
from nirantar import coordinate
from nirantar import heuristics
from nirantar.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES
from nirantar.population import Mandate
from nirantar.simulate import Attempt, Intervention


@dataclass
class PolicyModels:
    predict_model: object
    classify_model: object
    artifacts: dict

    @classmethod
    def load(cls, predict_path: str, classify_path: str, artifacts_path: str) -> "PolicyModels":
        return cls(
            predict_model=joblib.load(predict_path),
            classify_model=joblib.load(classify_path),
            artifacts=artifacts_mod.load(artifacts_path),
        )


def _mandate_history_features(mandate: Mandate, history: list[Attempt]) -> tuple[int, int, float]:
    """
    n_prior_cycles, n_prior_failed_cycles, prior_failure_rate -- computed
    from this mandate's own history ONLY (never other mandates'), matching
    exactly how features.build_examples computes the same three numbers
    from historical CSV data, so the model sees the same feature
    distribution at scoring time that it was trained on.
    """
    cycles_seen: dict[int, bool] = {}
    for a in history:
        succeeded = cycles_seen.get(a.cycle_index, False) or a.outcome == "SUCCESS"
        cycles_seen[a.cycle_index] = succeeded
    n_prior_cycles = len(cycles_seen)
    n_prior_failed = sum(1 for ok in cycles_seen.values() if not ok)
    prior_failure_rate = (n_prior_failed / n_prior_cycles) if n_prior_cycles > 0 else 0.20
    return n_prior_cycles, n_prior_failed, prior_failure_rate


def _feature_row(mandate: Mandate, scheduled_date: date, history: list[Attempt], models: PolicyModels) -> pd.DataFrame:
    n_prior_cycles, n_prior_failed, prior_failure_rate = _mandate_history_features(mandate, history)
    days_since = heuristics.days_since_salary(scheduled_date.day, mandate.salary_day)
    row = {
        "plan": mandate.plan,
        "rail": mandate.rail,
        "bank": mandate.bank,
        "psp_app": mandate.psp_app,
        "amount_paise": mandate.amount_paise,
        "day_of_month": scheduled_date.day,
        "days_since_salary_credit": days_since,
        "n_prior_cycles": n_prior_cycles,
        "n_prior_failed_cycles": n_prior_failed,
        "prior_failure_rate": prior_failure_rate,
        "bank_psp_historical_fail_rate": artifacts_mod.lookup_bank_psp_rate(
            models.artifacts, mandate.bank, mandate.psp_app
        ),
    }
    return pd.DataFrame([{k: row[k] for k in CATEGORICAL_FEATURES + NUMERIC_FEATURES}])


def decide(mandate: Mandate, cycle_index: int, scheduled_date: date,
           history: list[Attempt], models: PolicyModels) -> Intervention | None:
    # Economic floor first (docs/TAXONOMY.md section 3) -- cheapest check,
    # overrides everything else regardless of cause. IMPORTANT: this means
    # "don't spend an intervention on it", NOT "don't bill it" -- returning
    # None here is exactly what a plain no-risk-flagged cycle gets, so
    # native T+1/T+2/T+3 retry proceeds completely untouched. Returning an
    # actual HOLD Intervention here was an earlier bug: simulate.py treats
    # HOLD as "skip this cycle's attempt entirely" (correct ONLY for a
    # true compliance hold -- a revoked mandate that must not be billed at
    # all), which silently zeroed out every cycle for any mandate priced
    # under the floor (e.g. OTT_BASIC at Rs 149, just under the Rs 150
    # floor) instead of simply declining to intervene on it.
    if mandate.amount_paise < config.ECONOMIC_FLOOR_PAISE:
        return None

    X = _feature_row(mandate, scheduled_date, history, models)
    fail_prob = float(models.predict_model.predict_proba(X)[0, 1])

    if fail_prob < config.THRESHOLD_NOTIFY_FLOOR:
        return None  # not flagged as at-risk at all -- native retry runs untouched

    cause = str(models.classify_model.predict(X)[0])

    action, retime_kind = _cause_to_action(mandate, cause, fail_prob, scheduled_date, models)
    if action is None:
        return None

    if action == "HOLD":
        return Intervention(action="HOLD")

    suppress = coordinate.should_suppress_native_retry(action)

    if action == "RETIME" and retime_kind == "date_liquidity":
        # INSUFFICIENT_FUNDS / VELOCITY_LIMIT_EXCEEDED / FUNDS_BLOCKED_BY_MANDATE:
        # date genuinely matters (environment.liquidity_score is keyed on
        # day-of-month), so a later date within a heuristically better
        # liquidity window is a real lever here.
        new_date = heuristics.suggest_retime_date(scheduled_date, mandate.salary_day)
        if new_date is None:
            # No better window found -- retiming would be pointless motion.
            return Intervention(action="PERSONALISE_NOTIFICATION", suppress_further_native_retries=False)
        return Intervention(action="RETIME", new_scheduled_date=new_date, suppress_further_native_retries=suppress)

    if action == "RETIME" and retime_kind == "hour_outage":
        # PSP_APP_UNAVAILABLE / BANK_TECHNICAL_ERROR: the underlying cause
        # here is a per-(bank, psp_app) outage window keyed on HOUR, not
        # date (confirmed by experiment.py: a date-only retime measured
        # zero lift against this cause, because it still fired at the same
        # doomed hour). SWITCH_RAIL was considered but this build cannot
        # actually re-simulate a mandate on a different rail (Mandate.rail
        # is fixed at creation) -- shipping a "SWITCH_RAIL" that quietly
        # behaved like a no-op retime would be a fake result. Choosing a
        # different, empirically safer PRESENTMENT HOUR is the real,
        # implementable lever, and TAXONOMY.md's own definition of RETIME
        # is "move the scheduled debit to a later date/TIME" -- this is
        # still a RETIME, just on the time-of-day axis rather than date.
        best_hour = artifacts_mod.lookup_best_hour(models.artifacts, mandate.bank, mandate.psp_app)
        if best_hour is None or best_hour == 10:
            # No recommendation, or the empirically-best hour IS the
            # native default -- nothing to gain by forcing anything.
            return Intervention(action="PERSONALISE_NOTIFICATION", suppress_further_native_retries=False)
        return Intervention(action="RETIME", forced_hour=best_hour, suppress_further_native_retries=suppress)

    if action == "SPLIT_AMOUNT":
        return Intervention(action="SPLIT_AMOUNT", suppress_further_native_retries=suppress)

    # PERSONALISE_NOTIFICATION
    return Intervention(action="PERSONALISE_NOTIFICATION", suppress_further_native_retries=suppress)


def _cause_to_action(mandate: Mandate, cause: str, fail_prob: float,
                      scheduled_date: date, models: PolicyModels) -> tuple[str | None, str | None]:
    """
    docs/TAXONOMY.md section 4, in the documented fixed order. Returns
    (action, retime_kind) -- retime_kind disambiguates HOW to retime
    (date-driven liquidity window vs hour-driven outage window) for the
    two causes where RETIME applies for different physical reasons; it is
    None for every other action.
    """
    if cause in config.NON_ACTIONABLE_CAUSES:  # MANDATE_REVOKED, PRE_DEBIT_OPT_OUT
        return "HOLD", None

    if cause == "TOKEN_REISSUED":
        return "PERSONALISE_NOTIFICATION", None

    if cause == "AFA_NOT_COMPLETED":
        return "PERSONALISE_NOTIFICATION", None

    if cause == "INSUFFICIENT_FUNDS":
        if fail_prob >= config.THRESHOLD_RETIME:
            return "RETIME", "date_liquidity"
        return "PERSONALISE_NOTIFICATION", None

    if cause == "VELOCITY_LIMIT_EXCEEDED":
        if fail_prob >= config.THRESHOLD_RETIME:
            return "RETIME", "date_liquidity"
        return "PERSONALISE_NOTIFICATION", None

    if cause == "FUNDS_BLOCKED_BY_MANDATE":
        if fail_prob >= config.THRESHOLD_RETIME:
            return "RETIME", "date_liquidity"
        return "PERSONALISE_NOTIFICATION", None

    if cause in ("PSP_APP_UNAVAILABLE", "BANK_TECHNICAL_ERROR"):
        return "RETIME", "hour_outage"

    if cause == "GATEWAY_TECHNICAL_ERROR":
        return "RETIME", "date_liquidity"

    if cause == "MANDATE_PAUSED":
        return "PERSONALISE_NOTIFICATION", None

    # UNCLASSIFIED, or anything the classifier outputs that isn't in the
    # taxonomy at all -- never guess, escalate via HOLD + notification.
    return "HOLD", None

    # NOTE: SPLIT_AMOUNT is never returned by this function, on purpose.
    # config.THRESHOLD_SPLIT exists and coordinate.py/notify.py are wired
    # for it, but simulate.py/environment.py have no partial-amount
    # attempt mechanism to execute it against -- selecting it here would
    # silently recreate the SWITCH_RAIL no-op bug documented in
    # docs/METRICS.md. See docs/ARCHITECTURE.md's known-limitations
    # section before wiring a real gate in here.


def make_policy_fn(models: PolicyModels):
    """Returns a closure matching simulate.PolicyFn's exact signature."""
    def policy_fn(mandate: Mandate, cycle_index: int, scheduled_date: date,
                  history: list[Attempt]) -> Intervention | None:
        return decide(mandate, cycle_index, scheduled_date, history, models)
    return policy_fn
