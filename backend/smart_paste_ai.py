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

# The 15-line schema we ALWAYS return (hidden from users).
SCHEMA_FIELDS: List[str] = [
    "NAME", "PHONE", "ALT_PHONE", "ADDRESS_1", "ADDRESS_2", "CITY", "STATE",
    "PINCODE", "ITEMS", "AMOUNT", "PAYMENT", "COURIER", "ORDER_ID", "WEIGHT", "NOTES",
]

# Default ShipBot-style system prompt bundled with the app. Users can
# prepend/override their own instructions via Settings.smart_paste_instructions.
DEFAULT_SHIPBOT_PROMPT = """\
You are "ShipBot" — a strict Shipment Data Parser for a courier app.
You process WhatsApp messages in Gujarati / Hindi / English and extract
shipment details into a fixed 15-line structured block.

CRITICAL: Every line must be a real parsed value OR a single dash `-`.
NEVER output template placeholders like "<customer name>", "<phone>",
"<6 digits>" etc. — those are descriptions, NOT values. If the text
doesn't contain the info, write `-` only.

OUTPUT FORMAT (STRICT) — return ONE code block with EXACTLY 15 lines,
in this exact order, with no emoji / no explanation / no extra lines:

NAME: (real customer name, keep original script — Gujarati stays Gujarati, English stays English)
PHONE: (primary 10 digits only, strip +91 or 0)
ALT_PHONE: (second / alternative 10-digit number if message has TWO phones, else -)
ADDRESS_1: (primary address line — street / house / area / village ONLY, keep original script)
ADDRESS_2: (secondary address / landmark, else -)
CITY: (city / town — keep original script)
STATE: (state — keep original script)
PINCODE: (6 digits only)
ITEMS: (item + quantity like "Saree x 2"; comma-separated for multiple)
AMOUNT: (number only, no ₹ symbol)
PAYMENT: (COD or PAID)
COURIER: (courier name or -)
ORDER_ID: (order number or -)
WEIGHT: (weight with unit or -)
NOTES: (special instruction or -)

EXAMPLE of a GOOD response (for a Gujarati message):
```
NAME: Asari Nikunj Babubhai
PHONE: 8128949387
ALT_PHONE: 7874786098
ADDRESS_1: મુ-પોસ્ટ ઝિંઝોડી, તાલુકો ભિલોડા, જિલ્લા અરવલ્લી
ADDRESS_2: -
CITY: Aravalli
STATE: Gujarat
PINCODE: 383246
ITEMS: ODC3 x 1
AMOUNT: -
PAYMENT: COD
COURIER: -
ORDER_ID: -
WEIGHT: 20gm
NOTES: -
```

RULES:
  - NEVER output angle brackets `<` or `>` in a value. They are only used
    as description markers in this prompt — a value must be a real word
    or the single character `-`.
  - Convert Gujarati (૧૨૩) & Hindi (१२३) digits to English (123).
  - If any field is missing or unclear → write EXACTLY: -
  - NEVER guess, invent or assume data.
  - When two phone numbers appear (e.g. "8128949387 / 7874786098" or
    "call 98765 43210 or 99887 76655"), the FIRST goes in PHONE and the
    SECOND goes in ALT_PHONE.
  - ITEMS MUST NEVER appear in ADDRESS fields. Products (saree, kurti,
    dress, shoes, toy, book, ODC, gm/kg weight codes, etc.) always go
    in ITEMS — NEVER in ADDRESS_1 or ADDRESS_2.
  - QUANTITY rules for ITEMS:
    * "Saree 2 pcs" → "Saree x 2"
    * "Saree 2" → "Saree x 2"
    * "2 saree" → "Saree x 2"
    * "Saree" (no qty mentioned) → "Saree x 1"
    * Multiple items: "Saree x 2, Kurti x 1"
    * A weight-only item like "20gm ODC3" → "ODC3 x 1" (weight goes in WEIGHT)
  - ADDRESS_1 is ONLY physical address: house no / street / area /
    colony / village / post / taluka / district. NEVER products,
    quantity, or amount.
  - **CRITICAL ADDRESS RULE — NEVER LEAVE ADDRESS_1 EMPTY IF THE
    INPUT CONTAINS ANY ADDRESS-LIKE TEXT.** This is the single most
    important rule.
    * If the input has a label like "Shipping address" / "Delivery
      address" / "Address" / "પત્તો" / "पता", treat the WHOLE multi-
      line block that follows (until you hit phone / email / name /
      order id / payment) as the address block.
    * From that address block, peel OFF the trailing city + state +
      pincode (whatever you can detect) and put each in its own
      field. Whatever is LEFT (street/house/apartment/area/landmark
      bits) MUST go into ADDRESS_1 (with overflow into ADDRESS_2).
    * Example of a long shipping address that MUST be split, NOT
      collapsed into city only:
        Input  : "Shipping address: C-401 Venus Apartment, near
                  Sainik Vihar Saraswati Vihar, Rani Bagh, Pitampura,
                  Delhi, 110034 Delhi"
        WRONG  : ADDRESS_1: -
                 CITY: Delhi  STATE: Delhi  PINCODE: 110034
        RIGHT  : ADDRESS_1: C-401 Venus Apartment, Saraswati Vihar,
                            Rani Bagh, Pitampura
                 ADDRESS_2: near Sainik Vihar
                 CITY: Delhi  STATE: Delhi  PINCODE: 110034
    * If the same word (e.g. "Delhi") appears TWICE — once as a
      neighbourhood name and once as the city — keep the city
      occurrence in CITY and KEEP the neighbourhood occurrence in
      ADDRESS_1. Do NOT silently drop one.
    * "near …" / "behind …" / "opp …" landmarks → ADDRESS_2 if a
      separate ADDRESS_1 already exists, else keep them in ADDRESS_1.
    * If you can identify a flat/house number (C-401, B/12, 3rd floor,
      Room 5, Plot 22 etc.) — that ALWAYS belongs in ADDRESS_1.
  - City / State should be English transliteration when obvious (e.g.
    "અરવલ્લી" → "Aravalli", "ગુજરાત" → "Gujarat") so the courier sheet
    is consistent; otherwise keep the source script.
  - AMOUNT = COD amount (never PAID/token unless explicitly COD).
  - Token-paid amounts go in NOTES as "Token <value>".
  - PAYMENT = COD if a COD number is present, else PAID.

After the 15-line block, on a NEW line, output one JSON object describing
the address complexity:
  {"complexity":"simple"|"medium"|"complex","reason":"short reason"}
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

    # ── Address-recovery fallback (text path) ───────────────────────
    # Same problem as photo OCR — if the LLM picked CITY/PINCODE from
    # the text but left ADDRESS_1 blank, do a tightly scoped follow-up
    # that asks ONLY for the street/area portion. This is a single
    # extra LLM call that fires only on bad cases (≈5% of inputs).
    addr1 = (fields.get("ADDRESS_1") or "").strip()
    has_locality = bool(
        (fields.get("CITY") or "").strip()
        or (fields.get("PINCODE") or "").strip()
    )
    if (not addr1 or addr1 == "-") and has_locality:
        try:
            from emergentintegrations.llm.chat import (
                LlmChat as _Chat3, UserMessage as _UM3,
            )
            chat3 = (
                _Chat3(
                    api_key=_LLM_KEY,
                    session_id=f"smart-paste-text-fix-{abs(hash(text[:200]))}",
                    system_message=(
                        "You are an address extractor. The user will give "
                        "you raw text containing a customer shipping "
                        "address. Output ONLY the street / house / flat / "
                        "apartment / building / area / locality / landmark "
                        "portion — i.e. EVERYTHING except city, state, "
                        "pincode, phone, email, name, order id, items, "
                        "amount and payment. Keep the original script. "
                        "One or two short comma-separated lines. No labels, "
                        "no markdown, no explanation. If genuinely no "
                        "street/area text is present, return a single dash."
                    ),
                )
                .with_model(_PROVIDER, _MODEL)
            )
            msg3 = _UM3(text=f"Text:\n```\n{text}\n```\n\nAddress only:")
            recovered = await asyncio.wait_for(
                chat3.send_message(msg3), timeout=_TIMEOUT,
            )
            recovered = (recovered or "").strip()
            recovered = re.sub(r"^```[a-z]*\s*", "", recovered)
            recovered = re.sub(r"\s*```$", "", recovered)
            recovered = re.sub(
                r"^(address[_\s-]*1?\s*:\s*)", "", recovered, flags=re.I,
            )
            if recovered and recovered != "-":
                lines = [
                    ln.strip(" ,;-")
                    for ln in recovered.splitlines() if ln.strip()
                ]
                if lines:
                    fields["ADDRESS_1"] = lines[0][:140]
                    if len(lines) > 1 and not (fields.get("ADDRESS_2") or "").strip():
                        fields["ADDRESS_2"] = lines[1][:140]
                    if "ADDRESS_1" in missing:
                        missing.remove("ADDRESS_1")
                    reason = (reason or "llm classification") + " + address recovery"
                    _LOG.info(
                        "Smart-paste TEXT: address recovered via re-prompt"
                    )
        except Exception as e:
            _LOG.warning("Text address recovery failed: %s", e)

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
        # Defensive: if the LLM copied our template placeholder
        # verbatim (e.g. "<customer name in English>", "<6 digits only>")
        # treat it as missing. Without this, the broken value would leak
        # into the Orders list and Google Sheet.
        if val and "<" in val and ">" in val:
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
    """Map the 15-line schema keys onto the field names the rest of the
    app (Shipment model) already uses. Also splits compound phone values
    like "9876543210 / 9988776655" into primary + alternative.
    """
    phone_raw = ai_fields.get("PHONE", "") or ""
    alt_raw = ai_fields.get("ALT_PHONE", "") or ""

    primary, alt = _split_compound_phone(phone_raw)
    # If the LLM already put a value in ALT_PHONE, prefer that over our
    # best-effort split.
    if alt_raw.strip():
        alt = alt_raw

    return {
        "customer_name":  ai_fields.get("NAME", ""),
        "customer_phone": primary,
        "customer_alt_phone": alt,
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


_PHONE_DIGITS_RE = re.compile(r"\d{10,}")


def _split_compound_phone(value: str) -> Tuple[str, str]:
    """Return (primary, alternative) from a free-form phone string.

    Handles inputs like:
      "8128949387 / 7874786098"          → ("8128949387", "7874786098")
      "+91-98765 43210, 9988776655"      → ("9876543210",  "9988776655")
      "call 98765 43210 or 99887 76655"  → ("9876543210",  "9988776655")
      "9876543210"                       → ("9876543210",  "")
    """
    if not value:
        return ("", "")
    # Strip +91/91 prefix then pull sequences of 10+ digits.
    cleaned = re.sub(r"(?:\+?91)", "", value)
    digits_only = re.sub(r"\D", " ", cleaned)
    parts = [p for p in digits_only.split() if len(p) >= 10]
    if not parts:
        # Still return the original if it looks like a partial number,
        # so the regex parser can complain about missing digits later.
        compact = re.sub(r"\D", "", value)
        return (compact, "")
    primary = parts[0][-10:]
    alt = parts[1][-10:] if len(parts) > 1 else ""
    return (primary, alt)



# ---- Photo / Image OCR (Gemini Vision) ---------------------------------

_VISION_MODEL = os.getenv("SMART_PASTE_VISION_MODEL", "gemini-2.5-pro")
_VISION_PROVIDER = os.getenv("SMART_PASTE_VISION_PROVIDER", "gemini")
_VISION_TIMEOUT = float(os.getenv("SMART_PASTE_VISION_TIMEOUT", "20.0"))

# Vision-specific prompt — same 15-line schema, extra rules for image OCR.
DEFAULT_VISION_PROMPT = (
    DEFAULT_SHIPBOT_PROMPT
    + """

