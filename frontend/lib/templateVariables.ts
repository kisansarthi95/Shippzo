/**
 * Shared WhatsApp template variable system (Phase-15 E)
 * -----------------------------------------------------
 * One source of truth for `{placeholder}` resolution across every
 * screen that fires WhatsApp. Add a new variable here and it
 * automatically:
 *   - shows up in the AI generator modal's variable-chip strip
 *   - is offered as an insert chip in the manual template editor
 *   - is resolved at send-time by every fillTemplate caller
 *
 * The AI generator backend (server-side) keeps its OWN copy of the
 * variable list inside the system prompt. Keep the two in sync when
 * you add new fields here.
 */

export type TemplateVarKey =
  | "customer_name"
  | "customer_phone"
  | "alt_phone"
  | "item"
  | "items"
  | "order_items"        // alias of `items` for legacy templates
  | "item_description"
  | "quantity"
  | "order_id"
  | "tracking_id"
  | "tracking_url"
  | "tracking_link"      // alias of `tracking_url`
  | "courier"
  | "courier_name"       // alias of `courier`
  | "eta_days"
  | "estimated_delivery" // human-readable "X days" / explicit date
  | "address"
  | "address_line1"
  | "address_line2"
  | "city"
  | "state"
  | "pincode"
  | "amount"
  | "weight"
  | "payment_mode"
  | "shop_name"
  | "shop_phone"
  | "helpline"
  | "google_review_url"
  | "website_url";

export type TemplateVarMeta = {
  key: TemplateVarKey;
  label: string;       // shown on the chip
  emoji: string;       // small icon for the chip
  group: "customer" | "order" | "shop" | "links";
  example: string;     // sample value (placeholder hint in editor)
  description: string; // short tooltip / hint
};

/** Master list — order matters (chips render in this order). */
export const TEMPLATE_VARIABLES: TemplateVarMeta[] = [
  // Customer block
  { key: "customer_name",  label: "Customer Name", emoji: "👤", group: "customer",
    example: "Rakesh Patel",      description: "Buyer's name from the shipment row" },
  { key: "customer_phone", label: "Customer Phone", emoji: "📞", group: "customer",
    example: "9876543210",        description: "Primary 10-digit mobile" },
  { key: "alt_phone",      label: "Alt Phone",      emoji: "📱", group: "customer",
    example: "9712544747",        description: "Alternate / family mobile" },

  // Order block
  { key: "order_id",         label: "Order ID",      emoji: "🧾", group: "order",
    example: "ORD-001",            description: "Your reference (falls back to tracking)" },
  { key: "tracking_id",      label: "Tracking ID",   emoji: "🔢", group: "order",
    example: "AWB12345",           description: "Courier tracking number" },
  { key: "item",             label: "Item",          emoji: "📦", group: "order",
    example: "Haldar 500g",        description: "First item / product name" },
  { key: "items",            label: "All Items",     emoji: "📦", group: "order",
    example: "Haldar 500g, Jeera 250g",
    description: "Comma-joined list of every item in the parcel" },
  // alias retained for legacy templates that wrote `{order_items}`
  { key: "order_items",      label: "Order Items",   emoji: "📦", group: "order",
    example: "Haldar 500g, Jeera 250g",
    description: "Alias of {items} — same comma-joined list" },
  { key: "item_description", label: "Item Notes",    emoji: "📝", group: "order",
    example: "Mixed spices",       description: "Free-text item description" },
  { key: "quantity",         label: "Qty",           emoji: "🔢", group: "order",
    example: "2",                  description: "Total item count" },
  { key: "courier",          label: "Courier",       emoji: "🚚", group: "order",
    example: "DTDC",               description: "Courier service name" },
  { key: "courier_name",     label: "Courier Name",  emoji: "🚚", group: "order",
    example: "DTDC",               description: "Alias of {courier}" },
  { key: "eta_days",         label: "ETA",           emoji: "⏱️", group: "order",
    example: "3",                  description: "Expected delivery in days" },
  { key: "estimated_delivery", label: "Est. Delivery", emoji: "📅", group: "order",
    example: "3 days",             description: "Human-readable ETA (e.g. \"3 days\")" },
  { key: "tracking_url",     label: "Tracking URL",  emoji: "🔗", group: "order",
    example: "https://...",        description: "Full tracking page link" },
  { key: "tracking_link",    label: "Tracking Link", emoji: "🔗", group: "order",
    example: "https://...",        description: "Alias of {tracking_url}" },
  { key: "amount",           label: "Amount",        emoji: "💰", group: "order",
    example: "499",                description: "Order amount (₹)" },
  { key: "weight",           label: "Weight",        emoji: "⚖️", group: "order",
    example: "0.5kg",              description: "Parcel weight" },
  { key: "payment_mode",     label: "Payment",       emoji: "💳", group: "order",
    example: "Prepaid / COD",      description: "Payment mode" },
  { key: "address",          label: "Full Address",  emoji: "🏠", group: "order",
    example: "12, MG Road, Surat 395003",
    description: "Joined address (line1 + line2 + city + pincode)" },
  { key: "address_line1",    label: "Address 1",     emoji: "🏘️", group: "order",
    example: "12 MG Road",         description: "First address line only" },
  { key: "address_line2",    label: "Address 2",     emoji: "🏘️", group: "order",
    example: "Near Park",          description: "Second address line only" },
  { key: "city",             label: "City",          emoji: "🌆", group: "order",
    example: "Surat",              description: "City name" },
  { key: "state",            label: "State",         emoji: "📍", group: "order",
    example: "Gujarat",            description: "State name" },
  { key: "pincode",          label: "Pincode",       emoji: "🔢", group: "order",
    example: "395003",             description: "6-digit pincode" },

  // Shop block
  { key: "shop_name",   label: "Shop Name",   emoji: "🏪", group: "shop",
    example: "Patel Spices",      description: "Your business name (auto)" },
  { key: "shop_phone",  label: "Shop Phone",  emoji: "📞", group: "shop",
    example: "9000011111",         description: "Your shop's contact number" },
  { key: "helpline",    label: "Helpline",    emoji: "🆘", group: "shop",
    example: "1800-XXX",           description: "Customer support helpline" },

  // Links block
  { key: "google_review_url", label: "Google Review", emoji: "⭐", group: "links",
    example: "g.page/r/...",       description: "Your Google review link" },
  { key: "website_url",       label: "Website",       emoji: "🌐", group: "links",
    example: "shop.com",           description: "Your website / catalog link" },
];

