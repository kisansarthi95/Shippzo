"""
Stage Rules — Unified SLA + Alert + Template + Escalation system.
================================================================

ONE admin-configurable doc that drives every stage's behaviour:

  • SLA days        — expected duration before the stage is flagged
  • Internal alert  — team/admin notification when SLA breached
  • Customer msg    — automated WhatsApp to customer (variant rotation)
  • Cooldown        — minimum gap between repeat alerts (anti-spam)
  • Escalation      — Day 1 → team, Day 2 → admin, Day 3 → high

Stored in `admin_config._id="default"` under key `stage_rules`. Per-user
overrides are NOT supported in v1 (admin-only config).

The 6 fixed stages (in flow order):
  Pending → Processing → Ready to Ship → Shipped → Delivered → Feedback

Modified is NOT a stage — it's a flag that any shipment edited
post-creation gets, alongside its current real stage.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# Canonical stage list — keep order (used for UI rendering AND escalation).
STAGES: List[str] = [
    "Pending",
    "Processing",
    "Ready to Ship",
    "Shipped",
    "Delivered",
    "Feedback",
]

# Map every stage → the bulk-message ttype it sends (None when no
# customer message). Mirrors the existing bulk-message router types.
STAGE_TO_TEMPLATE: Dict[str, Optional[str]] = {
    "Pending":       None,                       # no auto customer msg (spec)
    "Processing":    None,
    "Ready to Ship": None,
    "Shipped":       "dispatch_confirmation",    # optional delay-info / tracking
    "Delivered":     "delivery_confirmation",    # 3-variant "did you receive?"
    "Feedback":      "feedback_request",         # 3-variant review ask
}

# Sensible per-stage defaults (mirrors the user's spec verbatim).
DEFAULT_STAGE_RULES: Dict[str, Dict[str, Any]] = {
    "Pending": {
        "sla_days":              1,
        "alert_enabled":         True,
        "alert_priority":        "medium",
        "alert_channel":         "both",     # whatsapp / app / both
        "alert_recipients":      ["admin", "team"],
        "customer_msg_enabled":  False,
        "template_type":         None,
        "auto_trigger":          False,
        "cooldown_hours":        24,
        "escalation": [
            {"day_after_sla": 0, "recipients": ["team"],   "priority": "low"},
            {"day_after_sla": 1, "recipients": ["admin"],  "priority": "medium"},
            {"day_after_sla": 2, "recipients": ["admin"],  "priority": "high"},
        ],
    },
    "Processing": {
        "sla_days":              2,
        "alert_enabled":         True,
        "alert_priority":        "high",
        "alert_channel":         "both",
        "alert_recipients":      ["admin", "team"],
        "customer_msg_enabled":  False,
        "template_type":         None,
        "auto_trigger":          False,
        "cooldown_hours":        24,
        "escalation": [
            {"day_after_sla": 0, "recipients": ["team"],   "priority": "medium"},
            {"day_after_sla": 1, "recipients": ["admin"],  "priority": "high"},
            {"day_after_sla": 2, "recipients": ["admin"],  "priority": "high"},
        ],
    },
    "Ready to Ship": {
        "sla_days":              1,
        "alert_enabled":         True,
        "alert_priority":        "medium",
        "alert_channel":         "both",
        "alert_recipients":      ["admin", "team"],
        "customer_msg_enabled":  False,
        "template_type":         None,
        "auto_trigger":          False,
        "cooldown_hours":        24,
        "escalation": [
            {"day_after_sla": 0, "recipients": ["team"],  "priority": "low"},
            {"day_after_sla": 1, "recipients": ["admin"], "priority": "medium"},
            {"day_after_sla": 2, "recipients": ["admin"], "priority": "high"},
        ],
    },
    "Shipped": {
        "sla_days":              5,
        "alert_enabled":         True,
        "alert_priority":        "medium",
        "alert_channel":         "both",
        "alert_recipients":      ["admin"],
        "customer_msg_enabled":  True,
        "template_type":         "dispatch_confirmation",
        "auto_trigger":          False,    # opt-in (delay-info is sensitive)
        "cooldown_hours":        24,
        "escalation": [
            {"day_after_sla": 0, "recipients": ["admin"], "priority": "medium"},
            {"day_after_sla": 2, "recipients": ["admin"], "priority": "high"},
        ],
    },
    "Delivered": {
        "sla_days":              1,
        "alert_enabled":         True,
        "alert_priority":        "low",
        "alert_channel":         "app",
        "alert_recipients":      ["admin"],
        "customer_msg_enabled":  True,
        "template_type":         "delivery_confirmation",
        "auto_trigger":          True,
        "cooldown_hours":        24,
        "escalation": [],
    },
    "Feedback": {
        "sla_days":              2,
        "alert_enabled":         False,     # nudging customers, not us
        "alert_priority":        "low",
        "alert_channel":         "app",
        "alert_recipients":      [],
        "customer_msg_enabled":  True,
        "template_type":         "feedback_request",
        "auto_trigger":          True,
        "cooldown_hours":        72,        # don't ask for review repeatedly
        "escalation": [],
    },
}

DEFAULT_STAGE_RULES_DOC: Dict[str, Any] = {
    "stages":              DEFAULT_STAGE_RULES,
    "alert_team_numbers":  [],   # phone numbers (E.164 like "919XXX")
    "alert_admin_number":  "",   # single admin number
    "alert_app_user_ids":  [],   # uuid list for in-app push notifications
    "global_enabled":      True, # master kill-switch

    # ── How / where alerts are surfaced inside the app ───────────────
    # All three channels are independent toggles; turn off any you
    # don't want. "list" = the dedicated /admin/sla-alerts list,
    # "banner" = the dashboard "Action Required" widget,
    # "push" = expo/FCM push notification (when wired in Phase D).
    "display_channels": {
        "list":   True,
        "banner": True,
        "push":   False,
    },
    # How often the background scanner runs. Min 15min, max 240.
    "scan_interval_minutes": 60,
    # Default per-stage cooldown applied when a stage's own cooldown
    # is missing. Lets the admin tune anti-spam centrally.
    "default_cooldown_hours": 24,
}


# ── Pydantic payloads ────────────────────────────────────────────────

class StageEscalationStep(BaseModel):
    day_after_sla: int = 0
    recipients: List[str] = Field(default_factory=list)  # ["team","admin"]
    priority: str = "medium"   # low / medium / high


class StageRuleConfig(BaseModel):
    sla_days: int = 1
    alert_enabled: bool = True
    alert_priority: str = "medium"
    alert_channel: str = "both"            # whatsapp / app / both / none
    alert_recipients: List[str] = Field(default_factory=lambda: ["admin"])
    customer_msg_enabled: bool = False
    template_type: Optional[str] = None
    auto_trigger: bool = False
    cooldown_hours: int = 24
    escalation: List[StageEscalationStep] = Field(default_factory=list)


class StageRulesPayload(BaseModel):
    """Full PUT body. All fields optional — server merges with defaults."""
    stages: Optional[Dict[str, StageRuleConfig]] = None
    alert_team_numbers: Optional[List[str]] = None
    alert_admin_number: Optional[str] = None
    alert_app_user_ids: Optional[List[str]] = None
    global_enabled: Optional[bool] = None
    display_channels: Optional[Dict[str, bool]] = None
    scan_interval_minutes: Optional[int] = None
    default_cooldown_hours: Optional[int] = None


def merge_with_defaults(saved: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a fully-populated stage_rules doc — every stage is
    present, every rule field has a sensible value. Callers should
    always go through this helper rather than reading raw DB.
    """
    out: Dict[str, Any] = dict(DEFAULT_STAGE_RULES_DOC)
    out["stages"] = {}
    saved_stages = (saved or {}).get("stages") or {}
    for stage in STAGES:
        merged = dict(DEFAULT_STAGE_RULES[stage])
        s = saved_stages.get(stage) or {}
        for k, v in s.items():
            if v is None:
                continue
            merged[k] = v
        out["stages"][stage] = merged

    out["alert_team_numbers"] = list(
        (saved or {}).get("alert_team_numbers")
        or DEFAULT_STAGE_RULES_DOC["alert_team_numbers"],
    )
    out["alert_admin_number"] = str(
        (saved or {}).get("alert_admin_number")
        or DEFAULT_STAGE_RULES_DOC["alert_admin_number"],
    ).strip()
    out["alert_app_user_ids"] = list(
        (saved or {}).get("alert_app_user_ids")
        or DEFAULT_STAGE_RULES_DOC["alert_app_user_ids"],
    )
    out["global_enabled"] = bool(
        (saved or {}).get("global_enabled", True),
    )

    # Display channels — admin chooses which UI surfaces show alerts.
    saved_dc = (saved or {}).get("display_channels") or {}
    default_dc = DEFAULT_STAGE_RULES_DOC["display_channels"]
    out["display_channels"] = {
        "list":   bool(saved_dc.get("list",   default_dc["list"])),
        "banner": bool(saved_dc.get("banner", default_dc["banner"])),
        "push":   bool(saved_dc.get("push",   default_dc["push"])),
    }

    # Scan interval — clamp into [15, 240] minutes.
    try:
        scan_int = int(
            (saved or {}).get(
                "scan_interval_minutes",
                DEFAULT_STAGE_RULES_DOC["scan_interval_minutes"],
            )
        )
    except (TypeError, ValueError):
        scan_int = DEFAULT_STAGE_RULES_DOC["scan_interval_minutes"]
    out["scan_interval_minutes"] = max(15, min(240, scan_int))

    # Default cooldown — sane bounds [1, 168] hours.
    try:
        dch = int(
            (saved or {}).get(
                "default_cooldown_hours",
                DEFAULT_STAGE_RULES_DOC["default_cooldown_hours"],
            )
        )
    except (TypeError, ValueError):
        dch = DEFAULT_STAGE_RULES_DOC["default_cooldown_hours"]
    out["default_cooldown_hours"] = max(1, min(168, dch))

    return out


def normalise_phone(p: str) -> str:
    """E.164 lite — keeps only digits, prepends "91" if missing."""
    digits = "".join(c for c in str(p or "") if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:
        return f"91{digits}"
    return digits
