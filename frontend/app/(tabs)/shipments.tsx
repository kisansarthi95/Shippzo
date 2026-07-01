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
import { fillFromShipment } from "../../lib/templateVariables";
import { useAuth } from "../../lib/auth";
import { buildLabelHtml, pageDimensionsFor } from "../../lib/label";
import { colors } from "../../lib/theme";
import { LabelsApi, ShipmentLabel, LABEL_ICON_MAP } from "../../lib/labels";
import LabelChip from "../../components/LabelChip";
import LabelPickerSheet from "../../components/LabelPickerSheet";
import { useFeatureFlag } from "../../lib/feature_flags";
import { requestWhatsAppSend } from "../../lib/whatsappGuard";
import DailyLimitBanner from "../../components/DailyLimitBanner";
import ConfirmCancelModal, {
  TerminalAction,
  isTerminalShipmentStatus,
} from "../../components/ConfirmCancelModal";
import SearchBar from "../../components/SearchBar";
import { openSaveContactIntent, saveBulkVcf } from "../../lib/contactSave";
import {
  generatePackingSummary,
  getPackingLangPref,
  PACKING_LANG_OPTIONS,
  type PackingLang,
} from "../../lib/packingSummary";

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
  const { user } = useAuth();
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
  // ── Label picker state (additive — does not touch selection/bulk flow)
  // labelPickerFor  = shipment.id whose labels the sheet is editing (null = closed)
  // labelDefs       = map<label_id → ShipmentLabel> for chip rendering on cards
  // shipmentLabels  = map<shipment.id → label_id[]> (mirror of shipment.labels)
  const [labelPickerFor, setLabelPickerFor] = useState<string | null>(null);
  const [labelDefs, setLabelDefs] = useState<Record<string, ShipmentLabel>>({});
  const [shipmentLabels, setShipmentLabels] = useState<Record<string, string[]>>({});
  // ── Phase F4.3 — Persistent Print Status state
  // MUST be declared BEFORE the `dateFilteredItems` useMemo below,
  // which reads `printFilter`. Previously declared further down and
  // hit a TDZ ReferenceError on first render → ErrorBoundary.
  const [advancingId, setAdvancingId] = useState<string | null>(null);
  const [pendingPrintConfirmId, setPendingPrintConfirmId] = useState<string | null>(null);
  const [printFilter, setPrintFilter] = useState<"All" | "Printed" | "Not Printed">("All");
  // ── Phase F4.6 — Label filter chip. "" (empty) = All; otherwise a
  // label id that shipments must carry in their labels[] array to
  // be visible. Piggybacks the existing dateFilteredItems memo so
  // it composes cleanly with status + date + print filters.
  const [labelFilter, setLabelFilter] = useState<string>("");
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

  // ─── Bulk WhatsApp Packing-Summary feature ─────────────────────────
  // Two-stage flow on top of the existing multi-select toolbar:
  //   1. User taps the WhatsApp icon → language picker modal opens.
  //   2. User picks a language → packing summary is generated client-
  //      side from already-loaded shipment data (no API call) and shown
  //      in a preview modal with Copy / WhatsApp / Share actions.
  // The default language is read from AsyncStorage (`@shippzo:packing_
  // language`) which is owned by the Settings → Packing Language card.
  const [packLangPickerOpen, setPackLangPickerOpen] = useState(false);
  const [packingDefault, setPackingDefault] = useState<PackingLang>("en");
  const [packSummaryOpen, setPackSummaryOpen] = useState(false);
  const [packSummaryText, setPackSummaryText] = useState("");
  const [packSummaryLang, setPackSummaryLang] = useState<PackingLang>("en");
  const [packGenBusy, setPackGenBusy] = useState(false);

  // Load the persisted default language on mount; we re-read it every
  // time the user lands on the screen so a change made from Settings
  // is reflected immediately without a full app reload.
  useEffect(() => {
    let cancelled = false;
    getPackingLangPref().then((lang) => {
      if (!cancelled) setPackingDefault(lang);
    });
    return () => { cancelled = true; };
  }, []);

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
  // Phase-33 — Cancel/Return confirmation modal. Holds the in-flight
  // terminal action (status flip OR the "cancel order" tick action).
  // When set, the ConfirmCancelModal renders; submitting it calls the
  // backend and clears this state.
  const [pendingTerminal, setPendingTerminal] = useState<
    | (TerminalAction & {
        shipmentId: string;
        targetStatus: "Cancelled" | "Cancel by buyer" | "Returned";
      })
    | null
  >(null);
  const [terminalSubmitting, setTerminalSubmitting] = useState(false);
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
      // Mirror shipment.labels into local state so chip render is O(1)
      // and instant-toggle in the picker doesn't need to refetch.
      const sm: Record<string, string[]> = {};
      list.forEach((sh: any) => {
        if (Array.isArray(sh.labels) && sh.labels.length) sm[sh.id] = sh.labels;
      });
      setShipmentLabels(sm);
    } catch (e: any) {
      // silently ignore transient failures so no global toast
      console.log("shipments load error:", e?.message || e);
    } finally {
      setRefreshing(false);
    }
  }, [search]);

  // Load the user's label definitions once (auto-seeds 10 defaults on
  // first visit via the backend). Refetched on focus so newly-created
  // labels from other flows show up.
  const loadLabels = useCallback(async () => {
    try {
      const arr = await LabelsApi.list();
      const map: Record<string, ShipmentLabel> = {};
      arr.forEach((l) => { map[l.id] = l; });
      setLabelDefs(map);
    } catch { /* non-fatal */ }
  }, []);

  useFocusEffect(useCallback(() => {
    load().catch(() => {});
    loadNeedConfirm().catch(() => {});
    loadLabels().catch(() => {});
  }, [load, loadNeedConfirm, loadLabels]));

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
    // Client-side compound filter: status (8 tabs) + date range +
    // print status (Phase F4.3) + label (Phase F4.6). All four combine
    // — an item must pass every filter to be visible.
    const byStatus = status === "All"
      ? items
      : items.filter((s) => matchesStatusFilter(s.status || "", status));
    const byPrint = printFilter === "All"
      ? byStatus
      : byStatus.filter((s) => {
          const isPrinted = (s.print_status || "") === "Printed";
          return printFilter === "Printed" ? isPrinted : !isPrinted;
        });
    // Phase F4.6 — Label filter. Empty labelFilter = show all. When
    // a label id is set, only shipments whose labels[] contains that
    // exact id survive. Uses the same `shipmentLabels` map that the
    // card row uses for chip rendering, so the filter set is
    // guaranteed to match what the user sees on each card.
    const byLabel = !labelFilter
      ? byPrint
      : byPrint.filter((s) => {
          const arr = shipmentLabels[s.id] || (s as any).labels || [];
          return Array.isArray(arr) && arr.includes(labelFilter);
        });
    if (dateFilter === "all") return byLabel;
    if (dateFilter === "custom") {
      if (!customFrom && !customTo) return byLabel;
      const from = customFrom ? new Date(customFrom.getFullYear(), customFrom.getMonth(), customFrom.getDate()).getTime() : 0;
      const to = customTo ? new Date(customTo.getFullYear(), customTo.getMonth(), customTo.getDate(), 23, 59, 59, 999).getTime() : Number.MAX_SAFE_INTEGER;
      return byLabel.filter((s) => {
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
    return byLabel.filter((s) => {
      const t = Date.parse(s.created_at || "");
      return !isNaN(t) && t >= cutoff;
    });
  }, [items, dateFilter, customFrom, customTo, status, printFilter, labelFilter, shipmentLabels]);

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
    // Capture the id list BEFORE the print — the OS print dialog can
    // take a while and by the time we come back the user may have
    // touched the selection, so we snapshot upfront and use this
    // exact list in the Confirm-Bulk-Print dialog below.
    const idsSnapshot = Array.from(selectedIds);
    try {
      const shipments = await Api.bulkFetch(idsSnapshot);
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

      // ── Phase F4.4 — ONE Confirm-Bulk-Print dialog after the
      //    entire batch completes. Unlike the single-print flow, we
      //    do NOT queue one confirmation per shipment; a single
      //    "Yes, All Printed" tap marks every selected shipment as
      //    Printed in one Promise.all fan-out.
      const batchCount = idsSnapshot.length;
      Alert.alert(
        "Confirm Bulk Print",
        `Did all ${batchCount} shipment ${batchCount === 1 ? "label" : "labels"} print successfully?`,
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Yes, All Printed",
            onPress: async () => {
              try {
                // Parallel fan-out — one PUT per shipment. Failures
                // are per-id and never block the rest of the batch;
                // a failed id simply stays "Not Printed" and the
                // user can retry from its card. `allSettled` gives
                // us the count of survivors for the summary toast.
                const results = await Promise.allSettled(
                  idsSnapshot.map((sid) => Api.setPrintStatus(sid, true)),
                );
                const okIds = new Set<string>();
                results.forEach((r, i) => {
                  if (r.status === "fulfilled") okIds.add(idsSnapshot[i]);
                });
                // Optimistic UI — flip the Printed state locally on
                // every successful id so cards turn green before the
                // background refetch reconciles.
                setItems((prev) => prev.map((row) =>
                  okIds.has(row.id)
                    ? { ...row, print_status: "Printed" as any }
                    : row,
                ));
                // Surface partial failures without blocking success.
                const failed = idsSnapshot.length - okIds.size;
                if (failed > 0) {
                  Alert.alert(
                    "Some shipments could not be marked",
                    `${okIds.size} marked as Printed, ${failed} failed. The failed ones will stay orange — retry from their cards.`,
                  );
                }
                load().catch(() => {});
              } catch (e: any) {
                Alert.alert(
                  "Couldn't save print status",
                  e?.response?.data?.detail || e?.message || "Try again.",
                );
              }
            },
          },
        ],
      );
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
    // Phase F4.2 — Same tracking-ID gate as the Next-Stage button.
    if (!guardTrackingForNextStatus(s, newStatus)) return;
    try {
      await Api.updateShipment(s.id, { status: newStatus });
    } catch (e: any) {
      if (
        e?.response?.status === 422 &&
        typeof e?.response?.data?.detail === "string" &&
        e.response.data.detail.startsWith("Tracking ID Required")
      ) {
        Alert.alert(
          "Tracking ID Required",
          "Please add the Courier Tracking ID before moving this shipment to the next stage.",
          [{ text: "OK" }],
        );
        return;
      }
      if (isNetworkErrish(e)) {
        await SyncQueue.enqueueShipmentStatus(s.id, newStatus, s.tracking_id);
      } else {
        Alert.alert("Couldn't update", e?.response?.data?.detail || e?.message || "Try again");
      }
    }
    load();
  };

  // Open the full 8-status picker for a single shipment.
  // Phase-33 — Terminal shipments are read-only; tapping the stage
  // pill opens a friendly toast instead of the picker. This keeps the
  // dead-state contract bullet-proof in the UI (the backend already
  // returns 423 on direct API calls).
  const openStatusPicker = (s: Shipment) => {
    if (isTerminalShipmentStatus(s.status)) {
      Alert.alert(
        "Order locked",
        "Cancelled / Returned orders cannot change status anymore. Only customer contact and view actions are available.",
      );
      return;
    }
    setStatusPickerShipment(s);
  };

  // Apply a new status, close the sheet, and refresh the list.
  // Phase-33 — Cancel / Cancel-by-buyer / Returned now flow through
  // a confirmation modal because the action is permanent.
  const changeStatus = async (newStatus: string) => {
    if (!statusPickerShipment) return;
    // Terminal-state targets need an explicit confirmation. Stage the
    // modal and let the user finalise via the Yes/Close buttons.
    if (isTerminalShipmentStatus(newStatus)) {
      const target = newStatus as "Cancelled" | "Cancel by buyer" | "Returned";
      const kind =
        target === "Cancel by buyer"
          ? "cancel_by_buyer"
          : target === "Returned"
          ? "returned"
          : "cancel";
      setPendingTerminal({
        kind,
        targetStatus: target,
        shipmentId: statusPickerShipment.id,
        orderLabel:
          (statusPickerShipment.tracking_id ||
            statusPickerShipment.customer_name ||
            ""),
      });
      // Close the status picker but keep the row reference until the
      // modal resolves so refresh below still finds the right row.
      setStatusPickerShipment(null);
      return;
    }
    setStatusUpdating(true);
    try {
      // Phase F4.2 — Same guard as the Next-Stage button. `Cancelled`
      // and `Returned` are already exempted inside the guard so they
      // continue to flow through the terminal-confirmation modal.
      if (!guardTrackingForNextStatus(statusPickerShipment, newStatus)) {
        setStatusUpdating(false);
        return;
      }
      try {
        await Api.updateShipment(statusPickerShipment.id, { status: newStatus });
      } catch (e: any) {
        if (
          e?.response?.status === 422 &&
          typeof e?.response?.data?.detail === "string" &&
          e.response.data.detail.startsWith("Tracking ID Required")
        ) {
          Alert.alert(
            "Tracking ID Required",
            "Please add the Courier Tracking ID before moving this shipment to the next stage.",
            [{ text: "OK" }],
          );
          return;
        }
        if (isNetworkErrish(e)) {
          await SyncQueue.enqueueShipmentStatus(
            statusPickerShipment.id,
            newStatus,
            statusPickerShipment.tracking_id,
          );
        } else if (e?.response?.status === 423) {
          Alert.alert(
            "Order locked",
            e?.response?.data?.detail ||
              "This order is permanently cancelled / returned.",
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

  // Phase-33 — Final submit for the Confirm-Cancel modal. Routes both
  // the trash/X "Cancel Order" tick AND the status-picker terminal
  // transitions to the right endpoint:
  //   - kind="delete"  → DELETE /shipments/{id}  (server flips to Cancelled)
  //   - other kinds   → PUT /shipments/{id} { status: target }
  const submitTerminal = useCallback(async () => {
    if (!pendingTerminal) return;
    setTerminalSubmitting(true);
    try {
      try {
        if (pendingTerminal.kind === "delete") {
          await Api.deleteShipment(pendingTerminal.shipmentId);
        } else {
          await Api.updateShipment(pendingTerminal.shipmentId, {
            status: pendingTerminal.targetStatus,
          });
        }
      } catch (e: any) {
        if (isNetworkErrish(e)) {
          if (pendingTerminal.kind === "delete") {
            await SyncQueue.enqueueShipmentDelete(
              pendingTerminal.shipmentId,
              pendingTerminal.orderLabel || "",
            );
          } else {
            await SyncQueue.enqueueShipmentStatus(
              pendingTerminal.shipmentId,
              pendingTerminal.targetStatus,
              pendingTerminal.orderLabel || "",
            );
          }
        } else if (e?.response?.status === 423) {
          Alert.alert(
            "Order locked",
            e?.response?.data?.detail ||
              "This order is permanently cancelled / returned.",
          );
        } else {
          throw e;
        }
      }
      setPendingTerminal(null);
      await load();
    } catch (e: any) {
      Alert.alert(
        "Couldn't cancel order",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setTerminalSubmitting(false);
    }
  }, [pendingTerminal]);

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

  // ── Phase F4.3 — Persistent Print Status ─────────────────────
  // useState hooks are hoisted to the top of the component (see
  // near `selectedIds`) so `printFilter` is available to the
  // `dateFilteredItems` useMemo above. Only the callbacks stay here.

  const onPrintButtonPress = useCallback((s: Shipment) => {
    // State 1 — no tracking id: keep the legacy Add-Tracking redirect.
    if (!s.tracking_id && !((s as any).manual_tracking_id)) {
      router.push(`/shipment-details/${s.id}` as any);
      return;
    }
    const isPrinted = (s.print_status || "") === "Printed";
    // State 3 — already printed: show Reprint dialog FIRST.
    if (isPrinted) {
      Alert.alert(
        "Reprint Shipment Label",
        "This shipment has already been marked as Printed.\nDo you want to print another copy of this label?",
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Reprint",
            onPress: () => {
              // Execute the existing print workflow. Do NOT queue a
              // Confirm-Print dialog on return; the button stays green.
              router.push(`/label/${s.id}`);
            },
          },
        ],
      );
      return;
    }
    // State 2 — ready to print. Kick the existing label preview /
    // print flow and queue the Confirm-Print dialog for when they
    // come back to this tab.
    setPendingPrintConfirmId(s.id);
    router.push(`/label/${s.id}`);
  }, [router]);

  // Show the "Confirm Print" alert when the user returns to the
  // Shipments tab after opening a label preview via the orange
  // Print Now button.
  useFocusEffect(useCallback(() => {
    if (!pendingPrintConfirmId) return;
    const sid = pendingPrintConfirmId;
    // Reset immediately so a second focus (e.g., quick re-tap) doesn't
    // re-fire the alert.
    setPendingPrintConfirmId(null);
    Alert.alert(
      "Confirm Print",
      "Did you successfully print this shipment label?",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Yes, Printed",
          onPress: async () => {
            try {
              await Api.setPrintStatus(sid, true);
              // Optimistic UI — flip the local row's print_status so
              // the button turns green without waiting for a refetch.
              setItems((prev) => prev.map((r) =>
                r.id === sid ? { ...r, print_status: "Printed" as any } : r,
              ));
              load().catch(() => {});
            } catch (e: any) {
              Alert.alert(
                "Couldn't save print status",
                e?.response?.data?.detail || e?.message || "Try again.",
              );
            }
          },
        },
      ],
    );
  }, [pendingPrintConfirmId, load]));

  // ─────────────────────────────────────────────────────────────
  // Phase F4.2 — Tracking-ID-required client-side gate.
  //
  // Mirrors the backend rule in shipments_write.py:
  //   A shipment MUST NOT move beyond Pending until a courier
  //   Tracking ID has been assigned. Frontend prevents the user
  //   action; backend rejects any bypass with HTTP 422.
  //
  // Returns true when the transition is allowed to proceed; false
  // AFTER showing the informative alert when it should be blocked.
  // ─────────────────────────────────────────────────────────────
  const STATUS_ALLOWED_WITHOUT_TRACKING = new Set<string>([
    "Pending", "Cancelled", "Cancel by buyer", "Returned",
  ]);
  const shipmentHasTracking = (s: Shipment | null | undefined): boolean => {
    if (!s) return false;
    return !!((s.tracking_id || "").trim() || ((s as any).manual_tracking_id || "").trim());
  };
  const guardTrackingForNextStatus = (
    s: Shipment | null | undefined,
    nextStatus: string,
  ): boolean => {
    if (!s || !nextStatus) return true;
    if (STATUS_ALLOWED_WITHOUT_TRACKING.has(nextStatus)) return true;
    if (shipmentHasTracking(s)) return true;
    Alert.alert(
      "Tracking ID Required",
      "Please add the Courier Tracking ID before moving this shipment to the next stage.",
      [{ text: "OK" }],
    );
    return false;
  };

  const advanceStage = async (ship: Shipment) => {
    const next = nextStageOf(ship.status || "");
    if (!next) return;
    // Phase F4.2 — Guard before we even set the loading state so the
    // button doesn't briefly grey-out on a blocked action.
    if (!guardTrackingForNextStatus(ship, next)) return;
    setAdvancingId(ship.id);
    try {
      try {
        await Api.updateShipment(ship.id, { status: next });
      } catch (e: any) {
        // Defence-in-depth: backend returned 422 tracking-required
        // even though the local guard let it through (e.g., cache
        // race after tracking was cleared). Surface the exact same
        // friendly dialog as the local check.
        if (
          e?.response?.status === 422 &&
          typeof e?.response?.data?.detail === "string" &&
          e.response.data.detail.startsWith("Tracking ID Required")
        ) {
          Alert.alert(
            "Tracking ID Required",
            "Please add the Courier Tracking ID before moving this shipment to the next stage.",
            [{ text: "OK" }],
          );
          return;
        }
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

  // Phase-33 — "Remove" no longer hard-deletes. The trash icon has
  // been replaced by an X "Cancel Order" tick that triggers the
  // shared ConfirmCancelModal. On confirm the shipment is flipped
  // to status="Cancelled" via DELETE /shipments/{id} (the server
  // re-purposes the legacy endpoint into a cancel-flip). Reads stay
  // intact — operators can still view the card and call/WhatsApp
  // the customer.
  const remove = (s: Shipment) => {
    if (isTerminalShipmentStatus(s.status)) {
      Alert.alert(
        "Already cancelled",
        "This order is permanently locked and cannot be cancelled again.",
      );
      return;
    }
    setPendingTerminal({
      kind: "delete",
      shipmentId: s.id,
      targetStatus: "Cancelled",
      orderLabel: s.tracking_id || s.customer_name || "",
    });
  };

  const sendWhatsApp = async (s: Shipment) => {
    if (!s.customer_phone) {
      Alert.alert("No phone", "Customer phone not set.");
      return;
    }
    // Phase-23 (2026-05-17) — WhatsApp template binding fix.
    // The older `buildWhatsAppText()` helper only knew about 6
    // variables, so customer-saved templates that used keys like
    // `{order_items}`, `{tracking_link}`, `{estimated_delivery}` got
    // sent as raw text. We now route through the canonical resolver
    // (`fillFromShipment`) so EVERY registered placeholder — and
    // every alias (`order_items` ↔ `items`, `courier_name` ↔ `courier`,
    // `tracking_link` ↔ `tracking_url`) — is replaced from this exact
    // shipment row. Unknown placeholders fall back to empty strings
    // courtesy of the underlying `fillTemplate()` (it strips any
    // `{xyz}` token whose key wasn't resolved). If the user hasn't
    // saved a personal template we fall back to the legacy
    // `buildWhatsAppText()` output so the message is still useful.
    const tpl = String((settings as any)?.whatsapp_template || "").trim();
    const msg = tpl
      ? fillFromShipment(tpl, s, settings, user, findCourier(s))
      : buildWhatsAppText(s, settings, findCourier(s));
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
        await openSaveContactIntent({
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
      await saveBulkVcf(r.vcf, `contacts_${ids.length}.vcf`);
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

  // ─── Bulk WhatsApp Packing-Summary handlers ───────────────────────
  //
  // openPackLangPicker — entry point from the toolbar's WhatsApp icon.
  // Validates that at least one shipment is selected, then opens the
  // language-picker modal. The actual summary is built only after a
  // language is chosen, so we can correctly drop the user back into
  // their last preference without doing wasted work up-front.
  const openPackLangPicker = () => {
    if (selectedIds.size === 0) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    setPackLangPickerOpen(true);
  };

  // generateAndShowSummary — builds the packing summary from already-
  // loaded shipment objects (no API call) and opens the preview modal.
  // We yield to the next tick before the heavy work so the language-
  // picker animation finishes and the user sees a brief spinner rather
  // than a frozen UI when 100+ shipments are selected.
  const generateAndShowSummary = (lang: PackingLang) => {
    setPackLangPickerOpen(false);
    setPackGenBusy(true);
    setPackSummaryLang(lang);
    setPackSummaryText("");
    setPackSummaryOpen(true);

    // Tiny defer so the modal frame paints before we run the join.
    setTimeout(() => {
      try {
        // Pick the in-memory shipments by selectedIds, preserving the
        // user's on-screen ordering so the packer reads the list in
        // the same order they see it.
        const selected = items.filter((it) => selectedIds.has(it.id));
        const text = generatePackingSummary(selected, lang);
        setPackSummaryText(text);
      } catch (e: any) {
        Alert.alert("Could not build summary", e?.message || "Try again.");
        setPackSummaryOpen(false);
      } finally {
        setPackGenBusy(false);
      }
    }, 50);
  };

  // Action handlers for the preview modal — Copy / WhatsApp / Share.
  // WhatsApp uses the OS deep-link with prefilled text; the user picks
  // any contact or group from inside WhatsApp itself.
  const copyPackingSummary = async () => {
    try {
      await Clipboard.setStringAsync(packSummaryText);
      Alert.alert("Copied", "Packing summary copied to clipboard.");
    } catch (e: any) {
      Alert.alert("Copy failed", e?.message || "Try again.");
    }
  };

  const whatsappPackingSummary = async () => {
    // No phone number — opens the contact picker inside WhatsApp.
    const url = `whatsapp://send?text=${encodeURIComponent(packSummaryText)}`;
    try {
      const supported = await Linking.canOpenURL(url);
      if (!supported) {
        // Fallback to the universal wa.me link which works in web
        // browsers and triggers the chooser on most platforms.
        await Linking.openURL(
          `https://wa.me/?text=${encodeURIComponent(packSummaryText)}`,
        );
        return;
      }
      await Linking.openURL(url);
    } catch (e: any) {
      Alert.alert("WhatsApp unavailable", e?.message || "Try Share instead.");
    }
  };

  const sharePackingSummary = async () => {
    try {
      // expo-sharing needs a file URL — but for plain text we can use
      // the native share-sheet via Linking on iOS, and fall back to
      // Clipboard + Share-intent on Android. The cleanest cross-
      // platform path is to write a temp .txt and share that.
      const tmpPath = `${require("expo-file-system").cacheDirectory}packing_${Date.now()}.txt`;
      const FS = require("expo-file-system");
      await FS.writeAsStringAsync(tmpPath, packSummaryText, {
        encoding: FS.EncodingType.UTF8,
      });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(tmpPath, {
          mimeType: "text/plain",
          dialogTitle: "Share packing summary",
          UTI: "public.plain-text",
        });
      } else {
        await Clipboard.setStringAsync(packSummaryText);
        Alert.alert("Copied", "Sharing not available — text copied instead.");
      }
    } catch (e: any) {
      Alert.alert("Share failed", e?.message || "Try Copy instead.");
    }
  };

  // ────────────────────────────────────────────────────────────────
  // 2026-05-25 — Authenticated CSV download for the user-side
  // "Export Shipments" pill (icon-bar). The previous implementation
  // used `Linking.openURL()` which strips the JWT bearer header,
  // so the browser tab showed {"detail":"Authentication required"}
  // instead of saving the file.
  //
  // We now pull the CSV body via the auth-aware axios client and
  // hand the bytes to a platform-appropriate save / share flow:
  //   • Web:    Blob URL + <a download="…"> click (browser save)
  //   • Native: write to cacheDirectory + open Share sheet
  //             (so the user can WhatsApp / e-mail / Drive the file
  //              just like any normal attachment).
  // Empty payload is treated as "nothing to export" so the user
  // doesn't get a 0-byte file silently.
  // ────────────────────────────────────────────────────────────────
  const [exportCsvBusy, setExportCsvBusy] = useState<boolean>(false);
  const handleExportCsv = async () => {
    if (exportCsvBusy) return;
    setExportCsvBusy(true);
    try {
      // 2026-05-25 — Filter-aware export. We send the EXACT ID list
      // that's currently visible on screen — `dateFilteredItems` is
      // the result of the compound (status + date) client-side
      // filter already running in dateFilteredItems useMemo. The
      // backend is told to export only those rows, so:
      //
      //   • Status="All" + Date="all"   → every shipment
      //   • Status="Pending"            → only Pending rows
      //   • Date="today" + Status="All" → only last-24h rows
      //   • Date="custom" 4 days        → only that window
      //   • Both filters combined       → AND-intersection
      //
      // No filters? `ids` is empty → API uses the legacy "all" path
      // (single bulk SELECT, same behaviour as before).
      const visibleIds = dateFilteredItems.map((s) => s.id).filter(Boolean);

      const csv = await Api.exportShipmentsCsv(
        visibleIds.length > 0 ? visibleIds : undefined,
      );
      if (!csv || csv.trim().split("\n").length <= 1) {
        Alert.alert("No data", "You don't have any shipments to export yet.");
        return;
      }
      // Filename pattern reflects the filter context so the user can
      // tell apart "shipments_pending_…" from "shipments_all_…" at a
      // glance in their Downloads folder.
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const filterTag =
        (status !== "All" ? `_${status.toLowerCase().replace(/\s+/g, "")}` : "") +
        (dateFilter !== "all" ? `_${dateFilter}` : "");
      const filename = `shipments${filterTag}_${stamp}.csv`;

      if (Platform.OS === "web") {
        const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
        const url  = URL.createObjectURL(blob);
        const a    = document.createElement("a");
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click();
        a.remove(); URL.revokeObjectURL(url);
      } else {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const FileSystem = require("expo-file-system/legacy");
        const path = `${FileSystem.cacheDirectory || ""}${filename}`;
        await FileSystem.writeAsStringAsync(path, csv, {
          encoding: FileSystem.EncodingType.UTF8,
        });
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(path, {
            mimeType: "text/csv",
            dialogTitle: "Export Shipments",
            UTI: "public.comma-separated-values-text",
          });
        } else {
          Alert.alert("Export ready", `Saved to ${path}`);
        }
      }
    } catch (e: any) {
      Alert.alert(
        "Export failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setExportCsvBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Shipments</Text>
        <View style={{ flexDirection: "row", gap: 8 }}>
          {/* Bulk WhatsApp Packing-Summary — ONLY in selection mode AND
              only when at least one row is checked. Renders FIRST in
              the selection-mode toolbar per product spec. Uses the
              official WhatsApp logo via Ionicons. */}
          {selectMode && selectedIds.size > 0 && (
            <TouchableOpacity
              testID="bulk-whatsapp-pack"
              style={[styles.iconBtn, { backgroundColor: "#25D366", borderColor: "#25D366" }]}
              onPress={openPackLangPicker}
            >
              <PhIcon name="logo-whatsapp" size={20} color="#fff" />
            </TouchableOpacity>
          )}
          {/* Bulk Download — visible in BOTH modes; sits before the
              Multi-Select toggle in normal mode and before Save-
              Contacts in selection mode. Always second in selection
              mode, always first in normal mode. */}
          {flagCsvExport && (
            <TouchableOpacity
              testID="export-csv-btn" style={styles.iconBtn}
              onPress={handleExportCsv}
            >
              <PhIcon name="download-outline" size={20} color={colors.text} />
            </TouchableOpacity>
          )}
          {/* Save-Contacts — selection-mode only, sits before Cancel. */}
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
          {/* Multi-Select toggle (normal mode) → flips to Cancel when
              selection mode is active. Always rendered last so it sits
              on the far right of the toolbar, matching the spec. */}
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
        </View>
      </View>

      <SearchBar
        testID="search"
        placeholder="Search tracking, name, city, order..."
        value={search}
        onChangeText={setSearch}
        onClear={() => {
          // Phase-32 one-tap clear UX:
          //   • wipe the search box (handled by SearchBar)
          //   • reset Status filter back to "All"
          //   • reset Date filter back to "all"
          //   • drop any custom-date window
          // List reloads automatically via the existing effect-on-state.
          setStatus("All");
          setDateFilter("all");
          setCustomFrom(null);
          setCustomTo(null);
        }}
        onSubmitEditing={load}
      />

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

      {/* Phase F4.3 — Print Status filter row. Sits below the date
          filters and combines with every other filter (status,
          labels, dates, search). Same pill styling as the date
          filters so it feels native to the existing bar. */}
      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={[styles.filterRow, { paddingTop: 0 }]}>
          {(["All", "Not Printed", "Printed"] as const).map((f) => {
            const active = printFilter === f;
            const accent = f === "Printed" ? "#10B981" : colors.primary;
            return (
              <TouchableOpacity
                key={f}
                testID={`printfilter-${f.toLowerCase().replace(/\s/g, "-")}`}
                onPress={() => setPrintFilter(f)}
                style={[
                  styles.filterPill,
                  { borderColor: accent, flexDirection: "row", gap: 4 },
                  active && { backgroundColor: accent, borderColor: accent },
                ]}
              >
                {f === "Printed" ? (
                  <PhIcon
                    name="checkmark"
                    size={12}
                    color={active ? "#fff" : accent}
                  />
                ) : null}
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : accent }]}
                >
                  {f}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      {/* Phase F4.6 — Label filter row. Empty state (no labels
          created yet) collapses entirely. Selecting a label filters
          the list to shipments whose labels[] array contains that
          label id. Chips render in the label's own color so the
          filter is instantly recognisable. */}
      {Object.keys(labelDefs).length > 0 && (
        <View style={styles.filterRowWrap}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}
            contentContainerStyle={[styles.filterRow, { paddingTop: 0 }]}>
            {(() => {
              const chips: React.ReactElement[] = [];
              chips.push(
                <TouchableOpacity
                  key="__all__"
                  testID="labelfilter-all"
                  onPress={() => setLabelFilter("")}
                  style={[
                    styles.filterPill,
                    { borderColor: colors.primary, flexDirection: "row", gap: 4 },
                    !labelFilter && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <PhIcon
                    name="pricetag"
                    size={12}
                    color={!labelFilter ? "#fff" : colors.primary}
                  />
                  <Text
                    numberOfLines={1}
                    allowFontScaling={false}
                    style={[styles.filterText, { color: !labelFilter ? "#fff" : colors.primary }]}
                  >
                    All Labels
                  </Text>
                </TouchableOpacity>
              );
              Object.values(labelDefs)
                .sort((a, b) => {
                  const kindOrder: Record<string, number> = { order: 0, priority: 1, custom: 2 };
                  const ka = kindOrder[a.kind] ?? 3;
                  const kb = kindOrder[b.kind] ?? 3;
                  if (ka !== kb) return ka - kb;
                  return a.name.localeCompare(b.name);
                })
                .forEach((l) => {
                  const active = labelFilter === l.id;
                  chips.push(
                    <TouchableOpacity
                      key={l.id}
                      testID={`labelfilter-${l.id}`}
                      onPress={() => setLabelFilter(active ? "" : l.id)}
                      style={[
                        styles.filterPill,
                        { borderColor: l.color, flexDirection: "row", gap: 4 },
                        active && { backgroundColor: l.color, borderColor: l.color },
                      ]}
                    >
                      <PhIcon
                        name={(LABEL_ICON_MAP[l.icon] || l.icon || "pricetag") as any}
                        size={12}
                        color={active ? "#fff" : l.color}
                      />
                      <Text
                        numberOfLines={1}
                        allowFontScaling={false}
                        style={[styles.filterText, { color: active ? "#fff" : l.color }]}
                      >
                        {l.name}
                      </Text>
                    </TouchableOpacity>
                  );
                });
              return chips;
            })()}
          </ScrollView>
        </View>
      )}

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
                  name="cube"
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
            <PhIcon name="cube" size={48} color="#9CA3AF" />
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
                  {item.tracking_id ? (
                    <Text style={styles.track}>{item.tracking_id}</Text>
                  ) : (
                    <TouchableOpacity
                      testID={`add-tracking-${item.id}`}
                      onPress={() =>
                        router.push(`/shipment-details/${item.id}` as any)
                      }
                      style={styles.trackingMissingPill}
                      activeOpacity={0.7}
                      hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
                    >
                      <PhIcon name="scan-outline" size={12} color="#92400E" />
                      <Text style={styles.trackingMissingPillTxt}>
                        Add Tracking ID first
                      </Text>
                    </TouchableOpacity>
                  )}
                </View>
                {/* Phase-20 — Replaced the top-right StatusChip with a
                    direct "Print Now" CTA.
                    Phase F4.3 — Now a 3-state button:
                      • disabled grey  → tracking_id empty
                      • orange         → ready to print (not printed yet)
                      • green + ✓      → already printed (opens Reprint dialog)
                    Sizing / padding / layout kept EXACTLY the same via
                    the shared `styles.printNowBtn`. */}
                {flagPrint && !selectMode ? (() => {
                  const isPrinted = (item.print_status || "") === "Printed";
                  const hasTracking = !!(item.tracking_id || (item as any).manual_tracking_id);
                  return (
                    <TouchableOpacity
                      onPress={() => onPrintButtonPress(item)}
                      activeOpacity={0.7}
                      style={[
                        styles.printNowBtn,
                        !hasTracking && styles.printNowBtnDisabled,
                        hasTracking && isPrinted && styles.printNowBtnPrinted,
                      ]}
                      testID={`print-now-${item.tracking_id || item.id}`}
                      hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
                    >
                      <PhIcon
                        name={isPrinted ? "checkmark" : "print"}
                        size={14}
                        color={
                          !hasTracking ? "#9CA3AF" : "#fff"
                        }
                      />
                      <Text
                        style={[
                          styles.printNowTxt,
                          !hasTracking && { color: "#6B7280" },
                        ]}
                      >
                        {isPrinted ? "Printed" : "Print Now"}
                      </Text>
                    </TouchableOpacity>
                  );
                })() : null}
              </View>
              <Text style={styles.name}>{item.customer_name}</Text>
              {!!item.order_id && (
                <Text style={styles.order}>Order #{item.order_id}</Text>
              )}
              <Text style={styles.sub} numberOfLines={1}>
                {item.courier_name} · {item.city || "—"} · {
                  // Phase-31 — COD list subtitle shows what the courier
                  // will collect (cod_amount = max(0, amount − token)),
                  // not the gross total. Prepaid rows still show
                  // `amount` since there's no COD balance to derive.
                  item.payment_mode === "COD"
                    ? `COD ₹${item.cod_amount ?? item.amount ?? 0}`
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
              {/* ── Phase F4.1 — Label row (additive, does NOT touch
                     any existing action button). Divider + "+" on the
                     right; chips render below when the shipment has
                     any assigned labels. Horizontal scroll when many. */}
              <View style={labelStyles.dividerRow}>
                <View style={labelStyles.divider} />
                <TouchableOpacity
                  onPress={() => setLabelPickerFor(item.id)}
                  hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                  style={labelStyles.plusBtn}
                  accessibilityLabel="Add label"
                >
                  <PhIcon name="add" size={16} color={colors.primary} />
                </TouchableOpacity>
              </View>
              {(shipmentLabels[item.id] || []).length > 0 && (
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  style={labelStyles.chipRow}
                  contentContainerStyle={{ paddingRight: 4 }}
                >
                  {(shipmentLabels[item.id] || []).map((lid) => {
                    const def = labelDefs[lid];
                    if (!def) return null;
                    return (
                      <LabelChip
                        key={lid}
                        label={def}
                        size="sm"
                        onRemove={() => {
                          const next = (shipmentLabels[item.id] || []).filter((x) => x !== lid);
                          setShipmentLabels((prev) => ({ ...prev, [item.id]: next }));
                          LabelsApi.setForShipment(item.id, next).catch(() => {});
                        }}
                      />
                    );
                  })}
                </ScrollView>
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
                    icon="copy" color={colors.text}
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
                {flagEdit && !isTerminalShipmentStatus(item.status) ? (
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
                {flagDelete && !isTerminalShipmentStatus(item.status) ? (
                  <ActionBtn
                    icon="close-circle-outline" color={colors.dangerText}
                    onPress={() => remove(item)}
                    testID={`cancel-order-${item.tracking_id}`}
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

      {/* Phase-33 — Reusable Cancel / Return / Cancel-by-buyer
          confirmation modal. Hosts:
            • Trash/X tap → kind="delete" → DELETE /shipments/{id}
            • Terminal status select → kind="cancel" / "cancel_by_buyer"
              / "returned" → PUT /shipments/{id} { status }
          Both routes converge on submitTerminal() so error handling,
          offline-queue fallback, and load() refresh logic stay in
          one place. The modal closes itself after a successful
          submit; the underlying card refreshes via load(). */}
      <ConfirmCancelModal
        action={pendingTerminal}
        loading={terminalSubmitting}
        onClose={() => !terminalSubmitting && setPendingTerminal(null)}
        onConfirm={submitTerminal}
      />

      {/* ─── Bulk WhatsApp Packing Summary — Language Picker ──────────
          Opens when the user taps the WhatsApp icon in the selection-
          mode toolbar. Defaults to the language saved in
          Settings → Packing Language (read on mount). Tapping any row
          immediately hands off to generateAndShowSummary which opens
          the preview modal. */}
      <Modal
        visible={packLangPickerOpen}
        animationType="fade"
        transparent
        onRequestClose={() => setPackLangPickerOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeaderRow}>
              <PhIcon name="logo-whatsapp" size={20} color="#25D366" />
              <Text style={styles.modalTitle}>Choose Language</Text>
            </View>
            <Text style={styles.modalSub}>
              {selectedIds.size} shipment{selectedIds.size === 1 ? "" : "s"} selected
            </Text>
            {PACKING_LANG_OPTIONS.map((opt) => {
              const isDefault = opt.code === packingDefault;
              return (
                <TouchableOpacity
                  key={opt.code}
                  testID={`pack-lang-${opt.code}`}
                  style={styles.langRow}
                  onPress={() => generateAndShowSummary(opt.code)}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.langTitle}>{opt.label}</Text>
                    <Text style={styles.langNative}>{opt.native}</Text>
                  </View>
                  {isDefault && (
                    <View style={styles.defaultBadge}>
                      <Text style={styles.defaultBadgeTxt}>Default</Text>
                    </View>
                  )}
                  <PhIcon name="chevron-forward" size={18} color={colors.textMuted} />
                </TouchableOpacity>
              );
            })}
            <TouchableOpacity
              style={styles.modalCloseBtn}
              onPress={() => setPackLangPickerOpen(false)}
            >
              <Text style={styles.modalCloseTxt}>Cancel</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* ─── Bulk WhatsApp Packing Summary — Preview & Share ─────────
          Built fully client-side from shipments already in `items`
          state — no extra API call. The text is plain ASCII (with a
          couple of unicode separators) so it copies cleanly into any
          chat app or print preview. */}
      <Modal
        visible={packSummaryOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPackSummaryOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { maxHeight: "85%" }]}>
            <View style={styles.modalHeaderRow}>
              <PhIcon name="cube" size={20} color={colors.primary} />
              <Text style={styles.modalTitle}>Packing Summary</Text>
              <View style={styles.langPill}>
                <Text style={styles.langPillTxt}>
                  {PACKING_LANG_OPTIONS.find((o) => o.code === packSummaryLang)?.native || "—"}
                </Text>
              </View>
            </View>

            {packGenBusy ? (
              <View style={styles.packBusy}>
                <ActivityIndicator color={colors.primary} />
                <Text style={styles.packBusyTxt}>
                  Generating {selectedIds.size} order{selectedIds.size === 1 ? "" : "s"}…
                </Text>
              </View>
            ) : (
              <ScrollView
                style={styles.packPreview}
                contentContainerStyle={{ paddingBottom: 8 }}
              >
                <Text style={styles.packPreviewTxt} selectable>
                  {packSummaryText}
                </Text>
              </ScrollView>
            )}

            <View style={styles.packActionsRow}>
              <TouchableOpacity
                testID="pack-copy"
                style={[styles.packActionBtn, { backgroundColor: "#0EA5E9" }]}
                onPress={copyPackingSummary}
                disabled={packGenBusy || !packSummaryText}
              >
                <PhIcon name="copy" size={16} color="#fff" />
                <Text style={styles.packActionTxt}>Copy</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="pack-whatsapp"
                style={[styles.packActionBtn, { backgroundColor: "#25D366" }]}
                onPress={whatsappPackingSummary}
                disabled={packGenBusy || !packSummaryText}
              >
                <PhIcon name="logo-whatsapp" size={16} color="#fff" />
                <Text style={styles.packActionTxt}>WhatsApp</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="pack-share"
                style={[styles.packActionBtn, { backgroundColor: "#7C3AED" }]}
                onPress={sharePackingSummary}
                disabled={packGenBusy || !packSummaryText}
              >
                <PhIcon name="share-social" size={16} color="#fff" />
                <Text style={styles.packActionTxt}>Share</Text>
              </TouchableOpacity>
            </View>

            <TouchableOpacity
              style={styles.modalCloseBtn}
              onPress={() => setPackSummaryOpen(false)}
            >
              <Text style={styles.modalCloseTxt}>Close</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
      {/* ── Phase F4.1 — Shipment Label picker (single bottom-sheet
             reused for every card via labelPickerFor === card.id). */}
      <LabelPickerSheet
        visible={!!labelPickerFor}
        selectedIds={labelPickerFor ? (shipmentLabels[labelPickerFor] || []) : []}
        onClose={() => setLabelPickerFor(null)}
        onApply={(ids) => {
          if (!labelPickerFor) return;
          // Optimistic UI — chip row updates instantly; PUT flies to
          // backend in the background. Failure is swallowed to avoid
          // interrupting the toggle interaction.
          const sid = labelPickerFor;
          setShipmentLabels((prev) => ({ ...prev, [sid]: ids }));
          LabelsApi.setForShipment(sid, ids).catch(() => {});
          // Refresh label defs so a just-created label's chip shows up.
          loadLabels().catch(() => {});
        }}
      />
    </SafeAreaView>
  );
}

// ── Phase F4.1 — Label section styles (kept separate from `styles`
// so a future refactor can move them into a shared module without
// touching the giant existing stylesheet).
const labelStyles = StyleSheet.create({
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
  // Phase-25 — Tracking-ID Gate. When a shipment has no tracking
  // we replace the mono tracking text with this amber pill that
  // routes to /shipment-details where the user can scan/type.
  printNowBtnDisabled: {
    backgroundColor: "#E5E7EB",
  },
  // Phase F4.3 — "Printed" state. Same width/padding/text-style as
  // the orange button so the card layout doesn't shift when a
  // shipment transitions from Not Printed → Printed. Only the
  // background color + leading icon change (see JSX above).
  printNowBtnPrinted: {
    backgroundColor: "#10B981",
  },
  trackingMissingPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    backgroundColor: "#FEF3C7",
    borderWidth: 1,
    borderColor: "#FCD34D",
  },
  trackingMissingPillTxt: {
    color: "#92400E",
    fontSize: 11,
    fontWeight: "800",
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

  // ─── Bulk WhatsApp Packing Summary modal styles ──────────────────
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "center",
    alignItems: "center",
    padding: 20,
  },
  modalCard: {
    width: "100%",
    maxWidth: 480,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 16,
  },
  modalHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginBottom: 4,
  },
  modalTitle: {
    flex: 1,
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  modalSub: {
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 12,
  },
  langRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 14,
    paddingHorizontal: 12,
    borderRadius: 12,
    backgroundColor: "#F8FAFC",
    borderWidth: 1,
    borderColor: "#E2E8F0",
    marginTop: 8,
  },
  langTitle: { fontSize: 15, fontWeight: "800", color: colors.text },
  langNative: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  defaultBadge: {
    backgroundColor: "#16A34A",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  defaultBadgeTxt: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 10,
    letterSpacing: 0.4,
  },
  modalCloseBtn: {
    marginTop: 12,
    paddingVertical: 12,
    alignItems: "center",
    borderRadius: 10,
    backgroundColor: "#F1F5F9",
  },
  modalCloseTxt: { color: "#475569", fontWeight: "800", fontSize: 13 },
  langPill: {
    backgroundColor: "#EEF2FF",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
  },
  langPillTxt: {
    fontSize: 11,
    fontWeight: "800",
    color: "#4338CA",
  },
  packPreview: {
    backgroundColor: "#F8FAFC",
    borderWidth: 1,
    borderColor: "#E2E8F0",
    borderRadius: 12,
    padding: 12,
    marginTop: 10,
    maxHeight: 380,
  },
  packPreviewTxt: {
    fontSize: 13,
    lineHeight: 19,
    color: colors.text,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
  },
  packBusy: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    padding: 40,
  },
  packBusyTxt: { color: colors.textMuted, fontSize: 13, fontWeight: "700" },
  packActionsRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 12,
  },
  packActionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 44,
    borderRadius: 10,
  },
  packActionTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },
});
