"""
Shared import-schema constants for CSV/Excel uploads + Webhook ingest.

Single source of truth so the same field list, aliases, and coercions
power both /api/orders/import/* (file upload) and /api/webhook/orders/*
(JSON webhook). Add new fields here exactly once.
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
}


def normalise_value(field: str, raw: Any) -> Any:
    """Coerce a raw cell/JSON value into the type the schema expects."""
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


def suggest_mapping(
    columns: List[str],
    saved: Dict[str, str] | None = None,
) -> Dict[str, str]:
    """Build an auto-suggested col-header → schema-field mapping using
    saved-default first, then HEADER_ALIASES + lowercase-snake_case
    direct match."""
    saved = saved or {}
    out: Dict[str, str] = {}
    for c in columns:
        if c in saved and saved[c]:
            out[c] = saved[c]
            continue
        cl = c.lower().strip().replace(" ", "_").replace("-", "_")
        if cl in SCHEMA_FIELDS:
            out[c] = cl
        elif cl in HEADER_ALIASES:
            out[c] = HEADER_ALIASES[cl]
    return out


def build_pending_doc_from_mapping(
    row: Dict[str, Any],
    mapping: Dict[str, str],
) -> Dict[str, Any]:
    """Apply the (col → field) mapping to a single source row and
    return the schema-field dict ready to merge into a pending_orders
    document. Multi-column "address" mappings auto-merge with " ".

    `row` may have either string or arbitrary-typed values (e.g. JSON
    webhook payloads pass numbers / null directly).
    """
    field_to_cols: Dict[str, List[str]] = {}
    for col, field in mapping.items():
        if not field:
            continue
        if field not in SCHEMA_FIELDS:
            raise ValueError(f"Unknown schema field in mapping: {field}")
        field_to_cols.setdefault(field, []).append(col)

    out: Dict[str, Any] = {}
    # Defaults
    for f in SCHEMA_FIELDS:
        if f == "address":
            continue
        out[f] = 0.0 if f in NUMERIC_FIELDS else ("COD" if f == "payment_mode" else "")
    out["address_line1"] = ""
    out["address_line2"] = ""

    # Apply mappings
    for field, cols in field_to_cols.items():
        if field == "address":
            parts = [str(row.get(c) or "").strip() for c in cols]
            out["address_line1"] = " ".join(p for p in parts if p)
            continue
        out[field] = normalise_value(field, row.get(cols[-1]))

    return out
