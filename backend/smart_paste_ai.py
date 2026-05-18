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

# The 18-line schema we ALWAYS return (hidden from users).
# Phase-3 Smart Paste enhancement added EMAIL + GSTIN at the tail so
# the LLM doesn't have to re-learn the order — just two extra fields.
SCHEMA_FIELDS: List[str] = [
    "NAME", "PHONE", "ALT_PHONE", "ADDRESS_1", "ADDRESS_2", "CITY", "STATE",
    "PINCODE", "ITEMS", "AMOUNT", "PAYMENT", "TOKEN",
    "COURIER", "ORDER_ID", "WEIGHT", "NOTES",
    "EMAIL", "GSTIN",
]

# Default ShipBot-style system prompt bundled with the app. Users can
# prepend/override their own instructions via Settings.smart_paste_instructions.
DEFAULT_SHIPBOT_PROMPT = """\
You are "ShipBot" — a strict Shipment Data Parser for a courier app.
You process WhatsApp messages in Gujarati / Hindi / English and extract
shipment details into a fixed 18-line structured block.

CRITICAL: Every line must be a real parsed value OR a single dash `-`.
NEVER output template placeholders like "<customer name>", "<phone>",
"<6 digits>", "<real customer name, keep original script>" or anything
that starts with `(` and ends with `)`. Those are descriptions, NOT
values. If the input text doesn't contain the info, write `-` only.

OUTPUT FORMAT (STRICT) — return ONE code block with EXACTLY 18 lines,
in this exact order, with no emoji / no explanation / no extra lines.
Each value MUST be the actual data from the input or `-`. The `<…>`
placeholders below are descriptions only and MUST NEVER appear in
your output:

NAME: <real customer name, keep original script — Gujarati stays Gujarati, English stays English>
PHONE: <primary 10 digits only, strip +91 or 0>
ALT_PHONE: <second / alternative 10-digit number if message has TWO phones, else ->
ADDRESS_1: <FULL physical address — house / street / area / landmark / village / taluka / district — all on one line, NEVER split. Keep original script.>
ADDRESS_2: <ALWAYS leave blank / output `-`. Do NOT use this field.>
CITY: <city / town — keep original script>
STATE: <state — keep original script>
PINCODE: <6 digits only>
ITEMS: <item + quantity like "Saree x 2"; comma-separated for multiple>
AMOUNT: <number only, no ₹ symbol>
PAYMENT: <COD or PAID — leave blank if not stated>
TOKEN: <token / advance / partial-paid amount — number only, no ₹ symbol; `-` if not present>
COURIER: <courier name or ->
ORDER_ID: <order number or ->
WEIGHT: <ALWAYS leave as `-`. NEVER infer parcel weight from item name.>
NOTES: <special instruction or ->
EMAIL: <customer email address (lowercase, no spaces) — only if a clear email is visible in the input, else ->
GSTIN: <15-character Indian GST number (2 digits + 5 letters + 4 digits + 1 letter + 1 alphanumeric + Z + 1 alphanumeric) in UPPERCASE — only if explicitly present, else ->

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
AMOUNT: 1500
PAYMENT: COD
TOKEN: -
COURIER: -
ORDER_ID: -
WEIGHT: -
NOTES: -
EMAIL: -
GSTIN: -
```

EXAMPLE of a GOOD response when NO payment info is mentioned (only
address + name was provided — leave AMOUNT and PAYMENT blank):
```
NAME: Dr. Kagathara
PHONE: 7405304899
ALT_PHONE: -
ADDRESS_1: C-25, Prarambh Complex, Nr. Parivar Char Rasta, Waghodhiya Road
ADDRESS_2: -
CITY: Vadodara
STATE: Gujarat
PINCODE: 390019
ITEMS: -
AMOUNT: -
PAYMENT: -
TOKEN: -
COURIER: -
ORDER_ID: -
WEIGHT: -
NOTES: -
EMAIL: -
GSTIN: -
```

EXAMPLE of a GOOD response when input has BOTH a COD amount AND a
token already paid (Gujarati / mixed-script message):
```
NAME: GREY GENTS
PHONE: 7575848410
ALT_PHONE: 7777978550
ADDRESS_1: 20 "Dev Atelier", Nr RK Enterprise, Hiran Circle, Ramdevnagar Road, Prahladnagar, Ahmedabad, 380015 Gujarat
ADDRESS_2: -
CITY: Ahmedabad
STATE: Gujarat
PINCODE: 380015
ITEMS: 3 Kg Natural Honey
AMOUNT: 1750
PAYMENT: COD
TOKEN: 50
COURIER: -
ORDER_ID: -
WEIGHT: -
NOTES: -
EMAIL: -
GSTIN: -
```

EXAMPLE of a GOOD response for a B2B order with email + GSTIN:
```
NAME: Mahek Creations
PHONE: 9988776655
ALT_PHONE: -
ADDRESS_1: Shop 12, Ratan Industrial Estate, Odhav, Ahmedabad, 382415 Gujarat
ADDRESS_2: -
CITY: Ahmedabad
STATE: Gujarat
PINCODE: 382415
ITEMS: Cotton Sarees x 50
AMOUNT: 75000
PAYMENT: PAID
TOKEN: -
COURIER: -
ORDER_ID: PO-2026-0042
WEIGHT: -
NOTES: -
EMAIL: orders@mahekcreations.in
GSTIN: 24ABCDE1234F1Z5
```

RULES:

**Rule 1 — No angle brackets:**
NEVER output angle brackets `<` or `>` in a value. They are only used
as description markers in this prompt — a value must be a real word
or the single character `-`.

**Rule 2 — Digit normalisation:**
Convert Gujarati (૧૨૩) & Hindi (१२३) digits to English (123).

**Rule 3 — Missing fields:**
If any field is missing or unclear → write EXACTLY: `-`. NEVER guess,
invent or assume data.

**Rule 4 — Two phone numbers:**
When two phone numbers appear (e.g. "8128949387 / 7874786098" or
"call 98765 43210 or 99887 76655"), the FIRST goes in PHONE and the
SECOND goes in ALT_PHONE.

**Rule 5 — Items vs Address separation:**
ITEMS MUST NEVER appear in ADDRESS fields. Products (saree, kurti,
dress, shoes, toy, book, ODC, gm/kg weight codes, etc.) always go
in ITEMS — NEVER in ADDRESS_1 or ADDRESS_2.

**Rule 6 — Quantity rules for ITEMS:**
  * "Saree 2 pcs" → "Saree x 2"
  * "Saree 2" → "Saree x 2"
  * "2 saree" → "Saree x 2"
  * "Saree" (no qty mentioned) → "Saree x 1"
  * Multiple items: "Saree x 2, Kurti x 1"
  * A weight-only product code like "20gm ODC3" → "ODC3 x 1"
    (KEEP the weight as part of the item name, e.g. "ODC3 20gm x 1")
  * IMPORTANT: a product weight like "20gm", "500g", "3 kg" baked
    into the item name is the PRODUCT'S OWN weight. It is NOT the
    parcel's shipping weight. Do NOT copy it into WEIGHT.

**Rule 6b — PARCEL WEIGHT IS NEVER GUESSED:**
WEIGHT is the dispatch/parcel weight (= product + box + bubble wrap).
It can ONLY be filled if the user EXPLICITLY states a parcel /
shipping / dispatch weight using clear words like:
  - "parcel weight: 500g"
  - "shipping weight 1.2 kg"
  - "dispatch weight - 2 kg"
  - "package: 750gm"
  - "weight (incl. box): 800gm"
NEVER infer WEIGHT from any of these:
  ❌ Item name ("3 Kg Natural Honey", "ODC3 20gm")
  ❌ Standalone product weight on its own line ("20gm")
  ❌ Quantity numbers ("Saree x 2")
  ❌ Pricing tiers ("500g pack")
If the message does not contain a clearly-labelled PARCEL/SHIPPING/
DISPATCH/PACKAGE weight, output `WEIGHT: -` (single dash). The user
will be prompted to enter the actual parcel weight on the form.

Examples:
  Input: "3 Kg Natural Honey, Qty 1"
    →  WEIGHT: -            (3 kg is the product's own weight)
        ITEMS:  3 Kg Natural Honey x 1
  Input: "ODC3 20gm x 1"
    →  WEIGHT: -            (20gm is part of the SKU code)
        ITEMS:  ODC3 20gm x 1
  Input: "Saree x 2, Parcel weight: 800g"
    →  WEIGHT: 800g         (explicit "Parcel weight" label)
        ITEMS:  Saree x 2
  Input: "Kurti, dispatch wt 1.2 kg"
    →  WEIGHT: 1.2 kg       (explicit "dispatch wt" label)
        ITEMS:  Kurti x 1

**Rule 7 — ADDRESS_1 content:**
ADDRESS_1 holds the COMPLETE physical address — house no / flat /
street / area / colony / village / post / taluka / district /
landmark — ALL of it together on ONE line, separated by commas.
NEVER products, quantity, or amount.

**Rule 8 — CRITICAL ADDRESS RULE: never blank, NEVER split, MOVE the trio out:**
NEVER LEAVE ADDRESS_1 EMPTY IF THE INPUT CONTAINS ANY ADDRESS-LIKE
TEXT. NEVER use ADDRESS_2 — leave it as `-`.
  * If the input has a label like "Shipping address" / "Delivery
    address" / "Address" / "પત્તો" / "पता", treat the WHOLE multi-
    line block that follows (until you hit phone / email / name /
    order id / payment) as the address block.
  * MOVE (do NOT copy) the trailing CITY + STATE + PINCODE into
    their own dedicated fields. They MUST BE REMOVED from
    ADDRESS_1 — those three values live ONLY in the CITY / STATE /
    PINCODE columns. The courier-label renderer composes the
    printable footer line "<city>, <state> - <pincode>" from those
    dedicated fields, so duplicating them inside ADDRESS_1 prints
    the same trio twice on the sticker.
  * ADDRESS_1 = the physical address MINUS the trailing
    city/state/pincode trio (house, street, apartment, area,
    landmark, locality, road) joined with ", " in the same order
    they appear in the source. DO NOT split into ADDRESS_2.
  * Example of the correct behaviour (city + state + pincode
    REMOVED from ADDRESS_1):
      Input  : "Shipping address: C-401 Venus Apartment, near
                Sainik Vihar Saraswati Vihar, Rani Bagh, Pitampura,
                Delhi, 110034 Delhi"
      WRONG  : ADDRESS_1: C-401 Venus Apartment, near Sainik Vihar,
                          Saraswati Vihar, Rani Bagh, Pitampura,
                          Delhi, 110034 Delhi
               (city / state / pincode duplicated inside ADDRESS_1)
      RIGHT  : ADDRESS_1: C-401 Venus Apartment, near Sainik Vihar,
                          Saraswati Vihar, Rani Bagh, Pitampura
               ADDRESS_2: -
               CITY: Delhi  STATE: Delhi  PINCODE: 110034
               (city / state / pincode MOVED into their own fields
                and STRIPPED from ADDRESS_1.)
  * If the same word (e.g. "Delhi") appears TWICE — once as a
    neighbourhood name and once as the city — keep only the
    NEIGHBOURHOOD occurrence inside ADDRESS_1, and put the trailing
    CITY occurrence into the CITY field.
  * "near …" / "behind …" / "opp …" landmarks → stay INSIDE
    ADDRESS_1, separated by ", ".
  * Flat/house numbers (C-401, B/12, 3rd floor, Room 5, Plot 22
    etc.) ALWAYS belong in ADDRESS_1.

**Rule 9 — City / State translation:**
City / State should be English transliteration when obvious (e.g.
"અરવલ્લી" → "Aravalli", "ગુજરાત" → "Gujarat") so the courier sheet
is consistent; otherwise keep the source script.

**Rule 10 — Amount & token (CRITICAL):**
AMOUNT = the COD / total order amount (the BIG number after ₹/Rs/INR
on the same line as Payment / COD / Cash on Delivery / Total).
  * Examples:
      "💰 Payment: COD ₹1750"          → AMOUNT: 1750, PAYMENT: COD
      "Cash on delivery — Rs. 2400"    → AMOUNT: 2400, PAYMENT: COD
      "Total: 999 / Paid online"       → AMOUNT: 999,  PAYMENT: PAID
      "₹1750 COD"                      → AMOUNT: 1750, PAYMENT: COD
  * If the input has BOTH a COD/Total amount AND a Token / Advance,
    the LARGER number is the AMOUNT, the SMALLER is the TOKEN.

TOKEN = the advance / partial amount the customer ALREADY paid online.
Look for any of these signals (case-insensitive, may be in Gujarati / Hindi):
    "Token", "Tokn", "advance", "advance paid",
    "ઍડ્વાન્સ", "આગોતરા", "ટોકન", "टोकन", "अग्रिम"
Examples:
    "50 tokn"          → TOKEN: 50
    "Token ₹100"       → TOKEN: 100
    "advance 200"      → TOKEN: 200
    "ટોકન 300"         → TOKEN: 300
TOKEN is a NUMBER ONLY (just the digits). NEVER copy "tokn" / "token"
text into TOKEN — only the number.
TOKEN is its OWN field. DO NOT put token text into NOTES anymore.

If both AMOUNT and TOKEN are present, the customer still owes
(AMOUNT − TOKEN) on COD, but the AI's job is just to extract both
numbers verbatim — do NOT do any subtraction.

**Rule 11 — Payment field:**
PAYMENT: COD only if "COD/Cash on Delivery" is mentioned;
PAID only if "Paid/Online/UPI/Prepaid" is mentioned;
LEAVE BLANK (`-`) if no payment info is present in the input.
DO NOT guess. The user will set it later if needed.

**Rule 12 — ITEMS: capture FULL product description verbatim:**
  * Include quantity, weight, size, colour, material — DO NOT
    shorten to a single word.
  * Examples:
      Input  : "તમારો ઓર્ડર: 3 Kg Natural Honey"
      WRONG  : ITEMS: Honey
      RIGHT  : ITEMS: 3 Kg Natural Honey
      Input  : "Order: 2 Cotton Sarees Red Free-size"
      WRONG  : ITEMS: Saree
      RIGHT  : ITEMS: 2 Cotton Sarees Red Free-size x 2
      Input  : "1 ODC3 Drone Kit + 5 spare batteries"
      RIGHT  : ITEMS: ODC3 Drone Kit x 1, Spare Battery x 5
  * Multiple distinct products → comma-separate, each with its own
    "x QTY" suffix when quantity is known.
  * If the input has a label like "ઓર્ડર / Order / Items / Product",
    copy EVERYTHING after that label (until you hit a different
    field) into ITEMS.

**Rule 13 — MOVE the trailing city/state/pincode OUT of ADDRESS_1:**
There is a SINGLE address line (ADDRESS_1). Capacity is up to
~280 characters. ADDRESS_2 must always be `-`.
NEVER drop any visible street/area/landmark text from ADDRESS_1 —
but DO strip the trailing CITY / STATE / PINCODE trio so each
value lives in EXACTLY ONE field.
  * Procedure when the address block has 3+ comma-separated parts:
      1. Last 2-3 parts (city, state, pincode) → MOVE into CITY /
         STATE / PINCODE (not parallel; an EXTRACT-and-REMOVE).
      2. Remaining earlier parts → ADDRESS_1, joined by ", ",
         in original order. Do NOT split into ADDRESS_2.
  * Example showing the bug to AVOID and the correct behaviour:
      Input  : "20 \"Dev Atelier\", Nr RK Enterprise, Hiran Circle,
                Ramdevnagar Road, Prahladnagar, Ahmedabad,
                380015 Gujarat"
      WRONG #1: ADDRESS_1: 20 "Dev Atelier", Nr RK Enterprise
                ADDRESS_2: Hiran Circle, Ramdevnagar Road
                (splitting into TWO lines is FORBIDDEN.)
      WRONG #2: ADDRESS_1: 20 "Dev Atelier", Nr RK Enterprise,
                           Hiran Circle, Ramdevnagar Road,
                           Prahladnagar, Ahmedabad, 380015 Gujarat
                (city / state / pincode duplicated inside ADDRESS_1
                 — they belong ONLY in CITY/STATE/PINCODE.)
      RIGHT   : ADDRESS_1: 20 "Dev Atelier", Nr RK Enterprise,
                           Hiran Circle, Ramdevnagar Road,
                           Prahladnagar
                ADDRESS_2: -
                CITY: Ahmedabad  STATE: Gujarat  PINCODE: 380015
                (city / state / pincode MOVED out of ADDRESS_1 into
                 their dedicated fields — single source of truth.)
  * If the joined ADDRESS_1 would still exceed 280 chars (very rare),
    truncate trailing duplicates only — do NOT move parts into
    ADDRESS_2.
  * "Near / Opp / Behind / Landmark / etc." stay INSIDE ADDRESS_1,
    in their original position separated by ", ".

**Rule 14 — NAME: combine PERSON + SHOP name when both are visible:**
  * Prefer a person's name when present.
  * If BOTH a person name AND a shop/business/company name are visible
    in the input, OUTPUT the combined form:
        "<Person Name> - <Shop Name>"
    The hyphen-separated form lets the operator instantly tell who at
    which shop placed the order. Examples:
      Person="Bharatbhai", Shop="Tulsi Sports"
        → NAME: Bharatbhai - Tulsi Sports
      Person="Asari Nikunj", Shop="Asari Industries"
        → NAME: Asari Nikunj - Asari Industries
      Person="Dr. Kagathara", Shop="Kagathara Clinic"
        → NAME: Dr. Kagathara - Kagathara Clinic
  * If ONLY a shop / business / company name is visible (no
    personal name anywhere in the input), put the SHOP NAME into
    NAME. NEVER leave NAME blank when a shop/business name is
    clearly identifiable. Examples:
      "GREY GENTS"           → NAME: GREY GENTS
      "Mahek Creations"      → NAME: Mahek Creations
      "Balaji Developers"    → NAME: Balaji Developers
      "Iscon Balaji M/s"     → NAME: Iscon Balaji
  * If ONLY a person name is visible (no shop/business mentioned),
    use just the person name in NAME (no hyphen, no shop suffix).
  * Trim and de-duplicate: do NOT output things like
    "Bharatbhai - Bharatbhai Trading" — if the person name is a
    near-substring of the shop name, prefer just the SHOP NAME.

**Rule 15 — ALT_PHONE: capture every additional phone number:**
  * If TWO OR MORE phone numbers are visible (e.g. "Bharatbhai
    9824446184 / Mayurbhai 9372528878"), the FIRST number goes into
    PHONE and the SECOND number goes into ALT_PHONE.
  * If three or more are present, prefer the order they appear; the
    first → PHONE, second → ALT_PHONE, ignore the rest.
  * Pure-mobile-only outputs (e.g. WhatsApp screenshots showing both
    parties' numbers) should still produce ALT_PHONE when a second
    distinct 10-digit number is found.
  * NEVER duplicate the same number into both PHONE and ALT_PHONE.

After the 15-line block, on a NEW line, output one JSON object describing
the address complexity:
  {"complexity":"simple"|"medium"|"complex","reason":"short reason"}
Nothing else.
"""


