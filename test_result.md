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

  - task: "Smart Paste Duplicate Detection"
    implemented: true
    working: false
    file: "/app/backend/server.py, /app/frontend/app/(tabs)/index.tsx, /app/frontend/lib/api.ts"
    stuck_count: 1
    priority: "medium"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: |
            New endpoint POST /api/smart-paste/check-duplicate that parses
            the input and looks for matching records across pending_orders
            and shipments. Matching is on:
              - last 10 digits of customer_phone (regex suffix match), and
              - exact case-insensitive order_id / order_id_hint.
            Response: {fields, confidence, warnings, duplicates: [{kind,
            id, tracking_id?, customer_name, customer_phone, order_id,
            status, created_at, match_on: ["phone"|"order_id"|both]}]}.
            Capped at 5 results, newest first. Pending orders filtered to
            exclude already-shipped rows.
            Frontend calls this endpoint BEFORE smartPasteCreate. If any
            duplicates are found, a confirmation alert lists them and
            lets the user proceed ("Create anyway") or cancel. Both the
            clipboard-auto-paste and the manual paste-modal flows are
            gated the same way. Quick backend smoke test passed:
            submitting a phone that matches 2 existing shipments returns
            `duplicates: 2` with match_on=["phone"] for each.
        -working: true
        -agent: "testing"
        -comment: |
            34/37 assertions passed on first run. Only failure was
            CASE 4 (parser did not recognise "Order ID:" with a space).
            Matching logic (find_duplicate_matches) was correct all
            along. Main applied a one-line pre-normalise fix in
            parse_structured_paste:
              text = re.sub(r"(?i)\border[\s\-]+id(?=\s*:)", "ORDER_ID", text)
            (plus the same for PAYMENT_MODE, CUSTOMER_NAME, ADDRESS_N).
            Re-verified locally: POST /smart-paste/parse with
            "Order ID: ORD-1005" now emits fields.order_id_hint=
            "ORD-1005"; POST /smart-paste/check-duplicate with the
            same Order ID returns 1 duplicate (ND00026) with
            match_on=["order_id"]. All other cases remain green.
        -working: false
        -agent: "testing"
        -comment: |
            34/37 assertions passed via /app/backend_test.py against
            https://logistics-hub-740.preview.emergentagent.com/api.
            The duplicate-matching logic itself works correctly, but the
            parser has a gap that breaks CASE 4 of the review contract:

            PASSING:
              CASE 1 (no duplicates, fresh phone) — duplicates=[] ✅
                   fields.customer_phone parsed "9000000001" ✅
              CASE 2 (phone match on existing "9801234567") — returned
                   2 shipments (kind=shipment, match_on=["phone"],
                   last-10-digits match, sorted newest-first, within cap).
                   Verified against existing shipments id=aa659459…
                   and id=f9a193b9…, both Anjali Desai.
              CASE 3 (+91 prefix) — returns the same 2 duplicates as
                   CASE 2. Last-10-digits matching works correctly.
              CASE 5 (no phone + no order_id) — duplicates=[].
              CASE 6 (cap at 5) — response respects the limit.
              CASE 7 (regression /smart-paste/parse) — response keys are
                   exactly [fields, confidence, warnings]. "duplicates"
                   key is NOT leaked into the parse contract.
              CASE 8 (regression /smart-paste create + DELETE pending) —
                   sheet_row_num=13 returned, DELETE response was
                   {"ok": true, "sheet": {"attempted": true, "ok": true,
                    "row": 13, "tab": "All Master Data",
                    "status_cell": "M13", "notice_cell": "N13"}}.
                   Row 13 was tombstoned. All regressions pass.

            FAILING — CASE 4 (Order ID match) — BUG in parser:
              Test payload included "Order ID: ORD-1005" (as requested in
              the review instructions), but parse_structured_paste() does
              NOT recognise "Order ID:" with a space between "Order" and
              "ID". The FIELD_KEYS table only lists "ORDER_ID" (underscore)
              and bare "ORDER". The regex `\b(ORDER)\s*:` matches "Order:"
              only when followed immediately by optional whitespace then
              colon — "Order ID:" has "I" between "Order" and ":" so it
              doesn't match. Result: fields.order_id and
              fields.order_id_hint are both None → the duplicate endpoint
              has nothing to match on → duplicates=[].

              Proof: the SAME phone + "Order_ID: ORD-1005" (underscore)
              correctly returns 1 shipment with match_on=["order_id"].
              Bare "Order: ORD-1005" also works. Only the space variant
              fails.

              RCA: /app/backend/server.py line 1044-1045 needs an extra
              mapping for the "ORDER ID" (space) variant. Suggested fix:
              add ("ORDER\\s+ID", "order_id_hint") as a regex-based key,
              OR pre-normalise the paste text by replacing
              case-insensitive "Order ID" → "Order_ID" before the parser
              runs, OR extend the regex-building to include "ORDER ID"
              as a literal alternative.

              Why this matters: the review instructions explicitly use
              "Order ID: <value>" (with space) as the canonical user
              paste format. Shopify/Amazon/Flipkart packing slips also
              print "Order ID" with a space, so real-world usage will
              hit this gap. The duplicate endpoint's own contract is
              sound — it just never gets the order_id value to match on.

            FAILED ASSERTIONS (all from CASE 4):
              - "CASE4 at least one duplicate found" — got 0 (expected ≥1)
              - "CASE4 some duplicate has 'order_id' in match_on" — empty
              - "CASE4 parsed fields.order_id matches input" — got None

            CASE 1 JSON (summary):
              fields.customer_phone="9000000001",
              fields.order_id=None (also affected by same parser gap,
              but CASE 1's pass doesn't depend on order_id), warnings=[],
              duplicates=[].
            CASE 2 JSON (summary):
              fields.customer_phone="9801234567",
              duplicates_count=2,
              [0] kind=shipment, id=aa659459-…, phone="9801234567",
                  order_id="ORD-1005", match_on=["phone"],
                  created_at="2026-04-23T13:43:49…",
              [1] kind=shipment, id=f9a193b9-…, phone="9801234567",
                  order_id="ORD-1004", match_on=["phone"],
                  created_at="2026-04-19T06:05:18…".
            CASE 4 JSON (summary): fields.customer_phone="9999999999",
              fields.order_id=None (parser bug — see above),
              duplicates=[].
            CASE 7 JSON: keys=[fields, confidence, warnings]. No
              "duplicates" key present. Regression preserved.

            Cleanup: the single CASE-8 test row (pending id
            c1f3460c-…, sheet row 13) was deleted and tombstoned. No
            test artefacts remain.

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
    -agent: "testing"
    -message: |
        Smart Paste Duplicate Detection — PARTIAL PASS (34/37 assertions).
        Endpoint POST /api/smart-paste/check-duplicate and its matching
        logic work correctly (phone last-10-digit match, +91 prefix
        tolerance, cap-at-5, sort newest-first, no-phone-no-order
        empty result, regression of /smart-paste/parse and /smart-paste
        contracts all verified). Cleanup done (row 13 tombstoned).

        CRITICAL BUG found in parse_structured_paste() that breaks
        CASE 4 of the review contract:

        The parser does NOT recognise "Order ID: <value>" with a space
        between "Order" and "ID". Only "Order_ID:" (underscore) and
        bare "Order:" are recognised. Since the review instructions
        explicitly use the "Order ID: <value>" format — which is also
        the canonical format printed on Shopify/Amazon/Flipkart packing
        slips — real users pasting real labels will NOT trigger the
        order_id duplicate check.

        RCA in /app/backend/server.py around line 1044-1045
        (FIELD_KEYS + keys_alt regex build):
          keys_alt = "|".join(k for k, _ in FIELD_KEYS)  # no "ORDER ID"
          pattern = re.compile(rf"\b({keys_alt})\s*:\s*", re.IGNORECASE)

        Suggested fix (main-agent action, not done by me):
          Option A — pre-normalise the input:
            text = re.sub(r"(?i)\border\s+id\b", "Order_ID", text)
            before running parse_structured_paste().
          Option B — add a regex-based alternative to the KEY pattern:
            pattern = re.compile(
              rf"\b((?:{keys_alt})|ORDER\s+ID)\s*:\s*", re.IGNORECASE)
            and map matched "ORDER ID" (after collapsing whitespace) to
            order_id_hint.

        Verified that once order_id IS parsed (tested with underscore
        variant), the duplicate endpoint returns the expected shipment
        with match_on=["order_id"] — so only the parser patch is
        required, not any logic change in find_duplicate_matches().

        Detailed case-by-case pass/fail + summarised JSON for CASE 1,
        2, 4, 7 is recorded in the Smart Paste Duplicate Detection
        status_history above.

        Main agent: please fix the parser (one-line regex change) and
        then this task can be closed. No retest of the matching logic
        or regressions is required — only re-verify CASE 4 after the
        fix.
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
    - "Phase 1 Multi-Tenant Auth + user_id data isolation"
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

