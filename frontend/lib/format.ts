import type { Shipment, SenderAddress, Courier } from "./api";

export function trackingUrlFor(shipment: Shipment, courier?: Courier | null): string {
  const tpl = courier?.tracking_url_template?.trim();
  if (!tpl) return "";
  return tpl.replace(/\{tracking_id\}/g, encodeURIComponent(shipment.tracking_id));
}

export function buildCopyText(
  shipment: Shipment,
  settings: Settings | null,
  courier?: Courier | null
): string {
  const tpl =
    settings?.copy_template ||
    "Hi {customer_name}, your order #{order_id} has been shipped via {courier}. " +
      "Tracking ID: {tracking_id}. Track here: {tracking_url}";
  const url = trackingUrlFor(shipment, courier);
  return tpl
    .replace(/\{customer_name\}/g, shipment.customer_name || "")
    .replace(/\{order_id\}/g, shipment.order_id || "-")
    .replace(/\{courier\}/g, shipment.courier_name || "")
    .replace(/\{tracking_id\}/g, shipment.tracking_id || "")
    .replace(/\{tracking_url\}/g, url || "(link not set)")
    .replace(/\{amount\}/g, String(shipment.amount || 0));
}

export function buildWhatsAppText(
  shipment: Shipment,
  settings: Settings | null,
  courier?: Courier | null
): string {
  const tpl =
    settings?.whatsapp_template ||
    "Hi {customer_name}, your parcel via {courier}. Tracking: {tracking_id}. ETA {eta_days} days.";
  const url = trackingUrlFor(shipment, courier);
  return tpl
    .replace(/\{customer_name\}/g, shipment.customer_name || "")
    .replace(/\{order_id\}/g, shipment.order_id || "-")
    .replace(/\{courier\}/g, shipment.courier_name || "")
    .replace(/\{tracking_id\}/g, shipment.tracking_id || "")
    .replace(/\{tracking_url\}/g, url || "")
    .replace(/\{eta_days\}/g, String(settings?.default_eta_days ?? 7));
}

export function cleanPhone(raw: string): string {
  const digits = (raw || "").replace(/\D/g, "");
  if (!digits) return "";
  if (digits.length === 10) return `91${digits}`;
  return digits;
}
