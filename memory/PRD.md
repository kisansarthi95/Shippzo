# Courier Label Manager — PRD (Iteration 2)

## Problem
નાના બિઝનેસમેન જે લોકલ કુરિયર (નંદન કુરિયર, ઈન્ડિયા પોસ્ટ etc.) વાપરે છે — API નથી હોતી, label print કાઢવું મુશ્કેલ. એપ: shipment manage + tracking series auto + label print + Google Sheet થી orders import.

## Iteration 2 Additions
- **Google Sheet integration** (read-only, just paste public link) — auto-detect columns + manual re-map UI, picks from Sheet to auto-fill Add form
- **Order details** — Order ID + Items (multi-line) per shipment
- **Amount field always visible** — both Prepaid & COD
- **Copy All** button — copies templated tracking message to clipboard
- **Courier full CRUD** — contact phone/email/website/tracking URL template/notes + WhatsApp share button per courier
- **Camera scan** — now uses correct SDK-54-compatible expo-camera@17.0.10 with proper permission flow

## Known Limitations
- **Sheet is read-only** — delivery status update back to Sheet requires OAuth / Apps Script (user explicitly chose to skip)
- User must share Sheet as "Anyone with the link → Viewer" for public CSV read to work

## Tech Stack
- Frontend: Expo Router (SDK 54) + expo-camera/print/sharing/clipboard
- Backend: FastAPI + Motor + httpx for Google Sheet CSV fetch
- Design: Swiss/High-Contrast (Safety Orange #FF5A00 + Jet Black)

## API Routes (new)
- GET /api/couriers/{id}
- POST /api/sheets/preview {url} → headers + sample_rows + auto_mapping
- GET /api/sheets/orders → parsed orders + already_shipped detection + headers_changed flag
- Enhanced: /api/shipments now accepts order_id, items[], amount
- /api/shipments/stats now includes revenue_total
- /api/settings includes sheet{url,sheet_id,gid,headers,column_mapping} + copy_template

## Seeded couriers
Nandan Courier (ND), DTDC (DT), India Post (IP), ST Courier (ST), Trackon (TR)