## EXTRA RULES FOR IMAGE INPUTS (handwritten paper, visiting card, screenshot, packing slip, ID card, ANYTHING):

1. Read EVERY language present — Gujarati, Hindi, Marathi, English. Keep
   each text in its ORIGINAL script. Do NOT transliterate.
2. PHONE NUMBERS:
   - If the image shows MULTIPLE phone numbers, take the FIRST TWO
     (in reading order, top-to-bottom, left-to-right).
   - First → PHONE, second → ALT_PHONE.
   - Strip +91 / 0 prefixes; keep last 10 digits only.
3. NAME RULES:
   - Prefer a person's name. If only a SHOP NAME is visible (no person),
     use the shop name as NAME.
   - If both shop name + person are visible, prefer the person's name
     and append the shop name into ADDRESS_1 (e.g.
     "M/s Mahek Creations, Shop 12, …").
4. ADDRESS:
   - Combine all address-like text into ADDRESS_1 and ADDRESS_2.
   - Treat any trailing landmark / "near …" / "behind …" line as ADDRESS_2.
5. PINCODE:
   - Always pick the 6-digit number that LOOKS like a postal code
     (often near the end of the address, often after city/state).
   - If only 5 or fewer digits are visible, leave PINCODE blank.
6. AMOUNT / COD / PAID:
   - Words like "Cash", "COD", "Cash On Delivery" → PAYMENT: COD.
   - Words like "Paid", "Online", "Advance Paid" → PAYMENT: PAID.
   - If you can't tell, default PAYMENT: COD.
