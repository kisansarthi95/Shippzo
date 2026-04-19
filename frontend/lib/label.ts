import type { Shipment, SenderAddress } from "./api";
import bwipjs from "bwip-js";

export type Brand = { name?: string; logo_base64?: string };

export type LabelOptions = {
  perPage: 1 | 2 | 4 | "thermal" | "barcode";
  showSenderContact: boolean;
  brand?: Brand;
};

const escape = (s: string) =>
  (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

// Generate a CODE128 barcode as SVG string using bwip-js. No CDN / runtime JS needed.
function barcodeSvg(value: string, opts?: { height?: number; scale?: number; width?: number }): string {
  try {
    const svg = bwipjs.toSVG({
      bcid: "code128",
      text: value || "NA",
      scale: opts?.scale ?? 2,
      height: opts?.height ?? 14, // mm
      includetext: false,
      backgroundcolor: "FFFFFF",
      paddingwidth: 0,
      paddingheight: 0,
    });
    return svg;
  } catch (e) {
    return `<div style="font-family:monospace;font-weight:800;letter-spacing:2px;">${escape(value)}</div>`;
  }
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

  const isCod = s.payment_mode === "COD";
  const payPillText = isCod
    ? `COD ₹${Number(amt).toFixed(0)}`
    : `PREPAID${amt ? ` ₹${Number(amt).toFixed(0)}` : ""}`;
  const payPillClass = isCod ? "pay-pill cod" : "pay-pill prepaid";
  const courierLine = s.courier_name
    ? `<div class="courier-sub">via ${escape(s.courier_name)}</div>`
    : "";

  const itemsText =
    s.items && s.items.length
      ? s.items.join(", ")
      : (s.item_description || "-");

  const bcSvg = barcodeSvg(s.tracking_id, { height: 14, scale: 2 });

  return `
  <div class="label">
    <div class="hdr">
      <div class="brand-wrap">
        ${logoImg ? `<img class="brand-logo" src="${logoImg}" />` : ""}
        <div class="brand-name">${escape(brandName)}</div>
      </div>
      <div class="pay-wrap">
        <div class="${payPillClass}">${payPillText}</div>
        ${courierLine}
      </div>
    </div>

    <div class="body">
      ${senderBlock(sender, opts.showSenderContact)}
      ${receiverBlock(s)}
    </div>

    <div class="meta-row">
      ${s.order_id ? `<div class="meta-item"><span class="lbl">Order #:</span> ${escape(s.order_id)}</div>` : "<div></div>"}
      ${s.weight ? `<div class="meta-item right"><span class="lbl">Weight:</span> ${escape(s.weight)}</div>` : ""}
    </div>
    <div class="meta-row">
      <div class="meta-item"><span class="lbl">Items:</span> ${escape(itemsText)}</div>
    </div>

    <div class="track">
      <div class="track-label">TRACKING ID</div>
      <div class="track-id">${escape(s.tracking_id)}</div>
      <div class="barcode-wrap">${bcSvg}</div>
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
        const bcSvg = barcodeSvg(s.tracking_id, { height: 10, scale: 2 });
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
      ? `.sheet { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; gap: 2mm; }
         .label { height: 140mm; }`
      : perPage === 2
      ? `.sheet { display: grid; grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; gap: 2mm; }
         .label { height: 140mm; }`
      : perPage === 1
      ? `.sheet { display: block; page-break-after: always; }
         .label { height: 260mm; }`
      : `.sheet { display: block; page-break-after: always; }
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
  .label { border: 2px solid #0A0A0A; padding: 6mm; display: flex; flex-direction: column;
    break-inside: avoid; background: #fff; border-radius: 3mm; }

  /* Header: brand on left, payment pill on right */
  .hdr { display: flex; justify-content: space-between; align-items: flex-start;
    border-bottom: 2px solid #0A0A0A; padding-bottom: 3mm; gap: 4mm; }
  .brand-wrap { display: flex; align-items: center; gap: 3mm; flex: 1; min-width: 0; }
  .brand-logo { width: 14mm; height: 14mm; object-fit: contain; border-radius: 2mm; }
  .brand-name { font-size: 20pt; font-weight: 900; letter-spacing: -0.3px; line-height: 1.1;
    word-break: break-word; }
  .pay-wrap { text-align: right; flex-shrink: 0; }
  .pay-pill { display: inline-block; padding: 2.5mm 4mm; font-weight: 900;
    font-size: 13pt; border-radius: 999px; line-height: 1; white-space: nowrap;
    letter-spacing: 0.5px; }
  .pay-pill.prepaid { background: #0A0A0A; color: #fff; }
  .pay-pill.cod { background: #FF5A00; color: #fff; }
  .courier-sub { margin-top: 1.5mm; font-size: 9pt; color: #4B5563; font-weight: 600; }

  /* From / To */
  .body { display: grid; grid-template-columns: 1fr 1.15fr; gap: 4mm; margin-top: 4mm; }
  .blk { padding: 3mm; border-radius: 3mm; }
  .blk.sender { border: 1.5px dashed #6B7280; }
  .blk.receiver { border: 1.5px solid #0A0A0A; background: #F4F5F7; }
  .blk-title { font-size: 8pt; font-weight: 800; color: #4B5563;
    letter-spacing: 2px; margin-bottom: 2mm; }
  .blk-name { font-size: 13pt; font-weight: 900; margin-bottom: 1.5mm; line-height: 1.15; }
  .blk-line { font-size: 10pt; line-height: 1.35; color: #1F2937; }
  .blk-contact { font-size: 10pt; margin-top: 2mm; font-weight: 700; }

  /* Meta rows */
  .meta-row { display: flex; justify-content: space-between; font-size: 10pt;
    border-top: 1px solid #E5E7EB; padding-top: 2mm; margin-top: 3mm; gap: 4mm; }
  .meta-item { color: #0A0A0A; }
  .meta-item.right { text-align: right; }
  .meta-row .lbl { color: #6B7280; font-weight: 700; }

  /* Tracking block */
  .track { margin-top: 4mm; border-top: 2px solid #0A0A0A; padding-top: 4mm;
    text-align: center; }
  .track-label { font-size: 8pt; font-weight: 800; color: #4B5563;
    letter-spacing: 3px; margin-bottom: 1.5mm; }
  .track-id { font-family: 'Courier New', monospace; font-size: 18pt; font-weight: 900;
    letter-spacing: 3px; margin-bottom: 2mm; }
  .barcode-wrap { display: flex; justify-content: center; align-items: center; }
  .barcode-wrap svg { width: 80%; height: auto; max-height: 18mm; }
</style>
</head>
<body>
  ${allPages}
</body>
</html>`;
}
