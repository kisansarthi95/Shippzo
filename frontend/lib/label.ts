import type { Shipment, SenderAddress } from "./api";

export type LabelOptions = {
  perPage: 1 | 2 | 4 | "thermal";
  showSenderContact: boolean;
};

const escape = (s: string) =>
  (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

function senderBlock(sender: SenderAddress, show: boolean) {
  const lines = [
    sender.name,
    sender.address_line1,
    sender.address_line2,
    [sender.city, sender.state, sender.pincode].filter(Boolean).join(", "),
  ].filter(Boolean);
  const contact = show && sender.phone ? `📞 ${escape(sender.phone)}` : "";
  return `
    <div class="blk">
      <div class="blk-title">FROM</div>
      <div class="blk-name">${escape(sender.name || "Sender")}</div>
      ${lines
        .slice(1)
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
  const codBadge =
    s.payment_mode === "COD"
      ? `<div class="cod">COD ₹${s.cod_amount?.toFixed ? s.cod_amount.toFixed(0) : s.cod_amount}</div>`
      : `<div class="prepaid">PREPAID</div>`;

  return `
  <div class="label">
    <div class="hdr">
      <div class="courier">${escape(s.courier_name || "Courier")}</div>
      ${codBadge}
    </div>
    <div class="body">
      ${senderBlock(sender, opts.showSenderContact)}
      ${receiverBlock(s)}
    </div>
    <div class="meta">
      <div><span class="lbl">Weight:</span> ${escape(s.weight || "-")}</div>
      <div><span class="lbl">Item:</span> ${escape(s.item_description || "-")}</div>
    </div>
    <div class="track">
      <div class="track-id">${escape(s.tracking_id)}</div>
      <svg class="barcode" jsbarcode-value="${escape(s.tracking_id)}"
        jsbarcode-format="CODE128" jsbarcode-displayvalue="false"
        jsbarcode-height="45" jsbarcode-margin="0"></svg>
    </div>
  </div>
  `;
}

export function buildLabelHtml(
  shipments: Shipment[],
  sender: SenderAddress,
  opts: LabelOptions
): string {
  const labels = shipments.map((s) => singleLabel(s, sender, opts)).join("");

  const perPage = opts.perPage;
  const gridCss =
    perPage === 4
      ? `.sheet { display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; }
         .label { height: 140mm; }`
      : perPage === 2
      ? `.sheet { display: grid; grid-template-columns: 1fr; grid-template-rows: 1fr 1fr; page-break-after: always; }
         .label { height: 140mm; }`
      : perPage === 1
      ? `.sheet { display: block; page-break-after: always; }
         .label { height: 260mm; }`
      : // thermal
        `.sheet { display: block; page-break-after: always; }
         .label { height: 145mm; width: 100mm; }`;

  const pageCss =
    perPage === "thermal"
      ? `@page { size: 100mm 150mm; margin: 2mm; }`
      : `@page { size: A4; margin: 5mm; }`;

  // group into pages
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
  const allPages = pages.length ? pages.join("") : `<div class="sheet">${labels}</div>`;

  return `
<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Shipping Labels</title>
<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.6/dist/JsBarcode.all.min.js"></script>
<style>
  ${pageCss}
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
    margin: 0; color: #0A0A0A; }
  ${gridCss}
  .label { border: 2px solid #0A0A0A; padding: 6mm; display: flex; flex-direction: column;
    justify-content: space-between; break-inside: avoid; background: #fff; }
  .hdr { display: flex; justify-content: space-between; align-items: center;
    border-bottom: 2px solid #0A0A0A; padding-bottom: 4mm; }
  .courier { font-size: 18pt; font-weight: 800; letter-spacing: 0.5px; }
  .cod { background: #FF5A00; color: #fff; padding: 3mm 5mm; font-weight: 800;
    font-size: 14pt; border-radius: 4px; }
  .prepaid { background: #0A0A0A; color: #fff; padding: 3mm 5mm; font-weight: 800;
    font-size: 12pt; border-radius: 4px; }
  .body { display: grid; grid-template-columns: 1fr 1.3fr; gap: 4mm; margin-top: 4mm; }
  .blk { padding: 3mm; border: 1px dashed #4B5563; border-radius: 4px; }
  .blk.receiver { border: 2px solid #0A0A0A; background: #F4F5F7; }
  .blk-title { font-size: 8pt; font-weight: 700; color: #4B5563;
    letter-spacing: 1px; margin-bottom: 2mm; }
  .blk-name { font-size: 13pt; font-weight: 800; margin-bottom: 1mm; }
  .blk-line { font-size: 10pt; line-height: 1.3; }
  .blk-contact { font-size: 10pt; margin-top: 2mm; font-weight: 600; }
  .meta { display: flex; justify-content: space-between; font-size: 9pt;
    border-top: 1px solid #D1D5DB; padding-top: 2mm; margin-top: 3mm; }
  .meta .lbl { color: #4B5563; font-weight: 700; }
  .track { margin-top: 3mm; border-top: 2px solid #0A0A0A; padding-top: 3mm;
    text-align: center; }
  .track-id { font-family: 'Courier New', monospace; font-size: 16pt; font-weight: 800;
    letter-spacing: 2px; margin-bottom: 2mm; }
  .barcode { width: 85%; height: 45px; }
</style>
</head>
<body>
  ${allPages}
  <script>
    document.addEventListener('DOMContentLoaded', function () {
      try { JsBarcode('.barcode').init(); } catch (e) { console.log(e); }
    });
  </script>
</body>
</html>`;
}
