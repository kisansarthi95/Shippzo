import React, { useCallback, useMemo, useState, useEffect } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  FlatList, RefreshControl, Linking, Alert, Platform, Modal, ToastAndroid,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Clipboard from "expo-clipboard";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useFocusEffect, useRouter, useLocalSearchParams } from "expo-router";
// DateTimePicker moved to `components/shipments/DateRangeModal.tsx`
// (Phase F4.6). No longer imported here.
import { Api, Shipment, Settings, Courier } from "../../lib/api";
import { SyncQueue } from "../../lib/syncQueue";
import {
  StatusFilter,
  DateFilter,
  STATUS_META,
  STATUS_FILTER_ORDER,
  matchesStatusFilter,
  formatTimestamp,
  isNetworkErrish,
  labelStyles,
} from "../../components/shipments/status_meta";
import ActionBtn from "../../components/shipments/ActionBtn";
import DateRangeModal from "../../components/shipments/DateRangeModal";
import BulkPrintActionModal from "../../components/shipments/BulkPrintActionModal";
import { buildCopyText, buildWhatsAppText, cleanPhone } from "../../lib/format";
import { fillFromShipment } from "../../lib/templateVariables";
import { useAuth } from "../../lib/auth";
import { buildLabelHtml, pageDimensionsFor } from "../../lib/label";
import { colors } from "../../lib/theme";
import { LabelsApi, ShipmentLabel, LABEL_ICON_MAP } from "../../lib/labels";
import LabelChip from "../../components/LabelChip";
import LabelPickerSheet from "../../components/LabelPickerSheet";
import QuickPickerSheet, { QuickPickerOption } from "../../components/QuickPickerSheet";
import ShipmentsFilterSheet from "../../components/ShipmentsFilterSheet";
import BatchPickerSheet from "../../components/BatchPickerSheet";
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

