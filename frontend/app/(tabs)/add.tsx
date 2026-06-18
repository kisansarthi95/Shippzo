import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
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
  Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, useFocusEffect } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Api, Courier, SheetOrder } from "../../lib/api";
import { SyncQueue } from "../../lib/syncQueue";
import { scannerBridge } from "../../lib/scannerBridge";
import { validateTrackingId } from "../../lib/trackingValidator";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";
import { useFieldConfig } from "../../lib/fieldConfig";
// Phase-31 shared helper — canonical (amount, cod_amount, token) math.
import { computeOrderAmounts } from "../../lib/orderAmounts";

// AsyncStorage keys for "last used" memory — shown as hints (not defaults).
const LS_LAST_COURIER = "@csm/lastCourierId";
const LS_LAST_PAYMENT = "@csm/lastPaymentMode";
const LS_LAST_TRACK_MODE = "@csm/lastTrackMode";

/**
 * Phase-9 unified-address fix (2026-04-30):
 * The address is now ONE string from end to end. We never split it into
 * line1/line2, never try to extract city/state from comma-separated
 * tokens, and never re-parse it. The Smart Paste AI / Google Sheet /
 * pending-order flows all provide `city`, `state`, `pincode` as
 * separate fields anyway, so the address string can be used verbatim.
 *
 * This finally kills the recurring "address gets truncated to first
 * comma chunk" bug that plagued the New Shipment form for 9+ rounds:
 * the legacy `splitAddress(full)` helper was tokenising on commas and
 * dropping everything after the first comma into city/state slots,
 * even though those slots were already correctly populated upstream.
 *
 * The legacy `splitAddress` helper was DELETED. Use `o.address`
 * (or `o.address_line1` for the rare back-compat case where the
 * server still produced legacy split fields) as the FULL address.
 */
function fullAddressFrom(o: any): string {
  // Priority order:
  //   1. The unified `o.address` string (new pipeline)
  //   2. Legacy `address_line1` + `address_line2` joined (very old data
  //      that was written before the single-field migration)
  //   3. Empty string fallback.
  const unified = String(o?.address || "").trim();
  if (unified) return unified.slice(0, 300);
  const l1 = String(o?.address_line1 || "").trim();
  const l2 = String(o?.address_line2 || "").trim();
  if (l1 && l2 && l2 !== "-") return `${l1}, ${l2}`.slice(0, 300);
  return (l1 || l2).slice(0, 300);
}

