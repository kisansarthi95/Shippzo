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

    # Phase F3.4 — Shopify / Dukaan / Meesho / WooCommerce nested-path
    # aliases. After suggest_mapping() prefix-strips known root +
    # address-container prefixes (see PREFIXES_TO_STRIP below) the
    # tail-only key is tried against this dict. So entries here are
    # written as the LEAF + parent (where it disambiguates), e.g.
    # "billing_address.zip" survives even after "order." is stripped.
    "first_name":          "customer_name",
    "last_name":           "customer_name",
    "full_name":           "customer_name",
    "fullname":            "customer_name",
    "customer_first_name": "customer_name",
    "customer_last_name":  "customer_name",
    "customer_full_name":  "customer_name",
    "customer.first_name": "customer_name",
    "customer.last_name":  "customer_name",
    "customer.full_name":  "customer_name",
    "customer.name":       "customer_name",
    "customer.email":      "customer_email",
    "customer.phone":      "customer_phone",
    "billing_address.first_name": "customer_name",
    "billing_address.last_name":  "customer_name",
    "billing_address.full_name":  "customer_name",
    "billing_address.name":       "customer_name",
    "billing_address.phone":      "customer_phone",
    "billing_address.email":      "customer_email",
    "billing_address.address1":   "address",
    "billing_address.address_1":  "address",
    "billing_address.address2":   "address",
    "billing_address.address_2":  "address",
    "billing_address.line1":      "address",
    "billing_address.line2":      "address",
    "billing_address.area":       "address",
    "billing_address.landmark":   "address",
    "billing_address.locality":   "address",
    "billing_address.city":       "city",
    "billing_address.state":      "state",
    "billing_address.province":   "state",
    "billing_address.zip":        "pincode",
    "billing_address.postcode":   "pincode",
    "billing_address.postal_code":"pincode",
    "billing_address.pincode":    "pincode",
    "shipping_address.first_name":"customer_name",
    "shipping_address.last_name": "customer_name",
    "shipping_address.full_name": "customer_name",
    "shipping_address.name":      "customer_name",
    "shipping_address.phone":     "customer_phone",
    "shipping_address.email":     "customer_email",
    "shipping_address.address1":  "address",
    "shipping_address.address2":  "address",
    "shipping_address.line1":     "address",
    "shipping_address.line2":     "address",
    "shipping_address.area":      "address",
    "shipping_address.landmark":  "address",
    "shipping_address.locality":  "address",
    "shipping_address.province":  "state",
    "shipping_address.zip":       "pincode",
    "shipping_address.postcode":  "pincode",
    "shipping_address.postal_code":"pincode",
    "default_address.first_name": "customer_name",
    "default_address.last_name":  "customer_name",
    "default_address.address1":   "address",
    "default_address.city":       "city",
    "default_address.province":   "state",
    "default_address.state":      "state",
    "default_address.zip":        "pincode",
    "default_address.phone":      "customer_phone",
    # Bare leaf aliases (kick in after prefix stripping)
    "province":            "state",
    "region":              "state",
    "country":             "state",
    "locality":            "city",
    "area":                "address",
    "landmark":            "address",
    "address1":            "address",
    "address2":            "address",
    "addressline":         "address",
    "line1":               "address",
    "line2":               "address",
    "postcode":            "pincode",
    "postal_code":         "pincode",
    "area_code":           "pincode",
    # Money — Shopify uses "total_price"/"subtotal_price"
    "total_price":         "amount",
    "subtotal_price":      "amount",
    "grand_total":         "amount",
    "price":               "amount",
    # Order id variants
    "display_id":          "order_id",
    "order_number":        "order_id",
    "order_no":            "order_id",
    "name_":               "order_id",   # Shopify ships order # as `name`
    # Status variants
    "financial_status":    "status",
    "fulfillment_status":  "status",
    "fulfilment_status":   "status",
    # Items (Shopify line_items / WooCommerce line_items)
    "line_items":          "items",
    "lineitems":           "items",
    "skus":                "items",
}


