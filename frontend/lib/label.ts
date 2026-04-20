import type { Shipment, SenderAddress, Courier } from "./api";
import { barcodeSvg } from "./barcode";

export type Brand = { name?: string; logo_base64?: string };

export type LabelOptions = {
  perPage: 1 | 2 | 4 | "thermal" | "barcode";
  showSenderContact: boolean;
  brand?: Brand;
  preferLogo?: boolean; // true = show logo when available; false = always show name
  logoShape?: "square" | "wide"; // influences rendered size
  couriers?: Courier[]; // used to pull per-courier customer_id onto the label
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
  // Use short label: "PAID" instead of "PREPAID", "COD" stays
  const payPillText = isCod
    ? `COD ₹${Number(amt).toFixed(0)}`
    : `PAID${amt ? ` ₹${Number(amt).toFixed(0)}` : ""}`;
  const payPillClass = isCod ? "pay-pill cod" : "pay-pill prepaid";

  // Look up courier's printable customer_id (if provided in options)
  const courier = (opts.couriers || []).find(
    (c) => c.id === s.courier_id || c.name === s.courier_name
  );
  const custId = (courier?.customer_id || "").trim();
  const courierLine = s.courier_name
    ? `<div class="courier-sub">via ${escape(s.courier_name)}${custId ? ` · Cust ID: ${escape(custId)}` : ""}</div>`
    : (custId ? `<div class="courier-sub">Cust ID: ${escape(custId)}</div>` : "");

  const dispatchDate = formatDispatchDate(s.created_at);

  const itemsText =
    s.items && s.items.length
      ? s.items.join(", ")
      : (s.item_description || "-");

  const bcSvg = renderBarcodeSvg(s.tracking_id);

  // Compact sender "FROM" line (goes into footer area, above barcode)
  const senderFooterLine = (() => {
    const addr = [
      sender.address_line1,
      sender.address_line2,
      [sender.city, sender.state, sender.pincode].filter(Boolean).join(", "),
    ].filter(Boolean).join(", ");
    const parts = [
      `<b>${escape(sender.name || "Sender")}</b>`,
      escape(addr),
    ];
    if (opts.showSenderContact && sender.phone) {
      parts.push(`📞 <b>${escape(sender.phone)}</b>`);
    }
    return parts.filter(Boolean).join(" · ");
  })();

  return `
  <div class="label">
    <!-- TOP (fixed height) -->
    <div class="hdr">
      <div class="brand-wrap">${brandHeader}</div>
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
        ${[s.address_line1, s.address_line2,
           [s.city, s.state, s.pincode].filter(Boolean).join(", ")]
           .filter(Boolean)
           .map((l) => `<div class="blk-line">${escape(l)}</div>`).join("")}
        ${s.customer_phone ? `<div class="blk-contact">📞 <b>${escape(s.customer_phone)}</b></div>` : ""}
      </div>

      <div class="meta-row">
        ${dispatchDate ? `<span><b class="lbl">Dispatch:</b> ${escape(dispatchDate)}</span>` : ""}
        ${s.order_id ? `<span><b class="lbl">Order:</b> ${escape(s.order_id)}</span>` : ""}
        ${s.weight ? `<span><b class="lbl">Wt:</b> ${escape(s.weight)}</span>` : ""}
        <span><b class="lbl">Item:</b> ${escape(itemsText)}</span>
      </div>
    </div>

    <!-- BOTTOM (fixed height, barcode never cut) -->
    <div class="footer">
      <div class="sender-line">From: ${senderFooterLine}</div>
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

  /* ---- TOP (fixed) ---- */
  .hdr { display: flex; justify-content: space-between; align-items: center;
    border-bottom: 2px solid #0A0A0A; padding-bottom: 3mm; gap: 4mm;
    min-height: 20mm; }
  .brand-wrap { display: flex; align-items: center; gap: 3mm; flex: 1; min-width: 0; }
  .brand-logo-square { max-width: 22mm; max-height: 22mm; object-fit: contain; }
  .brand-logo-wide   { max-width: 100%; width: 100%; max-height: 22mm; height: auto; object-fit: contain; display: block; }
  .brand-name { font-size: 17pt; font-weight: 900; letter-spacing: -0.2px;
    line-height: 1.1; word-break: break-word; max-width: 100%; }
  .pay-wrap { text-align: right; flex-shrink: 0; }
  .pay-pill { display: inline-block; padding: 2.5mm 4.5mm; font-weight: 900;
    font-size: 14pt; border-radius: 999px; line-height: 1; white-space: nowrap;
    letter-spacing: 0.5px; }
  .pay-pill.prepaid { background: #0A0A0A; color: #fff; }
  .pay-pill.cod { background: #FF5A00; color: #fff; }
  .courier-sub { margin-top: 1.5mm; font-size: 9pt; color: #4B5563; font-weight: 600; }

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
