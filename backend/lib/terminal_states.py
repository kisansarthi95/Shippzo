"""
Terminal (dead-state) status constants — Phase-33.

Once a shipment enters ANY of these statuses, it is permanently
locked from the user's perspective:
  * No status transitions (forward or backward)
  * No re-shipment, re-label, re-printing of operational artefacts
  * No re-imports / duplicates from webhook/sheet/file ingestion

What IS still allowed:
  * View order details / history
  * Contact the customer (WhatsApp / phone call)
  * Notes that don't change processing state

Why a single source-of-truth module?
  * Backend (routers/shipments_write.py, routers/pending_orders.py,
    routers/webhook.py, routers/webhooks_multi.py, sheet ingestion)
    and frontend share the same vocabulary. Any drift would be a
    correctness bug (e.g. a card looks editable but the API rejects
    the write, or vice versa). Keeping one Python module + one
    matching TS module under /app/frontend/lib/terminalStates.ts
    is the cheapest way to guarantee parity.

Note on casing:
  * Shipments use Title-case ("Cancelled", "Cancel by buyer",
    "Returned") to match the existing Mongo data. The set is
    case-insensitive on lookup but we standardise to Title-case
    on writes.
  * Pending Orders historically use lower-case ("pending",
    "shipped", "cancelled"). We add "cancelled" there.
"""
from typing import Set

# Shipments — the 3 terminal statuses spelled exactly as they sit in Mongo today.
TERMINAL_SHIPMENT_STATUSES: Set[str] = {
    "Cancelled",
    "Cancel by buyer",
    "Returned",
}

# Pending Orders use a different casing convention.
TERMINAL_PENDING_STATUSES: Set[str] = {
    "cancelled",
}

# Lowercased lookup set for shipment statuses (defensive — some
# legacy clients send lowercase). Used by helpers below.
_TERMINAL_SHIPMENT_LC = {s.lower() for s in TERMINAL_SHIPMENT_STATUSES}


def is_terminal_shipment_status(status) -> bool:
    """True when the given status string belongs to the shipment dead set.

    Defensive against:
      * None / empty
      * Casing drift ("cancelled", "CANCELLED")
      * Surrounding whitespace ("Cancelled ")
    """
    if not status:
        return False
    return str(status).strip().lower() in _TERMINAL_SHIPMENT_LC


def is_terminal_pending_status(status) -> bool:
    """True when the pending-order status equals the dead-state value."""
    if not status:
        return False
    return str(status).strip().lower() in {s.lower() for s in TERMINAL_PENDING_STATUSES}


# Convenience: the human-readable error string. Used by every endpoint
# that rejects a write so the frontend can pattern-match the message
# and surface a friendlier toast / banner.
TERMINAL_LOCK_DETAIL = (
    "This order is locked. Cancelled / Returned orders cannot be "
    "modified, re-shipped, or restored."
)
