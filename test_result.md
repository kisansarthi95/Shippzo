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
