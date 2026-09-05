"""
Phase 6: the pre-debit notification composer.

Every recurring debit above the AFA-exempt limit (config.AFA_EXEMPT_LIMIT_*)
requires a pre-debit notice at least config.PRE_DEBIT_NOTICE_HOURS (24h)
before the attempt, per the RBI Digital Payments E-Mandate Framework, 2026.
That notice has a small set of legally/contractually mandatory fields --
this module's whole design is built around a single rule:

    FIELD-IMMUTABILITY RULE (docs/ARCHITECTURE.md): the mandatory fields
    (amount, debit date, merchant name, mandate reference) are rendered
    from the mandate/attempt record directly and are NEVER passed through
    an LLM. An LLM (when configured) may only compose the SURROUNDING
    human-readable explanation -- why this debit might fail and what the
    customer can do -- and its output is validated post-hoc to confirm
    the mandatory fields still appear verbatim before being used; if that
    check fails, the composer falls back to the plain template. This is
    "the model scores/writes, policy decides" principle applied to
    notification text instead of a money-moving action: the LLM never
    gets a chance to alter a legally mandatory number.

Two backends:
  - template_compose(): pure deterministic string formatting, zero cost,
    always available, used as both the default AND the safety fallback.
  - llm_compose(): calls an LLM ONLY for the free-text explanation, if
    NIRANTAR_LLM_API_KEY is set in the environment; otherwise silently
    behaves exactly like template_compose(). This keeps the AI-integration
    cost genuinely optional and bounded -- one short completion per
    at-risk cycle, never per attempt, never for cycles the policy engine
    didn't already flag as worth an intervention.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date


CAUSE_EXPLANATIONS = {
    "INSUFFICIENT_FUNDS": "your account balance may be low on the scheduled date",
    "VELOCITY_LIMIT_EXCEEDED": "a transaction-frequency limit on your account may block this debit",
    "FUNDS_BLOCKED_BY_MANDATE": "another payment mandate may be holding a block on these funds",
    "PSP_APP_UNAVAILABLE": "your UPI app's servers have had recent outages around this time",
    "BANK_TECHNICAL_ERROR": "your bank has had recent technical issues around this time",
    "AFA_NOT_COMPLETED": "this debit needs an additional authentication step from you",
    "GATEWAY_TECHNICAL_ERROR": "a payment gateway issue may affect this debit",
    "TOKEN_REISSUED": "your saved payment method may have been reissued or updated",
    "MANDATE_PAUSED": "your mandate is currently paused",
}

ACTION_REMEDY = {
    "RETIME": "We've moved this debit to a window where it's historically more likely to succeed.",
    "SPLIT_AMOUNT": "We may offer a partial/split collection if the full amount doesn't clear.",
    "PERSONALISE_NOTIFICATION": "No schedule change -- please make sure funds/authentication are ready.",
    "SWITCH_RAIL": "We may route this collection through an alternate payment method.",
    "HOLD": "No debit will be attempted this cycle.",
}


@dataclass
class MandatoryFields:
    merchant_name: str
    amount_paise: int
    debit_date: date
    mandate_ref: str

    def rupees_str(self) -> str:
        return f"Rs {self.amount_paise / 100:,.2f}"

    def date_str(self) -> str:
        return self.debit_date.strftime("%d %b %Y")


def template_compose(fields: MandatoryFields, cause: str | None, action: str) -> str:
    lines = [
        f"{fields.merchant_name}: A payment of {fields.rupees_str()} is scheduled on "
        f"{fields.date_str()} against mandate {fields.mandate_ref}.",
    ]
    if cause and cause in CAUSE_EXPLANATIONS:
        lines.append(f"Heads up: {CAUSE_EXPLANATIONS[cause]}.")
    if action in ACTION_REMEDY:
        lines.append(ACTION_REMEDY[action])
    lines.append("Reply STOP to this message to opt out of this specific debit (PRE_DEBIT_OPT_OUT).")
    return " ".join(lines)


def _fields_present_verbatim(text: str, fields: MandatoryFields) -> bool:
    """The field-immutability check: every mandatory field must appear in
    the composed text exactly as rendered by template_compose's own
    formatting -- if an LLM has paraphrased or altered any of them, this
    fails and the caller must fall back to the template."""
    required = [fields.rupees_str(), fields.date_str(), fields.mandate_ref]
    return all(r in text for r in required)


def llm_compose(fields: MandatoryFields, cause: str | None, action: str) -> tuple[str, str]:
    """
    Returns (text, backend) where backend is "llm" or "template_fallback".
    Only ever calls out for the free-text explanation; the mandatory
    fields are always injected by this function directly, never generated
    by the model, so there's nothing for the field-immutability check to
    catch in normal operation -- it exists as a safety net for the day
    someone loosens that discipline, not as the primary defense.
    """
    api_key = os.environ.get("NIRANTAR_LLM_API_KEY")
    if not api_key:
        return template_compose(fields, cause, action), "template_fallback"

    cause_note = CAUSE_EXPLANATIONS.get(cause, "a temporary issue")
    remedy_note = ACTION_REMEDY.get(action, "")
    prompt = (
        f"Write ONE short, warm, plain-language sentence (no markdown) explaining to a "
        f"customer why their upcoming recurring payment might fail, given: {cause_note}. "
        f"Then one more short sentence on what we're doing: {remedy_note} "
        f"Do not mention any amount, date, or account/mandate number -- those are added separately."
    )
    try:
        import anthropic  # optional dependency, only imported if a key is configured
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-haiku-4-5",  # cheapest available tier -- this is free text only, low stakes
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        explanation = resp.content[0].text.strip()
    except Exception:
        return template_compose(fields, cause, action), "template_fallback"

    header = (
        f"{fields.merchant_name}: A payment of {fields.rupees_str()} is scheduled on "
        f"{fields.date_str()} against mandate {fields.mandate_ref}."
    )
    full_text = f"{header} {explanation} Reply STOP to opt out of this specific debit."

    if not _fields_present_verbatim(full_text, fields):
        return template_compose(fields, cause, action), "template_fallback"
    return full_text, "llm"


def compose(fields: MandatoryFields, cause: str | None, action: str) -> dict:
    text, backend = llm_compose(fields, cause, action)
    return {
        "text": text,
        "backend": backend,
        "mandatory_fields_verified": _fields_present_verbatim(text, fields),
    }
