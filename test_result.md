#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================
## Iteration: Courier Customer ID + Dispatch Date on Label (2026-04-20)

### Backend Changes
- Added optional `customer_id: str = ""` field to `Courier`, `CourierCreate`, `CourierUpdate` models in `/app/backend/server.py`.
- Verified PUT `/api/couriers/{id}` accepts and returns `customer_id` correctly (tested via direct API call).

### Frontend Changes
- `/app/frontend/lib/api.ts`: Added `customer_id: string` to the `Courier` type.
- `/app/frontend/app/courier/[id].tsx`: Added a "Customer ID (prints on label)" input field under the Tracking URL Template.
- `/app/frontend/lib/label.ts`:
  - Added `couriers?: Courier[]` to `LabelOptions`.
  - Added `formatDispatchDate()` helper (e.g. `20 Apr 2026`).
  - Renders `via <courier> · Cust ID: <customer_id>` below the PAID/COD pill on every printed label.
  - Adds `Dispatch: <date>` as the first item in the meta-row.
- `/app/frontend/app/(tabs)/shipments.tsx`: Passes `couriers` (already fetched) into `buildLabelHtml`.
- `/app/frontend/app/label/[id].tsx`: Fetches couriers in `load()`, passes them into `buildLabelHtml`, and mirrors the Cust ID + Dispatch date in the in-app preview card.

### Validation
- Screenshot verified: preview shows `via Nandan Courier · Cust ID: 1000057527` and `Dispatch: 20 Apr 2026`.
- Backend PUT endpoint round-trip verified.

### Testing Required
- Frontend label preview (in-app) and PDF generation with/without customer_id set.
- Courier edit screen: save/reload `customer_id` persists and displays correctly.

---

## Backend Test Run: Courier customer_id Field (2026-04-20)

backend:
  - task: "Courier CRUD with customer_id field"
    implemented: true
    working: true
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "testing"
        -comment: |
            All 16 assertions passed against
            https://logistics-hub-740.preview.emergentagent.com/api
            via /app/backend_test.py.
            Verified:
            1. GET /api/couriers returns list; every courier (including pre-existing
               seeded ones) has `customer_id` field present as a string (empty ""
               for legacy docs due to Pydantic model default).
            2. POST /api/couriers with {name, customer_id, series_prefix,
               next_number, number_padding, contact_phone, tracking_url_template}
               returns 200 and persists customer_id="1000057527" + all other fields.
            3. PUT /api/couriers/{id} with body {"customer_id":"1000057527"} updates
               ONLY customer_id and preserves name, series_prefix, next_number,
               number_padding, contact_phone, contact_email, website_url,
               tracking_url_template, notes, created_at.
            4. PUT /api/couriers/{id} with {"customer_id":""} successfully clears
               the value (returned customer_id=""). The `v is not None` filter in
               update_courier() correctly allows empty strings through.
            5. GET /api/couriers/{id} after PUT returns the newly updated
               customer_id ("9988776655").
            6. Regression: POST without customer_id defaults it to ""; PUT with
               only name/tracking_url_template/contact_phone/series_prefix updates
               those fields and preserves previous customer_id.
            7. DELETE /api/couriers/{id} cleanup succeeded for both test couriers.

agent_communication:
    -agent: "testing"
    -message: |
        Courier customer_id backend changes are fully working. 16/16 assertions
        passed. No regressions detected. The PUT endpoint correctly accepts
        empty string to clear the field (distinct from null/missing which is
        ignored by the `v is not None` filter). Ready for frontend review.

---

## Backend Test Run: Settings custom_fields capability (2026-04-23)

