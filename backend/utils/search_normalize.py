"""Shared search-normalisation utility.

Used by both the backend `/api/shipments` search endpoint AND the
suggested-filters generator so that the count shown next to a chip
always matches the number of shipments returned when the chip is
tapped (that mismatch is exactly the bug this module fixes).

Design goals (see product spec):

* English input should match Gujarati / Hindi / any language that
  ultimately refers to the same product.
* Ignore word order, case, hyphens, extra spaces.
* Treat "100gm" == "100 gram" == "100 g" == "100 ग्राम" == "100 ગ્રામ".
* Strip trivial quantity suffixes like "x1", "x 1", "1 pkt",
  "1 packet", "1 pcs", "1 pc".
* Ignore Devanagari/Gujarati virama, matras (via unidecode).

The exact transliteration of an *entirely* non-Latin product code —
e.g. "ઓડીસી3" for "ODC3" — is intentionally NOT hard-coded here (there
is no reliable rule for it).  In practice, sellers keep the Latin
product code visible in the string ("100 ग्राम ODC-3 बीज"), so
substring matching on the normalised blob covers the real-world
inputs. If a pure-Indic transliteration ever needs to match, add the
mapping to `_PRODUCT_CODE_TRANSLIT` below.
"""

from __future__ import annotations

import re
from typing import List, Optional

from unidecode import unidecode

# ── Optional Indic↔Latin product-code overrides.  Extend this map
#    when the seller confirms that a specific Gujarati/Hindi spelling
#    represents an English SKU. Keys are already unidecode'd + lower.
_PRODUCT_CODE_TRANSLIT = {
    # unidecode('ઓડીસી') → 'oddiisii'   ⇒ collapse to 'odc'
    "oddiisii": "odc",
    # unidecode('ओडीसी')  → 'odisi'      ⇒ same product code
    "odisi": "odc",
    # unidecode('बीज')    → 'biij'       ⇒ 'seed'
    "biij": "seed",
    # unidecode('ग्राम')  → 'graam'      ⇒ 'g'   (handled by regex too)
    "graam": "g",
    # unidecode('પેકેટ') → 'peketa'      ⇒ 'pkt'
    "peketa": "pkt",
}

# Unit tokens that should collapse to a single canonical "g" when they
# follow a digit.  These are the *transliterated* forms so unidecode()
# has already run once by the time this pattern applies.
_WEIGHT_UNIT_RE = re.compile(
    r"(\d)\s*(?:graams?|grams?|gms?|g)\b",
    flags=re.IGNORECASE,
)

# Quantity suffix stripping.  Removes:
#   "x1", "x 2", "X10"
#   "1 pkt", "2 packet", "3 pcs", "5 pc"
_QTY_X_RE = re.compile(r"\bx\s*\d+\b", flags=re.IGNORECASE)
_QTY_UNIT_RE = re.compile(
    r"\b\d+\s*(?:pkts?|packets?|pcs?|pieces?|nos?|units?)\b",
    flags=re.IGNORECASE,
)

# Punctuation that we JOIN across instead of splitting on. Hyphens
# and slashes inside product codes should not create extra tokens
# ("ODC-3" → "odc3", not "odc 3").
_JOIN_PUNCT_RE = re.compile(r"[-–—_/]")
# Everything else (commas, dots, etc.) becomes a space.
_SPLIT_PUNCT_RE = re.compile(r"[^\w\s]")

# Collapse consecutive whitespace.
_WS_RE = re.compile(r"\s+")

# Consonants used by the schwa-drop pass.
_CONSONANTS = set("bcdfghjklmnpqrstvwxyz")


def _schwa_compact(token: str) -> str:
    """Return a schwa-dropped compact form of a Latin token.

    Devanagari / Gujarati transliterate through unidecode() with
    trailing/inline "a" schwas AND stretched matras that surface as
    doubled vowels ("bhaavanagaara").  Native English speakers write
    the same city as "Bhavnagar" → "bhavnagar".  This helper
    collapses both forms to the common consonant skeleton "bhvngr"
    so a query in any language matches.

    Steps:
      1. Dedupe consecutive vowels ("aa" → "a", "ii" → "i").
      2. Strip trailing "a" after consonant.
      3. Iteratively drop internal "a" between two consonants.

    The compact form is stored ALONGSIDE the original in the blob so
    ordinary Latin searches ("Moringa") still hit their unmodified
    variant. Only tokens ≥ 3 chars are folded — short words like
    "1kg" or "od" are noisy.
    """
    if not token or len(token) < 3:
        return token
    vowels = "aeiou"
    # 1. Dedupe consecutive vowels.
    t = re.sub(r"([aeiou])\1+", r"\1", token)
    # 2. Strip trailing schwa after consonant.
    if len(t) >= 2 and t[-1] == "a" and t[-2] in _CONSONANTS:
        t = t[:-1]
    # 3. Drop every internal schwa between two consonants (iterative
    #    so "bhavanagar" → "bhvangr" → "bhvngr" via successive passes).
    prev = None
    while t != prev:
        prev = t
        t = re.sub(
            r"([bcdfghjklmnpqrstvwxyz])a([bcdfghjklmnpqrstvwxyz])",
            r"\1\2",
            t,
        )
    return t