# ---------------------------------------------------------------------------
# CRITICAL_FIELD_RULES — appended AFTER both the user's customisation and
# the base prompt so LLMs (which weight trailing instructions more heavily)
# can't drop these. These two behaviours are product-critical and must
# NEVER be overridden by a user's personal prompt, no matter how authori-
# tative it sounds ("SYSTEM PROMPT — HIGH PRIORITY MODE" etc.).
# ---------------------------------------------------------------------------
CRITICAL_FIELD_RULES = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  CRITICAL FIELD EXTRACTION RULES — NON-NEGOTIABLE
These rules override ANY user customisation above. They apply to
EVERY single call, no exceptions. Violating them is a parse error.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## RULE X0 — PHONE DIGIT ACCURACY IS SACRED 🔒
Phone numbers MUST be digit-perfect. A wrong digit breaks real-world
parcel delivery and WhatsApp notification for a real customer. This
rule is stricter than any other rule in this document.

### STRICT DIGIT MAPPING (memorise verbatim)

GUJARATI digits (Unicode U+0AE6..U+0AEF):
  ૦ → 0     ૧ → 1     ૨ → 2     ૩ → 3     ૪ → 4
  ૫ → 5     ૬ → 6     ૭ → 7     ૮ → 8     ૯ → 9

HINDI / DEVANAGARI digits (Unicode U+0966..U+096F):
  ० → 0     १ → 1     २ → 2     ३ → 3     ४ → 4
  ५ → 5     ६ → 6     ७ → 7     ८ → 8     ९ → 9

