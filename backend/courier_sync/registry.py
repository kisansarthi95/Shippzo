"""Courier Auto Sync — partners registry.

Central lookup table mapping partner_key → parser module. Adding a new
partner (e.g. Blue Dart, DTDC) is a one-line change here once their
parser module is written.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import india_post

# ------------------------------------------------------------------
# Public registry
# ------------------------------------------------------------------
PARTNERS: Dict[str, Dict[str, Any]] = {
    india_post.PARTNER_KEY: {
        "key":              india_post.PARTNER_KEY,
        "name":             india_post.PARTNER_NAME,
        "channel":          "sms",  # how the notifications arrive
        "tracking_pattern": india_post.TRACKING_PATTERN_STR,
        "sender_pattern":   india_post.SENDER_PATTERN_STR,
        "parse":            india_post.parse,
        "matches_sender":   india_post.matches_sender,
        # Canonical statuses that are allowed to mutate shipment.status.
        # Everything else parsed by this partner is recorded as an audit
        # event but does NOT touch the shipment row. See india_post.py
        # for the rationale (Phase 1 = only Booked + Delivered).
        "status_update_whitelist": india_post.STATUS_UPDATE_WHITELIST,
        # Brief operator-facing description (English; UI may swap to
        # Gujarati via i18n later).
        "description":      (
            "Reads DLT SMS from senders like 'VA-INPOST-G' and "
            "auto-updates shipments by AWB (e.g., EG350860840IN)."
        ),
    },
}


def get_partner(key: str) -> Optional[Dict[str, Any]]:
    """Return the partner config dict or None if unknown."""
    if not key:
        return None
    return PARTNERS.get(str(key).strip().lower())


def list_partners() -> List[Dict[str, Any]]:
    """Return a JSON-serialisable list (drops the python callables)."""
    out: List[Dict[str, Any]] = []
    for cfg in PARTNERS.values():
        out.append({
            "key":              cfg["key"],
            "name":             cfg["name"],
            "channel":          cfg["channel"],
            "tracking_pattern": cfg["tracking_pattern"],
            "sender_pattern":   cfg["sender_pattern"],
            "description":      cfg["description"],
        })
    return out


def parse_notification(
    sender: str = "",
    text: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Try every registered partner; return the first match.

    Returns a dict with `matched: False` and `partner_key: ""` when
    NO partner recognised the notification. When at least one partner
    was tried but rejected, the FIRST partner's failure dict is
    returned verbatim (carrying the specific `reason`) so callers /
    the test-parse UI can show exactly why the SMS was ignored.
    """
    last_failure: Dict[str, Any] | None = None
    for cfg in PARTNERS.values():
        try:
            fn: Callable[..., Dict[str, Any]] = cfg["parse"]
            r = fn(sender=sender, text=text, title=title)
            if r.get("matched"):
                return r
            # Capture the FIRST attempted partner's specific failure
            # reason. Later partners don't override it — the user is
            # almost always shipping with a single primary partner
            # (Phase 1 = India Post), so the first reason is the
            # actionable one to surface.
            if last_failure is None:
                last_failure = r
        except Exception:
            # Defensive — never let one parser explode the ingest pipeline.
            continue
    if last_failure is not None:
        return last_failure
    # No partner was even tried (registry empty).
    return {
        "matched":           False,
        "reason":            "no_partner_matched",
        "partner_key":       "",
        "sender":            sender,
    }
