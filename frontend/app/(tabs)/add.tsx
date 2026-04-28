import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Modal,
  FlatList,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Api, Courier, SheetOrder } from "../../lib/api";
import { SyncQueue } from "../../lib/syncQueue";
import { scannerBridge } from "../../lib/scannerBridge";
import { validateTrackingId } from "../../lib/trackingValidator";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";

// AsyncStorage keys for "last used" memory — shown as hints (not defaults).
const LS_LAST_COURIER = "@csm/lastCourierId";
const LS_LAST_PAYMENT = "@csm/lastPaymentMode";
const LS_LAST_TRACK_MODE = "@csm/lastTrackMode";

/**
 * Parse a free-form address string into the form's structured fields.
 *
 * Phase-6 (2026-04-28): we no longer split into line1/line2. Everything
 * non-city/state/pincode goes into a single "line1" return key, capped
 * at 300 characters. line2 is kept in the return shape only for
 * back-compat with the form state — it's always empty.
 */
function splitAddress(full: string): {
  line1: string;
  line2: string;
  city: string;
  state: string;
  pincode: string;
} {
  const clean = (full || "").trim();
  if (!clean) return { line1: "", line2: "", city: "", state: "", pincode: "" };
  const pinMatch = clean.match(/(\d{6})/);
  const pincode = pinMatch ? pinMatch[1] : "";
  // Strip the pincode from the working text so it doesn't leak into
  // line1 (it shows up in the dedicated Pincode field instead).
  let working = pincode ? clean.replace(pincode, "") : clean;
  const parts = working.split(/[,\n]/).map((p) => p.trim()).filter(Boolean);
  let city = "";
  let state = "";
  let line1 = "";
  if (parts.length >= 3) {
    state = parts[parts.length - 1] || "";
    city  = parts[parts.length - 2] || "";
    line1 = parts.slice(0, -2).join(", ");
  } else if (parts.length === 2) {
    state = parts[1] || "";
    line1 = parts[0] || "";
  } else {
    line1 = parts.join(", ");
  }
  // Trim to the 300-char cap (defensive) — backend post-processor and
  // form maxLength also enforce this.
  if (line1.length > 300) line1 = line1.slice(0, 300);
  return { line1, line2: "", city, state, pincode };
}

