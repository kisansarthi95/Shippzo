/**
 * Packing Summary generator
 * --------------------------
 *
 * Builds a WhatsApp-friendly plain-text packing list from one or many
 * shipments. The format is deliberately monospaced-friendly (uses ASCII
 * heavy-line separators) so it renders consistently on every device.
 *
 * Localisation contract
 *   • ONLY labels (Customer, City, Courier, Payment, …) are translated.
 *   • Values stay verbatim — tracking IDs, courier names, product
 *     descriptions, dates, etc. are NEVER touched. This is critical
 *     because the staff who packs needs to read the exact strings off
 *     courier portals and product SKUs, which are themselves often
 *     English.
 *
 * Future-proof
 *   • All labels go through `t(key, lang)` so adding Marathi / Bengali /
 *     etc. is a matter of dropping a new map into `DICTIONARIES` — no
 *     business-logic changes anywhere.
 */
import type { Shipment } from "./api";
import AsyncStorage from "@react-native-async-storage/async-storage";

const PACKING_LANG_KEY = "@shippzo:packing_language";

/** Persisted preference — defaults to English on first run.
 *  Sync API for both reading + writing so callers can use it from
 *  inside an effect without an extra try/catch wrapper. */
export async function getPackingLangPref(): Promise<PackingLang> {
  try {
    const v = await AsyncStorage.getItem(PACKING_LANG_KEY);
    if (v === "en" || v === "gu" || v === "hi") return v;
  } catch { /* ignore */ }
  return "en";
}

export async function setPackingLangPref(lang: PackingLang): Promise<void> {
  try {
    await AsyncStorage.setItem(PACKING_LANG_KEY, lang);
  } catch { /* ignore */ }
}

export type PackingLang = "en" | "gu" | "hi";

export const PACKING_LANG_OPTIONS: { code: PackingLang; label: string; native: string }[] = [
  { code: "en", label: "English",  native: "English"   },
  { code: "gu", label: "Gujarati", native: "ગુજરાતી"   },
  { code: "hi", label: "Hindi",    native: "हिन्दी"     },
];

// ─── Label translations ────────────────────────────────────────────
// Keep this map narrow on purpose. We only ever translate the LEFT
// side of each line (the field's name). Values are passed through.
type LabelKey =
  | "title"
  | "tracking_number"
  | "customer"
  | "order_number"
  | "city"
  | "courier"
  | "payment"
  | "product"
  | "weight"
  | "order_date"
  | "total_orders"
  | "cod"
  | "prepaid";

const DICTIONARIES: Record<PackingLang, Record<LabelKey, string>> = {
  en: {
    title:           "PACKING LIST",
    tracking_number: "Tracking Number",
    customer:        "Customer",
    order_number:    "Order Number",
    city:            "City",
    courier:         "Courier",
    payment:         "Payment",
    product:         "Product",
    weight:          "Weight",
    order_date:      "Order Date",
    total_orders:    "Total Orders",
    cod:             "COD",
    prepaid:         "Prepaid",
  },
  gu: {
    title:           "પેકિંગ યાદી",
    tracking_number: "ટ્રેકિંગ નંબર",
    customer:        "ગ્રાહક",
    order_number:    "ઓર્ડર નંબર",
    city:            "શહેર",
    courier:         "કુરિયર",
    payment:         "ચુકવણી",
    product:         "પ્રોડક્ટ",
    weight:          "વજન",
    order_date:      "ઓર્ડર તારીખ",
    total_orders:    "કુલ ઓર્ડર",
    cod:             "COD",       // brand/term — kept as-is
    prepaid:         "પ્રીપેઇડ",
  },
  hi: {
    title:           "पैकिंग सूची",
    tracking_number: "ट्रैकिंग नंबर",
    customer:        "ग्राहक",
    order_number:    "ऑर्डर नंबर",
    city:            "शहर",
    courier:         "कूरियर",
    payment:         "भुगतान",
    product:         "उत्पाद",
    weight:          "वज़न",
    order_date:      "ऑर्डर तारीख",
    total_orders:    "कुल ऑर्डर",
    cod:             "COD",
    prepaid:         "प्रीपेड",
  },
};

