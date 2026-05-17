"""
Phase-24 (2026-05-17) — Centralized field-configuration service.

Lets a super-admin toggle the visibility (enabled/disabled) and the
requirement (mandatory/optional) of every NON-locked field across a
module — without per-screen hardcoding.

Design contract:
  • LOCKED fields are baked into source code and can NEVER be turned
    off or made optional. Their entries are rejected by the admin
    API and ignored if they show up in the database.
  • Every configurable field has a sensible DEFAULT (enabled=True,
    required=False unless explicitly marked otherwise). Defaults are
    materialised the first time the module is read so older shops
    upgrade seamlessly.
  • Storage is GLOBAL (one config per module — managed by the super
    admin). Per-user override can be layered on top later without
    schema changes.

Adding a new module = append to MODULE_REGISTRY below; nothing else.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


# ── Fields that MUST stay enabled + required forever ────────────
LOCKED_FIELDS: Dict[str, Set[str]] = {
    "new_shipment": {
        "customer_name",
        "customer_phone",
        "address",        # full address (line1 + city + state + pincode etc.)
        "city",
        "state",
        "pincode",
        "order_id",
        "amount",
    },
}


# ── Configurable fields per module with their defaults ──────────
#  enabled = is the field rendered in the form by default?
#  required = does the form block submission when missing by default?
#
# Keep this list small at first; new entries can be added without
# breaking older shops because anything not listed falls back to
# `enabled=True, required=False` via `_default_for_field()`.
MODULE_REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "new_shipment": [
        {"field_key": "tracking_id",          "label": "Courier Tracking ID",
         "default_enabled": True,  "default_required": False,
         "hint": "Turn off `required` if shipments start before the sticker arrives."},
        {"field_key": "courier_id",           "label": "Courier",
         "default_enabled": True,  "default_required": True},
        {"field_key": "customer_alt_phone",   "label": "Alternate Phone",
         "default_enabled": True,  "default_required": False},
        {"field_key": "items",                "label": "Order Items",
         "default_enabled": True,  "default_required": False},
        {"field_key": "item_description",     "label": "Item Description",
         "default_enabled": False, "default_required": False},
        {"field_key": "weight",               "label": "Weight (g)",
         "default_enabled": True,  "default_required": False},
        {"field_key": "payment_mode",         "label": "Payment Mode",
         "default_enabled": True,  "default_required": False},
        {"field_key": "eta_days",             "label": "ETA (days)",
         "default_enabled": False, "default_required": False},
        {"field_key": "sender_address_id",    "label": "Sender Address",
         "default_enabled": True,  "default_required": False},
        {"field_key": "notes",                "label": "Internal Notes",
         "default_enabled": False, "default_required": False},
    ],
}


def supported_modules() -> List[str]:
    return list(MODULE_REGISTRY.keys())


def is_locked(module: str, field_key: str) -> bool:
    return field_key in LOCKED_FIELDS.get(module, set())


def _default_for_field(module: str, field_key: str) -> Dict[str, Any]:
    """Return the registered defaults for `field_key`, or a safe
    `enabled=True, required=False` fallback for unknown keys."""
    for f in MODULE_REGISTRY.get(module, []):
        if f["field_key"] == field_key:
            return {
                "field_key":  field_key,
                "label":      f.get("label", field_key),
                "enabled":    f["default_enabled"],
                "required":   f["default_required"],
                "hint":       f.get("hint", ""),
                "locked":     False,
            }
    return {
        "field_key": field_key,
        "label":     field_key,
        "enabled":   True,
        "required":  False,
        "hint":      "",
        "locked":    False,
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_module_config(db, module: str) -> Dict[str, Any]:
    """
    Build the effective config for `module`:
      • locked fields are emitted FIRST, marked locked=True
      • configurable fields use stored values if present, else
        their registered defaults
    """
    if module not in MODULE_REGISTRY:
        raise ValueError(f"unknown module {module!r}")

    locked = [
        {
            "field_key": fk,
            "label":     fk.replace("_", " ").title(),
            "enabled":   True,
            "required":  True,
            "hint":      "Locked — always enabled and required.",
            "locked":    True,
        }
        for fk in sorted(LOCKED_FIELDS.get(module, set()))
    ]

    stored: Dict[str, Dict[str, Any]] = {}
    async for row in db.field_configs.find({"module": module}, {"_id": 0}):
        # Defensive: ignore any rows that accidentally store locked keys.
        if is_locked(module, row.get("field_key", "")):
            continue
        stored[row["field_key"]] = row

    configurable: List[Dict[str, Any]] = []
    for f in MODULE_REGISTRY[module]:
        key = f["field_key"]
        s = stored.get(key)
        if s:
            configurable.append({
                **_default_for_field(module, key),
                "enabled":  bool(s.get("enabled", True)),
                "required": bool(s.get("required", False)),
            })
        else:
            configurable.append(_default_for_field(module, key))

    return {
        "module":       module,
        "locked":       locked,
        "configurable": configurable,
        "updated_at":   _utcnow_iso(),
    }


async def update_field(
    db,
    module: str,
    field_key: str,
    *,
    enabled: Optional[bool],
    required: Optional[bool],
    actor_id: str,
) -> Tuple[bool, str]:
    """
    Upsert a single field's config. Returns (ok, message).
    Refuses to mutate locked fields with a 'locked' error string so
    the router can map it to HTTP 400.
    """
    if module not in MODULE_REGISTRY:
        return False, "unknown_module"
    if is_locked(module, field_key):
        return False, "locked_field"

    set_doc: Dict[str, Any] = {
        "updated_at": _utcnow_iso(),
        "updated_by": actor_id,
    }
    if enabled is not None:
        set_doc["enabled"] = bool(enabled)
    if required is not None:
        set_doc["required"] = bool(required)
    if len(set_doc) == 2:  # nothing meaningful changed
        return False, "nothing_to_update"

    await db.field_configs.update_one(
        {"module": module, "field_key": field_key},
        {
            "$set":         set_doc,
            "$setOnInsert": {"module": module, "field_key": field_key},
        },
        upsert=True,
    )
    log.info(
        "[field_configs] updated module=%s key=%s enabled=%s required=%s actor=%s",
        module, field_key, set_doc.get("enabled"), set_doc.get("required"), actor_id,
    )
    return True, "ok"
