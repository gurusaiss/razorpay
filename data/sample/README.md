# Sample data

150 rows from each CSV `generate.py` produces, taken from a real run
(`--seed 7 --mandates 4000 --months 12`), not hand-crafted. Committed so the
repo ships with something to look at without running anything first.

This is a sample, not the dataset the measured results in `docs/METRICS.md`
are based on -- that full run (4,000 mandates, ~36k attempts, ~6MB) is
regenerated on demand rather than committed, since it's fully deterministic
from one command:

```
python -m nirantar.generate --seed 7 --mandates 4000 --months 12 --out data/seed7_v1
```

- `mandates.csv` -- one row per mandate (plan, rail, bank, salary day, arm).
- `attempts.csv` -- one row per debit attempt (outcome, hour, day, intervention
  fields). `true_cause` is deliberately absent here -- see `labels.csv`.
- `labels.csv` -- `attempt_id` -> ground-truth `true_cause`, kept separate
  from `attempts.csv` because a real production system never observes this
  column directly; `predict.py`/`classify.py` exist to infer it.
- `registration_failures.csv` -- mandates that dropped off before ever
  reaching `REGISTERED`.