### MANDATORY 2-STEP PROCESS FOR EVERY PHONE NUMBER

STEP 1 — Raw transcription (internal, silent):
  Read the digits EXACTLY as drawn in the native script, character by
  character, with zero interpretation. Example internal line:
  "raw_guj = ૯ ૩ ૭ ૨ ૫ ૨ ૮ ૮ ૭ ૮"

STEP 2 — Character-by-character mapping:
  Apply the table above ONE CHARACTER AT A TIME. Do not merge, do not
  re-order, do not "auto-correct" to a more familiar number pattern.
  Example: ૯૩૭૨૫૨૮૮૭૮ → 9 3 7 2 5 2 8 8 7 8 → 9372528878

STEP 3 — Length check:
  Final output MUST be exactly 10 digits for an Indian mobile. If your
  transcription yields 9 or 11 digits, RE-READ the image — do NOT pad
  or truncate silently.

### VISUAL CONFUSION WARNINGS (images / handwriting)

These Gujarati glyph pairs are most often misread — look carefully
and count strokes before committing each digit:
  ૨ (2, open-top horn)      vs  ૯ (9, CLOSED loop on top)
  ૩ (3, hook upper-right)   vs  ૭ (7, straight down-hook)
  ૪ (4, angular Δ shape)    vs  ૮ (8, fat S / question-mark shape)
  ૧ (1, small vertical)     vs  ૭ (7, longer down-hook)
  ૭ (7, hook opens DOWN)    vs  ૪ (4, closed triangle on left)
  ૭ (7)                     vs  ૧ (1) — HIGH CONFUSION in small print
  ૪ (4, has closed loop)    vs  ૧ (1, plain stroke, NO loop)
  ૦ (0, circle)             vs  ૯ (9, circle + tail)
  ૫ (5, pentagon-ish)       vs  ૬ (6, open top)
  ૬ (6, open top)           vs  ૭ (7, down-hook)
  ૦ (0)                     vs  ૦૦ — two zeros in a row are common
                                      at end of numbers; keep both

These Hindi / Devanagari glyph pairs are most often misread:
  २ (2) vs ३ (3) vs ७ (7)   — all share a curled opening, count strokes
  ४ (4)         — has a closed left loop, NOT an 8
  ५ (5) vs ६ (6) — ५ is open-bottomed, ६ has a filled top
  ० (0) vs ९ (9) — ९ has a small tail/leg, ० is a pure circle
  १ (1) vs ७ (7) — ७ has the full curly top, १ is a vertical stroke

### ⚠️ SECOND-NUMBER RIGOR (applies when there are TWO numbers)
When a card / image shows TWO phone numbers stacked vertically
(main + alternate), the SECOND number is as important as the FIRST.
Do NOT relax attention on the second line just because you already
read the first. Re-apply the full 2-step process (transcribe →
map → length-check) independently to the alt number. Wrong
ALT_PHONE = package still mis-delivered. The user reported
repeated failures where the first number was correct but the
alternate was hallucinated — do NOT repeat this mistake.

### ABSOLUTE PROHIBITIONS (phone field only)

- ❌ NEVER "translate phonetically" (do not map ૨ to "bay" or any sound).
- ❌ NEVER auto-correct a digit because the resulting number "looks
     unusual" or "doesn't match a common pattern".