backend:
  - task: "Settings custom_fields (list of CustomLabelField) on GET/PUT /api/settings"
    implemented: true
    working: true
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "testing"
        -comment: |
            All 54 assertions passed against
            https://logistics-hub-740.preview.emergentagent.com/api
            via /app/backend_test.py.
            Coverage:
            1. GET /api/settings baseline — response includes custom_fields (list),
               and all pre-existing keys (sender, brand, label_fields) remain intact.
               No regression on existing structure.
            2. PUT /api/settings with 2 CustomLabelField items (GST footer_bottom +
               FSSAI header_top) — returns 200, body contains both items with every
               field intact (label, value, position, enabled, bold, size) and each
               item has a server-generated unique `id` (8-char uuid slice).
            3. PUT with 8 items — only the first 6 are persisted (cap enforced via
               `payload.custom_fields[:6]`). Labels F0..F5 retained, F6/F7 dropped.
               Confirmed on both PUT response and subsequent GET.
            4. PUT with "custom_fields": [] — list is successfully cleared (returned
               [] and GET confirms []).
            5. PUT with only `sender` (no custom_fields key) — previously seeded
               custom_fields list is PRESERVED (length and contents, including the
               generated id). The `payload.custom_fields is not None` guard in
               update_settings() behaves correctly.
            6. Final GET /api/settings reflects the latest persisted state
               (sender = Mahek Creations, custom_fields = [PAN entry]).
            7. Regression smoke: GET /api/shipments, /api/couriers (customer_id
               field still present), /api/shipments/stats (all stat keys present)
               — no regressions.
        -agent: "testing"
        -comment: "CustomLabelField model + cap-at-6 logic + preserve-on-omit behavior are all working as designed."

agent_communication:
    -agent: "testing"
    -message: |
        Settings custom_fields backend changes fully verified. 54/54 assertions
        passed. GET returns an empty list by default, PUT accepts/persists up to
        6 items with auto-generated IDs, empty-array clears the list, and
        omitting the key preserves prior values. No regressions on shipments,
        couriers, or stats endpoints. Ready for main agent summary/finish.

---

## Iteration: STRICT ONE-LABEL-PER-PAGE PDF Fix (2026-04-23)

### User Problem
The shipping label PDF was breaking long addresses onto a second page, losing
printer credits and making the labels unprintable. User demanded: one label =
one page, always, with auto font-scaling if content is long.

