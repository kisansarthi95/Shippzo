"""Generic SMS parser — Phase F5.0.

Reads an SMS parsing config from a Courier document (as stored in the
`couriers` MongoDB collection) and applies the same 3-step matching
that the hardcoded India Post parser used to do:

    1. Sender pattern gate (regex-per-substring, OR)
    2. Tracking-number regex extraction
    3. Status keyword classification → (canonical_status, shipment_status)

The Courier document is expected to carry these fields (all optional,
default empty):

    auto_sync_enabled:          bool
    auto_sync_sender_patterns:  List[str]         # ["INPOST", "IPOST"]
    auto_sync_tracking_regex:   str               # "[A-Z]{2}\\d{9}IN"
    auto_sync_status_rules:     List[Dict]        # see below
    auto_sync_case_sensitive:   bool              # default False

Each status rule dict has this shape:

    {
        "keyword":            "out for delivery",   # substring OR regex
        "canonical_status":   "Out for Delivery",   # pretty label
        "shipment_status":    "Out for Delivery",   # value to write on shipment.status
        "whitelisted":        True,                 # allowed to mutate shipment.status
    }

Rules are evaluated top-to-bottom — the FIRST match wins. Order them
from most-specific (negative phrasings like "could not be delivered")
to least-specific (bare `delivered`).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------
# Reusable extractors (identical to the legacy india_post module)
# --------------------------------------------------------------------
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_POSTMAN_RE = re.compile(
    r"by\s*-\s*([A-Z][A-Za-z .\-]+?)\s*\((BEAT_\d+)\)",
)


def _parse_event_date(text: str) -> Optional[str]:
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        datetime.strptime(m.group(1), "%Y-%m-%d")
        return m.group(1)
    except ValueError:
        return None


def _parse_postman(text: str) -> Optional[Dict[str, str]]:
    if not text:
        return None
    m = _POSTMAN_RE.search(text)
    if not m:
        return None
    return {"postman_name": m.group(1).strip(), "beat": m.group(2).strip()}


# --------------------------------------------------------------------
# Compiled-config cache. Keyed by courier_id + updated_at so a config
# change on the DB row automatically invalidates the cached regexes.
# --------------------------------------------------------------------
_COMPILED: Dict[str, Dict[str, Any]] = {}


def _compile_config(courier: Dict[str, Any]) -> Dict[str, Any]:
    """Compile the raw dict-shaped auto_sync config into ready-to-use
    regexes. Cached per (courier_id + updated_at fingerprint) so a
    hot ingest loop doesn't recompile on every SMS."""
    cid = str(courier.get("id") or "")
    fingerprint = "|".join([
        cid,
        str(courier.get("updated_at") or ""),
        str(courier.get("auto_sync_case_sensitive") or ""),
        ",".join(courier.get("auto_sync_sender_patterns") or []),
        str(courier.get("auto_sync_tracking_regex") or ""),
        str(len(courier.get("auto_sync_status_rules") or [])),
    ])
    hit = _COMPILED.get(cid)
    if hit and hit.get("_fp") == fingerprint:
        return hit

    flags = 0 if courier.get("auto_sync_case_sensitive") else re.IGNORECASE
    senders_raw = [
        s.strip() for s in (courier.get("auto_sync_sender_patterns") or [])
        if s and s.strip()
    ]
    sender_regexes: List[re.Pattern[str]] = []
    for s in senders_raw:
        try:
            sender_regexes.append(re.compile(s, flags))
        except re.error:
            # Fallback to literal substring match on invalid regex.
            sender_regexes.append(re.compile(re.escape(s), flags))

    tracking_re: Optional[re.Pattern[str]] = None
    tregex = (courier.get("auto_sync_tracking_regex") or "").strip()
    if tregex:
        try:
            tracking_re = re.compile(tregex, flags | re.IGNORECASE)
        except re.error:
            tracking_re = None

    status_rules_compiled: List[Dict[str, Any]] = []
    for rule in (courier.get("auto_sync_status_rules") or []):
        kw = (rule.get("keyword") or "").strip()
        if not kw:
            continue
        try:
            rx = re.compile(kw, flags)
        except re.error:
            rx = re.compile(re.escape(kw), flags)
        status_rules_compiled.append({
            "rx":               rx,
            "canonical_status": (rule.get("canonical_status") or "").strip() or "Unknown",
            "shipment_status":  (rule.get("shipment_status") or "").strip(),
            "whitelisted":      bool(rule.get("whitelisted", False)),
        })

    compiled = {
        "_fp":            fingerprint,
        "sender_regexes": sender_regexes,
        "tracking_re":    tracking_re,
        "status_rules":   status_rules_compiled,
        "case_sensitive": bool(courier.get("auto_sync_case_sensitive")),
    }
    _COMPILED[cid] = compiled
    return compiled