- ❌ NEVER substitute the customer's number with a number that appeared
     elsewhere (shop helpline, sender's own number, printed letterhead).
- ❌ NEVER hallucinate missing digits. If the image is blurry, smudged,
     partially cropped, or only 9 digits are legible, output PHONE: "-"
     and explain in AI_REASON. A dash is ALWAYS better than a wrong
     digit.
- ❌ NEVER mix scripts inside ONE number. If the first 5 digits are
     Gujarati and the last 5 are Arabic, transcribe each group using
     ITS OWN script's mapping.

### EXAMPLES — CORRECT vs WRONG

Input: "મયુરભાઈ ૯૩૭૨૫૨૮૮૭૮"
  ✅ PHONE: 9372528878       (character-by-character map)
  ❌ PHONE: 9428528878       (hallucinated — ૩૭ became ૪૨)
  ❌ PHONE: 9372528800       (truncated / wrong last two)

Input: "Call ९८७६५४३२१०"
  ✅ PHONE: 9876543210
  ❌ PHONE: 9876543200       (dropped १)

Input handwritten faint: "૯ ૩ ૭ ? ૫ ૨ ૮ ૮ ૭ ૮"   (4th digit unclear)
  ✅ PHONE: -                (with AI_REASON: "4th digit illegible")
  ❌ PHONE: 9370528878       (guessed)

## RULE X1 — ALT_PHONE (alternate mobile) capture is MANDATORY
If the input contains TWO or more distinct 10-digit mobile numbers
(ignoring spaces, hyphens, +91 country codes, and Gujarati/Hindi
numerals) — you MUST populate BOTH:
  PHONE:     <first number seen, normalised to 10 digits>
  ALT_PHONE: <second number seen, normalised to 10 digits>

Both PHONE and ALT_PHONE must be obtained using the STRICT RULE X0
process above. Never share digits between the two numbers.

Examples (input → expected PHONE / ALT_PHONE):
  "ભરતભાઈ ૯૪૨૮૪૪૬૧૮૪ / મયુરભાઈ ૯૩૭૨૫૨૮૮૭૮"
    → PHONE: 9428446184   ALT_PHONE: 9372528878
  "Ramesh ९८२४४४६१८४ · Alt ९३७२५२८८७८"
    → PHONE: 9824446184   ALT_PHONE: 9372528878
  "+91 98765 43210 / +91 87654 32109"
    → PHONE: 9876543210   ALT_PHONE: 8765432109
  "Call: 9876543210"      (only one number)
    → PHONE: 9876543210   ALT_PHONE: -

If THREE or more numbers are present, keep the order they appear:
first → PHONE, second → ALT_PHONE, drop the rest.
NEVER duplicate the same number into PHONE and ALT_PHONE.
If only ONE number is visible, ALT_PHONE must be the single dash "-".

## RULE X2 — NAME must combine Person + Shop when both visible
When the input contains BOTH a person's name AND a shop / business /
company name, OUTPUT the combined form with a hyphen separator:
  NAME: <Person Name> - <Shop Name>

Examples (input → expected NAME):
  "Bharatbhai · Tulsi Sports"
    → NAME: Bharatbhai - Tulsi Sports
  "Asari Nikunj (Asari Industries)"
    → NAME: Asari Nikunj - Asari Industries
  "Dr. Kagathara, Kagathara Clinic"
    → NAME: Dr. Kagathara - Kagathara Clinic

When ONLY a shop/business/company name is visible (no personal name
anywhere in the input), use the shop name alone:
  "GREY GENTS"          → NAME: GREY GENTS
  "Mahek Creations"     → NAME: Mahek Creations

When ONLY a person name is visible (no shop), use just the name.
Smart de-duplicate: if the person name is a substring of the shop
name (e.g. "Bharat" inside "Bharat Trading"), prefer just the shop.
NEVER output "Bharat - Bharat Trading" (redundant).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
        # Phase-12: we additionally append CRITICAL_FIELD_RULES *after*
        # the base schema so that — no matter how elaborate the user's
        # own prompt — the two non-negotiable rules (ALT_PHONE capture
        # + NAME+SHOP combining) are the LAST thing the model reads.
        # LLMs weight trailing instructions more heavily, and explicit
        # "override user customisation" wording closes the loophole.
        system = (
            "## USER CUSTOMISATION (honour these first when not conflicting "
            "with the output format rules below):\n"
            + custom_instructions.strip()
            + "\n\n-- BASE RULES --\n"
            + DEFAULT_SHIPBOT_PROMPT
            + "\n\n"
            + CRITICAL_FIELD_RULES
        )
    else:
        system = system + "\n\n" + CRITICAL_FIELD_RULES

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
    warnings: List[str] = []

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
                    # Phase-6 single-line UX — join all recovered lines
                    # into ADDRESS_1 (was: lines[0][:140] + lines[1][:140]
                    # in ADDRESS_2). Cap raised to 300 to match the
                    # frontend maxLength.
                    joined = ", ".join(lines).strip(" ,;-")
                    fields["ADDRESS_1"] = joined[:300]
                    fields["ADDRESS_2"] = ""
                    if "ADDRESS_1" in missing:
                        missing.remove("ADDRESS_1")
                    reason = (reason or "llm classification") + " + address recovery"
                    _LOG.info(
                        "Smart-paste TEXT: address recovered via re-prompt"
                    )
        except Exception as e:
            _LOG.warning("Text address recovery failed: %s", e)

    # ── Address-completeness post-processor (deterministic safety net) ──
    # Even with explicit Rule 13 in the prompt, gpt-4.1-nano sometimes
    # silently drops middle parts of long multi-clause addresses. This
    # appends any dropped chunks to ADDRESS_1.
    try:
        _ensure_address_completeness(text, fields, warnings)
    except Exception as e:
        _LOG.warning("Address completeness check failed: %s", e)

    # ── Phase-6 final safety net: regex-based address repair from raw ──
    # If the AI/recovery pipeline still left ADDRESS_1 short, run a
    # pure-Python regex extractor over the original paste text. This
    # is the LAST line of defence — guarantees the form gets the full
    # captured address even when the LLM is having a bad day.
    try:
        repair_address_from_raw(text, fields)
    except Exception as e:
        _LOG.warning("Deterministic address repair failed: %s", e)

    # ── Phase-6 PARCEL WEIGHT GUARD ──
    # AI sometimes copies the product's own weight (from item names like
    # "3 Kg Natural Honey" or "ODC3 20gm") into the WEIGHT field. That
    # is the product's own weight — NOT the parcel/dispatch weight. We
    # forcibly clear WEIGHT unless the source contains an explicit
    # "parcel weight" / "shipping weight" / "dispatch weight" label.
    try:
        _strip_product_weight_from_parcel_weight(text, fields)
    except Exception as e:
        _LOG.warning("Parcel-weight guard failed: %s", e)

    # Phase-7c: deterministic TOKEN extraction safety net. If the AI
    # missed the "50 tokn" / "Token ₹100" / "advance 200" amount,
    # this regex pulls it out of the raw text so the user doesn't
    # have to type it manually.
    try:
        _extract_token_from_raw(text, fields)
    except Exception as e:
        _LOG.warning("Token-extract guard failed: %s", e)

    return {
        "fields": fields,
        "missing": missing,
        "complexity": complexity,
        "ai_reason": reason,
        "raw": raw,
        "source": "llm",
        "warnings": warnings,
    }


# ─────────── Address-completeness post-processor ───────────
#
# Even with explicit prompt rules, gpt-4.1-nano sometimes silently drops
# middle parts of long multi-clause addresses (e.g. "Hiran Circle,
# Ramdevnagar Road, Prahladnagar" missing while it kept "20 Dev Atelier"
# + "Nr RK Enterprise" + city/state/pincode).
#
# This deterministic post-processor:
#   1. Locates the address block in the raw text using header keywords.
#   2. Tokenises by commas / newlines.
#   3. For each chunk, checks if it's already represented in
#      ADDRESS_1/ADDRESS_2/CITY/STATE/PINCODE (alnum-normalised compare).
#   4. Appends every dropped chunk to ADDRESS_2 (or ADDRESS_1 if A2 is
#      empty), keeping original order.
#   5. Conservative — only ADDS, never modifies/removes existing data.

_ADDR_HEADER_RE = re.compile(
    r"^\s*(?:shipping\s*address|delivery\s*address|address|"
    r"પત્તા|સરનામું|ઠેકાણું|पता|ठिकाना)\s*[:\-]?\s*$",
    re.I,
)
_BLOCK_END_RE = re.compile(
    r"^\s*(?:items?|order|payment|amount|qty|quantity|"
    r"તમારો\s*ઓર્ડર|ઓર્ડર|"
    r"\d+\s*(?:rs|inr|₹)|\u20b9|@|tel|phone|mob)",
    re.I,
)


def _norm_for_compare(s: str) -> str:
    """Lowercase + keep only letters/digits/Devanagari/Gujarati."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9\u0900-\u097F\u0A80-\u0AFF]+", "", s.lower())


def _find_address_block(text: str) -> List[str]:
    """Return the lines that make up the address block, or [] if none."""
    lines = text.splitlines()
    addr_block: List[str] = []
    in_addr = False
    consumed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_addr:
                break
            continue
        if not in_addr and _ADDR_HEADER_RE.match(stripped):
            in_addr = True
            continue
        if in_addr:
            if _BLOCK_END_RE.match(stripped):
                break
            addr_block.append(stripped)
            consumed += 1
            if consumed >= 4:
                break
    return addr_block


def _ensure_address_completeness(
    text: str,
    fields: Dict[str, str],
    warnings: List[str],
) -> None:
    """Mutates `fields` in place — appends any dropped address fragments
    to ADDRESS_2 (or ADDRESS_1 if A2 is empty) so nothing the user
    typed is silently lost."""
    addr_block = _find_address_block(text)
    if not addr_block:
        return

    full_addr = ", ".join(addr_block)
    chunks_raw = [
        c.strip(' ,.;\n\t-"\'')
        for c in re.split(r"[,\n]+", full_addr)
    ]
    chunks: List[str] = []
    for c in chunks_raw:
        if len(c) < 3:
            continue
        # Skip pure-digit chunks (pincode handled by PINCODE field).
        if re.fullmatch(r"\d{4,7}", c.strip()):
            continue
        # Must contain at least one letter.
        if not re.search(r"[A-Za-z\u0900-\u097F\u0A80-\u0AFF]", c):
            continue
        chunks.append(c)

    if not chunks:
        return

    captured = " | ".join([
        fields.get("ADDRESS_1") or "",
        fields.get("ADDRESS_2") or "",
        fields.get("CITY") or "",
        fields.get("STATE") or "",
        fields.get("PINCODE") or "",
    ])
    captured_n = _norm_for_compare(captured)

    missing: List[str] = []
    for c in chunks:
        cn = _norm_for_compare(c)
        if not cn or len(cn) < 3:
            continue
        # Strict substring match first.
        if cn in captured_n:
            continue
        # Token-wise fallback — handles cases where the chunk is
        # "<pincode> <state>" but our captured fields hold them in a
        # different order. We consider the chunk "represented" if
        # EVERY non-trivial token (≥ 3 chars) appears somewhere in
        # captured_n.
        tokens = [
            _norm_for_compare(t)
            for t in re.split(r"\s+", c)
            if len(t.strip()) >= 2
        ]
        tokens = [t for t in tokens if len(t) >= 3]
        if tokens and all(t in captured_n for t in tokens):
            continue
        missing.append(c)

    if not missing:
        return

    addn = ", ".join(missing)
    # Phase-6 single-line UX: append missing chunks to ADDRESS_1 (not
    # ADDRESS_2). The legacy ADDRESS_2 path is dead code under the new
    # form; we keep `fields["ADDRESS_2"] = ""` for output consistency.
    a1 = (fields.get("ADDRESS_1") or "").strip().rstrip(",").strip()
    if a1 and a1 != "-":
        merged = f"{a1}, {addn}"
    else:
        merged = addn
    fields["ADDRESS_1"] = merged[:300]
    fields["ADDRESS_2"] = ""

    warnings.append(
        f"Auto-recovered {len(missing)} address fragment"
        f"{'s' if len(missing) > 1 else ''}: {', '.join(missing)[:140]}"
    )
    _LOG.info(
        "Address completeness: appended %d missing chunk(s): %s",
        len(missing), missing,
    )


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
        # verbatim (e.g. "<customer name in English>", "(6 digits only)",
        # "(real customer name, keep original script)") treat it as
        # missing. Without this, the broken value would leak into the
        # Orders list and Google Sheet.
        if val:
            if "<" in val and ">" in val:
                val = ""
            else:
                # Round-bracket placeholders: any value that LOOKS like
                # a description rather than data — starts with "(" or
                # contains diagnostic phrases we use in the prompt.
                stripped = val.strip()
                low = stripped.lower()
                placeholder_signals = (
                    "keep original script",
                    "real customer name",
                    "primary 10 digits",
                    "second / alternative",
                    "alternative 10-digit",
                    "primary address line",
                    "secondary address",
                    "city / town",
                    "6 digits only",
                    "comma-separated for multiple",
                    "no ₹ symbol",
                    "courier name or",
                    "order number or",
                    "weight with unit or",
                    "special instruction or",
                    "leave blank if not stated",
                )
                if (
                    stripped.startswith("(")
                    or stripped.endswith(")")
                    or any(sig in low for sig in placeholder_signals)
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

def repair_address_from_raw(raw_text: str, schema: Dict[str, str]) -> Dict[str, str]:
    """Defensive deterministic address extraction.

    The AI sometimes truncates long addresses (puts only the first 1-2
    fragments in ADDRESS_1 and silently drops the rest). This function
    runs a regex-driven extraction over the ORIGINAL raw paste and, if
    it finds a more complete address than what the AI returned, it
    OVERWRITES schema["ADDRESS_1"] with the full version.

    Returns the (possibly mutated) schema dict — same object, modified
    in-place for caller convenience.

    Strategy:
      1. Find an "address header" line — one of:
           Shipping Address / Delivery Address / Address / શિપિંગ /
           પત્તો / डिलीवरी / पता / ઠેકાણું.
      2. Capture EVERYTHING from after the colon until we hit a clear
         end-marker: another Payment / Order / Items / phone-only line,
         or two consecutive blank lines.
      3. From that captured block, peel off trailing PINCODE (6 digits)
         and the word(s) around it as STATE; the word right before
         pincode/state as CITY.
      4. Whatever's left becomes the new ADDRESS_1, joined with ", ".
      5. Only override the AI if our extraction is at least 30% longer
         than what the AI produced AND ours is non-empty — protects
         against pathological pastes where this heuristic finds noise.
    """
    if not raw_text or not schema:
        return schema or {}

    # Header markers (first match wins). We anchor at line-start to avoid
    # accidentally matching a customer name like "Mahek Patel - Address Bar".
    header_re = re.compile(
        r"(?im)^[\s>•\-\*]*"
        r"(?:shipping\s*address|delivery\s*address|address|"
        r"\u0936\u093F\u092A\u093F\u0902\u0917|"  # शिपिंग
        r"\u092A\u0924\u093E|"                     # पता
        r"\u0aa1\u0ac0\u0ab2\u093F\u0935\u0930\u0940|"  # ડીલીવરી
        r"\u0aa0\u0ac7\u0a95\u0abe\u0aa3\u0ac1\u0a82|"   # ઠેકાણું
        r"\u0AAA\u0AA4\u0acd\u0AA4\u0acB)"         # પત્તો
        r"\s*[:\-–—]\s*",
    )
    m = header_re.search(raw_text)
    if not m:
        return schema

    after = raw_text[m.end():]
    # End-markers: consecutive blank lines OR a known next-section label
    # OR a payment/total emoji+amount line.
    end_re = re.compile(
        r"(?im)(?:\n\s*\n|"
        r"^[\s>•\-\*]*(?:order(?:\s*id)?|items?|amount|total|qty|"
        r"payment|paid|cod|prepaid|notes?|courier|"
        r"\u0aa4\u0aae\u0abe\u0ab0\u0acb\s*\u0a93\u0aa1\u0acd\u0ab0|"  # તમારો ઓર્ડર
        r"\u0aaa\u0ac7\u092e\u0947\u0902\u091f|"                        # પેમેન્ટ
        r"\u0905\u0902\u0915\u093E\u0908\s*\u0930\u0941\u092A)"        # रकम
        r"[:\-]?)",
    )
    em = end_re.search(after)
    block = (after[:em.start()] if em else after).strip()

    if not block:
        return schema

    # Flatten to commas, collapse whitespace, dedupe consecutive separators.
    block = re.sub(r"[\r\n]+", ", ", block)
    block = re.sub(r"\s*,\s*", ", ", block)
    block = re.sub(r"\s{2,}", " ", block).strip(" ,")
    if not block:
        return schema

    # Cap absurdly long captures (likely we ran past a signal we didn't
    # know about). Anything past ~400 chars is suspicious for an address.
    if len(block) > 400:
        block = block[:400]

    # Pull off trailing pincode if present (for the dedicated PINCODE
    # field), but DO NOT remove it from the working address text — the
    # courier label needs the full address, and we now duplicate the
    # city/state/pincode into their own fields rather than stripping.
    pin_match = re.search(r"\b(\d{6})\b", block)
    pincode = pin_match.group(1) if pin_match else ""

    # Don't strip pincode from working — keep it inside ADDRESS_1.
    working = block

    parts = [p.strip() for p in working.split(",") if p.strip()]
    city = state = ""
    # Detect (but DO NOT remove) trailing parts as city/state.
    if len(parts) >= 3:
        # Last part = state, second-last = city. Keep them in line1_parts.
        last_part = parts[-1]
        # If last_part is just the pincode, look one further back.
        if re.fullmatch(r"\s*\d{6}\s*", last_part):
            state = parts[-2] if len(parts) >= 2 else ""
            city = parts[-3] if len(parts) >= 3 else ""
        else:
            # Last part may contain pincode + state (e.g. "380015 Gujarat").
            cleaned_last = re.sub(r"\b\d{6}\b", "", last_part).strip()
            state = cleaned_last or last_part
            city = parts[-2]
    elif len(parts) == 2:
        state = parts[-1]
    # ADDRESS_1 = full line — city/state/pincode REMAIN inside it.
    line1_parts = parts

    line1_full = ", ".join(line1_parts)
    if len(line1_full) > 300:
        line1_full = line1_full[:300]

    # Decide whether to override AI output. Heuristic: if our extraction
    # is at least 30% longer than the AI's and contains a substring of
    # the AI's, we trust ours. This protects against:
    #  • pathological pastes with no real address (we'd produce noise)
    #  • cases where the AI did a perfect job (we don't disturb it)
    ai_addr = (schema.get("ADDRESS_1", "") or "").strip()
    if line1_full and len(line1_full) >= int(len(ai_addr) * 1.3 + 1):
        schema["ADDRESS_1"] = line1_full
        # Also ensure ADDRESS_2 stays empty under the new single-line UX.
        schema["ADDRESS_2"] = ""
    elif line1_full and not ai_addr:
        # AI gave nothing but we found something — definitely use ours.
        schema["ADDRESS_1"] = line1_full
        schema["ADDRESS_2"] = ""

    # Only set CITY/STATE/PINCODE if the AI didn't already have a value
    # (we trust the AI's classification of geography over our naive last-
    # token heuristic when both are non-empty).
    if pincode and not (schema.get("PINCODE") or "").strip():
        schema["PINCODE"] = pincode
    if city and not (schema.get("CITY") or "").strip():
        schema["CITY"] = city
    if state and not (schema.get("STATE") or "").strip():
        schema["STATE"] = state

    # Phase-36 (2026-05-18) — DISABLED: previously this block re-
    # appended CITY / STATE / PINCODE back onto ADDRESS_1 so the
    # printable courier label would always see the trio. The new label
    # renderer composes the trio from their dedicated fields, so
    # re-appending here just produced duplicates on the printed
    # sticker (e.g. "...Pitampura, Delhi, 110034 Delhi\nDelhi, Delhi -
    # 110034"). Leaving the block out makes the dedicated CITY /
    # STATE / PINCODE columns the single source of truth, which keeps
    # the address line clean across:
    #   * Smart Paste preview
    #   * Shipment edit form
    #   * Master sheet column "Address"
    #   * Printed courier sticker
    #
    # Original logic preserved below as a comment for archaeology.
    #
    # cur_addr = (schema.get("ADDRESS_1", "") or "").strip().rstrip(", ")
    # cur_lower = cur_addr.lower()
    # final_city  = (schema.get("CITY",    "") or "").strip()
    # final_state = (schema.get("STATE",   "") or "").strip()
    # final_pin   = (schema.get("PINCODE", "") or "").strip()
    # appendix = []
    # if final_city and final_city.lower() not in cur_lower:
    #     appendix.append(final_city)
    # if final_state and final_state.lower() not in cur_lower and final_state.lower() != final_city.lower():
    #     appendix.append(final_state)
    # if final_pin and final_pin not in cur_addr:
    #     appendix.append(final_pin)
    # if appendix:
    #     glue = ", " if cur_addr else ""
    #     merged = (cur_addr + glue + ", ".join(appendix)).strip(", ")
    #     if len(merged) <= 300:
    #         schema["ADDRESS_1"] = merged
    #     else:
    #         schema["ADDRESS_1"] = merged[:300]

    return schema


def _strip_product_weight_from_parcel_weight(raw_text: str, fields: Dict[str, str]) -> None:
    """Deterministic guard: prevent product weight (from item name) from
    leaking into the parcel WEIGHT field.

    Trigger conditions: WEIGHT is filled AND the raw paste does NOT
    contain any explicit "parcel/shipping/dispatch/package weight"
    label. In that case we forcibly clear WEIGHT so the form prompts
    the user for the real parcel weight.

    The AI is instructed to do this in Rule 6b — this function is the
    safety net that catches it when the AI ignores the rule.
    """
    weight_val = (fields.get("WEIGHT", "") or "").strip()
    if not weight_val or weight_val == "-":
        return
    # Look for an explicit "parcel weight" / "shipping weight" /
    # "dispatch weight" / "package weight" / "wt incl box" mention.
    explicit_weight_re = re.compile(
        r"(?i)\b(parcel|shipping|dispatch|package|courier|box|"
        r"\u092A\u093E\u0930\u094D\u0938\u0932|"     # पार्सल
        r"\u0aaa\u093e\u0ab0\u094d\u0ab8\u0ab2)"     # પાર્સલ
        r"[^\n]{0,20}(weight|wt|\u0935\u091C\u0928|\u0935\u091C\u0928)"
    )
    if explicit_weight_re.search(raw_text or ""):
        return  # explicit label found → trust the AI's value
    # No explicit parcel-weight mention → AI almost certainly pulled
    # this from the item name. Clear it.
    _LOG.info(
        "Smart-paste: cleared WEIGHT '%s' — no explicit parcel-weight "
        "label in source (likely product weight from item name).",
        weight_val,
    )
    fields["WEIGHT"] = ""


def _extract_token_from_raw(raw_text: str, fields: Dict[str, str]) -> None:
    """Deterministic safety net: if AI did NOT extract TOKEN but the
    raw paste mentions a token / advance amount, pull it out via
    regex so the user doesn't have to type it manually.

    Triggers:
      - existing TOKEN value is empty / `-`
      - raw text contains: token, tokn, advance, ઍડ્વાન્સ, ટોકન,
        टोकन, अग्रिम (case-insensitive), with a number nearby.
    """
    cur = (fields.get("TOKEN", "") or "").strip()
    if cur and cur != "-":
        return
    if not raw_text:
        return
    # Match: <number><optional ws><token-keyword>  OR  <token-keyword><optional ws>< ₹/Rs?><number>
    # Examples handled:
    #   "50 tokn"          → 50
    #   "Token ₹100"       → 100
    #   "Token: 100"       → 100
    #   "advance 200"      → 200
    #   "ટોકન 300"         → 300
    #   "ઍડ્વાન્સ Rs 150"   → 150
    keyword = (
        r"(?:token|tokn|advance|adv|"
        r"\u091F\u094B\u0915\u0928|"                  # टोकन
        r"\u0A9F\u0acb\u0A95\u0AA8|"                  # ટોકન
        r"\u0905\u0917\u094D\u0930\u093F\u092E|"      # अग्रिम
        r"\u0A8D\u0AA1\u0acd\u0AB5\u0Aa3\u0acd\u0Ab8)" # ઍડ્વાન્સ (best-effort)
    )
    # Pattern A: NUMBER then keyword
    pat_a = re.compile(rf"(?i)(\d{{1,7}})\s*{keyword}")
    m = pat_a.search(raw_text)
    if not m:
        # Pattern B: keyword then optional ₹/Rs then number
        pat_b = re.compile(rf"(?i){keyword}\s*[:\-]?\s*(?:₹|rs\.?|inr)?\s*(\d{{1,7}})")
        m = pat_b.search(raw_text)
    if not m:
        return
    token_val = m.group(1)
    _LOG.info("Smart-paste: TOKEN extracted via deterministic fallback = %s", token_val)
    fields["TOKEN"] = token_val


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

    # Phase-6 single-address-field merge (2026-04-28).
    # The form now exposes ONE address field, so we collapse anything
    # the AI may still drop into ADDRESS_2 back into ADDRESS_1. This
    # is a defensive safety net — the prompt has already been updated
    # to instruct the AI to put EVERYTHING in ADDRESS_1, but we
    # belt-and-braces the merge here so a single mis-step from the
    # model never silently truncates a customer's address.
    addr1_raw = (ai_fields.get("ADDRESS_1", "") or "").strip()
    addr2_raw = (ai_fields.get("ADDRESS_2", "") or "").strip()
    if addr2_raw and addr2_raw != "-":
        # Avoid stupid duplication: only append addr2 if it's not
        # already a substring of addr1 (some prompt drift is possible).
        if addr2_raw.lower() not in addr1_raw.lower():
            addr1_raw = (addr1_raw + ", " + addr2_raw) if addr1_raw else addr2_raw

    # Phase-36 (2026-05-18) — Strip city, state, and pincode from the
    # trailing end of ADDRESS_1 so the dedicated city/state/pincode
    # fields are the single source of truth for those values. The new
    # courier-label renderer composes the printable address as:
    #   "<address_line1>\n<city>, <state> - <pincode>"
    # so if we leave the trio inside ADDRESS_1 we get duplicates on the
    # label (e.g. "...Pitampura, Delhi, 110034 Delhi\nDelhi, Delhi -
    # 110034"). Order of removal matters: pincode first (most specific),
    # then state, then city. Each strip is anchored to a trailing-comma
    # context so we never accidentally hit a SAME-named landmark earlier
    # in the address (e.g. a road called "Gujarat Road" stays intact).
    city_val   = (ai_fields.get("CITY",   "") or "").strip()
    state_val  = (ai_fields.get("STATE",  "") or "").strip()
    pin_val    = (ai_fields.get("PINCODE","") or "").strip()

    def _strip_tail_token(text: str, token: str) -> str:
        """Remove `token` (case-insensitive) when it sits at the end of
        the string, optionally preceded by a comma + whitespace. Leaves
        any earlier occurrence of the same token untouched. Whitespace
        and stray commas are normalised at the end."""
        if not text or not token:
            return text
        # Build a regex that matches the token at the trailing end,
        # tolerating: ", token" / "token" / "  ,  token,  " / "token."
        pat = re.compile(
            r"\s*[,;\u3001]?\s*"        # optional separator before
            r"\b" + re.escape(token) + r"\b"  # the token itself
            r"\s*[\.,;]?\s*$",          # optional trailing punctuation
            re.IGNORECASE,
        )
        return pat.sub("", text).rstrip(" ,;").strip()

    cleaned_addr = addr1_raw
    # Run the three strips in a loop until the address text stops
    # changing. This handles inputs where the tokens are glued in
    # unexpected order (e.g. "110034 Delhi" — pincode then state, so
    # the first pincode-pass can't see it as trailing but a second
    # pass can after the state-pass exposes it). Loop is bounded so
    # a pathological input can't spin forever.
    for _ in range(4):
        prev = cleaned_addr
        # 1) Pincode at the trailing end (most specific).
        if pin_val:
            cleaned_addr = _strip_tail_token(cleaned_addr, pin_val)
        # 2) State at the trailing end.
        if state_val:
            cleaned_addr = _strip_tail_token(cleaned_addr, state_val)
        # 3) City at the trailing end (last because state often sits
        #    AFTER city in Indian inputs).
        if city_val:
            cleaned_addr = _strip_tail_token(cleaned_addr, city_val)
        if cleaned_addr == prev:
            break
    # Final tidy — collapse any double commas / dangling separators
    # left behind by repeated strips.
    cleaned_addr = re.sub(r"\s*,\s*,+\s*", ", ", cleaned_addr).strip(" ,;")
    addr1_raw = cleaned_addr

    return {
        "customer_name":  ai_fields.get("NAME", ""),
        "customer_phone": primary,
        "customer_alt_phone": alt,
        "address_line1":  addr1_raw,
        "address_line2":  "",  # always blank under the single-field UX
        "city":           ai_fields.get("CITY", ""),
        "state":          ai_fields.get("STATE", ""),
        "pincode":        ai_fields.get("PINCODE", ""),
        "items":          ai_fields.get("ITEMS", ""),
        "amount":         ai_fields.get("AMOUNT", ""),
        "payment_mode":   (ai_fields.get("PAYMENT", "") or "").upper(),
        "token_amount":   ai_fields.get("TOKEN", ""),
        "courier_name":   ai_fields.get("COURIER", ""),
        "order_id":       ai_fields.get("ORDER_ID", ""),
        "weight":         ai_fields.get("WEIGHT", ""),
        "notes":          ai_fields.get("NOTES", ""),
        # Phase-3 Smart Paste enhancement.
        "customer_email": (ai_fields.get("EMAIL", "") or "").strip().lower(),
        "customer_gstin": (ai_fields.get("GSTIN", "") or "").strip().upper(),
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

_VISION_MODEL = os.getenv("SMART_PASTE_VISION_MODEL", "gemini-2.5-flash")
_VISION_PROVIDER = os.getenv("SMART_PASTE_VISION_PROVIDER", "gemini")
_VISION_TIMEOUT = float(os.getenv("SMART_PASTE_VISION_TIMEOUT", "25.0"))
# Dedicated model for the targeted phone-number re-verification pass.
# Kept on "pro" for maximum Indic-digit OCR accuracy — this is the
# safety net that catches cases where Flash misreads ૭ <-> ૧ /
# ૪ <-> ૧ in faint / low-res visiting cards. Flash handles the main
# schema (fast) while Pro focuses ONLY on the critical phone fields.
_PHONE_VERIFY_MODEL = os.getenv("SMART_PASTE_PHONE_VERIFY_MODEL", "gemini-2.5-pro")
_PHONE_VERIFY_TIMEOUT = float(os.getenv("SMART_PASTE_PHONE_VERIFY_TIMEOUT", "35.0"))

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
   - Words like "Paid", "Online", "Advance Paid", "UPI Done",
     "Prepaid" → PAYMENT: PAID.
   - **If NO payment info is visible — leave PAYMENT EMPTY (`-`).
     DO NOT guess or default to COD. The user will fill it later.**
7. ITEMS:
   - Pull product / SKU words. Append "x QTY" if quantity is visible.
8. NOISE FILTER:
   - Ignore decorative text (logos, slogans, watermarks).
   - Ignore phone-number listings that are clearly the SHOP's helpline
     (printed at the top of a visiting card) — but if those are the
     only numbers visible, still use them.
9. **STRIKETHROUGH / CROSSED-OUT NUMBERS:**
   - If a phone number has a visible STRIKETHROUGH line drawn through
     it (a horizontal pen/marker stroke crossing the digits), treat
     it as INVALID and SKIP it entirely.
   - Same rule for any text the customer has manually crossed out
     with pen/pencil/marker — it's a "cancel this" signal.
   - Pick the next available un-crossed number as PHONE / ALT_PHONE.
   - If only ONE valid (un-crossed) number remains, that's PHONE and
     ALT_PHONE stays empty.
10. NEVER invent missing values — leave them as `-`.
"""
)


