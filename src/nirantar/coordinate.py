"""
The coordination lock (Phase 5b).

The research behind this project's track selection found a real, named
failure mode in this space: recovery vendors (Churnkey, Paddle, Vindicia,
FlexFactor) layer their own retry logic ON TOP OF the gateway's native
T+1/T+2/T+3 retries, rather than replacing it -- so a customer's mandate
gets hit by both schedules independently, degrading issuer-side
authorization rates over time as declines stack up. Only FlyCode was found
to replace rather than stack. `RETRY` is deliberately absent from
config.PERMITTED_ACTIONS for exactly this reason (see docs/TAXONOMY.md
section 3).

This module is the one place that "replace, don't stack" rule lives, so
it's reviewed once rather than re-decided inline in policy.py or
simulate.py.
"""

from __future__ import annotations

# Actions that change WHEN or HOW the debit is attempted -- Nirantar now
# owns the attempt schedule for this cycle, so the native gateway's
# T+1/T+2/T+3 retry schedule must be suppressed, not run alongside it.
ACTIONS_THAT_REPLACE_NATIVE_RETRY = {"RETIME", "SWITCH_RAIL", "SPLIT_AMOUNT"}

# Actions that touch only the notification, or take no attempt at all --
# the native retry schedule (if the attempt fails) is left alone because
# nothing about the attempt itself has changed.
ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE = {"PERSONALISE_NOTIFICATION", "HOLD"}


def should_suppress_native_retry(action: str) -> bool:
    if action in ACTIONS_THAT_REPLACE_NATIVE_RETRY:
        return True
    if action in ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE:
        return False
    raise ValueError(
        f"coordinate.py has no stacking rule for action {action!r} -- "
        f"every entry in config.PERMITTED_ACTIONS must be classified in "
        f"one of ACTIONS_THAT_REPLACE_NATIVE_RETRY or "
        f"ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE, on purpose, rather than "
        f"defaulting silently either way."
    )