/** Variable groups for chip-strip rendering with section headers. */
export const VARIABLE_GROUPS: Array<{
  key: TemplateVarMeta["group"];
  label: string;
  emoji: string;
}> = [
  { key: "customer", label: "Customer", emoji: "👤" },
  { key: "order",    label: "Order",    emoji: "🧾" },
  { key: "shop",     label: "Shop",     emoji: "🏪" },
  { key: "links",    label: "Links",    emoji: "🔗" },
];

/** Loose shape — uses any-cast since shipments come from many sources. */
type ShipmentLike = Record<string, any>;
type SettingsLike = {
  business_links?: { google_review_url?: string; website_url?: string };
  shop_phone?: string;
  helpline?: string;
} | null | undefined;
type UserLike = { shop_name?: string; name?: string } | null | undefined;
type CourierLike = { name?: string; eta_days?: number | string } | null | undefined;

/**
 * Builds the full {var: value} dict for one shipment. Missing fields
 * collapse to empty strings so leftover placeholders don't render
 * literally in the message.
 */
export function buildTemplateVars(
  shipment: ShipmentLike,
  settings: SettingsLike = null,
  user: UserLike = null,
  courier: CourierLike = null,
): Record<string, string> {
  const s = shipment || {};
  const items: string[] = Array.isArray(s.items) ? s.items.filter(Boolean) : [];
  const itemDesc = String(s.item_description || "").trim();
  const firstItem = items[0] || itemDesc || "";
  const itemsAll = items.length ? items.join(", ") : itemDesc;

  // Address: prefer concatenation when components exist, else
  // address_line1 + line2 fallback.
  const addrParts = [
    String(s.address_line1 || "").trim(),
    String(s.address_line2 || "").trim(),
    String(s.city || "").trim(),
    String(s.state || "").trim(),
    String(s.pincode || "").trim(),
  ].filter(Boolean);
  const fullAddress = addrParts.join(", ");

  const links = settings?.business_links || {};
  const orderId = String(s.order_id || "").trim();
  const trackingId = String(s.tracking_id || "").trim();

  const courierName = String(courier?.name || s.courier_name || "").trim();
  const etaDays = String(
    (courier as any)?.eta_days ??
      (s as any).courier_eta_days ??
      (s as any).eta_days ??
      "",
  );
  // Phase-22 (2026-05-17) — Resolve the full tracking URL from the
  // courier's template. Aliases `{tracking_link}` + `{tracking_url}`
  // share this value so legacy templates keep working. Falls back
  // to an empty string when either piece is missing (so the message
  // never sends literal `{tracking_link}` text to customers).
  const tplUrl = String((courier as any)?.tracking_url_template || "").trim();
  const trackingUrl = trackingId && tplUrl
    ? tplUrl.replace(/\{tracking_id\}/g, encodeURIComponent(trackingId))
    : "";
  const etaHuman = etaDays
    ? `${etaDays} day${Number(etaDays) === 1 ? "" : "s"}`
    : "";

  return {
    customer_name:    String(s.customer_name || "").trim(),
    customer_phone:   String(s.customer_phone || "").trim(),
    alt_phone:        String(s.customer_alt_phone || s.alt_phone || "").trim(),

    order_id:         orderId || trackingId,
    tracking_id:      trackingId,
    item:             firstItem,
    items:            itemsAll,
    order_items:      itemsAll,           // alias of {items}
    item_description: itemDesc,
    quantity:         String(items.length || s.quantity || ""),

    courier:          courierName,
    courier_name:     courierName,         // alias of {courier}
    eta_days:         etaDays,
    estimated_delivery: etaHuman,          // "3 days" / "" fallback

    tracking_url:     trackingUrl,
    tracking_link:    trackingUrl,         // alias of {tracking_url}

    address:          fullAddress,
    address_line1:    String(s.address_line1 || "").trim(),
    address_line2:    String(s.address_line2 || "").trim(),
    city:             String(s.city || "").trim(),
    state:            String(s.state || "").trim(),
    pincode:          String(s.pincode || "").trim(),
    amount:           String(s.amount ?? s.cod_amount ?? "").trim(),
    weight:           String(s.weight || "").trim(),
    payment_mode:     String(s.payment_mode || "").trim(),

    shop_name:        String(user?.shop_name || user?.name || "").trim(),
    shop_phone:       String(settings?.shop_phone || "").trim(),
    helpline:         String(
      settings?.helpline ||
        settings?.shop_phone || // fall back to shop phone if helpline empty
        "",
    ).trim(),

    google_review_url: String(links.google_review_url || "").trim(),
    website_url:       String(links.website_url || "").trim(),
  };
}