export function t(key: LabelKey, lang: PackingLang): string {
  return DICTIONARIES[lang]?.[key] || DICTIONARIES.en[key] || key;
}

// ─── Date formatting ───────────────────────────────────────────────
// "30 May 2026\n10:39 AM" — month name is English even when the rest
// of the summary is in Gujarati/Hindi. This matches the user's brief:
// "Never translate" the date itself, only the label. We localise the
// month name only when the language explicitly wants it.
const MONTH_NAMES_EN = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function formatOrderDate(iso?: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const day   = d.getDate();
  const mon   = MONTH_NAMES_EN[d.getMonth()] || "";
  const year  = d.getFullYear();
  let hh   = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = hh >= 12 ? "PM" : "AM";
  hh = hh % 12 || 12;
  return `${day} ${mon} ${year}\n${hh}:${mm} ${ampm}`;
}

// ─── Payment formatting ────────────────────────────────────────────
// Values stay numeric — only the "COD" / "Prepaid" word is localised
// (and even those default to English brand terms since they're widely
// recognised).
function formatPayment(s: Shipment, lang: PackingLang): string {
  const amount = (s.payment_mode === "COD"
    ? (s.cod_amount || s.amount || 0)
    : (s.amount || 0)
  );
  const word = s.payment_mode === "COD" ? t("cod", lang) : t("prepaid", lang);
  if (!amount) return word;
  // ₹ is universal across all three languages we support, no need to swap.
  return `${word} ₹${amount}`;
}

// ─── Product description ───────────────────────────────────────────
// Product strings are NEVER translated. We just pick the best-available
// field and stitch quantity if there's only a single description.
function formatProduct(s: Shipment): string {
  if (Array.isArray(s.items) && s.items.length > 0) {
    return s.items.filter(Boolean).join("\n");
  }
  if (s.item_description) return s.item_description;
  return "—";
}

// ─── Single-shipment block ─────────────────────────────────────────
// Built as line-arrays so we can easily filter empty rows without
// leaving "Label:\n\n" gaps in the output. Each emitted block is
// concatenated by `\n\n` (one blank line between rows).
function buildShipmentBlock(
  index: number,
  s: Shipment,
  lang: PackingLang,
): string {
  const lines: string[] = [`${index}) `];

  const push = (label: LabelKey, value: string | undefined | null) => {
    const v = (value || "").toString().trim();
    if (!v) return;
    lines.push("", `${t(label, lang)}:`, v);
  };

  push("tracking_number", s.tracking_id);
  push("customer",        s.customer_name);
  push("order_number",    s.order_id);
  push("city",            s.city);
  push("courier",         s.courier_name);
  // Payment always exists (mode + amount), and is the most useful for
  // the packer because it tells them whether to collect cash.
  lines.push("", `${t("payment", lang)}:`, formatPayment(s, lang));
  push("product", formatProduct(s));
  push("weight",  s.weight);
  push("order_date", formatOrderDate(s.created_at));

  return lines.join("\n").replace(/\n{3,}/g, "\n\n");
}

// ─── Public entry-point ────────────────────────────────────────────
const SEP = "━".repeat(16);

export function generatePackingSummary(
  shipments: Shipment[],
  lang: PackingLang = "en",
): string {
  if (!Array.isArray(shipments) || shipments.length === 0) return "";

  const parts: string[] = [];
  parts.push(`📦 ${t("title", lang)}`);
  parts.push(SEP);

  shipments.forEach((s, i) => {
    parts.push(buildShipmentBlock(i + 1, s, lang));
    parts.push(SEP);
  });

  parts.push(`${t("total_orders", lang)}: ${shipments.length}`);

  return parts.join("\n\n");
}
