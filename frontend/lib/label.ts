import type { Shipment, SenderAddress, Courier, LabelFields } from "./api";
import { barcodeSvg } from "./barcode";

export type Brand = { name?: string; logo_base64?: string };

export type LabelOptions = {
  perPage: 1 | 2 | 4 | "thermal" | "barcode";
  showSenderContact: boolean;
  brand?: Brand;
  preferLogo?: boolean; // true = show logo when available; false = always show name
  logoShape?: "square" | "wide"; // influences rendered size
  couriers?: Courier[]; // used to pull per-courier customer_id onto the label
  labelFields?: Partial<LabelFields>; // user-chosen field visibility toggles
  shipmentTagline?: string; // fallback tagline when shipment.shipment_notes is empty
};

const escape = (s: string) =>
  (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

// Generate a CODE128 barcode as SVG string using pure JS encoder.
// No native/RN/zlib deps — works in Hermes, browser, Node.
function renderBarcodeSvg(value: string): string {
  return barcodeSvg(value || "NA");
}

// Format ISO timestamp to "15 Jan 2026" (locale-safe, no leading zero fuss).
function formatDispatchDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
}

function senderBlock(sender: SenderAddress, show: boolean) {
  const lines = [
    sender.address_line1,
    sender.address_line2,
    [sender.city, sender.state, sender.pincode].filter(Boolean).join(", "),
  ].filter(Boolean);
  const contact = show && sender.phone ? `📞 ${escape(sender.phone)}` : "";
  return `
    <div class="blk sender">
      <div class="blk-title">FROM</div>
      <div class="blk-name">${escape(sender.name || "Sender")}</div>
      ${lines
        .map((l) => `<div class="blk-line">${escape(l)}</div>`)
        .join("")}
      ${contact ? `<div class="blk-contact">${contact}</div>` : ""}
    </div>
  `;
}

function receiverBlock(s: Shipment) {
  const lines = [
    s.address_line1,
    s.address_line2,
    [s.city, s.state, s.pincode].filter(Boolean).join(", "),
  ].filter(Boolean);
  return `
    <div class="blk receiver">
      <div class="blk-title">TO</div>
      <div class="blk-name">${escape(s.customer_name)}</div>
      ${lines
        .map((l) => `<div class="blk-line">${escape(l)}</div>`)
        .join("")}
      ${s.customer_phone ? `<div class="blk-contact">📞 ${escape(s.customer_phone)}</div>` : ""}
    </div>
  `;
}