def _extract_phones_via_tesseract(image_base64: str) -> List[str]:
    """Run Tesseract OCR (guj+hin+eng) on the image and extract every
    10-digit Indian mobile number (starts with 6/7/8/9) it can find,
    in visual reading order.

    Strategy: run MULTIPLE Tesseract passes with different PSM modes
    AND different lightweight image variants (original, autocontrast,
    grayscale), then VOTE across them. A number that appears in the
    majority of passes is much more trustworthy than a single-pass
    answer — this is how we catch tesseract's occasional ૭ <-> ૪
    confusion on faint print.

    FULLY DETERMINISTIC: no LLM, no network. Returns [] on any error.
    """
    try:
        import base64
        import io
        from collections import Counter

        import pytesseract
        from PIL import Image, ImageOps

        data = base64.b64decode(image_base64)
        base_im = Image.open(io.BytesIO(data))
        if base_im.mode != "RGB":
            base_im = base_im.convert("RGB")

        # Build several lightweight image variants — tesseract likes
        # clean, non-upscaled images so we deliberately DO NOT apply
        # any upscale / unsharp / contrast boost (that tends to hurt
        # already-crisp printed cards). Just autocontrast + grayscale.
        variants = [("orig", base_im)]
        try:
            variants.append(("ac", ImageOps.autocontrast(base_im, cutoff=1)))
        except Exception:
            pass
        try:
            variants.append(("gray", ImageOps.grayscale(base_im).convert("RGB")))
        except Exception:
            pass

        all_hits: List[str] = []
        per_pass: List[Tuple[str, List[str]]] = []

        # PSM 4 (single column) usually best on visiting cards.
        # PSM 6 (block) is a strong fallback.
        # PSM 11 (sparse) catches loosely-laid screenshots.
        # PSM 3 (auto) is the generic Tesseract default.
        psms = (4, 6, 11, 3)

        for vname, im in variants:
            for psm in psms:
                try:
                    txt = pytesseract.image_to_string(
                        im,
                        lang="guj+hin+eng",
                        config=f"--psm {psm}",
                    )
                except Exception:
                    continue
                if not txt:
                    continue
                norm = _digits_to_en(txt)
                # Drop common separators inside runs so "98245 44417" and
                # "98245-44417" become one 10-digit run.
                compact = re.sub(r"[\s\-\.\(\)]+", "", norm)
                hits = re.findall(r"(?<!\d)([6-9]\d{9})(?!\d)", compact)
                per_pass.append((f"{vname}/psm{psm}", hits))
                all_hits.extend(hits)

        if not all_hits:
            return []

        # Vote: pick the most commonly seen number(s). Maintain first-
        # seen order among ties so the main number on the card (typ-
        # ically top/left) comes before the alt.
        counts = Counter(all_hits)
        max_count = max(counts.values())
        # Build preserved-order unique list weighted by count.
        ordered: List[str] = []
        seen: set = set()
        for n in all_hits:
            if n not in seen:
                ordered.append(n)
                seen.add(n)

        # Keep only numbers that appeared at least twice (anti-noise)
        # if we have enough voters; otherwise keep all.
        strong = [n for n in ordered if counts[n] >= 2]
        chosen = strong if len(strong) >= 1 else ordered

        _LOG.info(
            "Smart-paste phone tesseract: per_pass=%r votes=%r chosen=%r",
            [(name, hits) for name, hits in per_pass],
            dict(counts),
            chosen[:5],
        )
        return chosen[:5]
    except Exception as e:
        _LOG.warning("Tesseract phone extraction failed: %s", e)
        return []






