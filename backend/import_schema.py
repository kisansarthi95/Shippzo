"""
Shared import-schema constants for CSV/Excel uploads + Webhook ingest.

Single source of truth so the same field list, aliases, and coercions
power both /api/orders/import/* (file upload) and /api/webhook/orders/*
(JSON webhook). Add new fields here exactly once.

Phase F2.1 (2026-05-09):
  • Added `status` and `created_at_override` so a CSV / Sheet / Webhook
    that already carries shipment status (Shipped / Delivered / etc.)
    and a real-world timestamp lands directly on the resulting shipment
    instead of defaulting to "Pending" + now().
  • Status normalisation: free-text values like "shipped", "dispatched",
    "ready to ship", "delivered" are matched (case-insensitive) to the
    canonical pipeline statuses. Legacy "Dispatch" → "Ready to Ship".
  • Timestamp parsing uses dateutil for ISO 8601 + common DD/MM/YYYY
    fallbacks; failures yield "" (no override) instead of raising.
  • Per-user custom fields can be mapped by prefixing the schema-field
    id with "custom:" (e.g. mapping a column to "custom:<custom_id>"
    routes its value into the PendingOrder.custom_values dict).
"""
from typing import Any, Dict, List, Set


# Order-of-importance for the field-picker modal in the import UI —
# most-mapped fields sit at the top so users find them immediately.
SCHEMA_FIELDS: List[str] = [
    # Identity
    "customer_name",
    "customer_phone",
    "customer_alt_phone",
    "customer_email",
    "customer_gstin",
    # Delivery address (virtual; persisted to address_line1)
    "address",
    "city",
    "state",
    "pincode",
    # Payment
    "amount",
    "token_amount",
    "payment_mode",
    # Items / parcel
    "items",
    "category",
    "weight",
    "box_dimensions",
    "box_length",
    "box_width",
    "box_height",
    # Misc
    "courier_hint",
    "order_id",
    "notes",
    # Phase F2.1 — order-state metadata. When populated by the import,
    # ship_pending_order copies these onto the resulting Shipment so the
    # row lands in the right pipeline bucket (e.g. already-Shipped) and
    # carries the historical timestamp instead of "now".
    "status",
    "created_at_override",
]

NUMERIC_FIELDS: Set[str] = {
    "amount", "token_amount",
    "box_length", "box_width", "box_height",
}

PAYMENT_MODE_NORMALISE: Dict[str, str] = {
    "cod": "COD", "c": "COD", "cash on delivery": "COD",
    "paid": "PAID", "p": "PAID", "prepaid": "PAID",
    "online": "PAID", "upi": "PAID",
}


# Canonical pipeline statuses (exact strings stored in shipments.status).
# Mirrors the STATUS_META keys in /app/frontend/app/(tabs)/shipments.tsx.
# IMPORTANT: "Dispatch" is intentionally absent — every import value
# that previously meant Dispatch (dispatched, ready_to_ship, etc.) is
# normalised to the user-facing label "Ready to Ship".
CANONICAL_STATUSES: List[str] = [
    "Pending",
    "Processing",
    "Ready to Ship",
    "Shipped",
    "Delivered",
    "Feedback",
    "Modified",
    "Cancel by buyer",
    "Cancelled",
    "Returned",
]

