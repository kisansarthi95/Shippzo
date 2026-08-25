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

## Phase F8.1 (Jun-2026) — contact_email + OTP-verified Password Reset
- `contact_email` (user's registered email; overridable via new profile field) now rides on EVERY auth OTP webhook payload (`otp_login`, `otp_signup`, new `otp_password_reset`) — even when the operator's saved field list pre-dates the feature — so their automation can deliver OTP via email.
- New endpoints: POST /api/auth/forgot-password/request-otp (email+phone gate → fires password_reset OTP webhook), POST /api/auth/contact-email (set/clear dedicated OTP email). /api/auth/forgot-password now REQUIRES a valid single-use OTP (3rd factor).
- New event `otp_password_reset` auto-seeded into whatsapp_event_triggers; `contact_email` added to AVAILABLE_FIELDS + auth defaults; test-send debug includes it.
- Frontend: 2-step Forgot Password screen (Send OTP → OTP+new password, resend cooldown); Settings → My Account → "Contact Email (for OTP)" editor.
- Tests: tests/test_phase_f81_contact_email_otp.py (11 pass). Dedicated test user f81-reset-user@example.com.


## Phase F12 (Aug-2026) — Audience Hub & Navigation Overhaul
- **Nav bar redesign:** bottom tab bar reduced to 5 tabs — Home, Orders, **Audience**, Shipments, Settings. Central "+" (Ship) tab removed. The legacy `/(tabs)/add` screen still exists but is hidden from the tab bar (href:null) and reached via router.push("/add") from the Audience FAB.
- **New Audience tab** (`/(tabs)/audience.tsx`) — premium customer cards derived directly from the `shipments` collection. Four filter chips with live counts: All / New (1 order) / Returning (2+) / Imported (any shipment with `import_batch_ids`). Search bar, pull-to-refresh, screenCache-backed stale-while-revalidate.
- **Card layout matches spec:** name (bold) → "phone | email" line → city/state with pin icon → optional IMPORTED badge → stats row split (TOTAL ORDERS | TOTAL SALES). Sales sums `amount` only for Delivered orders (successful orders).
- **Floating "+" FAB** on Audience tab replaces the old central tab button; opens the existing manual-entry screen. Permission-gated (shipments_create / smart_paste_ai / file_import_csv).
- **New Audience Profile screen** (`/audience/[key].tsx`) — avatar, name, tap-to-call phone chip, tap-to-mail email chip, default address, Total Orders + Total Sales summary strip, and a clickable Order History list linking each row to `/shipment-details/[id]`.
- **New backend router** `routers/audience.py` (3 endpoints, prefix /api):
  - `GET /me/audience/stats` — { all, new, returning, imported }
  - `GET /me/audience?segment=&q=&limit=&offset=` — segmented + searchable list
  - `GET /me/audience/{customer_key}` — profile + full order history

## Phase F11.L (Aug-2026) — Atomic Tracking Allocation + Cleanup COMPLETE
- Backend: `couriers.py::consume_tracking` uses `find_one_and_update` with `$inc:{next_number:1}` — atomic under concurrent creates.
- `shipments_write.py` allocates tracking server-side inside the insert path (no client-side counter allocation).
- `server.py` creates MongoDB unique partial index `uniq_user_trackingId` on `(user_id, tracking_id)` with `partialFilterExpression={tracking_id:{$exists:true, $type:"string", $gt:""}}` — allows blank tracking rows for manual-mode couriers.
- **One-time cleanup executed:** the cleanup script (`/tmp/cleanup_dupes_v2.py`) walked all `shipments`, kept the oldest doc per duplicate `(user_id, tracking_id)` and reassigned fresh atomic IDs (with unused-check loop) to the losers. 50+ duplicates cleared including the ND00013 case.
- **Testing:** `tests/test_phase_f11l_tracking_atomic.py` (7 pass): concurrent 20-way allocation → 20 unique IDs; direct-Mongo dup insert → DuplicateKeyError; per-tenant isolation (same tracking_id allowed under different user_id); partial filter allows blank tracking rows.

  - Uses `$group` aggregation over shipments by `customer_phone` then Python-side re-merges by normalized last-10-digits so "+91 98..." and "98..." collapse to one card.