# ---- Image preprocessing + phone re-verification helpers ---------------

def _preprocess_image_for_vision(image_base64: str) -> str:
    try:
        import base64
        import io
        from PIL import Image, ImageFilter, ImageOps, ImageEnhance

        data = base64.b64decode(image_base64)
        im = Image.open(io.BytesIO(data))
        if im.mode != "RGB":
            im = im.convert("RGB")

        w, h = im.size
        short_side = min(w, h)
        TARGET_SHORT = 1400
        if short_side < TARGET_SHORT:
            scale = TARGET_SHORT / float(short_side)
            new_w = int(round(w * scale))
            new_h = int(round(h * scale))
            im = im.resize((new_w, new_h), Image.LANCZOS)

        try:
            im = ImageOps.autocontrast(im, cutoff=1)
        except Exception:
            pass

        try:
            im = im.filter(ImageFilter.UnsharpMask(
                radius=1.5, percent=150, threshold=3,
            ))
        except Exception:
            pass

        try:
            im = ImageEnhance.Contrast(im).enhance(1.15)
        except Exception:
            pass

        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        enhanced = base64.b64encode(buf.getvalue()).decode("ascii")
        _LOG.info(
            "Smart-paste photo: image preprocessed "
            "(in=%dB -> out=%dB, size=%dx%d)",
            len(data), len(buf.getvalue()), im.size[0], im.size[1],
        )
        return enhanced
    except Exception as e:
        _LOG.warning("Image preprocessing failed, using original: %s", e)
        return image_base64


