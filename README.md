# Nirantar

A processor-agnostic mandate-health layer for Indian recurring payments
(UPI Autopay, card e-mandate, netbanking e-mandate). It predicts a
recurring-debit failure *before* the debit attempt, acts within the
RBI-mandated 24-hour pre-debit notification window, and replaces native
gateway retry rather than stacking another retry schedule on top of it.

Built for the Razorpay AI Buildathon 2026, Track 03 (AI Revenue Recovery).

**At a glance:** solo build · Python 3.12 · scikit-learn + pandas for the
ML layer · plain HTML/CSS/JS for both dashboards · no paid infra, no
database, no API keys required · fully reproducible from one seeded
command · full run end-to-end in under 2 minutes from a clean clone.

## Why

Recovery vendors in this space publish headline recovery percentages;
independent audits routinely find the real number well below the headline.
Nirantar's answer is a same-population, same-seed control-group comparison
every time a lift number is quoted — never a before/after on different
data, and never a number without the run that produced it. See
`docs/METRICS.md` for the full measured result, including two real
negative results found and fixed along the way rather than edited out.

**Headline result** (one seed, 4,000 synthetic mandates, 12 months, see
`docs/METRICS.md` for the full breakdown and the honest history behind it,
including a third bug found and fixed after these numbers were already
measured): cycle recovery rate on the treatment arm 98.01% → 98.99% (+0.99
percentage points), unrecovered revenue cut by 50.8%, control arm proven
byte-identical between the baseline and with-policy runs.

## See it live

