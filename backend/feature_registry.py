"""
Feature Registry — central catalogue of every toggleable feature in the app.

Why this exists
---------------
The admin should be able to tick/untick which features are visible to users
on each plan (Free Trial / Silver / Gold / Platinum). Hard-coding plan->feature
mappings means every new feature requires backend AND frontend edits scattered
across the codebase.

Instead, we keep ONE registry here. Adding a feature =
    1) Add a row below.
    2) Wrap the corresponding UI in `useFeatureFlag(key)` on the frontend.
That's it. The admin panel will pick up the new row automatically and ship
with `default=False` so it stays hidden until an admin opts it in for a plan.

Default values
--------------
For features that already shipped before the registry existed, we seed
sensible per-plan defaults below so live users don't lose access overnight.
NEW features added in future will default to OFF for every plan and only
become visible after the admin enables them.
"""
from typing import Dict, List

# Categories used by the admin UI to group checkboxes.
CATEGORY_ORDER = [
    "Smart Paste",
    "Shipments List",
    "Label Design",
    "Google Sheets",
    "Couriers & Tracking",
    "Customer Intelligence",
    "Print & PDF",
    "WhatsApp",
    "AI & Wallet",
    "Form Fields",
]

# The single source of truth. Order is preserved by Python 3.7+ dicts.
FEATURE_REGISTRY: Dict[str, Dict[str, str]] = {
    # ── Smart Paste ──────────────────────────────────────────────
    "smart_paste_ai":            {"label": "Smart Paste AI (text)",        "category": "Smart Paste"},
    "smart_paste_voice":         {"label": "Voice input (microphone)",     "category": "Smart Paste"},
    "smart_paste_image_ocr":     {"label": "Photo / Aadhaar OCR",          "category": "Smart Paste"},
    "smart_paste_chat_refine":   {"label": "Chat-style refine flow",       "category": "Smart Paste"},
    "smart_paste_custom_prompt": {"label": "Custom AI instructions",       "category": "Smart Paste"},

    # ── Shipments List per-row buttons ───────────────────────────
    "shipment_copy_btn":         {"label": "Copy button",                  "category": "Shipments List"},
    "shipment_whatsapp_btn":     {"label": "WhatsApp send button",         "category": "Shipments List"},
    "shipment_edit_btn":         {"label": "Edit button",                  "category": "Shipments List"},
    "shipment_delete_btn":       {"label": "Delete button",                "category": "Shipments List"},
    "shipment_mark_delivered":   {"label": "Mark Delivered button",        "category": "Shipments List"},
    "shipment_print_btn":        {"label": "Per-row Print button",         "category": "Shipments List"},

    # ── Label Design ─────────────────────────────────────────────
    "label_brand_logo":          {"label": "Brand logo on label",          "category": "Label Design"},
    "label_brand_name":          {"label": "Brand name on label",          "category": "Label Design"},
    "label_brand_tagline":       {"label": "Brand tagline on label",       "category": "Label Design"},
    "label_custom_fields":       {"label": "Custom label fields (up to 6)","category": "Label Design"},
    "label_field_toggles":       {"label": "Show/hide label fields",       "category": "Label Design"},
    "label_logo_shape":          {"label": "Logo shape (square / wide)",   "category": "Label Design"},
    "label_size_options":        {"label": "A4 / A5 / Thermal sizes",      "category": "Label Design"},

    # ── Google Sheets ────────────────────────────────────────────
    "sheet_import":              {"label": "Import orders from sheet",     "category": "Google Sheets"},
    "sheet_two_way_sync":        {"label": "Two-way sync (write back)",    "category": "Google Sheets"},
    "sheet_column_mapping":      {"label": "Custom column mapping",        "category": "Google Sheets"},

    # ── Couriers & Tracking ──────────────────────────────────────
    "multiple_couriers":         {"label": "More than 1 courier partner",  "category": "Couriers & Tracking"},
    "auto_tracking":             {"label": "Auto-generate tracking ID",    "category": "Couriers & Tracking"},
    "manual_tracking_scan":      {"label": "Scan barcode for tracking",    "category": "Couriers & Tracking"},

    # ── Customer Intelligence ────────────────────────────────────
    "repeat_customer_detect":    {"label": "Repeat customer detection",    "category": "Customer Intelligence"},
    "repeat_items_dialog":       {"label": "Reuse old items prompt",       "category": "Customer Intelligence"},
    "pending_orders_inbox":      {"label": "Pending Orders tab",           "category": "Customer Intelligence"},

    # ── Print & PDF ──────────────────────────────────────────────
    "bulk_print":                {"label": "Bulk print labels",            "category": "Print & PDF"},
    "pdf_download":              {"label": "Download label as PDF",        "category": "Print & PDF"},
    "print_preview":             {"label": "Preview before print",         "category": "Print & PDF"},

    # ── WhatsApp ─────────────────────────────────────────────────
    "whatsapp_template_editor":  {"label": "Custom message templates",     "category": "WhatsApp"},
    "whatsapp_eta_customization":{"label": "ETA days configuration",       "category": "WhatsApp"},
    "whatsapp_copy_template":    {"label": "Separate copy template",       "category": "WhatsApp"},

    # ── AI & Wallet ──────────────────────────────────────────────
    "ai_rate_customization":     {"label": "Custom AI rate card",          "category": "AI & Wallet"},
    "wallet_topup":              {"label": "Wallet top-up (Razorpay)",     "category": "AI & Wallet"},

    # ── Form Fields ──────────────────────────────────────────────
    "form_alt_phone":            {"label": "Alternative phone field",      "category": "Form Fields"},
    "form_box_dimensions":       {"label": "Box dimensions (L×W×H)",       "category": "Form Fields"},
    "form_token_amount":         {"label": "Token / Advance paid field",   "category": "Form Fields"},
    "form_shipment_notes":       {"label": "Special instructions field",   "category": "Form Fields"},
}

