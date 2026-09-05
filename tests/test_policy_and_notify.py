"""
Phase 8: targeted failure-injection / edge-case tests for the policy engine
and notification composer -- the pieces where a silent regression would be
a compliance or money problem, not just a wrong number.

Run with:  PYTHONPATH=src python3 tests/test_policy_and_notify.py
"""

from __future__ import annotations

from datetime import date

from nirantar import config
from nirantar.coordinate import should_suppress_native_retry, ACTIONS_THAT_REPLACE_NATIVE_RETRY, ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE
from nirantar.notify import compose, MandatoryFields, _fields_present_verbatim


def test_coordinate_covers_every_permitted_action():
    covered = ACTIONS_THAT_REPLACE_NATIVE_RETRY | ACTIONS_THAT_LEAVE_NATIVE_RETRY_ALONE
    missing = set(config.PERMITTED_ACTIONS) - covered
    assert not missing, f"coordinate.py has no stacking rule for: {missing}"
    for action in config.PERMITTED_ACTIONS:
        should_suppress_native_retry(action)  # must not raise
    print("PASS: every config.PERMITTED_ACTIONS entry has a coordination rule")


def test_coordinate_raises_on_unknown_action():
    try:
        should_suppress_native_retry("RETRY")  # deliberately absent from PERMITTED_ACTIONS
        raise AssertionError("expected ValueError for an unclassified action")
    except ValueError:
        pass
    print("PASS: coordinate.py raises rather than silently defaulting on an unknown action")


def test_notify_field_immutability_catches_tampering():
    fields = MandatoryFields("Test Merchant", 2_499_00, date(2026, 9, 15), "MND000999")
    honest_text, backend = "", ""
    from nirantar.notify import template_compose
    honest_text = template_compose(fields, "INSUFFICIENT_FUNDS", "RETIME")
    assert _fields_present_verbatim(honest_text, fields), "template output must always pass its own check"

    tampered = honest_text.replace(fields.rupees_str(), "Rs 1.00")
    assert not _fields_present_verbatim(tampered, fields), (
        "field-immutability check failed to catch a tampered amount -- "
        "this is the one check standing between an LLM paraphrase and a "
        "wrong number reaching a customer"
    )
    print("PASS: field-immutability check catches a tampered mandatory field")


def test_notify_falls_back_without_api_key():
    import os
    assert "NIRANTAR_LLM_API_KEY" not in os.environ, (
        "this test assumes no LLM key is configured in this environment"
    )
    fields = MandatoryFields("Test Merchant", 999_00, date(2026, 9, 20), "MND000001")
    result = compose(fields, "PSP_APP_UNAVAILABLE", "RETIME")
    assert result["backend"] == "template_fallback"
    assert result["mandatory_fields_verified"] is True
    print("PASS: notify.compose() falls back to the free template with no API key configured")


def test_economic_floor_mandate_gets_no_intervention():
    """
    Regression test for the bug found via experiment.py: a mandate priced
    under config.ECONOMIC_FLOOR_PAISE must get policy_fn -> None (native
    retry proceeds untouched), never an Intervention(action="HOLD") that
    would skip the whole cycle's attempt.
    """
    from nirantar.population import Mandate
    from nirantar.policy import decide, PolicyModels
    import joblib
    from nirantar import artifacts as artifacts_mod

    models = PolicyModels(
        predict_model=joblib.load("models/predict_v1.joblib"),
        classify_model=joblib.load("models/classify_v1.joblib"),
        artifacts=artifacts_mod.load("models/artifacts_v1.json"),
    )
    cheap_mandate = Mandate(
        mandate_id="MND999999", customer_id="CUST999999", plan="OTT_BASIC",
        amount_paise=149_00, rail="UPI_AUTOPAY", bank="HDFC", psp_app="GPAY",
        salary_day=1, registered_on="2025-09-01", arm="treatment",
    )
    assert cheap_mandate.amount_paise < config.ECONOMIC_FLOOR_PAISE
    decision = decide(cheap_mandate, 0, date(2025, 9, 1), [], models)
    assert decision is None, (
        f"expected None (native retry untouched) for a sub-floor mandate, got {decision!r}"
    )
    print("PASS: a mandate priced under ECONOMIC_FLOOR_PAISE gets no intervention (not a HOLD)")


if __name__ == "__main__":
    test_coordinate_covers_every_permitted_action()
    test_coordinate_raises_on_unknown_action()
    test_notify_field_immutability_catches_tampering()
    test_notify_falls_back_without_api_key()
    test_economic_floor_mandate_gets_no_intervention()
    print("\nAll policy/notify edge-case tests passed.")