export default function AddShipment() {
  const router = useRouter();
  const params = useLocalSearchParams<{ scanned?: string; fromSheet?: string; prefill?: string }>();

  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [selectedCourier, setSelectedCourier] = useState<Courier | null>(null);
  // Phase-35 — Inquiry CTA. Shows a small premium pill button on the
  // Courier Partner section header that opens an editable WhatsApp /
  // SMS draft pre-populated with the current shipment's details.
  // The button only activates after a courier is picked, and the
  // outgoing inquiry uses ONLY the selected courier's contact phone.
  const [inquiryOpen, setInquiryOpen] = useState(false);
  const [inquiryDraft, setInquiryDraft] = useState("");
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
  // Phase-3 Smart Paste enhancement: optional B2B fields
  const [customerEmail, setCustomerEmail] = useState("");
  const [customerGstin, setCustomerGstin] = useState("");
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
  // NEW (2026-04-30) — Per-user custom fields (the plan-gated kind from
  // Settings → Manage Custom Fields). Stored separately from the legacy
  // CustomLabelField list because the schemas differ (column_letter,
  // show_in_form, etc).
  const [userCustomFields, setUserCustomFields] = useState<
    Array<{
      id: string;
      name: string;
      column_letter: string;
      field_type: "text" | "number" | "date";
      show_in_form: boolean;
      show_in_smart_paste: boolean;
      active: boolean;
    }>
  >([]);
  const [userCustomValues, setUserCustomValues] = useState<
    Record<string, string>
  >({});
  const [customValues, setCustomValues] = useState<Record<string, string>>({});

  const [sheetConnected, setSheetConnected] = useState(false);
  // Phase-8: per-field "Required" toggles loaded from settings.
  const [fieldReqs, setFieldReqs] = useState<Record<string, boolean>>({});
  const [showImport, setShowImport] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [importOrders, setImportOrders] = useState<SheetOrder[]>([]);
  const [importFilter, setImportFilter] = useState<"pending" | "all">("pending");
  const [importSearch, setImportSearch] = useState("");

  // Phase 2 — Packing Variant state. When the user picks a courier we
  // load that courier's variants and offer a chip row so they can fill
  // weight / dimensions / rate in one tap. `originState` comes from the
  // user's sender address in Settings → Business Profile and drives the
  // within-vs-outside-state rate choice.
  const [variants, setVariants] = useState<Array<{
    id: string;
    variant_name: string;
    package_type: string;
    category: string;
    length_cm: number;
    width_cm: number;
    height_cm: number;
    weight_g: number;
    within_state_rate: number;
    outside_state_rate: number;
    active: boolean;
  }>>([]);
  const [selectedVariant, setSelectedVariant] = useState<typeof variants[0] | null>(null);
  const [originState, setOriginState] = useState<string>("");
  // Phase 2D — Flexible Variant mode. Lets the user mix-and-match
  // dimensions / weight / package-type / category from existing fixed
  // variants when the order doesn't match any variant exactly. Rate is
  // derived from the closest matching variant + delivery basis radio.
  const [flexibleMode, setFlexibleMode] = useState(false);
  const [flexDim, setFlexDim]           = useState<string>("");   // "L×W×H" string
  const [flexWeightG, setFlexWeightG]   = useState<number>(0);
  const [flexPkgType, setFlexPkgType]   = useState<string>("");
  const [flexCategory, setFlexCategory] = useState<string>("");
  const [flexBasis, setFlexBasis]       = useState<"within_state" | "outside_state" | "">("");
  const [flexRate, setFlexRate]         = useState<string>("");
  // Phase 2D-update — Custom dim / weight / category support.
  const [showDimCustom, setShowDimCustom]       = useState(false);
  const [customDimL, setCustomDimL]             = useState("");
  const [customDimW, setCustomDimW]             = useState("");
  const [customDimH, setCustomDimH]             = useState("");
  const [showWeightCustom, setShowWeightCustom] = useState(false);
  const [customWeightG, setCustomWeightG]       = useState("");
  // User-defined custom categories — loaded once on mount, mutable
  // via a small "+ Add Category" affordance under the Other chip.
  const [customCategories, setCustomCategories] = useState<string[]>([]);
  const [showAddCategory, setShowAddCategory]   = useState(false);
  const [newCategoryName, setNewCategoryName]   = useState("");
  // Rate basis captured at save time — derived from origin-vs-destination
  // state comparison. Exposed here so the UI can preview which rate is
  // currently applicable for the picked variant.
  const rateBasis: "within_state" | "outside_state" | "" = (() => {
    if (!originState || !state) return "";
    return originState.trim().toLowerCase() === state.trim().toLowerCase()
      ? "within_state"
      : "outside_state";
  })();

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
      setFieldReqs(((settings as any).field_requirements || {}) as Record<string, boolean>);
      // Phase 2 — Origin state for variant rate (within vs outside).
      setOriginState(((settings as any).sender?.state || "").toString().trim());
      // Per-user custom fields (plan-gated, defined in Manage Custom Fields).
      // Loaded best-effort — never blocks the form.
      Api.listMyCustomFields()
        .then((r) => {
          if (r?.feature_enabled) {
            setUserCustomFields(
              (r.fields || []).filter(
                (f: any) => f.active !== false && f.show_in_form,
              ) as any,
            );
          } else {
            setUserCustomFields([]);
          }
        })
        .catch(() => setUserCustomFields([]));
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

  // Phase 2 — Load variants whenever the user picks a courier (or
  // switches). Also resets the current selection so we never apply a
  // stale variant from the previous courier.
  useEffect(() => {
    setSelectedVariant(null);
    if (!selectedCourier?.id) {
      setVariants([]);
      return;
    }
    let cancelled = false;
    Api.listCourierVariants(selectedCourier.id)
      .then((r) => {
        if (cancelled) return;
        const active = (r.variants || []).filter((v: any) => v.active !== false);
        setVariants(active as any);
      })
      .catch(() => { if (!cancelled) setVariants([]); });
    return () => { cancelled = true; };
  }, [selectedCourier?.id]);

  // Phase 2 — Apply a variant: auto-fill weight, dims and rate. User
  // can still override anything; we only fill empty-or-matching fields
  // to avoid clobbering manual edits. Rate is applied only when the
  // amount field is empty so Prepaid orders aren't overwritten.
  const applyVariant = useCallback((v: typeof variants[0]) => {
    setFlexibleMode(false);                          // exit flex mode
    setSelectedVariant(v);
    // Weight
    if (v.weight_g) {
      setWeight(String(v.weight_g));
      setWeightUnit("g");
    }
    // Dimensions
    if (v.length_cm) setBoxL(String(v.length_cm));
    if (v.width_cm)  setBoxW(String(v.width_cm));
    if (v.height_cm) setBoxH(String(v.height_cm));
    // Phase-39 — Rate auto-fill into `amount` REMOVED.
    //
    // The variant's within/outside-state rate must populate ONLY the
    // `rate_applied` field on the save payload (i.e. the courier
    // charge, not the customer-collectable amount). Auto-filling
    // the visible Amount input was confusing operators because
    // "Amount" is the order total the customer pays (COD value /
    // prepaid order value) and is conceptually different from the
    // courier rate. The Amount field is now exclusively populated by:
    //   * Smart Fill / Smart Paste extraction
    //   * Manual user input
    //   * Prefill from sheet / pending order
    //
    // `rate_applied` (the variant's chosen rate) is computed at
    // submit-time from `selectedVariant` + the within/outside-state
    // basis in the save handler, so no state we need to set here.
    //
    // Original logic preserved as a comment for archaeology:
    //
    // const currentAmt = parseFloat(amount) || 0;
    // if (!currentAmt) {
    //   const basis: "within_state" | "outside_state" = (() => {
    //     if (!originState || !state) return "outside_state";
    //     return originState.trim().toLowerCase() === state.trim().toLowerCase()
    //       ? "within_state" : "outside_state";
    //   })();
    //   const rate = basis === "within_state" ? v.within_state_rate : v.outside_state_rate;
    //   if (rate) setAmount(String(rate));
    // }
  }, []);

  // Phase 2D — Flexible Mode helpers ------------------------------
  // Aggregate distinct dimensions / weights / package_types / categories
  // from the current courier's variants so the chip selectors are
  // populated from real data the user has already defined.
  const flexDimChips = useMemo(() => {
    const seen = new Set<string>();
    const out: Array<{ label: string; l: number; w: number; h: number }> = [];
    for (const v of variants) {
      if (!v.length_cm && !v.width_cm && !v.height_cm) continue;
      const key = `${v.length_cm}×${v.width_cm}×${v.height_cm}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ label: key, l: v.length_cm, w: v.width_cm, h: v.height_cm });
    }
    return out;
  }, [variants]);

  const flexWeightChips = useMemo(() => {
    // Always include common ladder + any distinct weights from variants.
    const fixed = [100, 200, 500, 1000, 2000, 3000, 5000];
    const extra = Array.from(new Set(
      variants.map((v) => v.weight_g).filter((g) => g && !fixed.includes(g)),
    )).sort((a, b) => a - b);
    return [...fixed, ...extra];
  }, [variants]);

  const flexPkgChips = useMemo(() => {
    const PRESET = ["Cover", "Poly Bag", "Small Box", "Medium Box", "Large Box", "Tube"];
    const extra = Array.from(new Set(
      variants.map((v) => (v.package_type || "").trim()).filter((p) => p && !PRESET.includes(p)),
    ));
    return [...PRESET, ...extra];
  }, [variants]);

  const flexCatChips = useMemo(() => {
    const PRESET = ["Electronics", "Clothing", "Medical", "Documents", "Home Goods", "Other"];
    const variantCats = Array.from(new Set(
      variants.map((v) => (v.category || "").trim()).filter((c) => c && !PRESET.includes(c)),
    ));
    // Custom categories merged with variant-derived ones, dedup'd.
    const all = Array.from(new Set([...PRESET, ...customCategories, ...variantCats]));
    return all;
  }, [variants, customCategories]);

  // Phase 2D-update — Load user's custom categories once. Keeps the
  // chip list reactive so a category added inline re-renders both the
  // Flexible picker and the Fixed Variants screen on next visit.
  useEffect(() => {
    let cancelled = false;
    Api.listMyCategories()
      .then((r) => { if (!cancelled) setCustomCategories(r.custom || []); })
      .catch(() => { if (!cancelled) setCustomCategories([]); });
    return () => { cancelled = true; };
  }, []);

  // "+ Add Category" handler — saves to backend then merges into the
  // local list so the new chip is selectable immediately.
  const addCustomCategory = useCallback(async () => {
    const nm = newCategoryName.trim();
    if (!nm) return;
    try {
      const r = await Api.addMyCategory(nm);
      setCustomCategories(r.custom || []);
      setFlexCategory(nm);
      setNewCategoryName("");
      setShowAddCategory(false);
    } catch (e: any) {
      Alert.alert(
        "Couldn't save category",
        e?.response?.data?.detail || e?.message || "Try a different name.",
      );
    }
  }, [newCategoryName]);

  // Phase 2D-update — Auto-default category to "Other" once the user
  // touches any other Flexible field but never picks a category.
  useEffect(() => {
    if (!flexibleMode) return;
    const touched = !!(flexDim || flexWeightG || flexPkgType || flexBasis || flexRate);
    if (touched && !flexCategory) {
      setFlexCategory("Other");
    }
  }, [flexibleMode, flexDim, flexWeightG, flexPkgType, flexBasis, flexRate, flexCategory]);

  // Suggested rate = closest variant by weight (within flex basis).
  const flexSuggestedRate = useMemo(() => {
    if (!flexBasis || !flexWeightG || variants.length === 0) return null;
    const sorted = [...variants]
      .filter((v) => v.weight_g)
      .sort((a, b) => Math.abs(a.weight_g - flexWeightG) - Math.abs(b.weight_g - flexWeightG));
    const closest = sorted[0];
    if (!closest) return null;
    const rate = flexBasis === "within_state"
      ? closest.within_state_rate : closest.outside_state_rate;
    return rate
      ? { rate, source: closest.variant_name, basis: flexBasis }
      : null;
  }, [flexBasis, flexWeightG, variants]);

  // Apply a flex selection back into the form fields. Called whenever
  // the user changes a flex chip — keeps the main inputs in sync so
  // they can review / override before saving.
  const syncFlexToForm = useCallback(() => {
    if (!flexibleMode) return;
    if (flexWeightG) {
      setWeight(String(flexWeightG));
      setWeightUnit("g");
    }
    if (flexDim) {
      const m = /^([\d.]+)×([\d.]+)×([\d.]+)$/.exec(flexDim);
      if (m) { setBoxL(m[1]); setBoxW(m[2]); setBoxH(m[3]); }
    }
    // Phase-39 — `setAmount(flexRate)` REMOVED.
    //
    // Flexible Mode used to drop the picked rate straight into the
    // Amount input. That conflated "courier rate" with "amount the
    // customer pays" (COD value / prepaid order total). The form's
    // Amount field is now reserved for Smart Fill / Smart Paste /
    // manual entry only — `rate_applied` is computed from
    // flexRate / variant rate at submit-time and lives on the save
    // payload independently of the visible Amount input.
    //
    // Original logic preserved as a comment for archaeology:
    //   if (flexRate) {
    //     setAmount(flexRate);
    //   }
  }, [flexibleMode, flexWeightG, flexDim]);
  useEffect(() => { syncFlexToForm(); }, [syncFlexToForm]);

  // Auto-fills the Order ID input ONLY when:
  //   - User is creating a NEW shipment (no edit_id)
  //   - Auto-Generate Order ID is ON
  //   - Auto-fill in New Shipment is ON
  //   - User hasn't already typed in the Order ID input
  //
  // Phase-37 (2026-05-18) — Auto-generation now decoupled from
  // Master ID. The previous logic gated auto-fill behind BOTH
  // `auto_generate` AND `autofill_in_new_shipment` AND
  // `master_order_id` non-empty, which meant a user whose admin
  // turned off "autofill in new shipment" would land on a blank
  // Order ID field even though Master ID was present in the system.
  // That broke shipment creation because the field is `*` required.
  //
  // New rule (matches the product spec):
  //   * If the form already has an external Order ID (loaded from
  //     Smart-Paste / pending / sheet / webhook / edit / draft) →
  //     leave it untouched.
  //   * Otherwise auto-generate using the same unique sequence the
  //     Master ID uses. Master ID and Order ID are DIFFERENT fields
  //     conceptually (Master ID = internal system ref, Order ID =
  //     end-customer reference) but when no external Order ID is
  //     provided they happen to share the same generated value
  //     because re-using the already-allocated sequence avoids
  //     two parallel counters.
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
        // Auto-fill ONLY when:
        //   1) The sequence engine is on (admin hasn't disabled
        //      Master ID generation entirely), AND
        //   2) The Order ID input is empty (no external order id
        //      came in from any source), AND
        //   3) The user hasn't started typing manually.
        //
        // NOTE: `autofill_in_new_shipment` is intentionally NOT a
        // gate here — that setting used to leave the field blank,
        // which is the bug the operator reported. The field can
        // never stay blank when no external Order ID is provided.
        if (
          r.auto_generate &&
          r.master_order_id &&
          !userTouchedOrderId
        ) {
          // Read the current orderId from state via the functional
          // setter so we don't race against a Smart-Paste /
          // pending-order loader that fired after us.
          setOrderId((prev) => (prev && prev.trim() ? prev : r.master_order_id));
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

  // Phase-23 — When the selected courier is in manual-tracking mode
  // (India Post Speed Post stickers etc.), force the form into manual
  // entry mode and never preview a next-number. The auto/manual toggle
  // is also hidden in this case (see JSX) so the user can't pick a
  // sequential number that doesn't exist for this courier.
  const courierIsManual = !!(selectedCourier as any)?.manual_tracking;
  useEffect(() => {
    if (courierIsManual && autoTracking !== false) {
      setAutoTracking(false);
    }
  }, [courierIsManual, autoTracking]);

  // Peek next tracking preview — ONLY for display/hint.
  // We never consume it until user explicitly chose "Auto" AND clicks Save.
  useEffect(() => {
    if (!selectedCourier) {
      setNextPreview("");
      return;
    }
    if (courierIsManual) {
      // Manual couriers have no sequential number to preview.
      setNextPreview("");
      return;
    }
    Api.peekNextTracking(selectedCourier.id)
      .then((r) => setNextPreview(r.tracking_id))
      .catch(() => setNextPreview(""));
  }, [selectedCourier, courierIsManual]);

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
  // Phase-35 — Plan-gated Courier Inquiry pill. Default ON for every
  // plan; admin can untick per-plan in the admin Plan Features panel.
  const flagCourierInquiry = useFeatureFlag("courier_whatsapp_inquiry");

  // Phase-24 — Centralised Field Control System. Driven by the
  // /api/field-configs/new_shipment endpoint, super-admin can toggle
  // visibility/required for non-locked fields without touching code.
  // Locked fields (customer_name, customer_phone, address, city,
  // state, pincode, order_id, amount) are still enforced inline below.
  const fcShipment = useFieldConfig("new_shipment");
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
        // Phase-33 — Terminal shipments are read-only. If a user
        // deep-links to /(tabs)/add?edit_id=<cancelled-id> we
        // refuse to populate the form (which would otherwise
        // produce an editable form whose submit would 423 anyway)
        // and bounce them back to the Shipments list with a clear
        // explanation.
        const status = String((s as any).status || "");
        const lc = status.trim().toLowerCase();
        if (["cancelled", "cancel by buyer", "returned"].includes(lc)) {
          Alert.alert(
            "Order locked",
            "This order has been cancelled or returned. It cannot be edited anymore.",
            [{ text: "OK", onPress: () => router.back() }],
          );
          return;
        }
        // Map every shipment field back into the form state.
        setOrderId(s.order_id || "");
        setCustomerName(s.customer_name || "");
        setCustomerPhone(s.customer_phone || "");
        setCustomerAltPhone((s as any).customer_alt_phone || "");
        setCustomerEmail((s as any).customer_email || "");
        setCustomerGstin((s as any).customer_gstin || "");
        // Phase-9 unified-address (2026-04-30): use FULL address verbatim.
        // Supports the legacy `address_line1` + `address_line2` shape too
        // for old shipment docs that pre-date the migration.
        setAddr1(fullAddressFrom(s));
        setAddr2("");
        setCity(s.city || "");
        setState(s.state || "");
        setPincode(s.pincode || "");
        // Phase-31 rev-2 — `amount` on the DB row is the Gross Total
        // (= cod_amount + token_amount for COD). The form field stores
        // the COD-to-Collect verbatim, so for COD shipments we MUST
        // pre-fill from `cod_amount` (not `amount`); otherwise the
        // operator would see the token rolled in twice when they save.
        // For prepaid we fall back to `amount` (which equals the
        // entered value verbatim because prepaid has no token math).
        const isCodShip =
          s.payment_mode === "COD" || (s.payment_mode as any) === "COD/Partial";
        const codField = (s as any).cod_amount;
        const srcAmt = isCodShip
          ? (codField != null && codField !== "" ? codField : s.amount)
          : s.amount;
        const amt =
          srcAmt != null && (srcAmt as any) !== ""
            ? String(srcAmt)
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
        // Phase-37 — Only overwrite Order ID when the prefill source
        // ACTUALLY provided one. Empty prefill → leave the field at
        // whatever the peek-master effect already auto-filled. This
        // is the fix for "Order ID stayed blank because no external
        // order id existed AND prefill wiped the auto-fill".
        const incomingOid = String(o.order_id || "").trim();
        if (incomingOid) setOrderId(incomingOid);
        setCustomerName(o.customer_name || "");
        setCustomerPhone(o.phone || "");
        setCustomerAltPhone(o.alt_phone || o.customer_alt_phone || "");
        setCustomerEmail(o.customer_email || o.email || "");
        setCustomerGstin(o.customer_gstin || o.gstin || o.gst || "");
        // Phase-9 unified-address (2026-04-30): use FULL address verbatim.
        // City / State / Pincode arrive as separate fields from upstream
        // (Smart Paste AI / Pending Orders / Sheet) and are NEVER parsed
        // out of the address string. This kills the recurring truncation
        // bug where "Village, Ramvav Ta. Rapar Ji.Kachchh" was getting cut to
        // just "Village" because a comma was being interpreted as a city
        // boundary.
        setAddr1(fullAddressFrom(o));
        setAddr2("");
        setCity(String(o.city || "").trim());
        setState(String(o.state || "").trim());
        setPincode(String(o.pincode || "").trim());
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
    // Phase-37 — Sheet pick: only overwrite Order ID when the sheet
    // row ACTUALLY had one. Empty value → keep the auto-generated
    // Order ID from the peek-master effect.
    const incomingOid = String(o.order_id || "").trim();
    if (incomingOid) setOrderId(incomingOid);
    setCustomerName(o.customer_name);
    setCustomerPhone(o.phone);
    // Phase-9 unified-address (2026-04-30): use FULL address verbatim,
    // never split. Sheet provides city/state/pincode as separate cols.
    setAddr1(fullAddressFrom(o));
    setAddr2("");
    setCity(String(o.city || "").trim());
    setState(String(o.state || "").trim());
    setPincode(String(o.pincode || "").trim());
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
      // Phase-8: Dynamic per-field requirements honour Settings →
      // Field Requirements. A small "isReq" helper checks the user
      // setting first, falling back to legacy hardcoded defaults.
      // Phase-27: New centralised Field Controls (`field_configs`)
      // wins when the user has saved a preference there — that
      // collection is per-tenant and plan-gated.
      const HARDCODED_REQS: Record<string, boolean> = {
        customer_name: true, customer_phone: true,
        address_line1: true, city: true, state: true,
        pincode: true, amount: true, payment_mode: true,
        weight: true, customer_alt_phone: false, items: false,
        courier_name: false, order_id: false, notes: false,
        token_amount: false,
      };
      // Legacy form keys → new field-config keys. Keys not in this
      // map continue to use the legacy isReq path (so locked
      // fields like address_line1/customer_name are unaffected).
      const FC_KEY_MAP: Record<string, string> = {
        courier_name: "courier_id",
        customer_alt_phone: "customer_alt_phone",
        items: "items",
        item_description: "item_description",
        weight: "weight",
        payment_mode: "payment_mode",
        eta_days: "eta_days",
        notes: "notes",
        sender_address_id: "sender_address_id",
      };
      const isReq = (k: string) => {
        const fcKey = FC_KEY_MAP[k];
        if (fcKey && fcShipment.cfg) {
          return fcShipment.isRequired(fcKey);
        }
        return k in fieldReqs ? !!fieldReqs[k] : !!HARDCODED_REQS[k];
      };

      const missing: string[] = [];
      if (isReq("customer_phone") && !customerPhone.trim()) missing.push("Mobile");
      if (isReq("address_line1") && !addr1.trim()) missing.push("Address");
      if (isReq("city") && !city.trim()) missing.push("City");
      if (isReq("state") && !state.trim()) missing.push("State");
      if (isReq("pincode") && !pincode.trim()) missing.push("Pincode");
      if (isReq("amount") && !(Number(amount) > 0)) missing.push("Amount");
      if (isReq("items")) {
        const _items = itemsText
          .split(/\n|,|;/)
          .map((s) => s.trim())
          .filter(Boolean);
        if (_items.length === 0) missing.push("Item(s)");
      }
      if (isReq("courier_name") && !selectedCourier) missing.push("Courier");
      if (isReq("order_id") && !orderId.trim()) missing.push("Order ID");
      if (isReq("notes") && !shipmentNotes.trim()) missing.push("Notes");
      // Phase-27 — Additional field-config gated validations
      if (isReq("customer_alt_phone") && !customerAltPhone.trim()) {
        missing.push("Alt. Phone");
      }
      // Custom Fields with required:true
      for (const ucf of userCustomFields as any[]) {
        if (ucf?.required && !String(userCustomValues[ucf.id] ?? "").trim()) {
          missing.push(ucf.name);
        }
      }
      if (missing.length > 0) {
        Alert.alert("Please fill required fields", missing.join(", "));
        return;
      }
      // Weight (when required by settings — defaults true) must be
      // non-blank. Couriers refuse parcels without weight, and rate
      // calc depends on it.
      if (isReq("weight") && !weight.trim()) {
        Alert.alert(
          "Weight required",
          "Please enter the parcel weight before saving. Couriers cannot accept a shipment without weight.",
          [{ text: "OK" }]
        );
        return;
      }
      if (!autoTracking && !trackingId.trim() && fcShipment.isRequired("tracking_id")) {
        Alert.alert(
          "Tracking ID required",
          courierIsManual
            ? "Please type the AWB from the courier's printed sticker before saving."
            : "Enter a tracking ID or switch to Auto Series.",
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
          customer_email: customerEmail.trim(),
          customer_gstin: customerGstin.trim().toUpperCase(),
          // Phase-6 single-address-field — line1 holds the entire
          // address; line2 is always blank under the new UX. Backend
          // schema kept for back-compat with old shipments.
          address_line1: addr1.trim().slice(0, 300),
          address_line2: "",
          city: city.trim(),
          state: state.trim(),
          pincode: pincode.trim(),
          payment_mode: paymentMode,
          // Phase-31 canonical math (computeOrderAmounts helper):
          //   amount       = Total Order Value (verbatim from form).
          //   cod_amount   = max(0, amount − token_amount) for COD,
          //                  0 for prepaid / non-COD modes.
          //   token_amount = advance already paid online (independent).
          // The Phase-30 trick of adding the token into `amount`
          // double-counted advances in downstream reports, so we
          // now keep `amount` as the gross total and derive `cod_amount`
          // from a single helper that every surface (form, list,
          // details, label, WhatsApp, CSV) shares.
          ...(() => {
            const a = computeOrderAmounts({
              amount,
              token: tokenAmount,
              paymentMode,
            });
            return {
              amount: a.amount,
              cod_amount: a.codAmount,
              token_amount: a.tokenAmount,
            };
          })(),
          box_dimensions: boxDimensions.trim(),
          shipment_notes: shipmentNotes.trim(),
          items,
          item_description: items.join(", "),
          weight: weight.trim() ? `${weight.trim()} ${weightUnit}` : "",
          sheet_row_key: sheetRowKey,
          // Phase 2 — Variant snapshot (captured at save time).
          // Phase 2D — When Flexible Mode is on, we save the user's
          // chip selections instead of a fixed variant id so reports
          // still get the package_type / category / rate breakdown.
          variant_id: flexibleMode ? "" : (selectedVariant?.id || ""),
          variant_name: flexibleMode
            ? `Flexible (${flexWeightG ? (flexWeightG >= 1000 ? `${flexWeightG / 1000}kg` : `${flexWeightG}g`) : "—"})`
            : (selectedVariant?.variant_name || ""),
          package_type: flexibleMode ? flexPkgType : (selectedVariant?.package_type || ""),
          category: flexibleMode ? flexCategory : (selectedVariant?.category || ""),
          rate_applied: (() => {
            if (flexibleMode) return parseFloat(flexRate) || 0;
            if (!selectedVariant) return 0;
            if (rateBasis === "within_state") return selectedVariant.within_state_rate || 0;
            return selectedVariant.outside_state_rate || 0;
          })(),
          rate_basis: flexibleMode
            ? (flexBasis || "")
            : (selectedVariant ? (rateBasis || "outside_state") : ""),
          custom_values: (() => {
            // Keep only values for fields that still exist + are enabled
            // + use per-shipment source. Trim empty strings.
            const out: Record<string, string> = {};
            for (const cf of customFields) {
              if (!cf?.enabled || cf?.source !== "shipment") continue;
              const v = (customValues[cf.id] || "").trim();
              if (v) out[cf.id] = v;
            }
            // Merge in the new per-user custom field values. Their IDs
            // are unique uuids so no key collisions with the legacy
            // label-field IDs above. Backend's _write_custom_values_to
            // _user_sheet_bg routes them by `user_custom_fields.id` →
            // `column_letter`.
            for (const ucf of userCustomFields) {
              const v = (userCustomValues[ucf.id] || "").trim();
              if (v) out[ucf.id] = v;
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
          <PhIcon name="scan" size={16} color={colors.primary} />
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
            <PhIcon name="cloud-download" size={20} color="#fff" />
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
            <PhIcon name="chevron-forward" size={18} color="#fff" />
          </TouchableOpacity>

          {/* Courier */}
          {/* Phase-27 — Courier Partner title reflects field-config */}
          <Section
            title={
              fcShipment.isRequired("courier_id")
                ? "Courier Partner *"
                : "Courier Partner"
            }
            rightSlot={
              // Phase-35 — Compact premium "Inquiry" pill button. Stays
              // disabled until a courier partner has been picked so the
              // outbound WhatsApp / SMS draft always targets exactly one
              // courier (no ambiguity). The button is *visually* slightly
              // smaller than the courier chips below so the chips remain
              // the primary action on this section.
              //
              // 2026-05-25 — Gated behind `courier_whatsapp_inquiry`
              // feature flag (defaults ON for every plan, admin can
              // untick per-plan). When OFF, the rightSlot is empty so
              // the row falls back to the plain section header.
              flagCourierInquiry ? (
              <TouchableOpacity
                testID="courier-inquiry-btn"
                style={[
                  styles.inquiryBtn,
                  !selectedCourier && styles.inquiryBtnDisabled,
                ]}
                disabled={!selectedCourier}
                onPress={() => {
                  if (!selectedCourier) return;
                  // Compose the inquiry message — Phase-35 spec.
                  // Empty fields auto-hide so the recipient never
                  // sees "Customer Name: " with nothing after.
                  const full = (addr1 || "").trim();
                  const cityStateLine = [city, state]
                    .map((x) => (x || "").trim())
                    .filter(Boolean)
                    .join(", ");
                  const addressBlock = [full, cityStateLine]
                    .filter(Boolean)
                    .join("\n");
                  const wUnit = (weightUnit || "g").toLowerCase();
                  const weightTxt = weight ? `${weight}${wUnit}` : "";
                  const itemsTxt = (itemsText || "")
                    .split(/[\n,;|]/)
                    .map((s) => s.trim())
                    .filter(Boolean)
                    .join(", ");
                  const lines: string[] = [
                    "Hello Team,",
                    "",
                    "Please confirm delivery feasibility for below shipment:",
                    "",
                  ];
                  if (customerName.trim())
                    lines.push(`Customer Name: ${customerName.trim()}`);
                  if (addressBlock) {
                    lines.push("");
                    lines.push("Full Address:");
                    lines.push(addressBlock);
                  }
                  if (pincode.trim())
                    lines.push(`Pincode: ${pincode.trim()}`);
                  if (weightTxt)
                    lines.push(`Parcel Weight: ${weightTxt}`);
                  if (itemsTxt)
                    lines.push(`Parcel Content: ${itemsTxt}`);
                  lines.push("");
                  lines.push("Please confirm:");
                  lines.push("1. Delivery possible or not?");
                  lines.push("2. Expected charges");
                  lines.push("3. Any remote/ODA issue");
                  lines.push("");
                  lines.push("Thank you.");
                  setInquiryDraft(lines.join("\n"));
                  setInquiryOpen(true);
                }}
              >
                <PhIcon
                  name="chatbubble-outline"
                  size={13}
                  color={selectedCourier ? "#EA580C" : "#D1D5DB"}
                />
                <Text
                  style={[
                    styles.inquiryBtnText,
                    !selectedCourier && { color: "#9CA3AF" },
                  ]}
                >
                  Inquiry
                </Text>
              </TouchableOpacity>
              ) : null
            }
          >
            {!selectedCourier && (
              <Text style={styles.requiredHint}>
                {couriers.length === 0
                  ? "Please pick a courier below"
                  : "Select a courier partner to enable inquiry"}
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

          {/* Phase 2 — Packing Variant Picker. Always shown so users
              discover the feature. Gracefully handles the three states:
              no courier picked yet / courier has no variants / courier
              has variants ready to pick. */}
          <Section title="📦 Packing Variant & Rate (optional)">
            {!selectedCourier ? (
              <Text style={styles.hint}>
                Pick a Courier Partner above first to see its packing
                variants (weight + rate auto-fill).
              </Text>
            ) : variants.length === 0 ? (
              <>
                <Text style={styles.hint}>
                  No variants defined for {selectedCourier.name} yet. Add
                  your first one (e.g. "ODC 320gm") so this form can
                  auto-fill weight, dimensions and rate on future orders.
                </Text>
                <TouchableOpacity
                  testID="add-first-variant-btn"
                  style={styles.outlineBtn}
                  onPress={() => router.push(`/courier/${selectedCourier.id}/variants` as any)}
                >
                  <PhIcon name="add-circle-outline" size={16} color="#7C3AED" />
                  <Text style={[styles.outlineBtnText, { color: "#7C3AED" }]}>
                    Add Packing Variants
                  </Text>
                </TouchableOpacity>
              </>
            ) : (
              <>
                <Text style={styles.hint}>
                  Tap a variant to auto-fill weight, dimensions, and rate
                  ({originState && state
                    ? rateBasis === "within_state"
                      ? `within ${originState}`
                      : `outside ${originState} → ${state || "other"}`
                    : "state detection pending — fill customer state first"})
                </Text>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{ gap: 8, paddingRight: 16, paddingVertical: 6 }}
                >
                  {variants.map((v) => {
                    const active = !flexibleMode && selectedVariant?.id === v.id;
                    const currentRate =
                      rateBasis === "within_state" ? v.within_state_rate :
                      rateBasis === "outside_state" ? v.outside_state_rate :
                      v.outside_state_rate; // default to outside when unknown
                    return (
                      <TouchableOpacity
                        key={v.id}
                        testID={`variant-card-${v.variant_name}`}
                        onPress={() => applyVariant(v)}
                        style={[
                          styles.variantCard,
                          active && styles.variantCardActive,
                        ]}
                      >
                        <Text style={[
                          styles.variantCardName,
                          active && { color: "#fff" },
                        ]} numberOfLines={1}>
                          {v.variant_name}
                        </Text>
                        <Text style={[
                          styles.variantCardMeta,
                          active && { color: "#E0E7FF" },
                        ]} numberOfLines={1}>
                          {v.weight_g ? `${v.weight_g}g` : "—"}
                          {" · "}
                          {v.length_cm ? `${v.length_cm}×${v.width_cm}×${v.height_cm}` : "—"}
                        </Text>
                        <Text style={[
                          styles.variantCardRate,
                          active && { color: "#fff" },
                        ]}>
                          ₹{currentRate || 0}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                  {/* Phase 2D — Flexible card. Lets the user mix-and-match
                      dims / weight / package / category / rate when none
                      of the fixed variants matches the order exactly. */}
                  <TouchableOpacity
                    testID="variant-card-flexible"
                    onPress={() => {
                      const next = !flexibleMode;
                      setFlexibleMode(next);
                      if (next) setSelectedVariant(null);
                      // Auto-pick basis from origin/destination if known.
                      if (next && originState && state && !flexBasis) {
                        setFlexBasis(
                          originState.trim().toLowerCase() === state.trim().toLowerCase()
                            ? "within_state" : "outside_state",
                        );
                      }
                    }}
                    style={[
                      styles.variantCard,
                      styles.flexCard,
                      flexibleMode && styles.flexCardActive,
                    ]}
                  >
                    <Text style={[
                      styles.variantCardName,
                      flexibleMode && { color: "#fff" },
                    ]} numberOfLines={1}>
                      ✨ Flexible
                    </Text>
                    <Text style={[
                      styles.variantCardMeta,
                      flexibleMode && { color: "#FCE7F3" },
                    ]} numberOfLines={1}>
                      Custom mix
                    </Text>
                    <Text style={[
                      styles.variantCardRate,
                      flexibleMode && { color: "#fff" },
                    ]}>
                      ₹—
                    </Text>
                  </TouchableOpacity>
                </ScrollView>
                {selectedVariant && !flexibleMode && (
                  <TouchableOpacity
                    onPress={() => setSelectedVariant(null)}
                    style={styles.clearVariantBtn}
                  >
                    <PhIcon name="close-circle" size={14} color="#6B7280" />
                    <Text style={styles.clearVariantTxt}>Clear variant</Text>
                  </TouchableOpacity>
                )}

                {/* Phase 2D — Flexible UI block. Chip-driven editor that
                    populates dim / weight / package / category / rate
                    from the courier's existing variants + fixed weight
                    ladder. User confirms or overrides each value. */}
                {flexibleMode && (
                  <View style={styles.flexBlock}>
                    {/* Dimensions */}
                    <Text style={styles.flexLabel}>📏 Box Dimensions (cm)</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                      <View style={{ flexDirection: "row", gap: 6 }}>
                        {flexDimChips.map((d) => {
                          const active = flexDim === d.label;
                          return (
                            <TouchableOpacity
                              key={d.label}
                              onPress={() => {
                                setFlexDim(active ? "" : d.label);
                                setShowDimCustom(false);
                              }}
                              style={[styles.flexChip, active && styles.flexChipActive]}
                            >
                              <Text style={[
                                styles.flexChipTxt,
                                active && styles.flexChipTxtActive,
                              ]}>
                                {d.label}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                        {/* + Custom dim chip */}
                        <TouchableOpacity
                          testID="flex-dim-custom"
                          onPress={() => setShowDimCustom((s) => !s)}
                          style={[
                            styles.flexChip, styles.flexChipDashed,
                            showDimCustom && styles.flexChipActive,
                          ]}
                        >
                          <Text style={[
                            styles.flexChipTxt,
                            showDimCustom && styles.flexChipTxtActive,
                          ]}>
                            + Custom
                          </Text>
                        </TouchableOpacity>
                      </View>
                    </ScrollView>
                    {showDimCustom && (
                      <View style={styles.flexCustomRow}>
                        <TextInput
                          value={customDimL} onChangeText={setCustomDimL}
                          keyboardType="decimal-pad" placeholder="L"
                          placeholderTextColor="#9CA3AF" style={styles.flexCustomInput}
                        />
                        <Text style={styles.flexCustomX}>×</Text>
                        <TextInput
                          value={customDimW} onChangeText={setCustomDimW}
                          keyboardType="decimal-pad" placeholder="W"
                          placeholderTextColor="#9CA3AF" style={styles.flexCustomInput}
                        />
                        <Text style={styles.flexCustomX}>×</Text>
                        <TextInput
                          value={customDimH} onChangeText={setCustomDimH}
                          keyboardType="decimal-pad" placeholder="H"
                          placeholderTextColor="#9CA3AF" style={styles.flexCustomInput}
                        />
                        <TouchableOpacity
                          style={styles.flexCustomApply}
                          onPress={() => {
                            const l = parseFloat(customDimL) || 0;
                            const w = parseFloat(customDimW) || 0;
                            const h = parseFloat(customDimH) || 0;
                            if (!l && !w && !h) {
                              Alert.alert("Enter at least one dimension");
                              return;
                            }
                            setFlexDim(`${l}×${w}×${h}`);
                            setShowDimCustom(false);
                          }}
                        >
                          <Text style={styles.flexCustomApplyTxt}>Apply</Text>
                        </TouchableOpacity>
                      </View>
                    )}

                    {/* Weight */}
                    <Text style={[styles.flexLabel, { marginTop: 12 }]}>⚖️ Weight</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                      <View style={{ flexDirection: "row", gap: 6 }}>
                        {flexWeightChips.map((g) => {
                          const active = flexWeightG === g;
                          const lbl = g >= 1000 ? `${g / 1000}kg` : `${g}g`;
                          return (
                            <TouchableOpacity
                              key={`w-${g}`}
                              onPress={() => {
                                setFlexWeightG(active ? 0 : g);
                                setShowWeightCustom(false);
                              }}
                              style={[styles.flexChip, active && styles.flexChipActive]}
                            >
                              <Text style={[
                                styles.flexChipTxt,
                                active && styles.flexChipTxtActive,
                              ]}>
                                {lbl}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                        {/* + Custom weight chip */}
                        <TouchableOpacity
                          testID="flex-weight-custom"
                          onPress={() => setShowWeightCustom((s) => !s)}
                          style={[
                            styles.flexChip, styles.flexChipDashed,
                            showWeightCustom && styles.flexChipActive,
                          ]}
                        >
                          <Text style={[
                            styles.flexChipTxt,
                            showWeightCustom && styles.flexChipTxtActive,
                          ]}>
                            + Custom
                          </Text>
                        </TouchableOpacity>
                      </View>
                    </ScrollView>
                    {showWeightCustom && (
                      <View style={styles.flexCustomRow}>
                        <TextInput
                          value={customWeightG} onChangeText={setCustomWeightG}
                          keyboardType="decimal-pad" placeholder="Weight (grams)"
                          placeholderTextColor="#9CA3AF"
                          style={[styles.flexCustomInput, { flex: 1 }]}
                        />
                        <TouchableOpacity
                          style={styles.flexCustomApply}
                          onPress={() => {
                            const g = parseFloat(customWeightG) || 0;
                            if (!g) {
                              Alert.alert("Enter a weight in grams");
                              return;
                            }
                            setFlexWeightG(g);
                            setShowWeightCustom(false);
                          }}
                        >
                          <Text style={styles.flexCustomApplyTxt}>Apply</Text>
                        </TouchableOpacity>
                      </View>
                    )}

                    {/* Package Type */}
                    <Text style={[styles.flexLabel, { marginTop: 12 }]}>📦 Package Type</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                      <View style={{ flexDirection: "row", gap: 6 }}>
                        {flexPkgChips.map((p) => {
                          const active = flexPkgType === p;
                          return (
                            <TouchableOpacity
                              key={`p-${p}`}
                              onPress={() => setFlexPkgType(active ? "" : p)}
                              style={[styles.flexChip, active && styles.flexChipActive]}
                            >
                              <Text style={[
                                styles.flexChipTxt,
                                active && styles.flexChipTxtActive,
                              ]}>
                                {p}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                      </View>
                    </ScrollView>

                    {/* Category */}
                    <Text style={[styles.flexLabel, { marginTop: 12 }]}>🏷️ Category</Text>
                    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                      <View style={{ flexDirection: "row", gap: 6 }}>
                        {flexCatChips.map((c) => {
                          const active = flexCategory === c;
                          return (
                            <TouchableOpacity
                              key={`c-${c}`}
                              onPress={() => setFlexCategory(active ? "" : c)}
                              style={[styles.flexChip, active && styles.flexChipActive]}
                            >
                              <Text style={[
                                styles.flexChipTxt,
                                active && styles.flexChipTxtActive,
                              ]}>
                                {c}
                              </Text>
                            </TouchableOpacity>
                          );
                        })}
                        {/* + Add Category chip — only enabled when current
                            selection is "Other" or empty (matches request:
                            "Other" chip પર click → "+ Add Category"). */}
                        <TouchableOpacity
                          testID="flex-add-category"
                          onPress={() => setShowAddCategory((s) => !s)}
                          style={[
                            styles.flexChip, styles.flexChipDashed,
                            showAddCategory && styles.flexChipActive,
                          ]}
                        >
                          <Text style={[
                            styles.flexChipTxt,
                            showAddCategory && styles.flexChipTxtActive,
                          ]}>
                            + Add Category
                          </Text>
                        </TouchableOpacity>
                      </View>
                    </ScrollView>
                    {showAddCategory && (
                      <View style={styles.flexCustomRow}>
                        <TextInput
                          value={newCategoryName}
                          onChangeText={setNewCategoryName}
                          placeholder='New category name (e.g. "Toys")'
                          placeholderTextColor="#9CA3AF"
                          style={[styles.flexCustomInput, { flex: 1 }]}
                          autoCapitalize="words"
                          maxLength={40}
                        />
                        <TouchableOpacity
                          style={styles.flexCustomApply}
                          onPress={addCustomCategory}
                        >
                          <Text style={styles.flexCustomApplyTxt}>Save</Text>
                        </TouchableOpacity>
                      </View>
                    )}

                    {/* Delivery basis radio */}
                    <Text style={[styles.flexLabel, { marginTop: 12 }]}>🚚 Delivery</Text>
                    <View style={{ flexDirection: "row", gap: 8 }}>
                      <TouchableOpacity
                        onPress={() => setFlexBasis(flexBasis === "within_state" ? "" : "within_state")}
                        style={[styles.flexRadio, flexBasis === "within_state" && styles.flexRadioActive]}
                      >
                        <PhIcon
                          name={flexBasis === "within_state" ? "radio-button-on" : "radio-button-off"}
                          size={16}
                          color={flexBasis === "within_state" ? "#7C3AED" : "#9CA3AF"}
                        />
                        <Text style={styles.flexRadioTxt}>Within State</Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        onPress={() => setFlexBasis(flexBasis === "outside_state" ? "" : "outside_state")}
                        style={[styles.flexRadio, flexBasis === "outside_state" && styles.flexRadioActive]}
                      >
                        <PhIcon
                          name={flexBasis === "outside_state" ? "radio-button-on" : "radio-button-off"}
                          size={16}
                          color={flexBasis === "outside_state" ? "#7C3AED" : "#9CA3AF"}
                        />
                        <Text style={styles.flexRadioTxt}>Outside State</Text>
                      </TouchableOpacity>
                    </View>

                    {/* Suggestion + manual rate */}
                    {flexSuggestedRate && (
                      <View style={styles.flexSuggestion}>
                        <PhIcon name="bulb" size={14} color="#B45309" />
                        <Text style={styles.flexSuggestionTxt}>
                          Suggestion: ₹{flexSuggestedRate.rate}{" "}
                          (from "{flexSuggestedRate.source}",{" "}
                          {flexSuggestedRate.basis === "within_state" ? "within-state" : "outside-state"})
                        </Text>
                        <TouchableOpacity
                          onPress={() => setFlexRate(String(flexSuggestedRate.rate))}
                          style={styles.flexSuggestionBtn}
                        >
                          <Text style={styles.flexSuggestionBtnTxt}>Use</Text>
                        </TouchableOpacity>
                      </View>
                    )}

                    <Text style={[styles.flexLabel, { marginTop: 10 }]}>💰 Rate (₹) — confirm or change</Text>
                    <TextInput
                      value={flexRate}
                      onChangeText={setFlexRate}
                      keyboardType="decimal-pad"
                      placeholder="Enter rate"
                      placeholderTextColor="#9CA3AF"
                      style={styles.flexRateInput}
                    />
                  </View>
                )}

                <TouchableOpacity
                  testID="manage-variants-link"
                  style={styles.manageVariantsLink}
                  onPress={() => router.push(`/courier/${selectedCourier.id}/variants` as any)}
                >
                  <PhIcon name="settings-outline" size={12} color="#6B7280" />
                  <Text style={styles.manageVariantsLinkTxt}>
                    Manage variants for {selectedCourier.name}
                  </Text>
                </TouchableOpacity>
              </>
            )}
          </Section>

          {/* Tracking — Phase-24: required asterisk reflects field-config */}
          <Section
            title={
              fcShipment.isRequired("tracking_id")
                ? "Tracking ID *"
                : "Tracking ID (optional)"
            }
          >
            {/* Phase-23 — Manual-tracking couriers (India Post Speed
                Post, Anjani physical labels) skip the Auto/Manual
                choice entirely: a sequential AWB doesn't exist, the
                user types from the printed sticker. We show a small
                yellow info card instead of the toggle. */}
            {courierIsManual ? (
              <View style={styles.manualInfoCard}>
                <PhIcon name="information-circle" size={16} color="#92400E" />
                <Text style={styles.manualInfoTxt}>
                  This courier uses manual tracking. Please type the AWB from the printed sticker below.
                </Text>
              </View>
            ) : (
              <>
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
                <PhIcon
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
                <PhIcon
                  name="create-outline"
                  size={14}
                  color={autoTracking === false ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, autoTracking === false && { color: "#fff" }]}>
                  Manual / Scan
                </Text>
              </TouchableOpacity>
            </View>
              </>
            )}
            <View style={{ position: "relative" }}>
              <TextInput
                testID="tracking-id-input"
                value={trackingId}
                editable={courierIsManual ? true : autoTracking === false}
                onChangeText={setTrackingId}
                placeholder={
                  courierIsManual
                    ? "Type AWB from courier sticker"
                    : autoTracking === null
                    ? "Pick a mode above first"
                    : autoTracking
                    ? nextPreview
                    : "Enter tracking ID"
                }
                placeholderTextColor="#9CA3AF"
                style={[
                  styles.input,
                  styles.trackingInput,
                  (courierIsManual || autoTracking === false) && { paddingRight: 48 },
                  !courierIsManual && autoTracking === null && { opacity: 0.6 },
                ]}
                autoCapitalize="characters"
              />
              {(courierIsManual || autoTracking === false) && (
                <TouchableOpacity
                  testID="tracking-inline-scan"
                  onPress={() => router.push("/scanner?returnTo=add&from=add")}
                  style={styles.inlineScanBtn}
                  hitSlop={8}
                >
                  <PhIcon name="camera" size={20} color={colors.primary} />
                </TouchableOpacity>
              )}
            </View>
            {!courierIsManual && autoTracking === true && nextPreview ? (
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
                  orderIdAutoGen
                    ? "Auto-generated (editable)"
                    : "Order ID / Invoice #"
                }
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            {orderIdAutoGen && previewMasterId ? (
              // Phase-37 — Master ID is now displayed as a pure
              // INTERNAL system reference, not a sibling of Order
              // ID. The hint copy below makes the distinction
              // explicit so operators don't think Order ID stays
              // blank "because Master ID exists" (which was the
              // original bug — see useEffect above).
              <Text style={styles.hint}>
                Master ID (system, internal only): {previewMasterId}
                {orderId && orderId !== previewMasterId
                  ? "  ·  Order ID kept separately"
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
            {/* Phase-3 Smart Paste enhancement: optional B2B fields. */}
            <Field label="Email (optional)">
              <TextInput
                testID="customer-email-input"
                value={customerEmail}
                onChangeText={setCustomerEmail}
                placeholder="customer@example.com"
                placeholderTextColor="#9CA3AF"
                keyboardType="email-address"
                autoCapitalize="none"
                autoCorrect={false}
                style={styles.input}
              />
            </Field>
            <Field label="GSTIN (optional, B2B)">
              <TextInput
                testID="customer-gstin-input"
                value={customerGstin}
                onChangeText={(t) => setCustomerGstin(t.toUpperCase())}
                placeholder="15-character GST number"
                placeholderTextColor="#9CA3AF"
                autoCapitalize="characters"
                autoCorrect={false}
                maxLength={15}
                style={styles.input}
              />
            </Field>
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
                <PhIcon
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
                <PhIcon
                  name="cash"
                  size={14}
                  color={paymentMode === "COD" ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, paymentMode === "COD" && { color: "#fff" }]}>
                  COD
                </Text>
              </TouchableOpacity>
            </View>
            <Field label="COD to Collect (₹)">
              <TextInput
                testID="amount-input"
                value={amount}
                onChangeText={setAmount}
                placeholder={paymentMode === "COD" ? "What courier collects" : "Order value"}
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
                    // Phase-31 — the "Order Amount" field is now the
                    // Total Order Value (not the post-advance COD).
                    // The preview chip derives what the courier will
                    // actually collect via the canonical helper:
                    //   COD to collect = max(0, amount − token).
                    // Mirrors the math in shipments_write.py and
                    // computeOrderAmounts() so the preview, the
                    // payload, and the printed label all agree.
                    const a = computeOrderAmounts({
                      amount,
                      token: tokenAmount,
                      paymentMode,
                    });
                    if (paymentMode === "Prepaid") {
                      return (
                        <View style={[styles.input, { justifyContent: "center", backgroundColor: "#ECFDF5", borderColor: "#A7F3D0" }]}>
                          <Text style={{ color: "#047857", fontWeight: "800" }}>
                            Already paid ✓
                          </Text>
                        </View>
                      );
                    }
                    return (
                      <View style={[styles.input, { justifyContent: "center", backgroundColor: "#F9FAFB" }]}>
                        <Text style={{ color: a.tokenAmount > 0 ? colors.primary : "#9CA3AF", fontWeight: "700" }}>
                          COD to collect: ₹{a.codAmount.toFixed(0)}
                        </Text>
                      </View>
                    );
                  })()}
                </View>
              </View>

              {/* Inline error: Prepaid + token > 0 is invalid */}
              {paymentMode === "Prepaid" && Number(tokenAmount) > 0 ? (
                <View style={styles.tokenErrorBox} testID="token-prepaid-error">
                  <PhIcon name="alert-circle" size={16} color="#B91C1C" />
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

          {/* ---------- User Custom Fields (plan-gated, defined in Manage Custom Fields) ---------- */}
          {userCustomFields.length > 0 && (
            <Section title="My Custom Fields" icon="layers">
              <Text style={styles.hint}>
                Per-shipment values for your custom Google Sheet columns.
              </Text>
              {userCustomFields.map((cf) => (
                <Field key={`ucf-${cf.id}`} label={`${cf.name} (col ${cf.column_letter})`}>
                  <TextInput
                    testID={`user-cf-${cf.id}`}
                    value={userCustomValues[cf.id] || ""}
                    onChangeText={(t) =>
                      setUserCustomValues({ ...userCustomValues, [cf.id]: t })
                    }
                    placeholder={
                      cf.field_type === "number"
                        ? "0"
                        : cf.field_type === "date"
                        ? "YYYY-MM-DD"
                        : `Enter ${cf.name.toLowerCase()}`
                    }
                    placeholderTextColor="#9CA3AF"
                    keyboardType={
                      cf.field_type === "number" ? "decimal-pad" : "default"
                    }
                    style={styles.input}
                  />
                </Field>
              ))}
            </Section>
          )}

          <View style={styles.ctaRow}>
            <TouchableOpacity
              testID="cancel-shipment-btn"
              style={styles.cancelBtn}
              disabled={saving}
              onPress={onCancel}
              hitSlop={8}
            >
              <PhIcon name="close" size={18} color="#B91C1C" />
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
                  <PhIcon
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
              <PhIcon name="print" size={18} color="#fff" />
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
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Import from Sheet</Text>
            <TouchableOpacity
              testID="import-refresh"
              onPress={openImport}
              style={styles.modalClose}
            >
              <PhIcon name="refresh" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          <View style={styles.modalSearchWrap}>
            <PhIcon name="search" size={16} color={colors.textMuted} />
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
                  <PhIcon name="chevron-forward" size={20} color={colors.textMuted} />
                </TouchableOpacity>
              )}
            />
          )}
        </SafeAreaView>
      </Modal>

      {/* Phase-35 — Courier Inquiry preview modal.

          Opens when the operator taps the small "Inquiry" pill on the
          Courier Partner section. The textarea is fully editable so
          the operator can tweak the auto-composed message before it
          leaves their device. "Open WhatsApp" hits wa.me with the
          selected courier's contact_phone; if WhatsApp isn't
          installed we fall back to the system SMS composer at the
          same number. Both routes use deep-links — no backend call,
          no network requirement, no PII leaving the device until
          the operator hits Send in WhatsApp/SMS themselves. */}
      <Modal
        visible={inquiryOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setInquiryOpen(false)}
      >
        <View style={styles.inquiryBackdrop}>
          <View style={styles.inquiryCard}>
            <View style={styles.inquiryHead}>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <View style={styles.inquiryIconCircle}>
                  <PhIcon name="chatbubble-outline" size={16} color="#EA580C" />
                </View>
                <View>
                  <Text style={styles.inquiryTitle}>Courier Inquiry</Text>
                  <Text style={styles.inquirySub} numberOfLines={1}>
                    To: {selectedCourier?.name || ""}
                    {(selectedCourier as any)?.contact_phone
                      ? ` · ${(selectedCourier as any).contact_phone}`
                      : ""}
                  </Text>
                </View>
              </View>
              <TouchableOpacity
                onPress={() => setInquiryOpen(false)}
                hitSlop={10}
              >
                <PhIcon name="close" size={20} color="#374151" />
              </TouchableOpacity>
            </View>

            {!(selectedCourier as any)?.contact_phone && (
              <View style={styles.inquiryWarn}>
                <PhIcon name="alert-circle-outline" size={14} color="#92400E" />
                <Text style={styles.inquiryWarnText}>
                  This courier has no contact number saved. Add one in
                  Settings → Couriers to enable inquiry.
                </Text>
              </View>
            )}

            <Text style={styles.inquiryLabel}>Message (editable)</Text>
            <TextInput
              style={styles.inquiryTextarea}
              value={inquiryDraft}
              onChangeText={setInquiryDraft}
              multiline
              textAlignVertical="top"
            />

            <View style={styles.inquiryActions}>
              <TouchableOpacity
                style={[styles.inquiryActionBtn, styles.inquirySmsBtn]}
                onPress={async () => {
                  const phone = String(
                    (selectedCourier as any)?.contact_phone || "",
                  ).replace(/\D/g, "");
                  if (!phone) {
                    Alert.alert(
                      "No contact number",
                      "This courier has no phone saved. Add one in Settings → Couriers.",
                    );
                    return;
                  }
                  const sep = Platform.OS === "ios" ? "&" : "?";
                  const url = `sms:${phone}${sep}body=${encodeURIComponent(inquiryDraft)}`;
                  try {
                    await Linking.openURL(url);
                    setInquiryOpen(false);
                  } catch {
                    Alert.alert("SMS app unavailable", "Couldn't open the SMS composer.");
                  }
                }}
              >
                <PhIcon name="chatbubble-outline" size={14} color="#374151" />
                <Text style={styles.inquirySmsText}>SMS</Text>
              </TouchableOpacity>

              <TouchableOpacity
                style={[styles.inquiryActionBtn, styles.inquiryWaBtn]}
                onPress={async () => {
                  const phoneRaw = String(
                    (selectedCourier as any)?.contact_phone || "",
                  ).replace(/\D/g, "");
                  if (!phoneRaw) {
                    Alert.alert(
                      "No contact number",
                      "This courier has no phone saved. Add one in Settings → Couriers.",
                    );
                    return;
                  }
                  // Normalise to international format (default IN +91 if
                  // operator saved a bare 10-digit number).
                  const phone =
                    phoneRaw.length === 10 ? `91${phoneRaw}` : phoneRaw;
                  const waUrl = `whatsapp://send?phone=${phone}&text=${encodeURIComponent(inquiryDraft)}`;
                  const httpsUrl = `https://wa.me/${phone}?text=${encodeURIComponent(inquiryDraft)}`;
                  // Try the native WhatsApp scheme first, fall back to
                  // the universal wa.me link, then finally fall back
                  // to SMS so the inquiry NEVER silently fails.
                  try {
                    const can = await Linking.canOpenURL(waUrl);
                    await Linking.openURL(can ? waUrl : httpsUrl);
                    setInquiryOpen(false);
                  } catch {
                    try {
                      await Linking.openURL(httpsUrl);
                      setInquiryOpen(false);
                    } catch {
                      const sep = Platform.OS === "ios" ? "&" : "?";
                      await Linking.openURL(
                        `sms:${phoneRaw}${sep}body=${encodeURIComponent(inquiryDraft)}`,
                      );
                      setInquiryOpen(false);
                    }
                  }
                }}
              >
                <PhIcon name="logo-whatsapp" size={14} color="#fff" />
                <Text style={styles.inquiryWaText}>Open WhatsApp</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function Section({
  title,
  children,
  rightSlot,
}: {
  title: string;
  children: React.ReactNode;
  /** Optional content rendered on the SAME row as the section title,
   *  right-aligned. Used by Phase-35 to host the "Inquiry" CTA on the
   *  Courier Partner section without breaking other sections. */
  rightSlot?: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeaderRow}>
        {/* When sitting inside a row, drop the title's own marginBottom
            so the spacing math stays exactly the same as before (the
            wrapper row carries it instead). */}
        <Text style={[styles.sectionTitle, { marginBottom: 0, flex: 1 }]}>
          {title}
        </Text>
        {rightSlot}
      </View>
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
  // Phase-35 — section-header row + right slot for the new Inquiry CTA.
  // Keeps the title and the action button on the SAME baseline so the
  // section header doesn't double its height on phones that wrap.
  sectionHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    marginBottom: 10,
  },
  // Compact premium "Inquiry" pill — white bg, thin orange border,
  // small message icon + orange text. Slightly smaller font than the
  // courier chips below so the chips remain the visual primary.
  inquiryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingVertical: 6,
    paddingHorizontal: 12,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#FB923C",
    backgroundColor: "#fff",
  },
  inquiryBtnDisabled: {
    borderColor: "#E5E7EB",
    backgroundColor: "#F9FAFB",
  },
  inquiryBtnText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#EA580C",
  },
  // -------- Inquiry preview modal --------
  inquiryBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.55)",
    justifyContent: "flex-end",
  },
  inquiryCard: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 22,
    borderTopRightRadius: 22,
    padding: 18,
    paddingBottom: 28,
    gap: 12,
  },
  inquiryHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  inquiryIconCircle: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: "#FFF7ED",
    alignItems: "center",
    justifyContent: "center",
  },
  inquiryTitle: { fontSize: 16, fontWeight: "800", color: "#0F172A" },
  inquirySub: { fontSize: 11, color: "#6B7280", maxWidth: 240 },
  inquiryLabel: { fontSize: 11, color: "#6B7280", fontWeight: "700" },
  inquiryTextarea: {
    minHeight: 200,
    maxHeight: 320,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 12,
    fontSize: 13,
    lineHeight: 19,
    color: "#111827",
    backgroundColor: "#F8F9FB",
  },
  inquiryWarn: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "#FFFBEB",
    borderWidth: 1,
    borderColor: "#FCD34D",
    borderRadius: 10,
    padding: 10,
  },
  inquiryWarnText: {
    flex: 1,
    fontSize: 11,
    color: "#78350F",
    lineHeight: 16,
  },
  inquiryActions: { flexDirection: "row", gap: 10 },
  inquiryActionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 12,
    borderRadius: 12,
  },
  inquirySmsBtn: {
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  inquirySmsText: { fontSize: 13, fontWeight: "700", color: "#374151" },
  inquiryWaBtn: { backgroundColor: "#25D366" },
  inquiryWaText: { fontSize: 13, fontWeight: "800", color: "#fff" },
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
  // Phase-23 — Manual-tracking info card (replaces Auto/Manual toggle
  // when the selected courier requires manual AWB entry).
  manualInfoCard: {
    flexDirection: "row", alignItems: "flex-start", gap: 8,
    backgroundColor: "#FEF3C7", borderRadius: 10,
    borderWidth: 1, borderColor: "#FDE68A",
    paddingVertical: 10, paddingHorizontal: 12, marginBottom: 10,
  },
  manualInfoTxt: {
    flex: 1,
    fontSize: 12.5, color: "#92400E", lineHeight: 18, fontWeight: "600",
  },
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

  // Phase 2 — Packing Variant picker
  variantCard: {
    minWidth: 130,
    paddingVertical: 10, paddingHorizontal: 12,
    borderRadius: 10,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  variantCardActive: {
    backgroundColor: "#7C3AED", borderColor: "#7C3AED",
  },
  variantCardName: { fontSize: 13, fontWeight: "800", color: colors.text },
  variantCardMeta: { fontSize: 10.5, color: "#6B7280", marginTop: 3 },
  variantCardRate: { fontSize: 13, fontWeight: "800", color: "#1F4FBF", marginTop: 5 },

  // Phase 2D — Flexible card + UI block
  flexCard: {
    backgroundColor: "#FDF4FF", borderColor: "#E9D5FF", borderStyle: "dashed",
  },
  flexCardActive: {
    backgroundColor: "#A855F7", borderColor: "#A855F7", borderStyle: "solid",
  },
  flexBlock: {
    marginTop: 10, padding: 12, borderRadius: 10,
    backgroundColor: "#FAF5FF",
    borderWidth: 1, borderColor: "#E9D5FF",
  },
  flexLabel: { fontSize: 12, fontWeight: "800", color: "#6B21A8", marginBottom: 6 },
  flexEmpty: { fontSize: 11, color: "#9CA3AF", fontStyle: "italic" },
  flexChip: {
    paddingHorizontal: 11, paddingVertical: 7, borderRadius: 20,
    flexShrink: 0,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#D8B4FE",
  },
  flexChipActive: { backgroundColor: "#7C3AED", borderColor: "#7C3AED" },
  flexChipDashed: { borderStyle: "dashed", backgroundColor: "#FAF5FF" },
  flexChipTxt: { fontSize: 11.5, fontWeight: "700", color: "#6B21A8" },
  flexChipTxtActive: { color: "#fff" },
  flexCustomRow: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 8, paddingHorizontal: 4,
  },
  flexCustomInput: {
    width: 60, paddingHorizontal: 10, paddingVertical: 8,
    borderWidth: 1, borderColor: "#D8B4FE", borderRadius: 8,
    fontSize: 13, color: colors.text, backgroundColor: "#fff",
    textAlign: "center",
  },
  flexCustomX: { fontSize: 14, fontWeight: "800", color: "#9CA3AF" },
  flexCustomApply: {
    paddingHorizontal: 14, paddingVertical: 9, borderRadius: 8,
    backgroundColor: "#7C3AED",
  },
  flexCustomApplyTxt: { fontSize: 12, fontWeight: "800", color: "#fff" },
  flexRadio: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 12, paddingVertical: 9, borderRadius: 8,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    flex: 1,
  },
  flexRadioActive: { borderColor: "#7C3AED", backgroundColor: "#F5F3FF" },
  flexRadioTxt: { fontSize: 12, fontWeight: "700", color: "#374151" },
  flexSuggestion: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 10, padding: 8, borderRadius: 8,
    backgroundColor: "#FFFBEB", borderWidth: 1, borderColor: "#FDE68A",
  },
  flexSuggestionTxt: { flex: 1, fontSize: 11, fontWeight: "700", color: "#92400E" },
  flexSuggestionBtn: {
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 6,
    backgroundColor: "#B45309",
  },
  flexSuggestionBtnTxt: { fontSize: 11, fontWeight: "800", color: "#fff" },
  flexRateInput: {
    borderWidth: 1, borderColor: "#D8B4FE", borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 14, fontWeight: "700", color: "#1F4FBF",
    backgroundColor: "#fff",
  },
  clearVariantBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    alignSelf: "flex-start",
    paddingVertical: 4, paddingHorizontal: 8, marginTop: 4,
  },
  clearVariantTxt: { fontSize: 11, color: "#6B7280" },
  manageVariantsLink: {
    flexDirection: "row", alignItems: "center", gap: 4,
    alignSelf: "flex-start",
    paddingVertical: 4, paddingHorizontal: 4, marginTop: 4,
  },
  manageVariantsLinkTxt: { fontSize: 10.5, color: "#6B7280", textDecorationLine: "underline" },
  outlineBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 10, borderRadius: 10,
    backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
    marginTop: 8,
  },
  outlineBtnText: { fontSize: 13, fontWeight: "700", color: colors.text },

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