# Dedicated prompt for the focused phone-number re-verification pass.
# Gemini outputs BOTH the raw native-script transcription AND the
# final Arabic mapping; server then deterministically re-translates
# the native script via str.maketrans — so if the model mistakes the
# VISUAL-TO-NUMERIC mapping we still recover the right number from
# the raw transcription (transcription is an easier task than mapping).
_PHONE_VERIFY_PROMPT = """\
You are an OCR expert specialising in Indian mobile phone numbers
written in Arabic, Gujarati, or Hindi (Devanagari) digits.

INPUT: an image containing one or more phone numbers. Numbers may
be handwritten, printed on a visiting card, or part of a WhatsApp
message. They may be in:
  - Arabic digits:   0 1 2 3 4 5 6 7 8 9
  - Gujarati digits: ૦ ૧ ૨ ૩ ૪ ૫ ૬ ૭ ૮ ૯   (map to 0-9 IN ORDER)
  - Hindi digits:    ० १ २ ३ ४ ५ ६ ७ ८ ९   (map to 0-9 IN ORDER)

TASK: extract EVERY mobile number visible in the image (10 digits
each, Indian mobile starts with 6/7/8/9). For each number output
BOTH:
  1. The EXACT native-script transcription, digit-by-digit, with
     single spaces between digits (so server can parse).
  2. The final 10-digit Arabic representation.

VISUAL CONFUSION WARNINGS — look carefully before committing:
  Gujarati:
    2<->9   3<->7   4<->8   4<->1   7<->1   7<->4   0<->9   5<->6
    (i.e. in script: two vs nine, three vs seven, four vs eight,
    four vs one, seven vs one, seven vs four, zero vs nine, five
    vs six)
  Hindi / Devanagari:
    2<->3<->7   1<->7   0<->9   5<->6

STRIKETHROUGH / CROSSED-OUT numbers MUST BE SKIPPED (if a horizontal
pen/marker line crosses the digits) — do NOT skip a number that
merely has an UNDERLINE below it (underline = emphasis, not cancel).

If a digit is too blurry or partially obscured to be certain, mark
the whole number invalid with status "unclear".

OUTPUT FORMAT — ONE line per number, in visual reading order
(top-to-bottom, left-to-right). NO other text, NO markdown, NO
explanation.

Example (two Gujarati numbers on a card):
  PHONE_RAW: 9 8 2 4 4 7 5 1 0 0 | ARABIC: 9824475100 | STATUS: ok
  PHONE_RAW: 9 7 1 2 5 4 4 7 4 7 | ARABIC: 9712544747 | STATUS: ok
(but when Gujarati, use the ACTUAL Gujarati glyphs ૦-૯ in PHONE_RAW,
 e.g. PHONE_RAW: ૯ ૭ ૧ ૨ ૫ ૪ ૪ ૭ ૪ ૭ | ARABIC: 9712544747 | STATUS: ok)

Example (faint number):
  PHONE_RAW: ? ? ? ? ? ? ? ? ? ? | ARABIC: - | STATUS: unclear

ABSOLUTE RULES:
  - Never hallucinate a missing digit. If unsure, STATUS=unclear and
    ARABIC=-.
  - Never auto-correct the number to look "more familiar".
  - Transcribe each digit INDEPENDENTLY; do NOT compare to common
    Indian mobile prefixes to "guess" a digit.
  - Final ARABIC must be exactly 10 digits (or "-" for unclear).
  - Include ALL phone numbers you see, even if more than 2.
"""