function singleLabel(s: Shipment, sender: SenderAddress, opts: LabelOptions) {
  const amt = s.amount || s.cod_amount || 0;
  const brandName = opts.brand?.name?.trim() || sender.name || "Courier Label Manager";
  const logo = opts.brand?.logo_base64?.trim();
  const logoImg = logo
    ? (logo.startsWith("data:") ? logo : `data:image/png;base64,${logo}`)
    : "";
  // Show logo if preferLogo!=false AND logo available; else brand name
  const usePreferLogo = opts.preferLogo !== false;
  const logoClass = opts.logoShape === "wide" ? "brand-logo-wide" : "brand-logo-square";
  const brandHeader = logoImg && usePreferLogo
    ? `<img class="${logoClass}" src="${logoImg}" />`
    : `<div class="brand-name">${escape(brandName)}</div>`;

  const isCod = s.payment_mode === "COD";
  const tokenAmtPreview = Number((s as any).token_amount || 0);
  const amtPreview = Number(amt) || 0;
  const collectAmt = isCod ? Math.max(0, amtPreview - tokenAmtPreview) : 0;
  // Pill shows ONLY what the delivery boy should collect.
  // If Token was paid upfront (e.g., 300 total, 50 token) → COD ₹250.
  // Token info (Total + Advance) appears in the small footer token-box.
  const payPillText = isCod
    ? `COD ₹${collectAmt.toFixed(0)}`
    : `PAID${amtPreview ? ` ₹${amtPreview.toFixed(0)}` : ""}`;
  const payPillClass = isCod ? "pay-pill cod" : "pay-pill prepaid";

  // ---- Label field visibility toggles (Phase A) ----
  const lf = {
    oid: true, dispatch_date: true, weight: true, item: true, phone: true,
    customer_id: true, token_info: false, box_dimensions: false, shipment_notes: false,
    ...(opts.labelFields || {}),
  };

  // Look up courier's printable customer_id (if provided in options)
  const courier = (opts.couriers || []).find(
    (c) => c.id === s.courier_id || c.name === s.courier_name
  );
  const custId = (courier?.customer_id || "").trim();
  const dispatchDate = formatDispatchDate(s.created_at);
  const showCustId = lf.customer_id && custId;
  const courierLine = s.courier_name
    ? `<div class="courier-sub">via ${escape(s.courier_name)}</div>${
        showCustId ? `<div class="courier-sub2">Cust ID: ${escape(custId)}</div>` : ""
      }`
    : (showCustId ? `<div class="courier-sub2">Cust ID: ${escape(custId)}</div>` : "");

  const itemsText =
    s.items && s.items.length
      ? s.items.join(", ")
      : (s.item_description || "-");

  const bcSvg = renderBarcodeSvg(s.tracking_id);

  // Token / advance info (only if toggled on AND COD with real advance).
  // For PAID/Prepaid orders we never show this box (no confusion possible).
  const tokenAmt = Number((s as any).token_amount || 0);
  const amtNum = Number(s.amount || 0);
  const tokenFooterBlock = (lf.token_info && tokenAmt > 0 && s.payment_mode === "COD")
    ? `<div class="token-box">
         <span class="tk-label">💰 Paid Advance:</span>
         <span class="tk-val">₹${tokenAmt.toFixed(0)}</span>
         <span class="tk-sep">·</span>
         <span class="tk-label">Order Total:</span>
         <span class="tk-val">₹${amtNum.toFixed(0)}</span>
       </div>`
    : "";

  // Compact sender "FROM" block (footer area, above barcode).
  // Structure:
  //   Line 1: From: [BrandName]            (bigger, bolder)
  //   Line 2: [tagline]                    (italic, muted, below name) — if set
  //   Line 3: address · 📞 phone           (small, muted)
  const senderFooterBlock = (() => {
    const addr = [
      sender.address_line1,
      sender.address_line2,
      [sender.city, sender.state, sender.pincode].filter(Boolean).join(", "),
    ].filter(Boolean).join(", ");
    const tail: string[] = [];
    if (addr) tail.push(escape(addr));
    if (opts.showSenderContact && sender.phone) {
      tail.push(`📞 <b>${escape(sender.phone)}</b>`);
    }
    const tagline = (opts.shipmentTagline || "").trim();
    return `
      <div class="sender-name-line">From: <b class="sender-brand">${escape(sender.name || "Sender")}</b></div>
      ${tagline ? `<div class="sender-tagline">${escape(tagline)}</div>` : ""}
      ${tail.length ? `<div class="sender-addr">${tail.join(" · ")}</div>` : ""}
    `;
  })();

  return `
  <div class="label">
    <!-- TOP (fixed height) -->
    <div class="hdr">
      <div class="brand-wrap">
        ${brandHeader}
        <div class="brand-meta">
          ${(lf.dispatch_date && dispatchDate) ? `<span><b class="lbl">DD:</b> <b>${escape(dispatchDate)}</b></span>` : ""}
          ${(lf.oid && s.order_id) ? `<span><b class="lbl">OID:</b> <b class="meta-val">${escape(s.order_id)}</b></span>` : ""}
        </div>
      </div>
      <div class="pay-wrap">
        <div class="${payPillClass}">${payPillText}</div>
        ${courierLine}
      </div>
    </div>

    <!-- MIDDLE (flex – can shrink/grow) -->
    <div class="mid">
      <div class="recv-block">
        <div class="blk-title">DELIVER TO</div>
        <div class="blk-name">${escape(s.customer_name)}</div>
        ${(() => {
          const addr = [s.address_line1, s.address_line2].filter(Boolean).join(", ");
          const cityLine = [s.city, s.state, s.pincode].filter(Boolean).join(", ");
          return [addr, cityLine].filter(Boolean)
            .map((l) => `<div class="blk-line">${escape(l)}</div>`).join("");
        })()}
        ${(lf.phone && s.customer_phone) ? `<div class="blk-contact">📞 <b>${escape(s.customer_phone)}</b></div>` : ""}
        ${(() => {
          // Per-order shipment notes — below customer phone.
          // Only shows when the user explicitly filled the "Shipment Notes" field
          // in the Add Shipment form. Brand tagline is handled separately in the
          // sender footer block (not here).
          if (!lf.shipment_notes) return "";
          const perOrder = ((s as any).shipment_notes || "").trim();
          return perOrder ? `<div class="blk-notes">${escape(perOrder)}</div>` : "";
        })()}
      </div>

      <div class="meta-row">
        ${(lf.weight && s.weight) ? `<span><b class="lbl">Wt:</b> ${escape(s.weight)}</span>` : ""}
        ${(lf.box_dimensions && (s as any).box_dimensions) ? `<span><b class="lbl">Box:</b> ${escape((s as any).box_dimensions)}</span>` : ""}
        ${lf.item ? `<span><b class="lbl">Item:</b> ${escape(itemsText)}</span>` : ""}
      </div>
      ${tokenFooterBlock}
    </div>

    <!-- BOTTOM (fixed height, barcode never cut) -->
    <div class="footer">
      <div class="sender-block">${senderFooterBlock}</div>
      <div class="track-wrap">
        <div class="track-id">${escape(s.tracking_id)}</div>
        <div class="barcode-wrap">${bcSvg}</div>
      </div>
    </div>
  </div>
  `;
}