# Lower-case input → canonical output. Anything not matched here falls
# back to title-case of the raw value (no validation crash).
STATUS_ALIASES: Dict[str, str] = {
    "pending":              "Pending",
    "new":                  "Pending",
    "open":                 "Pending",
    "to_pack":              "Pending",
    "to pack":              "Pending",

    "processing":           "Processing",
    "packing":              "Processing",
    "packed":               "Processing",
    "in_progress":          "Processing",
    "in progress":          "Processing",
    "preparing":            "Processing",

    "ready":                "Ready to Ship",
    "ready_to_ship":        "Ready to Ship",
    "ready to ship":        "Ready to Ship",
    "readytoship":          "Ready to Ship",
    "rts":                  "Ready to Ship",
    # Legacy aliases — old DB rows + external systems may still use
    # "Dispatch" / "Dispatched" / "DISPATCH". They all collapse to
    # the new canonical label per Phase F2.1 cleanup.
    "dispatch":             "Ready to Ship",
    "dispatched":           "Ready to Ship",
    "dispatching":          "Ready to Ship",

    "shipped":              "Shipped",
    "in_transit":           "Shipped",
    "in transit":           "Shipped",
    "intransit":            "Shipped",
    "out for delivery":     "Shipped",  # we don't track OFD separately
    "out_for_delivery":     "Shipped",
    "ofd":                  "Shipped",
    "picked_up":            "Shipped",
    "picked up":            "Shipped",

    "delivered":            "Delivered",
    "complete":             "Delivered",
    "completed":            "Delivered",
    "done":                 "Delivered",
    "received":             "Delivered",

    "feedback":             "Feedback",
    "review":               "Feedback",
    "reviewed":             "Feedback",
    "rated":                "Feedback",

    "modified":             "Modified",
    "edited":               "Modified",

    "cancel_by_buyer":      "Cancel by buyer",
    "cancel by buyer":      "Cancel by buyer",
    "buyer_cancelled":      "Cancel by buyer",
    "buyer cancelled":      "Cancel by buyer",
    "cancelled_by_buyer":   "Cancel by buyer",

    "cancel":               "Cancelled",
    "cancelled":            "Cancelled",
    "canceled":             "Cancelled",
    "void":                 "Cancelled",
    "rejected":             "Cancelled",

    "return":               "Returned",
    "returned":             "Returned",
    "rto":                  "Returned",
    "refund":               "Returned",
    "refunded":             "Returned",
}


# Header / JSON-key aliases → canonical schema field. Lowercase,
# spaces → underscores, hyphens → underscores BEFORE lookup.
HEADER_ALIASES: Dict[str, str] = {
    # Address — all variants merge into the single `address` field
    "address":         "address",
    "addr":            "address",
    "address_line":    "address",
    "address_line_1":  "address",
    "address_line_2":  "address",
    "address_1":       "address",
    "address_2":       "address",
    "addressline1":    "address",
    "addressline2":    "address",
    "full_address":    "address",
    "delivery_address":"address",
    # Identity
    "name":            "customer_name",
    "customer":        "customer_name",
    "phone":           "customer_phone",
    "mobile":          "customer_phone",
    "contact":         "customer_phone",
    "alt_phone":       "customer_alt_phone",
    "email":           "customer_email",
    "gst":             "customer_gstin",
    "gstin":           "customer_gstin",
    # Address fragments
    "pin":             "pincode",
    "pin_code":        "pincode",
    "zip":             "pincode",
    # Payment
    "amt":             "amount",
    "total":           "amount",
    "order_amount":    "amount",
    "order_value":     "amount",
    "token":           "token_amount",
    "advance":         "token_amount",
    "advance_amount":  "token_amount",
    "token_amt":       "token_amount",
    "mode":            "payment_mode",
    "payment":         "payment_mode",
    # Courier
    "courier":         "courier_hint",
    "logistics":       "courier_hint",
    # Items / parcel
    "wt":              "weight",
    "parcel_weight":   "weight",
    "package_weight":  "weight",
    "remarks":         "notes",
    "comment":         "notes",
    "comments":        "notes",
    "shipment_notes":  "notes",
    "box":             "box_dimensions",
    "box_size":        "box_dimensions",
    "dimensions":      "box_dimensions",
    "lwh":             "box_dimensions",
    "size":            "box_dimensions",
    "length":          "box_length",
    "l":               "box_length",
    "breadth":         "box_width",
    "width":           "box_width",
    "w":               "box_width",
    "height":          "box_height",
    "h":               "box_height",
    "category":        "category",
    "cat":             "category",
    "item_category":   "category",
    "item":            "items",
    "product":         "items",
    "products":        "items",
    # Phase F2.1 — order state + timestamp aliases
    "status":          "status",
    "stage":           "status",          # legacy column header
    "order_status":    "status",
    "shipment_status": "status",
    "state":           "status",
    "current_status":  "status",
    "delivery_status": "status",

    "timestamp":       "created_at_override",
    "date":            "created_at_override",
    "order_date":      "created_at_override",
    "created":         "created_at_override",
    "created_at":      "created_at_override",
    "created_on":      "created_at_override",
    "placed_at":       "created_at_override",
    "placed_on":       "created_at_override",
    "ordered_on":      "created_at_override",
    "order_time":      "created_at_override",

    # Phase F2.4 — Dukaan webhook payload aliases. Dukaan's order-
    # received event sends keys like "order.buyer.name",
    # "order.shipping_address.address_1", "order.total_cost". After
    # _flatten() those become dotted paths; we add explicit one-shot
    # entries for the leaf names so suggest_mapping picks them up.
    "buyer_name":         "customer_name",
    "buyer.name":         "customer_name",
    "buyer_phone":        "customer_phone",
    "buyer.phone":        "customer_phone",
    "buyer_email":        "customer_email",
    "buyer.email":        "customer_email",
    "shipping_address.address_1": "address",
    "shipping_address.address_2": "address",
    "shipping_address.city":      "city",
    "shipping_address.state":     "state",
    "shipping_address.pincode":   "pincode",
    "shipping_address.country":   "state",
    "address_1":          "address",
    "address_2":          "address",
    "total_cost":         "amount",
    "total":              "amount",
    "subtotal":           "amount",
    "order_total":        "amount",
    "uuid":               "order_id",
    "display_order_id":   "order_id",
    "order_status":       "status",
    "is_cod":             "payment_mode",
    "payment_method":     "payment_mode",
    "buyer_address":      "address",
    "buyer.address":      "address",
    "shipping_address":   "address",
    "shipping_address.full_address": "address",
}