- [Nirantar Console](https://claude.ai/code/artifact/621d4689-f58d-4b29-bfd4-9b56ff149978) — the portfolio-level result: KPIs, cause breakdown, a 52-row audit trail (wins and losses both shown, not cherry-picked).
- [Reelio × Nirantar demo](https://claude.ai/code/artifact/68350144-1da1-477e-84f2-da6836eb58e7) — a fictional subscription brand's checkout, walking through exactly where Nirantar sits in a Razorpay recurring-payments integration, ending on one real recovered billing cycle.

## Tech stack

- **Language:** Python 3.12 (standard library + `random.Random` for all
  seeded, reproducible randomness — no hidden global RNG state).
- **ML:** `scikit-learn` (`LogisticRegression` + `CalibratedClassifierCV`
  with isotonic calibration for failure-probability prediction,
  `RandomForestClassifier` for decline-cause classification), `pandas` and
  `numpy` for feature frames, `joblib` for model persistence.
- **Policy/decision layer:** plain Python — no framework, no prompt, no
  model call. Every action is a deterministic branch matching a numbered
  rule in `docs/TAXONOMY.md`.
- **Optional LLM:** used only for the notification's free-text
  explanation (never the mandatory fields), with a template fallback if
  no API key is configured — see `src/nirantar/notify.py`.
- **Dashboards:** self-contained HTML/CSS/vanilla JS, no build step, no
  framework, no external backend — the data is embedded directly from one
  real `experiment.py` run.
- **Testing:** Python's own `assert`-based scripts (`tests/`), no test
  framework dependency — runnable with a plain `python tests/<file>.py`.
- **Infra:** none required. No database, no message queue, no paid API,
  no cloud service — the entire pipeline runs on a laptop from one
  `pip install`.

## Architecture (one glance)

```
 Customer bank/PSP        Razorpay recurring          Nirantar                     Decision
 (UPI Autopay /      -->  payments (mandate     -->   pre-debit scoring     -->    RETIME /
  e-mandate)               schedule + gateway)         (24h RBI window)            SWITCH_RAIL /
                                 ^                            |                    SPLIT_AMOUNT /
                                 |                            v                    NOTIFY / HOLD
                                 +------ feeds next cycle's prediction ------------------+
```
The model layer only scores (a failure probability, a likely cause); every
branch that turns a score into one of those five actions is plain,
auditable Python matching a numbered rule in `docs/TAXONOMY.md` — never a
prompt. Full module map, both design boundaries, and stated limitations
(what this build can't yet do, and why) are in `docs/ARCHITECTURE.md`.

## Project layout

```
docs/
  TAXONOMY.md      Frozen decline-cause taxonomy, state machine, action set.
  METRICS.md       Baseline numbers + the Phase 7 holdout experiment result.
  ARCHITECTURE.md  Module map, design boundaries, stated limitations,
                   and other approaches considered but not built.
dashboard/         The two live demo pages linked above (self-contained HTML).
src/nirantar/      All source (see docs/ARCHITECTURE.md for the module map).
tests/             Regression tests -- run both before trusting any change.
data/sample/       150-row sample of generate.py's output, for a first look
                   without running anything. Full data is regenerated on
                   demand (below), not committed -- it's fully deterministic
                   from one command, seed and all.
.env.example       The one optional environment variable this project reads.
```

## Quickstart

Verified end-to-end from a completely clean clone: fresh `git clone`, a new
`venv`, `pip install -r requirements.txt`, then every command below in
order, with no other setup -- **2m00s total**, reproducing the numbers in
`docs/METRICS.md` byte-for-byte (same seed, deterministic). No API keys,
no database, no manual steps. (Re-timed after adding
`tests/test_model_determinism.py` -- it was 1m41s with two test files,
2m00s with three; re-verify this number if the test suite grows further.)

```bash
pip install -r requirements.txt   # or: pip install scikit-learn pandas joblib numpy --break-system-packages
export PYTHONPATH=src

# 1. Generate the baseline (no-intervention) synthetic dataset.
python -m nirantar.generate --seed 7 --mandates 4000 --months 12 --out data/seed7_v1

# 2. Summarise it -- the fixed reference point everything else is measured against.
python -m nirantar.baseline --data data/seed7_v1

# 3. Train the pre-debit failure predictor and decline-cause classifier,
#    and build the policy engine's reference tables (all from a temporal
#    training split, cycles 0-8; cycles 9-11 are the held-out test set).
python -m nirantar.predict   --data data/seed7_v1 --model-out models/predict_v1.joblib
python -m nirantar.classify  --data data/seed7_v1 --model-out models/classify_v1.joblib
python -m nirantar.artifacts --data data/seed7_v1 --out models/artifacts_v1.json

# 4. Run the holdout experiment: same population/seed, policy_fn=None vs
#    the real policy engine, control arm proven untouched before any lift
#    number is trusted.
python -m nirantar.experiment --seed 7 --mandates 4000 --months 12

# 5. Run the test suite.
python tests/test_rng_isolation.py
python tests/test_policy_and_notify.py
python tests/test_model_determinism.py
```

## Known limitations

- `SWITCH_RAIL` is not actively selected — this build can't re-simulate a
  mandate on a different rail, and shipping it anyway silently cost a retry
  slot for no benefit (found via `experiment.py`, see the honest history in
  `docs/METRICS.md`). Retired in favor of an hour-shifting `RETIME`, which
  actually works against the outage cause it was meant to fix.
- `SPLIT_AMOUNT` is likewise never selected — `simulate.py`/`environment.py`
  have no partial-amount attempt mechanism, so activating it without that
  would silently recreate the same no-op bug. Wired end-to-end
  (`coordinate.py`, `notify.py`) but gated off in `policy.py` on purpose.
- Four taxonomy causes (`MANDATE_REVOKED`, `PRE_DEBIT_OPT_OUT`,
  `TOKEN_REISSUED`, `MANDATE_PAUSED`) never occur at the attempt level in
  this synthetic build — they're modelled as mandate-state events instead.
- One seed evaluated so far (`seed=7`); the determinism and holdout-integrity
  checks are seed-independent, but the specific +0.99pp lift number is one
  measured run, not yet a confidence interval across seeds.
- The LLM notification path (`notify.py`) is untested against a real API
  key in this environment — only its template fallback is exercised by the
  test suite.

Full detail on all of the above: `docs/ARCHITECTURE.md`.

## Status

Complete: taxonomy/config, synthetic generator, baseline metrics,
calibrated predictor, decline-cause classifier, policy engine +
coordination lock, notification composer, the holdout experiment,
edge-case and determinism tests, both live dashboards, and this
documentation set. A repo audit after the initial build found and fixed
three real bugs along the way (full history in `docs/METRICS.md`) rather
than shipping the first version that ran without crashing.