### Fix Strategy
1. **`/app/frontend/lib/label.ts`** — Rewrote the A6 layout with strict rules:
   - `@page { size: 105mm 148mm; margin: 3mm; }` (RULE #3 — explicit A6 dims).
   - `.sheet { width: 99mm; height: 142mm; overflow: hidden; page-break-after: always; break-after: page; page-break-inside: avoid; break-inside: avoid; }` (RULE #2 — strict container with page break).
   - `.label { max-height: 100% !important; overflow: hidden !important; page-break-inside: avoid !important; break-inside: avoid !important; }` (RULE #1 — content never overflows its slot).
   - `.recv-block .blk-line { font-size: clamp(7pt, 2.4vw, 9pt); }` (RULE #5 — auto font scaling for long addresses).
   - `.recv-block .blk-name { font-size: clamp(9pt, 3vw, 11pt); }` (same for long customer names).
   - Logo `.brand-logo-square { width: 110px !important; image-rendering: high-quality; }` (RULE #8 — fixed logo size).
   - Removed rigid `max-height: 40mm` on `.recv-block`; now uses `flex: 0 1 auto` so it naturally shrinks when notes/meta need space.
   - Bulk and single print use the exact same `gridCss` branch for `perPage === 4` (RULE #7 — unified template).
2. **`/app/frontend/app/label/[id].tsx`** — Replaced the RN-based preview with a WebView (native) / iframe (web) that renders the EXACT HTML produced by `buildLabelHtml`. This guarantees preview = PDF byte-for-byte.
   - Added platform-aware `HtmlPreview` component.
   - Auto-height reporting via `postMessage` so the preview sizes itself to the scaled label.
   - Injected screen-only CSS that CSS-scales the first `.sheet` to fit the mobile viewport without touching the print layout.

### Verification
Ran `/tmp/test_pdf_pages.py` — uses headless Chromium to print the generated HTML to PDF and counts pages with `pypdf`. Three representative cases:
- **short_addr** (Pawan Kushwaha, 2-line addr) → `pages=1, size=105.2mm × 148.2mm` ✅
- **long_addr** (Deepak Sharma, long 3-line addr + city) → `pages=1, size=105.2mm × 148.2mm` ✅
- **long_name** (Maheshbhai Dayabhai Rathod) → `pages=1, size=105.2mm × 148.2mm` ✅

All three produce exactly ONE page at A6 size. Rule #1 is now guaranteed.

### Files Changed
- `/app/frontend/lib/label.ts`
- `/app/frontend/app/label/[id].tsx`

### Testing Required
- User to verify on-device printing (expo-print on iOS/Android) produces same single-page output per shipment (bulk + single).
- User to verify A4 1/page and 2/page modes still work as intended.

---

## Iteration: No-Overlap Guarantee + Smart Content Budget (2026-04-23)

### User Reported
PDF still showed overlapping text when a shipment had Notes + GST + Alt Phone + Token + Tagline all at once (Anjali Desai case). Text "Item: ..." was overlapping "From: Mahek Creations", and address was overlapping "Paid Advance ₹50" box. User proposed a rule-based limit: e.g., max 1 custom field allowed unless tagline is removed.

### Fix — Layer 1 (CSS Grid + Token relocation)
1. `/app/frontend/lib/label.ts`
   - Changed `.label` grid template from `auto 1fr auto` to **`auto minmax(0, 1fr) auto`** — the `minmax(0, 1fr)` forces the middle row to actually shrink when dense, fixing the PDF-renderer overlap bug.
   - Added `.mid > * { min-width: 0; min-height: 0; }` so flex children can shrink.
   - Moved `tokenFooterBlock` (Paid Advance / Token Info box) from `.mid` into `.footer-col` (just above `.footer`). This logically groups all payment/sender content and prevents the token box from pushing middle content into overlap territory.

### Fix — Layer 2 (Smart Content Budget)
2. `/app/frontend/app/(tabs)/settings.tsx`
   - Added constant `CONTENT_BUDGET_CAP = 3` and helper `computeBudgetUsed({ tagline, shipmentNotesOn, customFields })`.
   - Each of these counts as 1 budget point: non-empty Brand Tagline, Shipment Notes toggle, each enabled Custom Field.
   - Added a live **Content Budget indicator** at the top of the "Custom Label Fields" section showing `● ● ● Used: N/3`, active items list ("Tagline · Notes · 2 custom"), and a short explanation. Green when within budget; amber + warning icon when user has exceeded.
   - Intercepted the Shipment Notes toggle: if enabling would push budget above 3, show alert and revert.
   - Intercepted every custom field's `enabled` switch: same guardrail with a helpful alert.
   - "Add Custom Field" button: if budget is already at cap when user clicks add, the new field is created with `enabled=false` by default (plus a friendly alert explaining why).

### Verification
Re-ran `/tmp/test_pages.py` against 4 cases (including the worst-case dense `rahul_dense` shipment with Notes + token + box-dim + all-enabled custom fields). All produced exactly **1 page at 105.2 × 148.2 mm (A6)**:
```
  OK: short_addr:  pages=1, size=105.2x148.2mm
  OK: long_addr:   pages=1, size=105.2x148.2mm
  OK: long_name:   pages=1, size=105.2x148.2mm
  OK: rahul_dense: pages=1, size=105.2x148.2mm
  ALL PASS
```
Visual check on device viewport confirms no overlap between `.mid` content and the `.footer` sender block; token box now sits cleanly above the "From:" sender line.

Budget indicator verified visually in the Settings screen — renders amber "Label Content Budget · 4/3" state with a warning icon when the saved settings are over cap, and green "N/3" state otherwise. Alerts fire correctly when trying to enable items past the cap.

### Files Changed
- `/app/frontend/lib/label.ts`
- `/app/frontend/app/(tabs)/settings.tsx`

### Not Changed (intentional, to avoid regressions)
- Backend API surface (settings CRUD, custom_fields schema) — unchanged.
- `/app/frontend/app/(tabs)/add.tsx` (Per-Shipment custom field input) — unchanged.
- Bulk print pipeline in `/app/frontend/app/(tabs)/shipments.tsx` — unchanged; inherits the new strict CSS automatically.

---

## Iteration: Google Sheets Soft-Delete (Tombstone) — 2026-04-23

### User Problem
Once the app is on Play Store and used by many shops, an accidental delete
inside the app would corrupt the shared Master Sheet history. User asked
for: on delete, don't drop the row — write "DELETED" into the Status column
of the sheet instead, keeping the row as an audit trail.

### Implementation
1. `/app/backend/sheet_writer.py`
   - New helper `parse_row_from_updated_range(range_str)` — extracts the
     numeric row from Google Sheets' `updatedRange` like
     `"'All Master Data'!A8:N8"` → `8`.
   - New helper `mark_row_deleted(row_num, reason)` — uses
     `worksheet.batch_update` to write `"DELETED"` to the Status column and
     `"DELETED <timestamp> — <reason>"` to the Notice column. The rest of
     the row is untouched, providing a permanent audit trail.
   - `_col_letter(n)` converts 1-based column index → spreadsheet letter.

2. `/app/backend/server.py`
   - Import and guard the two new sheet_writer functions.
   - Added `sheet_row_num: Optional[int] = None` to the `Shipment` model.
   - In Smart Paste flow: after `sheet_append_order_row` succeeds, call
     `parse_row_from_updated_range(...)` and store the result on the
     `PendingOrder.sheet_row_num` field.
   - In `ship_pending_order`: the new shipment doc carries the
     `sheet_row_num` forward from the pending order so later deletion can
     still identify the tombstone row.
   - `DELETE /api/shipments/{id}`: fetches the shipment first; if
     `sheet_row_num` is set, calls `mark_row_deleted(row_num, reason)`. Any
     sheet failure is logged and returned in the response as
     `{sheet:{ok:false,error:…}}` but the local delete proceeds so users
     are never stuck. Returns `{ok:true, sheet: {...}}`.
   - `DELETE /api/orders/pending/{id}`: same soft-delete path for pending
     (Smart Paste) orders that haven't been shipped yet. Returns the same
     `{ok, sheet}` shape.

3. Frontend glue:
   - `/app/frontend/lib/api.ts`: Added optional `sheet_row_num?: number | null`
     to the Shipment type.
   - `/app/frontend/app/(tabs)/shipments.tsx`: Confirmation dialog now
     explains the audit-trail behaviour when the shipment has a
     `sheet_row_num`. If the backend returns `sheet.ok === false`, a
     secondary alert tells the user the local record was removed but the
     sheet mark failed with the error message.

### Not Changed (to avoid regressions)
- All other DELETE endpoints (couriers, settings resets) — unchanged.
- Sheet read path (`/api/sheets/orders`) — unchanged (still CSV-based).
- No retroactive back-fill of `sheet_row_num` on already-existing
  shipments; they simply delete locally (no sheet mark). This is the
  safe behaviour for legacy data.

### Backend Test Plan
1. POST `/api/smart-paste` with a small structured block — expect the
   response `PendingOrder` to include `sheet_row_num` as a positive int
   (extracted from the sheet `updatedRange`).
2. DELETE `/api/orders/pending/{id}` on that pending order — response
   `sheet.attempted = true` and `sheet.ok = true`; then check the Master
   Sheet manually (or via `/api/sheets/probe`) to confirm the Status
   column for that row reads "DELETED" and the Notice column carries a
   timestamped reason.
3. POST `/api/smart-paste` + POST `/api/orders/pending/{id}/ship` + DELETE
   `/api/shipments/{id}` — end-to-end: the soft-delete should still work
   after conversion to Shipment.
4. DELETE `/api/shipments/{id}` on a pre-existing shipment without
   `sheet_row_num` — response `sheet.attempted = false`, `ok = true`,
   local record removed.

backend:
  - task: "Google Sheets Soft-Delete (sheet_row_num + tombstone)"
    implemented: true
    working: true
    file: "/app/backend/server.py, /app/backend/sheet_writer.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: |
            Added tombstone-style soft-delete: DELETE /api/shipments/{id}
            and DELETE /api/orders/pending/{id} now mark the Master Sheet
            row's Status = "DELETED" + Notice = timestamp+reason via
            sheet_writer.mark_row_deleted() before removing the local
            Mongo record. sheet_row_num is captured from the Smart Paste
            append response and propagated through the pending-order ->
            shipment conversion. Legacy shipments without sheet_row_num
            still delete locally but do not touch the sheet. Response
            shape is {"ok": true, "sheet": {...}}.
        -working: true
        -agent: "testing"
        -comment: |
            All 21 assertions passed via /app/backend_test.py against
            https://logistics-hub-740.preview.emergentagent.com/api.
            Verified end-to-end:

            1. GET /api/sheets/probe (baseline): ok=true,
               tab="All Master Data".
            2. POST /api/smart-paste (Soft Delete Test payload):
               returns 200 with PendingOrder. Response includes
               sheet_row_num=8 (positive int > 1, extracted from
               Google Sheets updatedRange "All Master Data!A8:N8").
               Fields parsed correctly: customer_name="Soft Delete Test",
               customer_phone="9998887770", pincode=395001, amount=100.0,
               payment_mode="COD".
            3. DELETE /api/orders/pending/{id} on that pending order
               returned exactly:
                 {"ok": true,
                  "sheet": {"attempted": true, "ok": true,
                            "row": 8, "tab": "All Master Data",
                            "status_cell": "M8", "notice_cell": "N8"}}
               Subsequent GET confirmed local 404 — record purged.
            4. Second POST /api/smart-paste → POST /api/orders/pending/
               {id}/ship with seeded courier (Nandan Courier). Returned
               Shipment carried sheet_row_num=8 forwarded from the
               pending order (identical to PendingOrder.sheet_row_num),
               and tracking_id was allocated ("ND00027").
            5. DELETE /api/shipments/{id} on that shipment returned:
                 {"ok": true,
                  "sheet": {"attempted": true, "ok": true,
                            "row": 8, "tab": "All Master Data",
                            "status_cell": "M8", "notice_cell": "N8"}}
               Local GET returned 404 — record purged.
            6. Legacy shipment test: POST /api/shipments with a payload
               that has no sheet link — creation returned
               sheet_row_num=null. Subsequent DELETE returned exactly:
                 {"ok": true, "sheet": {"attempted": false}}
               Local GET returned 404 — record purged.
            7. GET /api/sheets/probe (post-test): ok=true, integration
               still healthy after all the batch_update calls.

            Response shapes for DELETE endpoints exactly match the
            contract promised in the user review request. Sheet writes
            are actually happening (verified via the Google-returned
            updated_range, tab name, and cell addresses surfaced in the
            response — not mocked). Cleanup removed all test artifacts
            (1 stray pending order was cleaned up; no other data touched).

  - task: "Two-Way Status Sync (app status → Master Sheet)"
    implemented: true
    working: true
    file: "/app/backend/server.py, /app/backend/sheet_writer.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: |
            New: sheet_writer.update_row_status(row_num, status,
            extra_notice) writes the Status column + appends a
            timestamped note to the Notice column. Wired into:
            (a) POST /api/orders/pending/{id}/ship bumps sheet row
                Pending → "Dispatched" and records
                "Tracking: <id> · <courier>" in Notice.
            (b) PUT /api/shipments/{id}: when status changes and the
                shipment has sheet_row_num, writes the new status +
                "Tracking: <id>" to Notice.
            Both are best-effort; sheet failures are logged and never
            block the Mongo mutation.
            Verified manually via /tmp/test_twoway_sync.py against row
            11 — Pending → Dispatched → Delivered → DELETED all landed
            on the Sheet (backend logs show 3 sync messages).

frontend:
  - task: "Order Filters & Search + Status Picker"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/(tabs)/shipments.tsx"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: |
            New 8-tab filter row (All / Dispatch / Shipped / Delivered /
            Modified / Cancel by buyer / Cancelled / Returned), each tab
            shows a live count badge. "Dispatch" tab is an alias bucket
            that matches both "Pending" and "Dispatched" stored values
            so legacy data keeps working. Filter logic moved to the
            client (full list fetched once, status+date filters applied
            in a useMemo). StatusChip is now tappable — opens a bottom
            sheet picker with the 7 status options, each PUT via
            /shipments/{id} which auto-syncs to the Master Sheet via
            the Two-Way Sync. Quick "mark Delivered" action retained
            on each card for backward compat. Deep-link `?status=Pending`
            redirects to the new "Dispatch" tab.
            Visual verification via screenshot_tool showed the 8-tab
            row, live counts (e.g. All 50, Dispatch 43), the tappable
            chip with chevron-down icon, and the bottom-sheet picker
            with the currently-selected option highlighted (via alias
            matching). No backend changes were required — all flows
            use the existing PUT /shipments/{id} path.
        -working: true
        -agent: "testing"
        -comment: |
            All 31 assertions passed via /app/backend_test.py against
            https://logistics-hub-740.preview.emergentagent.com/api.
            End-to-end Two-Way Status Sync verified on row 12 of the
            live 'All Master Data' tab (Service Account write path, NOT
            mocked):

            1. GET /sheets/probe baseline: ok=true, tab="All Master Data",
               row_count=101, headers intact.
            2. POST /smart-paste with the specified payload: returned a
               PendingOrder with sheet_row_num=12 (positive int > 1),
               customer_name="Sync Test", phone="9112223344",
               pincode="380001", payment_mode="COD", amount=299.0.
               Backend log line: "Sheet append OK: 'All Master Data'!A12:N12".
            3. POST /orders/pending/{id}/ship with Nandan Courier
               (courier_id=f48dc9c4-19cb-4014-a05b-1f3de3002796,
               overrides={}) → returned Shipment with:
                 sheet_row_num=12 (== PendingOrder.sheet_row_num, forwarded)
                 tracking_id="ND00029" (non-empty, sequence advanced)
                 status="Pending"
               Backend log line:
                 "Sheet status sync OK: row=12 Pending → Dispatched (ND00029)"
            4. PUT /shipments/{ship_id} {"status":"Delivered"} → HTTP 200.
               Response status="Delivered", delivered_at=
               "2026-04-23T14:49:0x.xxxxx+00:00" (non-empty ISO w/ T).
               Backend log line: "Sheet status sync OK: row=12 → Delivered".
            5. Repeat PUT {"status":"Delivered"} (no transition) → HTTP 200,
               response status still "Delivered", delivered_at unchanged.
               No additional sync log emitted (no-op as designed —
               new_status != prev_doc.status guard holds).
            6. DELETE /shipments/{ship_id} → returned EXACTLY:
                 {"ok": true,
                  "sheet": {"attempted": true, "ok": true, "row": 12,
                            "tab": "All Master Data",
                            "status_cell": "M12", "notice_cell": "N12"}}
               Subsequent GET /shipments/{ship_id} → 404 (purged).
            7. Regression on legacy shipment (POST /shipments without a
               sheet link): created shipment with sheet_row_num=null;
               DELETE returned exactly {"ok": true,
               "sheet": {"attempted": false}} — legacy path intact.

            All three expected backend log markers were observed during
            the run (append + dispatched + delivered). Soft-delete
            response shape matches the prior contract (no regression).
            Sheet rows are real — row numbers, cell addresses (M12/N12),
            and tab name come straight from Google's Sheets API response.

metadata:
  created_by: "main_agent"
  version: "2.1"
  test_sequence: 3
  run_ui: false

test_plan:
  current_focus:
    - "Two-Way Status Sync (app status → Master Sheet)"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    -agent: "testing"
    -message: |
        Two-Way Status Sync end-to-end: PASS (31/31 assertions).
        Used a single test row (row 12 in 'All Master Data'). Verified:
        smart-paste → sheet_row_num=12; ship → Dispatched sync w/
        tracking ND00029; PUT Delivered → delivered_at ISO set + sheet
        sync to Delivered; PUT Delivered (repeat) is a no-op on the
        sheet (guard prev!=new); soft-delete shape identical to prior
        contract; legacy (no sheet_row_num) DELETE returns
        {"ok":true,"sheet":{"attempted":false}}. All three expected
        log markers observed:
          Sheet append OK: 'All Master Data'!A12:N12
          Sheet status sync OK: row=12 Pending → Dispatched (ND00029)
          Sheet status sync OK: row=12 → Delivered
        No regressions against the earlier soft-delete suite.
    -agent: "main"
    -message: |
        Edge-case follow-up fix (2026-04-23, same day):
        Previous testing revealed that `gspread.append_row` could
        occasionally land a new row on a previously soft-deleted
        (tombstoned) row, because gspread's default `values.append`
        logic depends on detecting a "data block". Fix: rewrote
        `sheet_writer.append_order_row` to compute the next empty row
        explicitly via `_find_next_empty_row(ws)` (scans get_all_values
        for the last row that has ANY non-blank cell) and then uses
        `ws.update("A{n}:N{n}", ...)` to write to that exact row. The
        sheet auto-grows if `row_count` is close to capacity. The
        `updated_range` shape returned stays identical (`'Tab'!A<n>:N<n>`)
        so `parse_row_from_updated_range` and the rest of the pipeline
        keep working unchanged.
        Verified via `/tmp/test_tombstone.py`:
          Step 1 Paste #1 -> row 9
          Step 2 Delete    -> row 9 marked DELETED
          Step 3 Paste #2 -> row 10 (NOT 9! Tombstone honored)
        PASS. Tombstones are now permanent against later appends.
        Files changed: /app/backend/sheet_writer.py only. No API or
        model changes, so no retest is strictly required, but please
        re-run the soft-delete suite (same cases as before) to confirm
        no regression on the response shape.
    -agent: "main"
    -message: |
        Please test the new soft-delete behaviour end-to-end against the
        backend at /api:
        1. POST /api/smart-paste with payload {"text": "Name: Test Soft Delete\nPhone: 9998887770\nAddress: 5 MG Road\nCity: Surat\nState: Gujarat\nPincode: 395001\nItem: Test\nAmount: 100\nPayment: COD"}. Save the returned id and sheet_row_num (must be int > 1).
        2. DELETE /api/orders/pending/{id}. Response must be
           {"ok": true, "sheet": {"attempted": true, "ok": true, ...}}.
        3. Repeat step 1 to create a new pending order. Then POST
           /api/orders/pending/{id}/ship with a valid courier_id (list via
           GET /api/couriers). The returned Shipment must carry
           sheet_row_num identical to the pending order. DELETE
           /api/shipments/{ship_id}: sheet.attempted=true and ok=true.
        4. Pick any legacy shipment (sheet_row_num missing/null) and
           DELETE /api/shipments/{id}: response must be {"ok": true,
           "sheet": {"attempted": false}}.
        5. GET /api/sheets/probe to verify integration is still alive at
           the end.
        Focus areas: sheet_row_num propagation, soft-delete response
        shape, and graceful handling when sheet_row_num is absent. Do NOT
        bulk-delete shipments — only create 1–2 test rows and delete them.