# Phase F3.4 — Common nested-path prefixes that suggest_mapping()
# strips before alias lookup. Order matters: longest first so that
# `order.billing_address.first_name` matches the more specific rule
# before falling back to just stripping `order.`.
PREFIXES_TO_STRIP: List[str] = [
    "order.customer.",
    "order.buyer.",
    "order.billing_address.",
    "order.shipping_address.",
    "order.default_address.",
    "checkout.customer.",
    "checkout.billing_address.",
    "checkout.shipping_address.",
    "data.order.",
    "data.customer.",
    "data.billing_address.",
    "data.shipping_address.",
    "payload.order.",
    "payload.customer.",
    "event.order.",
    "event.customer.",
    "order.",
    "checkout.",
    "data.",
    "payload.",
    "event.",
    "customer.",
    "buyer.",
    "billing_address.",
    "shipping_address.",
    "default_address.",
    "billing.",
    "shipping.",
]


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
    if field == "items" and isinstance(raw, list):
        # Phase F3.3.1+ — pretty-format Shopify/Dukaan-style line_items.
        # Each entry is typically a dict with `title` / `name` /
        # `product_name` and a `quantity`. Render as
        # "Title xQty, Title xQty, ..." for clean display in the
        # Pending Order card / Smart Paste / Webhook downstream UI.
        # Falls back to str(raw) if no recognisable name key is found
        # in any element (preserves backward-compatibility for exotic
        # payload shapes).
        parts: List[str] = []
        for it in raw:
            if not isinstance(it, dict):
                # Mixed list (string + dict) — bail out to legacy behaviour.
                parts = []
                break
            name = (
                it.get("title")
                or it.get("name")
                or it.get("product_name")
                or ""
            )
            name = str(name).strip()
            if not name:
                # No name key in this element → abort, fall back below.
                parts = []
                break
            qty_raw = it.get("quantity")
            try:
                qty = int(qty_raw) if qty_raw not in (None, "") else 1
            except (TypeError, ValueError):
                qty = 1
            parts.append(f"{name} x{qty}")
        if parts:
            return ", ".join(parts)
        return str(raw)
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

    Phase F3.4 — for nested webhook payloads (Shopify / Dukaan / Meesho /
    WooCommerce) the keys arrive as dotted paths
    (`order.billing_address.first_name`). suggest_mapping now tries
    EVERY prefix-stripped candidate plus the bare leaf segment so the
    user gets a pre-populated field-mapping screen instead of a wall
    of empty "Select…" dropdowns.

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

    # Track which schema fields already have an unambiguous suggestion so
    # we don't pile every name-fragment column onto customer_name when a
    # cleaner full_name column is also present. Maps target → "clean" |
    # "fragment" so first_name/last_name pairs can still BOTH be
    # mapped (the build path joins them) when no full_name source exists.
    suggested_quality: Dict[str, str] = {}

    def _name_quality(leaf: str) -> str:
        """`clean` = single-source name (full_name, name);
        `fragment` = part of a multi-column name (first_name, last_name)."""
        if leaf in ("first_name", "last_name"):
            return "fragment"
        return "clean"

    def _candidates(cl: str) -> List[str]:
        """Generate normalised lookup candidates for a single column,
        widest → narrowest. We want suffix-stripped variants tried in
        order so that the most specific path (e.g.
        `billing_address.first_name`) wins before a bare `first_name`
        leaf-match."""
        cands: List[str] = [cl]
        # Successive prefix strips (longest-prefix-first).
        for p in PREFIXES_TO_STRIP:
            if cl.startswith(p) and len(cl) > len(p):
                cands.append(cl[len(p):])
        # Final fallback: just the LEAF segment (after last dot).
        if "." in cl:
            cands.append(cl.rsplit(".", 1)[-1])
        # De-dupe while preserving order.
        seen: Set[str] = set()
        uniq: List[str] = []
        for x in cands:
            if x and x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq

    # Two-pass pipeline:
    #   Pass 1 — anything that's an EXACT canonical schema field match
    #            after normalisation. Highest confidence.
    #   Pass 2 — alias/leaf-resolved matches with duplicate suppression
    #            for known multi-source fields (customer_name).
    pending: List[tuple[str, List[str]]] = []
    for c in columns:
        if c in saved and saved[c]:
            out[c] = saved[c]
            suggested_quality[saved[c]] = "clean"
            continue
        cl = c.lower().strip().replace(" ", "_").replace("-", "_")
        cands = _candidates(cl)

        # Pass 1: direct schema-field hit on any candidate.
        matched = None
        for cand in cands:
            if cand in SCHEMA_FIELDS:
                matched = cand
                break
        if matched:
            out[c] = matched
            suggested_quality[matched] = "clean"
            continue
        # Defer alias matching — we want exact schema hits to win.
        pending.append((c, cands))

    # Pass 2: alias / custom-field / leaf resolution.
    for c, cands in pending:
        matched = None
        matched_via = ""
        for cand in cands:
            if cand in HEADER_ALIASES:
                matched = HEADER_ALIASES[cand]
                matched_via = cand
                break
            if cand in custom_lookup:
                matched = custom_lookup[cand]
                matched_via = cand
                break
            if cand.replace("_", " ") in custom_lookup:
                matched = custom_lookup[cand.replace("_", " ")]
                matched_via = cand
                break
        if not matched:
            continue
        # Suppress duplicate name suggestions: skip first/last name
        # fragments only when a CLEAN customer_name source (full_name /
        # name) was already suggested. If only fragments exist, keep
        # both first AND last so build_pending_doc joins them.
        if matched == "customer_name":
            leaf = matched_via.rsplit(".", 1)[-1] if "." in matched_via else matched_via
            this_quality = _name_quality(leaf)
            prev_quality = suggested_quality.get(matched)
            if prev_quality == "clean" and this_quality == "fragment":
                continue
            # Update tracking so a later "clean" source overrides
            # earlier fragment quality.
            if not prev_quality or this_quality == "clean":
                suggested_quality[matched] = this_quality
        elif matched == "address":
            # Address is joined in build_pending_doc, so multiple
            # cols (address1 + area + landmark) are intentionally
            # allowed — no suppression.
            suggested_quality[matched] = "clean"
        else:
            if matched in suggested_quality:
                # Plain duplicate (e.g. two `phone` fields) — keep first.
                # Last-wins in build_pending_doc means the second one
                # would silently override; suppressing is safer.
                continue
            suggested_quality[matched] = "clean"
        out[c] = matched
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
        # Phase F3.4 — when the same payload offers multiple
        # customer_name sources (e.g. first_name + last_name),
        # join them so we get "First Last" instead of just whichever
        # column happened to be processed last. Drop empty parts and
        # de-dupe so a payload that has BOTH `full_name` and
        # `first_name`/`last_name` mapped to customer_name doesn't
        # render "First Last First Last".
        if field == "customer_name" and len(cols) > 1:
            seen: Set[str] = set()
            parts: List[str] = []
            for c in cols:
                v = str(row.get(c) or "").strip()
                if v and v.lower() not in seen:
                    seen.add(v.lower())
                    parts.append(v)
            joined = " ".join(parts)
            if joined:
                out[field] = normalise_value(field, joined)
                continue
        out[field] = normalise_value(field, row.get(cols[-1]))

    # Custom-field values flow into the dict — string-coerced.
    for cf_id, cols in custom_to_cols.items():
        v = row.get(cols[-1])
        out["custom_values"][cf_id] = "" if v is None else str(v).strip()

    return out
