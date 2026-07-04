"""India Post — DLT-SMS parser for Courier Status Auto Sync (Phase 1).

India Post does not expose a public courier-tracking API for general
shippers, so we rely on the DLT-registered SMS notifications they send
to the consignor's phone for every status update. The Android client
(NotificationListenerService) forwards the raw SMS body + sender to
`POST /api/courier-sync/ingest`, which calls this module to extract:

    {
        "tracking_id":  "EG350860840IN",
        "canonical_status": "Out for Delivery",
        "shipment_status":  "Shipped",   # value to write on Shipment.status
        "sub_status":       "out for delivery",  # raw verb for display
        "event_at":         <iso ts or None>,
        "matched":          True,
    }

Sender ID format (India Post DLT):
    [Telco-prefix]-INPOST-G    e.g.  VA-INPOST-G, VK-INPOST-G,
    JD-INPOST-G, AX-INPOST-G, BP-INPOST-G

Tracking pattern:
    Two upper-case letters + 9 digits + "IN"   e.g. EG350860840IN,
    EM123456789IN, RU987654321IN, CP123456789IN, EE111122222IN.

Known message templates (collected from operator screenshots):
    1. "Item: <AWB> has been Booked at <PO> on <date> ... - IndiaPost"
    2. "Item: <AWB> is out for delivery. Delivery will be attempted
       by - <Postman Name> (BEAT_NN) - on <date> - IndiaPost"
    3. "Item: <AWB> has been Delivered on <date> at <PO> - IndiaPost"
    4. "Item: <AWB> could not be delivered ... - IndiaPost"  (Undelivered)
    5. "Item: <AWB> has been returned ... - IndiaPost"  (RTO)
    6. "Item: <AWB> ... in transit / dispatched / arrived at <PO>"
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Optional

# --------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------
PARTNER_KEY  = "india_post"
PARTNER_NAME = "India Post"

# Sender-ID matchers. We accept any DLT header that contains
# "INPOST" (case-insensitive) so future telco-prefix variations
# (today: VA-, VK-, JD-, AX-, BP-, etc.) don't break detection.
_SENDER_RE = re.compile(r"INPOST", re.IGNORECASE)

# AWB / Tracking ID — strict 13-char ICAO-style barcode used by
# India Post Speed Post, Registered Post, and EMS. "\b" anchors
# prevent matching inside larger alphanumeric blobs.
TRACKING_RE = re.compile(r"\b([A-Z]{2}\d{9}IN)\b")

# YYYY-MM-DD inside the SMS — used to stamp event_at when the
# message carries an explicit date (most India Post templates do).
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

# Optional postman/beat block — useful for analytics later.
_POSTMAN_RE = re.compile(
    r"by\s*-\s*([A-Z][A-Za-z .\-]+?)\s*\((BEAT_\d+)\)",
)

# --------------------------------------------------------------------
# Status whitelist — ONLY these canonical statuses are allowed to
# mutate the shipment.status field. Everything else is parsed and
# recorded as an audit event but does NOT touch the shipment row.
#
# Rationale: intermediate events (Out for Delivery, In Transit,
# Dispatched, Arrived) churn the DB on every postman scan without
# adding actionable signal — operators want only the two terminal
# states that anchor the lifecycle: initial booking and final
# delivery. RTO / Undelivered are intentionally also excluded from
# the whitelist for Phase 1 (manual confirmation required) and may
# be added in a later phase.
# --------------------------------------------------------------------
STATUS_UPDATE_WHITELIST: frozenset[str] = frozenset({"Booked", "Out for Delivery", "Delivered"})

# --------------------------------------------------------------------
# Status keyword map  (canonical_status, shipment_status)
# Order matters — most specific phrases first. Negative phrasings
# ("could not be delivered", "returned to sender") MUST come before
# the bare `\bdelivered\b` rule to avoid false positives.
#
# `shipment_status` is intentionally empty ("") for every canonical
# that is NOT in STATUS_UPDATE_WHITELIST — the router uses that
# emptiness as a secondary safety net.
# --------------------------------------------------------------------
_STATUS_RULES = [
    # phrase                             canonical            ship status
    # ── Negative / RTO phrasings first (would false-match "delivered" below)
    (r"could\s+not\s+be\s+delivered",    "Undelivered",       ""),
    (r"undelivered",                     "Undelivered",       ""),
    (r"return(ed)?\s+to\s+sender",       "RTO",               ""),
    (r"\brto\b",                         "RTO",               ""),
    # ── Intermediate transit / scan events — parsed but ignored
    # Note: "Out for Delivery" IS whitelisted (see STATUS_UPDATE_WHITELIST)
    #       so we DO set shipment_status="Out for Delivery" — the router
    #       then also appends to `out_for_delivery_history` with postman
    #       details for the 2-hour SLA alert.
    (r"out\s+for\s+delivery",            "Out for Delivery",  "Out for Delivery"),
    (r"in\s+transit",                    "In Transit",        ""),
    (r"dispatched",                      "In Transit",        ""),
    (r"arrived\s+at",                    "In Transit",        ""),
    (r"received\s+at",                   "In Transit",        ""),
    (r"bag\s+received",                  "In Transit",        ""),
    # ── Whitelisted terminal/initial statuses that DO mutate shipment.status
    (r"has\s+been\s+delivered",          "Delivered",         "Delivered"),
    (r"\bdelivered\b",                   "Delivered",         "Delivered"),
    (r"has\s+been\s+booked",             "Booked",            "Shipped"),
    (r"\bbooked\b",                      "Booked",            "Shipped"),
]
_STATUS_RULES = [(re.compile(p, re.IGNORECASE), c, s) for (p, c, s) in _STATUS_RULES]


def matches_sender(sender: str) -> bool:
    """True iff `sender` looks like an India Post DLT header.

    Accepts both raw header (`VA-INPOST-G`) and freeform notification
    titles where the sender id might be embedded (`SMS from VA-INPOST-G`).
    """
    if not sender:
        return False
    return bool(_SENDER_RE.search(sender))


def extract_tracking_ids(text: str) -> list[str]:
    """Return every India-Post-shaped AWB found in `text` (deduped,
    order preserved). One SMS usually carries exactly one AWB but
    we tolerate batched messages.
    """
    if not text:
        return []
    seen: list[str] = []
    for m in TRACKING_RE.finditer(text.upper()):
        awb = m.group(1)
        if awb not in seen:
            seen.append(awb)
    return seen


def classify_status(text: str) -> tuple[str, str, str]:
    """Return (canonical_status, shipment_status, matched_phrase).

    `canonical_status` — pretty display label ("Out for Delivery",
      "Delivered", "RTO", "In Transit", "Undelivered", "Booked",
      or "Unknown" if nothing matched).
    `shipment_status` — value to write on Shipment.status (one of
      "Shipped" / "Delivered" / "Returned" — keeps the existing
      shipment lifecycle compatible). Empty string when canonical
      is "Unknown".
    `matched_phrase` — substring that triggered the match (for audit).
    """
    if not text:
        return ("Unknown", "", "")
    for rx, canonical, ship in _STATUS_RULES:
        m = rx.search(text)
        if m:
            return (canonical, ship, m.group(0))
    return ("Unknown", "", "")


def _parse_event_date(text: str) -> Optional[str]:
    """Extract YYYY-MM-DD from the SMS and return ISO date string
    (no time component — India Post SMS rarely carries the clock).
    Returns None if no date is found.
    """
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        # Validate it's a real date (rejects 2026-13-40 etc.)
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


def parse(
    *,
    sender: str = "",
    text: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Top-level parser used by the ingest endpoint.

    Returns a dict with `matched: False` if the notification does NOT
    look like an India Post SMS update (sender doesn't match OR no
    tracking ID found OR no recognisable status keyword). The caller
    can short-circuit and skip the shipment lookup in that case.
    """
    body = " ".join(s for s in (title, text) if s).strip()

    # 1. Sender gate — primary filter. Avoids false positives from
    #    unrelated apps that happen to contain a 2L+9D+IN sequence.
    sender_ok = matches_sender(sender) or matches_sender(body)
    if not sender_ok:
        return {
            "matched":           False,
            "reason":            "sender_not_india_post",
            "partner_key":       PARTNER_KEY,
            "partner_name":      PARTNER_NAME,
            "sender":            sender,
        }

    # 2. Tracking ID extraction.
    awbs = extract_tracking_ids(body)
    if not awbs:
        return {
            "matched":           False,
            "reason":            "no_tracking_id_in_text",
            "partner_key":       PARTNER_KEY,
            "partner_name":      PARTNER_NAME,
            "sender":            sender,
        }

    # 3. Status classification.
    canonical, ship_status, phrase = classify_status(body)
    if canonical == "Unknown":
        return {
            "matched":           False,
            "reason":            "no_status_keyword_matched",
            "partner_key":       PARTNER_KEY,
            "partner_name":      PARTNER_NAME,
            "sender":            sender,
            "tracking_ids":      awbs,
        }

    return {
        "matched":              True,
        "partner_key":          PARTNER_KEY,
        "partner_name":         PARTNER_NAME,
        "sender":               sender,
        "tracking_id":          awbs[0],
        "tracking_ids":         awbs,
        "canonical_status":     canonical,
        "shipment_status":      ship_status,
        "matched_phrase":       phrase,
        "event_date":           _parse_event_date(body),
        "postman":              _parse_postman(body),
        "raw_text":             body,
    }


# Default tracking-pattern regex (string form) exposed to the frontend
# so the Android client can prefilter notifications before posting.
TRACKING_PATTERN_STR = r"[A-Z]{2}\d{9}IN"

# Sender pattern (substring) exposed to frontend for the same reason.
SENDER_PATTERN_STR = r"INPOST"
