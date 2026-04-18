# Courier Label Manager — PRD

## Problem (Gujarati)
નાના બિઝનેસમેન જે લોકલ કુરિયર (દા.ત. નંદન કુરિયર) વાપરે છે તેમને API મળતી નથી અને લેબલ પ્રિન્ટ કાઢવામાં તકલીફ પડે છે. આ એપ શિપમેન્ટ મેનેજ કરે, tracking ID auto-generate કરે, beautiful labels print કરે (A4 1/2/4 per page or thermal 4x6), અને WhatsApp થી customer ને જણાવે.

## MVP Features (Built)
- Dashboard with stats (Total / Pending / Delivered / COD total) + recent shipments + quick actions
- Add Shipment form with 3 tracking input modes: Auto series / Manual / Camera barcode scan
- Courier partners with series prefix + next number (auto-increment)
- Shipments list: search, status filters (All/Pending/Delivered/Cancelled), mark delivered, WhatsApp share (wa.me), print, delete
- Label preview + print via expo-print: A4 1/2/4 per page, Thermal 4x6, copies control, show/hide sender contact toggle, JsBarcode (CODE128) embedded via HTML
- Settings: sender address (saved once), WhatsApp template with {customer_name}/{courier}/{tracking_id}/{eta_days}, courier CRUD
- CSV export of all shipments

## Tech Stack
- Frontend: Expo (React Native) + expo-router (bottom tabs), expo-camera (barcode scan), expo-print, expo-sharing
- Backend: FastAPI + Motor + MongoDB
- Design: Swiss/High-Contrast Safety Orange (#FF5A00) with Jet Black accents, Courier mono for tracking IDs

## Data Models
- Shipment: id, tracking_id, courier_id/name, customer details, address, payment_mode (COD/Prepaid), cod_amount, weight, item_description, status, created_at, delivered_at
- Courier: id, name, series_prefix, next_number, number_padding
- Settings: sender address (+ show_contact), whatsapp_template, default_eta_days

## Seeded Defaults
- Couriers: Nandan Courier (ND), DTDC (DT), ST Courier (ST), Trackon (TR), Other

## Key Routes
- /api/shipments CRUD + /stats + /export/csv
- /api/couriers CRUD + /next-tracking + /consume-tracking
- /api/settings GET/PUT
