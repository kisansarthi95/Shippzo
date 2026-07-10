/**
 * Shipments — status metadata + filter helpers + time formatter.
 *
 * Extracted from `app/(tabs)/shipments.tsx` (Phase F4.5) to keep the
 * screen file under the agent-tool string-match ceiling and to allow
 * the same constants to be re-used by the shipment-details page,
 * dashboard cards, exports, etc.
 *
 * NOTHING in this module holds React state — every symbol here is
 * either a pure type, a pure constant, or a pure function. Safe to
 * import from anywhere (client + server-render + tests).
 */
import { StyleSheet } from "react-native";

// ---------------------- Types ----------------------------------------
export type StatusFilter =
  | "All"
  | "Pending"
  | "Processing"
  | "Ready to Ship"
  | "Shipped"
  | "In Transit"
  | "Out for Delivery"
  | "Delivered"
  | "Feedback"
  | "Modified"
  | "Cancel by buyer"
  | "Cancelled"
  | "Returned";

export type DateFilter = "all" | "today" | "week" | "month" | "custom";

export type StatusMetaEntry = {
  value: string;
  label?: string;
  bg: string;
  fg: string;
  aliases?: string[];
  activeBg?: string;
  activeFg?: string;
};

// ---------------------- STATUS_META ----------------------------------
// Status meta (label, backend value, color). `value` is the string
// stored in `shipment.status` in Mongo and sent to the PUT
// /shipments/{id} endpoint. The Two-Way Sync propagates every change
// to the Master Sheet automatically.
// Phase-9 color palette (locked): Pending = BLACK active (warehouse
// emphasis), Dispatch = soft CREAM (#F4E3CF / #8B5E34), Shipped =
// lavender, Delivered = green. `activeBg` / `activeFg` override the
// default black pill when a bucket is selected. `label` overrides the
// visible chip text — used to rename "Dispatch" to "Ready to Ship"
// without breaking historic DB rows still tagged "Dispatch".
export const STATUS_META: Record<
  Exclude<StatusFilter, "All">,
  StatusMetaEntry
> = {
  "Pending": {
    value: "Pending",
    bg: "#F8ECC2",      // soft yellow-cream pill badge on the card
    fg: "#8B6B00",
    activeBg: "#000000",
    activeFg: "#FFFFFF",
  },
  // NEW: Processing — between Pending and Ready-to-Ship. Used for
  // parcels that are currently being packed / labelled but not yet
  // handed over.
  "Processing": {
    value: "Processing",
    bg: "#FEF3C7",
    fg: "#92400E",
    activeBg: "#FCD34D",
    activeFg: "#7C2D12",
  },
  // Phase F2.2 (2026-05-09) — formerly keyed as "Dispatch" with a
  // label override. Renamed to canonical "Ready to Ship" everywhere.
  // Legacy DB rows still tagged "Dispatch" / "Dispatched" surface
  // here via the alias list below.
  "Ready to Ship": {
    value: "Ready to Ship",
    bg: "#F4E3CF",
    fg: "#8B5E34",
    activeBg: "#F4E3CF",
    activeFg: "#8B5E34",
    aliases: ["Dispatch", "Dispatched", "ReadyToShip", "READY_TO_SHIP"],
  },
  "Shipped": {
    value: "Shipped",
    bg: "#EEE9FF",
    fg: "#6B5BFF",
    activeBg: "#EEE9FF",
    activeFg: "#6B5BFF",
  },
  // Phase F7.7 (Jun-2026) — In Transit is a new first-class stage
  // between Shipped and Out for Delivery. Populated automatically by
  // Delivery Import when the India Post "Last Event" text contains
  // "Item Dispatched", "Item bagged", or "Item Received". Ocean-teal
  // palette conveys "en-route, cross-hub" without clashing with the
  // amber Out-for-Delivery step that follows.
  "In Transit": {
    value: "In Transit",
    bg: "#CFFAFE",
    fg: "#0369A1",
    activeBg: "#CFFAFE",
    activeFg: "#0369A1",
    aliases: [
      "in_transit", "In transit", "IN_TRANSIT", "InTransit",
      "In-Transit", "in-transit",
    ],
  },
  // Phase-F5 (Jul-2026) — Out for Delivery is a first-class stage now.
  // Sits between Shipped (violet) and Delivered (green) in the
  // pipeline and uses a warm amber palette so the "en-route, arriving
  // today" vibe reads at a glance. Aliases keep legacy DB rows and
  // the various courier webhook spellings mapping to the same status.
  "Out for Delivery": {
    value: "Out for Delivery",
    bg: "#FEF3C7",
    fg: "#B45309",
    activeBg: "#FEF3C7",
    activeFg: "#B45309",
    aliases: [
      "OFD", "ofd",
      "Out for delivery", "out for delivery",
      "Out_for_delivery", "out_for_delivery",
      "OutForDelivery", "outfordelivery",
      "On the way", "on the way", "on_the_way",
    ],
  },
  "Delivered": {
    value: "Delivered",
    bg: "#E6F7EE",
    fg: "#1F9D55",
    activeBg: "#E6F7EE",
    activeFg: "#1F9D55",
  },
  // NEW: Feedback — terminal stage. Customer has confirmed receipt
  // and (optionally) given a rating/review. Lives after "Delivered"
  // so the workflow flows linearly Pending → Processing → Ready →
  // Shipped → Delivered → Feedback.
  "Feedback": {
    value: "Feedback",
    bg: "#DBEAFE",
    fg: "#1E40AF",
    activeBg: "#1E40AF",
    activeFg: "#FFFFFF",
  },
  "Modified":        { value: "Modified",       bg: "#FEF9C3", fg: "#854D0E" },
  "Cancel by buyer": { value: "Cancel by buyer", bg: "#FCE7F3", fg: "#9D174D" },
  "Cancelled":       { value: "Cancelled",      bg: "#FEE2E2", fg: "#991B1B" },
  "Returned":        { value: "Returned",       bg: "#FFEDD5", fg: "#9A3412" },
};

