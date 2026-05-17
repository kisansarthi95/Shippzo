import type { Shipment, Settings, SenderAddress, Courier } from "./api";
import { fillFromShipment } from "./templateVariables";

export function trackingUrlFor(shipment: Shipment, courier?: Courier | null): string {
  const tpl = courier?.tracking_url_template?.trim();
  if (!tpl) return "";
  return tpl.replace(/\{tracking_id\}/g, encodeURIComponent(shipment.tracking_id));
}

/**
 * Build a plain-text message ready to be copied to clipboard.
 *
 * Phase-23 (2026-05-17) — The old hand-rolled regex chain only knew
 * about 5–6 variables, so any customer-saved template that used
 * `{order_items}`, `{tracking_link}`, `{estimated_delivery}`,
 * `{address}`, etc. leaked literal placeholders into the message.
 * We now delegate to the canonical resolver so EVERY registered
 * placeholder (plus aliases like `order_items` ↔ `items`) is mapped
 * from the exact shipment row, and unknown tokens fall back to
 * empty strings instead of being sent as-is.
 */
export function buildCopyText(
  shipment: Shipment,
  settings: Settings | null,
  courier?: Courier | null,
): string {
  const tpl =
    (settings as any)?.copy_template ||
    "Hi {customer_name}, your order #{order_id} has been shipped via {courier}. " +
      "Tracking ID: {tracking_id}. Track here: {tracking_url}";
  return fillFromShipment(tpl, shipment, settings, null, courier);
}

/**
 * Build a WhatsApp-ready message. Same Phase-23 treatment as
 * `buildCopyText` — all variables (including aliases) bind from
 * the supplied shipment / settings / courier, missing values become
 * empty strings rather than literal `{xyz}` placeholders.
 */
export function buildWhatsAppText(
  shipment: Shipment,
  settings: Settings | null,
  courier?: Courier | null,
): string {
  const tpl =
    (settings as any)?.whatsapp_template ||
    "Hi {customer_name}, your parcel via {courier}. Tracking: {tracking_id}. ETA {eta_days} days.";
  return fillFromShipment(tpl, shipment, settings, null, courier);
}

export function cleanPhone(raw: string): string {
  const digits = (raw || "").replace(/\D/g, "");
  if (!digits) return "";
  if (digits.length === 10) return `91${digits}`;
  return digits;
}