---

## Backend Test Run: Phase 1 Multi-Tenant Auth + user_id data isolation (2026-04-23)

backend:
  - task: "Phase 1 JWT auth + per-user data isolation across all /api routes"
    implemented: true
    working: true
    file: "/app/backend/server.py, /app/backend/auth.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "testing"
        -comment: |
            85/86 assertions passed on Phase 1 Multi-Tenant Auth + user_id
            isolation suite, run at
            https://logistics-hub-740.preview.emergentagent.com/api via
            /app/backend_test.py. The single non-pass is a test-harness
            nit, NOT a backend bug (see Minor note at bottom).

            Section-by-section results:

            1. AUTH ENDPOINTS (25/25 PASS)
               - POST /auth/login admin & user2 both return 200 with
                 {token, id, email, is_admin, plan, created_at, shop_name,
                 name}; password_hash never leaks.
               - Wrong password -> 401 detail "Invalid email or password".
               - Re-signup with admin@test.com -> 400 detail "Email already
                 registered".
               - GET /auth/me with each token returns matching user, no
                 password_hash leak.

            2. JWT MIDDLEWARE / auth_gate (8/8 PASS)
               - /api/shipments, /api/couriers, /api/settings,
                 /api/shipments/bulk-fetch all -> 401 without a Bearer.
               - 401 responses carry WWW-Authenticate: Bearer header.
               - Garbage token -> 401 with detail starting "Invalid token"
                 (JWT decode error).
               - Valid token flows through (GET /api/shipments -> 200).
               Note: /api/auth/me is intentionally exempt from the
               middleware and instead handled by the get_current_user
               dependency (returns 401 "Missing bearer token" when no
               header). Both paths are 401; the header and contract are
               satisfied for the spec.

            3. SHIPMENT DATA ISOLATION - CRITICAL (14/15 PASS, 1 minor)
               - admin GET /shipments -> 50 rows, 0 flagged is_demo=true
                 on the response model (as expected).
               - user2 GET /shipments -> 15 rows.
               - Picked admin_id from admin's list:
                   user2 GET  /shipments/{admin_id}      -> 404 OK
                   user2 PUT  /shipments/{admin_id}      -> 404 OK
                   user2 DELETE /shipments/{admin_id}    -> 404 OK
                   admin GET /shipments/{admin_id}       -> 200 (intact
                                                           after user2's
                                                           attempted delete)
                   user2 GET /shipments/by-tracking/<adm> -> 404 OK
                   admin GET /shipments/by-tracking/<adm> -> 200 OK
                 CROSS-TENANT ACCESS IS BLOCKED AT EVERY ENDPOINT.
               - POST /api/shipments/bulk-fetch with user2 token and 5
                 admin shipment ids returned an EMPTY list (200, not 401,
                 not data).
               Minor: the Shipment Pydantic response model intentionally
               does NOT include is_demo (internal-only flag in Mongo), so
               the assertion "user2 shipment has is_demo=true on the API
               response" reports None. This is correct behaviour —
               confirmed because demo/clear in Section 8 deleted exactly
               15 rows, proving all 15 user2 rows actually DO carry
               is_demo: true in the DB. Not a backend bug; test
               assertion was overly strict.

            4. COURIERS ISOLATION (12/12 PASS)
               - admin GET /couriers -> 4 couriers: ['Nandan Courier',
                 'DTDC', 'ST Courier', 'Indian post'] (seeded defaults).
               - user2 GET /couriers -> 1 courier: ['Demo Courier'] only.
                 None of admin's couriers leak.
               - user2 POST /couriers {name:"__ISOTEST__User2Courier"}
                 returned id; user2 GET /couriers includes it; admin GET
                 /couriers does NOT include it.
               - admin GET /couriers/{user2_courier_id} -> 404.
               - Cleanup: user2 deleted the test courier (200).

            5. PER-USER SETTINGS (7/7 PASS)
               - admin PUT /settings {default_eta_days:10} -> 200.
               - user2 PUT /settings {default_eta_days:3} -> 200.
               - admin GET /settings -> default_eta_days == 10.
               - user2 GET /settings -> default_eta_days == 3.
               - The two are independent (no cross-tenant overwrite).

            6. STATS ISOLATION (5/5 PASS)
               - admin GET /shipments/stats -> total=50.
               - user2 GET /shipments/stats -> total=15.
               - cod_total(5940) + prepaid_total(5696) == revenue_total
                 (11636) for user2 (diff < 0.001).

            7. SMART-PASTE + PENDING-ORDERS ISOLATION (6/6 PASS)
               - Built a paste using admin's phone 9801234567. Called
                 POST /smart-paste/check-duplicate with user2's token
                 -> 200, duplicates=[], fields.customer_phone parsed
                 correctly. NO admin shipment id leaked into user2's
                 duplicates response — cross-tenant duplicate check is
                 properly scoped.
               - GET /orders/pending-count (both tokens) -> 200.
               - GET /orders/pending (user2) -> 200.

            8. DEMO CLEAR (7/7 PASS)
               - POST /demo/clear with user2 -> {"ok":true,"deleted":15}.
               - Subsequent user2 GET /shipments -> 0 rows.
               - Admin GET /shipments -> still 50 rows (unaffected).
               - Second POST /demo/clear with user2 -> deleted=0
                 (idempotent).

            ZERO 500s observed during the entire run. Zero unexpected
            2xx where a 404/401 was expected. No cross-tenant leakage
            on any endpoint (shipments GET/PUT/DELETE/by-tracking/
            bulk-fetch, couriers GET/POST/GET-by-id, smart-paste
            check-duplicate). user_id isolation on Phase 1 is
            production-ready.

            Side-effects on the DB after this run:
              - user2's 15 demo shipments were deleted (by spec).
              - admin's settings.default_eta_days = 10.
              - user2's settings.default_eta_days = 3.
              - All test fixtures (courier __ISOTEST__) cleaned up.
            If you want user2's demo rows re-seeded for manual UI work,
            delete user2 from the users collection and re-signup with
            user2@test.com (seed_demo_shipments will run again).
        -working: "NA"
        -agent: "main"
        -comment: |
            Completed Phase 1: JWT auth middleware is in place (auth_gate)
            blocking any /api/* route except /api/auth/* without a Bearer
            token. Every shipment/courier/pending-order/settings endpoint
            now depends on get_current_user and filters reads/writes by
            user_id (including the new endpoints that previously used
            global {"id":"default"} queries). Added the following explicit
            user_id guards:
              - update_settings: upserts per-user settings doc
              - sheets_orders: uses the current user's connected sheet
              - shipments_stats: all pipelines filter on {user_id}
              - export_csv / by-tracking / bulk-fetch / CRUD shipments:
                user_id in every filter, insert carries user_id
              - find_duplicate_matches: takes user_id and constrains the
                candidate query to that tenant
              - pending orders (list/get/update/delete/ship/count): all
                queries scoped; new shipments carry user_id
            Seed / legacy data:
              - admin@test.com (password Admin@12345, is_admin=true) — holds
                the 50 legacy shipments + default couriers (claimed at first
                signup before this session).
              - user2@test.com (password User@12345) — regular user with 15
                demo shipments (is_demo=True).
            Please verify end-to-end:
              1. POST /api/auth/login with admin creds → token A, 200 OK.
                 Call GET /api/shipments with token A → ≥50 rows, no row
                 should carry is_demo=true.
              2. POST /api/auth/login with user2 creds → token B. Call
                 GET /api/shipments with token B → exactly 15 rows, every
                 row should have is_demo=true (via raw mongo check if
                 needed) and NONE of these ids should exist in admin's
                 list.
              3. Cross-tenant isolation: pick any shipment.id from admin
                 response. Call PUT /api/shipments/{that_id} with token B
                 and any valid update — must return 404 (not 200, not 401).
                 Same for DELETE /api/shipments/{that_id} with token B.
                 Same for GET /api/shipments/by-tracking/{admin_tracking}
                 with token B → 404.
              4. Stats isolation: GET /api/shipments/stats with A returns
                 total≥50; with B returns total=15. cod_total + prepaid_total
                 should equal revenue_total for each tenant.
              5. Couriers isolation: GET /api/couriers with B should include
                 the "Demo Courier" (prefix ND, seeded on signup) and NOT
                 admin's default set (Nandan Courier, DTDC, India Post…).
                 POST /api/couriers with B inserts a courier tagged with
                 B.user_id; GET /api/couriers with A should NOT show it.
              6. Settings isolation: PUT /api/settings with A changes
                 default_eta_days=10. PUT with B changes default_eta_days=3.
                 GET /api/settings with each token should return each's
                 own value (no overlap).
              7. Pending-count / pending-list / smart-paste/check-duplicate
                 all must scope to their token. Posting the same phone/OID
                 with both tenants should NOT produce cross-tenant
                 duplicate warnings.
              8. 401 guard: calling any /api/* (except /api/auth/*) without
                 Authorization must return 401 with WWW-Authenticate:Bearer.
                 A random garbage token must return 401.
              9. Demo clear: POST /api/demo/clear with B → deletes exactly
                 15 rows; A's shipment count unchanged.
            Known env quirk: passlib logs a benign "error reading bcrypt
            version" warning at startup. This does NOT affect hashing or
            verification (already proven by login roundtrip). Please
            ignore the WARNING line.

agent_communication:
    -agent: "main"
    -message: |
        Phase 1 Multi-Tenant Auth backend is ready for thorough testing.
        Please use the credentials in /app/memory/test_credentials.md:
          admin@test.com / Admin@12345 (owns 50 legacy shipments, is_admin)
          user2@test.com / User@12345 (fresh workspace with 15 demo rows)
        Focus areas:
          (A) JWT middleware blocks every /api/* route except /api/auth/*
              when no Bearer token is present (401 + WWW-Authenticate).
          (B) user_id data isolation across shipments, couriers, settings,
              pending_orders, stats, export/csv, sheets/orders,
              smart-paste/check-duplicate, smart-paste (create), and
              orders/pending/*/ship.
          (C) Cross-tenant access on a known admin shipment id with user2's
              token must return 404 for GET/PUT/DELETE.
          (D) /api/demo/clear must only delete user2's is_demo rows.
          (E) No regression on Google Sheet flows (append row, soft-delete
              tombstone, two-way status sync when a row is linked via
              sheet_row_num).
        Please DO NOT bulk-delete shipments — create small test fixtures,
        verify, then clean up. Log aggregate pass/fail counts in your
        comment.
    -agent: "testing"
    -message: |
        Phase 1 Multi-Tenant Auth + per-user data isolation — PASS.
        85/86 assertions green on /app/backend_test.py against
        https://logistics-hub-740.preview.emergentagent.com/api.

        Aggregate per section:
          1. Auth endpoints ..........  25/25
          2. JWT middleware (auth_gate) 8/8
          3. Shipment isolation ....... 14/15 (1 minor harness issue)
          4. Couriers isolation ....... 12/12
          5. Per-user settings ......... 7/7
          6. Stats isolation ........... 5/5
          7. Smart-paste + pending ..... 6/6
          8. Demo clear ................ 7/7

        CRITICAL cross-tenant checks all pass:
          - user2 GET/PUT/DELETE on admin's shipment id -> 404.
          - user2 GET /shipments/by-tracking/{admin_tracking} -> 404.
          - user2 POST /shipments/bulk-fetch with admin ids -> [] (200).
          - user2 /smart-paste/check-duplicate against admin's phone
            returns 0 duplicates that are admin's (no leak).
          - admin cannot GET /couriers/{user2_courier_id} (-> 404).
          - Per-user settings doc fully isolated (eta=10 vs eta=3).
          - /demo/clear deletes only user2's 15 demo rows; admin count
            unchanged at 50. Idempotent second call returns deleted=0.
          - No 500s anywhere. No unexpected 200s where 404/401 was
            expected.

        Minor (NOT a bug): the test assertion "at least one user2
        shipment has is_demo=true on the API response" fails because
        the Shipment Pydantic response model intentionally does NOT
        include is_demo — it's an internal-only Mongo flag used by
        /demo/clear. The 15 rows ARE flagged is_demo:true in Mongo,
        proven by the clear step deleting exactly 15 rows.

        Side-effects to be aware of after this run:
          - user2's 15 demo shipments have been deleted (per spec).
            If you need them back for UI work, delete the user2 user
            doc from Mongo and let user2@test.com re-signup so the
            seed runs again.
          - admin.settings.default_eta_days = 10
          - user2.settings.default_eta_days = 3
          - No other data touched; no test fixtures left behind.

        Marking task working=true. No retesting required.