7. ITEMS:
   - Pull product / SKU words. Append "x QTY" if quantity is visible.
8. NOISE FILTER:
   - Ignore decorative text (logos, slogans, watermarks).
   - Ignore phone-number listings that are clearly the SHOP's helpline
     (printed at the top of a visiting card) — but if those are the
     only numbers visible, still use them.
9. NEVER invent missing values — leave them as `-`.
"""
)


async def parse_image_with_ai(
    *,
    image_base64: str,
    mime: str,
    custom_instructions: str = "",
) -> Dict[str, Any]:
    """Vision parse — accepts a base64 image (no data: prefix) and returns
    the same shape as parse_with_ai().

    Pipeline:
      1. Build vision-specific system prompt.
      2. Send to Gemini with the base64 attachment.
      3. Parse the same 15-line schema block + complexity tag.
      4. Return {fields, missing, complexity, ai_reason, raw, source}.

    On any error we return an empty result with source='fallback' so the
    caller can decide whether to surface the error or recover.
    """
    if not image_base64:
        return _empty_result(source="fallback")
    if not _LLM_KEY:
        return _empty_result(source="fallback")

    # Sanity-check the MIME type — Gemini supports JPEG/PNG/WEBP.
    mime = (mime or "").lower().strip() or "image/jpeg"
    if mime not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
        # Coerce odd MIME types onto JPEG (the most common camera output).
        mime = "image/jpeg"

    system = DEFAULT_VISION_PROMPT
    if custom_instructions.strip():
        system = (
            "## USER CUSTOMISATION (honour these first when not conflicting "
            "with the output format rules below):\n"
            + custom_instructions.strip()
            + "\n\n-- BASE RULES --\n"
            + DEFAULT_VISION_PROMPT
        )

    try:
        from emergentintegrations.llm.chat import (
            LlmChat, UserMessage, ImageContent,
        )
        chat = (
            LlmChat(
                api_key=_LLM_KEY,
                # Use a hash of the base64 head as session id so repeated
                # uploads of the same image hit the same session bucket.
                session_id=f"smart-paste-photo-{abs(hash(image_base64[:200]))}",
                system_message=system,
            )
            .with_model(_VISION_PROVIDER, _VISION_MODEL)
        )
        msg = UserMessage(
            text=(
                "Read this image and extract the shipment / address details "
                "into the strict 15-line schema. Then add the JSON complexity "
                "tag at the end."
            ),
            file_contents=[ImageContent(image_base64=image_base64)],
        )
        raw = await asyncio.wait_for(
            chat.send_message(msg), timeout=_VISION_TIMEOUT,
        )
    except Exception as e:
        _LOG.warning("Smart-paste VISION failed (%s) — caller should fall back", e)
        return _empty_result(source="fallback")

    fields, missing = _parse_schema_block(raw)
    complexity, reason = _parse_complexity_block(raw)
    # Photo OCR is ALWAYS billed as 'complex' regardless of model output —
    # vision calls cost more and we want a predictable price for users.
    if complexity != "complex":
        complexity = "complex"
        reason = (reason or "vision call") + " (photo OCR billed as complex)"

    # ── Address-recovery fallback (Phase-5d patch) ───────────────────
    # Gemini sometimes returns ADDRESS_1 = "-" even when there's a clearly
    # visible street/house line in the image — most common on screenshots
    # of e-commerce order screens where city/state/pincode appear after
    # the street and the model thinks the locality has covered it.
    # If we got CITY or PINCODE but ADDRESS_1 is blank, re-prompt with a
    # tightly scoped "address only" question. Costs one extra call only
    # on bad cases — typical happy path runs zero retries.
    addr1 = (fields.get("ADDRESS_1") or "").strip()
    has_locality = bool(
        (fields.get("CITY") or "").strip()
        or (fields.get("PINCODE") or "").strip()
    )
    if (not addr1 or addr1 == "-") and has_locality:
        try:
            from emergentintegrations.llm.chat import (
                LlmChat as _Chat2, UserMessage as _UM2, ImageContent as _IC2,
            )
            chat2 = (
                _Chat2(
                    api_key=_LLM_KEY,
                    session_id=f"smart-paste-photo-fix-{abs(hash(image_base64[:200]))}",
                    system_message=(
                        "You are an OCR helper. The user image contains "
                        "a CUSTOMER SHIPPING ADDRESS. Output ONLY the "
                        "street / house / apartment / building / area / "
                        "locality / landmark portion — i.e. everything "
                        "EXCEPT the city, state, pincode, phone, name "
                        "and order id. Keep the original script. Use one "
                        "or two short lines, comma-separated. No labels, "
                        "no explanation, no markdown. If there is "
                        "genuinely no street/area text in the image, "
                        "return a single dash `-`."
                    ),
                )
                .with_model(_VISION_PROVIDER, _VISION_MODEL)
            )
            msg2 = _UM2(
                text="Address (street / house / area / landmark) only:",
                file_contents=[_IC2(image_base64=image_base64)],
            )
            recovered = await asyncio.wait_for(
                chat2.send_message(msg2), timeout=_VISION_TIMEOUT,
            )
            recovered = (recovered or "").strip()
            recovered = re.sub(r"^```[a-z]*\s*", "", recovered)
            recovered = re.sub(r"\s*```$", "", recovered)
            recovered = re.sub(
                r"^(address[_\s-]*1?\s*:\s*)", "", recovered, flags=re.I,
            )
            if recovered and recovered != "-":
                lines = [
                    ln.strip(" ,;-")
                    for ln in recovered.splitlines() if ln.strip()
                ]
                if lines:
                    fields["ADDRESS_1"] = lines[0][:140]
                    if len(lines) > 1 and not (fields.get("ADDRESS_2") or "").strip():
                        fields["ADDRESS_2"] = lines[1][:140]
                    if "ADDRESS_1" in missing:
                        missing.remove("ADDRESS_1")
                    reason = (reason or "vision call") + " + address recovery"
                    _LOG.info(
                        "Smart-paste photo: address recovered via re-prompt"
                    )
        except Exception as e:
            _LOG.warning("Address recovery re-prompt failed: %s", e)

    return {
        "fields": fields,
        "missing": missing,
        "complexity": complexity,
        "ai_reason": reason,
        "raw": raw,
        "source": "llm",
    }
