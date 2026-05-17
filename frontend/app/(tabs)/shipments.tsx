import React, { useCallback, useMemo, useState, useEffect } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  FlatList, RefreshControl, Linking, Alert, Platform, Modal,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Clipboard from "expo-clipboard";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useFocusEffect, useRouter, useLocalSearchParams } from "expo-router";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Api, Shipment, Settings, Courier } from "../../lib/api";
import { SyncQueue } from "../../lib/syncQueue";

// Helper: detect axios "no server reachable" type errors so we can route
// the operation to the offline queue instead of surfacing a scary alert.
function isNetworkErrish(err: any): boolean {
  if (!err) return false;
  if (err?.response) return false; // server replied — definitive failure
  return /network|timeout|abort|err_network/i.test(String(err?.message || ""));
}
import { buildCopyText, buildWhatsAppText, cleanPhone } from "../../lib/format";
import { buildLabelHtml, pageDimensionsFor } from "../../lib/label";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";
import { requestWhatsAppSend } from "../../lib/whatsappGuard";
import DailyLimitBanner from "../../components/DailyLimitBanner";

type StatusFilter =
  | "All"
  | "Pending"
  | "Processing"
  | "Ready to Ship"
  | "Shipped"
  | "Delivered"
  | "Feedback"
  | "Modified"
  | "Cancel by buyer"
  | "Cancelled"
  | "Returned";
type DateFilter = "all" | "today" | "week" | "month" | "custom";

// Status meta (label, backend value, color). `value` is the string stored
// in `shipment.status` in Mongo and sent to the PUT /shipments/{id} endpoint.
// The Two-Way Sync propagates every change to the Master Sheet automatically.
// Phase-9 color palette (locked): Pending = BLACK active (warehouse emphasis),
// Dispatch = soft CREAM (#F4E3CF / #8B5E34), Shipped = lavender, Delivered = green.
// `activeBg` / `activeFg` override the default black pill when a bucket is selected.
// `label` overrides the visible chip text — used to rename "Dispatch" to
// "Ready to Ship" without breaking historic DB rows still tagged "Dispatch".
const STATUS_META: Record<
  Exclude<StatusFilter, "All">,
  {
    value: string;
    label?: string;
    bg: string;
    fg: string;
    aliases?: string[];
    activeBg?: string;
    activeFg?: string;
  }
