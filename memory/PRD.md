# Courier Label Manager — PRD (Iteration 4)

## Iteration 4 fixes & additions
- **Dashboard refresh button** — manual sync of stats + shipments with one tap
- **Bulk print mode** — tap checkbox icon in Shipments header → select multiple shipments (or tap "Select all" / long-press card) → print 1/2/4 per A4 page together OR preview PDF
- **Date filter** — second filter row: All dates / Last 24h / Last 7 days / Last 30 days (prevents re-printing old labels)
- **Brand on labels** — Settings → Brand on Labels section; brand name replaces courier name on top of every label PDF. COD/Prepaid amount + "via <Courier>" moved to top-right (small line below the big amount)
- **Scanner existing-check** — on scan or manual entry, app first checks DB; if tracking exists → opens that label, else → opens new shipment form
- **Tab bar height increased** — paddingBottom +8 on both iOS (34) and Android (18) to avoid phone home/back button overlap
- **Enhanced dashboard** — Total Revenue card (primary), COD count+amount card, Prepaid count+amount card
- **Backend**: new endpoints `GET /shipments/by-tracking/{id}`, `POST /shipments/bulk-fetch`, Settings.brand field

## Known limitations carried forward
- Google Sheet write-back still needs OAuth/Apps Script (user declined)
- Camera barcode scan requires real device (Expo Go or installed build)

## Tech additions
- expo-print `printAsync` for bulk + single label
- Settings.brand `{ name, logo_base64 }` — logo stored base64 for future upload feature

## Phase F8.0 (Jun-2026) — SMS/Notification Auto-Sync Repair
- **Root causes found & fixed:**
  1. Native listener master switch was armed ONLY by legacy /courier-sync toggle — Courier Partner Settings auto-sync never armed it.
  2. Backend URL + JWT pushed to native layer only when /courier-sync screen opened → stale/empty config.
  3. senderPattern hardcoded to "IndiaPost" → all other couriers dropped on-device.
  4. "is Successful/Unsuccessful" India Post templates had no scanning rule → Delivered SMS ignored.
  5. booking_date / confirmation_status / last_event never written by SMS path (duplicate logic vs import engine).
- **Fixes:** shared `apply_last_event_engine()` in routers/shipment_import.py used by BOTH Delivery Import and SMS ingest; startup migration F8.0 patches existing courier scanning rules (adds unsuccessful→Undelivered, successful→Delivered, In Transit rules→In Transit stage, idempotent); datetime extractor supports YYYY-MM-DD & DD-MM-YYYY with optional HH:MM:SS; Shipment model now exposes booking_date/needs_return_review/return_review_at.
- **Native (requires new Android build!):** multi-pattern sender filter ("|"-joined needles), offline SMS queue (IngestQueue.kt, flush on network-available/listener-connect/app-foreground), "onIngestResult" event → JS for instant UI refresh.
- **Frontend:** lib/courier_sync_native.ts syncs native config on app start + foreground + courier save; shipments list / details / dashboard refetch on ingest event (event-driven, no polling); OFD section shows staff, beat, attempt date/time + full original SMS; Timeline shows booking_date.
- Backend tests: tests/test_phase_f80_sms_engine.py + test_phase_f80_edge_cases.py (all green, 145 total inc. regressions).