export const STATUS_FILTER_ORDER: StatusFilter[] = [
  "All", "Pending", "Processing", "Ready to Ship", "Shipped",
  "In Transit", "Out for Delivery", "Delivered", "Feedback",
  "Modified", "Cancel by buyer", "Cancelled", "Returned",
];

// ---------------------- Helpers --------------------------------------

/** Return true when a shipment's stored status matches the selected
 *  filter — handles alias fan-out (e.g., legacy "Dispatch" rows
 *  matching the "Ready to Ship" bucket). */
export function matchesStatusFilter(
  shipStatus: string,
  filter: StatusFilter,
): boolean {
  if (filter === "All") return true;
  const meta = STATUS_META[filter];
  if (!meta) return false;
  if (shipStatus === meta.value) return true;
  if (meta.aliases && meta.aliases.includes(shipStatus)) return true;
  return false;
}

/** Phase F2.2 — render an ISO timestamp as a glanceable, locale-aware
 *  "29 Apr 2026 · 02:30 PM" string. Empty/garbage input returns "" so
 *  the caller can short-circuit. The label intentionally uses 12-hour
 *  format because that matches how Indian shop owners read receipts. */
export function formatTimestamp(iso: string | undefined | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = String(d.getDate()).padStart(2, "0");
  const mon = months[d.getMonth()];
  const yr  = d.getFullYear();
  let hh = d.getHours();
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ampm = hh >= 12 ? "PM" : "AM";
  hh = hh % 12 || 12;
  return `${day} ${mon} ${yr} · ${String(hh).padStart(2, "0")}:${mm} ${ampm}`;
}

/** Detect axios "no server reachable" type errors so we can route the
 *  operation to the offline queue instead of surfacing a scary alert. */
export function isNetworkErrish(err: any): boolean {
  if (!err) return false;
  if (err?.response) return false;   // server replied — definitive failure
  return /network|timeout|abort|err_network/i.test(String(err?.message || ""));
}

// ---------------------- Label chip row styles ------------------------
// Small local stylesheet that used to live inline in shipments.tsx.
// Exported so any screen that renders the "+ Add Label" divider row
// can share the exact styling.
export const labelStyles = StyleSheet.create({
  dividerRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 6,
    marginBottom: 2,
  },
  divider: {
    flex: 1,
    height: StyleSheet.hairlineWidth,
    backgroundColor: "#E5E7EB",
  },
  plusBtn: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#F8FAFC",
  },
  chipRow: {
    marginTop: 6,
    marginBottom: 2,
  },
});