def _matches_sender(compiled: Dict[str, Any], text: str) -> bool:
    if not text:
        return False
    if not compiled["sender_regexes"]:
        return False
    return any(rx.search(text) for rx in compiled["sender_regexes"])


def _extract_tracking_ids(compiled: Dict[str, Any], text: str) -> List[str]:
    rx = compiled.get("tracking_re")
    if not rx or not text:
        return []
    seen: List[str] = []
    for m in rx.finditer(text.upper() if not compiled.get("case_sensitive") else text):
        val = m.group(1) if m.groups() else m.group(0)
        val = val.strip()
        if val and val not in seen:
            seen.append(val)
    return seen


def _classify_status(
    compiled: Dict[str, Any],
    text: str,
) -> tuple[str, str, str, bool]:
    """Return (canonical_status, shipment_status, matched_phrase, whitelisted)."""
    if not text:
        return ("Unknown", "", "", False)
    for rule in compiled["status_rules"]:
        m = rule["rx"].search(text)
        if m:
            return (
                rule["canonical_status"],
                rule["shipment_status"],
                m.group(0),
                bool(rule["whitelisted"]),
            )
    return ("Unknown", "", "", False)


def parse_with_courier(
    *,
    courier: Dict[str, Any],
    sender: str = "",
    text: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Try to parse a notification against ONE courier's auto_sync config.

    Returns a dict with `matched=True` on success (partner_key is the
    courier's ID and partner_name is the courier's name). On failure
    returns `matched=False` with a `reason` code so callers can either
    try the next courier or record a specific audit reason.
    """
    if not courier.get("auto_sync_enabled"):
        return {
            "matched":      False,
            "reason":       "courier_auto_sync_disabled",
            "partner_key":  str(courier.get("id") or ""),
            "partner_name": str(courier.get("name") or ""),
            "sender":       sender,
        }
    compiled = _compile_config(courier)

    body = " ".join(s for s in (title, text) if s).strip()

    # 1. Sender gate — try the raw sender header AND the full body
    #    (some Android apps put the DLT header inside the SMS body).
    if not (_matches_sender(compiled, sender) or _matches_sender(compiled, body)):
        return {
            "matched":      False,
            "reason":       "sender_not_matched",
            "partner_key":  str(courier.get("id") or ""),
            "partner_name": str(courier.get("name") or ""),
            "sender":       sender,
        }

    # 2. Tracking ID extraction.
    awbs = _extract_tracking_ids(compiled, body)
    if not awbs:
        return {
            "matched":      False,
            "reason":       "no_tracking_id_in_text",
            "partner_key":  str(courier.get("id") or ""),
            "partner_name": str(courier.get("name") or ""),
            "sender":       sender,
        }

    # 3. Status classification.
    canonical, ship_status, phrase, whitelisted = _classify_status(compiled, body)
    if canonical == "Unknown":
        return {
            "matched":      False,
            "reason":       "no_status_keyword_matched",
            "partner_key":  str(courier.get("id") or ""),
            "partner_name": str(courier.get("name") or ""),
            "sender":       sender,
            "tracking_ids": awbs,
        }

    return {
        "matched":              True,
        "partner_key":          str(courier.get("id") or ""),
        "partner_name":         str(courier.get("name") or ""),
        "sender":               sender,
        "tracking_id":          awbs[0],
        "tracking_ids":         awbs,
        "canonical_status":     canonical,
        "shipment_status":      ship_status if whitelisted else "",
        "matched_phrase":       phrase,
        "whitelisted":          whitelisted,
        "event_date":           _parse_event_date(body),
        "postman":              _parse_postman(body),
        "raw_text":             body,
    }


def parse_with_couriers(
    *,
    couriers: List[Dict[str, Any]],
    sender: str = "",
    text: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Try each courier in order and return the first match. If none
    matched, return the FIRST attempted courier's specific failure
    reason (or a generic 'no_courier_configured' when the list is
    empty)."""
    first_failure: Optional[Dict[str, Any]] = None
    for c in couriers:
        try:
            r = parse_with_courier(courier=c, sender=sender, text=text, title=title)
        except Exception:  # noqa: BLE001 — defensive isolation per courier
            continue
        if r.get("matched"):
            return r
        if first_failure is None and r.get("reason") != "courier_auto_sync_disabled":
            first_failure = r
    if first_failure:
        return first_failure
    return {
        "matched":      False,
        "reason":       "no_courier_configured",
        "partner_key":  "",
        "partner_name": "",
        "sender":       sender,
    }


# --------------------------------------------------------------------
# Defaults used by the migration + Add-Courier UI (starts empty for
# unknown couriers, prefilled for well-known ones so operators can
# tweak instead of typing from scratch).
# --------------------------------------------------------------------
INDIA_POST_DEFAULT_CONFIG: Dict[str, Any] = {
    "auto_sync_enabled":         True,
    "auto_sync_sender_patterns": ["INPOST"],
    "auto_sync_tracking_regex":  r"\b([A-Z]{2}\d{9}IN)\b",
    "auto_sync_case_sensitive":  False,
    "auto_sync_status_rules": [
        # Order matters. Negative phrasings first (would false-match
        # the bare "delivered" rule below otherwise).
        {"keyword": r"could\s+not\s+be\s+delivered",
         "canonical_status": "Undelivered",       "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"undelivered",
         "canonical_status": "Undelivered",       "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"return(ed)?\s+to\s+sender",
         "canonical_status": "RTO",               "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"\brto\b",
         "canonical_status": "RTO",               "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"out\s+for\s+delivery",
         "canonical_status": "Out for Delivery",  "shipment_status": "Out for Delivery", "whitelisted": True},
        {"keyword": r"in\s+transit",
         "canonical_status": "In Transit",        "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"dispatched",
         "canonical_status": "In Transit",        "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"arrived\s+at",
         "canonical_status": "In Transit",        "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"received\s+at",
         "canonical_status": "In Transit",        "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"bag\s+received",
         "canonical_status": "In Transit",        "shipment_status": "",                 "whitelisted": False},
        {"keyword": r"has\s+been\s+delivered",
         "canonical_status": "Delivered",         "shipment_status": "Delivered",        "whitelisted": True},
        {"keyword": r"\bdelivered\b",
         "canonical_status": "Delivered",         "shipment_status": "Delivered",        "whitelisted": True},
        {"keyword": r"has\s+been\s+booked",
         "canonical_status": "Booked",            "shipment_status": "Shipped",          "whitelisted": True},
        {"keyword": r"\bbooked\b",
         "canonical_status": "Booked",            "shipment_status": "Shipped",          "whitelisted": True},
    ],
}


# List of well-known courier names → default config. Used by the
# Create-Courier flow to auto-prefill when the operator types a
# matching name. Empty list is fine for unknown couriers — they
# just start blank.
DEFAULT_CONFIGS_BY_NAME: Dict[str, Dict[str, Any]] = {
    "india post": INDIA_POST_DEFAULT_CONFIG,
}


def default_config_for_name(name: str) -> Optional[Dict[str, Any]]:
    """Return the built-in default auto_sync config for a courier
    name (case-insensitive substring match). None if we don't
    recognise the courier — the operator will fill in from scratch.
    """
    if not name:
        return None
    n = name.strip().lower()
    for key, cfg in DEFAULT_CONFIGS_BY_NAME.items():
        if key in n:
            return cfg
    return None


# Canonical slugs for well-known courier names. Used by the ingest
# audit trail so a courier named "TEST_IndiaPost_xxx" or "My India
# Post" still resolves to `india_post` for grep-ability & backward
# compatibility with pre-F5.0 data.
_KNOWN_SLUGS = {
    "india_post": ("india post", "indiapost", "indian post"),
}


def partner_slug_for_name(name: str) -> str:
    """Return a canonical slug for a courier name. Recognises well-
    known partners (India Post et al.) and falls back to a lowercase,
    space-to-underscore transform for everything else."""
    if not name:
        return ""
    n = name.strip().lower()
    for slug, aliases in _KNOWN_SLUGS.items():
        if any(a in n for a in aliases):
            return slug
        # Also match if all key tokens appear anywhere (handles
        # "Indian post", "IndiaPost Speed", "My India Post" etc.)
        if slug == "india_post" and "india" in n and "post" in n:
            return slug
    return (
        n.replace(" ", "_").replace("-", "_").replace(".", "_")
    )


def canonical_status_choices() -> List[Dict[str, str]]:
    """Return the fixed list of internal stages that a courier keyword
    can map to. Kept in sync with the shipment.status vocabulary."""
    return [
        {"label": "Booked (→ Shipped)",       "canonical": "Booked",             "shipment": "Shipped",             "whitelisted": True},
        {"label": "In Transit",               "canonical": "In Transit",         "shipment": "",                    "whitelisted": False},
        {"label": "Out for Delivery",         "canonical": "Out for Delivery",   "shipment": "Out for Delivery",    "whitelisted": True},
        {"label": "Delivered",                "canonical": "Delivered",          "shipment": "Delivered",           "whitelisted": True},
        {"label": "Undelivered",              "canonical": "Undelivered",        "shipment": "",                    "whitelisted": False},
        {"label": "RTO / Returned",           "canonical": "RTO",                "shipment": "",                    "whitelisted": False},
    ]