export default function AddShipment() {
  const router = useRouter();
  const params = useLocalSearchParams<{ scanned?: string; fromSheet?: string; prefill?: string }>();

  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [selectedCourier, setSelectedCourier] = useState<Courier | null>(null);
  // null = user hasn't chosen yet. true = auto series. false = manual/scan.
  const [autoTracking, setAutoTracking] = useState<boolean | null>(null);
  const [nextPreview, setNextPreview] = useState<string>("");
  // Last-used hints (suggested only; not pre-selected).
  const [lastCourierId, setLastCourierId] = useState<string | null>(null);
  const [lastPaymentMode, setLastPaymentMode] = useState<"COD" | "Prepaid" | null>(null);
  const [lastTrackMode, setLastTrackMode] = useState<"auto" | "manual" | null>(null);

  const [trackingId, setTrackingId] = useState("");
  const [orderId, setOrderId] = useState("");
  // Phase-7d/e: Master Order ID preview + frontend-known auto-gen flags.
  // Populated from GET /orders/peek-master-id when the form opens (and
  // when the user hasn't explicitly typed in the Order ID input).
  const [previewMasterId, setPreviewMasterId] = useState<string>("");
  const [orderIdAutoGen, setOrderIdAutoGen] = useState<boolean>(true);
  const [orderIdAutofillNew, setOrderIdAutofillNew] = useState<boolean>(true);
  const [userTouchedOrderId, setUserTouchedOrderId] = useState<boolean>(false);
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [addr1, setAddr1] = useState("");
  const [addr2, setAddr2] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  // null = not chosen yet (user MUST explicitly pick COD or Prepaid).
  const [paymentMode, setPaymentMode] = useState<"COD" | "Prepaid" | null>(null);
  const [amount, setAmount] = useState("");
  const [tokenAmount, setTokenAmount] = useState("");
  const [boxDimensions, setBoxDimensions] = useState("");
  // Box dimensions split into 3 separate L × W × H inputs for easier entry.
  // We keep the legacy combined string in `boxDimensions` for backward
  // compat (saved on submit) and rebuild it from these 3 values.
  const [boxL, setBoxL] = useState("");
  const [boxW, setBoxW] = useState("");
  const [boxH, setBoxH] = useState("");
  const [shipmentNotes, setShipmentNotes] = useState("");
  const [customerAltPhone, setCustomerAltPhone] = useState("");
  // Label-field visibility toggles (synced from /api/settings → label_fields).
  // Controls which optional sections appear on this New Shipment form so the
  // form mirrors what will actually be printed.
  const [labelFields, setLabelFields] = useState<{
    weight: boolean;
    item: boolean;
    phone: boolean;
    alt_phone: boolean;
    customer_id: boolean;
    token_info: boolean;
    box_dimensions: boolean;
    shipment_notes: boolean;
  }>({
    weight: true,
    item: true,
    phone: true,
    alt_phone: false,
    customer_id: true,
    token_info: false,
    box_dimensions: false,
    shipment_notes: false,
  });
  const [itemsText, setItemsText] = useState(""); // newline or comma separated
  const [weight, setWeight] = useState("");
  const [weightUnit, setWeightUnit] = useState<"g" | "kg">("g");
  const [sheetRowKey, setSheetRowKey] = useState("");
  const [pendingOrderId, setPendingOrderId] = useState("");
  const [saving, setSaving] = useState(false);

  // Phase B Part 2 — per-shipment custom fields (definitions come from Settings)
  const [customFields, setCustomFields] = useState<Array<any>>([]);
  const [customValues, setCustomValues] = useState<Record<string, string>>({});

  const [sheetConnected, setSheetConnected] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [importOrders, setImportOrders] = useState<SheetOrder[]>([]);
  const [importFilter, setImportFilter] = useState<"pending" | "all">("pending");
  const [importSearch, setImportSearch] = useState("");

  useEffect(() => {
    (async () => {
      const [cs, settings, lc, lp, lt] = await Promise.all([
        Api.listCouriers(),
        Api.getSettings(),
        AsyncStorage.getItem(LS_LAST_COURIER).catch(() => null),
        AsyncStorage.getItem(LS_LAST_PAYMENT).catch(() => null),
        AsyncStorage.getItem(LS_LAST_TRACK_MODE).catch(() => null),
      ]);
      setCouriers(cs);
      // Intentionally DO NOT auto-select a default courier. User must pick.
      setSheetConnected(Boolean(settings.sheet?.sheet_id));
      setCustomFields(((settings as any).custom_fields || []) as any[]);
      // Sync label-field visibility so the form mirrors the user's label
      // settings — fields hidden on the label are also hidden here.
      const lf = (settings as any).label_fields || {};
      setLabelFields((prev) => ({
        ...prev,
        weight: lf.weight !== false,           // default true
        item: lf.item !== false,
        phone: lf.phone !== false,
        alt_phone: !!lf.alt_phone,             // default false
        customer_id: lf.customer_id !== false,
        token_info: !!lf.token_info,
        box_dimensions: !!lf.box_dimensions,
        shipment_notes: !!lf.shipment_notes,
      }));
      setLastCourierId(lc || null);
      if (lp === "COD" || lp === "Prepaid") setLastPaymentMode(lp);
      if (lt === "auto" || lt === "manual") setLastTrackMode(lt);
      // Phase-7d/e: track flags for downstream save logic.
      setOrderIdAutoGen((settings as any).order_id_auto_generate !== false);
      setOrderIdAutofillNew(
        (settings as any).order_id_autofill_in_new_shipment !== false,
      );
    })();
  }, []);

  // Phase-7e: Fetch a fresh Master Order ID preview when the form opens.
  // Auto-fills the Order ID input ONLY when:
  //   - User is creating a NEW shipment (no edit_id)
  //   - Auto-Generate Order ID is ON
  //   - Auto-fill in New Shipment is ON
  //   - User hasn't already typed in the Order ID input
  useEffect(() => {
    const eid = String(params.edit_id || "").trim();
    if (eid) return;          // edit mode → leave existing value alone
    let cancelled = false;
    (async () => {
      try {
        const r = await Api.peekMasterOrderId();
        if (cancelled) return;
        setPreviewMasterId(r.master_order_id || "");
        setOrderIdAutoGen(!!r.auto_generate);
        setOrderIdAutofillNew(!!r.autofill_in_new_shipment);
        if (
          r.auto_generate &&
          r.autofill_in_new_shipment &&
          r.master_order_id &&
          !userTouchedOrderId
        ) {
          setOrderId(r.master_order_id);
        }
      } catch {
        /* offline / fresh user — silently skip preview */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.edit_id]);

  // Peek next tracking preview — ONLY for display/hint.
  // We never consume it until user explicitly chose "Auto" AND clicks Save.
  useEffect(() => {
    if (!selectedCourier) {
      setNextPreview("");
      return;
    }
    Api.peekNextTracking(selectedCourier.id)
      .then((r) => setNextPreview(r.tracking_id))
      .catch(() => setNextPreview(""));
  }, [selectedCourier]);

  // Fill tracking input with preview ONLY when user has explicitly chosen Auto
  // (autoTracking === true, not null). Previously this auto-populated on mount
  // which caused the user to accidentally consume tracking numbers even when
  // they hadn't chosen a mode yet.
  useEffect(() => {
    if (autoTracking === true && nextPreview && !params.scanned) {
      setTrackingId(nextPreview);
    }
  }, [autoTracking, nextPreview, params.scanned]);

  useEffect(() => {
    if (params.scanned) {
      setAutoTracking(false);
      setTrackingId(String(params.scanned));
    }
  }, [params.scanned]);

  // Pick up scanned value from bridge when returning from modal scanner
  // (router.back preserves form state; we read the value here on focus).
  // Phase-4d: the bridge now also carries the matched courier_id so
  // we can auto-select the courier dropdown when the scanner detected
  // the format (e.g. "EG…IN" ⇒ India Post).
  useFocusEffect(
    useCallback(() => {
      const v = scannerBridge.consume();
      if (v) {
        setAutoTracking(false);
        setTrackingId(v.value);
        if (v.courier_id) {
          // If couriers list is already hydrated, resolve immediately;
          // otherwise park it in pendingCourierId so the existing
          // resolver effect picks it up.
          setPendingCourierId(v.courier_id);
        }
      }
    }, [])
  );

  // Dashboard-entry path: when scanner replaces to /(tabs)/add with
  // query params, also honour `courier_id` here.
  useEffect(() => {
    const cid = String((params as any).courier_id || "").trim();
    if (cid) setPendingCourierId(cid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [(params as any).courier_id]);

  // Auto-detect courier from manually-typed tracking ID when the user
  // is in manual/scan mode and has NOT already picked a courier.
  // We match against couriers that have format rules configured —
  // first match wins. This makes the "EG…IN ⇒ India Post" auto-select
  // work even for manual typing, not just camera scans.
  useEffect(() => {
    if (autoTracking !== false) return;       // only in manual mode
    if (selectedCourier) return;              // user already picked one
    const tid = trackingId.trim();
    if (tid.length < 6) return;               // avoid noise
    const candidates = couriers.filter(
      (c) => c.tracking_id_prefix || c.tracking_id_suffix || c.tracking_id_length,
    );
    if (candidates.length === 0) return;
    const match = candidates.find((c) => validateTrackingId(tid, c as any).ok);
    if (match) setSelectedCourier(match);
  }, [trackingId, autoTracking, selectedCourier, couriers]);

  // Raw sheet row captured from prefill — used to auto-fill per-shipment
  // custom fields once both the prefill AND the customFields definitions
  // from Settings are available. Stored in state to handle the race where
  // customFields may load AFTER params.prefill arrives.
  const [prefillRaw, setPrefillRaw] = useState<Record<string, string> | null>(null);

  // Edit mode: when navigated from Shipments tab with `?edit_id=...` we
  // load the existing shipment, prefill every field, and switch the save
  // button into "Update" mode (PUT /shipments/:id instead of POST).
  const [editingShipmentId, setEditingShipmentId] = useState<string>("");

  // ── Form-field feature flags ───────────────────────────────────────
  // These hide the optional inputs entirely on plans that don't include
  // them. Settings' `label_fields` map is still consulted as a per-user
  // override; the flag is the harder, plan-level gate.
  const flagAltPhone   = useFeatureFlag("form_alt_phone");
  const flagBoxDims    = useFeatureFlag("form_box_dimensions");
  const flagTokenAmt   = useFeatureFlag("form_token_amount");
  const flagShipNotes  = useFeatureFlag("form_shipment_notes");
  const flagAutoTrack  = useFeatureFlag("auto_tracking");
  const flagManualScan = useFeatureFlag("manual_tracking_scan");
  // Holds the courier_id from a shipment we are editing so we can resolve
  // it to the full Courier object once the couriers list finishes loading.
  // (The fetch race meant we used to drop the user's saved courier on edit.)
  const [pendingCourierId, setPendingCourierId] = useState<string>("");

  // Resolve pendingCourierId → selectedCourier as soon as the couriers list
  // is hydrated. Runs both on edit prefill and on prefill from pending orders.
  useEffect(() => {
    if (!pendingCourierId || couriers.length === 0) return;
    const found = couriers.find((c) => c.id === pendingCourierId);
    if (found) {
      setSelectedCourier(found);
      setPendingCourierId(""); // resolved
    }
  }, [pendingCourierId, couriers]);

  useEffect(() => {
    const eid = String(params.edit_id || "").trim();
    if (!eid) return;
    setEditingShipmentId(eid);
    let cancelled = false;
    (async () => {
      try {
        const s = await Api.getShipment(eid);
        if (cancelled || !s) return;
        // Map every shipment field back into the form state.
        setOrderId(s.order_id || "");
        setCustomerName(s.customer_name || "");
        setCustomerPhone(s.customer_phone || "");
        setCustomerAltPhone((s as any).customer_alt_phone || "");
        // Phase-6 single-address-field merge: collapse legacy line1 +
        // line2 into the single field. New form only writes line1.
        const legacyL1 = (s.address_line1 || "").trim();
        const legacyL2 = (s.address_line2 || "").trim();
        let merged = legacyL1;
        if (legacyL2 && legacyL2 !== "-") {
          merged = legacyL1 ? `${legacyL1}, ${legacyL2}` : legacyL2;
        }
        if (merged.length > 300) merged = merged.slice(0, 300);
        setAddr1(merged);
        setAddr2("");
        setCity(s.city || "");
        setState(s.state || "");
        setPincode(s.pincode || "");
        const amt =
          s.amount != null && (s.amount as any) !== ""
            ? String(s.amount)
            : "";
        setAmount(amt);
        // Items can be array or string.
        const items = Array.isArray(s.items)
          ? s.items
          : String(s.items || "").split(/[,\n;|]/).map((x) => x.trim()).filter(Boolean);
        setItemsText(items.join("\n"));
        setPaymentMode(
          s.payment_mode === "PAID" || (s.payment_mode as any) === "Prepaid"
            ? "Prepaid"
            : "COD"
        );
        if (s.weight) {
          const wStr = String(s.weight);
          const m = /^(\d+\.?\d*)\s*(g|kg|gm|gms|grams|kilogram)?/i.exec(wStr);
          if (m) {
            setWeight(m[1]);
            const u = (m[2] || "g").toLowerCase();
            setWeightUnit(u.startsWith("k") ? "kg" : "g");
          } else {
            setWeight(wStr);
          }
        }
        if ((s as any).box_dimensions) {
          const bd = String((s as any).box_dimensions);
          const parts = bd.split(/x|×|\*/i).map((x) => x.replace(/[^\d.]/g, "")).filter(Boolean);
          if (parts[0]) setBoxL(parts[0]);
          if (parts[1]) setBoxW(parts[1]);
          if (parts[2]) setBoxH(parts[2]);
          setBoxDimensions(bd);
        }
        if ((s as any).shipment_notes) setShipmentNotes((s as any).shipment_notes);
        if ((s as any).token_amount) setTokenAmount(String((s as any).token_amount));
        if ((s as any).notes) setNotes((s as any).notes);
        if (s.tracking_id) {
          setTrackingId(s.tracking_id);
          setAutoTracking(false);  // user clearly already has the ID
        }
        if (s.courier_id) {
          // Try direct match first; if couriers list isn't loaded yet,
          // queue it for the resolver useEffect above.
          const found = couriers.find((c) => c.id === s.courier_id);
          if (found) {
            setSelectedCourier(found);
          } else {
            setPendingCourierId(s.courier_id);
          }
        }
        if (s.dispatch_date) setDispatchDate(s.dispatch_date);
      } catch (e: any) {
        Alert.alert("Edit failed", e?.response?.data?.detail || e?.message || "Could not load shipment");
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.edit_id]);

  useEffect(() => {
    if (params.prefill) {
      try {
        const o = JSON.parse(String(params.prefill));
        const addr = splitAddress(o.address || "");
        setOrderId(o.order_id || "");
        setCustomerName(o.customer_name || "");
        setCustomerPhone(o.phone || "");
        setCustomerAltPhone(o.alt_phone || o.customer_alt_phone || "");
        // Phase-6: support either a structured prefill (separate
        // line1 + line2) or the new flat "address" string. In all
        // cases we collapse into a single 300-char line1.
        const incomingL1 = (o.address_line1 || "").trim();
        const incomingL2 = (o.address_line2 || "").trim();
        let merged = incomingL1 || addr.line1;
        if (incomingL2 && incomingL2 !== "-") {
          merged = merged ? `${merged}, ${incomingL2}` : incomingL2;
        }
        if (merged.length > 300) merged = merged.slice(0, 300);
        setAddr1(merged);
        setAddr2("");
        setCity(o.city || addr.city);
        setState(o.state || addr.state);
        setPincode(o.pincode || addr.pincode);
        const amt = String(o.amount || "").replace(/[^\d.]/g, "");
        setAmount(amt);
        const items = String(o.item || "")
          .split(/[,\n;|]/)
          .map((s: string) => s.trim())
          .filter(Boolean);
        setItemsText(items.join("\n"));
        setSheetRowKey(o.row_key || "");
        // New fields (from Smart Paste)
        if (o.payment_mode === "COD") setPaymentMode("COD");
        else if (o.payment_mode === "PAID" || o.payment_mode === "Prepaid") setPaymentMode("Prepaid");
        if (o.weight) {
          const wStr = String(o.weight);
          const m = /^(\d+\.?\d*)\s*(g|kg|gm|gms|grams|kilogram)?/i.exec(wStr);
          if (m) {
            setWeight(m[1]);
            const u = (m[2] || "g").toLowerCase();
            setWeightUnit(u.startsWith("k") ? "kg" : "g");
          } else {
            setWeight(wStr);
          }
        }
        if (o.pending_order_id) setPendingOrderId(String(o.pending_order_id));
        // Auto-fill optional fields the AI/regex captured.
        if (o.box_dimensions) {
          const bd = String(o.box_dimensions).trim();
          // Try to split a "30x20x10 cm" or "30 x 20 x 10" into 3 boxes.
          const parts = bd.split(/x|×|\*/i).map((s) => s.replace(/[^\d.]/g, "")).filter(Boolean);
          if (parts.length >= 1) setBoxL(parts[0] || "");
          if (parts.length >= 2) setBoxW(parts[1] || "");
          if (parts.length >= 3) setBoxH(parts[2] || "");
          setBoxDimensions(bd);
        }
        if (o.shipment_notes) setShipmentNotes(String(o.shipment_notes));
        // Token / advance amount comes through as a dedicated field —
        // never embedded inside notes.
        if (o.token_amount) {
          setTokenAmount(String(o.token_amount));
        }
        // Stash raw row for per-shipment custom field auto-fill (runs in
        // separate effect once customFields load from Settings).
        if (o.raw && typeof o.raw === "object") {
          setPrefillRaw(o.raw as Record<string, string>);
        }
      } catch {
        // ignore
      }
    }
  }, [params.prefill]);

  // Auto-fill per-shipment custom fields from sheet row — runs when either
  // the prefillRaw (from orders.tsx ship-now) OR customFields (from Settings)
  // becomes available, so the two-way race is handled cleanly.
  useEffect(() => {
    if (!prefillRaw || !customFields || customFields.length === 0) return;
    // Normalise raw headers once for forgiving match.
    const rawNorm: Record<string, string> = {};
    for (const k of Object.keys(prefillRaw)) {
      const norm = k.trim().toLowerCase().replace(/[\s_\-]+/g, " ");
      rawNorm[norm] = String((prefillRaw as any)[k] ?? "");
    }
    const add: Record<string, string> = {};
    for (const cf of customFields) {
      if (!cf?.enabled || cf.source !== "shipment") continue;
      const col = String(cf.sheet_column || "").trim();
      if (!col) continue;
      const norm = col.toLowerCase().replace(/[\s_\-]+/g, " ");
      const v = (rawNorm[norm] || "").trim();
      if (v) add[cf.id] = v;
    }
    if (Object.keys(add).length > 0) {
      setCustomValues((prev) => ({ ...prev, ...add }));
    }
  }, [prefillRaw, customFields]);

  const openImport = useCallback(async () => {
    if (!sheetConnected) {
      Alert.alert(
        "Google Sheet not connected",
        "Go to Settings → Google Sheet and paste your sheet link first."
      );
      return;
    }
    setShowImport(true);
    setImportLoading(true);
    try {
      const res = await Api.sheetsOrders();
      setImportOrders(res.orders);
      if (res.headers_changed) {
        Alert.alert(
          "Sheet columns changed",
          "Your sheet's column structure has changed. Open Settings → Google Sheet to re-map columns."
        );
      }
    } catch (e: any) {
      Alert.alert("Import error", e?.response?.data?.detail || e?.message || "Failed");
      setShowImport(false);
    } finally {
      setImportLoading(false);
    }
  }, [sheetConnected]);

  const pickOrder = (o: SheetOrder) => {
    const addr = splitAddress(o.address);
    setOrderId(o.order_id);
    setCustomerName(o.customer_name);
    setCustomerPhone(o.phone);
    // Phase-6 single-address-field — no more line2 split.
    setAddr1((addr.line1 || "").slice(0, 300));
    setAddr2("");
    setCity(o.city || addr.city);
    setState(o.state || addr.state);
    setPincode(o.pincode || addr.pincode);
    const amt = (o.amount || "").replace(/[^\d.]/g, "");
    setAmount(amt);
    const items = (o.item || "")
      .split(/[,\n;|]/)
      .map((s) => s.trim())
      .filter(Boolean);
    setItemsText(items.join("\n"));
    setSheetRowKey(o.row_key);

    // ---- Auto-fill per-shipment custom fields from Google Sheet columns ----
    // Each custom field with source="shipment" and a non-empty sheet_column
    // will look up the value from o.raw using a forgiving match (trim +
    // case-insensitive on both header name and underscore/space variants).
    const raw = o.raw || {};
    // Pre-normalise raw keys once for O(1) lookup.
    const rawNorm: Record<string, string> = {};
    for (const k of Object.keys(raw)) {
      const norm = k.trim().toLowerCase().replace(/[\s_\-]+/g, " ");
      rawNorm[norm] = String(raw[k] ?? "");
    }
    const nextCv: Record<string, string> = {};
    let filledCount = 0;
    for (const cf of customFields) {
      if (!cf?.enabled) continue;
      if (cf.source !== "shipment") continue;
      const col = String(cf.sheet_column || "").trim();
      if (!col) continue;
      const norm = col.toLowerCase().replace(/[\s_\-]+/g, " ");
      const val = (rawNorm[norm] || "").trim();
      if (val) {
        nextCv[cf.id] = val;
        filledCount += 1;
      }
    }
    if (filledCount > 0) {
      setCustomValues((prev) => ({ ...prev, ...nextCv }));
    }

    setShowImport(false);
  };

  const resetForm = () => {
    setOrderId("");
    setCustomerName("");
    setCustomerPhone("");
    setAddr1("");
    setAddr2("");
    setCity("");
    setState("");
    setPincode("");
    setPaymentMode(null);
    setAmount("");
    setItemsText("");
    setWeight("");
    setTokenAmount("");
    setBoxDimensions("");
    setShipmentNotes("");
    setSheetRowKey("");
    setSelectedCourier(null);
    setAutoTracking(null);
    setTrackingId("");
    setCustomValues({});
  };

  // Does form have unsaved content? Used by Cancel confirmation.
  const hasFormContent = (): boolean => {
    return Boolean(
      customerName.trim() ||
      customerPhone.trim() ||
      orderId.trim() ||
      addr1.trim() ||
      city.trim() ||
      amount.trim() ||
      itemsText.trim() ||
      trackingId.trim() ||
      selectedCourier ||
      paymentMode
    );
  };

  const onCancel = () => {
    if (!hasFormContent()) {
      router.replace("/(tabs)");
      return;
    }
    Alert.alert(
      "Discard this shipment?",
      "All entered details will be lost. No tracking number will be consumed.",
      [
        { text: "Keep editing", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            resetForm();
            router.replace("/(tabs)");
          },
        },
      ]
    );
  };

  const save = useCallback(
    async (thenPrint: boolean) => {
      // --- Explicit-choice validation (no silent defaults) ---
      if (!selectedCourier) {
        Alert.alert(
          "Select Courier",
          "Please pick a courier partner before saving.",
          [{ text: "OK" }]
        );
        return;
      }
      if (autoTracking === null) {
        Alert.alert(
          "Tracking ID mode required",
          'Please choose either "Auto Series" or "Manual / Scan" for the tracking ID.',
          [{ text: "OK" }]
        );
        return;
      }
      if (!paymentMode) {
        Alert.alert(
          "Select Payment Mode",
          "Please pick either COD or Prepaid before saving.",
          [{ text: "OK" }]
        );
        return;
      }
      // Hard block: Prepaid + token is a data-entry mistake (full amount
      // already paid — token makes no sense). User must either clear token
      // or switch to COD.
      if (paymentMode === "Prepaid" && Number(tokenAmount) > 0) {
        Alert.alert(
          "Token not valid for Prepaid",
          "This order is Prepaid (full amount already paid). A Token/Advance only makes sense for COD. Please clear the token amount OR switch payment mode to COD.",
          [
            { text: "Keep editing", style: "cancel" },
            {
              text: "Clear token",
              onPress: () => setTokenAmount(""),
            },
            {
              text: "Switch to COD",
              onPress: () => setPaymentMode("COD"),
            },
          ]
        );
        return;
      }
      if (!customerName.trim()) {
        Alert.alert("Validation", "Customer name is required");
        return;
      }
      // Weight is mandatory — couriers refuse parcels without weight, and
      // rate calculation depends on it.
      if (!weight.trim()) {
        Alert.alert(
          "Weight required",
          "Please enter the parcel weight before saving. Couriers cannot accept a shipment without weight.",
          [{ text: "OK" }]
        );
        return;
      }
      if (!autoTracking && !trackingId.trim()) {
        Alert.alert(
          "Tracking ID required",
          "Enter a tracking ID or switch to Auto Series.",
          [{ text: "OK" }]
        );
        return;
      }

      setSaving(true);
      try {
        let finalTracking = trackingId.trim();
        if (autoTracking && selectedCourier) {
          // NOTE: This CONSUMES the next tracking number in the courier's
          // series. Only happens after explicit user confirmation via Save.
          const r = await Api.consumeTracking(selectedCourier.id);
          finalTracking = r.tracking_id;
        }
        const items = itemsText
          .split(/\n|,|;/)
          .map((s) => s.trim())
          .filter(Boolean);
        const payload = {
          tracking_id: finalTracking,
          courier_id: selectedCourier?.id,
          courier_name: selectedCourier?.name,
          // Phase-7e: pass the previewed master ID so backend uses
          // exactly THIS value (no surprise drift if other shipments
          // were saved between form-open and Save). Empty string when
          // auto-gen is OFF — backend will then require user order_id.
          master_order_id: previewMasterId,
          order_id: orderId.trim(),
          customer_name: customerName.trim(),
          customer_phone: customerPhone.trim(),
          customer_alt_phone: customerAltPhone.trim(),
          // Phase-6 single-address-field — line1 holds the entire
          // address; line2 is always blank under the new UX. Backend
          // schema kept for back-compat with old shipments.
          address_line1: addr1.trim().slice(0, 300),
          address_line2: "",
          city: city.trim(),
          state: state.trim(),
          pincode: pincode.trim(),
          payment_mode: paymentMode,
          amount: Number(amount) || 0,
          cod_amount: paymentMode === "COD"
            ? Math.max(0, (Number(amount) || 0) - (Number(tokenAmount) || 0))
            : 0,
          token_amount: Number(tokenAmount) || 0,
          box_dimensions: boxDimensions.trim(),
          shipment_notes: shipmentNotes.trim(),
          items,
          item_description: items.join(", "),
          weight: weight.trim() ? `${weight.trim()} ${weightUnit}` : "",
          sheet_row_key: sheetRowKey,
          custom_values: (() => {
            // Keep only values for fields that still exist + are enabled
            // + use per-shipment source. Trim empty strings.
            const out: Record<string, string> = {};
            for (const cf of customFields) {
              if (!cf?.enabled || cf?.source !== "shipment") continue;
              const v = (customValues[cf.id] || "").trim();
              if (v) out[cf.id] = v;
            }
            return out;
          })(),
        };
        // Edit mode → PUT existing shipment, otherwise POST a new one.
        const created = editingShipmentId
          ? await Api.updateShipment(editingShipmentId, payload as any)
          : await Api.createShipment(payload as any);
        // Persist last-used choices (hints for next entry, never defaults).
        try {
          await Promise.all([
            AsyncStorage.setItem(LS_LAST_COURIER, selectedCourier.id),
            AsyncStorage.setItem(LS_LAST_PAYMENT, paymentMode),
            AsyncStorage.setItem(LS_LAST_TRACK_MODE, autoTracking ? "auto" : "manual"),
          ]);
        } catch {/* non-critical */}
        // If shipment came from a pending order (Smart Paste queue), mark it shipped
        if (pendingOrderId) {
          try {
            await Api.updatePendingOrder(pendingOrderId, {
              status: "shipped",
              shipment_id: created.id,
              tracking_id: created.tracking_id,
              processed_at: new Date().toISOString(),
            } as any);
          } catch {/* ignore */}
        }
        resetForm();
        if (thenPrint) {
          router.replace(`/label/${created.id}`);
        } else {
          Alert.alert("Saved", `Shipment ${created.tracking_id} saved.`, [
            { text: "OK", onPress: () => router.replace("/(tabs)/shipments") },
          ]);
        }
      } catch (e: any) {
        // Phase-5+: On a network-style failure, save to the offline queue
        // instead of losing the user's data. Both Create and Edit are now
        // queue-able. Permanent (4xx) errors still bubble to the user.
        const isNetworkErr =
          !e?.response &&
          /network|timeout|abort|err_network/i.test(String(e?.message || ""));
        if (isNetworkErr) {
          try {
            if (editingShipmentId) {
              await SyncQueue.enqueueShipmentUpdate(editingShipmentId, payload, customerName);
            } else {
              await SyncQueue.enqueueShipmentCreate(payload, customerName);
            }
            resetForm();
            Alert.alert(
              "Saved offline",
              "We couldn't reach the server. Your changes are queued and will sync automatically when you're back online.",
              [{ text: "OK", onPress: () => router.replace("/(tabs)/shipments") }],
            );
            return;
          } catch (qErr: any) {
            Alert.alert(
              "Couldn't queue",
              qErr?.message || "Unable to save offline. Please try again with internet.",
            );
            return;
          }
        }
        Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to save");
      } finally {
        setSaving(false);
      }
    },
    [
      autoTracking,
      selectedCourier,
      trackingId,
      orderId,
      customerName,
      customerPhone,
      addr1,
      addr2,
      city,
      state,
      pincode,
      paymentMode,
      amount,
      itemsText,
      weight,
      sheetRowKey,
      router,
    ]
  );

  const filteredImport = importOrders.filter((o) => {
    if (importFilter === "pending" && o.already_shipped) return false;
    const q = importSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      o.order_id.toLowerCase().includes(q) ||
      o.customer_name.toLowerCase().includes(q) ||
      o.phone.toLowerCase().includes(q) ||
      o.city.toLowerCase().includes(q)
    );
  });

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>
          {editingShipmentId ? "Edit Shipment" : "New Shipment"}
        </Text>
        <TouchableOpacity
          testID="scan-tracking-btn"
          onPress={() => router.push("/scanner?returnTo=add&from=add")}
          style={styles.scanPill}
        >
          <Ionicons name="scan" size={16} color={colors.primary} />
          <Text style={styles.scanPillText}>Scan</Text>
        </TouchableOpacity>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          testID="add-scroll"
          contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
          keyboardShouldPersistTaps="handled"
        >
          {/* Import from Sheet */}
          <TouchableOpacity
            testID="import-from-sheet-btn"
            onPress={openImport}
            style={[
              styles.importBtn,
              !sheetConnected && { opacity: 0.55 },
            ]}
          >
            <Ionicons name="cloud-download" size={20} color="#fff" />
            <View style={{ flex: 1 }}>
              <Text style={styles.importBtnTitle}>
                {sheetConnected ? "Import from Google Sheet" : "Connect Google Sheet in Settings"}
              </Text>
              <Text style={styles.importBtnSub}>
                {sheetConnected
                  ? "Auto-fill customer, order, amount from your form/sheet"
                  : "Settings → Google Sheet → paste link"}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color="#fff" />
          </TouchableOpacity>

          {/* Courier */}
          <Section title="Courier Partner *">
            {!selectedCourier && (
              <Text style={styles.requiredHint}>
                Please pick a courier below
              </Text>
            )}
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: 8, paddingRight: 16 }}
            >
              {couriers.map((c) => {
                const active = selectedCourier?.id === c.id;
                const isLastUsed = !active && c.id === lastCourierId;
                return (
                  <TouchableOpacity
                    key={c.id}
                    testID={`courier-pill-${c.name}`}
                    style={[styles.pill, active && styles.pillActive]}
                    onPress={() => setSelectedCourier(c)}
                  >
                    {isLastUsed && (
                      <View style={styles.lastBadge}>
                        <Text style={styles.lastBadgeText}>Last used</Text>
                      </View>
                    )}
                    <Text style={[styles.pillText, active && { color: "#fff" }]}>
                      {c.name}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </Section>

          {/* Tracking */}
          <Section title="Tracking ID *">
            {autoTracking === null && (
              <Text style={styles.requiredHint}>
                Choose how you want to assign the tracking ID
              </Text>
            )}
            <View style={styles.toggleRow}>
              <TouchableOpacity
                testID="auto-tracking-toggle"
                style={[styles.toggleBtn, autoTracking === true && styles.toggleBtnActive]}
                onPress={() => {
                  setAutoTracking(true);
                  if (nextPreview) setTrackingId(nextPreview);
                }}
              >
                {autoTracking !== true && lastTrackMode === "auto" && (
                  <View style={styles.lastBadgeSm}>
                    <Text style={styles.lastBadgeSmText}>Last</Text>
                  </View>
                )}
                <Ionicons
                  name="repeat"
                  size={14}
                  color={autoTracking === true ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, autoTracking === true && { color: "#fff" }]}>
                  Auto Series
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="manual-tracking-toggle"
                style={[styles.toggleBtn, autoTracking === false && styles.toggleBtnActive]}
                onPress={() => {
                  setAutoTracking(false);
                  // Clear any preview value so user types fresh
                  if (trackingId === nextPreview) setTrackingId("");
                }}
              >
                {autoTracking !== false && lastTrackMode === "manual" && (
                  <View style={styles.lastBadgeSm}>
                    <Text style={styles.lastBadgeSmText}>Last</Text>
                  </View>
                )}
                <Ionicons
                  name="create-outline"
                  size={14}
                  color={autoTracking === false ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, autoTracking === false && { color: "#fff" }]}>
                  Manual / Scan
                </Text>
              </TouchableOpacity>
            </View>
            <View style={{ position: "relative" }}>
              <TextInput
                testID="tracking-id-input"
                value={trackingId}
                editable={autoTracking === false}
                onChangeText={setTrackingId}
                placeholder={
                  autoTracking === null
                    ? "Pick a mode above first"
                    : autoTracking
                    ? nextPreview
                    : "Enter tracking ID"
                }
                placeholderTextColor="#9CA3AF"
                style={[
                  styles.input,
                  styles.trackingInput,
                  autoTracking === false && { paddingRight: 48 },
                  autoTracking === null && { opacity: 0.6 },
                ]}
                autoCapitalize="characters"
              />
              {autoTracking === false && (
                <TouchableOpacity
                  testID="tracking-inline-scan"
                  onPress={() => router.push("/scanner?returnTo=add&from=add")}
                  style={styles.inlineScanBtn}
                  hitSlop={8}
                >
                  <Ionicons name="camera" size={20} color={colors.primary} />
                </TouchableOpacity>
              )}
            </View>
            {autoTracking === true && nextPreview ? (
              <Text style={styles.hint}>Next auto: {nextPreview}</Text>
            ) : null}
          </Section>

          {/* Order */}
          <Section title="Order Details">
            <Field label="Order ID">
              <TextInput
                testID="order-id-input"
                value={orderId}
                onChangeText={(t) => {
                  setUserTouchedOrderId(true);
                  setOrderId(t);
                }}
                placeholder={
                  orderIdAutoGen && orderIdAutofillNew
                    ? "Auto-filled from Master Order ID"
                    : "Order ID / Invoice #"
                }
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            {orderIdAutoGen && previewMasterId ? (
              <Text style={styles.hint}>
                Master ID (system): {previewMasterId}
                {orderId && orderId !== previewMasterId
                  ? "  ·  Your ID kept separately"
                  : ""}
              </Text>
            ) : null}
            <Field label="Items / Products">
              <TextInput
                testID="items-input"
                value={itemsText}
                onChangeText={setItemsText}
                placeholder="One item per line (or comma separated)"
                placeholderTextColor="#9CA3AF"
                multiline
                style={[styles.input, { height: 80, textAlignVertical: "top", paddingTop: 10 }]}
              />
            </Field>
          </Section>

          {/* Customer */}
          <Section title="Customer">
            <Field label="Name *">
              <TextInput
                testID="customer-name-input"
                value={customerName}
                onChangeText={setCustomerName}
                placeholder="Full name"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Phone">
              <TextInput
                testID="customer-phone-input"
                value={customerPhone}
                onChangeText={setCustomerPhone}
                placeholder="10-digit mobile"
                placeholderTextColor="#9CA3AF"
                keyboardType="phone-pad"
                style={styles.input}
              />
            </Field>
            {flagAltPhone && labelFields.alt_phone && (
              <Field label="Alternative Phone (optional)">
                <TextInput
                  testID="customer-alt-phone-input"
                  value={customerAltPhone}
                  onChangeText={setCustomerAltPhone}
                  placeholder="Secondary 10-digit mobile"
                  placeholderTextColor="#9CA3AF"
                  keyboardType="phone-pad"
                  style={styles.input}
                />
              </Field>
            )}
          </Section>

          {/* Address — single full-address field (Phase-6 2026-04-28).
              Previous version had two separate "Line 1" / "Line 2"
              fields and the Smart Paste AI tried to split addresses
              between them, sometimes truncating. We now use one
              multiline field with a 300-char cap; the AI is instructed
              to put the entire address here, leaving City / State /
              Pincode in their own (separate) fields. */}
          <Section title="Delivery Address">
            <Field label="Address">
              <TextInput
                testID="addr1-input"
                value={addr1}
                onChangeText={(v) => {
                  // Hard 300-char cap, applied even if maxLength is
                  // bypassed (e.g. paste from clipboard on some Android
                  // devices). Prefer truncating silently to losing data.
                  setAddr1(v.length > 300 ? v.slice(0, 300) : v);
                  // Keep legacy line2 always blank so old multi-line
                  // data never sneaks back in.
                  if (addr2) setAddr2("");
                }}
                placeholder="Full address (landmark, area, street)"
                placeholderTextColor="#9CA3AF"
                multiline
                numberOfLines={3}
                maxLength={300}
                style={[styles.input, { minHeight: 70, textAlignVertical: "top", paddingTop: 10 }]}
              />
              <Text style={{
                fontSize: 11,
                color: addr1.length > 280 ? "#DC2626" : "#94A3B8",
                marginTop: 4,
                textAlign: "right",
                fontWeight: addr1.length > 280 ? "700" : "500",
              }}>
                {addr1.length} / 300
              </Text>
            </Field>
            <View style={styles.grid2}>
              <View style={{ flex: 1 }}>
                <Field label="City">
                  <TextInput
                    testID="city-input"
                    value={city}
                    onChangeText={setCity}
                    placeholder="City"
                    placeholderTextColor="#9CA3AF"
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ width: 12 }} />
              <View style={{ flex: 1 }}>
                <Field label="Pincode">
                  <TextInput
                    testID="pincode-input"
                    value={pincode}
                    onChangeText={setPincode}
                    placeholder="6-digit"
                    placeholderTextColor="#9CA3AF"
                    keyboardType="number-pad"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            <Field label="State">
              <TextInput
                testID="state-input"
                value={state}
                onChangeText={setState}
                placeholder="State"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
          </Section>

          {/* Payment & Parcel */}
          <Section title="Payment & Parcel *">
            {!paymentMode && (
              <Text style={styles.requiredHint}>
                Pick COD or Prepaid below
              </Text>
            )}
            <View style={styles.toggleRow}>
              <TouchableOpacity
                testID="prepaid-toggle"
                style={[
                  styles.toggleBtn,
                  paymentMode === "Prepaid" && styles.toggleBtnActive,
                ]}
                onPress={() => setPaymentMode("Prepaid")}
              >
                {paymentMode !== "Prepaid" && lastPaymentMode === "Prepaid" && (
                  <View style={styles.lastBadgeSm}>
                    <Text style={styles.lastBadgeSmText}>Last</Text>
                  </View>
                )}
                <Ionicons
                  name="card"
                  size={14}
                  color={paymentMode === "Prepaid" ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, paymentMode === "Prepaid" && { color: "#fff" }]}>
                  Prepaid
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="cod-toggle"
                style={[
                  styles.toggleBtn,
                  paymentMode === "COD" && styles.toggleBtnActive,
                ]}
                onPress={() => setPaymentMode("COD")}
              >
                {paymentMode !== "COD" && lastPaymentMode === "COD" && (
                  <View style={styles.lastBadgeSm}>
                    <Text style={styles.lastBadgeSmText}>Last</Text>
                  </View>
                )}
                <Ionicons
                  name="cash"
                  size={14}
                  color={paymentMode === "COD" ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, paymentMode === "COD" && { color: "#fff" }]}>
                  COD
                </Text>
              </TouchableOpacity>
            </View>
            <Field label={paymentMode === "COD" ? "COD Amount (₹)" : "Order Amount (₹)"}>
              <TextInput
                testID="amount-input"
                value={amount}
                onChangeText={setAmount}
                placeholder={paymentMode === "COD" ? "Amount to collect" : "Order value"}
                placeholderTextColor="#9CA3AF"
                keyboardType="decimal-pad"
                style={styles.input}
              />
            </Field>
            <Field label="Weight" required>
              <View style={{ flexDirection: "row", gap: 8, alignItems: "stretch" }}>
                <TextInput
                  testID="weight-input"
                  value={weight}
                  onChangeText={setWeight}
                  placeholder={weightUnit === "g" ? "e.g. 250" : "e.g. 0.5"}
                  placeholderTextColor="#9CA3AF"
                  keyboardType="decimal-pad"
                  style={[styles.input, { flex: 1 }]}
                />
                <View style={{ flexDirection: "row", gap: 4 }}>
                  <TouchableOpacity
                    testID="weight-unit-g"
                    onPress={() => setWeightUnit("g")}
                    style={[
                      styles.unitBtn,
                      weightUnit === "g" && styles.unitBtnActive,
                    ]}
                  >
                    <Text style={[styles.unitText, weightUnit === "g" && { color: "#fff" }]}>g</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="weight-unit-kg"
                    onPress={() => setWeightUnit("kg")}
                    style={[
                      styles.unitBtn,
                      weightUnit === "kg" && styles.unitBtnActive,
                    ]}
                  >
                    <Text style={[styles.unitText, weightUnit === "kg" && { color: "#fff" }]}>kg</Text>
                  </TouchableOpacity>
                </View>
              </View>
            </Field>
            {/* Token / Advance — only meaningful for COD.
                If user fills this while Prepaid is selected → we block Save
                and show inline error (common typing-habit mistake). */}
            {flagTokenAmt ? (
            <Field label="Token / Advance Paid (optional)">
              <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
                <View style={{ flex: 1 }}>
                  <TextInput
                    testID="token-amount-input"
                    value={tokenAmount}
                    onChangeText={setTokenAmount}
                    placeholder={paymentMode === "Prepaid" ? "Not needed for Prepaid" : "e.g. 50"}
                    placeholderTextColor="#9CA3AF"
                    keyboardType="decimal-pad"
                    style={[
                      styles.input,
                      paymentMode === "Prepaid" && Number(tokenAmount) > 0 && styles.inputError,
                    ]}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  {(() => {
                    const total = Number(amount) || 0;
                    const tok = Number(tokenAmount) || 0;
                    if (paymentMode === "Prepaid") {
                      return (
                        <View style={[styles.input, { justifyContent: "center", backgroundColor: "#ECFDF5", borderColor: "#A7F3D0" }]}>
                          <Text style={{ color: "#047857", fontWeight: "800" }}>
                            Already paid ✓
                          </Text>
                        </View>
                      );
                    }
                    const cod = Math.max(0, total - tok);
                    return (
                      <View style={[styles.input, { justifyContent: "center", backgroundColor: "#F9FAFB" }]}>
                        <Text style={{ color: tok > 0 ? colors.primary : "#9CA3AF", fontWeight: "700" }}>
                          COD to collect: ₹{cod.toFixed(0)}
                        </Text>
                      </View>
                    );
                  })()}
                </View>
              </View>

              {/* Inline error: Prepaid + token > 0 is invalid */}
              {paymentMode === "Prepaid" && Number(tokenAmount) > 0 ? (
                <View style={styles.tokenErrorBox} testID="token-prepaid-error">
                  <Ionicons name="alert-circle" size={16} color="#B91C1C" />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.tokenErrorTitle}>
                      Token is not valid for Prepaid
                    </Text>
                    <Text style={styles.tokenErrorSub}>
                      Prepaid orders are already fully paid. Remove the token or switch to COD.
                    </Text>
                  </View>
                  <TouchableOpacity
                    testID="clear-token-btn"
                    onPress={() => setTokenAmount("")}
                    style={styles.tokenErrorClearBtn}
                    hitSlop={8}
                  >
                    <Text style={styles.tokenErrorClearText}>Clear</Text>
                  </TouchableOpacity>
                </View>
              ) : paymentMode === "COD" ? (
                <Text style={styles.hint}>
                  Advance already collected is deducted from COD to collect.
                </Text>
              ) : null}
            </Field>
            ) : null}
            {flagBoxDims && labelFields.box_dimensions && (
              <Field label="Box Dimensions (optional)">
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                  <TextInput
                    testID="box-dim-l-input"
                    value={boxL}
                    onChangeText={(v) => {
                      setBoxL(v);
                      setBoxDimensions(`${v}x${boxW}x${boxH}`.replace(/^x|x$|xx/g, ""));
                    }}
                    placeholder="L"
                    placeholderTextColor="#9CA3AF"
                    keyboardType="numeric"
                    style={[styles.input, { flex: 1, textAlign: "center" }]}
                  />
                  <Text style={{ fontWeight: "800", color: "#6B7280" }}>×</Text>
                  <TextInput
                    testID="box-dim-w-input"
                    value={boxW}
                    onChangeText={(v) => {
                      setBoxW(v);
                      setBoxDimensions(`${boxL}x${v}x${boxH}`.replace(/^x|x$|xx/g, ""));
                    }}
                    placeholder="W"
                    placeholderTextColor="#9CA3AF"
                    keyboardType="numeric"
                    style={[styles.input, { flex: 1, textAlign: "center" }]}
                  />
                  <Text style={{ fontWeight: "800", color: "#6B7280" }}>×</Text>
                  <TextInput
                    testID="box-dim-h-input"
                    value={boxH}
                    onChangeText={(v) => {
                      setBoxH(v);
                      setBoxDimensions(`${boxL}x${boxW}x${v}`.replace(/^x|x$|xx/g, ""));
                    }}
                    placeholder="H"
                    placeholderTextColor="#9CA3AF"
                    keyboardType="numeric"
                    style={[styles.input, { flex: 1, textAlign: "center" }]}
                  />
                  <Text style={{ fontSize: 12, color: "#6B7280" }}>cm</Text>
                </View>
              </Field>
            )}
            {flagShipNotes && labelFields.shipment_notes && (
              <Field label="Shipment Notes (optional)">
                <TextInput
                  testID="shipment-notes-input"
                  value={shipmentNotes}
                  onChangeText={setShipmentNotes}
                  placeholder="Fragile / Handle with care / Any special instruction"
                  placeholderTextColor="#9CA3AF"
                  multiline
                  numberOfLines={2}
                  style={[styles.input, { minHeight: 56, textAlignVertical: "top" }]}
                />
              </Field>
            )}
          </Section>

          {/* ---------- Per-Shipment Custom Fields (defined in Settings) ---------- */}
          {(() => {
            const perShipCfs = customFields.filter(
              (cf) => cf && cf.enabled && cf.source === "shipment"
            );
            if (perShipCfs.length === 0) return null;
            return (
              <Section title="Custom Fields" icon="layers-outline">
                <Text style={styles.hint}>
                  Extra label info for this shipment only. Defined in Settings → Custom Label Fields.
                </Text>
                {perShipCfs.map((cf) => {
                  const key = cf.id;
                  const displayLabel =
                    (cf.label || "").replace(/:\s*$/, "") || "Value";
                  return (
                    <Field key={key} label={`${displayLabel}${cf.sheet_column ? "  (from Sheet)" : ""}`}>
                      <TextInput
                        testID={`cf-ship-input-${key}`}
                        value={customValues[key] || ""}
                        onChangeText={(t) =>
                          setCustomValues({ ...customValues, [key]: t })
                        }
                        placeholder={
                          cf.placeholder || "Enter value for this shipment"
                        }
                        placeholderTextColor="#9CA3AF"
                        style={styles.input}
                      />
                      <Text style={styles.hint}>
                        📍 Prints at: {cf.position?.replace(/_/g, " ") || "meta row"}
                      </Text>
                    </Field>
                  );
                })}
              </Section>
            );
          })()}

          <View style={styles.ctaRow}>
            <TouchableOpacity
              testID="cancel-shipment-btn"
              style={styles.cancelBtn}
              disabled={saving}
              onPress={onCancel}
              hitSlop={8}
            >
              <Ionicons name="close" size={18} color="#B91C1C" />
              <Text style={styles.cancelBtnText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="save-shipment-btn"
              style={styles.secondaryBtn}
              disabled={saving}
              onPress={() => save(false)}
            >
              {saving ? (
                <ActivityIndicator color={colors.text} />
              ) : (
                <>
                  <Ionicons
                    name={editingShipmentId ? "checkmark-circle-outline" : "save-outline"}
                    size={18}
                    color={colors.text}
                  />
                  <Text style={styles.secondaryBtnText}>
                    {editingShipmentId ? "Update" : "Save"}
                  </Text>
                </>
              )}
            </TouchableOpacity>
            <TouchableOpacity
              testID="save-print-btn"
              style={styles.primaryBtn}
              disabled={saving}
              onPress={() => save(true)}
            >
              <Ionicons name="print" size={18} color="#fff" />
              <Text style={styles.primaryBtnText}>
                {editingShipmentId ? "Update & Print" : "Save & Print"}
              </Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      {/* Import Modal */}
      <Modal visible={showImport} animationType="slide" onRequestClose={() => setShowImport(false)}>
        <SafeAreaView style={styles.modalSafe}>
          <View style={styles.modalHeader}>
            <TouchableOpacity
              testID="import-close"
              onPress={() => setShowImport(false)}
              style={styles.modalClose}
            >
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Import from Sheet</Text>
            <TouchableOpacity
              testID="import-refresh"
              onPress={openImport}
              style={styles.modalClose}
            >
              <Ionicons name="refresh" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          <View style={styles.modalSearchWrap}>
            <Ionicons name="search" size={16} color={colors.textMuted} />
            <TextInput
              testID="import-search"
              placeholder="Search order, name, phone"
              placeholderTextColor="#9CA3AF"
              value={importSearch}
              onChangeText={setImportSearch}
              style={styles.modalSearch}
            />
          </View>
          <View style={styles.filterRow}>
            <TouchableOpacity
              testID="import-filter-pending"
              onPress={() => setImportFilter("pending")}
              style={[
                styles.filterPill,
                importFilter === "pending" && styles.filterPillActive,
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  importFilter === "pending" && { color: "#fff" },
                ]}
              >
                Not yet shipped
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="import-filter-all"
              onPress={() => setImportFilter("all")}
              style={[
                styles.filterPill,
                importFilter === "all" && styles.filterPillActive,
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  importFilter === "all" && { color: "#fff" },
                ]}
              >
                All {importOrders.length ? `(${importOrders.length})` : ""}
              </Text>
            </TouchableOpacity>
          </View>

          {importLoading ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 24 }} />
          ) : (
            <FlatList
              data={filteredImport}
              // See orders.tsx for the rationale — row_key alone is
              // not unique when two sheet rows hash identically (same
              // amount + same customer name). Suffix with row_index.
              keyExtractor={(o, idx) => `${o.row_key || "row"}|${o.row_index ?? idx}`}
              contentContainerStyle={{ padding: 12, paddingBottom: 32 }}
              ListEmptyComponent={
                <Text style={styles.emptyImport}>
                  {importOrders.length === 0
                    ? "No rows found in your sheet."
                    : "No matching orders."}
                </Text>
              }
              renderItem={({ item }) => (
                <TouchableOpacity
                  testID={`import-row-${item.row_index}`}
                  onPress={() => pickOrder(item)}
                  style={[
                    styles.orderCard,
                    item.already_shipped && { opacity: 0.55 },
                  ]}
                >
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                      <Text style={styles.orderCustomer}>
                        {item.customer_name || "(no name)"}
                      </Text>
                      {item.already_shipped && (
                        <View style={styles.shippedChip}>
                          <Text style={styles.shippedChipText}>SHIPPED</Text>
                        </View>
                      )}
                    </View>
                    <Text style={styles.orderLine}>
                      {item.order_id ? `Order #${item.order_id} · ` : ""}
                      {item.phone || "no phone"}
                    </Text>
                    <Text style={styles.orderLine} numberOfLines={1}>
                      {[item.city, item.state, item.pincode].filter(Boolean).join(", ")}
                    </Text>
                    <Text style={styles.orderItem} numberOfLines={2}>
                      📦 {item.item || "—"} · ₹{item.amount || "0"}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={20} color={colors.textMuted} />
                </TouchableOpacity>
              )}
            />
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Field({
  label,
  children,
  required,
}: {
  label: string;
  children: React.ReactNode;
  required?: boolean;
}) {
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.fieldLabel}>
        {label}
        {required ? <Text style={{ color: "#DC2626" }}> *</Text> : null}
      </Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 10,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  scanPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 2,
    borderColor: colors.primary,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
  },
  scanPillText: { color: colors.primary, fontWeight: "700" },
  importBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.secondary,
    borderRadius: 12,
    padding: 14,
    marginBottom: 14,
  },
  importBtnTitle: { color: "#fff", fontWeight: "800", fontSize: 14 },
  importBtnSub: { color: "rgba(255,255,255,0.7)", fontSize: 11, marginTop: 2 },
  section: {
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 14,
    marginBottom: 14,
  },
  sectionTitle: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
    marginBottom: 10,
  },
  fieldLabel: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "700",
    marginBottom: 6,
  },
  input: {
    minHeight: 46,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 14,
    fontSize: 15,
    color: colors.text,
    backgroundColor: colors.surface,
  },
  trackingInput: {
    fontFamily: "Courier",
    fontWeight: "800",
    letterSpacing: 2,
    fontSize: 17,
  },
  inlineScanBtn: {
    position: "absolute",
    right: 6,
    top: 0,
    bottom: 0,
    width: 40,
    alignItems: "center",
    justifyContent: "center",
  },
  pill: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  pillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  pillText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  toggleRow: { flexDirection: "row", gap: 8, marginBottom: 10 },
  toggleBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
  },
  toggleBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  toggleText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  hint: { fontSize: 12, color: colors.textMuted, marginTop: 6 },
  requiredHint: {
    fontSize: 12,
    fontWeight: "700",
    color: "#B45309",
    backgroundColor: "#FFFBEB",
    borderColor: "#FDE68A",
    borderWidth: 1,
    borderRadius: 8,
    paddingVertical: 6,
    paddingHorizontal: 10,
    marginBottom: 8,
    overflow: "hidden",
  },
  lastBadge: {
    position: "absolute",
    top: -6,
    right: -6,
    backgroundColor: "#7C3AED",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 10,
    zIndex: 2,
  },
  lastBadgeText: {
    color: "#fff",
    fontSize: 9,
    fontWeight: "800",
    includeFontPadding: false,
    letterSpacing: 0.3,
  },
  lastBadgeSm: {
    position: "absolute",
    top: -5,
    right: -5,
    backgroundColor: "#7C3AED",
    paddingHorizontal: 5,
    paddingVertical: 1.5,
    borderRadius: 8,
    zIndex: 2,
  },
  lastBadgeSmText: {
    color: "#fff",
    fontSize: 8.5,
    fontWeight: "800",
    includeFontPadding: false,
    letterSpacing: 0.3,
  },
  grid2: { flexDirection: "row" },
  ctaRow: { flexDirection: "row", gap: 8, marginTop: 6 },
  cancelBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 52,
    paddingHorizontal: 14,
    backgroundColor: "#FEF2F2",
    borderWidth: 2,
    borderColor: "#FECACA",
    borderRadius: 12,
  },
  cancelBtnText: { fontWeight: "800", color: "#B91C1C" },

  /* Inline validation: token + Prepaid mismatch */
  inputError: {
    borderColor: "#DC2626",
    backgroundColor: "#FEF2F2",
  },
  tokenErrorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 8,
    paddingVertical: 8,
    paddingHorizontal: 10,
    backgroundColor: "#FEF2F2",
    borderColor: "#FECACA",
    borderWidth: 1,
    borderRadius: 10,
  },
  tokenErrorTitle: {
    fontSize: 13,
    fontWeight: "800",
    color: "#B91C1C",
  },
  tokenErrorSub: {
    fontSize: 11.5,
    color: "#7F1D1D",
    marginTop: 1,
    lineHeight: 15,
  },
  tokenErrorClearBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: "#B91C1C",
    borderRadius: 8,
  },
  tokenErrorClearText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 12,
  },
  secondaryBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.secondary,
    borderRadius: 12,
  },
  secondaryBtnText: { fontWeight: "800", color: colors.text },
  primaryBtn: {
    flex: 1.3,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: colors.primary,
    borderRadius: 12,
  },
  primaryBtnText: { fontWeight: "800", color: "#fff" },

  modalSafe: { flex: 1, backgroundColor: colors.background },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  modalClose: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: "#F3F4F6",
    justifyContent: "center",
    alignItems: "center",
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  modalSearchWrap: {
    marginHorizontal: 12,
    marginTop: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    height: 44,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    backgroundColor: "#fff",
  },
  modalSearch: { flex: 1, color: colors.text, fontSize: 14 },
  filterRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  filterPill: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: { fontWeight: "700", fontSize: 12, color: colors.text },
  orderCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  orderCustomer: { fontSize: 15, fontWeight: "800", color: colors.text },
  orderLine: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  orderItem: { fontSize: 12, color: colors.text, marginTop: 4, fontWeight: "600" },
  shippedChip: {
    backgroundColor: colors.successBg,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  shippedChipText: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.successText,
    letterSpacing: 0.5,
  },
  emptyImport: {
    textAlign: "center",
    color: colors.textMuted,
    marginTop: 30,
  },
  unitBtn: {
    width: 44,
    height: 46,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#fff",
  },
  unitBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  unitText: { fontWeight: "800", color: colors.text, fontSize: 13 },
});