def normalise_status(raw: Any) -> str:
    """Coerce a free-text status into one of CANONICAL_STATUSES.

    Falls back to title-cased raw value so unknown statuses still get
    stored (the user can fix later) without breaking the import.
    Returns "" if the cell is blank / None.
    """
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    key = s.lower().replace("-", " ").replace("_", " ")
    key = " ".join(key.split())   # collapse repeated whitespace
    if key in STATUS_ALIASES:
        return STATUS_ALIASES[key]
    # Try the underscored variant too (e.g. "ready_to_ship" came in as
    # "ready_to_ship" pre-collapse).
    key2 = key.replace(" ", "_")
    if key2 in STATUS_ALIASES:
        return STATUS_ALIASES[key2]
    # Already a canonical value with mixed casing? E.g. "SHIPPED".
    for canon in CANONICAL_STATUSES:
        if canon.lower() == key:
            return canon
    # Unknown — keep the user's value title-cased. Will land in the
    # shipment as-is; UI's STATUS_META falls back to the default chip.
    return s.title()


def normalise_timestamp(raw: Any) -> str:
    """Parse a free-text date/time cell into an ISO 8601 string in UTC.

    Empty cells / unparseable values return "" (caller treats as "no
    override, use server now()"). dateutil handles ISO, RFC, common
    locale formats. We additionally try day-first DD/MM/YYYY since
    Indian Excel exports default to that.
    """
    if raw is None:
        return ""
    if hasattr(raw, "isoformat"):     # native datetime / date
        try:
            return raw.isoformat()
        except Exception:
            pass
    s = str(raw).strip()
    if not s:
        return ""
    try:
        from dateutil import parser as _dateparser
        # Try default first, then dayfirst (Indian DD/MM/YYYY).
        try:
            dt = _dateparser.parse(s, fuzzy=True)
        except Exception:
            dt = _dateparser.parse(s, fuzzy=True, dayfirst=True)
        return dt.isoformat()
    except Exception:
        return ""


def normalise_value(field: str, raw: Any) -> Any:
    """Coerce a raw cell/JSON value into the type the schema expects."""
    if field == "status":
        return normalise_status(raw)
    if field == "created_at_override":
        return normalise_timestamp(raw)
    if raw is None:
        return 0.0 if field in NUMERIC_FIELDS else ""
    s = str(raw).strip()
    if field in NUMERIC_FIELDS:
        if not s:
            return 0.0
        try:
            return float(s.replace(",", "").replace("₹", "").strip())
        except ValueError:
            return 0.0
    if field == "payment_mode":
        return PAYMENT_MODE_NORMALISE.get(
            s.lower(),
            "COD" if not s else s.upper(),
        )
    if field == "pincode":
        return "".join(ch for ch in s if ch.isdigit())
    return s


def is_custom_field_mapping(field: str) -> bool:
    """True if the mapping value targets a per-user custom field
    (prefixed with "custom:<custom_field_id>")."""
    return isinstance(field, str) and field.startswith("custom:")


