"""
Smart Paste AI — replaces the regex parser with an LLM that understands
messy Gujarati / Hindi / English WhatsApp address blobs.

Pipeline per call:
  1. Merge the per-user custom instructions (from Settings) with our
     DEFAULT_SHIPBOT_PROMPT (strict 14-line schema).
  2. Send to Emergent LLM with a ~4 s timeout.
  3. Parse the 14-line code block, classify the complexity in one shot.
  4. Return {fields, missing, complexity, reason, raw}.
  5. Fall back to the legacy regex parser from `server.parse_structured_paste`
     on ANY error, so Smart Paste NEVER breaks because of an LLM outage.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

_LOG = logging.getLogger("smart_paste_ai")

_LLM_KEY = os.getenv("EMERGENT_LLM_KEY", "")
_MODEL = os.getenv("SMART_PASTE_MODEL", "gpt-4.1-nano")
_PROVIDER = os.getenv("SMART_PASTE_PROVIDER", "openai")
_TIMEOUT = float(os.getenv("SMART_PASTE_TIMEOUT", "6.0"))

# The 14-line schema we ALWAYS return (hidden from users).
SCHEMA_FIELDS: List[str] = [
    "NAME", "PHONE", "ADDRESS_1", "ADDRESS_2", "CITY", "STATE", "PINCODE",
    "ITEMS", "AMOUNT", "PAYMENT", "COURIER", "ORDER_ID", "WEIGHT", "NOTES",
]

# Default ShipBot-style system prompt bundled with the app. Users can
# prepend/override their own instructions via Settings.smart_paste_instructions.
DEFAULT_SHIPBOT_PROMPT = """\
You are "ShipBot" — a strict Shipment Data Parser for a courier app.
You process WhatsApp messages in Gujarati / Hindi / English and extract
shipment details into a fixed 14-line structured block.

OUTPUT FORMAT (STRICT) — return ONE code block with EXACTLY 14 lines,
in this exact order, with no emoji / no explanation / no extra lines:

NAME: <customer name in English>
PHONE: <10 digits only, strip +91 or 0>
ADDRESS_1: <primary address line>
ADDRESS_2: <secondary address or ->
CITY: <city in English>
STATE: <state in English>
PINCODE: <6 digits only>
ITEMS: <item x qty, comma separated>
AMOUNT: <number only, no ₹ symbol>
PAYMENT: <COD or PAID>
COURIER: <courier name or ->
ORDER_ID: <order number or ->
WEIGHT: <weight with unit or ->
NOTES: <special instruction or ->

Rules:
  - Convert Gujarati (૧૨૩) & Hindi (१२३) digits to English (123).
  - If any field is missing or unclear → write EXACTLY: -
  - NEVER guess, invent or assume data.
  - AMOUNT = COD amount (never PAID/token unless explicitly COD).
  - Token-paid amounts go in NOTES as "Token <value>".
  - PAYMENT = COD if a COD number is present, else PAID.

After the 14-line block, on a NEW line, output one JSON object describing
the address complexity, like:
  {"complexity":"simple"|"medium"|"complex","reason":"<max 10 words>"}
