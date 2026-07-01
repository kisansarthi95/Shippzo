/**
 * Shipment Label — types + API helpers.
 *
 * Backend contract (see /app/backend/routers/labels.py):
 *   GET    /api/labels                     → { labels: Label[], count }
 *   POST   /api/labels                     → { ok, label }
 *   PUT    /api/labels/{id}                → { ok, label }
 *   DELETE /api/labels/{id}                → { ok }
 *   PUT    /api/shipments/{id}/labels      → { ok, labels: string[] }
 *
 * All routes are additive — no existing endpoint touched.
 */
import { api } from "./api";

export type LabelKind = "order" | "priority" | "custom";

export type ShipmentLabel = {
  id: string;
  user_id: string;
  name: string;
  icon: string;
  color: string;
  kind: LabelKind;
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

/** Ionicon name used for each abstract icon key (kept in one place so
 *  every renderer picks the same glyph). */
export const LABEL_ICON_MAP: Record<string, string> = {
  tag:      "pricetag",
  package:  "cube",
  truck:    "car",
  cart:     "cart",
  bag:      "bag-handle",
  person:   "person-circle",
  alert:    "alert-circle",
  headset:  "headset",
  document: "document-text",
  pricetag: "pricetag",
  star:     "star",
  heart:    "heart",
  bell:     "notifications",
  calendar: "calendar",
  cash:     "cash",
  flag:     "flag",
  location: "location",
  call:     "call",
  cube:     "cube",
  return:   "return-up-back",
  check:    "checkmark-circle",
  shield:   "shield-checkmark",
  clock:    "time",
  pin:      "pin",
  gift:     "gift",
};

/** Palette exposed by the Create Label dialog. */
export const LABEL_COLORS: string[] = [
  "#F97316", // orange (default)
  "#3B82F6", // blue
  "#F59E0B", // amber
  "#8B5CF6", // violet
  "#10B981", // green
  "#6B7280", // gray
  "#7C2D12", // brown
  "#EC4899", // pink
  "#06B6D4", // cyan
  "#111827", // near-black
  "#DC2626", // red
  "#A855F7", // purple
];

/** Icons offered in the picker (must be keys of LABEL_ICON_MAP). */
export const LABEL_ICON_KEYS: string[] = [
  "tag", "package", "truck", "cart", "bag",
  "person", "alert", "headset", "document", "pricetag",
  "star", "heart", "bell", "calendar", "cash",
  "flag", "location", "call", "check", "shield",
  "clock", "pin", "gift", "return",
];

export const LabelsApi = {
  list: () =>
    api.get<{ labels: ShipmentLabel[]; count: number }>("/labels")
      .then((r) => r.data.labels),

  create: (input: { name: string; icon: string; color: string; kind?: string }) =>
    api.post<{ ok: boolean; label: ShipmentLabel }>("/labels", input)
      .then((r) => r.data.label),

  update: (id: string, patch: Partial<Pick<ShipmentLabel, "name" | "icon" | "color">>) =>
    api.put<{ ok: boolean; label: ShipmentLabel }>(`/labels/${id}`, patch)
      .then((r) => r.data.label),

  remove: (id: string) =>
    api.delete<{ ok: boolean }>(`/labels/${id}`).then((r) => r.data),

  /** Replace the labels[] array on one shipment (idempotent). */
  setForShipment: (shipmentId: string, labelIds: string[]) =>
    api.put<{ ok: boolean; labels: string[] }>(
      `/shipments/${shipmentId}/labels`,
      { labels: labelIds },
    ).then((r) => r.data.labels),
};
