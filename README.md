# Nirantar

A processor-agnostic mandate-health layer for Indian recurring payments
(UPI Autopay, card e-mandate, netbanking e-mandate). It predicts a
recurring-debit failure *before* the debit attempt, acts within the
RBI-mandated 24-hour pre-debit notification window, and replaces native
gateway retry rather than stacking another retry schedule on top of it.

Built for the Razorpay AI Buildathon 2026, Track 03 (AI Revenue Recovery).

## Why

Recovery vendors in this space publish headline recovery percentages;
independent audits routinely find the real number well below the headline.
Nirantar's answer is a same-population, same-seed control-group comparison
every time a lift number is quoted — never a before/after on different
data, and never a number without the run that produced it. See
`docs/METRICS.md` for the full measured result, including two real
negative results found and fixed along the way rather than edited out.

**Headline result** (one seed, 4,000 synthetic mandates, 12 months, see
`docs/METRICS.md` for the full breakdown and the honest history behind it):
cycle recovery rate on the treatment arm 98.82% → 99.36% (+0.54 percentage
points), unrecovered revenue cut by 44.2%, control arm proven byte-identical
between the baseline and with-policy runs.

## Project layout

See `docs/ARCHITECTURE.md` for the full module map and the two design
boundaries (ground-truth vs. observable features; model-scores/policy-decides)
everything else is built around. `docs/TAXONOMY.md` is the frozen contract
(decline causes, mandate states, permitted actions) every module conforms to.

```
docs/
  TAXONOMY.md      Frozen decline-cause taxonomy, state machine, action set.
  METRICS.md       Baseline numbers + the Phase 7 holdout experiment result.
  ARCHITECTURE.md  Module map, design boundaries, stated limitations.
src/nirantar/      All source (see docs/ARCHITECTURE.md for the module map).
tests/             Regression tests -- run both before trusting any change.
```

## Quickstart

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
```

## Status

Phases 1-8 of the build plan are complete (taxonomy/config, synthetic
generator, baseline metrics, calibrated predictor, decline-cause
classifier + policy engine + coordination lock, notification composer,
the holdout experiment, and edge-case tests). Phase 9 (this README,
`docs/ARCHITECTURE.md`, and the pitch/demo materials) is in progress.
