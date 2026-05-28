"""
Feature Registry — central catalogue of every toggleable feature in the app.

═══════════════════════════════════════════════════════════════════════════
🚨 STANDING RULE — READ BEFORE ADDING ANY USER-FACING FEATURE 🚨
═══════════════════════════════════════════════════════════════════════════

Whenever a NEW user-facing feature is built (anywhere in the app — backend
endpoint, frontend screen, button, toggle, modal, etc.), it MUST be:

  1. Registered in this file's `FEATURE_REGISTRY` dict below — pick or
     create an appropriate `category`, and write a short user-friendly
     `label` (this is what the admin sees in the panel).

  2. Seeded with a sensible default in `DEFAULT_PLAN_FEATURES` below for
     every plan tier (free_trial / silver / gold / platinum). Platinum
     gets EVERYTHING automatically because its list is `list(ALL_KEYS)`.
     For others, decide which plan should see this feature by default.
     Conservative principle: lock new features behind paid tiers unless
     they're clearly basic.

  3. Wired in the relevant UI code with `useFeatureFlag("key_here")` —
     the component must render only when the flag is on. Backend
     endpoints can also gate behaviour by reading the user's plan.

The admin's "Plan Features" panel is data-driven from this registry, so
adding a row here automatically gives the admin a checkbox for that
feature in every plan tab — no UI code changes are required there.

▶ DO NOT remove or rename existing keys. If a feature is being retired,
  leave its row in place and remove it from all plan defaults instead;
  that way already-saved admin choices in production keep working and
  no database migration is required.

▶ When you migrate or rename a feature, add an `aliases` block in
  `_get_plan_features_doc()` (server.py) so old keys still resolve.
═══════════════════════════════════════════════════════════════════════════

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
with the per-plan defaults declared in `DEFAULT_PLAN_FEATURES`.

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
    "File Import",
    "Webhooks",
    "Shipments List",
    "Label Design",
    "Google Sheets",
    "Master Order ID",
    "Couriers & Tracking",
    "Scanner",
    "Customer Intelligence",
    "Offline Mode",
    "Print & PDF",
    "WhatsApp",
    "AI & Wallet",
    "Form Fields",
    "Packing Variants",
    "Analytics & SLA",
    "Notifications",
    "Reports",
    "Team & RBAC",
    "Coupons & Credits",
    "Business Profile",
    "Help & Support",
]

# The single source of truth. Order is preserved by Python 3.7+ dicts.
FEATURE_REGISTRY: Dict[str, Dict[str, str]] = {
    # ── Smart Paste ──────────────────────────────────────────────
    "smart_paste_ai":            {"label": "Smart Paste AI (text)",        "category": "Smart Paste"},
    "smart_paste_voice":         {"label": "Voice input (microphone)",     "category": "Smart Paste"},
    "smart_paste_image_ocr":     {"label": "Photo / Aadhaar OCR",          "category": "Smart Paste"},
    "smart_paste_chat_refine":   {"label": "Chat-style refine flow",       "category": "Smart Paste"},
    "smart_paste_custom_prompt": {"label": "Custom AI instructions",       "category": "Smart Paste"},
    # NEW (2026-04-30) — Pre-save duplicate detection
    "smart_paste_duplicate_check": {"label": "Pre-save duplicate detection", "category": "Smart Paste"},
    # NEW (2026-05-09 — Phase G3) — Multi-address extraction (wires
    # when Smart Paste agent grows that capability).
    "smart_paste_multi_address": {"label": "Multi-address extraction (multiple orders per paste)", "category": "Smart Paste"},
    # NEW (2026-05-25) — Smart Paste Indic regional language support:
    # parses Bengali, Marathi, Punjabi, Tamil, Telugu, Kannada, Malayalam
    # alongside the existing Hindi & Gujarati digit / keyword tables.
    "smart_paste_indic_extended": {"label": "Smart Paste — 9 Indic regional languages", "category": "Smart Paste"},

    # ── File Import (NEW category, 2026-05-09 — Phase G3) ────────
    # CSV / Excel imports with dynamic column mapping. Already shipped
    # to all users; registry rows let the admin gate by plan if needed
    # (e.g. "Excel imports only on paid tiers"). Status / Timestamp /
    # Custom-field column mappings are individually toggleable so the
    # free tier can stay simple while paid tiers unlock advanced
    # mapping options.
    "file_import_csv":                   {"label": "Import orders from CSV",                "category": "File Import"},
    "file_import_excel":                 {"label": "Import orders from Excel (.xlsx)",      "category": "File Import"},
    "file_import_status_mapping":        {"label": "Map \"Status\" column from imports",     "category": "File Import"},
    "file_import_timestamp_mapping":     {"label": "Map \"Order Date / Timestamp\" column",  "category": "File Import"},
    "file_import_custom_fields_mapping": {"label": "Map dynamic custom-field columns",       "category": "File Import"},

    # ── Webhooks (NEW category, 2026-05-09 — Phase F2/G3) ────────
    # Webhook ingestion lets stores like Dukaan / Shopify push orders
    # into Shippzo automatically. Each row is independently toggleable
    # so the admin can offer "basic webhook receiver" on a low tier and
    # "auto-mapping + multiple endpoints" only on premium tiers.
    "webhook_ingestion":          {"label": "Receive orders via webhook URL (master switch)", "category": "Webhooks"},
    "webhook_multiple_endpoints": {"label": "Multiple named webhooks (Shopify / Dukaan / custom)", "category": "Webhooks"},
    "webhook_dukaan_auto_map":    {"label": "Auto-detect Dukaan payload (35+ keys)",         "category": "Webhooks"},
    "webhook_shopify_auto_map":   {"label": "Auto-detect Shopify payload",                   "category": "Webhooks"},
    "webhook_payload_preview":    {"label": "\"Use last received payload\" extract button",   "category": "Webhooks"},
    "webhook_custom_key_mapping": {"label": "Manual JSON key → field mapping UI",             "category": "Webhooks"},

    # ── Shipments List per-row buttons ───────────────────────────
    "shipment_copy_btn":         {"label": "Copy button",                  "category": "Shipments List"},
    "shipment_whatsapp_btn":     {"label": "WhatsApp send button",         "category": "Shipments List"},
    "shipment_edit_btn":         {"label": "Edit button",                  "category": "Shipments List"},
    "shipment_delete_btn":       {"label": "Delete button",                "category": "Shipments List"},
    "shipment_mark_delivered":   {"label": "Mark Delivered button",        "category": "Shipments List"},
    "shipment_print_btn":        {"label": "Per-row Print button",         "category": "Shipments List"},
    # NEW (2026-04-30 PM2) — Bulk select + CSV export are tier-gated
    "shipments_bulk_select":     {"label": "Bulk select / multi-pick mode", "category": "Shipments List"},
    "csv_export_orders":         {"label": "Export orders/shipments to CSV", "category": "Shipments List"},
    # NEW (2026-05-25) — Cancel / Cancel-by-buyer / Returned terminal
    # status workflow. When enabled, the row menu shows Cancel / Return
    # actions in place of the old Delete. Backend HTTP-423 locks
    # terminal shipments regardless of this flag (data integrity).
    "shipment_terminal_states":  {"label": "Cancel / Return terminal status flow", "category": "Shipments List"},

    # ── Label Design ─────────────────────────────────────────────
    "label_brand_logo":          {"label": "Brand logo on label",          "category": "Label Design"},
    "label_brand_name":          {"label": "Brand name on label",          "category": "Label Design"},
    "label_brand_tagline":       {"label": "Brand tagline on label",       "category": "Label Design"},
    "label_custom_fields":       {"label": "Custom label fields (up to 6)","category": "Label Design"},
    "label_field_toggles":       {"label": "Show/hide label fields",       "category": "Label Design"},
    "label_logo_shape":          {"label": "Logo shape (square / wide)",   "category": "Label Design"},
    "label_size_options":        {"label": "A4 / A5 / Thermal sizes",      "category": "Label Design"},
    # NEW (2026-04-30) — Customer ID & Content Budget on label
    "label_customer_id":         {"label": "Customer ID on label (per courier)", "category": "Label Design"},
    "label_content_budget":      {"label": "Content budget indicator (3 max)",   "category": "Label Design"},
    # NEW (2026-05-17 — Phase-26) — Barcode sticker on the regular
    # label footer. When OFF, the barcode rectangle stays empty (layout
    # preserved). The dedicated "Barcode Sticker 50×25mm" page layout
    # is unaffected — that layout is a barcode by definition.
    "label_barcode_sticker":     {"label": "Barcode on label footer",            "category": "Label Design"},

    # ── Google Sheets ────────────────────────────────────────────
    "sheet_import":              {"label": "Import orders from sheet",     "category": "Google Sheets"},
    "sheet_two_way_sync":        {"label": "Two-way sync (write back)",    "category": "Google Sheets"},
    "sheet_column_mapping":      {"label": "Custom column mapping",        "category": "Google Sheets"},
    # NEW (2026-04-30) — Phase-C Restore + Two-way status + soft-delete
    "sheet_restore_my_orders":   {"label": "Restore My Orders (Phase-C)",  "category": "Google Sheets"},
    "sheet_two_way_status_sync": {"label": "App status → Sheet auto sync", "category": "Google Sheets"},
    "sheet_soft_delete_tombstone": {"label": "Soft-delete preserves audit row", "category": "Google Sheets"},

    # ── Master Order ID (NEW category, 2026-04-30) ───────────────
    "master_order_id_counter_custom": {"label": "Counter customization (e.g. start at 2200)", "category": "Master Order ID"},
    "master_order_id_autofill_new":   {"label": "Auto-fill in New Shipment form",             "category": "Master Order ID"},

    # ── Couriers & Tracking ──────────────────────────────────────
    "multiple_couriers":         {"label": "More than 1 courier partner",  "category": "Couriers & Tracking"},
    "auto_tracking":             {"label": "Auto-generate tracking ID",    "category": "Couriers & Tracking"},
    "manual_tracking_scan":      {"label": "Scan barcode for tracking",    "category": "Couriers & Tracking"},
    # NEW (2026-05-09 — Phase G3) — Auto-route orders to a courier
    # based on rules (state / weight / COD vs prepaid). Big time-saver
    # for heavy-volume shops; default off for free tier.
    "courier_rules_engine":      {"label": "Auto-assign courier by rules (state / weight / COD)", "category": "Couriers & Tracking"},
    # NEW (2026-05-09 — Phase G3) — Per-courier required-field config.
    "field_requirements_per_courier": {"label": "Per-courier required-field configuration", "category": "Couriers & Tracking"},
    # NEW (2026-05-25) — Phase-35 — WhatsApp pre-filled "Courier Inquiry"
    # button on the courier add screen. Lets the shop owner ping a
    # potential courier partner with rate-card-ready intro text.
    "courier_whatsapp_inquiry":  {"label": "Courier Inquiry WhatsApp button", "category": "Couriers & Tracking"},

    # ── Scanner (NEW category, 2026-04-30) ───────────────────────
    "scanner_sound_feedback":    {"label": "Beep / buzz feedback on scan", "category": "Scanner"},
    "scanner_double_confirm":    {"label": "Double-confirm scan accuracy", "category": "Scanner"},
    "scanner_manual_entry":      {"label": "Manual tracking ID entry",     "category": "Scanner"},

    # ── Customer Intelligence ────────────────────────────────────
    "repeat_customer_detect":    {"label": "Repeat customer detection",    "category": "Customer Intelligence"},
    "repeat_items_dialog":       {"label": "Reuse old items prompt",       "category": "Customer Intelligence"},
    "pending_orders_inbox":      {"label": "Pending Orders tab",           "category": "Customer Intelligence"},
    # NEW (2026-04-30 PM2) — Yellow "Repeat customer" banner with Use button
    "repeat_customer_banner":    {"label": "Repeat customer banner (with Use button)", "category": "Customer Intelligence"},
    # NEW (2026-05-09 — Phase G3) — Pending Orders inbox enhancements.
    "pending_orders_unified_list":  {"label": "Unified vertical list (vs old horizontal queues)", "category": "Customer Intelligence"},
    "pending_orders_source_badges": {"label": "Coloured source badges (PASTE / FILE / SHEET / WEBHOOK)", "category": "Customer Intelligence"},
    "pending_orders_edit":          {"label": "Edit pending order before saving",                       "category": "Customer Intelligence"},
    "pending_orders_delete":        {"label": "Delete pending order",                                   "category": "Customer Intelligence"},
    # NEW (Phase-21) — NEW + REPEAT visual markers on every pending
    # order card. Plan-gated so the basic tier can be kept minimal.
    "pending_orders_new_marker":    {"label": "NEW badge on unviewed pending orders",                   "category": "Customer Intelligence"},
    "pending_orders_repeat_marker": {"label": "REPEAT badge on returning-customer pending orders",      "category": "Customer Intelligence"},
    "new_order_sound":              {"label": "Happy chime on new order arrival",                       "category": "Customer Intelligence"},
    # NEW (2026-05-09 — Phase G3) — Save customer to phone contacts.
    "contact_save_to_phone":     {"label": "Save customer to phone contacts",                          "category": "Customer Intelligence"},

    # ── Offline Mode (NEW category, 2026-04-30) ──────────────────
    "offline_mode":              {"label": "Offline Mode (master switch)", "category": "Offline Mode"},
    "offline_create_shipment":   {"label": "Save shipment offline",        "category": "Offline Mode"},
    "offline_sync_queue_view":   {"label": "View offline sync queue",      "category": "Offline Mode"},

    # ── Print & PDF ──────────────────────────────────────────────
    "bulk_print":                {"label": "Bulk print labels",            "category": "Print & PDF"},
    "pdf_download":              {"label": "Download label as PDF",        "category": "Print & PDF"},
    "print_preview":             {"label": "Preview before print",         "category": "Print & PDF"},

    # ── WhatsApp ─────────────────────────────────────────────────
    "whatsapp_template_editor":  {"label": "Custom message templates",     "category": "WhatsApp"},
    "whatsapp_eta_customization":{"label": "ETA days configuration",       "category": "WhatsApp"},
    "whatsapp_copy_template":    {"label": "Separate copy template",       "category": "WhatsApp"},
    # NEW (2026-04-30 PM2) — Per-courier WhatsApp message templates (Gold+ tier feature)
    "whatsapp_per_courier_template": {"label": "Per-courier WhatsApp templates", "category": "WhatsApp"},
    # NEW (2026-05-04) — Phase-2 — AI-generated 3 variants per template type
    "whatsapp_ai_variants":      {"label": "AI Template Variants (3 per type)", "category": "WhatsApp"},
    # NEW (2026-05-04) — Round-robin variant rotation per recipient (so the
    # same template doesn't go to every customer in a bulk send).
    "whatsapp_variant_rotation": {"label": "Auto-rotate variants per recipient",  "category": "WhatsApp"},

    # ── AI & Wallet ──────────────────────────────────────────────
    "ai_rate_customization":     {"label": "Custom AI rate card",          "category": "AI & Wallet"},
    "wallet_topup":              {"label": "Wallet top-up (Razorpay)",     "category": "AI & Wallet"},

    # ── Form Fields ──────────────────────────────────────────────
    "form_alt_phone":            {"label": "Alternative phone field",      "category": "Form Fields"},
    "form_box_dimensions":       {"label": "Box dimensions (L×W×H)",       "category": "Form Fields"},
    "form_token_amount":         {"label": "Token / Advance paid field",   "category": "Form Fields"},
    "form_shipment_notes":       {"label": "Special instructions field",   "category": "Form Fields"},
    # NEW (2026-04-30 PM3) — Plan-gated per-user custom fields (Gold=3, Platinum=5)
    "custom_fields":             {"label": "Custom fields (per-user defined columns)", "category": "Form Fields"},
    # NEW (2026-05-17 — Phase-27) — Centralised Field Controls: each
    # user can toggle their own per-field enabled/required for the
    # New Shipment form (locked fields stay locked). Off by default
    # for the basic tiers so this stays a power-user feature.
    "field_controls":            {"label": "Field Controls (toggle optional fields)", "category": "Form Fields"},

    # ── Packing Variants (NEW category, 2026-05-04) ─────────────
    # Per-courier packing variants (e.g. "ODC 320gm" → weight + dims +
    # within/outside-state rates). Plan-wise CAP is separate (see
    # admin Plan Limits → packing_variant_cap). These are FEATURE
    # FLAGS — they decide whether the section is visible at all.
    "packing_variants_manage":     {"label": "Manage packing variants per courier",        "category": "Packing Variants"},
    "packing_variants_picker":     {"label": "Pick variant in New Shipment (auto-fill)",   "category": "Packing Variants"},
    "packing_variants_flexible":   {"label": "Flexible mode (chip mix-match)",             "category": "Packing Variants"},
    "packing_variants_copy":       {"label": "Copy variants from another courier",         "category": "Packing Variants"},
    "packing_variants_custom_categories": {"label": "Add custom categories (+ Add Category)", "category": "Packing Variants"},

    # ── Analytics & SLA (NEW category, 2026-05-04) ──────────────
    "analytics_dashboard":         {"label": "Personal Analytics Dashboard",                "category": "Analytics & SLA"},
    "analytics_filters":           {"label": "Advanced filters (courier / status / state)", "category": "Analytics & SLA"},
    "analytics_revenue_breakdown": {"label": "Revenue breakdown (COD vs Prepaid)",          "category": "Analytics & SLA"},
    "sla_engine":                  {"label": "SLA Engine (auto-detect breaches)",           "category": "Analytics & SLA"},
    "sla_alerts_dashboard":        {"label": "SLA Alerts dashboard widget",                 "category": "Analytics & SLA"},
    "stage_rules_editor":          {"label": "Stage Rules editor (custom SLA timings)",     "category": "Analytics & SLA"},

    # ── Notifications (NEW category, 2026-05-04) ────────────────
    "push_notifications":          {"label": "Push notifications (Expo)",                   "category": "Notifications"},
    "bulk_messaging_stages":       {"label": "Bulk messaging across all stages",            "category": "Notifications"},
    "bulk_message_select_filter":  {"label": "Filter / search shipments before sending",    "category": "Notifications"},
    # NEW (2026-05-09 — Phase G3) — Per-event granular notification toggles.
    "notification_prefs_granular": {"label": "Per-event notification preferences",          "category": "Notifications"},

    # ── Reports (NEW category, 2026-05-04 — Phase 2.5) ───────────
    # Per-courier billing / volume / charge reports. Tied to the
    # /me/reports/* endpoints. Default off everywhere — admin enables
    # per plan via the Plan Features admin screen.
    "reports_courier_billing":     {"label": "Courier Billing report (in-app + Excel)",     "category": "Reports"},
    # NEW (2026-05-09 — Phase G3) — Additional report types.
    "reports_partner_comparison":  {"label": "Courier Partner Comparison report",            "category": "Reports"},
    "reports_reconciliation":      {"label": "Reconciliation report (paid vs collected)",    "category": "Reports"},
    "reports_return_analysis":     {"label": "Return / RTO analysis report",                 "category": "Reports"},
    "reports_weight_wise":         {"label": "Weight-wise volume report",                    "category": "Reports"},

    # ── Team & RBAC (NEW category, 2026-05-09 — Phase G3) ───────
    # Owner can invite team members and assign granular per-permission
    # roles. Inviting is the gate — RBAC is the upgrade.
    "team_members_invite":         {"label": "Invite team members (sub-users)",              "category": "Team & RBAC"},
    "team_members_rbac":           {"label": "Granular RBAC permissions per member",         "category": "Team & RBAC"},

    # ── Coupons & Credits (NEW category, 2026-05-09 — Phase G3) ─
    "coupons_apply":               {"label": "Apply coupon codes (user-side)",               "category": "Coupons & Credits"},
    "credit_packages_buy":         {"label": "Buy SMS / WhatsApp credit packages",           "category": "Coupons & Credits"},

    # ── Business Profile (NEW category, 2026-05-09 — Phase G3) ──
    # Phase-G captured the owner's `primary_business_category` at
    # signup. This flag decides whether that data point gets wired
    # into the Analytics dashboard as a filter. Off by default —
    # admin turns it on for tiers that have advanced analytics.
    "business_category_filter_analytics": {"label": "Use business category as Analytics filter", "category": "Business Profile"},

    # ── Help & Support (NEW category, 2026-05-25) ────────────────
    # Two surfaces shipped to all users by default; admin can hide
    # them per plan if desired (e.g. premium-only tutorial library
    # or restrict ticket creation on trial accounts).
    "support_tickets":           {"label": "Support Center & ticket creation",   "category": "Help & Support"},
    "video_tutorials":           {"label": "Video Tutorials library",            "category": "Help & Support"},
}

ALL_KEYS: List[str] = list(FEATURE_REGISTRY.keys())

# Per-plan defaults seeded the FIRST time an admin opens the panel.
# Existing customers don't lose anything: we ship them with reasonable
# coverage. Admin can later tighten or loosen per plan.
DEFAULT_PLAN_FEATURES: Dict[str, List[str]] = {
    "free_trial": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine",
        "smart_paste_duplicate_check",  # NEW — basic safety net for everyone
        # NEW (Phase G3) — File Import: CSV is universal, basic mappings
        # included so trial users can validate the import flow.
        "file_import_csv",
        "file_import_status_mapping",
        "file_import_timestamp_mapping",
        # NEW (Phase G3) — Pending Orders inbox UX improvements are part
        # of the basic experience for everyone.
        "pending_orders_unified_list",
        "pending_orders_source_badges",
        "pending_orders_edit",
        "pending_orders_delete",
        # NEW (Phase-21) — Free tier gets visual markers + new-order
        # chime by default; admin can untick per plan if desired.
        "pending_orders_new_marker",
        "pending_orders_repeat_marker",
        "new_order_sound",
        # NEW (Phase G3) — Coupon redemption is open to all so we can run
        # promo campaigns without flipping plans.
        "coupons_apply",
        "credit_packages_buy",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "label_brand_name", "label_field_toggles",
        "sheet_import",
        "master_order_id_autofill_new",  # NEW — basic UX, no cost
        "multiple_couriers", "auto_tracking",
        "scanner_manual_entry",          # NEW — manual entry is free for all
        "repeat_customer_detect",
        "pdf_download", "print_preview",
        # Phase-32: REMOVED `whatsapp_template_editor` from free_trial —
        # free users now see Super Admin defaults in read-only mode.
        # Custom template authoring unlocks at Silver+.
        "form_alt_phone",
        "label_barcode_sticker",         # NEW (Phase-26) — barcode on label footer
        # NEW (2026-05-25) — Common to ALL plans, admin can untick if
        # needed. Terminal status workflow, courier inquiry button,
        # support center, video tutorials, multi-address & 9-script
        # Indic Smart Paste are baseline UX for every user.
        "shipment_terminal_states",
        "courier_whatsapp_inquiry",
        "support_tickets",
        "video_tutorials",
        "smart_paste_indic_extended",
        "smart_paste_multi_address",
    ],
    "silver": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine",
        "smart_paste_duplicate_check",   # NEW
        # NEW (Phase G3) — File Import (Silver+): Excel + custom field
        # mapping unlock for paid tiers.
        "file_import_csv", "file_import_excel",
        "file_import_status_mapping",
        "file_import_timestamp_mapping",
        "file_import_custom_fields_mapping",
        # NEW (Phase G3) — Webhooks basic: master switch + Dukaan/
        # Shopify auto-detect (most common stores).
        "webhook_ingestion",
        "webhook_dukaan_auto_map",
        "webhook_shopify_auto_map",
        # NEW (Phase G3) — Pending Orders & Coupons same as free.
        "pending_orders_unified_list",
        "pending_orders_source_badges",
        "pending_orders_edit",
        "pending_orders_delete",
        "pending_orders_new_marker",     # NEW (Phase-21)
        "pending_orders_repeat_marker",  # NEW (Phase-21)
        "new_order_sound",               # NEW (Phase-21)
        "coupons_apply",
        "credit_packages_buy",
        # NEW (Phase G3) — Reports: basic Partner Comparison for Silver+.
        "reports_partner_comparison",
        # NEW (Phase G3) — Granular notification prefs.
        "notification_prefs_granular",
        # NEW (Phase G3) — Save customer to phone contacts.
        "contact_save_to_phone",
        # NEW (Phase G3) — Per-courier required-field config.
        "field_requirements_per_courier",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "csv_export_orders",             # NEW (2026-04-30 PM2) — Silver+
        "label_brand_logo", "label_brand_name", "label_brand_tagline",
        "label_field_toggles",
        "sheet_import", "sheet_two_way_sync",
        "sheet_restore_my_orders",       # NEW — Phase-C restore for paid tiers
        "sheet_soft_delete_tombstone",   # NEW — audit trail for paid tiers
        "master_order_id_autofill_new",  # NEW
        "multiple_couriers", "auto_tracking",
        "scanner_manual_entry",          # NEW
        "scanner_sound_feedback",        # NEW
        "repeat_customer_detect", "pending_orders_inbox",
        "repeat_customer_banner",        # NEW (2026-04-30 PM2) — Silver+
        "bulk_print", "pdf_download", "print_preview",
        "whatsapp_template_editor", "whatsapp_eta_customization",
        "form_alt_phone", "form_box_dimensions",
        "label_barcode_sticker",         # NEW (Phase-26)
        # NEW (2026-05-25) — Common to ALL plans, admin can untick.
        "shipment_terminal_states",
        "courier_whatsapp_inquiry",
        "support_tickets",
        "video_tutorials",
        "smart_paste_indic_extended",
        "smart_paste_multi_address",
    ],
    "gold": [
        "smart_paste_ai", "smart_paste_voice", "smart_paste_image_ocr",
        "smart_paste_chat_refine", "smart_paste_custom_prompt",
        "smart_paste_duplicate_check",   # NEW
        # NEW (Phase G3) — File Import: full mapping suite.
        "file_import_csv", "file_import_excel",
        "file_import_status_mapping",
        "file_import_timestamp_mapping",
        "file_import_custom_fields_mapping",
        # NEW (Phase G3) — Webhooks (Gold+): full suite incl. multiple
        # endpoints + payload preview + auto-mapping.
        "webhook_ingestion",
        "webhook_multiple_endpoints",
        "webhook_dukaan_auto_map",
        "webhook_shopify_auto_map",
        "webhook_payload_preview",
        # NEW (Phase G3) — Pending Orders & utility flags.
        "pending_orders_unified_list",
        "pending_orders_source_badges",
        "pending_orders_edit",
        "pending_orders_delete",
        "pending_orders_new_marker",     # NEW (Phase-21)
        "pending_orders_repeat_marker",  # NEW (Phase-21)
        "new_order_sound",               # NEW (Phase-21)
        "coupons_apply",
        "credit_packages_buy",
        # NEW (Phase G3) — Reports: most types unlock at Gold.
        "reports_partner_comparison",
        "reports_reconciliation",
        "reports_return_analysis",
        # NEW (Phase G3) — Notifications + utility.
        "notification_prefs_granular",
        "contact_save_to_phone",
        "field_requirements_per_courier",
        # NEW (Phase G3) — Auto-routing engine for high-volume shops.
        "courier_rules_engine",
        # NEW (Phase G3) — Team invites at Gold (RBAC stays Platinum).
        "team_members_invite",
        # NEW (Phase G3) — Business category as Analytics filter.
        "business_category_filter_analytics",
        "shipment_copy_btn", "shipment_whatsapp_btn", "shipment_edit_btn",
        "shipment_delete_btn", "shipment_print_btn", "shipment_mark_delivered",
        "shipments_bulk_select",         # NEW (2026-04-30 PM2) — Gold+
        "csv_export_orders",             # NEW
        "label_brand_logo", "label_brand_name", "label_brand_tagline",
        "label_custom_fields", "label_field_toggles", "label_logo_shape",
        "label_size_options",
        "label_customer_id", "label_content_budget",  # NEW
        "sheet_import", "sheet_two_way_sync", "sheet_column_mapping",
        "sheet_restore_my_orders",       # NEW
        "sheet_two_way_status_sync",     # NEW — full-power sheet sync for Gold+
        "sheet_soft_delete_tombstone",   # NEW
        "master_order_id_counter_custom",  # NEW — Gold+ shops need legacy series continuity
        "master_order_id_autofill_new",    # NEW
        "multiple_couriers", "auto_tracking", "manual_tracking_scan",
        "scanner_sound_feedback", "scanner_double_confirm", "scanner_manual_entry",  # NEW (full scanner UX)
        "repeat_customer_detect", "repeat_items_dialog", "pending_orders_inbox",
        "repeat_customer_banner",        # NEW
        "offline_mode", "offline_create_shipment", "offline_sync_queue_view",  # NEW (offline for Gold+)
        "bulk_print", "pdf_download", "print_preview",
        "whatsapp_template_editor", "whatsapp_eta_customization", "whatsapp_copy_template",
        "whatsapp_per_courier_template",  # NEW (2026-04-30 PM2) — Gold+ exclusive
        "ai_rate_customization",
        "form_alt_phone", "form_box_dimensions", "form_token_amount", "form_shipment_notes",
        "custom_fields",                 # NEW (2026-04-30 PM3) — Gold+ (up to 3 fields)
        "label_barcode_sticker",         # NEW (Phase-26)
        "field_controls",                # NEW (Phase-27) — power feature, Gold+
        # NEW (2026-05-25) — Common to ALL plans, admin can untick.
        "shipment_terminal_states",
        "courier_whatsapp_inquiry",
        "support_tickets",
        "video_tutorials",
        "smart_paste_indic_extended",
        "smart_paste_multi_address",
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
