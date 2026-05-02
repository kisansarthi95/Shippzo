"""
Contact Save Settings — Phase-16.

Per-user preferences that control how "Save Contact" builds the name,
maps fields, and auto-assigns categories. Everything is user-scoped
and fully editable — no hardcoded categories or product keywords.
Defaults follow the spec in the product requirement doc.
"""
from typing import Any, Dict, List
from pydantic import BaseModel, Field


# ── Schema ────────────────────────────────────────────────────────────

class NameFormat(BaseModel):
    prefix_enabled:    bool = True
    prefix_position:   str  = "start"          # "start" | "end"
    name_type:         str  = "full"           # "full"  | "first"
    product_placement: str  = "after_name"     # "after_name" | "end" | "notes_only"
    location:          str  = "city"           # "city" | "taluka" | "village" | "none"


class NotesInclude(BaseModel):
    order_id:     bool = False
    quantity:     bool = False
    payment_mode: bool = False


class FieldMapping(BaseModel):
    address_target: str          = "address"   # "address" | "notes"
    product_target: str          = "notes"     # "name" | "notes" | "both"
    notes_include:  NotesInclude = Field(default_factory=NotesInclude)


class ProductRule(BaseModel):
    """One row in the Product → Category mapping table."""
    keyword:  str             # matches case-insensitive substring in items
    category: str


class CategorySettings(BaseModel):
    categories:        List[str]          = Field(default_factory=list)
    default_category:  str                = ""
    auto_assign:       bool               = True
    manual_popup:      bool               = False
    product_mapping:   List[ProductRule]  = Field(default_factory=list)


class ContactSaveSettings(BaseModel):
    name_format:    NameFormat       = Field(default_factory=NameFormat)
    field_mapping:  FieldMapping     = Field(default_factory=FieldMapping)
    category:       CategorySettings = Field(default_factory=CategorySettings)


def default_settings() -> Dict[str, Any]:
    """Factory for a brand-new user's settings. No categories seeded —
    the user adds their own from the Settings UI."""
    return ContactSaveSettings().model_dump()


# ── Builder: turn a shipment + settings into a contact payload ────────

def _first_word(s: str) -> str:
    s = (s or "").strip()
    return s.split()[0] if s else ""


def _match_category(items: str, s: ContactSaveSettings) -> str:
    """Walk the product_mapping table in order; first keyword hit wins.
    Falls back to default_category when nothing matches."""
    text = (items or "").lower()
    for rule in s.category.product_mapping:
        kw = (rule.keyword or "").strip().lower()
        if kw and kw in text and rule.category:
            return rule.category
    return s.category.default_category or ""