def custom_field_id(field: str) -> str:
    """Extract the custom-field id from a "custom:<id>" mapping value."""
    return field.split(":", 1)[1] if is_custom_field_mapping(field) else ""


def suggest_mapping(
    columns: List[str],
    saved: Dict[str, str] | None = None,
    custom_fields: List[Dict[str, Any]] | None = None,
) -> Dict[str, str]:
    """Build an auto-suggested col-header → schema-field mapping using
    saved-default first, then HEADER_ALIASES + lowercase-snake_case
    direct match.

    `custom_fields` is an optional list of the user's CustomLabelField
    dicts (need at least `id` + `label` keys). If a column header matches
    a custom field's label, we auto-suggest "custom:<id>".
    """
    saved = saved or {}
    out: Dict[str, str] = {}
    custom_lookup: Dict[str, str] = {}
    for cf in (custom_fields or []):
        # NOTE: user_custom_fields docs store the human label under
        # `name` (not `label`). Older drafts of this helper assumed
        # `label`; keep both lookups so a future schema rename is safe.
        lbl = (cf.get("name") or cf.get("label") or "").strip().lower()
        if lbl:
            custom_lookup[lbl] = f"custom:{cf.get('id')}"
            custom_lookup[lbl.replace(" ", "_")] = f"custom:{cf.get('id')}"
    for c in columns:
        if c in saved and saved[c]:
            out[c] = saved[c]
            continue
        cl = c.lower().strip().replace(" ", "_").replace("-", "_")
        if cl in SCHEMA_FIELDS:
            out[c] = cl
        elif cl in HEADER_ALIASES:
            out[c] = HEADER_ALIASES[cl]
        elif cl.replace("_", " ") in custom_lookup:
            out[c] = custom_lookup[cl.replace("_", " ")]
        elif cl in custom_lookup:
            out[c] = custom_lookup[cl]
    return out


def validate_mapping_field(
    field: str,
    custom_field_ids: Set[str] | None = None,
) -> bool:
    """True when the mapping value is either a known schema field or
    a "custom:<id>" pointer to one of the user's custom fields."""
    if not field:
        return True   # blank == "ignore this column"
    if field in SCHEMA_FIELDS:
        return True
    if is_custom_field_mapping(field):
        if custom_field_ids is None:
            return True   # caller will validate later
        return custom_field_id(field) in custom_field_ids
    return False


def build_pending_doc_from_mapping(
    row: Dict[str, Any],
    mapping: Dict[str, str],
) -> Dict[str, Any]:
    """Apply the (col → field) mapping to a single source row and
    return the schema-field dict ready to merge into a pending_orders
    document. Multi-column "address" mappings auto-merge with " ".

    Custom-field mappings ("custom:<id>") are collected into a
    `custom_values` dict on the returned doc.

    `row` may have either string or arbitrary-typed values (e.g. JSON
    webhook payloads pass numbers / null directly).
    """
    field_to_cols: Dict[str, List[str]] = {}
    custom_to_cols: Dict[str, List[str]] = {}
    for col, field in mapping.items():
        if not field:
            continue
        if is_custom_field_mapping(field):
            custom_to_cols.setdefault(custom_field_id(field), []).append(col)
            continue
        if field not in SCHEMA_FIELDS:
            raise ValueError(f"Unknown schema field in mapping: {field}")
        field_to_cols.setdefault(field, []).append(col)

    out: Dict[str, Any] = {}
    # Defaults
    for f in SCHEMA_FIELDS:
        if f == "address":
            continue
        if f in NUMERIC_FIELDS:
            out[f] = 0.0
        elif f == "payment_mode":
            out[f] = "COD"
        else:
            out[f] = ""
    out["address_line1"] = ""
    out["address_line2"] = ""
    out["custom_values"] = {}

    # Apply mappings — last-mapped wins for non-address fields.
    for field, cols in field_to_cols.items():
        if field == "address":
            parts = [str(row.get(c) or "").strip() for c in cols]
            out["address_line1"] = " ".join(p for p in parts if p)
            continue
        out[field] = normalise_value(field, row.get(cols[-1]))

    # Custom-field values flow into the dict — string-coerced.
    for cf_id, cols in custom_to_cols.items():
        v = row.get(cols[-1])
        out["custom_values"][cf_id] = "" if v is None else str(v).strip()

    return out