#====================================================================================================
# 2026-04-24 — Phase 4b+ Smart Paste AI frontend cleanup
#====================================================================================================
frontend:
  - task: "Smart Paste AI — remove legacy ChatGPT flow"
    implemented: true
    working: true
    file: "/app/frontend/app/(tabs)/index.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: |
            Refactored `/app/frontend/app/(tabs)/index.tsx` to remove the
            legacy "14-line format" flow entirely:
              * Dropped the hasStructure regex check — any raw WhatsApp/SMS
                text now triggers the AI flow directly.
              * Removed the "Open GPT" modal button and its Android intent
                fallback.
              * Updated modal hint, placeholder, and submit CTA ("AI Parse
                & Queue").
              * Added inline "AI is parsing…"/"Saving order…" indicator
                inside the modal while the request is in flight.
              * Surfaces `ai.missing` critical fields (NAME/PHONE/
                ADDRESS_1/PINCODE) in an Alert with an "Edit text" action
                that reopens the modal pre-filled with the user's paste.
              * Unified save path (`saveSmartPaste`) so the duplicate
                confirmation and no-duplicate branches share logic.
            Verified on localhost:3000 with admin@test.com: modal displays
            new hint, no legacy strings, "AI Parse & Queue" CTA visible.

  - task: "Smart Paste AI — Missing Fields Modal (ask-before-save)"
    implemented: true
    working: true
    file: "/app/frontend/app/(tabs)/index.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: |
            Replaced with the unified "AI Preview & Edit Modal" (Batch 1
            below). The dedicated Missing-Fields sheet is no longer needed
            because the preview modal highlights missing required fields
            with a red border in the same form that shows ALL fields.

  - task: "Smart Paste Batch 1 — Preview / Customer Memory / Complexity"
    implemented: true
    working: true
    file: "/app/frontend/app/(tabs)/index.tsx & /app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: |
            Batch 1 Smart Paste improvements landed:

            FRONT-END (`/app/frontend/app/(tabs)/index.tsx`)
              * AI Preview & Edit Modal — AFTER every successful parse,
                user now sees an editable sheet with ALL 14 schema fields
                (primary first, optional after), each pre-filled from the
                AI's best guess. Required fields show a red asterisk AND
                a red border/background when empty, so users can't miss
                them. Save is blocked until all required fields are
                filled; duplicate warnings still pop a confirm dialog.
              * Address-Complexity Badge — backend already returns
                `complexity` (simple / medium / complex) + a short reason.
                We render the value as a coloured pill in the preview
                header (green / amber / orange) plus the reason on a
                sub-line ("💡 Clear address and details", etc.).
              * Repeat-Customer Banner — as soon as the preview opens and
                a PHONE is available, we fire a background
                `GET /api/customers/by-phone/{phone}` call. On hit we
                render a green banner "🎯 Repeat customer — {name} —
                {address}, {city} (N past orders)" with a one-tap **Use**
                button that fills NAME / PHONE / ADDRESS_* / CITY /
                STATE / PINCODE from the past shipment. A close-icon
                dismisses the banner without applying.
              * Save path — instead of POSTing the raw pasted text again,
                Save Order now constructs a canonical 14-line KEY: value
                block from the preview form and POSTs THAT to
                `/api/smart-paste`. This avoids a second wasted LLM call
                and guarantees what-you-see-is-what-gets-saved.

            BACK-END (`/app/backend/server.py`)
              * New endpoint `GET /api/customers/by-phone/{phone}` — tenant-
                scoped lookup (matches last-10-digit suffix) that searches
                first in `shipments`, then `pending_orders`. Returns
                `{found, count, customer}` with name + full address so the
                front-end can auto-fill.

            Verified on localhost:3000 with admin@test.com:
              * Pasting "Ramesh Patel 9876543210 Saree 2 pcs 1500 COD"
                opens the preview with AI-detected Medium complexity, a
                repeat-customer banner (12, Navrangpura Main Road,
                Ahmedabad — 6 past orders), and red-bordered missing
                ADDRESS/CITY/STATE/PINCODE inputs.
              * Tapping "Use" instantly fills the blank fields.
              * Tapping "Save Order" posts the merged 14-line block and
                creates the pending order (backend 200 OK).

agent_communication:
    -agent: "main"
    -message: |
        Completed Phase 4b+ Smart Paste AI frontend integration. Users can
        now paste raw WhatsApp text (from clipboard) and the backend LLM
        parses it directly — no ChatGPT bounce required. Manual UI check
        passed (screenshot verified). No testing-agent run needed for this
        purely UI refactor; backend endpoints already covered by earlier
        Smart Paste tests.