> = {
  "Pending": {
    value: "Pending",
    bg: "#F8ECC2",      // soft yellow-cream pill badge on the card
    fg: "#8B6B00",
    activeBg: "#000000",
    activeFg: "#FFFFFF",
  },
  // NEW: Processing — between Pending and Ready-to-Ship. Used for parcels
  // that are currently being packed / labelled but not yet handed over.
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
  "Delivered": {
    value: "Delivered",
    bg: "#E6F7EE",
    fg: "#1F9D55",
    activeBg: "#E6F7EE",
    activeFg: "#1F9D55",
  },
  // NEW: Feedback — terminal stage. Customer has confirmed receipt and
  // (optionally) given a rating/review. Lives after "Delivered" so the
  // workflow flows linearly Pending → Processing → Ready → Shipped →
  // Delivered → Feedback.
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
const STATUS_FILTER_ORDER: StatusFilter[] = [
  "All", "Pending", "Processing", "Ready to Ship", "Shipped", "Delivered", "Feedback",
  "Modified", "Cancel by buyer", "Cancelled", "Returned",
];

// Return true when a shipment's stored status matches the selected filter.
function matchesStatusFilter(shipStatus: string, filter: StatusFilter): boolean {
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
function formatTimestamp(iso: string | undefined | null): string {
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

export default function Shipments() {
  const router = useRouter();
  const params = useLocalSearchParams<{ status?: string; select?: string }>();
  // Per-row action visibility — feature flags wired from the admin panel.
  // Admin users see everything; other plans render only what's enabled.
  const flagCopy        = useFeatureFlag("shipment_copy_btn");
  const flagWhatsapp    = useFeatureFlag("shipment_whatsapp_btn");
  const flagEdit        = useFeatureFlag("shipment_edit_btn");
  const flagDelete      = useFeatureFlag("shipment_delete_btn");
  const flagPrint       = useFeatureFlag("shipment_print_btn");
  const flagBulkPrint   = useFeatureFlag("bulk_print");
  const flagMarkDeliv   = useFeatureFlag("shipment_mark_delivered");
  // NEW (2026-04-30 PM2) — Bulk select toggle + CSV export are tier-gated
  const flagBulkSelect  = useFeatureFlag("shipments_bulk_select");
  const flagCsvExport   = useFeatureFlag("csv_export_orders");
  const [items, setItems] = useState<Shipment[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("All");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [customFrom, setCustomFrom] = useState<Date | null>(null);
  const [customTo, setCustomTo] = useState<Date | null>(null);
  const [showDateModal, setShowDateModal] = useState(false);
  const [pickerField, setPickerField] = useState<"from" | "to" | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // perPage matches what the LabelViewer accepts so we can route every
  // option (A4, A6, Thermal 4×6, Barcode 2×1) through the same printer.
  type BulkPerPage = 1 | 2 | 4 | "thermal" | "barcode";
  // null = no layout chosen yet → action popup stays closed so first-time
  // users aren't lost. As soon as a layout is picked, a small popup opens
  // with Preview + Print buttons.
  const [bulkPerPage, setBulkPerPage] = useState<BulkPerPage | null>(null);
  // Controls the "Preview / Print" action popup that opens after a layout
  // is picked.
  const [actionPopupOpen, setActionPopupOpen] = useState(false);
  // Persist the last-used layout so we can show a "Last used" hint above
  // its card on the next bulk-print session.
  const LS_LAST_PERPAGE = "@bulk_last_perpage";
  const [lastUsedPerPage, setLastUsedPerPage] = useState<BulkPerPage | null>(null);

  // Phase-16 / P2 — Bulk Save Contacts. When the user taps "Save
  // Contacts" from the bulk bar, we open a small category-picker
  // sheet regardless of the auto_assign setting (per product spec:
  // the bulk flow should always let the user confirm / override
  // category for the whole batch without going to Settings).
  const [bulkContactPickerOpen, setBulkContactPickerOpen] = useState(false);
  const [bulkContactCats, setBulkContactCats] = useState<string[]>([]);
  const [bulkContactBusy, setBulkContactBusy] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(LS_LAST_PERPAGE)
      .then((v) => {
        if (!v) return;
        try {
          const parsed = JSON.parse(v);
          if (parsed === 1 || parsed === 2 || parsed === 4 ||
              parsed === "thermal" || parsed === "barcode") {
            setLastUsedPerPage(parsed);
          }
        } catch { /* ignore */ }
      })
      .catch(() => {});
  }, []);

  const persistLastUsedPerPage = (v: BulkPerPage) => {
    setLastUsedPerPage(v);
    AsyncStorage.setItem(LS_LAST_PERPAGE, JSON.stringify(v)).catch(() => {});
  };
  const [refreshing, setRefreshing] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  // Status picker state: when non-null, a bottom-sheet modal is shown for
  // changing this shipment's status to one of 7 values. The change is
  // written via PUT /shipments/:id → which propagates to the Master
  // Sheet via the Two-Way Status Sync we built.
  const [statusPickerShipment, setStatusPickerShipment] = useState<Shipment | null>(null);
  const [statusUpdating, setStatusUpdating] = useState(false);

  // Phase-11: Delivery-confirmation count — auto-flagged shipped
  // parcels that need a customer ping. Badge is lightweight (just
  // counts), loaded on focus alongside other lists.
  const [needConfirmCount, setNeedConfirmCount] = useState(0);
  const loadNeedConfirm = useCallback(async () => {
    try {
      const r = await Api.deliveryConfList(5);
      setNeedConfirmCount(r.counts?.list || 0);
    } catch {
      setNeedConfirmCount(0);
    }
  }, []);
  useEffect(() => { loadNeedConfirm(); }, [loadNeedConfirm]);

  const load = useCallback(async () => {
    // We fetch the FULL list (server-side text search still applies) and do
    // status + date filtering on the client so the 8-way filter — including
    // compound buckets like "Dispatch" that unify Pending+Dispatched —
    // works consistently without extra backend round-trips.
    const q: any = { search: search || undefined };
    try {
      const [list, s, cs] = await Promise.all([
        Api.listShipments(q), Api.getSettings(), Api.listCouriers(),
      ]);
      setItems(list);
      setSettings(s);
      setCouriers(cs);
    } catch (e: any) {
      // silently ignore transient failures so no global toast
      console.log("shipments load error:", e?.message || e);
    } finally {
      setRefreshing(false);
    }
  }, [search]);

  useFocusEffect(useCallback(() => {
    load().catch(() => {});
    loadNeedConfirm().catch(() => {});
  }, [load, loadNeedConfirm]));

  // Handle deep-link params from Dashboard quick actions.
  React.useEffect(() => {
    const st = String(params.status || "");
    if (STATUS_FILTER_ORDER.includes(st as StatusFilter)) {
      setStatus(st as StatusFilter);
    } else if (st === "Dispatched" || st === "Dispatch") {
      // Legacy alias: older deep-links used "Dispatch" / "Dispatched"
      // for the cream-coloured Ready-to-Ship tab.
      setStatus("Ready to Ship");
    }
    if (params.select === "1") {
      setSelectMode(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.status, params.select]);

  const findCourier = (s: Shipment) =>
    couriers.find((c) => c.id === s.courier_id) || null;

  const dateFilteredItems = useMemo(() => {
    // Client-side compound filter: status (8 tabs) + date range.
    const byStatus = status === "All"
      ? items
      : items.filter((s) => matchesStatusFilter(s.status || "", status));
    if (dateFilter === "all") return byStatus;
    if (dateFilter === "custom") {
      if (!customFrom && !customTo) return byStatus;
      const from = customFrom ? new Date(customFrom.getFullYear(), customFrom.getMonth(), customFrom.getDate()).getTime() : 0;
      const to = customTo ? new Date(customTo.getFullYear(), customTo.getMonth(), customTo.getDate(), 23, 59, 59, 999).getTime() : Number.MAX_SAFE_INTEGER;
      return byStatus.filter((s) => {
        const t = Date.parse(s.created_at || "");
        return !isNaN(t) && t >= from && t <= to;
      });
    }
    const now = Date.now();
    const cutoff =
      dateFilter === "today"
        ? now - 24 * 60 * 60 * 1000
        : dateFilter === "week"
        ? now - 7 * 24 * 60 * 60 * 1000
        : now - 30 * 24 * 60 * 60 * 1000;
    return byStatus.filter((s) => {
      const t = Date.parse(s.created_at || "");
      return !isNaN(t) && t >= cutoff;
    });
  }, [items, dateFilter, customFrom, customTo, status]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedIds(new Set(dateFilteredItems.map((i) => i.id)));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setSelectMode(false);
    setBulkPerPage(null);  // reset so next session starts at "choose layout"
  };

  const bulkPrint = async () => {
    if (selectedIds.size === 0 || !settings) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    if (!bulkPerPage) {
      Alert.alert("Choose layout", "Pick a print layout (Thermal / A4 / A6) first.");
      return;
    }
    try {
      const shipments = await Api.bulkFetch(Array.from(selectedIds));
      if (shipments.length === 0) return;
      const html = buildLabelHtml(shipments, settings.sender, {
        perPage: bulkPerPage,
        showSenderContact: settings.sender.show_contact,
        brand: settings.brand,
        preferLogo: (settings as any).prefer_logo !== false,
        logoShape: (settings as any).logo_shape === "wide" ? "wide" : "square",
        couriers,
        labelFields: (settings as any).label_fields,
        shipmentTagline: (settings as any).shipment_tagline,
        customFields: (settings as any).custom_fields,
      });
      const dims = pageDimensionsFor(bulkPerPage);
      await Print.printAsync({ html, ...(dims || {}) });
      // Remember this layout as the user's last-used choice.
      persistLastUsedPerPage(bulkPerPage);
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const bulkPreviewPdf = async () => {
    if (selectedIds.size === 0 || !settings) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    if (!bulkPerPage) {
      Alert.alert("Choose layout", "Pick a print layout (Thermal / A4 / A6) first.");
      return;
    }
    try {
      const shipments = await Api.bulkFetch(Array.from(selectedIds));
      const html = buildLabelHtml(shipments, settings.sender, {
        perPage: bulkPerPage,
        showSenderContact: settings.sender.show_contact,
        brand: settings.brand,
        preferLogo: (settings as any).prefer_logo !== false,
        logoShape: (settings as any).logo_shape === "wide" ? "wide" : "square",
        couriers,
        labelFields: (settings as any).label_fields,
        shipmentTagline: (settings as any).shipment_tagline,
        customFields: (settings as any).custom_fields,
      });
      const dims2 = pageDimensionsFor(bulkPerPage);
      const { uri } = await Print.printToFileAsync({ html, ...(dims2 || {}) });
      if (Platform.OS === "web" && typeof window !== "undefined") {
        window.open(uri, "_blank");
      } else if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, { mimeType: "application/pdf" });
      }
      // Remember this layout for next time.
      persistLastUsedPerPage(bulkPerPage);
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const toggleDelivered = async (s: Shipment) => {
    // Keeps legacy "quick mark" behaviour — toggle Delivered ↔ previous.
    // Users can get the full 8-status picker via the "⋮" chip tap.
    const prev = s.status || "Pending";
    const newStatus = prev === "Delivered" ? "Ready to Ship" : "Delivered";
    try {
      await Api.updateShipment(s.id, { status: newStatus });
    } catch (e: any) {
      if (isNetworkErrish(e)) {
        await SyncQueue.enqueueShipmentStatus(s.id, newStatus, s.tracking_id);
      } else {
        Alert.alert("Couldn't update", e?.response?.data?.detail || e?.message || "Try again");
      }
    }
    load();
  };

  // Open the full 8-status picker for a single shipment.
  const openStatusPicker = (s: Shipment) => {
    setStatusPickerShipment(s);
  };

  // Apply a new status, close the sheet, and refresh the list.
  const changeStatus = async (newStatus: string) => {
    if (!statusPickerShipment) return;
    setStatusUpdating(true);
    try {
      try {
        await Api.updateShipment(statusPickerShipment.id, { status: newStatus });
      } catch (e: any) {
        if (isNetworkErrish(e)) {
          await SyncQueue.enqueueShipmentStatus(
            statusPickerShipment.id,
            newStatus,
            statusPickerShipment.tracking_id,
          );
        } else {
          throw e;
        }
      }
      setStatusPickerShipment(null);
      await load();
    } catch (e: any) {
      Alert.alert("Couldn't update status", e?.message || "Network error");
    } finally {
      setStatusUpdating(false);
    }
  };

  // Phase-19 — Linear stage workflow. Tap the Next-Stage button on a
  // shipment card to advance one step instantly (no popup, no dropdown).
  // After Feedback the flow ends — no Next button shown for terminal
  // and side-branch stages (Modified / Cancel / Cancel by buyer /
  // Returned). For correction the Current-Stage button still opens
  // the manual picker via openStatusPicker.
  const NEXT_STAGE: Record<string, string> = {
    "Pending":       "Processing",
    "Processing":    "Ready to Ship",
    "Ready to Ship": "Shipped",
    "Dispatch":      "Shipped",          // legacy alias for Ready to Ship
    "Dispatched":    "Shipped",
    "Shipped":       "Delivered",
    "Delivered":     "Feedback",
  };
  const nextStageOf = (status: string): string => NEXT_STAGE[status || ""] || "";

  // Phase-19a — Resolve a stage label into its STATUS_META palette.
  // Mirrors the StatusChip lookup so the new wide stage-flow buttons
  // can pick up the same bg/fg per stage without hard-coding orange.
  // Falls back to the "Pending" cream palette for unknown values so
  // we never end up rendering a colourless ghost button.
  const lookupStatusMeta = (
    status: string,
  ): { value: string; label: string; bg: string; fg: string } => {
    const s = status || "";
    for (const [, meta] of Object.entries(STATUS_META)) {
      if (
        meta.value === s ||
        (meta.aliases && meta.aliases.includes(s))
      ) {
        return {
          value: meta.value,
          label: meta.label || meta.value,
          bg: meta.bg,
          fg: meta.fg,
        };
      }
    }
    return {
      value: s || "Pending",
      label: s || "Pending",
      bg: "#F8ECC2",
      fg: "#8B6B00",
    };
  };

  const [advancingId, setAdvancingId] = useState<string | null>(null);
  const advanceStage = async (ship: Shipment) => {
    const next = nextStageOf(ship.status || "");
    if (!next) return;
    setAdvancingId(ship.id);
    try {
      try {
        await Api.updateShipment(ship.id, { status: next });
      } catch (e: any) {
        if (isNetworkErrish(e)) {
          await SyncQueue.enqueueShipmentStatus(
            ship.id, next, ship.tracking_id,
          );
        } else {
          throw e;
        }
      }
      await load();
    } catch (e: any) {
      Alert.alert(
        "Couldn't advance stage",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setAdvancingId(null);
    }
  };

  // Live count per status filter — powers badge numbers on each tab.
  // Uses the already-loaded `items` so badges never lag the list.
  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = {
      "All": items.length,
      "Pending": 0, "Processing": 0, "Ready to Ship": 0, "Shipped": 0,
      "Delivered": 0, "Feedback": 0, "Modified": 0,
      "Cancel by buyer": 0, "Cancelled": 0, "Returned": 0,
    };
    for (const s of items) {
      const st = s.status || "";
      for (const f of STATUS_FILTER_ORDER) {
        if (f === "All") continue;
        if (matchesStatusFilter(st, f)) counts[f] += 1;
      }
    }
    return counts;
  }, [items]);

  const remove = (s: Shipment) => {
    // Explicit warning: sheet row is kept as an audit trail, not fully deleted.
    // Helps users understand that "Delete" is safe in a multi-user setup.
    const hasSheet = (s as any).sheet_row_num != null;
    const msg = hasSheet
      ? `Delete ${s.tracking_id} from the app?\n\nThe Master Sheet row will be marked "DELETED" (audit trail) — original data is never lost.`
      : `Delete ${s.tracking_id} from the app?`;
    Alert.alert("Delete shipment", msg, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          try {
            const res: any = await Api.deleteShipment(s.id);
            if (res?.sheet?.attempted && res.sheet.ok === false) {
              // Local delete succeeded but sheet mark failed; let user know.
              Alert.alert(
                "Deleted (sheet mark failed)",
                `Local record removed. Could not mark Master Sheet row as DELETED:\n${res.sheet.error || "unknown error"}`
              );
            }
          } catch (e: any) {
            if (isNetworkErrish(e)) {
              await SyncQueue.enqueueShipmentDelete(s.id, s.tracking_id);
              // Optimistic UX — let the user know it's queued.
              Alert.alert(
                "Queued for deletion",
                "We're offline — this shipment will be removed once you're back online.",
              );
            } else {
              Alert.alert("Delete error", e?.response?.data?.detail || e?.message || "Failed to delete");
            }
          }
          load();
        },
      },
    ]);
  };

  const sendWhatsApp = async (s: Shipment) => {
    if (!s.customer_phone) {
      Alert.alert("No phone", "Customer phone not set.");
      return;
    }
    const msg = buildWhatsAppText(s, settings, findCourier(s));
    // Phase-15 D: route through the daily-limit guard so the user gets
    // soft-warn / confirm / hard-block per admin policy, and the
    // server-side counter stays in sync across devices.
    await requestWhatsAppSend(s.customer_phone, msg, {
      templateLabel: "Shipped tracking message",
    });
  };

  const copyAll = async (s: Shipment) => {
    const text = buildCopyText(s, settings, findCourier(s));
    await Clipboard.setStringAsync(text);
    Alert.alert("Copied", "Tracking details copied to clipboard.");
  };

  // Phase-16: Save Contact. Flow:
  //   1. Pull per-user preferences + shipment into backend builder
  //   2. If settings.manual_popup is ON, prompt for category first
  //   3. Fire Android INSERT intent with the built payload; user
  //      confirms SAVE in the system contacts app.
  const handleSaveContact = async (ship: Shipment) => {
    if (!ship.customer_phone) {
      Alert.alert("No phone", "Customer phone is required to save as contact.");
      return;
    }
    try {
      // Phase-16.1 — Duplicate detection. We keep a tiny bookkeeping
      // record server-side every time the user fires the Save Contact
      // intent. On subsequent taps for the same phone we surface an
      // "Already saved" warning with Cancel / Save anyway so the user
      // can avoid creating accidental duplicates in their phonebook.
      // We don't block — Android's INSERT intent is fire-and-forget,
      // so the user may have canceled it last time. Save anyway is
      // always available.
      let alreadySaved: {
        saved_at?: string; name?: string;
      } | null = null;
      try {
        const r = await Api.checkContactSaved(ship.customer_phone);
        if (r?.saved) {
          alreadySaved = { saved_at: r.saved_at, name: r.name };
        }
      } catch {
        // If the check fails, fall through to the normal save flow
        // rather than blocking the user.
      }

      const cs = await Api.getContactSettings();
      const mustAskCategory =
        cs?.category?.manual_popup &&
        Array.isArray(cs?.category?.categories) &&
        cs.category.categories.length > 0;

      const doSave = async (overrideCategory: string) => {
        const built = await Api.buildOneContact({
          shipment_id: ship.id,
          override_category: overrideCategory || "",
        });
        const mod = await import("../../lib/contactSave");
        await mod.openSaveContactIntent({
          name:   built.name,
          phone:  built.phone,
          postal: built.postal,
          notes:  built.notes,
        });
        // Best-effort — record that the intent was fired so the next
        // tap can warn. We don't await failure here because the OS
        // intent has already gone out.
        Api.markContactSaved({
          phone: built.phone,
          name:  built.name,
          shipment_id: ship.id,
        }).catch(() => { /* ignore */ });
      };

      // Proceed straight to category picker (or auto-save) for fresh
      // numbers, or pop the "already saved" warning first.
      const proceedToCategoryFlow = () => {
        if (mustAskCategory) {
          const cats: string[] = cs.category.categories;
          Alert.alert(
            "Choose category",
            `Pick a category for "${ship.customer_name || "this contact"}".`,
            [
              ...cats.map((c) => ({ text: c, onPress: () => doSave(c) })),
              { text: "Cancel", style: "cancel" as const },
            ],
          );
        } else {
          doSave("");
        }
      };

      if (alreadySaved) {
        // Format the saved_at timestamp into a friendly "5 May 2026"
        // style string. Fall back gracefully when the ISO is missing.
        let when = "";
        if (alreadySaved.saved_at) {
          try {
            when = new Date(alreadySaved.saved_at).toLocaleDateString(
              undefined,
              { day: "numeric", month: "short", year: "numeric" },
            );
          } catch { /* ignore */ }
        }
        const who = alreadySaved.name || ship.customer_name || "This contact";
        Alert.alert(
          "Already saved",
          `${who} (${ship.customer_phone}) was saved${when ? ` on ${when}` : ""}. Save again?`,
          [
            { text: "Cancel", style: "cancel" as const },
            { text: "Save anyway", onPress: proceedToCategoryFlow },
          ],
        );
        return;
      }

      proceedToCategoryFlow();
    } catch (e: any) {
      Alert.alert(
        "Save Contact failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    }
  };

  // Phase-16 / P2 — open the bulk "Save Contacts" flow. Loads the
  // user's category list first so the picker can show real chips
  // (not hardcoded KSS/KOC). If the user has no categories yet, we
  // download straight without asking — there's nothing to choose.
  const openBulkContactPicker = async () => {
    if (selectedIds.size === 0) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    try {
      const cs = await Api.getContactSettings();
      const cats: string[] = Array.isArray(cs?.category?.categories)
        ? cs.category.categories
        : [];
      setBulkContactCats(cats);
      if (cats.length === 0) {
        // No categories configured → proceed with auto mapping.
        await runBulkContactDownload("");
        return;
      }
      setBulkContactPickerOpen(true);
    } catch (e: any) {
      Alert.alert(
        "Load settings failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    }
  };

  const runBulkContactDownload = async (overrideCategory: string) => {
    try {
      setBulkContactBusy(true);
      const ids = Array.from(selectedIds);
      const r = await Api.buildBulkVcf(ids, overrideCategory);
      const mod = await import("../../lib/contactSave");
      await mod.saveBulkVcf(r.vcf, `contacts_${ids.length}.vcf`);
      setBulkContactPickerOpen(false);
      Alert.alert(
        "Ready",
        `Prepared ${r.count} contact${r.count === 1 ? "" : "s"}${
          r.skipped ? ` (skipped ${r.skipped} without phone)` : ""
        }. Open the .vcf file to import into your Contacts app.`,
      );
    } catch (e: any) {
      Alert.alert(
        "Export failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setBulkContactBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Shipments</Text>
        <View style={{ flexDirection: "row", gap: 8 }}>
          {flagBulkSelect && flagBulkPrint ? (
            <TouchableOpacity
              testID="bulk-mode-toggle" style={[styles.iconBtn, selectMode && { backgroundColor: colors.primary, borderColor: colors.primary }]}
              onPress={() => {
                if (selectMode) clearSelection();
                else setSelectMode(true);
              }}
            >
              <PhIcon
                name={selectMode ? "close" : "checkbox-outline"}
                size={20}
                color={selectMode ? "#fff" : colors.text}
              />
            </TouchableOpacity>
          ) : null}
          {/* Phase-16/P2: Save-Contacts shortcut in the top bar. Only
              visible in multi-select mode — makes the bulk VCF export
              reachable WITHOUT having to scroll sideways through the
              print-layout cards (which was easy to miss). */}
          {selectMode && (
            <TouchableOpacity
              testID="bulk-save-contacts-header"
              style={[styles.iconBtn, { backgroundColor: "#7C3AED", borderColor: "#7C3AED" }]}
              onPress={openBulkContactPicker}
              disabled={selectedIds.size === 0}
            >
              <PhIcon name="person-add-outline" size={20} color="#fff" />
            </TouchableOpacity>
          )}
          {flagCsvExport && (
          <TouchableOpacity
            testID="export-csv-btn" style={styles.iconBtn}
            onPress={() => Linking.openURL(Api.csvUrl())}
          >
            <PhIcon name="download-outline" size={20} color={colors.text} />
          </TouchableOpacity>
          )}
        </View>
      </View>

      <View style={styles.searchWrap}>
        <PhIcon name="search" size={18} color={colors.textMuted} />
        <TextInput
          testID="search-input"
          placeholder="Search tracking, name, city, order..."
          placeholderTextColor="#9CA3AF"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={load}
          style={styles.searchInput}
          returnKeyType="search"
        />
      </View>

      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterRow}>
          {STATUS_FILTER_ORDER.map((f) => {
            const active = status === f;
            const count = statusCounts[f] || 0;
            const meta = f === "All" ? null : STATUS_META[f];
            // Phase-9: each status has its own active-color palette now
            // (Pending=BLACK, Dispatch=CREAM, Shipped=LAVENDER, Delivered=GREEN).
            // Fall back to the legacy dark pill only for the generic "All"
            // tab and any status that didn't override activeBg/activeFg.
            const activeBg = meta?.activeBg || "#111827";
            const activeFg = meta?.activeFg || "#FFFFFF";
            return (
              <TouchableOpacity
                key={f}
                testID={`filter-${f.toLowerCase().replace(/\s/g, "-")}`}
                style={[
                  styles.filterPill,
                  active && styles.filterPillActive,
                  active && { backgroundColor: activeBg, borderColor: activeBg },
                  // Tinted border when the bucket has a dedicated color.
                  meta && !active && { borderColor: meta.fg + "55" },
                ]}
                onPress={() => setStatus(f)}
              >
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[
                    styles.filterText,
                    { color: active ? activeFg : (meta ? meta.fg : colors.text) },
                  ]}
                >{meta?.label || f}</Text>
                <View
                  style={[
                    styles.filterCount,
                    {
                      backgroundColor: active
                        ? (activeBg === "#F4E3CF" || activeBg === "#EEE9FF" || activeBg === "#E6F7EE")
                          ? activeFg + "22"
                          : "rgba(255,255,255,0.25)"
                        : "#F3F4F6",
                    },
                  ]}
                >
                  <Text
                    allowFontScaling={false}
                    style={[
                      styles.filterCountText,
                      { color: active ? activeFg : colors.text },
                    ]}
                  >
                    {count}
                  </Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={[styles.filterRow, { paddingTop: 0 }]}>
          {([
            { key: "all", label: "All dates" },
            { key: "today", label: "Last 24h" },
            { key: "week", label: "Last 7 days" },
            { key: "month", label: "Last 30 days" },
          ] as const).map((f) => {
            const active = dateFilter === f.key;
            return (
              <TouchableOpacity
                key={f.key}
                testID={`datefilter-${f.key}`}
                onPress={() => setDateFilter(f.key)}
                style={[
                  styles.filterPill,
                  { borderColor: colors.primary },
                  active && { backgroundColor: colors.primary, borderColor: colors.primary },
                ]}
              >
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : colors.primary }]}
                >
                  {f.label}
                </Text>
              </TouchableOpacity>
            );
          })}
          {/* Custom date range pill */}
          {(() => {
            const active = dateFilter === "custom";
            const rangeLabel = (() => {
              if (!active) return "Custom";
              const fmt = (d: Date | null) =>
                d ? `${d.getDate()}/${d.getMonth() + 1}` : "…";
              return `${fmt(customFrom)} – ${fmt(customTo)}`;
            })();
            return (
              <TouchableOpacity
                testID="datefilter-custom"
                onPress={() => {
                  setDateFilter("custom");
                  setShowDateModal(true);
                }}
                style={[
                  styles.filterPill,
                  { borderColor: colors.primary, flexDirection: "row", gap: 4 },
                  active && { backgroundColor: colors.primary, borderColor: colors.primary },
                ]}
              >
                <PhIcon
                  name="calendar-outline"
                  size={13}
                  color={active ? "#fff" : colors.primary}
                />
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : colors.primary }]}
                >
                  {rangeLabel}
                </Text>
              </TouchableOpacity>
            );
          })()}
        </ScrollView>
      </View>

      {/* Phase-9 / Phase-11 removed (2026-05-12):
          Both the contextual "Scan & Ready to Ship" / "Scan to
          Shipped" card AND the "Need Delivery Confirmation" card
          have been moved out of the Shipments tab. The Home tab
          now owns these workflows — its existing scanner-shortcut
          rows were upgraded to match the visual interface that used
          to live here. Keeping Shipments focused on the search /
          filter / list-of-shipments job. */}

      {selectMode && (
        <View style={styles.bulkBar} testID="bulk-bar">
          {/* Left: count + Select-all link, stacked. */}
          <View style={styles.bulkBarLeft}>
            <Text style={styles.bulkCount}>
              {selectedIds.size} selected
            </Text>
            <TouchableOpacity testID="bulk-select-all" onPress={selectAllVisible} hitSlop={6}>
              <Text style={styles.bulkLink}>Select all</Text>
            </TouchableOpacity>
          </View>

          {/* Right: layout choice cards. Horizontally scrollable so the
              row never overlaps regardless of phone width. Tapping any
              card opens a small popup with Preview + Print buttons —
              keeps the bar minimal and the next step crystal clear. */}
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={{ gap: 6, alignItems: "flex-end" }}
            style={{ flexShrink: 1 }}
          >
            {([
              { k: "thermal" as BulkPerPage, top: "Thermal", sub: "(4×6)", icon: "print-outline" },
              { k: "barcode" as BulkPerPage, top: "Thermal", sub: "(2×1)", icon: "print-outline" },
              { k: 1 as BulkPerPage,         top: "A4",      sub: "",      icon: "document-outline" },
              { k: 4 as BulkPerPage,         top: "A6",      sub: "",      icon: "document-outline" },
            ]).map((opt) => {
              const active = bulkPerPage === opt.k;
              const isLastUsed = lastUsedPerPage === opt.k;
              return (
                <View key={String(opt.k)} style={{ alignItems: "center" }}>
                  {isLastUsed ? (
                    <Text style={styles.bulkLastUsedHint}>Last used</Text>
                  ) : (
                    <View style={{ height: 14 }} />
                  )}
                  <TouchableOpacity
                    testID={`bulk-layout-${opt.k}`}
                    onPress={() => {
                      // Pick the layout AND immediately open the action
                      // popup — one tap, the next step pops in front.
                      if (selectedIds.size === 0) {
                        Alert.alert("Select shipments", "Tap shipments to select first.");
                        return;
                      }
                      setBulkPerPage(opt.k);
                      setActionPopupOpen(true);
                    }}
                    style={[styles.bulkLayoutCard, active && styles.bulkLayoutCardActive]}
                  >
                    <PhIcon
                      name={opt.icon as any}
                      size={20}
                      color={active ? "#fff" : colors.primary}
                    />
                    <Text
                      style={[
                        styles.bulkLayoutTopText,
                        active && { color: "#fff" },
                      ]}
                    >
                      {opt.top}
                    </Text>
                    {opt.sub ? (
                      <Text
                        style={[
                          styles.bulkLayoutSubText,
                          active && { color: "#fff" },
                        ]}
                      >
                        {opt.sub}
                      </Text>
                    ) : null}
                  </TouchableOpacity>
                </View>
              );
            })}

            {/* Phase-16/P2: Save Contacts card — exports selected
                shipments to a .vcf file. Tapping it opens a small
                category-picker sheet (always, per spec), then hands
                the .vcf to the OS share sheet. */}
            <View style={{ alignItems: "center" }}>
              <View style={{ height: 14 }} />
              <TouchableOpacity
                testID="bulk-save-contacts"
                onPress={openBulkContactPicker}
                style={[
                  styles.bulkLayoutCard,
                  { borderColor: "#7C3AED", backgroundColor: "#F5F3FF" },
                ]}
              >
                <PhIcon
                  name="person-add-outline"
                  size={20}
                  color="#7C3AED"
                />
                <Text style={[styles.bulkLayoutTopText, { color: "#7C3AED" }]}>
                  Save
                </Text>
                <Text style={[styles.bulkLayoutSubText, { color: "#7C3AED" }]}>
                  Contacts
                </Text>
              </TouchableOpacity>
            </View>

            {/* Phase-12: Mark as Processing card — bulk flip selected
                rows from Pending → Processing. Pulses when the active
                tab is "Pending" to nudge the operator toward the new
                2-scan flow (Processing → Ready → Shipped). Rows with
                any other status are skipped server-side so it's safe
                to click anytime. */}
            <View style={{ alignItems: "center" }}>
              <View style={{ height: 14 }} />
              <TouchableOpacity
                testID="bulk-mark-processing"
                onPress={async () => {
                  if (selectedIds.size === 0) {
                    Alert.alert(
                      "Select shipments",
                      "Tick the parcels to move to Processing.",
                    );
                    return;
                  }
                  try {
                    const res = await Api.bulkMarkProcessing(
                      Array.from(selectedIds),
                    );
                    Alert.alert(
                      "Mark as Processing",
                      `${res.updated} moved to Processing.` +
                        (res.skipped
                          ? `\n${res.skipped} skipped (already past Pending).`
                          : "") +
                        (res.not_found
                          ? `\n${res.not_found} not found.`
                          : ""),
                    );
                    clearSelection();
                    fetchShipments();
                  } catch (e: any) {
                    Alert.alert(
                      "Error",
                      e?.response?.data?.detail ||
                        e?.message ||
                        "Could not update",
                    );
                  }
                }}
                style={[
                  styles.bulkLayoutCard,
                  {
                    borderColor: status === "Pending" ? "#F97316" : "#FCD34D",
                    backgroundColor:
                      status === "Pending" ? "#FFEDD5" : "#FEF3C7",
                  },
                ]}
              >
                <PhIcon
                  name="cube-outline"
                  size={20}
                  color={status === "Pending" ? "#C2410C" : "#92400E"}
                />
                <Text
                  style={[
                    styles.bulkLayoutTopText,
                    { color: status === "Pending" ? "#C2410C" : "#92400E" },
                  ]}
                >
                  Mark
                </Text>
                <Text
                  style={[
                    styles.bulkLayoutSubText,
                    { color: status === "Pending" ? "#C2410C" : "#92400E" },
                  ]}
                >
                  Processing
                </Text>
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>
      )}

      {/* Bulk Action Popup — opens after the user taps a layout card.
          Shows just two big buttons (Preview / Print) so the next step
          is impossible to miss. Backdrop tap or Cancel dismisses. */}
      <Modal
        visible={actionPopupOpen}
        animationType="fade"
        transparent
        onRequestClose={() => setActionPopupOpen(false)}
      >
        <TouchableOpacity
          activeOpacity={1}
          style={styles.bulkPopupBackdrop}
          onPress={() => setActionPopupOpen(false)}
        >
          <TouchableOpacity activeOpacity={1} style={styles.bulkPopupCard}>
            <View style={styles.bulkPopupHeaderRow}>
              <PhIcon name="print" size={18} color={colors.primary} />
              <Text style={styles.bulkPopupTitle}>
                {selectedIds.size} shipment{selectedIds.size !== 1 ? "s" : ""} •{" "}
                {bulkPerPage === "thermal" ? "Thermal 4×6" :
                 bulkPerPage === "barcode" ? "Thermal 2×1" :
                 bulkPerPage === 1 ? "A4" :
                 bulkPerPage === 4 ? "A6" :
                 bulkPerPage === 2 ? "½A4" : "Layout"}
              </Text>
              <TouchableOpacity onPress={() => setActionPopupOpen(false)} hitSlop={10}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <Text style={styles.bulkPopupSub}>
              Tap Preview to check the PDF before printing, or Print to send
              directly to your printer.
            </Text>
            <View style={styles.bulkPopupActions}>
              <TouchableOpacity
                testID="bulk-preview-btn"
                style={[styles.bulkPopupBtn, styles.bulkPopupBtnSecondary]}
                onPress={() => {
                  setActionPopupOpen(false);
                  bulkPreviewPdf();
                }}
              >
                <PhIcon name="eye-outline" size={20} color={colors.text} />
                <Text style={[styles.bulkPopupBtnText, { color: colors.text }]}>
                  Preview
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="bulk-print-btn"
                style={[styles.bulkPopupBtn, { backgroundColor: colors.primary }]}
                onPress={() => {
                  setActionPopupOpen(false);
                  bulkPrint();
                }}
              >
                <PhIcon name="print" size={20} color="#fff" />
                <Text style={styles.bulkPopupBtnText}>Print</Text>
              </TouchableOpacity>
            </View>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      {/* Phase-16/P2: Bulk Save-Contacts category picker. Opens after
          the user taps the "Save Contacts" card. Asks which category
          to apply to ALL selected contacts so they don't have to
          change the default in Settings every time. Includes an
          "Auto (by product)" chip for users who want the per-shipment
          mapping from their settings. */}
      <Modal
        visible={bulkContactPickerOpen}
        animationType="fade"
        transparent
        onRequestClose={() => setBulkContactPickerOpen(false)}
      >
        <TouchableOpacity
          activeOpacity={1}
          style={styles.bulkPopupBackdrop}
          onPress={() => !bulkContactBusy && setBulkContactPickerOpen(false)}
        >
          <TouchableOpacity activeOpacity={1} style={styles.bulkPopupCard}>
            <View style={styles.bulkPopupHeaderRow}>
              <PhIcon name="person-add" size={18} color="#7C3AED" />
              <Text style={styles.bulkPopupTitle}>
                Apply category to {selectedIds.size} contact
                {selectedIds.size !== 1 ? "s" : ""}
              </Text>
              <TouchableOpacity
                onPress={() => !bulkContactBusy && setBulkContactPickerOpen(false)}
                hitSlop={10}
                disabled={bulkContactBusy}
              >
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <Text
              style={{
                fontSize: 12, color: "#6B7280", marginHorizontal: 4, marginBottom: 10,
              }}
            >
              Pick one category for every contact in this batch, or tap
              "Auto" to use your Product → Category rules per shipment.
            </Text>
            <View
              style={{
                flexDirection: "row", flexWrap: "wrap", gap: 8,
                marginBottom: 12,
              }}
            >
              <TouchableOpacity
                testID="bulk-save-contacts-auto"
                disabled={bulkContactBusy}
                onPress={() => runBulkContactDownload("")}
                style={{
                  paddingHorizontal: 14, paddingVertical: 10,
                  backgroundColor: "#EDE9FE",
                  borderRadius: 8, borderWidth: 1, borderColor: "#C4B5FD",
                }}
              >
                <Text style={{ fontSize: 13, fontWeight: "800", color: "#5B21B6" }}>
                  Auto (by product)
                </Text>
              </TouchableOpacity>
              {bulkContactCats.map((c) => (
                <TouchableOpacity
                  key={c}
                  testID={`bulk-save-contacts-cat-${c}`}
                  disabled={bulkContactBusy}
                  onPress={() => runBulkContactDownload(c)}
                  style={{
                    paddingHorizontal: 14, paddingVertical: 10,
                    backgroundColor: "#7C3AED",
                    borderRadius: 8,
                  }}
                >
                  <Text style={{ fontSize: 13, fontWeight: "800", color: "#fff" }}>
                    {c}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
            {bulkContactBusy && (
              <View
                style={{
                  flexDirection: "row", alignItems: "center",
                  gap: 8, marginTop: 6,
                }}
              >
                <ActivityIndicator size="small" color="#7C3AED" />
                <Text style={{ fontSize: 12, color: "#7C3AED", fontWeight: "700" }}>
                  Preparing contacts… ({selectedIds.size})
                </Text>
              </View>
            )}
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>

      <FlatList
        testID="shipments-list"
        data={dateFilteredItems}
        keyExtractor={(i) => i.id}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => {
            setRefreshing(true); load();
          }} />
        }
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        ListEmptyComponent={
          <View style={styles.empty} testID="empty-shipments">
            <PhIcon name="cube-outline" size={48} color="#9CA3AF" />
            <Text style={styles.emptyText}>No shipments found.</Text>
            <TouchableOpacity
              testID="empty-new-shipment" style={styles.primaryBtn}
              onPress={() => router.push("/(tabs)/add")}
            >
              <Text style={styles.primaryBtnText}>+ New Shipment</Text>
            </TouchableOpacity>
          </View>
        }
        renderItem={({ item }) => {
          const isSelected = selectedIds.has(item.id);
          return (
          <View
            style={[styles.card, selectMode && isSelected && { borderColor: colors.primary, borderWidth: 2 }]}
            testID={`shipment-${item.tracking_id}`}
          >
            <TouchableOpacity
              style={{ flex: 1 }}
              onPress={() => {
                if (selectMode) toggleSelect(item.id);
                else router.push(`/shipment-details/${item.id}` as any);
              }}
              onLongPress={() => {
                setSelectMode(true);
                toggleSelect(item.id);
              }}
            >
              <View style={styles.row}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flex: 1 }}>
                  {selectMode && (
                    <PhIcon
                      name={isSelected ? "checkbox" : "square-outline"}
                      size={22}
                      color={isSelected ? colors.primary : colors.textMuted}
                    />
                  )}
                  <Text style={styles.track}>{item.tracking_id}</Text>
                </View>
                {/* Phase-20 — Replaced the top-right StatusChip with a
                    direct "Print Now" CTA. The current stage is already
                    surfaced (and tappable) via the wide stage-flow row
                    at the bottom of every card, so the chip up here was
                    redundant. Putting Print Now in this slot makes the
                    most-used action one-tap accessible without hunting
                    through the actions row. */}
                {flagPrint && !selectMode ? (
                  <TouchableOpacity
                    onPress={() => router.push(`/label/${item.id}`)}
                    activeOpacity={0.7}
                    style={styles.printNowBtn}
                    testID={`print-now-${item.tracking_id}`}
                    hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
                  >
                    <PhIcon name="print" size={14} color="#fff" />
                    <Text style={styles.printNowTxt}>Print Now</Text>
                  </TouchableOpacity>
                ) : null}
              </View>
              <Text style={styles.name}>{item.customer_name}</Text>
              {!!item.order_id && (
                <Text style={styles.order}>Order #{item.order_id}</Text>
              )}
              <Text style={styles.sub} numberOfLines={1}>
                {item.courier_name} · {item.city || "—"} · {
                  item.payment_mode === "COD"
                    ? `COD ₹${item.amount || item.cod_amount}`
                    : `Prepaid ₹${item.amount}`
                }
              </Text>
              {item.items && item.items.length > 0 && (
                <Text style={styles.items} numberOfLines={1}>
                  📦 {item.items.join(", ")}
                </Text>
              )}
              {/* Phase F2.2 — created_at timestamp (formatted compact)
                  so historical imports show their real-world date and
                  every shipment carries a glanceable "when" stamp. */}
              {!!item.created_at && (
                <Text style={styles.timestamp} numberOfLines={1}>
                  <PhIcon name="time-outline" size={11} color="#94A3B8" />
                  {"  "}{formatTimestamp(item.created_at)}
                </Text>
              )}
            </TouchableOpacity>
            {!selectMode && (
              <View style={styles.actions}>
                {flagMarkDeliv ? (
                  <ActionBtn
                    icon={item.status === "Delivered" ? "checkmark-done-circle" : "checkmark-circle-outline"}
                    color={item.status === "Delivered" ? colors.successText : colors.textMuted}
                    onPress={() => toggleDelivered(item)}
                    testID={`toggle-delivered-${item.tracking_id}`}
                  />
                ) : null}
                {flagCopy ? (
                  <ActionBtn
                    icon="copy-outline" color={colors.text}
                    onPress={() => copyAll(item)}
                    testID={`copy-all-${item.tracking_id}`}
                  />
                ) : null}
                {flagWhatsapp ? (
                  <ActionBtn
                    icon="logo-whatsapp" color="#25D366"
                    onPress={() => sendWhatsApp(item)}
                    testID={`whatsapp-${item.tracking_id}`}
                  />
                ) : null}
                {/* Phase-21 — Direct dial button. Always visible (no
                    plan gate) — calling the customer is a zero-cost
                    universal action. Tap opens the native dialer
                    with the shipment's phone pre-filled. Phone is
                    sanitised to digits-only (with optional leading +)
                    before passing to tel: so spaces/dashes from
                    pasted numbers don't break the intent on iOS. */}
                {item.customer_phone ? (
                  <ActionBtn
                    icon="call" color="#0EA5E9"
                    onPress={() => {
                      const cleaned = String(item.customer_phone || "")
                        .replace(/[^+\d]/g, "");
                      if (cleaned) {
                        Linking.openURL(`tel:${cleaned}`).catch(() => {});
                      }
                    }}
                    testID={`call-${item.tracking_id}`}
                  />
                ) : null}
                {flagEdit ? (
                  <ActionBtn
                    icon="create-outline" color="#2563EB"
                    onPress={() =>
                      router.push({
                        pathname: "/(tabs)/add",
                        params: { edit_id: item.id },
                      })
                    }
                    testID={`edit-${item.tracking_id}`}
                  />
                ) : null}
                {/* Phase-20 — Bottom printer ActionBtn removed. The
                    Print action is now surfaced in the top-right of
                    the card via the "Print Now" CTA so it's more
                    discoverable and one-tap accessible. */}
                {/* Phase-16: Save Contact — opens the native contact
                    INSERT intent with the shipment's info pre-filled.
                    Always visible (no plan gate) because it's a
                    zero-cost utility and a frequent request. */}
                <ActionBtn
                  icon="person-add-outline" color="#7C3AED"
                  onPress={() => handleSaveContact(item)}
                  testID={`save-contact-${item.tracking_id}`}
                />
                {flagDelete ? (
                  <ActionBtn
                    icon="trash-outline" color={colors.dangerText}
                    onPress={() => remove(item)}
                    testID={`delete-${item.tracking_id}`}
                  />
                ) : null}
              </View>
            )}

            {/* Phase-19a — Wide stage-flow row at the bottom of the
                card (per design ref 2026-05-12). Two equal-width
                pills:
                  Left  = CURRENT stage  (filled with stage colour +
                          chevron-down hint → tap opens manual picker)
                  Right = NEXT stage     (tinted background of the
                          target stage's colour + chevron-right → tap
                          instantly advances; hidden on terminal /
                          side-branch stages: Feedback / Modified /
                          Cancel / Cancel by buyer / Returned).
                The Next button no longer hard-codes orange — it
                inherits the destination stage's STATUS_META colours
                so each step keeps a consistent visual identity. */}
            {!selectMode && (() => {
              const next = nextStageOf(item.status || "");
              const isAdvancing = advancingId === item.id;

              const curMeta = lookupStatusMeta(item.status);
              const nextMeta = next ? lookupStatusMeta(next) : null;

              return (
                <View style={styles.stageRow}>
                  <TouchableOpacity
                    testID={`stage-current-${item.tracking_id}`}
                    onPress={() => openStatusPicker(item)}
                    activeOpacity={0.85}
                    style={[
                      styles.stagePill,
                      { backgroundColor: curMeta.bg, borderColor: curMeta.bg },
                    ]}
                  >
                    <Text
                      style={[styles.stagePillTxt, { color: curMeta.fg }]}
                      numberOfLines={1}
                      adjustsFontSizeToFit
                      minimumFontScale={0.85}
                      allowFontScaling={false}
                    >
                      {(curMeta.label || item.status || "Pending").toUpperCase()}
                    </Text>
                    <PhIcon
                      name="chevron-down"
                      size={14}
                      color={curMeta.fg}
                      style={{ marginLeft: 4 }}
                    />
                  </TouchableOpacity>
                  {nextMeta ? (
                    <TouchableOpacity
                      testID={`stage-next-${item.tracking_id}`}
                      onPress={() => advanceStage(item)}
                      disabled={isAdvancing}
                      activeOpacity={0.85}
                      style={[
                        styles.stagePill,
                        styles.stagePillOutline,
                        {
                          backgroundColor: nextMeta.bg + "33", // ~20 % alpha tint
                          borderColor: nextMeta.fg,
                        },
                        isAdvancing && { opacity: 0.5 },
                      ]}
                    >
                      {isAdvancing ? (
                        <ActivityIndicator size="small" color={nextMeta.fg} />
                      ) : (
                        <>
                          <Text
                            style={[styles.stagePillTxt, { color: nextMeta.fg }]}
                            numberOfLines={1}
                            adjustsFontSizeToFit
                            minimumFontScale={0.85}
                            allowFontScaling={false}
                          >
                            {(nextMeta.label || next).toUpperCase()}
                          </Text>
                          <PhIcon
                            name="chevron-forward"
                            size={14}
                            color={nextMeta.fg}
                            style={{ marginLeft: 4 }}
                          />
                        </>
                      )}
                    </TouchableOpacity>
                  ) : (
                    /* Workflow ended (Feedback / Modified / Cancel /
                        Cancel by buyer / Returned). Render an empty
                        spacer so the current-stage pill stays the
                        same width as on advance-able rows — keeps
                        the list visually tidy. */
                    <View style={[styles.stagePill, { backgroundColor: "transparent", borderColor: "transparent" }]} />
                  )}
                </View>
              );
            })()}
          </View>
          );
        }}
      />

      {/* Custom date range modal */}
      <Modal
        visible={showDateModal}
        transparent
        animationType="fade"
        onRequestClose={() => setShowDateModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.dateModalCard}>
            <View style={styles.dateModalHdr}>
              <PhIcon name="calendar" size={18} color={colors.primary} />
              <Text style={styles.dateModalTitle}>Custom Date Range</Text>
              <TouchableOpacity onPress={() => setShowDateModal(false)} hitSlop={10}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <Text style={styles.dateHint}>Select From &amp; To dates to filter shipments.</Text>

            <TouchableOpacity
              testID="picker-from"
              style={styles.dateField}
              onPress={() => setPickerField("from")}
            >
              <Text style={styles.dateFieldLabel}>From</Text>
              <Text style={styles.dateFieldValue}>
                {customFrom
                  ? customFrom.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
                  : "Tap to pick date"}
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="picker-to"
              style={styles.dateField}
              onPress={() => setPickerField("to")}
            >
              <Text style={styles.dateFieldLabel}>To</Text>
              <Text style={styles.dateFieldValue}>
                {customTo
                  ? customTo.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
                  : "Tap to pick date"}
              </Text>
            </TouchableOpacity>

            {pickerField && Platform.OS !== "web" && (
              <DateTimePicker
                value={(pickerField === "from" ? customFrom : customTo) || new Date()}
                mode="date"
                display={Platform.OS === "ios" ? "inline" : "default"}
                maximumDate={new Date()}
                onChange={(event: any, selected?: Date) => {
                  // On Android native dialog, it dismisses automatically.
                  if (Platform.OS === "android") setPickerField(null);
                  if (event?.type === "dismissed") return;
                  if (!selected) return;
                  if (pickerField === "from") setCustomFrom(selected);
                  else setCustomTo(selected);
                }}
              />
            )}

            <View style={styles.dateModalActions}>
              <TouchableOpacity
                style={styles.dateClearBtn}
                onPress={() => {
                  setCustomFrom(null);
                  setCustomTo(null);
                }}
              >
                <Text style={styles.dateClearText}>Clear</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="apply-date-range"
                style={styles.dateApplyBtn}
                onPress={() => {
                  setShowDateModal(false);
                  setPickerField(null);
                }}
              >
                <Text style={styles.dateApplyText}>Apply</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* ============================================================
          Status Picker — bottom sheet with the 7 status options.
          Triggered by tapping the status chip on any shipment card.
          Each selection fires PUT /shipments/{id} which auto-syncs
          to the Master Sheet via the Two-Way Status Sync.
          ============================================================ */}
      <Modal
        visible={!!statusPickerShipment}
        transparent
        animationType="fade"
        onRequestClose={() => !statusUpdating && setStatusPickerShipment(null)}
      >
        <TouchableOpacity
          activeOpacity={1}
          onPress={() => !statusUpdating && setStatusPickerShipment(null)}
          style={styles.statusSheetBackdrop}
        >
          <TouchableOpacity activeOpacity={1} style={styles.statusSheet} onPress={() => {}}>
            <View style={styles.statusSheetHandle} />
            <Text style={styles.statusSheetTitle}>Change status</Text>
            {statusPickerShipment && (
              <Text style={styles.statusSheetSub} numberOfLines={1}>
                {statusPickerShipment.tracking_id} · {statusPickerShipment.customer_name}
              </Text>
            )}
            <ScrollView style={{ marginTop: 4 }}>
              {STATUS_FILTER_ORDER.filter((f) => f !== "All").map((f) => {
                const meta = STATUS_META[f as Exclude<StatusFilter, "All">];
                const current = statusPickerShipment?.status || "";
                const selected =
                  current === meta.value ||
                  (meta.aliases && meta.aliases.includes(current));
                return (
                  <TouchableOpacity
                    key={f}
                    testID={`status-option-${f.toLowerCase().replace(/\s/g, "-")}`}
                    disabled={statusUpdating}
                    onPress={() => changeStatus(meta.value)}
                    style={[
                      styles.statusOptionRow,
                      selected && { borderColor: meta.fg, backgroundColor: meta.bg },
                    ]}
                  >
                    <View
                      style={[
                        styles.statusDot,
                        { backgroundColor: meta.fg },
                      ]}
                    />
                    <View style={{ flex: 1 }}>
                      <Text
                        style={[styles.statusOptionLabel, { color: meta.fg }]}
                      >
                        {meta.label || f}
                      </Text>
                      <Text style={styles.statusOptionHint} numberOfLines={1}>
                        Stored as "{meta.value}"
                        {meta.aliases && meta.aliases.length
                          ? ` · also matches ${meta.aliases.join(", ")}`
                          : ""}
                      </Text>
                    </View>
                    {selected && (
                      <PhIcon name="checkmark-circle" size={22} color={meta.fg} />
                    )}
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
            <TouchableOpacity
              onPress={() => !statusUpdating && setStatusPickerShipment(null)}
              style={styles.statusSheetCancel}
              testID="status-picker-cancel"
            >
              <Text style={styles.statusSheetCancelText}>
                {statusUpdating ? "Saving…" : "Cancel"}
              </Text>
            </TouchableOpacity>
          </TouchableOpacity>
        </TouchableOpacity>
      </Modal>
    </SafeAreaView>
  );
}

function ActionBtn({
  icon, color, onPress, testID,
}: {
  icon: string;
  color: string;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <TouchableOpacity testID={testID} onPress={onPress} style={styles.actionBtn}>
      <PhIcon name={icon} size={18} color={color} />
    </TouchableOpacity>
  );
}

function StatusChip({
  status,
  onPress,
}: {
  status: string;
  onPress?: () => void;
}) {
  // Walk STATUS_META to find a match (exact value OR one of its aliases).
  // Phase-12: prefer meta.label so legacy DB rows tagged "Dispatch" still
  // render as the user-facing "READY TO SHIP" badge.
  let bg = colors.warningBg;
  let fg = colors.warningText;
  let label = status || "Pending";
  for (const [, meta] of Object.entries(STATUS_META)) {
    if (meta.value === status || (meta.aliases && meta.aliases.includes(status))) {
      bg = meta.bg;
      fg = meta.fg;
      label = meta.label || meta.value;
      break;
    }
  }
  const content = (
    <View style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={[styles.chipText, { color: fg }]}>{label.toUpperCase()}</Text>
      {onPress && (
        <PhIcon
          name="chevron-down"
          size={11}
          color={fg}
          style={{ marginLeft: 2 }}
        />
      )}
    </View>
  );
  if (!onPress) return content;
  return (
    <TouchableOpacity onPress={onPress} activeOpacity={0.7} hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}>
      {content}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingTop: 10, paddingBottom: 8,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  iconBtn: {
    width: 40, height: 40, borderRadius: 10, borderWidth: 2,
    borderColor: "#E5E7EB", backgroundColor: "#fff",
    justifyContent: "center", alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16, marginTop: 4, flexDirection: "row",
    alignItems: "center", gap: 8, height: 46,
    backgroundColor: colors.surface, borderWidth: 2,
    borderColor: "#E5E7EB", borderRadius: 10, paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRowWrap: { flexGrow: 0, flexShrink: 0 },
  filterRow: { paddingHorizontal: 16, paddingVertical: 12 },
  filterPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    overflow: "visible",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 20,
    flexShrink: 0,
    backgroundColor: "#fff",
    marginRight: 8,
    minWidth: 64,
    justifyContent: "center",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },

  // Phase-9: "Scan & Ready to Ship" card — cream palette locked per spec.
  scanCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#F4E3CF",
    borderWidth: 1,
    borderColor: "#E6C9A8",
    borderRadius: 14,
    marginHorizontal: 12,
    marginTop: 2,
    marginBottom: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  scanCardIconBox: {
    width: 46,
    height: 46,
    borderRadius: 12,
    backgroundColor: "#FFF5EC",
    borderWidth: 1,
    borderColor: "#FFD9B8",
    alignItems: "center",
    justifyContent: "center",
  },
  scanCardTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: "#6B4220",
  },
  scanCardSub: {
    marginTop: 2,
    fontSize: 12,
    color: "#8B5E34",
    lineHeight: 16,
  },
  scanCardBtn: {
    backgroundColor: "#FF6B00",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 10,
    marginLeft: 10,
  },
  scanCardBtnText: {
    color: "#FFFFFF",
    fontWeight: "800",
    fontSize: 13,
  },

  // Phase-11: "Need Delivery Confirmation" card — soft orange palette
  // per spec (Warning / Pending tone). Full-card tap surface opens
  // /delivery-confirmation screen where admin can bulk-select +
  // WhatsApp + Mark as Delivered.
  confirmCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#FFF3E0",
    borderWidth: 1,
    borderColor: "#FCD7A0",
    borderRadius: 14,
    marginHorizontal: 12,
    marginTop: 2,
    marginBottom: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
  },
  confirmCardIconBox: {
    width: 42,
    height: 42,
    borderRadius: 11,
    backgroundColor: "#FFE5BA",
    borderWidth: 1,
    borderColor: "#FCD7A0",
    alignItems: "center",
    justifyContent: "center",
  },
  confirmCardTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: "#7C2D12",
  },
  confirmCardSub: {
    marginTop: 2,
    fontSize: 12,
    color: "#9A5F22",
  },
  confirmCardArrow: {
    marginLeft: 8,
    padding: 6,
  },
  // Empty-state variant: muted gray palette so the card stays visible
  // and informative without screaming for attention when there's
  // nothing to action on.
  confirmCardEmpty: {
    backgroundColor: "#F9FAFB",
    borderColor: "#E5E7EB",
  },
  confirmCardIconBoxEmpty: {
    backgroundColor: "#F3F4F6",
    borderColor: "#E5E7EB",
  },
  confirmCardTitleEmpty: {
    fontSize: 14,
    fontWeight: "700",
    color: "#6B7280",
  },
  confirmCardSubEmpty: {
    marginTop: 2,
    fontSize: 12,
    color: "#9CA3AF",
  },
  filterText: { fontWeight: "700", fontSize: 13, color: colors.text },
  filterCount: {
    minWidth: 22,
    paddingHorizontal: 6,
    height: 18,
    borderRadius: 9,
    alignItems: "center",
    justifyContent: "center",
  },
  filterCountText: { fontSize: 11, fontWeight: "800" },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  // Phase-20 — "Print Now" CTA pinned to the top-right corner of every
  // shipment card. Replaces the redundant status chip in that slot
  // since the current stage is already shown (and tappable) in the
  // wide stage-flow row at the bottom of the card. Surfacing Print
  // here makes it one-tap accessible without hunting through the
  // action-icons row underneath.
  printNowBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    backgroundColor: colors.primary,
  },
  printNowTxt: {
    color: "#fff",
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.3,
  },
  // shipment card. Two equal-width pills, the left filled with the
  // current stage's palette + a chevron-down hint, the right tinted
  // with the NEXT stage's palette + a chevron-right. Replaces the
  // earlier orange-outline `nextStageBtn` that was always orange
  // regardless of where the workflow was heading.
  stageRow: {
    flexDirection: "row",
    paddingHorizontal: 12,
    paddingTop: 4,
    paddingBottom: 12,
    gap: 10,
  },
  // Phase-22b (2026-05-17) — Responsive stage pills.
  //   • `flex: 1` keeps both buttons equal-width regardless of
  //     screen size (no fixed widths).
  //   • Lower horizontal padding + slightly smaller letterSpacing
  //     reclaims ~12 px so "READY TO SHIP" no longer truncates on
  //     360 dp phones. Touch-target stays >= 44 dp via minHeight.
  stagePill: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 8,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1.5,
    minHeight: 44,
    minWidth: 0,            // lets flex:1 children shrink inside row
  },
  stagePillOutline: {
    borderWidth: 1.5,
  },
  stageRow: {
    flexDirection: "row",
    paddingHorizontal: 10,
    paddingTop: 4,
    paddingBottom: 12,
    gap: 8,
  },
  // Phase-22b (2026-05-17) — Responsive stage pills.
  //   • `flex: 1` keeps both buttons equal-width regardless of
  //     screen size (no fixed widths).
  //   • Tight padding + smaller letterSpacing + 12 px font reclaims
  //     enough space so "READY TO SHIP" fits on 360 dp phones.
  //   • Touch-target preserved via minHeight: 44.
  stagePill: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 6,
    paddingVertical: 12,
    borderRadius: 8,
    borderWidth: 1.5,
    minHeight: 44,
    minWidth: 0,
  },
  stagePillOutline: {
    borderWidth: 1.5,
  },
  stagePillTxt: {
    fontSize: 12,
    fontWeight: "800",
    letterSpacing: 0.2,
    flexShrink: 1,
    textAlign: "center",
  },
  statusSheetBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  statusSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 8,
    paddingHorizontal: 16,
    paddingBottom: 18,
    maxHeight: "85%",
  },
  statusSheetHandle: {
    alignSelf: "center",
    width: 44,
    height: 4,
    borderRadius: 2,
    backgroundColor: "#E5E7EB",
    marginBottom: 10,
  },
  statusSheetTitle: {
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  statusSheetSub: {
    marginTop: 2,
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 8,
  },
  statusOptionRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 12,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    marginVertical: 4,
  },
  statusDot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  statusOptionLabel: {
    fontSize: 14.5,
    fontWeight: "800",
  },
  statusOptionHint: {
    marginTop: 1,
    fontSize: 11,
    color: colors.textMuted,
  },
  statusSheetCancel: {
    marginTop: 10,
    height: 46,
    borderRadius: 12,
    backgroundColor: "#F3F4F6",
    alignItems: "center",
    justifyContent: "center",
  },
  statusSheetCancelText: {
    fontWeight: "800",
    color: colors.text,
    fontSize: 14.5,
  },
  card: {
    backgroundColor: colors.surface, borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  track: {
    fontFamily: "Courier", fontWeight: "800", fontSize: 14,
    letterSpacing: 1.5, color: colors.text,
  },
  name: { marginTop: 6, fontSize: 15, fontWeight: "700", color: colors.text },
  order: { fontSize: 11, color: colors.primary, fontWeight: "700", marginTop: 2 },
  sub: { marginTop: 3, color: colors.textMuted, fontSize: 12 },
  items: { marginTop: 3, color: colors.text, fontSize: 12, fontWeight: "600" },
  // Phase F2.2 — Created-at timestamp shown discreetly under each card.
  timestamp: { marginTop: 4, color: "#94A3B8", fontSize: 11, fontWeight: "500" },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  actions: {
    flexDirection: "row", justifyContent: "flex-end", gap: 6,
    marginTop: 12, borderTopWidth: 1, borderTopColor: "#F1F1F1", paddingTop: 10,
  },
  actionBtn: {
    width: 36, height: 36, borderRadius: 8, backgroundColor: "#F9FAFB",
    borderWidth: 1, borderColor: "#E5E7EB",
    justifyContent: "center", alignItems: "center",
  },
  empty: {
    alignItems: "center", padding: 30, backgroundColor: colors.surface,
    borderRadius: 12, borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyText: { marginTop: 12, color: colors.textMuted, textAlign: "center" },
  primaryBtn: {
    marginTop: 16, backgroundColor: colors.primary,
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },
  bulkBar: {
    paddingVertical: 8,
    paddingLeft: 12,
    paddingRight: 8,
    backgroundColor: "#fff",
    borderTopWidth: 1,
    borderBottomWidth: 2,
    borderTopColor: "#E5E7EB",
    borderBottomColor: colors.primary,
    flexGrow: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  bulkBarLeft: {
    minWidth: 70,
    paddingRight: 6,
    borderRightWidth: 1,
    borderRightColor: "#E5E7EB",
    marginRight: 4,
  },
  bulkCount: { fontWeight: "800", color: colors.text, fontSize: 13 },
  bulkLink: { color: colors.primary, fontWeight: "700", fontSize: 12, marginTop: 2 },
  /** Each layout option is a small square card with icon + 1-2 text lines. */
  bulkLayoutCard: {
    width: 64,
    height: 64,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 4,
  },
  bulkLayoutCardActive: {
    backgroundColor: colors.secondary,
    borderColor: colors.secondary,
  },
  bulkLayoutTopText: {
    fontSize: 11,
    fontWeight: "800",
    color: colors.text,
    marginTop: 2,
  },
  bulkLayoutSubText: {
    fontSize: 9,
    fontWeight: "700",
    color: "#6B7280",
  },
  /** Tiny green hint shown above the layout the user picked last time —
      mirrors the courier-picker pattern so users have a familiar cue. */
  bulkLastUsedHint: {
    fontSize: 9,
    fontWeight: "800",
    color: "#10B981",
    marginBottom: 2,
    letterSpacing: 0.3,
  },
  /** Vertical divider between layout cards and the action buttons. */
  bulkSeparator: {
    width: 1,
    height: 48,
    backgroundColor: "#E5E7EB",
    marginHorizontal: 4,
    alignSelf: "center",
  },

  /* Bulk Action Popup — Preview / Print step opened after a layout pick. */
  bulkPopupBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 24,
  },
  bulkPopupCard: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 18,
    elevation: 8,
  },
  bulkPopupHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 6,
  },
  bulkPopupTitle: {
    flex: 1,
    fontSize: 15,
    fontWeight: "800",
    color: colors.text,
  },
  bulkPopupSub: {
    fontSize: 12,
    color: "#6B7280",
    lineHeight: 17,
    marginBottom: 14,
  },
  bulkPopupActions: {
    flexDirection: "row",
    gap: 10,
  },
  bulkPopupBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 14,
    borderRadius: 12,
  },
  bulkPopupBtnSecondary: {
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  bulkPopupBtnText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 14,
    letterSpacing: 0.3,
  },

  /* ----- Date Range Modal ----- */
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    alignItems: "center",
    padding: 16,
  },
  dateModalCard: {
    width: "100%",
    maxWidth: 400,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 18,
  },
  dateModalHdr: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  dateModalTitle: {
    flex: 1,
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  dateHint: { fontSize: 12, color: colors.textMuted, marginBottom: 12 },
  dateField: {
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 10,
  },
  dateFieldLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textMuted,
    marginBottom: 2,
  },
  dateFieldValue: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  dateModalActions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
  },
  dateClearBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    alignItems: "center",
    backgroundColor: "#fff",
  },
  dateClearText: { color: colors.text, fontWeight: "700" },
  dateApplyBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: colors.primary,
    alignItems: "center",
  },
  dateApplyText: { color: "#fff", fontWeight: "800" },
});