export function buildLabelHtml(
  shipments: Shipment[],
  sender: SenderAddress,
  opts: LabelOptions
): string {
  const perPage = opts.perPage;

  // Barcode-only small sticker layout (50x25mm each)
  if (perPage === "barcode") {
    const pages = shipments
      .map((s) => {
        const bcSvg = renderBarcodeSvg(s.tracking_id);
        return `
      <div class="sheet-sticker">
        <div class="sticker">
          <div class="sticker-track">${escape(s.tracking_id)}</div>
          <div class="sticker-bc">${bcSvg}</div>
          <div class="sticker-sub">${escape(s.courier_name || "")}${s.order_id ? " · #" + escape(s.order_id) : ""}</div>
        </div>
      </div>`;
      })
      .join("");
    return `<!doctype html>
<html><head><meta charset="utf-8" />
<title>Barcode Stickers</title>
<style>
  @page { size: 50mm 25mm; margin: 1mm; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, Arial, sans-serif; margin: 0; }
  .sheet-sticker { page-break-after: always; }
  .sticker { width: 48mm; height: 23mm; padding: 1mm; text-align: center;
    display: flex; flex-direction: column; justify-content: center; align-items: center;
    border: 1px solid #0A0A0A; border-radius: 1mm; }
  .sticker-track { font-family: 'Courier New', monospace; font-weight: 800;
    font-size: 9pt; letter-spacing: 1.2px; color: #0A0A0A; }
  .sticker-bc { margin-top: 1mm; }
  .sticker-bc svg { width: 42mm !important; height: 10mm !important; }
  .sticker-sub { font-size: 6pt; color: #4B5563; margin-top: 1mm; }
</style>
</head><body>${pages}</body></html>`;
  }

  const gridCss =
    perPage === 4
      ? `.sheet { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; gap: 2mm; width: 100%; }
         .label { height: 140mm; }`
      : perPage === 2
      ? `.sheet { display: grid; grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; gap: 2mm; width: 100%; }
         .label { height: 140mm; }`
      : perPage === 1
      ? `.sheet { display: block; page-break-after: always; width: 100%; }
         .label { height: 260mm; }`
      : `.sheet { display: block; page-break-after: always; width: 100mm; }
         .label { height: 145mm; width: 100mm; }`;

  const pageCss =
    perPage === "thermal"
      ? `@page { size: 100mm 150mm; margin: 2mm; }`
      : `@page { size: A4; margin: 5mm; }`;

  const chunkSize = perPage === "thermal" ? 1 : (perPage as number);
  const pages: string[] = [];
  const sheets = shipments;
  for (let i = 0; i < sheets.length; i += chunkSize) {
    const page = sheets
      .slice(i, i + chunkSize)
      .map((s) => singleLabel(s, sender, opts))
      .join("");
    pages.push(`<div class="sheet">${page}</div>`);
  }
  const allPages = pages.length ? pages.join("") : `<div class="sheet"></div>`;

  return `
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Shipping Labels</title>
<style>
  ${pageCss}
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
    margin: 0; color: #0A0A0A; }
  ${gridCss}
  /* Label is a 3-row grid: fixed header, flexible middle, fixed footer */
  .label { border: 2px solid #0A0A0A; padding: 5mm;
    break-inside: avoid; background: #fff; border-radius: 3mm;
    display: grid; grid-template-rows: auto 1fr auto; gap: 3mm; overflow: hidden;
    width: 100%; max-width: 100%; box-sizing: border-box; }

  /* ---- TOP (fixed, identical regardless of perPage) ---- */
  .hdr { display: flex; justify-content: space-between; align-items: flex-start;
    border-bottom: 2px solid #0A0A0A; padding-bottom: 3mm; gap: 4mm; }
  .brand-wrap { display: flex; flex-direction: column; align-items: flex-start; gap: 1.5mm;
    flex: 1 1 auto; min-width: 0; overflow: hidden; }
  .brand-logo-square { width: 14mm; height: 14mm; max-width: 14mm; max-height: 14mm;
    object-fit: contain; display: block; flex-shrink: 0; }
  .brand-logo-wide   { width: 100%; max-width: 100%; height: auto; max-height: 12mm;
    object-fit: contain; display: block; flex-shrink: 0; }
  .brand-meta { display: flex; flex-direction: row; justify-content: flex-start;
    align-items: center; gap: 3mm 4mm; font-size: 8.5pt; color: #1F2937;
    flex-wrap: wrap; width: 100%; }
  .brand-meta span { white-space: nowrap; }
  .brand-meta .meta-val { font-weight: 900; color: #0A0A0A; font-size: 9.5pt; }
  .brand-name { font-size: 17pt; font-weight: 900; letter-spacing: -0.2px;
    line-height: 1.1; word-break: break-word; max-width: 100%; }
  .pay-wrap { text-align: right; flex: 0 0 auto; min-width: 30mm; }
  .pay-pill { display: inline-block; padding: 2.5mm 4mm; font-weight: 900;
    font-size: 12pt; border-radius: 999px; line-height: 1; white-space: nowrap;
    letter-spacing: 0.5px; }
  .pay-pill.prepaid { background: #0A0A0A; color: #fff; }
  .pay-pill.cod { background: #FF5A00; color: #fff; }
  .courier-sub { margin-top: 1.5mm; font-size: 8.5pt; color: #4B5563; font-weight: 600;
    white-space: nowrap; }
  .courier-sub2 { margin-top: 0.5mm; font-size: 8pt; color: #4B5563; font-weight: 600;
    white-space: nowrap; }
  /* Token / advance info — always at BOTTOM of mid section, small + muted */
  .token-box { margin-top: 2mm; padding: 1.5mm 2mm; background: #F8FAFC;
    border: 1px dashed #94A3B8; border-radius: 2mm; font-size: 8pt; color: #334155;
    display: flex; gap: 2mm; align-items: center; flex-wrap: wrap; }
  .token-box .tk-label { font-weight: 600; color: #475569; }
  .token-box .tk-val { font-weight: 800; color: #0F172A; }
  .token-box .tk-val.strong { color: #B45309; font-size: 9pt; }
  .token-box .tk-sep { color: #CBD5E1; }
  .blk-notes { margin-top: 2mm; font-size: 9.5pt; color: #334155; font-style: italic;
    text-align: center; font-weight: 600; padding: 1mm 0; border-top: 1px dotted #CBD5E1; }

  /* ---- MIDDLE (flex) ---- */
  .mid { display: flex; flex-direction: column; gap: 3mm; overflow: hidden; min-height: 0; }
  .recv-block { border: 1.5px solid #0A0A0A; border-radius: 3mm;
    padding: 3mm 3.5mm; background: #F4F5F7; flex: 1; overflow: hidden; }
  .blk-title { font-size: 8pt; font-weight: 800; color: #4B5563;
    letter-spacing: 2px; margin-bottom: 2mm; }
  .blk-name { font-size: 14pt; font-weight: 900; margin-bottom: 2mm; line-height: 1.15; }
  .blk-line { font-size: 11pt; line-height: 1.35; color: #1F2937; }
  .blk-contact { font-size: 11pt; margin-top: 2mm; font-weight: 700; }
  .meta-row { display: flex; flex-wrap: wrap; gap: 3mm 5mm; font-size: 9.5pt;
    padding-top: 1mm; }
  .meta-row .lbl { color: #6B7280; font-weight: 700; }

  /* ---- BOTTOM (fixed – barcode never cut) ---- */
  .footer { border-top: 2px solid #0A0A0A; padding-top: 3mm;
    display: flex; flex-direction: column; gap: 2.5mm; }
  .sender-line { font-size: 8pt; color: #4B5563; line-height: 1.3;
    word-break: break-word; }
  .sender-line b { color: #1F2937; }
  /* --- Multi-line sender block (new) --- */
  .sender-block { margin-bottom: 1mm; }
  .sender-name-line { font-size: 9pt; color: #4B5563; line-height: 1.2; }
  .sender-name-line .sender-brand { font-size: 12.5pt; color: #0A0A0A; font-weight: 900;
    letter-spacing: 0.2px; }
  .sender-tagline { font-size: 9pt; color: #334155; font-style: italic; font-weight: 600;
    margin-top: 0.8mm; letter-spacing: 0.2px; }
  .sender-addr { font-size: 7.8pt; color: #6B7280; line-height: 1.3; margin-top: 1mm;
    word-break: break-word; }
  .sender-addr b { color: #1F2937; }
  .track-wrap { text-align: center; }
  .track-id { font-family: 'Courier New', monospace; font-size: 14pt; font-weight: 900;
    letter-spacing: 2.5px; margin-bottom: 1.5mm; }
  .barcode-wrap { display: flex; justify-content: center; align-items: center;
    height: 16mm; }
  .barcode-wrap svg { width: 92%; height: 16mm; max-height: 16mm; }
</style>
</head>
<body>
  ${allPages}
</body>
</html>`;
}