def build_contact(
    shipment: Dict[str, Any],
    settings: Dict[str, Any],
    override_category: str = "",
) -> Dict[str, Any]:
    """Turn one shipment document into the contact fields the UI
    needs (name, phone, postal, notes). Pure function — does NOT
    touch the DB or any intent; front-end drives the native "Save"
    dialog from this result.

    Parameters
    ----------
    shipment : shipment / pending-order doc.
    settings : dict matching ContactSaveSettings shape.
    override_category : when set, skips auto-assign (used for bulk
        "apply category to all" popup flow).

    Returns
    -------
    {
      "name":     "[KSS] Ramesh | Garlic | Surat",
      "phone":    "9876543210",
      "postal":   "Flat 203, ... 395003",
      "notes":    "Ordered: Garlic | Qty: 2kg | Order: ABC-001",
      "category": "KSS",
    }
    """
    s = ContactSaveSettings(**(settings or {}))

    raw_name   = (shipment.get("customer_name")  or "").strip()
    raw_phone  = (shipment.get("customer_phone") or "").strip()
    # Shipments persisted via the model store `items` as List[str];
    # inline-preview callers pass a plain string. Coerce defensively.
    _items     = shipment.get("items") or ""
    if isinstance(_items, list):
        _items = ", ".join(str(x) for x in _items if x)
    raw_items  = str(_items).strip()
    raw_city   = (shipment.get("city")           or "").strip()
    raw_taluka = (shipment.get("taluka")         or "").strip()
    raw_vill   = (shipment.get("village")        or shipment.get("locality") or "").strip()
    addr1      = (shipment.get("address_line1")  or "").strip()
    addr2      = (shipment.get("address_line2")  or "").strip()
    state      = (shipment.get("state")          or "").strip()
    pincode    = (shipment.get("pincode")        or "").strip()
    order_id   = (shipment.get("order_id")       or shipment.get("tracking_id") or "").strip()
    qty        = (shipment.get("quantity")       or "").strip()
    pay_mode   = (shipment.get("payment_mode")   or "").strip()

    # ── Category ──
    category = (override_category or "").strip()
    if not category and s.category.auto_assign:
        category = _match_category(raw_items, s)

    # ── Name pieces ──
    nm  = raw_name if s.name_format.name_type == "full" else _first_word(raw_name)

    # Location tail
    loc = ""
    if   s.name_format.location == "city":    loc = raw_city
    elif s.name_format.location == "taluka":  loc = raw_taluka
    elif s.name_format.location == "village": loc = raw_vill

    # Product segment only surfaces on the name line when the user asked
    # for it there — "notes_only" keeps the name clean.
    prod_in_name = (
        raw_items
        if s.name_format.product_placement in ("after_name", "end")
        else ""
    )

    # Assemble NAME following placement rules.
    # Structure is always: [prefix?] CORE [| loc?]
    # where CORE is "name | product" (after_name) or "name … | product" (end).
    parts: List[str] = []
    if s.name_format.product_placement == "after_name":
        parts.append(nm)
        if prod_in_name: parts.append(prod_in_name)
        if loc:          parts.append(loc)
    elif s.name_format.product_placement == "end":
        parts.append(nm)
        if loc:          parts.append(loc)
        if prod_in_name: parts.append(prod_in_name)
    else:  # notes_only
        parts.append(nm)
        if loc: parts.append(loc)

    core = " | ".join(p for p in parts if p)

    # Prefix wrapping
    name_final = core
    if s.name_format.prefix_enabled and category:
        tag = f"[{category}]"
        if s.name_format.prefix_position == "end":
            name_final = f"{core} {tag}".strip()
        else:
            name_final = f"{tag} {core}".strip()

    # ── Postal address ──
    postal_bits = [b for b in (addr1, addr2, raw_city, state, pincode) if b]
    full_address = ", ".join(postal_bits)
    postal = full_address if s.field_mapping.address_target == "address" else ""

    # ── Notes ──
    notes_bits: List[str] = []
    if raw_items and s.field_mapping.product_target in ("notes", "both"):
        notes_bits.append(f"Ordered: {raw_items}")
    # When address is routed to Notes instead of the Postal slot, glue
    # it into the notes blob so nothing is lost.
    if full_address and s.field_mapping.address_target == "notes":
        notes_bits.append(f"Address: {full_address}")
    if s.field_mapping.notes_include.order_id     and order_id:
        notes_bits.append(f"Order: {order_id}")
    if s.field_mapping.notes_include.quantity     and qty:
        notes_bits.append(f"Qty: {qty}")
    if s.field_mapping.notes_include.payment_mode and pay_mode:
        notes_bits.append(f"Payment: {pay_mode}")
    notes = " | ".join(notes_bits)

    return {
        "name":     name_final,
        "phone":    raw_phone,
        "postal":   postal,
        "notes":    notes,
        "category": category,
    }


def to_vcard(c: Dict[str, Any]) -> str:
    """Serialise a contact dict (from build_contact) as a single
    VCARD 3.0 record. Joined together with "\n\n" they form the .vcf
    the bulk endpoint returns."""
    def esc(v: str) -> str:
        return (v or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{esc(c.get('name',''))}", f"N:{esc(c.get('name',''))};;;;"]
    if c.get("phone"):  lines.append(f"TEL;TYPE=CELL:{esc(c['phone'])}")
    if c.get("postal"): lines.append(f"ADR;TYPE=HOME:;;{esc(c['postal'])};;;;")
    if c.get("notes"):  lines.append(f"NOTE:{esc(c['notes'])}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)
