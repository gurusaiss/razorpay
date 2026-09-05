# Panel prep

Answers to the questions the buildathon's own application guide says a
panel is likely to ask, written against this specific codebase — not
generic answers that could describe any project. Every claim here traces
to a real file, a real run, or a real number in `docs/METRICS.md`.

## 1. "Walk me through your architecture decisions"

Three decisions, each for a stated reason, not because it's popular:

**Isotonic calibration on top of logistic regression (`predict.py`), not a
raw classifier score.** `policy.py`'s gates (`THRESHOLD_RETIME = 0.55`,
`THRESHOLD_NOTIFY_FLOOR = 0.20`) are absolute probability thresholds. A
model that ranks well but isn't calibrated (say, systematically outputs
0.9 when the true rate is 0.6) would make those thresholds meaningless —
you'd be comparing a threshold tuned for real probabilities against a
score that isn't one. `predict.py` reports a reliability table specifically
so this is checkable, not asserted: on the held-out test split, the
[0.9, 1.0] bucket predicts 0.981 and the actual failure rate there is
0.973 — close enough that the threshold comparisons in `policy.py` mean
what they say.

**RandomForest for cause classification (`classify.py`), not the same
logistic regression as the failure predictor.** Different job: predicting
*whether* a cycle fails is roughly monotonic in a few numeric features
(liquidity proxy, historical fail rate) — logistic regression's linear
decision boundary fits that well and stays interpretable. Predicting
*which of seven causes*, conditional on failure, is not linearly separable
in the same features (a PSP outage and an AFA gap can share similar
liquidity numbers but need completely different actions) — a tree ensemble
handles that interaction structure without hand-engineering cross-terms.

**A hand-shifted hour, not a re-simulated rail, for the outage causes
(`PSP_APP_UNAVAILABLE` / `BANK_TECHNICAL_ERROR`).** The original design
was `SWITCH_RAIL` (route through card e-mandate instead of UPI Autopay).
Phase 7's experiment caught this as a no-op: `Mandate.rail` is fixed at
creation in this build, so "switching" a rail doesn't change anything the
simulator can observe, while it still spent a retry slot. Rather than ship
a fake result, `RETIME` was extended to also shift presentment *hour*
(`artifacts.compute_best_hour_by_bank_psp`, built only from the training
split) — a real, implementable lever, because the underlying outage
function is keyed on hour, not date. This is documented as a bug found and
a design correction in `docs/METRICS.md`, not silently fixed and forgotten.

## 2. "What happens when [component X] fails?"

**The predict/classify models fail to load** (missing file, corrupted
joblib, version mismatch). `PolicyModels.load()` raises immediately, before
`run_generation` starts — the run fails loudly at startup, never silently
falls back to "no intervention" or a stale model. Fail closed, not open.

**The optional LLM call in `notify.py` times out or errors.**
`llm_compose()` wraps the API call in a bare `try/except`; any exception
(timeout, auth failure, rate limit, malformed response) returns
`template_compose()`'s deterministic output instead, tagged
`backend: "template_fallback"` so the caller can tell which path fired.
Tested directly in `tests/test_policy_and_notify.py` with no API key
configured at all. The mandatory fields (amount, date, mandate reference)
never depend on the LLM succeeding — they're rendered directly in both
paths — so an LLM outage degrades explanation quality, never correctness
or compliance.

**`coordinate.py` sees an action it doesn't recognize.** Raises
`ValueError` immediately rather than defaulting either way (see the
docstring in `coordinate.py`) — a new action added to
`config.PERMITTED_ACTIONS` without also being classified into
`ACTIONS_THAT_REPLACE_NATIVE_RETRY` or `ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE`
breaks the test suite immediately (`test_coordinate_covers_every_permitted_action`)
instead of silently picking a default that might double-stack a retry.

**The control arm changes between the baseline and with-policy runs.**
`experiment.py`'s integrity gate asserts the two control-arm attempt
record sets are identical; if not, it prints why and calls
`raise SystemExit(1)` before printing a single lift number. The headline
number in this project has never been allowed to be reported alongside a
broken comparison.

## 3. "Why AI here and not a rule-based approach?"

Two different answers for two different parts of the system, which is
itself the point:

Predicting failure probability and decline cause **is** the AI part,
because the signal is genuinely a pattern across several interacting,
noisy features (liquidity proxy, historical bank/PSP fail rate, prior
cycle history, amount, day-of-month) — hand-writing "if liquidity < X and
prior_failures > Y and bank == Z, probability = 0.6" for every combination
doesn't scale and doesn't generalize to bank/PSP pairs not seen while
writing the rules. A model that learns the interaction and is checked for
calibration is the right tool.

Turning that score into an action is **not** AI, on purpose. `policy.py`'s
`decide()` and `_cause_to_action()` are plain deterministic Python
matching a numbered table in `docs/TAXONOMY.md` section 4 — because the
action has to be auditable (a judge, or a regulator, can point at exactly
which rule fired for a given cycle), reproducible (the same inputs always
produce the same action), and compliant (the field-immutability rule in
`notify.py` means an LLM literally cannot alter a legally mandatory
amount or date, even if `NIRANTAR_LLM_API_KEY` is set). Using a model to
pick the action would trade all three of those properties for nothing —
the rule table is short and the mapping is not ambiguous. This is the
project's own stated design principle ("the model scores, policy
decides"), and it is checkable in code, not just claimed in a slide.

## 4. "Show me a case where your system fails"

Two real ones, at different levels:

**A specific cycle the policy made worse**, from the live audit trail
(`dashboard/nirantar_console.html`, filterable by "Lost"): mandate
`MND001516` (OTT_PREMIUM, SBI via GPay), cycle 8. Predicted cause
`PSP_APP_UNAVAILABLE`, action `RETIME` to hour 23 (the empirically safer
hour for this bank/PSP pair). Without intervention this cycle succeeded;
with the intervention it failed. Why: `RETIME`'s hour-shift only addresses
the *outage-window* component of failure risk — it does nothing for the
liquidity-driven or AFA-driven components, which are independent random
draws in `environment.py`. Moving to a statistically safer hour lowers
the *population-average* failure rate for this bank/PSP pair; it does not
guarantee any single cycle succeeds, and this is one of 69 cycles (out of
1,940 intervened) where the draw went the other way. The system is honest
about this: the "Lost" count is shown on the dashboard next to "Recovered",
never hidden, and the net effect across all 1,940 is still positive
(206 improved vs. 69 worse).

**A live, current classifier weakness**: `classify.py`'s held-out test
report shows `FUNDS_BLOCKED_BY_MANDATE` and `VELOCITY_LIMIT_EXCEEDED` at
0.00 precision and recall (18 and 21 test examples respectively — both
rare causes, 1.0-1.8% of all failures). The classifier currently never
predicts either cause correctly on held-out data; cycles with these true
causes get misclassified as something else (usually `INSUFFICIENT_FUNDS`,
the majority class), so they receive a `RETIME` to a liquidity window
instead of the more targeted response those causes would ideally get. Two
honest reasons: too few examples for a class-imbalanced RandomForest to
learn a reliable boundary, and the features available pre-debit may not
actually separate these causes well from `INSUFFICIENT_FUNDS` in this
synthetic build. The fix isn't more tuning — it's a bigger dataset, or a
model with better minority-class handling, and this is stated as-is rather
than the RandomForest's better numbers on the majority causes being the
only thing shown.

## 5. "How would you scale this to 10x volume?"

Current run: 4,000 mandates, 12 months, ~38,100 attempts, full pipeline
(generate through experiment plus both test files) in 1m41s on a single
core, from a clean clone. Where the time actually goes: `predict.py`
trains a `CalibratedClassifierCV` (internal 3-fold CV) on ~23,300 rows —
this and `classify.py`'s 200-tree RandomForest are the two CPU-bound
steps; everything else is pure Python iteration over attempt records
which is comfortably linear. At 40,000 mandates (10x), the two model-fit
steps are the first thing to reprofile — likely still seconds, since
scikit-learn's cost here is closer to O(n) than O(n²) for both estimators
at this feature count (11 features, one-hot expanded). The cycle
simulation loop (`simulate.py`) is already per-mandate-independent by
construction (each mandate gets its own `random.Random` stream keyed on
`mandate_id`, precisely so results don't depend on iteration order) — that
makes it embarrassingly parallel across mandates if wall-clock ever
mattered at real scale, with zero change to the correctness guarantees
`tests/test_rng_isolation.py` checks. The part that would need real
engineering work before 10x, and is stated as such rather than glossed
over: `policy.py`'s `decide()` currently does one `predict_proba` call per
treatment-arm cycle (not just intervened ones) — at high volume that's the
place to batch-score a day's cycles at once instead of one row at a time,
which the pipeline's `pandas.DataFrame`-per-call shape doesn't currently
do.

## 6. "What would you build next if you had 6 months?"

In order of what actually matters, not the flashiest idea first:

1. **Multi-seed evaluation.** Everything measured right now is one seed
   (`seed=7`). The determinism and integrity-gate guarantees are
   seed-independent by construction, but the specific +0.987pp lift is one
   run. Six months of runway means running this across 20+ seeds and
   reporting a confidence interval on the lift, not a single number.
2. **A genuine `SWITCH_RAIL` implementation.** Requires extending
   `environment.py` to model what actually happens when a mandate is
   re-presented on a second rail it has on file (most real customers have
   more than one payment instrument even if this synthetic build assigns
   one rail per mandate) — this is real modeling work, not a one-line fix,
   which is why it wasn't attempted under deadline pressure instead of
   faking it.
3. **`SPLIT_AMOUNT`, properly.** `Mandate.partial_allowed` already exists
   as real per-plan data (true for `EDTECH_EMI` and `D2C_REFILL`); what's
   missing is a partial-amount attempt outcome in `environment.py` to
   execute against it. `coordinate.py` and `notify.py` are already wired
   for it.
4. **Extend the same detect-diagnose-decide-execute-verify architecture to
   the other two Track 3 angles the buildathon guide describes** —
   checkout-abandonment recovery and B2B receivables chasing — as
   additional recovery *workflows* sharing this project's policy-engine
   pattern (deterministic action table, audit trail, control-group
   measurement), rather than three unrelated demos. See
   `docs/ARCHITECTURE.md`'s "other approaches considered" note for what
   that would concretely look like; deliberately not attempted in the
   current build, because doing it properly needs its own data model and
   its own held-out measurement, not a rushed bolt-on next to a working
   submission.
5. **A real Razorpay sandbox integration** in place of the synthetic
   simulator — same policy engine, same taxonomy, real webhook-driven
   attempt data instead of `environment.py`'s generated physics. This is
   the natural "connect it to the real ecosystem" next step, and it's
   listed last on purpose: everything above it makes the *existing* claims
   stronger before extending the system's surface area.
