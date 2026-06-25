"""Courier Status Auto Sync package.

Provides per-partner SMS / push notification parsers that extract a
tracking ID + canonical status from raw text and feed it into the
shipments collection (via routers/courier_sync.py).

Phase 1 — India Post (DLT SMS via Android NotificationListenerService).
"""
from .registry import (
    PARTNERS,
    get_partner,
    list_partners,
    parse_notification,
)

__all__ = [
    "PARTNERS",
    "get_partner",
    "list_partners",
    "parse_notification",
]