ALL_KEYS: List[str] = list(FEATURE_REGISTRY.keys())

# Per-plan defaults seeded the FIRST time an admin opens the panel.
# Existing customers don't lose anything: we ship them with reasonable
# coverage. Admin can later tighten or loosen per plan.
DEFAULT_PLAN_FEATURES: Dict[str, List[str]] = {
    "free_trial": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "label_brand_name", "label_field_toggles",
        "sheet_import",
        "multiple_couriers", "auto_tracking",
        "repeat_customer_detect",
        "pdf_download", "print_preview",
        "whatsapp_template_editor",
        "form_alt_phone",
    ],
    "silver": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "label_brand_logo", "label_brand_name", "label_brand_tagline",
        "label_field_toggles",
        "sheet_import", "sheet_two_way_sync",
        "multiple_couriers", "auto_tracking",
        "repeat_customer_detect", "pending_orders_inbox",
        "bulk_print", "pdf_download", "print_preview",
        "whatsapp_template_editor", "whatsapp_eta_customization",
        "form_alt_phone", "form_box_dimensions",
    ],
    "gold": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine", "smart_paste_custom_prompt",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "label_brand_logo", "label_brand_name", "label_brand_tagline",
        "label_custom_fields", "label_field_toggles", "label_logo_shape",
        "label_size_options",
        "sheet_import", "sheet_two_way_sync", "sheet_column_mapping",
        "multiple_couriers", "auto_tracking", "manual_tracking_scan",
        "repeat_customer_detect", "repeat_items_dialog", "pending_orders_inbox",
        "bulk_print", "pdf_download", "print_preview",
        "whatsapp_template_editor", "whatsapp_eta_customization", "whatsapp_copy_template",
        "ai_rate_customization",
        "form_alt_phone", "form_box_dimensions", "form_token_amount", "form_shipment_notes",
    ],
    # Platinum gets EVERYTHING — also captures new features auto-added later
    # by referencing ALL_KEYS at runtime in the seeder.
    "platinum": list(ALL_KEYS),
}


def get_registry_payload() -> Dict:
    """Shape returned to the admin UI. Includes ordered categories."""
    return {
        "categories": CATEGORY_ORDER,
        "features": [
            {"key": k, "label": v["label"], "category": v["category"]}
            for k, v in FEATURE_REGISTRY.items()
        ],
    }