// Phase F6.4 — Last Event category → badge visual. Mirrors the
// server-side classify_last_event() output. Values kept to short
// labels so the top-right slot on the Shipments card stays compact.
const LAST_EVENT_BADGE_META: Record<string, { bg: string; fg: string; icon: string }> = {
  "Delivered":        { bg: "#DCFCE7", fg: "#166534", icon: "checkmark-circle" },
  "Out for Delivery": { bg: "#DBEAFE", fg: "#1E40AF", icon: "bicycle" },
  "Hold":             { bg: "#FEF3C7", fg: "#92400E", icon: "pause-circle" },
  "Redirected":       { bg: "#EDE9FE", fg: "#5B21B6", icon: "shuffle" },
  "Returned":         { bg: "#FEE2E2", fg: "#991B1B", icon: "return-up-back" },
  "Dispatched":       { bg: "#E0F2FE", fg: "#075985", icon: "airplane" },
  "Received":         { bg: "#F1F5F9", fg: "#334155", icon: "cube" },
  "Bagged":           { bg: "#F1F5F9", fg: "#334155", icon: "briefcase" },
  "Other":            { bg: "#F1F5F9", fg: "#334155", icon: "ellipsis-horizontal" },
};

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
  // ── Phase A — Quick Filter dropdown chips + Filter Bottom Sheet
  // paymentFilter: "" = All | "COD" | "Prepaid" (Set covers both)
  // courierFilter: "" = All | courier_id
  // quickPicker:   which dropdown chip's picker sheet is open
  // filterSheetOpen: main Filters bottom sheet open flag
  const [paymentFilter, setPaymentFilter] = useState<Set<string>>(new Set());
  const [courierFilter, setCourierFilter] = useState<string>("");
  const [quickPicker, setQuickPicker] = useState<"print" | "label" | "courier" | null>(null);
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);

  // Phase F6.3 — Shipment Import filter states. All are client-side
  // except importBatchPick / paymentBatchPick which are passed as
  // server query params (indexed Mongo lookup).
  const [importStatus, setImportStatus]         = useState<"all" | "imported" | "not_imported">("all");
  const [importTypes, setImportTypes]           = useState<Set<"booking" | "delivery" | "cod_payment">>(new Set());
  const [bookingFilter, setBookingFilter]       = useState<Set<"imported" | "pending">>(new Set());
  const [deliveryFilter, setDeliveryFilter]     = useState<Set<"imported" | "pending" | "confirmed">>(new Set());
  const [codPaymentFilter, setCodPaymentFilter] = useState<Set<"received" | "pending" | "amount_mismatch">>(new Set());
  const [validationFilter, setValidationFilter] = useState<Set<"weight" | "payment_mode" | "amount">>(new Set());
  const [importBatchPick, setImportBatchPick]   = useState<{ id: string; label: string; sub?: string } | null>(null);
  const [paymentBatchPick, setPaymentBatchPick] = useState<{ id: string; label: string; sub?: string } | null>(null);
  const [importBatchPickerOpen, setImportBatchPickerOpen]   = useState(false);
  const [paymentBatchPickerOpen, setPaymentBatchPickerOpen] = useState(false);
  // Phase F7.1 (Jun-2026) — India Post Complaint STATUS filter
  // (replaces the earlier "created / not_created" pair). Selecting
  // ANY status also acts as the trigger for the India Post bulk
  // complaint export: `handleExportCsv` routes to the CBS-format
  // Excel path whenever `complaintFilter.size > 0`.
  const [complaintFilter, setComplaintFilter] = useState<Set<"Open" | "In Progress" | "Resolved" | "Closed">>(new Set());

  // Phase F6.6 — "Suggested Filters" section was permanently removed
  // from the Filter Bottom Sheet at user request. The product-suggestion
  // state and its `/api/shipments/product-suggestions` fetch have been
  // deleted to eliminate the round-trip on every list change.

  // Phase B — when the "+ Create new label" button in the Filter
  // Bottom Sheet is tapped we open LabelPickerSheet in a special
  // "create-mode-only" state.  We reuse the existing sheet by
  // pointing labelPickerFor at a synthetic sentinel id so the sheet
  // opens but no shipment is affected.
  const [labelCreateOpen, setLabelCreateOpen] = useState(false);
  // ── Phase F4.8 — Contact-saved visual indicator ────────────────
  // Set of shipment ids that were successfully saved as contacts in
  // this session (single tap OR bulk vcf). UI-only — the "Already
  // Saved" server-side check remains completely unchanged; this
  // state only flips the per-card save button to a filled-green
  // Person✓ style so the operator can see at a glance which cards
  // have been actioned in the current session.
  const [contactSavedIds, setContactSavedIds] = useState<Set<string>>(new Set());
  const markContactsSaved = useCallback((ids: string[]) => {
    if (!ids || ids.length === 0) return;
    setContactSavedIds((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.add(id));
      return next;
    });
  }, []);
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
    // Phase F6.3 — Import Batch / Payment Batch drill-downs go
    // server-side because they translate to indexed Mongo queries
    // (`import_batch_ids: <id>` / `payment_batch_id: <id>`). Every
    // other new filter (Import Status / Type / validation / booking /
    // delivery / cod payment sub-filters) is applied CLIENT-SIDE so
    // it composes with the existing tab / date / label filters.
    if (importBatchPick?.id)  q.import_batch_id = importBatchPick.id;
    if (paymentBatchPick?.id) q.payment_batch_id = paymentBatchPick.id;
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
      // ── Rehydrate contact-saved icon set from the backend so the
      // green Person✓ pill persists across refreshes and focus
      // returns. Single batch query keeps this cheap regardless of
      // page size. Fire-and-forget — a failure here just means the
      // icon reverts to grey and the user can re-tap Save Contact.
      const pairs = list
        .map((sh: any) => ({
          shipment_id: sh.id,
          phone: String(sh.customer_phone || ""),
        }))
        .filter((p) => p.phone.length > 0);
      if (pairs.length > 0) {
        Api.batchCheckContactSaved(pairs)
          .then((resp) => {
            const savedIds = (resp.results || [])
              .filter((r) => r.saved)
              .map((r) => r.shipment_id);
            setContactSavedIds((prev) => {
              // Union of server-truth + in-session flips so any
              // taps that landed between fetch start and response
              // (or that haven't yet hit /contacts/mark-saved) are
              // preserved.
              const next = new Set(prev);
              savedIds.forEach((id) => next.add(id));
              // Also clear any stale in-session ids for shipments
              // that are no longer in the current page.
              const listedIds = new Set(list.map((sh: any) => sh.id));
              return new Set(
                Array.from(next).filter((id) => listedIds.has(id)),
              );
            });
          })
          .catch(() => { /* non-fatal — icon just stays grey */ });
      }
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
    // Phase F6.7 — the "Out for Delivery" tab is the Rule-5 catch-all:
    // shipments with a status that matches no other tab must still be
    // visible somewhere. We route them here so they can be actioned
    // (no shipment disappears + tab totals = All).
    const byStatus = status === "All"
      ? items
      : status === "Out for Delivery"
      ? items.filter((s) => {
          const st = s.status || "";
          if (matchesStatusFilter(st, "Out for Delivery")) return true;
          // Orphan check — status matches NO other tab at all.
          for (const f of STATUS_FILTER_ORDER) {
            if (f === "All" || f === "Out for Delivery") continue;
            if (matchesStatusFilter(st, f)) return false;
          }
          return true;   // no other tab claimed it → falls to OFD
        })
      : items.filter((s) => matchesStatusFilter(s.status || "", status));
    const byPrint = printFilter === "All"
      ? byStatus
      : byStatus.filter((s) => {
          const isPrinted = (s.print_status || "") === "Printed";
          return printFilter === "Printed" ? isPrinted : !isPrinted;
        });
    // Phase F4.6 — Label filter.
    const byLabel = !labelFilter
      ? byPrint
      : byPrint.filter((s) => {
          const arr = shipmentLabels[s.id] || (s as any).labels || [];
          return Array.isArray(arr) && arr.includes(labelFilter);
        });
    // Phase A — Payment (COD/Prepaid) + Courier Partner filters.
    const byPay = paymentFilter.size === 0
      ? byLabel
      : byLabel.filter((s) => paymentFilter.has(String((s as any).payment_mode || "").trim()));
    const byCourier = !courierFilter
      ? byPay
      : byPay.filter((s) => String((s as any).courier_id || "") === courierFilter);

    // ─── Phase F6.3 — Shipment Import filters (client-side) ───
    // Applied AFTER the tab/status/date filters so they compose cleanly
    // with every other active chip. Each block is a no-op when its set
    // is empty (fast path preserved).
    let byImp = byCourier;
    if (importStatus === "imported") {
      byImp = byImp.filter((s) => !!(s as any).last_import_at);
    } else if (importStatus === "not_imported") {
      byImp = byImp.filter((s) => !(s as any).last_import_at);
    }
    if (importTypes.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        // A shipment "belongs to" a type if we can see its stamp.
        // booking = imported_booking_at, delivery = delivery_source==="imported",
        // cod_payment = cod_payment_status==="received".
        if (importTypes.has("booking")     && anyS.imported_booking_at)                return true;
        if (importTypes.has("delivery")    && anyS.delivery_source === "imported")    return true;
        if (importTypes.has("cod_payment") && anyS.cod_payment_status === "received") return true;
        return false;
      });
    }
    if (bookingFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const hasBooking = !!anyS.imported_booking_at;
        if (bookingFilter.has("imported") && hasBooking)  return true;
        if (bookingFilter.has("pending")  && !hasBooking) return true;
        return false;
      });
    }
    if (deliveryFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const impDel   = anyS.delivery_source === "imported";
        const confirmed = anyS.confirmation_status === "confirmed";
        const pending   = String(anyS.status || "") !== "Delivered";
        if (deliveryFilter.has("imported")  && impDel)     return true;
        if (deliveryFilter.has("confirmed") && confirmed)  return true;
        if (deliveryFilter.has("pending")   && pending)    return true;
        return false;
      });
    }
    if (codPaymentFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const received = anyS.cod_payment_status === "received";
        const isCOD    = String(anyS.payment_mode || "").toUpperCase() === "COD";
        const pending  = isCOD && !received;
        const amtMismatch = ((anyS.import_validation_alerts || []) as any[])
          .some((a) => a.field === "amount");
        if (codPaymentFilter.has("received")        && received)       return true;
        if (codPaymentFilter.has("pending")         && pending)        return true;
        if (codPaymentFilter.has("amount_mismatch") && amtMismatch)    return true;
        return false;
      });
    }
    if (validationFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const alerts = ((s as any).import_validation_alerts || []) as any[];
        for (const a of alerts) {
          if (validationFilter.has(a.field)) return true;
        }
        return false;
      });
    }
    // Phase F7.1 — India Post Complaint STATUS filter. Keeps only
    // shipments that (a) have a complaint record AND (b) whose
    // `complaint_status` matches at least one selected chip.
    // A shipment without a complaint never survives this filter, so
    // the downstream export path is guaranteed to receive complaint-
    // flagged ids only.
    if (complaintFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        if (!anyS.complaint_created) return false;
        const st = String(anyS.complaint_status || "Open");
        return complaintFilter.has(st as any);
      });
    }
    // ─────────────────────────────────────────────────────────────

    if (dateFilter === "all") return byImp;
    if (dateFilter === "custom") {
      if (!customFrom && !customTo) return byImp;
      const from = customFrom ? new Date(customFrom.getFullYear(), customFrom.getMonth(), customFrom.getDate()).getTime() : 0;
      const to = customTo ? new Date(customTo.getFullYear(), customTo.getMonth(), customTo.getDate(), 23, 59, 59, 999).getTime() : Number.MAX_SAFE_INTEGER;
      return byImp.filter((s) => {
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
    return byImp.filter((s) => {
      const t = Date.parse(s.created_at || "");
      return !isNaN(t) && t >= cutoff;
    });
  }, [
    items, dateFilter, customFrom, customTo, status, printFilter, labelFilter,
    shipmentLabels, paymentFilter, courierFilter,
    // Phase F6.3
    importStatus, importTypes, bookingFilter, deliveryFilter,
    codPaymentFilter, validationFilter,
    complaintFilter,
  ]);

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

  // Shared helper — mark N shipments as Printed and reconcile UI.
  // Reused by both bulkPrint (native print dialog flow) and
  // bulkPreviewPdf (PDF Preview / Share flow) so a successful share
  // is treated the same as a successful print.
  const markShipmentsPrinted = useCallback(async (ids: string[]) => {
    if (!ids || ids.length === 0) return;
    try {
      const results = await Promise.allSettled(
        ids.map((sid) => Api.setPrintStatus(sid, true)),
      );
      const okIds = new Set<string>();
      results.forEach((r, i) => {
        if (r.status === "fulfilled") okIds.add(ids[i]);
      });
      setItems((prev) => prev.map((row) =>
        okIds.has(row.id) ? { ...row, print_status: "Printed" as any } : row,
      ));
      const failed = ids.length - okIds.size;
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
  }, [load]);

  // Reusable "Are all selected shipments printed successfully?"
  // confirmation. Whichever bulk export path the user takes (native
  // Print dialog OR PDF Preview/Share), we ask exactly once at the
  // end and, on Yes, mark everything Printed in one fan-out.
  const confirmAndMarkBulkPrinted = useCallback((ids: string[]) => {
    if (!ids || ids.length === 0) return;
    const n = ids.length;
    Alert.alert(
      "Confirm Bulk Print",
      `Are all ${n} selected shipment${n === 1 ? "" : "s"} printed successfully?`,
      [
        { text: "Not yet", style: "cancel" },
        {
          text: "Yes, All Printed",
          onPress: () => { markShipmentsPrinted(ids); },
        },
      ],
    );
  }, [markShipmentsPrinted]);

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
      //    entire batch completes. Unified into the shared helper so
      //    the PDF-Share flow (bulkPreviewPdf) behaves identically.
      confirmAndMarkBulkPrinted(idsSnapshot);
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
    // Snapshot before the OS Share sheet so touch events during share
    // don't mutate the ids we mark Printed.
    const idsSnapshot = Array.from(selectedIds);
    try {
      const shipments = await Api.bulkFetch(idsSnapshot);
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

      // Bug fix (Jul-2026): PDF Preview / Share now triggers the SAME
      // "Confirm Bulk Print" dialog as the native Print flow. From the
      // user's perspective, generating/sharing a PDF IS a successful
      // print action — we just need to confirm before flipping status.
      confirmAndMarkBulkPrinted(idsSnapshot);
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
    "Pending":          "Processing",
    "Processing":       "Ready to Ship",
    "Ready to Ship":    "Shipped",
    "Dispatch":         "Shipped",          // legacy alias for Ready to Ship
    "Dispatched":       "Shipped",
    "Shipped":          "Out for Delivery",
    "Out for Delivery": "Delivered",
    "Delivered":        "Feedback",
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
  //
  // Bug fix (Jul-2026): Per user request, single-shipment prints no
  // longer show a confirmation dialog — the label screen itself
  // auto-marks the shipment as Printed after a successful
  // print/share. This effect is retained as a light "refresh the
  // list" so the newly-flipped Printed badge shows up instantly on
  // return without waiting for the next background poll.
  useFocusEffect(useCallback(() => {
    if (!pendingPrintConfirmId) return;
    setPendingPrintConfirmId(null);
    load().catch(() => {});
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
  // Bug-fix (Jul-2026): badges must respect every other active filter
  // (print / label / courier / date / payment) so the visible count on
  // "All" matches the actual list length after those filters are
  // applied. Only the status filter itself is excluded from the input
  // set — otherwise each status tab would only ever show its own count.
  const countableItems = useMemo(() => {
    // Reuse the same filter chain as `dateFilteredItems` but skip the
    // status filter (that's the whole point of these per-tab counts).
    const byPrint = printFilter === "All"
      ? items
      : items.filter((s) => {
          const isPrinted = (s.print_status || "") === "Printed";
          return printFilter === "Printed" ? isPrinted : !isPrinted;
        });
    const byLabel = !labelFilter
      ? byPrint
      : byPrint.filter((s) => {
          const arr = shipmentLabels[s.id] || (s as any).labels || [];
          return Array.isArray(arr) && arr.includes(labelFilter);
        });
    const byPay = paymentFilter.size === 0
      ? byLabel
      : byLabel.filter((s) => paymentFilter.has(String((s as any).payment_mode || "").trim()));
    const byCourier = !courierFilter
      ? byPay
      : byPay.filter((s) => String((s as any).courier_id || "") === courierFilter);

    // ─── Phase F6.6 Bug-1 Fix — Import filters MUST participate in
    // the tab counter chain, otherwise the per-tab pills keep showing
    // the un-filtered totals while the list body already reflects the
    // active Import Status / Type / Validation / Booking / Delivery /
    // COD sub-filters. Same predicates as the visible-list `useMemo`.
    let byImp = byCourier;
    if (importStatus === "imported") {
      byImp = byImp.filter((s) => !!(s as any).last_import_at);
    } else if (importStatus === "not_imported") {
      byImp = byImp.filter((s) => !(s as any).last_import_at);
    }
    if (importTypes.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        if (importTypes.has("booking")     && anyS.imported_booking_at)                return true;
        if (importTypes.has("delivery")    && anyS.delivery_source === "imported")    return true;
        if (importTypes.has("cod_payment") && anyS.cod_payment_status === "received") return true;
        return false;
      });
    }
    if (bookingFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const hasBooking = !!anyS.imported_booking_at;
        if (bookingFilter.has("imported") && hasBooking)  return true;
        if (bookingFilter.has("pending")  && !hasBooking) return true;
        return false;
      });
    }
    if (deliveryFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const impDel   = anyS.delivery_source === "imported";
        const confirmed = anyS.confirmation_status === "confirmed";
        const pending   = String(anyS.status || "") !== "Delivered";
        if (deliveryFilter.has("imported")  && impDel)     return true;
        if (deliveryFilter.has("confirmed") && confirmed)  return true;
        if (deliveryFilter.has("pending")   && pending)    return true;
        return false;
      });
    }
    if (codPaymentFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        const received = anyS.cod_payment_status === "received";
        const isCOD    = String(anyS.payment_mode || "").toUpperCase() === "COD";
        const pending  = isCOD && !received;
        const amtMismatch = ((anyS.import_validation_alerts || []) as any[])
          .some((a) => a.field === "amount");
        if (codPaymentFilter.has("received")        && received)       return true;
        if (codPaymentFilter.has("pending")         && pending)        return true;
        if (codPaymentFilter.has("amount_mismatch") && amtMismatch)    return true;
        return false;
      });
    }
    if (validationFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const alerts = ((s as any).import_validation_alerts || []) as any[];
        for (const a of alerts) {
          if (validationFilter.has(a.field)) return true;
        }
        return false;
      });
    }
    // Phase F7.1 — India Post Complaint STATUS filter. Keeps only
    // shipments that (a) have a complaint record AND (b) whose
    // `complaint_status` matches at least one selected chip.
    // A shipment without a complaint never survives this filter, so
    // the downstream export path is guaranteed to receive complaint-
    // flagged ids only.
    if (complaintFilter.size > 0) {
      byImp = byImp.filter((s) => {
        const anyS = s as any;
        if (!anyS.complaint_created) return false;
        const st = String(anyS.complaint_status || "Open");
        return complaintFilter.has(st as any);
      });
    }
    // ─────────────────────────────────────────────────────────────

    if (dateFilter === "all") return byImp;
    if (dateFilter === "custom") {
      if (!customFrom && !customTo) return byImp;
      const from = customFrom ? new Date(customFrom.getFullYear(), customFrom.getMonth(), customFrom.getDate()).getTime() : 0;
      const to = customTo ? new Date(customTo.getFullYear(), customTo.getMonth(), customTo.getDate(), 23, 59, 59, 999).getTime() : Number.MAX_SAFE_INTEGER;
      return byImp.filter((s) => {
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
    return byImp.filter((s) => {
      const t = Date.parse(s.created_at || "");
      return !isNaN(t) && t >= cutoff;
    });
  }, [
    items, printFilter, labelFilter, shipmentLabels, paymentFilter, courierFilter,
    dateFilter, customFrom, customTo,
    // Phase F6.6 — Import filters must invalidate the counter cache.
    importStatus, importTypes, bookingFilter, deliveryFilter,
    codPaymentFilter, validationFilter,
    complaintFilter,
  ]);

  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = {
      "All": countableItems.length,
      "Pending": 0, "Processing": 0, "Ready to Ship": 0, "Shipped": 0,
      "Out for Delivery": 0,
      "Delivered": 0, "Feedback": 0, "Modified": 0,
      "Cancel by buyer": 0, "Cancelled": 0, "Returned": 0,
    };
    // ── Phase F6.7 — CANONICAL 1:1 counting ──
    // Enforce the user's invariant:
    //   All  =  Pending + Processing + Ready to Ship + Shipped
    //         + Out for Delivery + Delivered + Feedback + Modified
    //         + Cancel by buyer + Cancelled + Returned
    //
    // Each shipment MUST land in exactly ONE tab:
    //   • Match the tabs in STATUS_FILTER_ORDER order and stop at the
    //     FIRST hit (prevents double-counting when a status has
    //     overlapping aliases).
    //   • If NO tab matches (empty status, courier-webhook value we
    //     haven't mapped yet, or a novel India Post event) we default
    //     to "Out for Delivery" per Rule-5 — the shipment stays
    //     visible in the follow-up queue instead of disappearing.
    for (const s of countableItems) {
      const st = s.status || "";
      let hit = false;
      for (const f of STATUS_FILTER_ORDER) {
        if (f === "All") continue;
        if (matchesStatusFilter(st, f)) {
          counts[f] += 1;
          hit = true;
          break;    // 1:1 guarantee — stop after first match
        }
      }
      if (!hit) {
        // Rule-5 fallback so total always equals sum of tabs.
        counts["Out for Delivery"] += 1;
      }
    }
    return counts;
  }, [countableItems]);

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
        // Phase F4.8 — UI-only: mark this shipment as freshly saved
        // so the per-card person button flips to the green/white
        // "saved" state IMMEDIATELY. No popup / toast on single
        // save — matches the spec exactly.
        markContactsSaved([ship.id]);
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
      // Phase F4.8 — UI-only: mark all shipments that carried a
      // phone as freshly saved so the per-card person button flips
      // to the green/white "saved" state IMMEDIATELY. Backend's
      // `skipped` count == rows without a phone (i.e., rows that
      // could NOT be included in the .vcf), so we compute the
      // "succeeded" subset by filtering ids for a non-empty phone
      // — same rule the backend uses. Only successful rows flip.
      const idsWithPhone = ids.filter((id) => {
        const s = items.find((x) => x.id === id);
        return !!(s && (s.customer_phone || "").trim());
      });
      markContactsSaved(idsWithPhone);
      const savedCount = r.count;
      const failedCount = r.skipped || 0;
      // Cross-platform toast:
      //   • Android → native ToastAndroid (short-lived, non-blocking)
      //   • iOS / web preview → Alert with just an OK button
      const toastMsg = failedCount > 0
        ? `${savedCount} contacts saved, ${failedCount} failed.`
        : "Contacts saved successfully. Saved contacts are now marked in green.";
      if (Platform.OS === "android") {
        ToastAndroid.show(toastMsg, ToastAndroid.LONG);
      } else {
        Alert.alert("Contacts saved", toastMsg);
      }
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

  // Helper — turn an ArrayBuffer into a base64 string without pulling
  // in a Buffer polyfill on RN.  Used by the XLSX writer on native
  // since expo-file-system needs base64 for binary payloads.
  const _abToBase64 = (buf: ArrayBuffer): string => {
    const bytes = new Uint8Array(buf);
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    // eslint-disable-next-line no-undef
    return typeof btoa !== "undefined" ? btoa(bin) : "";
  };

  // ── Excel-native export (Jul-2026) ─────────────────────────────
  // XLSX baked-in encoding = correct rendering of Gujarati / Hindi
  // in every Excel version — sidesteps the Windows-1252 CSV mojibake
  // ("àn àn¥‹àn'" garbage) old Excel showed when opening our CSV
  // exports.  Same filter-aware ID list as the CSV path.
  const _doExportXlsx = async () => {
    const visibleIds = dateFilteredItems.map((s) => s.id).filter(Boolean);
    const buf = await Api.exportShipmentsXlsx(
      visibleIds.length > 0 ? visibleIds : undefined,
    );
    if (!buf || buf.byteLength < 200) {
      // A truly-empty workbook is ~2.5KB; anything < 200 bytes is a
      // "no data / server error" signal.
      Alert.alert("No data", "You don't have any shipments to export yet.");
      return;
    }
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const filterTag =
      (status !== "All" ? `_${status.toLowerCase().replace(/\s+/g, "")}` : "") +
      (dateFilter !== "all" ? `_${dateFilter}` : "");
    const filename = `shipments${filterTag}_${stamp}.xlsx`;
    const mime =
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    if (Platform.OS === "web") {
      const blob = new Blob([buf], { type: mime });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      a.remove(); URL.revokeObjectURL(url);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const FileSystem = require("expo-file-system/legacy");
      const path = `${FileSystem.cacheDirectory || ""}${filename}`;
      await FileSystem.writeAsStringAsync(path, _abToBase64(buf), {
        encoding: FileSystem.EncodingType.Base64,
      });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(path, {
          mimeType: mime,
          dialogTitle: "Export Shipments (Excel)",
          UTI: "org.openxmlformats.spreadsheetml.sheet",
        });
      } else {
        Alert.alert("Export ready", `Saved to ${path}`);
      }
    }
  };

  // ── Phase F7.0 — India Post Complaint export ──────────────────
  //
  // Called automatically when the "Complaint Created" filter is
  // active. The backend returns either a single .xlsx (≤ 500 rows)
  // or a .zip containing multiple 500-row parts (larger batches).
  // Filename comes from Content-Disposition so we always mirror
  // whatever the server generated (IndiaPost_Complaint_DD-MM-YYYY_PartX.xlsx).
  const _parseFilenameFromDisposition = (
    disposition: string, fallback: string,
  ): string => {
    // RFC-2183 style: `attachment; filename="XYZ.zip"`. Also handle
    // the RFC-5987 `filename*=UTF-8''XYZ` variant just in case.
    const m1 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (m1?.[1]) {
      try { return decodeURIComponent(m1[1]); } catch { /* noop */ }
    }
    const m2 = /filename="?([^";]+)"?/i.exec(disposition);
    if (m2?.[1]) return m2[1];
    return fallback;
  };

  const _doExportComplaints = async () => {
    const visibleIds = dateFilteredItems
      .filter((s) => !!(s as any).complaint_created)
      .map((s) => s.id)
      .filter(Boolean);
    if (visibleIds.length === 0) {
      Alert.alert(
        "No complaints to export",
        "There are no complaint-flagged shipments in the current view. " +
        "Open a shipment and use the ‘India Post Complaint’ section to " +
        "create one first.",
      );
      return;
    }
    const result = await Api.exportComplaints(visibleIds);
    if (!result?.data || result.data.byteLength < 200) {
      Alert.alert(
        "No data",
        "The complaint export was empty — nothing to save.",
      );
      return;
    }
    const isZip =
      (result.contentType || "").toLowerCase().includes("zip") ||
      (result.parts || 1) > 1;
    const stampDDMMYYYY = (() => {
      const d = new Date();
      const dd = String(d.getDate()).padStart(2, "0");
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const yy = d.getFullYear();
      return `${dd}-${mm}-${yy}`;
    })();
    const fallback = isZip
      ? `IndiaPost_Complaint_${stampDDMMYYYY}.zip`
      : `IndiaPost_Complaint_${stampDDMMYYYY}_Part1.xlsx`;
    const filename = _parseFilenameFromDisposition(
      result.contentDisposition || "", fallback,
    );
    const mime = isZip
      ? "application/zip"
      : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    const utiTag = isZip
      ? "public.zip-archive"
      : "org.openxmlformats.spreadsheetml.sheet";

    if (Platform.OS === "web") {
      const blob = new Blob([result.data], { type: mime });
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      a.remove(); URL.revokeObjectURL(url);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const FileSystem = require("expo-file-system/legacy");
      const path = `${FileSystem.cacheDirectory || ""}${filename}`;
      await FileSystem.writeAsStringAsync(path, _abToBase64(result.data), {
        encoding: FileSystem.EncodingType.Base64,
      });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(path, {
          mimeType: mime,
          dialogTitle: "India Post Complaint Export",
          UTI: utiTag,
        });
      } else {
        Alert.alert("Export ready", `Saved to ${path}`);
      }
    }
    if ((result.parts || 1) > 1) {
      Alert.alert(
        "India Post Complaint Export",
        `Generated ${result.parts} files (${result.totalRows} complaints) — ` +
        `India Post accepts max 500 rows per upload. Each Part file inside ` +
        `the ZIP can be uploaded independently to CBS.`,
      );
    }
  };

  const handleExportCsv = async () => {
    if (exportCsvBusy) return;
    // ── Phase F7.1 — India Post Complaint STATUS override ────────
    // When ANY India Post Complaint STATUS filter is active (Open,
    // In Progress, Resolved, or Closed), bypass the standard export
    // chooser entirely and generate the strict India Post CBS
    // complaint upload template automatically. Design decision (per
    // user requirement): do NOT ask the operator which format they
    // want — the filter alone decides.
    const complaintExportActive = complaintFilter.size > 0;
    if (complaintExportActive) {
      setExportCsvBusy(true);
      try { await _doExportComplaints(); }
      catch (e: any) {
        Alert.alert(
          "Complaint export failed",
          e?.response?.data?.detail || e?.message ||
          "Please make sure at least one complaint has been created.",
        );
      } finally { setExportCsvBusy(false); }
      return;
    }
    // ─────────────────────────────────────────────────────────────
    // Give users the choice: Excel-native XLSX (Recommended for
    // Gujarati / Hindi customer names — never breaks in old Windows
    // Excel) OR the classic CSV (compact, works in any spreadsheet
    // app that respects UTF-8 BOM).
    Alert.alert(
      "Export Shipments",
      "Excel (.xlsx) preserves Gujarati / Hindi names in every Excel version. Choose CSV only if you specifically need a comma-separated file.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Excel (.xlsx) — Recommended",
          onPress: async () => {
            setExportCsvBusy(true);
            try { await _doExportXlsx(); }
            catch (e: any) {
              Alert.alert(
                "Export failed",
                e?.response?.data?.detail || e?.message || "Try again.",
              );
            } finally { setExportCsvBusy(false); }
          },
        },
        {
          text: "CSV",
          onPress: () => { setExportCsvBusy(true); _doExportCsv().finally(() => setExportCsvBusy(false)); },
        },
      ],
    );
  };

  const _doExportCsv = async () => {
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
          {/* Phase F6.0 — Shipment Import System.
              Upload button visible ONLY in normal (non-selection) mode.
              Opens the type-picker (Booking / Delivery / COD Payment)
              which then leads to the mapping + preview flow. */}
          {!selectMode && (
            <TouchableOpacity
              testID="shipment-import-btn"
              style={styles.iconBtn}
              onPress={() => router.push("/shipment-import" as any)}
            >
              <PhIcon name="cloud-upload-outline" size={20} color={colors.text} />
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

      <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 12, gap: 8 }}>
        <View style={{ flex: 1 }}>
          <SearchBar
            testID="search"
            placeholder="Search tracking, name, city, order..."
            value={search}
            onChangeText={setSearch}
            onClear={() => {
              setStatus("All");
              setDateFilter("all");
              setCustomFrom(null);
              setCustomTo(null);
            }}
            onSubmitEditing={load}
          />
        </View>
        {/* Phase A — Filter icon opens the Filter Bottom Sheet with
            Date + Payment + Labels sections. Red dot when any filter
            (managed inside the sheet) is currently active. */}
        <TouchableOpacity
          testID="filter-icon"
          onPress={() => setFilterSheetOpen(true)}
          style={{
            width: 44, height: 44, borderRadius: 10, borderWidth: 1,
            borderColor: "#E5E7EB", alignItems: "center", justifyContent: "center",
            backgroundColor: "#fff",
          }}
        >
          <PhIcon name="filter" size={20} color={colors.primary} />
          {(dateFilter !== "all" || paymentFilter.size > 0 || labelFilter) ? (
            <View style={{
              position: "absolute", top: 8, right: 8, width: 8, height: 8,
              borderRadius: 4, backgroundColor: "#DC2626",
            }} />
          ) : null}
        </TouchableOpacity>
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

      {/* Phase A — New 3-chip Quick Filter row (All Print / All
          Labels / Courier Partner). Replaces the three older filter
          rows below (which are wrapped in `false && …` and can be
          removed once QA signs off). */}
      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={[styles.filterRow, { paddingTop: 0, gap: 8 }]}>
          {[
            { key: "print" as const, icon: "print",
              lbl: printFilter === "All" ? "All Print" : printFilter,
              active: printFilter !== "All" },
            { key: "label" as const, icon: "pricetag",
              lbl: labelFilter ? (labelDefs[labelFilter]?.name || "Label") : "All Labels",
              active: !!labelFilter },
            { key: "courier" as const, icon: "car",
              lbl: courierFilter ? (couriers.find((c) => c.id === courierFilter)?.name || "Courier") : "Courier Partner",
              active: !!courierFilter },
          ].map((chip) => (
            <TouchableOpacity
              key={chip.key}
              testID={`quickchip-${chip.key}`}
              onPress={() => setQuickPicker(chip.key)}
              style={[
                styles.filterPill,
                { borderColor: colors.primary, flexDirection: "row", gap: 6, alignItems: "center" },
                chip.active && { backgroundColor: colors.primary, borderColor: colors.primary },
              ]}
            >
              <PhIcon name={chip.icon} size={14} color={chip.active ? "#fff" : colors.primary} />
              <Text style={[styles.filterText, { color: chip.active ? "#fff" : colors.primary }]}
                numberOfLines={1} allowFontScaling={false}>{chip.lbl}</Text>
              <PhIcon name="chevron-down" size={14} color={chip.active ? "#fff" : colors.primary} />
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {/* Phase B — Date filter row moved into the Filter Bottom Sheet
          (opened via the funnel icon next to the SearchBar). This
          keeps the top of the screen clean and gives more room to
          shipment cards. */}

      {/* Phase A — Old Print Status & Label filter rows removed.
          Both are now surfaced through the new Quick Filter chips
          above ("All Print" and "All Labels") which open a picker
          bottom-sheet on tap. */}

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
      <BulkPrintActionModal
        visible={actionPopupOpen}
        onClose={() => setActionPopupOpen(false)}
        selectedCount={selectedIds.size}
        bulkPerPage={bulkPerPage}
        onPreview={() => bulkPreviewPdf()}
        onPrint={() => bulkPrint()}
      />

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
                    Phase F4.3 — 3-state Print button.
                    Phase F6.4 — For Out for Delivery cards that carry
                    a Last Event from a Delivery Import, the top-right
                    slot switches from the Print CTA to a compact Last
                    Event category badge (Delivered / Hold / Redirected
                    / Returned / Dispatched / Received / Bagged / Other).
                    The full free-text lives on Shipment Details. */}
                {flagPrint && !selectMode ? (() => {
                  const isPrinted = (item.print_status || "") === "Printed";
                  const hasTracking = !!(item.tracking_id || (item as any).manual_tracking_id);
                  const status = String((item as any).status || "");
                  const evCat = String((item as any).last_event_category || "");
                  const showEvent = status === "Out for Delivery" && !!evCat;
                  if (showEvent) {
                    const meta = LAST_EVENT_BADGE_META[evCat] || LAST_EVENT_BADGE_META.Other;
                    return (
                      <View
                        style={[
                          styles.printNowBtn,
                          { backgroundColor: meta.bg, borderWidth: 0 },
                        ]}
                        testID={`last-event-${item.tracking_id || item.id}`}
                      >
                        <PhIcon name={meta.icon as any} size={12} color={meta.fg} />
                        <Text
                          numberOfLines={1}
                          style={[styles.printNowTxt, { color: meta.fg }]}
                        >
                          {evCat}
                        </Text>
                      </View>
                    );
                  }
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
                    zero-cost utility and a frequent request.
                    Phase F4.8 — After a successful save (single or
                    bulk-with-phone), flip to a filled-green pill with
                    a white filled-person icon so the operator can
                    see at a glance which cards were already actioned
                    this session. Purely visual — the underlying
                    "Already Saved" check on tap is UNCHANGED. */}
                {(() => {
                  const savedNow = contactSavedIds.has(item.id);
                  return (
                    <ActionBtn
                      icon={savedNow ? "person" : "person-add-outline"}
                      color="#7C3AED"
                      bg={savedNow ? "#10B981" : undefined}
                      iconColor={savedNow ? "#fff" : "#7C3AED"}
                      onPress={() => handleSaveContact(item)}
                      testID={`save-contact-${item.tracking_id}`}
                    />
                  );
                })()}
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

      {/* Custom date range modal — extracted Phase F4.6 */}
      <DateRangeModal
        visible={showDateModal}
        onClose={() => setShowDateModal(false)}
        from={customFrom}
        to={customTo}
        setFrom={setCustomFrom}
        setTo={setCustomTo}
        pickerField={pickerField}
        setPickerField={setPickerField}
      />

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

      {/* ── Phase A — Quick Filter picker sheets. One shared modal
             per chip; only one can be open at a time (quickPicker). */}
      <QuickPickerSheet
        visible={quickPicker === "print"}
        title="Print Status"
        options={[
          { id: "All", label: "All Print", icon: "print" },
          { id: "Not Printed", label: "Not Printed", icon: "print", color: colors.primary },
          { id: "Printed", label: "Printed", icon: "checkmark", color: "#10B981" },
        ]}
        value={printFilter}
        onChange={(id) => setPrintFilter(id as "All" | "Printed" | "Not Printed")}
        onClose={() => setQuickPicker(null)}
      />
      <QuickPickerSheet
        visible={quickPicker === "label"}
        title="Filter by Label"
        options={[
          { id: "", label: "All Labels", icon: "pricetag" } as QuickPickerOption,
          ...Object.values(labelDefs)
            .sort((a, b) => {
              const kindOrder: Record<string, number> = { order: 0, priority: 1, custom: 2 };
              const ka = kindOrder[a.kind] ?? 3;
              const kb = kindOrder[b.kind] ?? 3;
              if (ka !== kb) return ka - kb;
              return a.name.localeCompare(b.name);
            })
            .map((l) => ({
              id: l.id,
              label: l.name,
              icon: (LABEL_ICON_MAP[l.icon] || l.icon || "pricetag") as string,
              color: l.color,
            })),
        ]}
        value={labelFilter}
        onChange={(id) => setLabelFilter(id)}
        onClose={() => setQuickPicker(null)}
      />
      <QuickPickerSheet
        visible={quickPicker === "courier"}
        title="Courier Partner"
        options={[
          { id: "", label: "All Couriers", icon: "car" } as QuickPickerOption,
          ...couriers.map((c) => ({
            id: c.id,
            label: c.name,
            icon: "car",
            color: colors.primary,
          })),
        ]}
        value={courierFilter}
        onChange={(id) => setCourierFilter(id)}
        onClose={() => setQuickPicker(null)}
      />

      {/* ── Phase B — Filter Bottom Sheet (funnel icon opens this). */}
      <ShipmentsFilterSheet
        visible={filterSheetOpen}
        onClose={() => setFilterSheetOpen(false)}
        dateFilter={dateFilter}
        setDateFilter={(v) => setDateFilter(v)}
        customFrom={customFrom}
        customTo={customTo}
        onOpenCustomDate={() => setShowDateModal(true)}
        paymentFilter={paymentFilter}
        setPaymentFilter={setPaymentFilter}
        onClearAll={() => {
          setDateFilter("all");
          setCustomFrom(null);
          setCustomTo(null);
          setPaymentFilter(new Set());
          // Phase F6.3 — also reset the Import filters
          setImportStatus("all");
          setImportTypes(new Set());
          setBookingFilter(new Set());
          setDeliveryFilter(new Set());
          setCodPaymentFilter(new Set());
          setValidationFilter(new Set());
          setImportBatchPick(null);
          setPaymentBatchPick(null);
          setComplaintFilter(new Set());
          // Kick a reload since Batch filters are server-side
          load().catch(() => {});
        }}
        // Phase F6.3 — Import filter props
        importStatus={importStatus}
        setImportStatus={setImportStatus}
        importTypes={importTypes}
        setImportTypes={setImportTypes}
        bookingFilter={bookingFilter}
        setBookingFilter={setBookingFilter}
        deliveryFilter={deliveryFilter}
        setDeliveryFilter={setDeliveryFilter}
        codPaymentFilter={codPaymentFilter}
        setCodPaymentFilter={setCodPaymentFilter}
        validationFilter={validationFilter}
        setValidationFilter={setValidationFilter}
        importBatch={importBatchPick}
        onOpenImportBatchPicker={() => setImportBatchPickerOpen(true)}
        paymentBatch={paymentBatchPick}
        onOpenPaymentBatchPicker={() => setPaymentBatchPickerOpen(true)}
        complaintFilter={complaintFilter}
        setComplaintFilter={setComplaintFilter}
      />

      {/* Phase F6.3 — Batch pickers (import + payment). Server-side
          filters — selection triggers a reload since the picked id
          is passed as a query param on Api.listShipments. */}
      <BatchPickerSheet
        visible={importBatchPickerOpen}
        onClose={() => setImportBatchPickerOpen(false)}
        kind="import"
        current={importBatchPick}
        onSelect={(b) => {
          setImportBatchPick(b);
          // Immediate reload so the list reflects the drill-down.
          load().catch(() => {});
        }}
      />
      <BatchPickerSheet
        visible={paymentBatchPickerOpen}
        onClose={() => setPaymentBatchPickerOpen(false)}
        kind="payment"
        current={paymentBatchPick}
        onSelect={(b) => {
          setPaymentBatchPick(b);
          load().catch(() => {});
        }}
      />


      {/* ── Phase B — Create-label shortcut. Opens LabelPickerSheet
             straight in Create mode; on save the new label refreshes
             the labelDefs map (via loadLabels) so it appears in the
             All Labels picker + card chips immediately. */}
      <LabelPickerSheet
        visible={labelCreateOpen}
        selectedIds={[]}
        initialView="create"
        onClose={() => setLabelCreateOpen(false)}
        onApply={() => {
          setLabelCreateOpen(false);
          loadLabels().catch(() => {});
        }}
      />
    </SafeAreaView>
  );
}

// Note: `labelStyles`, `ActionBtn`, and `StatusChip` were extracted into
// `../../components/shipments/{status_meta.ts, ActionBtn.tsx, StatusChip.tsx}`
// during Phase F4.5 to keep this file under the agent-tool string-match
// ceiling. All three symbols are still imported at the top.

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
  //
  // Phase-22b (2026-05-17) — Responsive stage pills tuned for 360 dp
  // phones. `flex: 1` keeps both buttons equal-width; tight padding +
  // 12 px font + minHeight 44 preserves the touch target while
  // fitting "READY TO SHIP" without truncation.
  stageRow: {
    flexDirection: "row",
    paddingHorizontal: 10,
    paddingTop: 4,
    paddingBottom: 12,
    gap: 8,
  },
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
    minWidth: 0,            // lets flex:1 children shrink inside row
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
  // NOTE: `chip` / `chipText` are declared earlier in this stylesheet
  // (defined once, used everywhere the tiny status pill is rendered).
  // The duplicates that used to live here were left over from the
  // pre-extraction StatusChip component — safe to drop.
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
  // Bulk-Print action popup + Date-Range modal styles have been
  // moved to their respective component files under
  // `components/shipments/` (Phase F4.6). Nothing here references
  // them any more, so they've been dropped from this stylesheet to
  // keep the file lean.

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