def normalize_text(text: Optional[str]) -> str:
    """Return a lower-cased, transliterated, unit-normalised form.

    The output is a single space-joined string of tokens suitable for
    a MongoDB `$regex` substring search on a stored `_search_blob`.
    Two product strings that "refer to the same product" (per the
    spec) will normalise to strings whose tokens are a subset of each
    other.
    """
    if not text:
        return ""
    # 1. Transliterate any non-Latin script → ASCII.
    s = unidecode(str(text))
    # 2. Lowercase.
    s = s.lower()
    # 3. Join hyphens / underscores / slashes ("ODC-3" → "odc3").
    s = _JOIN_PUNCT_RE.sub("", s)
    # 4. Replace remaining punctuation with a space.
    s = _SPLIT_PUNCT_RE.sub(" ", s)
    # 5. Apply Indic-code overrides *before* generic unit stripping so
    #    "graam" → "g", "biij" → "seed", etc.  Use a *lookahead* so
    #    the mapping fires even when a digit is glued to the token
    #    ("oddiisii3" → "odc3").
    for src, dst in _PRODUCT_CODE_TRANSLIT.items():
        s = re.sub(
            rf"(?<![a-z]){re.escape(src)}(?![a-z])",
            dst,
            s,
        )
    # 6. Collapse weight units after a digit: "100 gram" → "100g".
    s = _WEIGHT_UNIT_RE.sub(r"\1g", s)
    # 7. Strip quantity suffixes ("x1", "1 pkt", …).
    s = _QTY_X_RE.sub(" ", s)
    s = _QTY_UNIT_RE.sub(" ", s)
    # 8. Collapse whitespace.
    s = _WS_RE.sub(" ", s).strip()
    if not s:
        return ""
    # 9. Multilingual fold: for every token also emit its schwa-dropped
    #    consonant skeleton. Devanagari / Gujarati transliterations
    #    ("bhavanagara") and the native English spelling ("bhavnagar")
    #    both collapse to the same skeleton ("bhvngr"), so an English
    #    query hits a Gujarati stored value and vice-versa.
    tokens = s.split(" ")
    with_compact: List[str] = []
    for t in tokens:
        with_compact.append(t)
        compact = _schwa_compact(t)
        if compact and compact != t:
            with_compact.append(compact)
    return " ".join(with_compact)


def normalize_tokens(text: Optional[str]) -> List[str]:
    """Tokenised, sorted, unique version of normalize_text().

    Sorted so two products with the same tokens in different word
    order produce the exact same list — useful for grouping product
    variants inside the Suggested Filters generator.
    """
    n = normalize_text(text)
    if not n:
        return []
    return sorted({t for t in n.split(" ") if t})


def normalize_tokens_grouped(text: Optional[str]) -> List[List[str]]:
    """Return the per-source-word variant groups for a query.

    Unlike `normalize_tokens` (which flattens and dedupes everything
    into a single sorted set), this walks the *joined* normalised
    string and pairs each token with its schwa-compact sibling —
    exactly the way `normalize_text` emits them.  The result is a
    list of groups; each group holds every equivalent form of a
    single source word.  The query-builder in `list_shipments` /
    `product_suggestions` then AND-s across groups and OR-s inside
    each group, which lets a user's query match either the raw
    Latin form OR its Indic-transliterated compact skeleton.

    Called on the FULL query string so cross-word normalisations
    (e.g. "100 gram" → "100g") fire before we start grouping.
    """
    n = normalize_text(text)
    if not n:
        return []
    tokens = [t for t in n.split(" ") if t]
    groups: List[List[str]] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        compact = _schwa_compact(t)
        if (
            compact
            and compact != t
            and i + 1 < len(tokens)
            and tokens[i + 1] == compact
        ):
            # (raw, compact) pair produced by the fold — group them.
            groups.append([t, compact])
            i += 2
        else:
            groups.append([t])
            i += 1
    # Dedupe identical groups so "kids kids" doesn't AND against
    # itself twice.
    seen = set()
    out: List[List[str]] = []
    for g in groups:
        key = tuple(sorted(set(g)))
        if key in seen:
            continue
        seen.add(key)
        out.append(list(key))
    return out


def build_search_blob(shipment: dict) -> str:
    """Concatenate every searchable field of a shipment into one
    normalised blob suitable for `$regex` substring matching.

    Per the Universal Smart Search spec, every user-visible field
    should be searchable so a single query can match products,
    addresses, courier names, labels, statuses, etc.  Written on
    every create/update in `shipments_write.py` and lazily
    backfilled by `/api/shipments` for legacy docs.
    """
    parts: List[str] = []

    # Every scalar field that a user might reasonably search on.
    _scalar_fields = (
        "tracking_id", "customer_name", "customer_phone", "alt_phone",
        "email", "address", "landmark", "village", "taluka", "district",
        "city", "state", "country", "pincode", "order_id",
        "order_id_hint", "item_description", "shipment_notes",
        "payment_mode", "status", "weight", "courier_service",
        "courier_service_name", "master_order_id",
    )
    for field in _scalar_fields:
        v = shipment.get(field)
        if v not in (None, ""):
            parts.append(str(v))

    # Multi-value fields.
    items = shipment.get("items") or []
    if isinstance(items, list):
        parts.extend(str(x) for x in items if x)

    labels = shipment.get("labels") or []
    if isinstance(labels, list):
        # Labels stored as ids; the caller may pre-resolve them and
        # attach a `_label_names` list before saving. When present,
        # index the human names too so "Ready to dispatch" matches.
        parts.extend(str(x) for x in labels if x)
    label_names = shipment.get("_label_names") or []
    if isinstance(label_names, list):
        parts.extend(str(x) for x in label_names if x)

    # Custom key/value fields — treat both keys and values as text.
    custom = shipment.get("custom_values") or {}
    if isinstance(custom, dict):
        for k, v in custom.items():
            if k:
                parts.append(str(k))
            if v not in (None, ""):
                parts.append(str(v))

    return normalize_text(" ".join(parts))


def query_matches_blob(query: str, blob: str) -> bool:
    """Client-side helper (used by tests) — check if every token in
    the normalised query appears as a substring of `blob`."""
    q_tokens = normalize_tokens(query)
    if not q_tokens:
        return True
    return all(t in blob for t in q_tokens)
