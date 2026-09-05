"""
Feature engineering shared between training (predict.py) and scoring
(policy.py, once it exists). Every feature here must be honestly
observable BEFORE the debit attempt it is predicting -- this module is the
one place that boundary is enforced, so it's reviewed once rather than
re-derived per caller.

Explicitly excluded, and why:
  - true_cause      -- ground truth, never observable pre-debit in production.
  - outcome          -- that's the label, not a feature.
  - hour             -- attempt_number==1 always fires at hour 10 in this
                         simulator (see environment.simulate_attempt), so it
                         carries zero information for the first-attempt
                         prediction task this module serves. Retry hours
                         (attempt_number > 1) are chosen AFTER the first
                         attempt fails, i.e. after the prediction already
                         had to be made -- so hour is never a legitimate
                         pre-debit feature for ANY attempt number here.
  - bank_psp_downtime() itself -- that's the ground-truth mechanism function
    from environment.py. What a real predictor gets instead is an empirical
    proxy: the historical failure rate for that (bank, psp_app) pair,
    computed ONLY from the training split, never the split being scored.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict


CATEGORICAL_FEATURES = ["plan", "rail", "bank", "psp_app"]
NUMERIC_FEATURES = [
    "amount_paise",
    "day_of_month",
    "days_since_salary_credit",
    "n_prior_cycles",
    "n_prior_failed_cycles",
    "prior_failure_rate",
    "bank_psp_historical_fail_rate",
]


def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


BANK_PSP_FALLBACK_RATE = 0.15  # global prior for an (bank, psp_app) pair never seen in training


def compute_bank_psp_fail_rates(attempts: list[dict]) -> dict[tuple, float]:
    """
    Empirical first-attempt failure rate per (bank, psp_app) pair, computed
    from whatever attempt rows are passed in -- callers are responsible for
    passing ONLY a training split, never rows that will also be scored or
    evaluated, or this becomes a leakage channel.
    """
    fail_count: dict[tuple, int] = defaultdict(int)
    total: dict[tuple, int] = defaultdict(int)
    for a in attempts:
        if int(a["attempt_number"]) != 1:
            continue
        key = (a["bank"], a["psp_app"])
        total[key] += 1
        if a["outcome"] == "FAILED":
            fail_count[key] += 1
    return {key: fail_count[key] / total[key] for key in total}


def lookup_bank_psp_rate(rates_table: dict[tuple, float], bank: str, psp_app: str) -> float:
    return rates_table.get((bank, psp_app), BANK_PSP_FALLBACK_RATE)


def build_examples(data_dir: str) -> list[dict]:
    """
    One example per billing cycle's FIRST attempt (attempt_number == 1) --
    this is the pre-debit decision point the policy engine acts at. Returns
    a list of flat dicts: categorical + numeric features above, plus
    'mandate_id', 'cycle_index' (for the temporal split), and 'label'
    (1 if that first attempt failed, 0 if it succeeded).

    Running per-mandate history features (n_prior_cycles, prior failure
    counts) are computed strictly from cycles with a SMALLER cycle_index
    for that same mandate -- never from the current or a future cycle.
    """
    mandates = {m["mandate_id"]: m for m in load_csv(os.path.join(data_dir, "mandates.csv"))}
    attempts = load_csv(os.path.join(data_dir, "attempts.csv"))

    by_mandate_cycle: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for a in attempts:
        by_mandate_cycle[a["mandate_id"]][int(a["cycle_index"])].append(a)

    # Empirical (bank, psp_app) historical fail rate. Built here from the
    # WHOLE dataset for convenience when just exploring; predict.py and
    # classify.py instead call compute_bank_psp_fail_rates() themselves on
    # the TRAIN split only before fitting, and policy.py at inference time
    # loads a version persisted by artifacts.py that was likewise built
    # only from training data -- so no scoring path ever sees a rate
    # computed using the rows it is being evaluated or run against.
    bank_psp_rates_table = compute_bank_psp_fail_rates(attempts)

    def bank_psp_rate(bank: str, psp_app: str) -> float:
        return lookup_bank_psp_rate(bank_psp_rates_table, bank, psp_app)

    examples: list[dict] = []
    for mandate_id, cycles in by_mandate_cycle.items():
        m = mandates[mandate_id]
        ordered_cycle_indices = sorted(cycles.keys())

        n_prior_cycles = 0
        n_prior_failed_cycles = 0

        for cycle_idx in ordered_cycle_indices:
            cycle_attempts = sorted(cycles[cycle_idx], key=lambda a: int(a["attempt_number"]))
            first = next((a for a in cycle_attempts if int(a["attempt_number"]) == 1), None)
            if first is None:
                continue

            cycle_failed_entirely = not any(a["outcome"] == "SUCCESS" for a in cycle_attempts)

            prior_failure_rate = (
                n_prior_failed_cycles / n_prior_cycles if n_prior_cycles > 0 else 0.20
            )  # 0.20 ~= population base rate, used only for a mandate's first cycle

            examples.append({
                "mandate_id": mandate_id,
                "cycle_index": cycle_idx,
                "plan": m["plan"],
                "rail": first["rail"],
                "bank": first["bank"],
                "psp_app": first["psp_app"],
                "amount_paise": int(first["amount_paise"]),
                "day_of_month": int(first["day_of_month"]),
                "days_since_salary_credit": int(first["days_since_salary_credit"]),
                "n_prior_cycles": n_prior_cycles,
                "n_prior_failed_cycles": n_prior_failed_cycles,
                "prior_failure_rate": prior_failure_rate,
                "bank_psp_historical_fail_rate": bank_psp_rate(first["bank"], first["psp_app"]),
                "label": 1 if first["outcome"] == "FAILED" else 0,
            })

            n_prior_cycles += 1
            if cycle_failed_entirely:
                n_prior_failed_cycles += 1

    return examples