/**
 * Substitute every `{var}` token in the template body. Unknown
 * placeholders collapse to empty strings rather than rendering
 * literally — so a pasted-in template that uses a variable we don't
 * have yet will simply omit it instead of shouting "{nonsense}" at
 * the customer.
 */
export function fillTemplate(
  template: string,
  vars: Record<string, string>,
): string {
  return String(template || "").replace(/\{(\w+)\}/g, (_m, k) =>
    Object.prototype.hasOwnProperty.call(vars, k) ? vars[k] : "",
  );
}

/**
 * Quick helper for the common case: shipment + settings + user.
 * Equivalent to `fillTemplate(tpl, buildTemplateVars(...))`.
 *
 * Phase-31 (2026-05-17) — Item-name auto-inject. The customer
 * MUST always see what was shipped. If the user's saved template
 * doesn't reference any item placeholder (`{item}`, `{items}`,
 * `{order_items}`, `{item_description}`) yet the shipment HAS item
 * data, we splice a "📦 Item: …" line into the rendered message
 * right after the order-id line (or at the top if no order-id line
 * was found). Likewise, when `{eta_days}` is in the template but
 * blank, the surrounding "Expected delivery: …days" line is stripped
 * so customers don't see ugly orphaned text.
 */
const ITEM_PLACEHOLDER_RE = /\{(item|items|order_items|item_description)\}/;

function autoInjectItemLine(rendered: string, vars: Record<string, string>): string {
  const itemsText = (vars.items || vars.order_items || vars.item || vars.item_description || "").trim();
  if (!itemsText) return rendered;
  const lines = rendered.split("\n");
  // Insert after the line that contains the order id, otherwise at the
  // very top so the customer can't miss it.
  const orderId = (vars.order_id || "").trim();
  const itemLine = `📦 Item: ${itemsText}`;
  let inserted = false;
  for (let i = 0; i < lines.length; i++) {
    if (orderId && lines[i].includes(orderId)) {
      lines.splice(i + 1, 0, itemLine);
      inserted = true;
      break;
    }
  }
  if (!inserted) lines.splice(1, 0, itemLine);
  return lines.join("\n");
}

function stripEmptyEtaLine(rendered: string, vars: Record<string, string>): string {
  if ((vars.eta_days || "").trim()) return rendered;
  // Remove lines that mention "delivery" + " days" but have no number
  // before "days" — these are orphaned templates like
  // "Expected delivery:  days".
  return rendered
    .split("\n")
    .filter((ln) => {
      const t = ln.trim();
      if (!t) return true;
      // Pattern: any text + ":" + (space)* + "days" with nothing
      // numeric in between → drop.
      return !/:\s*(day|days)\b/i.test(t) || /\d/.test(t);
    })
    .join("\n");
}

export function fillFromShipment(
  template: string,
  shipment: ShipmentLike,
  settings: SettingsLike = null,
  user: UserLike = null,
  courier: CourierLike = null,
): string {
  const vars = buildTemplateVars(shipment, settings, user, courier);
  let out = fillTemplate(template, vars);
  if (!ITEM_PLACEHOLDER_RE.test(template || "")) {
    out = autoInjectItemLine(out, vars);
  }
  out = stripEmptyEtaLine(out, vars);
  return out;
}

/**
 * Phase-29 — render a template with REALISTIC sample values for every
 * single registered variable. Used by the WhatsApp template editor's
 * "Live preview" so the admin/user sees exactly what the customer
 * will get, with all 30+ variables substituted (not just the 5
 * historically hardcoded ones).
 *
 * Pulls each sample from `TEMPLATE_VARIABLES[i].example`, so adding
 * a new variable + example to the master list above is enough — the
 * preview picks it up automatically.
 */
export function previewWithSamples(template: string): string {
  if (!template) return "";
  const samples: Record<string, string> = {};
  for (const v of TEMPLATE_VARIABLES) {
    samples[v.key] = v.example || "";
  }
  return fillTemplate(template, samples);
}