# Regex to parse one line of the phone-verify response.
_PHONE_VERIFY_LINE_RE = re.compile(
    r"PHONE_RAW\s*:\s*(?P<raw>[^|]+?)\s*\|\s*"
    r"ARABIC\s*:\s*(?P<ar>[0-9\-]+)\s*\|\s*"
    r"STATUS\s*:\s*(?P<st>\w+)",
    flags=re.IGNORECASE,
)


async def _reverify_phones_via_vision(
    *, image_base64: str, mime: str,
) -> List[Dict[str, str]]:
    """Run a focused second Gemini Vision call ONLY for phone numbers.

    Returns an ordered list (top-to-bottom in the image):
      [{"raw_native": "<with spaces>", "raw_compact": "<no spaces>",
        "arabic_from_model": "9824475100",
        "arabic_deterministic": "9824475100", "status": "ok"},
       ...]
    """
    if not image_base64 or not _LLM_KEY:
        return []
    try:
        from emergentintegrations.llm.chat import (
            LlmChat, UserMessage, ImageContent,
        )
        chat = (
            LlmChat(
                api_key=_LLM_KEY,
                session_id=f"smart-paste-phone-verify-"
                           f"{abs(hash(image_base64[:200]))}",
                system_message=_PHONE_VERIFY_PROMPT,
            )
            .with_model(_VISION_PROVIDER, _PHONE_VERIFY_MODEL)
        )
        msg = UserMessage(
            text=(
                "Extract every mobile number in this image using the "
                "strict output format above. Output only the phone "
                "lines — no other text."
            ),
            file_contents=[ImageContent(image_base64=image_base64)],
        )
        raw = await asyncio.wait_for(
            chat.send_message(msg), timeout=_PHONE_VERIFY_TIMEOUT,
        )
    except Exception as e:
        _LOG.warning("Smart-paste phone re-verify failed: %s", e)
        return []

    out: List[Dict[str, str]] = []
    for m in _PHONE_VERIFY_LINE_RE.finditer(raw or ""):
        raw_native = m.group("raw").strip()
        ar_from_model = (m.group("ar") or "").strip()
        status = (m.group("st") or "").strip().lower()

        # Compact form: drop all whitespace.
        raw_compact = re.sub(r"\s+", "", raw_native)
        # Deterministic translation (the safety net):
        det = _digits_to_en(raw_compact)
        det_digits = re.sub(r"\D", "", det)

        out.append({
            "raw_native": raw_native,
            "raw_compact": raw_compact,
            "arabic_from_model": ar_from_model,
            "arabic_deterministic": det_digits,
            "status": status,
        })
    _LOG.info(
        "Smart-paste phone re-verify: parsed %d number(s); raw=%r",
        len(out), (raw or "")[:300],
    )
    return out


def _pick_best_phone(verify_entry: Dict[str, str]) -> str:
    """Choose the most trustworthy 10-digit number from a single
    phone-verify entry.

    Priority:
      1. Deterministic translation of native script (if valid 10-digit
         Indian mobile: starts with 6/7/8/9). This is the safest —
         pure str.translate, no LLM inference at this step.
      2. Model's own Arabic output (if valid 10-digit Indian mobile).
      3. Empty string (no reliable number).
    """
    status = (verify_entry.get("status") or "").lower()
    if status not in {"ok", ""}:
        return ""

    det = verify_entry.get("arabic_deterministic") or ""
    model_ar = verify_entry.get("arabic_from_model") or ""

    def _is_valid_indian_mobile(s: str) -> bool:
        return bool(re.fullmatch(r"[6-9]\d{9}", s or ""))

    if _is_valid_indian_mobile(det):
        return det
    if _is_valid_indian_mobile(model_ar):
        return model_ar
    return ""




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

    # Phase-14: keep a pristine copy of the ORIGINAL image for Tesseract.
    # Tesseract's Gujarati / Hindi models are tuned for natural camera
    # captures — aggressive preprocessing (upscale + unsharp) can hurt
    # accuracy on already-crisp printed cards.
    original_image_b64 = image_base64

    # Deterministic image enhancement BEFORE Gemini call only.
    # Boosts contrast + sharpness + upscales small images so faint
    # Indic-script digits print clearer. Zero LLM cost.
    image_base64 = _preprocess_image_for_vision(image_base64)
    # After preprocessing we always output PNG; Gemini handles PNG fine.
    mime = "image/png"

    system = DEFAULT_VISION_PROMPT
    if custom_instructions.strip():
        # Phase-12: append CRITICAL_FIELD_RULES *after* base rules so
        # LLMs can't ignore alt-phone + shop-name combining even when
        # the user's own prompt claims "HIGH PRIORITY ENFORCEMENT".
        system = (
            "## USER CUSTOMISATION (honour these first when not conflicting "
            "with the output format rules below):\n"
            + custom_instructions.strip()
            + "\n\n-- BASE RULES --\n"
            + DEFAULT_VISION_PROMPT
            + "\n\n"
            + CRITICAL_FIELD_RULES
        )
    else:
        system = system + "\n\n" + CRITICAL_FIELD_RULES

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

    # ── Phase-14: Phone re-verification (Indic-digit accuracy safety net)
    # PRIMARY: Tesseract OCR with Gujarati + Hindi language packs —
    # deterministic, fast, free, and trained specifically on Indic
    # scripts. Runs on the ORIGINAL (pristine, un-preprocessed) image
    # because tesseract's trained models already handle typical
    # camera noise better than aggressive sharpening / upscaling.
    # FALLBACK: focused Gemini 2.5 Pro call if Tesseract finds nothing
    # (e.g. unusual fonts / stylised handwriting where Tesseract
    # struggles but a vision LLM still recognises the digits).
    best_nums: List[str] = []

    try:
        tess_nums = _extract_phones_via_tesseract(original_image_b64)
    except Exception as e:
        _LOG.warning("Tesseract phone wrapper failed: %s", e)
        tess_nums = []
    for n in tess_nums:
        if n not in best_nums:
            best_nums.append(n)
        if len(best_nums) >= 2:
            break

    if len(best_nums) < 2:
        try:
            phone_verify = await _reverify_phones_via_vision(
                image_base64=image_base64, mime=mime,
            )
        except Exception as e:
            _LOG.warning("Phone re-verify wrapper failed: %s", e)
            phone_verify = []
        for entry in phone_verify:
            n = _pick_best_phone(entry)
            if n and n not in best_nums:
                best_nums.append(n)
            if len(best_nums) >= 2:
                break

    if best_nums:
        old_p = (fields.get("PHONE") or "").strip()
        old_ap = (fields.get("ALT_PHONE") or "").strip()
        if len(best_nums) >= 1 and best_nums[0] and best_nums[0] != old_p:
            _LOG.info(
                "Smart-paste photo: PHONE overridden by phone-verify "
                "(%r -> %r)", old_p, best_nums[0],
            )
            fields["PHONE"] = best_nums[0]
            if "PHONE" in missing:
                missing.remove("PHONE")
            reason = (reason or "vision call") + " + phone verify"
        if len(best_nums) >= 2 and best_nums[1] and best_nums[1] != old_ap:
            _LOG.info(
                "Smart-paste photo: ALT_PHONE overridden by phone-verify "
                "(%r -> %r)", old_ap, best_nums[1],
            )
            fields["ALT_PHONE"] = best_nums[1]
            if "ALT_PHONE" in missing:
                missing.remove("ALT_PHONE")
            if "phone verify" not in (reason or ""):
                reason = (reason or "vision call") + " + phone verify"

    # ── Address-recovery fallback (Phase-5d patch) ───────────────────
    # Gemini sometimes returns ADDRESS_1 = "-" even when there's a clearly
    # visible street/house line in the image — most common on:
    #   - Visiting cards where address text is smaller than name/logo
    #   - Screenshots of e-commerce order screens where city/state/
    #     pincode appear after the street and the model thinks the
    #     locality has covered it
    # If we got CITY or PINCODE but ADDRESS_1 is blank, re-prompt with a
    # tightly scoped "address only" question. Costs one extra call only
    # on bad cases — typical happy path runs zero retries.
    #
    # Phase-12 NOTE: retry is ALWAYS ON (critical for visiting-card
    # OCR accuracy). The first-call latency win comes from client-side
    # image compression (see frontend) — not from disabling this retry.
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
                    # Rule 13: NEVER drop address parts. If recovery
                    # returns 3+ lines, line 1 → ADDRESS_1 and the rest
                    # are merged (comma-joined) into ADDRESS_2.
                    fields["ADDRESS_1"] = lines[0][:140]
                    rest = ", ".join(lines[1:])[:140] if len(lines) > 1 else ""
                    if rest and not (fields.get("ADDRESS_2") or "").strip():
                        fields["ADDRESS_2"] = rest
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