Nothing else.
"""


# ---- Public API ---------------------------------------------------------

async def parse_paste_via_llm(
    text: str,
    *,
    custom_instructions: str = "",
) -> Dict[str, Any]:
    """Parse the user's raw paste into the 14-field schema + complexity.

    Returns:
      {
        "fields":        dict[NAME..NOTES → value | ""],
        "missing":       list[str],         # field names still "-"
        "complexity":    "simple"|"medium"|"complex",
        "ai_reason":     str,
        "raw":           str,               # raw LLM response (debug)
        "source":        "llm" | "fallback",
      }
    """
    text = (text or "").strip()
    if not text:
        return _empty_result(source="fallback")

    # No LLM key → skip LLM, caller will use the regex parser.
    if not _LLM_KEY:
        return _empty_result(source="fallback")

    system = DEFAULT_SHIPBOT_PROMPT
    if custom_instructions.strip():
        # User's addendum goes BEFORE the default rules so their overrides
        # take effect without losing the schema contract.
        system = (
            "## USER CUSTOMISATION (honour these first when not conflicting "
            "with the output format rules below):\n"
            + custom_instructions.strip()
            + "\n\n-- BASE RULES --\n"
            + DEFAULT_SHIPBOT_PROMPT
        )

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = (
            LlmChat(
                api_key=_LLM_KEY,
                session_id=f"smart-paste-{abs(hash(text[:200]))}",
                system_message=system,
            )
            .with_model(_PROVIDER, _MODEL)
        )
        msg = UserMessage(text=f"Parse this message:\n```\n{text}\n```")
        raw = await asyncio.wait_for(chat.send_message(msg), timeout=_TIMEOUT)
    except Exception as e:
        _LOG.warning("Smart-paste LLM failed (%s) — caller should fall back", e)
        return _empty_result(source="fallback")

    fields, missing = _parse_schema_block(raw)
    complexity, reason = _parse_complexity_block(raw)
    return {
        "fields": fields,
        "missing": missing,
        "complexity": complexity,
        "ai_reason": reason,
        "raw": raw,
        "source": "llm",
    }


# ---- Parsers ------------------------------------------------------------

_SCHEMA_RE = re.compile(r"^\s*([A-Z_]+)\s*:\s*(.+?)\s*$", re.MULTILINE)


def _parse_schema_block(raw: str) -> Tuple[Dict[str, str], List[str]]:
    """Extract KEY: value lines from the LLM response, tolerant to fences."""
    if not raw:
        return ({k: "" for k in SCHEMA_FIELDS}, list(SCHEMA_FIELDS))
    cleaned = raw.replace("```", "").strip()

    out: Dict[str, str] = {k: "" for k in SCHEMA_FIELDS}
    for m in _SCHEMA_RE.finditer(cleaned):
        key = m.group(1).upper()
        if key not in out:
            continue
        val = m.group(2).strip()
        # Treat literal "-" or "->" or common blanks as empty so the UI
        # doesn't echo ShipBot's placeholder back to the user.
        if (
            val in {"-", "->", "--"}
            or val.lower() in {"n/a", "na", "none", "null", ""}
        ):
            val = ""
        out[key] = _digits_to_en(val)

    missing = [k for k in SCHEMA_FIELDS if not out[k]]
    return out, missing


# Complexity is on its own trailing line as a JSON object. We search for
# the LAST {...} block so it isn't confused with anything in the body.
_COMPLEXITY_RE = re.compile(r"\{[^{}]*\"complexity\"[^{}]*\}", re.IGNORECASE)


def _parse_complexity_block(raw: str) -> Tuple[str, str]:
    if not raw:
        return ("simple", "llm returned nothing")
    matches = _COMPLEXITY_RE.findall(raw)
    if not matches:
        return ("medium", "complexity tag missing — defaulted")
    import json as _json
    try:
        obj = _json.loads(matches[-1])
    except Exception:
        return ("medium", "bad complexity JSON — defaulted")
    c = str(obj.get("complexity", "")).strip().lower()
    if c not in {"simple", "medium", "complex"}:
        return ("medium", f"unexpected complexity '{c}' — coerced to medium")
    reason = str(obj.get("reason", "")).strip()[:160] or "llm classification"
    return (c, reason)


# ---- Helpers ------------------------------------------------------------

_GUJ_DIGIT = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")
_HIN_DIGIT = str.maketrans("०१२३४५६७८९", "0123456789")


def _digits_to_en(s: str) -> str:
    if not s:
        return s
    return s.translate(_GUJ_DIGIT).translate(_HIN_DIGIT)


def _empty_result(source: str) -> Dict[str, Any]:
    return {
        "fields": {k: "" for k in SCHEMA_FIELDS},
        "missing": list(SCHEMA_FIELDS),
        "complexity": "medium",
        "ai_reason": f"no-op ({source})",
        "raw": "",
        "source": source,
    }


# ---- Converters for existing pipeline ----------------------------------

def to_legacy_fields(ai_fields: Dict[str, str]) -> Dict[str, str]:
    """Map the 14-line schema keys onto the field names the rest of the
    app (Shipment model) already uses."""
    return {
        "customer_name":  ai_fields.get("NAME", ""),
        "customer_phone": ai_fields.get("PHONE", ""),
        "address_line1":  ai_fields.get("ADDRESS_1", ""),
        "address_line2":  ai_fields.get("ADDRESS_2", ""),
        "city":           ai_fields.get("CITY", ""),
        "state":          ai_fields.get("STATE", ""),
        "pincode":        ai_fields.get("PINCODE", ""),
        "items":          ai_fields.get("ITEMS", ""),
        "amount":         ai_fields.get("AMOUNT", ""),
        "payment_mode":   (ai_fields.get("PAYMENT", "") or "COD").upper(),
        "courier_name":   ai_fields.get("COURIER", ""),
        "order_id":       ai_fields.get("ORDER_ID", ""),
        "weight":         ai_fields.get("WEIGHT", ""),
        "notes":          ai_fields.get("NOTES", ""),
    }
