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
