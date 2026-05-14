import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  TouchableOpacity,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Switch,
  ActivityIndicator,
  Modal,
  Linking,
  Image,
  BackHandler,
} from "react-native";
import * as ImagePicker from "expo-image-picker";
import * as Clipboard from "expo-clipboard";
import { SafeAreaView } from "react-native-safe-area-context";
import PendingSyncPanel from "../../components/PendingSyncPanel";
import { useRouter, useFocusEffect, useLocalSearchParams, useNavigation } from "expo-router";
import { Api, Courier, Settings as SettingsT, SenderAddress, SheetPreview, SHEET_FIELDS, api, PlanKey } from "../../lib/api";
import { useFeatureFlag } from "../../lib/feature_flags";
import { usePermissions } from "../../lib/permissions";
import { colors } from "../../lib/theme";
import { useAuth } from "../../lib/auth";
import Constants from "expo-constants";

// App-level metadata pulled from app.json `extra` block. Used by the
// About / Help & Support / Legal sections so the published Play Store
// + App Store builds always show the correct support email + policy
// URLs without code edits.
const APP_EXTRA = (Constants.expoConfig?.extra || {}) as {
  storeListingName?: string;
  privacyPolicyUrl?: string;
  termsUrl?: string;
  supportEmail?: string;
  supportPhone?: string;
};
const APP_NAME    = Constants.expoConfig?.name || "Shippzo";
const APP_VERSION = Constants.expoConfig?.version || "1.0.0";
const SUPPORT_EMAIL = APP_EXTRA.supportEmail || "shippzo.support@gmail.com";
const PRIVACY_URL   = APP_EXTRA.privacyPolicyUrl || "https://shippzo.com/privacy";
const TERMS_URL     = APP_EXTRA.termsUrl || "https://shippzo.com/terms";

/**
 * CONTENT BUDGET SYSTEM
 *
 * Prevents label overflow/overlap by capping how many "space-hungry"
 * elements can be enabled simultaneously. Each of these counts as 1 point:
 *   • Brand Tagline (non-empty text)
 *   • "Shipment Notes" toggle ON
 *   • Each ENABLED custom label field
 * Max budget = 3. If user tries to push it above 3, we block and alert.
 */
const CONTENT_BUDGET_CAP = 3;

function computeBudgetUsed(params: {
  tagline: string;
  shipmentNotesOn: boolean;
  customFields: Array<{ enabled: boolean }>;
}): number {
  let n = 0;
  if ((params.tagline || "").trim().length > 0) n += 1;
  if (params.shipmentNotesOn) n += 1;
  n += params.customFields.filter((c) => c.enabled).length;
  return n;
}

// Professional templates with blank lines for breathing room
const PRESETS = {
  wa_gujarati: (
    "Hello {customer_name} 🙏\n" +
    "\n" +
    "Your order #{order_id} has been shipped successfully.\n" +
    "\n" +
    "📦 Courier: {courier}\n" +
    "🔖 Tracking ID: {tracking_id}\n" +
    "\n" +
    "🔗 Track your order:\n" +
    "{tracking_url}\n" +
    "\n" +
    "⏱ Estimated delivery: {eta_days} days\n" +
    "\n" +
    "Thank you!"
  ),
  wa_english: (
    "Hi {customer_name} 👋\n" +
    "\n" +
    "Your order #{order_id} has been shipped.\n" +
    "\n" +
    "📦 Courier: {courier}\n" +
    "🔖 Tracking ID: {tracking_id}\n" +
    "\n" +
    "🔗 Track here:\n" +
    "{tracking_url}\n" +
    "\n" +
    "⏱ Expected delivery: {eta_days} days\n" +
    "\n" +
    "Thank you for your order!"
  ),
  copy_pro: (
    "Hi {customer_name},\n" +
    "\n" +
    "Your order #{order_id} has been shipped.\n" +
    "\n" +
    "Courier: {courier}\n" +
    "Tracking ID: {tracking_id}\n" +
    "Amount: ₹{amount}\n" +
    "\n" +
    "Track your order:\n" +
    "{tracking_url}\n" +
    "\n" +
    "Thank you!"
  ),
};

// Sample data for live preview
const SAMPLE = {
  customer_name: "Ramesh Patel",
  order_id: "ORD-1001",
  courier: "Nandan Courier",
  tracking_id: "ND00123",
  tracking_url: "https://nandancourier.com/track?id=ND00123",
  amount: "850",
  eta_days: "7",
};

// Plan tile theme + display labels (mirrors /plans screen)
const PLAN_THEME: Record<PlanKey, { bg: string; border: string; accent: string; chipBg: string; chipTxt: string }> = {
  free_trial: { bg: "#FAF5FF", border: "#DDD6FE", accent: "#7C3AED", chipBg: "#7C3AED", chipTxt: "#fff" },
  silver:     { bg: "#F8FAFC", border: "#CBD5E1", accent: "#475569", chipBg: "#475569", chipTxt: "#fff" },
  gold:       { bg: "#FFFBEB", border: "#F59E0B", accent: "#B45309", chipBg: "#F59E0B", chipTxt: "#fff" },
  platinum:   { bg: "#EFF6FF", border: "#3B82F6", accent: "#1E3A8A", chipBg: "#1E3A8A", chipTxt: "#fff" },
};
const PLAN_LABEL: Record<PlanKey, string> = {
  free_trial: "Free Trial",
  silver: "Silver",
  gold: "Gold",
  platinum: "Platinum",
};

function fillTemplate(tpl: string, brand?: string): string {
  let out = tpl;
  Object.entries(SAMPLE).forEach(([k, v]) => {
    out = out.replace(new RegExp(`\\{${k}\\}`, "g"), v);
  });
  if (brand) out = `${out}\n\n— ${brand}`;
  return out;
}

export default function SettingsScreen() {
  const router = useRouter();
  const navigation = useNavigation();
  const { user, signOut } = useAuth();
  // Phase B+C — when the active session is a TEAM-MEMBER (sub-account),
  // hide admin-only sections and the Team Members management entry
  // even if the parent is an admin. Owners see everything.
  const { isTeamMember, hasPerm } = usePermissions();
  const isAdminUI = (user as any)?.is_admin && !isTeamMember;
  // Settings-section feature flags. We don't gate the Hub itself (admin
  // panel + accounts always show); we gate INTERNAL sections so users on
  // limited plans don't see knobs they can't actually use.
  const flagSmartPasteAi      = useFeatureFlag("smart_paste_ai");
  const flagSmartPasteCustomP = useFeatureFlag("smart_paste_custom_prompt");
  const flagSheetImport       = useFeatureFlag("sheet_import");
  const flagSheetMapping      = useFeatureFlag("sheet_column_mapping");
  const flagAiRateCustom      = useFeatureFlag("ai_rate_customization");
  const flagBrandLogo         = useFeatureFlag("label_brand_logo");
  const flagBrandTagline      = useFeatureFlag("label_brand_tagline");
  const flagLabelCustomFields = useFeatureFlag("label_custom_fields");
  const flagLabelFieldToggles = useFeatureFlag("label_field_toggles");
  const flagWaTemplate        = useFeatureFlag("whatsapp_template_editor");
  const flagWaCopy            = useFeatureFlag("whatsapp_copy_template");
  const flagWaEta             = useFeatureFlag("whatsapp_eta_customization");
  // ── 2026-04-30: feature flags for the 14 newly-registered features ──
  const flagRestoreMyOrders        = useFeatureFlag("sheet_restore_my_orders");
  const flagOrderIdCounterCustom   = useFeatureFlag("master_order_id_counter_custom");
  const flagOrderIdAutofillNew     = useFeatureFlag("master_order_id_autofill_new");
  const flagOfflineSyncQueueView   = useFeatureFlag("offline_sync_queue_view");
  const flagLabelContentBudget     = useFeatureFlag("label_content_budget");
  const params = useLocalSearchParams<{ section?: string }>();
  // Section routing: empty = Hub view, otherwise show only the matching
  // group of cards. Keeps the screen short, focused, and click-driven.
  type Section =
    | ""
    | "account"
    | "business"
    | "couriers"
    | "billing"
    | "whatsapp"
    | "print"
    | "notifications"
    | "about";
  const section: Section = (String(params?.section || "") as Section);
  // Heading shown at the top of each section screen.
  const SECTION_TITLES: Record<Section, string> = {
    "": "Settings",
    account: "My Account",
    business: "Business",
    couriers: "Couriers",
    billing: "Plan & Billing",
    whatsapp: "WhatsApp",
    print: "Print & Labels",
    notifications: "Notifications",
    about: "About & Help",
  };
  const SECTION_SUBTITLES: Record<Section, string> = {
    "": "",
    account: "Profile, plan, and sign-out",
    business: "Brand, sender, Smart Paste AI, Google Sheet",
    couriers: "Manage courier partners",
    billing: "Plan, wallet, and AI processing rates",
    whatsapp: "Customer message templates and ETA",
    print: "Label fields, tagline, custom fields",
    notifications: "Push & email alerts",
    about: "App info and support",
  };
  // Hub cards — clicked to open each section.
  // The `🛡 Admin Panel` card is appended at runtime below for is_admin users.
  const HUB_CARDS: Array<{
    key: Section;
    icon: any;
    title: string;
    sub: string;
    color: string;
  }> = [
    { key: "account",       icon: "person-circle-outline", title: "My Account",      sub: "Profile · Plan · Sign out",          color: "#7C3AED" },
    { key: "business",      icon: "business-outline",      title: "Business",        sub: "Brand · Sender · Smart Paste · Sheet", color: "#0EA5E9" },
    { key: "couriers",      icon: "rocket-outline",        title: "Couriers",        sub: "Manage courier partners",            color: "#10B981" },
    { key: "billing",       icon: "wallet-outline",        title: "Plan & Billing",  sub: "Plan · Wallet · AI rates",           color: "#DC2626" },
    { key: "whatsapp",      icon: "logo-whatsapp",         title: "WhatsApp",        sub: "Templates & ETA",                    color: "#22C55E" },
    { key: "print",         icon: "print-outline",         title: "Print & Labels",  sub: "Label fields · Custom · Tagline",    color: "#F59E0B" },
    { key: "notifications", icon: "notifications-outline", title: "Notifications",   sub: "Push · Email alerts",                color: "#EC4899" },
    { key: "about",         icon: "information-circle-outline", title: "About & Help", sub: "Version · Support · Legal",       color: "#475569" },
  ];

  const [sender, setSender] = useState<SenderAddress>({
    name: "", phone: "", address_line1: "", address_line2: "",
    city: "", state: "", pincode: "", show_contact: true,
  });
  const [template, setTemplate] = useState("");
  const [copyTemplate, setCopyTemplate] = useState("");
  const [showPreview, setShowPreview] = useState(true);
  const [etaDays, setEtaDays] = useState("7");
  const [couriers, setCouriers] = useState<Courier[]>([]);
  // Plan-enforced courier partner cap — silver=1, gold=2, platinum/admin=unlimited.
  const [courierLimits, setCourierLimits] = useState<{
    plan: string;
    plan_label: string;
    is_admin: boolean;
    limit: number | null;
    current_count: number;
    can_add: boolean;
    is_unlimited: boolean;
    suggested_upgrade: string | null;
  } | null>(null);
  const [brandName, setBrandName] = useState("");
  const [brandLogo, setBrandLogo] = useState("");
  const [preferLogo, setPreferLogo] = useState(true);
  const [logoShape, setLogoShape] = useState<"square" | "wide">("square");
  const [shipmentTagline, setShipmentTagline] = useState("");
  const [labelFields, setLabelFields] = useState({
    oid: true, dispatch_date: true, weight: true, item: true, phone: true,
    customer_id: true, token_info: false, box_dimensions: false, shipment_notes: false,
  });
  // Phase B — user-defined custom label fields (max 6)
  const [customFields, setCustomFields] = useState<Array<{
    id?: string;
    label: string;
    value: string;
    position:
      | "header_top"
      | "from_block"
      | "to_block"
      | "meta_row"
      | "notes_area"
      | "footer_bottom";
    enabled: boolean;
    bold?: boolean;
    size?: "xs" | "sm" | "md";
    source?: "static" | "shipment";
    sheet_column?: string;
    placeholder?: string;
  }>>([]);

  // Sheet
  const [sheetUrl, setSheetUrl] = useState("");
  const [sheetStatus, setSheetStatus] = useState<"idle" | "loading" | "connected">("idle");
  const [preview, setPreview] = useState<SheetPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [pickerForField, setPickerForField] = useState<string | null>(null);
  const [connectedSheetId, setConnectedSheetId] = useState("");
  const [connectedHeaders, setConnectedHeaders] = useState<string[]>([]);
  // Phase-5: Service Account email (for sharing private user sheets)
  const [saEmail, setSaEmail] = useState<string>("");
  const [accessMethod, setAccessMethod] = useState<"service_account" | "public_csv" | "">("");

  // Phase-4b+ Smart Paste AI customisation
  const [spaiEnabled, setSpaiEnabled] = useState(true);
  // Phase-7d Order ID auto-generate toggle (Master Order ID system).
  const [orderIdAutoGen, setOrderIdAutoGen] = useState(true);
  // Phase-7e: When auto-gen is ON, also auto-fill in the New Shipment form.
  const [orderIdAutofillNew, setOrderIdAutofillNew] = useState(true);
  // Phase-7f: live current counter + input for migrating from legacy
  // numbering (eg user has shipped 2200 parcels → set seq to 2200 so
  // the next allocation produces …02201).
  const [oidCounterCurrent, setOidCounterCurrent] = useState<number | null>(null);
  const [oidCounterNextPreview, setOidCounterNextPreview] = useState<string>("");
  const [oidCounterInput, setOidCounterInput] = useState<string>("");
  const [oidCounterSaving, setOidCounterSaving] = useState<boolean>(false);
  const [spaiInstructions, setSpaiInstructions] = useState("");
  const [spaiDefaultPrompt, setSpaiDefaultPrompt] = useState("");
  const [spaiShowDefault, setSpaiShowDefault] = useState(false);
  const [spaiSaving, setSpaiSaving] = useState(false);

  // Phase-4b+ AI credit rate card (per-user tunable; max 2.0 per spec cap)
  const [aiCostSimple, setAiCostSimple] = useState("0.5");
  const [aiCostMedium, setAiCostMedium] = useState("1.0");
  const [aiCostComplex, setAiCostComplex] = useState("2.0");
  const [rateCardSaving, setRateCardSaving] = useState(false);

  // Wallet balance for billing section
  const [walletBal, setWalletBal] = useState<number | null>(null);

  // Bumped each time load() finishes — drives reliable snapshot capture for
  // the dirty-tracking logic below. Declared up here so the hooks that
  // depend on it can reference it without TDZ errors.
  const [loadVersion, setLoadVersion] = useState(0);

  // ── Unsaved-changes tracking ──────────────────────────────────────
  // Snapshot taken when a section is first opened. Compared against the
  // live state on every render to detect dirty fields. If user taps the
  // back chevron while dirty, we prompt: Save / Discard / Cancel.
  const [originalSnap, setOriginalSnap] = useState<string | null>(null);
  const [savingFromAlert, setSavingFromAlert] = useState(false);
  // Dedicated visible modal so the warning works identically on web + mobile
  // (RN Web's Alert.alert with 3 buttons is unreliable).
  const [unsavedOpen, setUnsavedOpen] = useState(false);

  // Compute the current "shape" of editable fields for the active section.
  // Order/keys must stay stable so JSON.stringify diff is reliable.
  const getSectionSnapshot = useCallback((sec: string): string => {
    switch (sec) {
      case "business":
        return JSON.stringify({
          sender,
          brandName,
          brandLogo,
          preferLogo,
          logoShape,
          spaiEnabled,
          spaiInstructions,
          orderIdAutoGen,
          orderIdAutofillNew,
        });
      case "print":
        return JSON.stringify({
          labelFields,
          customFields,
          shipmentTagline,
        });
      case "whatsapp":
        return JSON.stringify({
          template,
          copyTemplate,
          etaDays,
        });
      case "billing":
        return JSON.stringify({
          aiCostSimple,
          aiCostMedium,
          aiCostComplex,
        });
      default:
        return "";
    }
  }, [
    sender, brandName, brandLogo, preferLogo, logoShape,
    spaiEnabled, spaiInstructions, orderIdAutoGen, orderIdAutofillNew,
    labelFields, customFields, shipmentTagline,
    template, copyTemplate, etaDays,
    aiCostSimple, aiCostMedium, aiCostComplex,
  ]);

  // Keep a ref to the latest snapshot fn so timers / handlers can read fresh
  // state without re-creating themselves on every render.
  const getSnapRef = useRef(getSectionSnapshot);
  useEffect(() => { getSnapRef.current = getSectionSnapshot; }, [getSectionSnapshot]);

  // (Re)capture the original snapshot:
  //   • Hub view (no section): clear the baseline.
  //   • Section view + data not yet loaded: clear and wait.
  //   • Section view + data loaded: capture state as the baseline.
  // This fires reliably on section change AND on first load — no debounce
  // gymnastics, so fast typers can't slip past the dirty check.
  const liveSnap = section ? getSectionSnapshot(section) : "";
  useEffect(() => {
    if (!section) {
      setOriginalSnap(null);
      return;
    }
    if (loadVersion === 0) {
      setOriginalSnap(null);
      return;
    }
    setOriginalSnap(getSnapRef.current(section));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [section, loadVersion]);

  // Live "is the user mid-edit?" flag.
  const isDirty = !!section && originalSnap !== null
    && originalSnap !== liveSnap;

  // Section-aware save: bundles all editable fields for that section into
  // a single PUT /settings call. Smart Paste AI fields piggy-back on the
  // business save, AI rate-card piggy-backs on the billing save.
  const saveSectionAndExit = async (sec: string) => {
    try {
      setSavingFromAlert(true);
      if (sec === "billing") {
        await saveRateCard();
      } else {
        const payload: any = {
          sender,
          brand: { name: brandName, logo_base64: brandLogo },
          whatsapp_template: template,
          copy_template: copyTemplate,
          default_eta_days: Number(etaDays) || 7,
          prefer_logo: preferLogo,
          logo_shape: logoShape,
          shipment_tagline: shipmentTagline,
          label_fields: labelFields,
          custom_fields: customFields,
          smart_paste_ai_enabled: spaiEnabled,
          smart_paste_instructions: spaiInstructions,
          order_id_auto_generate: orderIdAutoGen,
          order_id_autofill_in_new_shipment: orderIdAutofillNew,
        };
        await api.put("/settings", payload);
      }
      // Reset snapshot so we don't double-prompt on the next mount
      setOriginalSnap(getSectionSnapshot(sec));
      // Resume pending nav (hardware/swipe back) OR fall back to hub
      const pending = pendingNavActionRef.current;
      pendingNavActionRef.current = null;
      if (pending) {
        try { (navigation as any).dispatch(pending); }
        catch { router.replace("/(tabs)/settings"); }
      } else {
        router.replace("/(tabs)/settings");
      }
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Please try again");
    } finally {
      setSavingFromAlert(false);
    }
  };

  // Back-press handler with dirty-state guard.
  const handleBackPress = () => {
    if (!isDirty) {
      router.replace("/(tabs)/settings");
      return;
    }
    setUnsavedOpen(true);
  };

  // ── Hardware-back / swipe-back interception ───────────────────
  // Reactive refs so the listeners always see the latest values
  // without re-subscribing on every keystroke (which flickers).
  const isDirtyRef = useRef(isDirty);
  const sectionRef = useRef(section);
  useEffect(() => { isDirtyRef.current = isDirty; }, [isDirty]);
  useEffect(() => { sectionRef.current = section; }, [section]);

  // Android hardware-back: intercept while we're on a section page so
  // it shows our modal instead of silently popping the route.
  useEffect(() => {
    if (Platform.OS === "web") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (!sectionRef.current) return false;        // hub view → default behaviour
      if (!isDirtyRef.current) {
        router.replace("/(tabs)/settings");
        return true;                                 // we navigated, swallow event
      }
      setUnsavedOpen(true);
      return true;                                   // swallow – we'll wait for user
    });
    return () => sub.remove();
  }, []);

  // iOS swipe-back / generic navigation pop: react-navigation event.
  // beforeRemove fires for ANY back navigation triggered by the framework
  // (gesture, hardware back, header back, programmatic goBack).
  // We DO NOT pre-empt navigation when isDirty is false so normal flow works.
  const pendingNavActionRef = useRef<any>(null);
  useEffect(() => {
    const nav: any = navigation;
    if (!nav?.addListener) return;
    const handler = (e: any) => {
      if (!sectionRef.current) return;        // hub view: allow
      if (!isDirtyRef.current) return;        // no edits: allow
      e.preventDefault();
      pendingNavActionRef.current = e.data?.action;
      setUnsavedOpen(true);
    };
    const unsub = nav.addListener("beforeRemove", handler);
    return () => {
      try { unsub?.(); } catch { /* ignore */ }
    };
  }, [navigation]);

  const load = useCallback(async () => {
    const [s, cs] = await Promise.all([Api.getSettings(), Api.listCouriers()]);
    setSender(s.sender);
    setTemplate(s.whatsapp_template);
    setCopyTemplate(s.copy_template);
    setEtaDays(String(s.default_eta_days));
    setCouriers(cs);
    // Best-effort fetch of the plan-enforced courier cap. Never blocks.
    Api.getCourierLimits()
      .then((lim) => setCourierLimits(lim))
      .catch(() => setCourierLimits(null));
    setBrandName(s.brand?.name || "");
    setBrandLogo(s.brand?.logo_base64 || "");
    setPreferLogo((s as any).prefer_logo !== false);
    setLogoShape(((s as any).logo_shape as any) === "wide" ? "wide" : "square");
    if ((s as any).label_fields) {
      setLabelFields((prev) => ({ ...prev, ...(s as any).label_fields }));
    }
    setCustomFields(((s as any).custom_fields || []) as any);
    setShipmentTagline(String((s as any).shipment_tagline || ""));
    // Phase-4b+ smart paste AI fields
    setSpaiEnabled((s as any).smart_paste_ai_enabled !== false);
    setSpaiInstructions(String((s as any).smart_paste_instructions || ""));
    // Phase-7d: Master Order ID auto-generate (default ON)
    setOrderIdAutoGen((s as any).order_id_auto_generate !== false);
    setOrderIdAutofillNew((s as any).order_id_autofill_in_new_shipment !== false);
    // Phase-7f: load current Master Order ID counter (separate endpoint
    // since it's not on the Settings doc — it's a global counter doc).
    try {
      const c = await Api.getMasterIdCounter();
      setOidCounterCurrent(c.current_seq);
      setOidCounterNextPreview(c.next_master_order_id);
    } catch {
      /* ignore — fresh user / offline */
    }
    // Phase-4b+ AI rate card (fall back to spec defaults)
    setAiCostSimple(
      String(
        (s as any).ai_cost_simple != null ? (s as any).ai_cost_simple : 0.5,
      ),
    );
    setAiCostMedium(
      String(
        (s as any).ai_cost_medium != null ? (s as any).ai_cost_medium : 1.0,
      ),
    );
    setAiCostComplex(
      String(
        (s as any).ai_cost_complex != null ? (s as any).ai_cost_complex : 2.0,
      ),
    );
    if (s.sheet?.sheet_id) {
      setSheetStatus("connected");
      // Fallback: reconstruct the URL when the stored settings only
      // hold the sheet_id (older docs / admin-seeded accounts where
      // `url` was never persisted). Without this, the Refresh /
      // Re-map button falls through to the "Please paste your
      // Google Sheet URL" alert even though the sheet IS connected.
      const reconstructed = s.sheet.sheet_id
        ? `https://docs.google.com/spreadsheets/d/${s.sheet.sheet_id}/edit`
        : "";
      setSheetUrl(s.sheet.url || reconstructed);
      setConnectedSheetId(s.sheet.sheet_id);
      setConnectedHeaders(s.sheet.headers || []);
      setMapping(s.sheet.column_mapping || {});
    }
    // Phase-5: fetch the Service Account email so we can show users
    // exactly which address to share their Sheet with. Lazy + best-
    // effort — never blocks the rest of the screen on failure.
    Api.sheetsServiceAccount()
      .then((sa) => setSaEmail(sa?.email || ""))
      .catch(() => {});
    // Bump load-version so the dirty-tracking useEffect captures a fresh
    // snapshot AFTER all the setters above have flushed into React state.
    setLoadVersion((v) => v + 1);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  // Pull the bundled default prompt once so the user can peek / reset.
  useEffect(() => {
    (async () => {
      try {
        const r = await Api.smartPasteDefaultPrompt();
        setSpaiDefaultPrompt(r.default_prompt || "");
      } catch {
        /* non-fatal */
      }
    })();
  }, []);

  // Lazy-load wallet balance only when user is on the billing section.
  useEffect(() => {
    if (section !== "billing") return;
    let cancelled = false;
    (async () => {
      try {
        const w: any = await Api.getWallet();
        // Wallet API returns `remaining_credits` (not `balance`). The
        // old code read w?.balance which was always undefined → the
        // billing tile silently rendered ₹0 while the /wallet screen
        // correctly showed the real credit balance. Fixed to match.
        const bal =
          typeof w?.remaining_credits === "number"
            ? w.remaining_credits
            : typeof w?.balance === "number"
              ? w.balance
              : 0;
        if (!cancelled) setWalletBal(bal);
      } catch {
        if (!cancelled) setWalletBal(0);
      }
    })();
    return () => { cancelled = true; };
  }, [section]);

  const saveSmartPasteAI = async () => {
    try {
      setSpaiSaving(true);
      await api.put("/settings", {
        smart_paste_ai_enabled: spaiEnabled,
        smart_paste_instructions: spaiInstructions,
        order_id_auto_generate: orderIdAutoGen,
        order_id_autofill_in_new_shipment: orderIdAutofillNew,
      });
      Alert.alert("Saved", "Smart Paste AI settings updated.");
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Please try again");
    } finally {
      setSpaiSaving(false);
    }
  };

  const resetSmartPasteAI = () => {
    Alert.alert(
      "Reset instructions?",
      "This will clear your custom instructions. The bundled default prompt will be used as-is.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: () => setSpaiInstructions(""),
        },
      ],
    );
  };

  // --- AI rate card save/reset ---
  const clamp02 = (raw: string, fallback: number): number => {
    const n = Number((raw || "").replace(/[^\d.]/g, ""));
    if (!Number.isFinite(n)) return fallback;
    return Math.max(0, Math.min(2, Math.round(n * 100) / 100));
  };

  const saveRateCard = async () => {
    try {
      setRateCardSaving(true);
      const simple = clamp02(aiCostSimple, 0.5);
      const medium = clamp02(aiCostMedium, 1.0);
      const complex = clamp02(aiCostComplex, 2.0);
      // Phase-5b: persist to GLOBAL admin config so every user inherits
      // the same rates. Per-user override is no longer used.
      const r = await api.put("/admin/global-config", {
        global_ai_rates: { simple, medium, complex },
      });
      const d = (r.data as any)?.global_ai_rates || {};
      // Snap UI back to server-clamped values so admin sees what was saved.
      setAiCostSimple(String(d.simple ?? simple));
      setAiCostMedium(String(d.medium ?? medium));
      setAiCostComplex(String(d.complex ?? complex));
      Alert.alert(
        "Global rate card saved",
        `Simple ${d.simple ?? simple} · Medium ${d.medium ?? medium} · Complex ${d.complex ?? complex} credits per order. Applied to every user.`,
      );
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Please try again");
    } finally {
      setRateCardSaving(false);
    }
  };

  const resetRateCard = () => {
    setAiCostSimple("0.5");
    setAiCostMedium("1.0");
    setAiCostComplex("2.0");
  };

  const saveSender = async () => {
    try {
      await Api.updateSettings({
        sender,
        brand: { name: brandName, logo_base64: brandLogo },
        whatsapp_template: template,
        copy_template: copyTemplate,
        default_eta_days: Number(etaDays) || 7,
        prefer_logo: preferLogo,
        logo_shape: logoShape,
        shipment_tagline: shipmentTagline,
        label_fields: labelFields,
        custom_fields: customFields,
      } as Partial<SettingsT>);
      Alert.alert("Saved", "Settings saved successfully.");
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const fetchPreview = async () => {
    // Self-heal: when we are already "connected" (admin accounts sometimes
    // store only sheet_id without url), reconstruct a canonical Google
    // Sheets URL from the stored sheet_id so Refresh / Re-map doesn't
    // bounce the user back to the "Paste link" alert.
    let url = sheetUrl.trim();
    if (!url && connectedSheetId) {
      url = `https://docs.google.com/spreadsheets/d/${connectedSheetId}/edit`;
      setSheetUrl(url);
    }
    if (!url) {
      Alert.alert("Paste link", "Please paste your Google Sheet URL");
      return;
    }
    setSheetStatus("loading");
    try {
      const p = await Api.sheetsPreview(url);
      setPreview(p);
      setMapping(p.auto_mapping || {});
      setAccessMethod((p.access_method as any) || "");
      setSheetStatus("idle");
    } catch (e: any) {
      setSheetStatus("idle");
      Alert.alert("Error", e?.response?.data?.detail || "Failed to load sheet. Make sure it's shared with 'Anyone with the link'.");
    }
  };

  const saveSheet = async () => {
    if (!preview) return;
    try {
      await Api.updateSettings({
        sheet: {
          url: sheetUrl.trim(),
          sheet_id: preview.sheet_id,
          gid: preview.gid,
          tab_name: "",
          headers: preview.headers,
          column_mapping: mapping,
        },
      } as Partial<SettingsT>);
      setSheetStatus("connected");
      setConnectedSheetId(preview.sheet_id);
      setConnectedHeaders(preview.headers);
      Alert.alert("Connected", "Google Sheet connected. Column mapping saved.");
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const disconnectSheet = () => {
    Alert.alert("Disconnect Sheet?", "Stored column mapping will be cleared.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Disconnect",
        style: "destructive",
        onPress: async () => {
          await Api.updateSettings({
            sheet: {
              url: "", sheet_id: "", gid: "", tab_name: "",
              headers: [], column_mapping: {},
            },
          } as Partial<SettingsT>);
          setSheetUrl("");
          setPreview(null);
          setMapping({});
          setSheetStatus("idle");
          setConnectedSheetId("");
          setConnectedHeaders([]);
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        {section ? (
          <TouchableOpacity
            onPress={handleBackPress}
            hitSlop={10}
            style={{ marginRight: 8 }}
            testID="settings-back"
          >
            <PhIcon name="chevron-back" size={26} color={colors.text} />
          </TouchableOpacity>
        ) : null}
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Text style={styles.title}>{SECTION_TITLES[section]}</Text>
            {section && isDirty ? (
              <View style={styles.dirtyBadge} testID="dirty-badge">
                <View style={styles.dirtyDot} />
                <Text style={styles.dirtyBadgeTxt}>Unsaved</Text>
              </View>
            ) : null}
          </View>
          {section ? (
            <Text style={styles.titleSub}>{SECTION_SUBTITLES[section]}</Text>
          ) : null}
        </View>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          testID="settings-scroll"
          contentContainerStyle={{ padding: 16, paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled"
        >
          {/* HUB: list of section cards. Click → /(tabs)/settings?section=key */}
          {!section && (
            <View>
              <View style={styles.hubGroup}>
                {HUB_CARDS.map((c, idx) => (
                  <TouchableOpacity
                    key={c.key}
                    testID={`settings-hub-${c.key}`}
                    style={[
                      styles.hubRow,
                      idx === 0 && styles.hubRowFirst,
                      idx === HUB_CARDS.length - 1 && !((user as any)?.is_admin) && styles.hubRowLast,
                    ]}
                    onPress={() =>
                      router.push({ pathname: "/(tabs)/settings", params: { section: c.key } })
                    }
                    activeOpacity={0.6}
                  >
                    <View style={[styles.hubIconWrap, { backgroundColor: c.color + "1A" }]}>
                      <PhIcon name={c.icon} size={22} color={c.color} />
                    </View>
                    <Text style={styles.hubTitle}>{c.title}</Text>
                    <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                  </TouchableOpacity>
                ))}
                {/* Admin Panel — only for is_admin users (and never for team-member sessions) */}
                {isAdminUI ? (
                  <>
                    <TouchableOpacity
                      testID="settings-hub-admin"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/plan-features")}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#FECACA" + "AA" }]}>
                        <PhIcon name="shield-checkmark-outline" size={22} color="#B91C1C" />
                      </View>
                      <Text style={styles.hubTitle}>Plan Features</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-packages"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/credit-packages")}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#FED7AA" }]}>
                        <PhIcon name="gift-outline" size={22} color="#C2410C" />
                      </View>
                      <Text style={styles.hubTitle}>Credit Packages</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-users"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/users" as any)}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#DBEAFE" }]}>
                        <PhIcon name="people-outline" size={22} color="#1D4ED8" />
                      </View>
                      <Text style={styles.hubTitle}>Users</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-pricing"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/pricing")}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#FCE7F3" }]}>
                        <PhIcon name="pricetags-outline" size={22} color="#BE185D" />
                      </View>
                      <Text style={styles.hubTitle}>Plan Pricing</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-wa-pricing"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/whatsapp-pricing" as any)}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#DCFCE7" }]}>
                        <PhIcon name="logo-whatsapp" size={22} color="#16A34A" />
                      </View>
                      <Text style={styles.hubTitle}>WhatsApp Pricing</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-stage-rules"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/stage-rules" as any)}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#FEE2E2" }]}>
                        <PhIcon name="time-outline" size={22} color="#DC2626" />
                      </View>
                      <Text style={styles.hubTitle}>Status Rules</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-wa-templates"
                      style={styles.hubRow}
                      onPress={() => router.push("/admin/whatsapp-templates" as any)}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#DCFCE7" }]}>
                        <PhIcon name="chatbubble-ellipses-outline" size={22} color="#16A34A" />
                      </View>
                      <Text style={styles.hubTitle}>WhatsApp Templates</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="settings-hub-admin-master-sheet"
                      style={[styles.hubRow, styles.hubRowLast]}
                      onPress={() => router.push("/admin/master-sheet" as any)}
                      activeOpacity={0.6}
                    >
                      <View style={[styles.hubIconWrap, { backgroundColor: "#DCFCE7" }]}>
                        <PhIcon name="grid-outline" size={22} color="#15803D" />
                      </View>
                      <Text style={styles.hubTitle}>Master Sheet</Text>
                      <View style={styles.adminPill}>
                        <Text style={styles.adminPillTxt}>ADMIN</Text>
                      </View>
                      <PhIcon name="chevron-forward" size={22} color="#9CA3AF" />
                    </TouchableOpacity>
                  </>
                ) : null}
              </View>

              <TouchableOpacity
                testID="settings-signout"
                onPress={() =>
                  Alert.alert("Sign out?", "You'll need to log in again.", [
                    { text: "Cancel", style: "cancel" },
                    { text: "Sign out", style: "destructive", onPress: () => signOut() },
                  ])
                }
                style={styles.hubSignOutBtn}
              >
                <PhIcon name="log-out-outline" size={20} color="#DC2626" />
                <Text style={styles.hubSignOutText}>Sign out</Text>
              </TouchableOpacity>
            </View>
          )}

          {/* === SECTION: My Account === */}
          {section === "account" && (<>
          {/* Account (Phase-1 multi-tenant auth) */}
          <Section title="Account" icon="person-circle-outline">
            <View style={styles.accountRow}>
              <View style={styles.accountAvatar}>
                <Text style={styles.accountAvatarTxt}>
                  {(user?.name || user?.email || "?").slice(0, 1).toUpperCase()}
                </Text>
              </View>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={styles.accountName} numberOfLines={1}>
                  {user?.name || user?.shop_name || user?.email || "Guest"}
                </Text>
                <Text style={styles.accountEmail} numberOfLines={1}>
                  {user?.email || ""}
                </Text>
                {user?.phone ? (
                  <Text style={styles.accountEmail} numberOfLines={1}>📞 {user.phone}</Text>
                ) : null}
                <View style={{ flexDirection: "row", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                  {user?.display_id ? (
                    <View style={styles.badgeDisplayId}>
                      <PhIcon name="finger-print" size={10} color="#0F172A" />
                      <Text style={styles.badgeDisplayIdTxt}>{user.display_id}</Text>
                    </View>
                  ) : null}
                  {user?.is_admin ? (
                    <View style={styles.badgeAdmin}>
                      <PhIcon name="shield-checkmark" size={11} color="#fff" />
                      <Text style={styles.badgeTxt}>Admin</Text>
                    </View>
                  ) : null}
                  <TouchableOpacity
                    testID="plan-badge-link"
                    onPress={() => router.push("/plans")}
                    style={styles.badgePlan}
                  >
                    <Text style={[styles.badgeTxt, { color: "#92400E" }]}>
                      {(user?.plan || "free_trial").replace("_", " ").toUpperCase()}
                    </Text>
                    <PhIcon name="chevron-forward" size={11} color="#92400E" />
                  </TouchableOpacity>
                </View>
              </View>
            </View>

            <TouchableOpacity
              testID="clear-demo-btn"
              style={[styles.secondaryBtn, { marginTop: 12 }]}
              onPress={() => {
                // Phase-21 — One-confirmation, one-click sweep. User
                // reported the old flow appeared to do nothing (only
                // shipments got removed; the demo courier + any demo
                // pending orders stayed). The backend now wipes ALL
                // three surfaces atomically and returns a breakdown,
                // which we surface in the success alert.
                Alert.alert(
                  "Confirm clear demo?",
                  "Removes every seeded demo shipment, demo pending order, and the starter Demo Courier (only if no real shipment uses it). Your real data is safe.",
                  [
                    { text: "Cancel", style: "cancel" },
                    {
                      text: "Clear Demo",
                      style: "destructive",
                      onPress: async () => {
                        try {
                          const r = await api.post("/demo/clear");
                          const d = r.data || {};
                          // Build a per-collection summary so the
                          // operator can see EXACTLY what was wiped
                          // instead of a single ambiguous number.
                          const parts: string[] = [];
                          if (d.shipments)      parts.push(`${d.shipments} shipment${d.shipments === 1 ? "" : "s"}`);
                          if (d.pending_orders) parts.push(`${d.pending_orders} pending order${d.pending_orders === 1 ? "" : "s"}`);
                          if (d.couriers)      parts.push(`${d.couriers} demo courier${d.couriers === 1 ? "" : "s"}`);
                          const total = Number(d.deleted ?? 0);
                          const msg = total === 0
                            ? "No demo data was found — your account is already clean."
                            : `Removed ${parts.join(" · ")}.`;
                          Alert.alert("Done", msg, [
                            {
                              text: "OK",
                              onPress: () => {
                                // Hard refresh of the dashboard so
                                // the now-stale shipment count + cards
                                // disappear instantly. We just bounce
                                // the user back to Home which re-runs
                                // every focus-effect on its tabs.
                                router.replace("/(tabs)" as any);
                              },
                            },
                          ]);
                        } catch (e: any) {
                          Alert.alert("Failed", e?.message || "Could not clear demo data");
                        }
                      },
                    },
                  ]
                );
              }}
            >
              <PhIcon name="sparkles-outline" size={16} color={colors.primary} />
              <Text style={styles.secondaryBtnTxt}>Clear Demo Data</Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="sign-out-btn"
              style={[styles.dangerBtn, { marginTop: 8 }]}
              onPress={() => {
                Alert.alert("Sign out?", "You'll need to log in again.", [
                  { text: "Cancel", style: "cancel" },
                  {
                    text: "Sign out",
                    style: "destructive",
                    onPress: async () => {
                      await signOut();
                    },
                  },
                ]);
              }}
            >
              <PhIcon name="log-out-outline" size={16} color="#C62828" />
              <Text style={styles.dangerBtnTxt}>Sign out</Text>
            </TouchableOpacity>
          </Section>
          </>)}

          {section === "business" && (<>
          {/* Phase-4b+ Smart Paste AI — Phase-21 cleanup:
              • Section heading removed (was "Smart Paste AI").
              • Purple badge re-labelled "Smart Fill" (operator-facing
                copy; "AI powered" leaked the implementation detail).
              • The "Enable AI parser" toggle + regex-fallback subtitle
                were removed — the parser is always ON for users who
                have the feature flag; admins manage availability via
                Plan Features. The Auto-Generate Order ID toggle
                below stays exactly as it was. */}
          {flagSmartPasteAi ? (
          <Section title="" icon="sparkles-outline">
            <View style={styles.spaiIntro}>
              <View style={styles.spaiBadge}>
                <PhIcon name="sparkles" size={11} color="#fff" />
                <Text style={styles.spaiBadgeTxt}>Smart Fill</Text>
              </View>
              <Text style={styles.spaiHint}>
                Paste customer's WhatsApp message here — the form will auto-fill. You can keep your own custom instructions for your business.
              </Text>
            </View>

            {/* Auto-generate Order ID toggle — user-facing copy intentionally
                minimal; internal "Master Order ID" terminology + YYMMDD pattern
                are implementation details that shouldn't leak to end users. */}
            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLbl}>Auto-Generate Order ID</Text>
                <Text style={styles.toggleSub}>
                  ON: A unique Order ID will be generated automatically for every new order. OFF: Enter your own Order ID manually.
                </Text>
              </View>
              <Switch
                testID="order-id-auto-gen-toggle"
                value={orderIdAutoGen}
                onValueChange={setOrderIdAutoGen}
              />
            </View>

            {/* Phase-7e: Auto-fill in New Shipment toggle (only relevant
                when Auto-Generate is ON). */}
            {flagOrderIdAutofillNew && (
            <View
              style={[
                styles.toggleRow,
                !orderIdAutoGen && { opacity: 0.5 },
              ]}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLbl}>Auto-fill in New Shipment</Text>
                <Text style={styles.toggleSub}>
                  ON: When the New Shipment form opens, the auto-generated Order ID is filled in automatically. OFF: The form input stays blank — type your own custom ID if needed.
                </Text>
              </View>
              <Switch
                testID="order-id-autofill-new-toggle"
                value={orderIdAutofillNew}
                onValueChange={setOrderIdAutofillNew}
                disabled={!orderIdAutoGen}
              />
            </View>
            )}

            {/* Phase-7f: Master Order ID counter customisation
                (one-time migration helper for users with legacy ID series). */}
            {flagOrderIdCounterCustom && (
            <View
              style={[
                styles.toggleRow,
                !orderIdAutoGen && { opacity: 0.5 },
                { flexDirection: "column", alignItems: "stretch", gap: 8 },
              ]}
            >
              <View>
                <Text style={styles.toggleLbl}>Order ID Sequence Number</Text>
                <Text style={styles.toggleSub}>
                  Current counter: <Text style={{ fontWeight: "800" }}>
                    {oidCounterCurrent ?? "…"}
                  </Text>
                  {oidCounterNextPreview ? (
                    <Text>  ·  Next ID: <Text style={{ fontWeight: "800" }}>
                      {oidCounterNextPreview}
                    </Text></Text>
                  ) : null}
                </Text>
                <Text style={[styles.toggleSub, { marginTop: 4 }]}>
                  Useful for cases like starting after 2200 existing parcels — type 2200 in the input and tap Set; the next allocation will be `{(oidCounterNextPreview || "").slice(0, 6)}02201`. The YYMMDD prefix automatically reflects today's date.
                </Text>
              </View>
              <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
                <TextInput
                  testID="oid-counter-input"
                  value={oidCounterInput}
                  onChangeText={(t) => setOidCounterInput(t.replace(/[^\d]/g, ""))}
                  placeholder={`e.g. 2200  (last shipped count)`}
                  placeholderTextColor="#9CA3AF"
                  keyboardType="numeric"
                  editable={orderIdAutoGen && !oidCounterSaving}
                  style={[styles.input, { flex: 1 }]}
                />
                <TouchableOpacity
                  testID="oid-counter-save"
                  disabled={
                    !orderIdAutoGen ||
                    oidCounterSaving ||
                    !oidCounterInput.trim()
                  }
                  onPress={async () => {
                    const seq = parseInt(oidCounterInput.trim(), 10);
                    if (!Number.isFinite(seq) || seq < 0) {
                      Alert.alert("Invalid number", "Enter a positive whole number.");
                      return;
                    }
                    const cur = oidCounterCurrent ?? 0;
                    let force = false;
                    if (seq < cur) {
                      // Confirm: lowering risks duplicates.
                      const ok = await new Promise<boolean>((resolve) => {
                        Alert.alert(
                          "Lowering counter?",
                          `Counter is currently at ${cur}. Setting it to ${seq} could create duplicate Order IDs with existing shipments. Continue?`,
                          [
                            { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
                            { text: "Force", style: "destructive", onPress: () => resolve(true) },
                          ],
                        );
                      });
                      if (!ok) return;
                      force = true;
                    }
                    setOidCounterSaving(true);
                    try {
                      const r = await Api.setMasterIdCounter(seq, force);
                      setOidCounterCurrent(r.current_seq);
                      setOidCounterNextPreview(r.next_master_order_id);
                      setOidCounterInput("");
                      Alert.alert(
                        "✅ Counter updated",
                        `Next Order ID will be ${r.next_master_order_id}.`,
                      );
                    } catch (e: any) {
                      Alert.alert(
                        "Failed",
                        e?.response?.data?.detail || e?.message || "Could not update counter.",
                      );
                    } finally {
                      setOidCounterSaving(false);
                    }
                  }}
                  style={{
                    paddingHorizontal: 14,
                    paddingVertical: 12,
                    backgroundColor: "#7C3AED",
                    borderRadius: 8,
                    opacity:
                      !orderIdAutoGen ||
                      oidCounterSaving ||
                      !oidCounterInput.trim()
                        ? 0.5
                        : 1,
                  }}
                >
                  {oidCounterSaving ? (
                    <ActivityIndicator size="small" color="#fff" />
                  ) : (
                    <Text style={{ color: "#fff", fontWeight: "800", fontSize: 13 }}>
                      Set
                    </Text>
                  )}
                </TouchableOpacity>
              </View>
            </View>
            )}

            <Text style={styles.fieldLabel}>Your custom instructions (optional)</Text>
            <Text style={styles.fieldHelp}>
              This text is injected before your default ShipBot rules. e.g. "Always use ODC3 as default item", "Ignore 'Rush order' keyword", etc.
            </Text>
            <TextInput
              testID="spai-instructions-input"
              style={styles.spaiTextArea}
              value={spaiInstructions}
              onChangeText={setSpaiInstructions}
              placeholder={
                "Example:\n- Token ₹ means prepaid advance, route to NOTES.\n- Default courier is India Post if not specified.\n- Any number after 'wt' is weight in grams."
              }
              placeholderTextColor="#94A3B8"
              multiline
              numberOfLines={8}
              textAlignVertical="top"
            />
            <Text style={styles.spaiCharCount}>
              {spaiInstructions.length} / 8000 chars
            </Text>

            <View style={styles.spaiActions}>
              <TouchableOpacity
                testID="spai-save-btn"
                disabled={spaiSaving}
                onPress={saveSmartPasteAI}
                style={[styles.primaryBtn, spaiSaving && { opacity: 0.6 }]}
              >
                {spaiSaving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <PhIcon name="checkmark-circle" size={16} color="#fff" />
                    <Text style={styles.primaryBtnTxt}>Save</Text>
                  </>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                testID="spai-reset-btn"
                style={styles.secondaryBtn}
                onPress={resetSmartPasteAI}
              >
                <PhIcon name="refresh" size={16} color={colors.primary} />
                <Text style={styles.secondaryBtnTxt}>Reset</Text>
              </TouchableOpacity>
            </View>

            <TouchableOpacity
              testID="spai-default-toggle"
              style={styles.spaiDefaultToggle}
              onPress={() => setSpaiShowDefault((v) => !v)}
            >
              <PhIcon
                name={spaiShowDefault ? "chevron-up" : "chevron-down"}
                size={14}
                color="#64748B"
              />
              <Text style={styles.spaiDefaultToggleTxt}>
                {spaiShowDefault ? "Hide" : "View"} bundled ShipBot rules
              </Text>
            </TouchableOpacity>
            {spaiShowDefault && spaiDefaultPrompt ? (
              <View style={styles.spaiDefaultBox}>
                <Text style={styles.spaiDefaultText}>{spaiDefaultPrompt}</Text>
              </View>
            ) : null}
          </Section>
          ) : null}
          </>)}

          {section === "billing" && (<>
          {/* Current Plan card */}
          <TouchableOpacity
            testID="billing-plans-card"
            style={[
              styles.planCard,
              {
                backgroundColor: PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].bg,
                borderColor: PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].border,
              },
            ]}
            onPress={() => router.push("/plans")}
            activeOpacity={0.7}
          >
            <View style={styles.planCardHead}>
              <View
                style={[
                  styles.planChip,
                  { backgroundColor: PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].chipBg },
                ]}
              >
                <PhIcon
                  name={user?.plan === "platinum" ? "rocket" : "ribbon"}
                  size={12}
                  color={PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].chipTxt}
                />
                <Text
                  style={[
                    styles.planChipTxt,
                    { color: PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].chipTxt },
                  ]}
                >
                  Current Plan
                </Text>
              </View>
              <PhIcon name="chevron-forward" size={20} color="#94A3B8" />
            </View>
            <Text
              style={[
                styles.planName,
                { color: PLAN_THEME[(user?.plan as PlanKey) || "free_trial"].accent },
              ]}
            >
              {PLAN_LABEL[(user?.plan as PlanKey) || "free_trial"]}
            </Text>
            <Text style={styles.planSub}>Tap to upgrade or compare plans</Text>
          </TouchableOpacity>

          {/* All plan tiles */}
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
            {(["free_trial", "silver", "gold", "platinum"] as PlanKey[]).map((k) => {
              const active = (user?.plan || "free_trial") === k;
              const t = PLAN_THEME[k];
              return (
                <TouchableOpacity
                  key={k}
                  testID={`billing-plan-tile-${k}`}
                  onPress={() => router.push("/plans")}
                  activeOpacity={0.7}
                  style={[
                    styles.planTile,
                    { backgroundColor: t.bg, borderColor: active ? t.chipBg : t.border },
                    active && { borderWidth: 2 },
                  ]}
                >
                  <PhIcon
                    name={k === "platinum" ? "rocket" : k === "gold" ? "trophy" : k === "silver" ? "ribbon" : "rose"}
                    size={18}
                    color={t.accent}
                  />
                  <Text style={[styles.planTileName, { color: t.accent }]}>
                    {PLAN_LABEL[k]}
                  </Text>
                  {active ? (
                    <View style={[styles.planTileBadge, { backgroundColor: t.chipBg }]}>
                      <Text style={[styles.planTileBadgeTxt, { color: t.chipTxt }]}>YOU</Text>
                    </View>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Wallet card */}
          <TouchableOpacity
            testID="billing-wallet-card"
            style={styles.walletCard}
            onPress={() => router.push("/wallet")}
            activeOpacity={0.7}
          >
            <View style={styles.walletIconWrap}>
              <PhIcon name="wallet-outline" size={22} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.walletLabel}>Credit Wallet</Text>
              <Text style={styles.walletBalance}>
                {walletBal != null
                  ? `${walletBal.toLocaleString("en-IN", {
                      minimumFractionDigits: walletBal % 1 === 0 ? 0 : 2,
                      maximumFractionDigits: 2,
                    })} credits`
                  : "Tap to view"}
              </Text>
            </View>
            <PhIcon name="chevron-forward" size={22} color="#94A3B8" />
          </TouchableOpacity>

          {/* AI Processing Charges — admin-only. Regular users don't need
              to see (or edit) the rate-card; their charges are dictated by
              the admin via /admin/global-config. */}
          {(user as any)?.is_admin ? (
          <Section title="AI Processing Charges" icon="pricetags-outline">
            <Text style={styles.spaiHint}>
              Set the credits to deduct per shipment for AI address checks. Max cap is 2.0 credits/order (spec).
            </Text>
            <View style={styles.rateGrid}>
              <View style={[styles.rateCell, { borderColor: "#04785755" }]}>
                <View style={styles.rateCellHead}>
                  <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: "#047857" }} />
                  <Text style={[styles.rateCellLabel, { color: "#047857" }]}>Simple</Text>
                </View>
                <Text style={styles.rateCellHint}>short & clean</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-simple"
                    style={styles.rateCellInput}
                    value={aiCostSimple}
                    onChangeText={(t) => setAiCostSimple(t.replace(/[^\d.]/g, ""))}
                    keyboardType="decimal-pad"
                    placeholder="0.5"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>
              <View style={[styles.rateCell, { borderColor: "#B4530955" }]}>
                <View style={styles.rateCellHead}>
                  <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: "#B45309" }} />
                  <Text style={[styles.rateCellLabel, { color: "#B45309" }]}>Medium</Text>
                </View>
                <Text style={styles.rateCellHint}>some extra text</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-medium"
                    style={styles.rateCellInput}
                    value={aiCostMedium}
                    onChangeText={(t) => setAiCostMedium(t.replace(/[^\d.]/g, ""))}
                    keyboardType="decimal-pad"
                    placeholder="1.0"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>
              <View style={[styles.rateCell, { borderColor: "#B91C1C55" }]}>
                <View style={styles.rateCellHead}>
                  <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: "#B91C1C" }} />
                  <Text style={[styles.rateCellLabel, { color: "#B91C1C" }]}>Complex</Text>
                </View>
                <Text style={styles.rateCellHint}>long, messy</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-complex"
                    style={styles.rateCellInput}
                    value={aiCostComplex}
                    onChangeText={(t) => setAiCostComplex(t.replace(/[^\d.]/g, ""))}
                    keyboardType="decimal-pad"
                    placeholder="2.0"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>
            </View>
            <Text style={styles.rateNote}>
              ⚠️ Max per-order cap 2.0 — values above 2.0 are clamped server-side.
            </Text>
            <View style={styles.spaiActions}>
              <TouchableOpacity
                testID="rate-save-btn"
                disabled={rateCardSaving}
                onPress={saveRateCard}
                style={[styles.primaryBtn, rateCardSaving && { opacity: 0.6 }]}
              >
                {rateCardSaving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <PhIcon name="checkmark-circle" size={16} color="#fff" />
                    <Text style={styles.primaryBtnTxt}>Save rates</Text>
                  </>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                testID="rate-reset-btn"
                style={styles.secondaryBtn}
                onPress={resetRateCard}
              >
                <PhIcon name="refresh" size={16} color={colors.primary} />
                <Text style={styles.secondaryBtnTxt}>Defaults</Text>
              </TouchableOpacity>
            </View>
          </Section>
          ) : null}
          </>)}

          {section === "business" && (<>
          {/* Google Sheet */}
          {flagSheetImport ? (
          <Section title="Google Sheet (Orders source)" icon="logo-google">
            <Text style={[styles.hint, { marginTop: -2, marginBottom: 8, color: "#1E3A8A", fontWeight: "700" }]}>
              Your orders stay safe even if the app is uninstalled
            </Text>
            {/* Phase F1 — adjacent CSV/Excel import-mapping shortcut */}
            <TouchableOpacity
              testID="open-file-import-mapping"
              onPress={() => router.push("/file-import?mode=settings" as any)}
              style={{
                flexDirection: "row", alignItems: "center", gap: 10,
                backgroundColor: "#ECFDF5", borderColor: "#A7F3D0", borderWidth: 1,
                borderRadius: 10, padding: 12, marginBottom: 12,
              }}
            >
              <PhIcon name="cloud-upload" size={18} color="#047857" />
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: "700", color: "#065F46", fontSize: 13 }}>
                  CSV / Excel Import Mapping
                </Text>
                <Text style={{ fontSize: 11, color: "#10B981", marginTop: 1 }}>
                  Map columns once · auto-applies on next upload
                </Text>
              </View>
              <PhIcon name="chevron-forward" size={16} color="#10B981" />
            </TouchableOpacity>
            {/* Phase F2.2 — Webhook ingest config (real-time JSON-payload
                imports). Same mapping mental model as CSV/Excel above. */}
            <TouchableOpacity
              testID="open-webhook-config"
              onPress={() => router.push("/webhook-config" as any)}
              style={{
                flexDirection: "row", alignItems: "center", gap: 10,
                backgroundColor: "#FFF7EE", borderColor: "#FED7AA", borderWidth: 1,
                borderRadius: 10, padding: 12, marginBottom: 12,
              }}
            >
              <PhIcon name="cloud-upload" size={18} color="#FF6B00" />
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: "700", color: "#9A3412", fontSize: 13 }}>
                  Webhook Ingest (Live JSON)
                </Text>
                <Text style={{ fontSize: 11, color: "#FF6B00", marginTop: 1 }}>
                  POST orders from Shopify, Zapier, custom scripts
                </Text>
              </View>
              <PhIcon name="chevron-forward" size={16} color="#FF6B00" />
            </TouchableOpacity>
            {/* Phase F3.3 — Abandoned Carts. Surfaces cart events
                ingested via webhooks with event_type=abandoned_order. */}
            <TouchableOpacity
              testID="open-abandoned-carts"
              onPress={() => router.push("/abandoned-carts" as any)}
              style={{
                flexDirection: "row", alignItems: "center", gap: 10,
                backgroundColor: "#FFF7EE", borderColor: "#FED7AA", borderWidth: 1,
                borderRadius: 10, padding: 12, marginBottom: 12,
              }}
            >
              <PhIcon name="cart" size={18} color="#9A3412" />
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: "700", color: "#9A3412", fontSize: 13 }}>
                  Abandoned Carts
                </Text>
                <Text style={{ fontSize: 11, color: "#FF6B00", marginTop: 1 }}>
                  Recover carts captured via webhooks → Pending Order
                </Text>
              </View>
              <PhIcon name="chevron-forward" size={16} color="#FF6B00" />
            </TouchableOpacity>
            {/* Phase F3.3 — Customers. Surfaces customer_created /
                customer_updated webhook events. */}
            <TouchableOpacity
              testID="open-customers"
              onPress={() => router.push("/customers" as any)}
              style={{
                flexDirection: "row", alignItems: "center", gap: 10,
                backgroundColor: "#EEF2FF", borderColor: "#C7D2FE", borderWidth: 1,
                borderRadius: 10, padding: 12, marginBottom: 12,
              }}
            >
              <PhIcon name="people" size={18} color="#3730A3" />
              <View style={{ flex: 1 }}>
                <Text style={{ fontWeight: "700", color: "#3730A3", fontSize: 13 }}>
                  Customers
                </Text>
                <Text style={{ fontSize: 11, color: "#4F46E5", marginTop: 1 }}>
                  Customer profiles synced from your store via webhooks
                </Text>
              </View>
              <PhIcon name="chevron-forward" size={16} color="#4F46E5" />
            </TouchableOpacity>
            {sheetStatus !== "connected" && saEmail ? (
              <View style={styles.saShareBox} testID="sa-share-box">
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                  <PhIcon name="shield-checkmark" size={14} color="#1E40AF" />
                  <Text style={styles.saShareTitle}>
                    Recommended: Share privately with our service account
                  </Text>
                </View>
                <Text style={styles.saShareSub}>
                  Your sheet stays private — only this email can read it.
                  Open your Google Sheet → Share → paste this email →
                  pick "Editor" → Send.
                </Text>
                <View style={styles.saEmailRow}>
                  <Text style={styles.saEmailTxt} numberOfLines={1} selectable>
                    {saEmail}
                  </Text>
                  <TouchableOpacity
                    testID="sa-email-copy"
                    onPress={async () => {
                      try {
                        await Clipboard.setStringAsync(saEmail);
                        Alert.alert("Copied", "Service account email copied. Paste it in your Sheet's Share dialog.");
                      } catch {
                        Alert.alert("Copy failed", "Long-press the email to select and copy manually.");
                      }
                    }}
                    style={styles.saEmailCopyBtn}
                  >
                    <PhIcon name="copy-outline" size={14} color="#1E40AF" />
                    <Text style={styles.saEmailCopyTxt}>Copy</Text>
                  </TouchableOpacity>
                </View>
                <Text style={styles.saShareAlt}>
                  Prefer the old way? You can also set the sheet to "Anyone
                  with the link → Viewer" — works too, but anyone with the
                  link can see customer data.
                </Text>
              </View>
            ) : null}

            <View style={styles.sampleBox} testID="sheet-sample-box">
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <PhIcon name="bulb-outline" size={14} color={colors.primary} />
                <Text style={styles.sampleTitle}>First time? Use the sample template</Text>
              </View>
              <Text style={styles.sampleText}>
                Download the sample CSV → open Google Sheets → File → Import → upload CSV → Replace current sheet. Then share with the service account above (recommended) or "Anyone with the link → Viewer".
              </Text>
              <Text style={styles.sampleCols}>
                Columns: Timestamp · Order ID · Name · Phone · Address · City · State · Pincode · Item · Amount · Payment Mode
              </Text>
              <TouchableOpacity
                testID="sheet-download-sample"
                style={styles.sampleBtn}
                onPress={() =>
                  Linking.openURL(
                    `${process.env.EXPO_PUBLIC_BACKEND_URL}/api/sheets/sample-template`
                  )
                }
              >
                <PhIcon name="download-outline" size={16} color="#fff" />
                <Text style={styles.sampleBtnText}>Download Sample CSV</Text>
              </TouchableOpacity>
            </View>

            {sheetStatus === "connected" && !preview ? (
              <View>
                <View style={styles.connectedBox}>
                  <PhIcon name="checkmark-circle" size={18} color={colors.successText} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.connectedTitle}>Connected</Text>
                    <Text style={styles.connectedSub} numberOfLines={1}>
                      Sheet: {connectedSheetId}
                    </Text>
                    <Text style={styles.connectedSub}>
                      {connectedHeaders.length} columns mapped
                    </Text>
                  </View>
                </View>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                  <TouchableOpacity
                    testID="sheet-open-btn"
                    style={[styles.outlineBtn, { flex: 1 }]}
                    onPress={() => sheetUrl && Linking.openURL(sheetUrl)}
                  >
                    <PhIcon name="open-outline" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Open Sheet</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-remap-btn"
                    style={[styles.outlineBtn, { flex: 1 }]}
                    onPress={fetchPreview}
                  >
                    <PhIcon name="refresh" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Refresh / Re-map</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-disconnect-btn"
                    style={[styles.outlineBtn, { borderColor: colors.dangerText }]}
                    onPress={disconnectSheet}
                  >
                    <PhIcon name="trash-outline" size={16} color={colors.dangerText} />
                  </TouchableOpacity>
                </View>

                {/* Phase-C: Sync rows from Master Sheet (filtered by user_id). */}
                {flagRestoreMyOrders && (
                <TouchableOpacity
                  testID="sheet-sync-from-master-btn"
                  style={[
                    styles.outlineBtn,
                    {
                      marginTop: 8,
                      backgroundColor: "#F5F3FF",
                      borderColor: "#DDD6FE",
                    },
                  ]}
                  onPress={async () => {
                    Alert.alert(
                      "Restore My Orders",
                      "How would you like to restore?\n\n" +
                      "• Full Restore: All orders are reloaded from scratch — existing data will be replaced.\n\n" +
                      "• Add Missing Orders: Only missing orders are added — existing data stays safe.",
                      [
                        { text: "Cancel", style: "cancel" },
                        {
                          text: "Add Missing Orders",
                          onPress: async () => {
                            try {
                              const r = await Api.syncFromMaster(false);
                              Alert.alert(
                                "Restore My Orders",
                                `${r.rows_synced} orders successfully restored ✓`,
                              );
                            } catch (e: any) {
                              Alert.alert(
                                "Restore failed",
                                e?.response?.data?.detail || e?.message || "Try again.",
                              );
                            }
                          },
                        },
                        {
                          text: "Full Restore",
                          style: "destructive",
                          onPress: async () => {
                            try {
                              const r = await Api.syncFromMaster(true);
                              Alert.alert(
                                "Restore My Orders",
                                `${r.rows_synced} orders successfully restored ✓`,
                              );
                            } catch (e: any) {
                              Alert.alert(
                                "Restore failed",
                                e?.response?.data?.detail || e?.message || "Try again.",
                              );
                            }
                          },
                        },
                      ],
                    );
                  }}
                >
                  <PhIcon name="cloud-download-outline" size={16} color="#7C3AED" />
                  <Text style={[styles.outlineBtnText, { color: "#7C3AED", fontWeight: "800" }]}>
                    Restore My Orders
                  </Text>
                </TouchableOpacity>
                )}
              </View>
            ) : (
              <>
                <Text style={styles.hint}>
                  Two ways to share:{"\n"}
                  • <Text style={{ fontWeight: "800" }}>Private (recommended):</Text>{" "}
                  share your sheet with the service-account email above (Editor).{"\n"}
                  • <Text style={{ fontWeight: "800" }}>Public:</Text>{" "}
                  set "Anyone with the link → Viewer".{"\n"}
                  Then paste your sheet URL below.
                </Text>
                <TextInput
                  testID="sheet-url-input"
                  value={sheetUrl}
                  onChangeText={setSheetUrl}
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  placeholderTextColor="#9CA3AF"
                  style={[styles.input, { marginTop: 8 }]}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <TouchableOpacity
                  testID="sheet-preview-btn"
                  onPress={fetchPreview}
                  style={[styles.saveBtn, { marginTop: 10 }]}
                  disabled={sheetStatus === "loading"}
                >
                  {sheetStatus === "loading" ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <>
                      <PhIcon name="download" size={18} color="#fff" />
                      <Text style={styles.saveBtnText}>Fetch & Preview</Text>
                    </>
                  )}
                </TouchableOpacity>
              </>
            )}

            {preview && (
              <View style={{ marginTop: 16 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <Text style={styles.subTitle}>
                    {preview.total_rows} rows · {preview.headers.length} columns
                  </Text>
                  {accessMethod === "service_account" ? (
                    <View style={styles.accessBadgePrivate}>
                      <PhIcon name="lock-closed" size={10} color="#065F46" />
                      <Text style={styles.accessBadgePrivateTxt}>Private · Service Account</Text>
                    </View>
                  ) : accessMethod === "public_csv" ? (
                    <View style={styles.accessBadgePublic}>
                      <PhIcon name="globe-outline" size={10} color="#92400E" />
                      <Text style={styles.accessBadgePublicTxt}>Public link</Text>
                    </View>
                  ) : null}
                </View>
                <Text style={[styles.hint, { marginTop: 4 }]}>
                  Map each field to a column from your sheet:
                </Text>
                {SHEET_FIELDS.map((f) => (
                  <View key={f.key} style={styles.mapRow} testID={`map-row-${f.key}`}>
                    <Text style={styles.mapLabel}>{f.label}</Text>
                    <TouchableOpacity
                      testID={`map-pick-${f.key}`}
                      style={styles.mapPick}
                      onPress={() => setPickerForField(f.key)}
                    >
                      <Text
                        style={[
                          styles.mapPickText,
                          !mapping[f.key] && { color: "#9CA3AF" },
                        ]}
                        numberOfLines={1}
                      >
                        {mapping[f.key] || "— pick column —"}
                      </Text>
                      <PhIcon name="chevron-down" size={16} color={colors.textMuted} />
                    </TouchableOpacity>
                  </View>
                ))}
                <TouchableOpacity
                  testID="sheet-save-mapping-btn"
                  onPress={saveSheet}
                  style={[styles.saveBtn, { marginTop: 12 }]}
                >
                  <PhIcon name="save" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>Save Mapping & Connect</Text>
                </TouchableOpacity>
                {/* Write headers to user's sheet — based on current mapping
                    + any custom fields defined. Existing non-blank headers
                    are preserved. */}
                <TouchableOpacity
                  testID="sheet-write-headers-btn"
                  onPress={async () => {
                    try {
                      const r = await Api.syncSheetHeaders(false);
                      const lines = [
                        `Wrote ${r.written_count} header${
                          r.written_count === 1 ? "" : "s"
                        }.`,
                      ];
                      if (r.skipped_count) {
                        lines.push(
                          `Skipped ${r.skipped_count} cell${
                            r.skipped_count === 1 ? "" : "s"
                          } (already had a value).`,
                        );
                      }
                      Alert.alert("✅ Headers synced", lines.join("\n"));
                    } catch (e: any) {
                      Alert.alert(
                        "Error",
                        e?.response?.data?.detail || e?.message || "Failed",
                      );
                    }
                  }}
                  style={[
                    styles.saveBtn,
                    { marginTop: 8, backgroundColor: "#10B981" },
                  ]}
                >
                  <PhIcon name="cloud-upload-outline" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>
                    Write Headers to My Sheet
                  </Text>
                </TouchableOpacity>
                <Text style={styles.toggleSub}>
                  Writes your mapped field names + custom fields into row 1 of
                  your Google Sheet. Only fills blank cells (your existing
                  header wording is preserved).
                </Text>
                <TouchableOpacity
                  testID="sheet-custom-fields-btn"
                  onPress={() => router.push("/custom-fields")}
                  style={[
                    styles.saveBtn,
                    { marginTop: 8, backgroundColor: "#6366F1" },
                  ]}
                >
                  <PhIcon name="layers-outline" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>Manage Custom Fields</Text>
                </TouchableOpacity>
              </View>
            )}
          </Section>
          ) : null}

          {/* Field Requirements — standalone, always-visible Section.
              Applies to BOTH Smart Paste + New Shipment form and is
              independent of whether the Google Sheet column-preview
              is toggled open. */}
          <Section title="Field Requirements" icon="checkmark-done-outline">
            <Text style={styles.toggleSub}>
              Choose which fields are required (mandatory) when saving a
              shipment via Smart Paste or the New Shipment form. Toggle
              any field ON/OFF to control validation everywhere.
            </Text>
            <TouchableOpacity
              testID="sheet-field-requirements-btn"
              onPress={() => router.push("/field-requirements")}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#0EA5E9" },
              ]}
            >
              <PhIcon name="checkmark-done-outline" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Field Requirements</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase-16: Save Contact settings. Owns the per-user prefs
              that drive how the shipment card's "Save Contact" button
              builds the final native-contact payload. Categories and
              product → category mapping are user-owned (not hardcoded).*/}
          <Section title="Save Contact">
            <Text style={styles.toggleSub}>
              Customize how the "Save Contact" button builds the contact
              name, categories and notes. Add your own category codes
              (e.g. KSS, KOC) and product → category rules.
            </Text>
            <TouchableOpacity
              testID="settings-hub-contact-save"
              onPress={() => router.push("/settings/contact-save" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#7C3AED" },
              ]}
            >
              <PhIcon name="person-add-outline" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Save Contact Settings</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase-12 — WhatsApp Templates editor (4 types × 3 langs).
              Lives separately from the legacy single-template editor in
              the WhatsApp section so users can opt into the multi-message
              flow incrementally. */}
          <Section title="WhatsApp Message Templates" icon="logo-whatsapp">
            <Text style={styles.toggleSub}>
              Customize all 4 outgoing WhatsApp messages — Shipment Booked,
              Dispatch Confirmation, Delivery Confirmation and Delivery
              Thanks — in Gujarati, Hindi and English. Pick a default
              language once and the app remembers it.
            </Text>
            <TouchableOpacity
              testID="settings-hub-whatsapp-templates"
              onPress={() => router.push("/settings/whatsapp-templates" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#16A34A" },
              ]}
            >
              <PhIcon name="logo-whatsapp" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open WhatsApp Templates</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase A — Team Members & Permissions
              Lets the shop-owner add staff who'll receive SLA alert
              WhatsApp messages now and (Phase C) get their own login
              with the permissions you grant.
              Hidden for team-member sessions — only owners manage staff. */}
          {!isTeamMember && (
          <Section title="Team Members & Permissions" icon="people-outline">
            <Text style={styles.toggleSub}>
              Add staff with their name, phone & role. Assign per-feature
              permissions — they'll only see what you allow when they get
              their own login.{"\n"}
              Free quota by plan: Gold = 1, Platinum = 2. Beyond that, buy
              extra slots from your wallet or via Razorpay.
            </Text>
            <TouchableOpacity
              testID="settings-hub-team-members"
              onPress={() => router.push("/settings/team-members" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#7C3AED" },
              ]}
            >
              <PhIcon name="people" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Manage Team Members</Text>
            </TouchableOpacity>
          </Section>
          )}

          {/* Phase 2.5 — Reports Hub
              Single entry point to 5 business reports with custom
              date ranges and Excel download support. */}
          <Section title="Reports & Analytics" icon="bar-chart-outline">
            <Text style={styles.toggleSub}>
              5 business reports: Courier Billing, Return Analysis,
              Weight-wise Breakup, Partner Comparison, COD Reconciliation.
              Custom date ranges + Excel download included.
            </Text>
            <TouchableOpacity
              testID="settings-hub-reports"
              onPress={() => router.push("/reports" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#0EA5E9" },
              ]}
            >
              <PhIcon name="bar-chart" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Reports Hub</Text>
            </TouchableOpacity>
          </Section>


          {/* Phase F4 — Courier Rules editor extracted into its own
              standalone /courier-rules screen as part of the legacy
              dispatch/delivery cleanup. The bulk Delivery Check-in
              flow still reads these rules. */}
          <Section title="Courier Delivery Rules" icon="time-outline">
            <Text style={styles.toggleSub}>
              Set how many days each courier typically takes to deliver.
              Parcels are auto-flagged for delivery confirmation once
              they cross this per-courier threshold.
            </Text>
            <TouchableOpacity
              testID="settings-hub-courier-rules"
              onPress={() => router.push("/courier-rules" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#F97316" },
              ]}
            >
              <PhIcon name="time-outline" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Courier Rules</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase G6 — Notification preferences shortcut. */}
          <Section title="Push Notifications" icon="notifications-outline">
            <Text style={styles.toggleSub}>
              Choose which alerts you want on your phone — SLA breaches,
              daily WhatsApp limit warnings, morning ops digest, plan
              expiry, and more. Send a test notification to confirm
              everything works.
            </Text>
            <TouchableOpacity
              testID="settings-hub-notification-prefs"
              onPress={() => router.push("/notification-prefs" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#1F4FBF" },
              ]}
            >
              <PhIcon name="notifications-outline" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Notification Settings</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase H — Google Sheet auto-sync shortcut. */}
          <Section title="Google Sheet Auto-Sync" icon="cloud-upload-outline">
            <Text style={styles.toggleSub}>
              Mirror every shipment lifecycle event (create, status
              change, delete) to your Google Sheet automatically. Toggle
              the three triggers on/off, see how many rows are synced,
              and run a manual sync for stragglers.
            </Text>
            <TouchableOpacity
              testID="settings-hub-sheet-sync"
              onPress={() => router.push("/sheet-sync" as any)}
              style={[
                styles.saveBtn,
                { marginTop: 12, backgroundColor: "#10B981" },
              ]}
            >
              <PhIcon name="cloud-upload-outline" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Open Sheet Sync</Text>
            </TouchableOpacity>
          </Section>

          {/* Phase I — Admin analytics shortcut (admins only). */}
          {(user as any)?.is_admin && (
            <Section title="📈 Admin Analytics" icon="stats-chart-outline">
              <Text style={styles.toggleSub}>
                Live KPIs across all users — total shipments, top
                couriers, top users, SLA health, sheet-sync status,
                and 30-day shipment trend chart with date-range
                filters.
              </Text>
              <TouchableOpacity
                testID="settings-hub-analytics"
                onPress={() => router.push("/admin/analytics" as any)}
                style={[
                  styles.saveBtn,
                  { marginTop: 12, backgroundColor: "#9333EA" },
                ]}
              >
                <PhIcon name="stats-chart-outline" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>Open Analytics</Text>
              </TouchableOpacity>
            </Section>
          )}


          {/* Column picker modal */}
          <Modal
            visible={!!pickerForField}
            transparent
            animationType="fade"
            onRequestClose={() => setPickerForField(null)}
          >
            <TouchableOpacity
              style={styles.modalBackdrop}
              activeOpacity={1}
              onPress={() => setPickerForField(null)}
            >
              <View style={styles.pickerCard}>
                <Text style={styles.pickerTitle}>
                  Pick column for{" "}
                  {SHEET_FIELDS.find((f) => f.key === pickerForField)?.label}
                </Text>
                <ScrollView style={{ maxHeight: 320 }}>
                  <TouchableOpacity
                    style={styles.pickerItem}
                    onPress={() => {
                      if (pickerForField) {
                        setMapping((m) => {
                          const copy = { ...m };
                          delete copy[pickerForField];
                          return copy;
                        });
                      }
                      setPickerForField(null);
                    }}
                  >
                    <Text style={[styles.pickerItemText, { color: colors.textMuted }]}>
                      — None —
                    </Text>
                  </TouchableOpacity>
                  {(preview?.headers || []).map((h, idx) => (
                    <TouchableOpacity
                      key={`${idx}-${h}`}
                      style={styles.pickerItem}
                      onPress={() => {
                        if (pickerForField) {
                          setMapping((m) => ({ ...m, [pickerForField]: h }));
                        }
                        setPickerForField(null);
                      }}
                    >
                      <Text style={styles.pickerItemText}>{h || `(empty col ${idx + 1})`}</Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              </View>
            </TouchableOpacity>
          </Modal>
          </>)}

          {section === "business" && (<>
          {/* Brand */}
          <Section title="Brand on Labels" icon="pricetag-outline">
            <Text style={styles.hint}>
              Shown at the top of every printed label. Upload a logo OR type a business name — and pick which one to show.
            </Text>
            <Field label="Brand / Business Name">
              <TextInput
                testID="brand-name-input"
                value={brandName}
                onChangeText={setBrandName}
                placeholder="e.g. Kisan Sarthi Organic"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Logo (optional)">
              <View style={styles.segmentRow}>
                <TouchableOpacity
                  testID="logo-shape-square"
                  style={[styles.segmentBtn, logoShape === "square" && styles.segmentBtnActive]}
                  onPress={() => setLogoShape("square")}
                >
                  <PhIcon name="square-outline" size={14} color={logoShape === "square" ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, logoShape === "square" && styles.segmentTextActive]}>
                    Square (1:1)
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="logo-shape-wide"
                  style={[styles.segmentBtn, logoShape === "wide" && styles.segmentBtnActive]}
                  onPress={() => setLogoShape("wide")}
                >
                  <PhIcon name="remove-outline" size={18} color={logoShape === "wide" ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, logoShape === "wide" && styles.segmentTextActive]}>
                    Wide / Banner (4:1)
                  </Text>
                </TouchableOpacity>
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 }}>
                {brandLogo ? (
                  <Image
                    source={{ uri: brandLogo.startsWith("data:") ? brandLogo : `data:image/png;base64,${brandLogo}` }}
                    style={
                      logoShape === "wide"
                        ? { width: 160, height: 40, borderRadius: 6, backgroundColor: "#F3F4F6" }
                        : { width: 70, height: 70, borderRadius: 10, backgroundColor: "#F3F4F6" }
                    }
                    resizeMode="contain"
                  />
                ) : (
                  <View
                    style={
                      logoShape === "wide"
                        ? { width: 160, height: 40, borderRadius: 6, backgroundColor: "#F3F4F6", alignItems: "center", justifyContent: "center" }
                        : { width: 70, height: 70, borderRadius: 10, backgroundColor: "#F3F4F6", alignItems: "center", justifyContent: "center" }
                    }
                  >
                    <PhIcon name="image-outline" size={28} color={colors.textMuted} />
                  </View>
                )}
                <View style={{ flex: 1, gap: 6 }}>
                  <TouchableOpacity
                    testID="brand-logo-upload"
                    style={[styles.smallBtn, { backgroundColor: colors.primary }]}
                    onPress={async () => {
                      try {
                        const res = await ImagePicker.launchImageLibraryAsync({
                          mediaTypes: ImagePicker.MediaTypeOptions.Images,
                          base64: true,
                          quality: 0.85,
                          allowsEditing: true,
                          aspect: logoShape === "wide" ? [4, 1] : [1, 1],
                        });
                        if (!res.canceled && res.assets?.[0]?.base64) {
                          setBrandLogo(`data:image/png;base64,${res.assets[0].base64}`);
                        }
                      } catch (e: any) {
                        Alert.alert("Upload failed", e?.message || "Could not pick image");
                      }
                    }}
                  >
                    <PhIcon name="cloud-upload-outline" size={14} color="#fff" />
                    <Text style={styles.smallBtnText}>
                      {brandLogo ? "Change Logo" : `Upload ${logoShape === "wide" ? "Wide" : "Square"} Logo`}
                    </Text>
                  </TouchableOpacity>
                  {!!brandLogo && (
                    <TouchableOpacity
                      testID="brand-logo-remove"
                      style={[styles.smallBtn, { backgroundColor: "#EF4444" }]}
                      onPress={() => setBrandLogo("")}
                    >
                      <PhIcon name="trash-outline" size={14} color="#fff" />
                      <Text style={styles.smallBtnText}>Remove</Text>
                    </TouchableOpacity>
                  )}
                </View>
              </View>
              <Text style={styles.hint}>
                💡 Tip: Pick the shape FIRST, then upload. Cropper will guide you to the right aspect.
              </Text>
            </Field>
            <Field label="Show on Label">
              <View style={styles.segmentRow}>
                <TouchableOpacity
                  testID="prefer-logo-on"
                  style={[styles.segmentBtn, preferLogo && styles.segmentBtnActive]}
                  onPress={() => setPreferLogo(true)}
                  disabled={!brandLogo}
                >
                  <PhIcon name="image" size={14} color={preferLogo ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, preferLogo && styles.segmentTextActive]}>
                    Logo {!brandLogo && "(upload first)"}
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="prefer-logo-off"
                  style={[styles.segmentBtn, !preferLogo && styles.segmentBtnActive]}
                  onPress={() => setPreferLogo(false)}
                >
                  <PhIcon name="text" size={14} color={!preferLogo ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, !preferLogo && styles.segmentTextActive]}>
                    Brand Name
                  </Text>
                </TouchableOpacity>
              </View>
              <Text style={styles.hint}>
                {preferLogo && brandLogo ? "🖼 Labels will show your logo" : "🔤 Labels will show the brand name"}
              </Text>
            </Field>
          </Section>
          </>)}

          {section === "print" && (<>
          {/* Label Customization — field visibility toggles */}
          <Section title="Label Fields (Show / Hide)" icon="options-outline">
            <Text style={styles.hint}>
              Toggle which optional fields appear on the printed label.
              Core fields (Name, Address, Barcode, PAID/COD) always show.
            </Text>
            {/* Brand Tagline (prints under the sender brand name in the label footer) */}
            <View style={{ marginBottom: 6 }}>
              <Text style={styles.hint}>
                Brand Tagline — prints once on every label, under your brand name in the
                "From" footer area (e.g. "Har Pal Prakruti Ke Sang"). Set once, applies to all.
              </Text>
              <TextInput
                testID="shipment-tagline-input"
                value={shipmentTagline}
                onChangeText={setShipmentTagline}
                placeholder="e.g. Har Pal Prakruti Ke Sang"
                placeholderTextColor="#9CA3AF"
                style={[styles.input, { marginBottom: 6 }]}
              />
            </View>
            {([
              { key: "oid" as const, label: "Order ID (OID)" },
              { key: "dispatch_date" as const, label: "Dispatch Date (DD)" },
              { key: "weight" as const, label: "Weight" },
              { key: "item" as const, label: "Item Description" },
              { key: "phone" as const, label: "Customer Phone" },
              { key: "alt_phone" as const, label: "Alternative / Secondary Phone" },
              { key: "customer_id" as const, label: "Courier Customer ID" },
              { key: "token_info" as const, label: "Token / Advance Info (footer)" },
              { key: "box_dimensions" as const, label: "Box Dimensions" },
              { key: "shipment_notes" as const, label: "Shipment Notes" },
            ]).map((f) => (
              <View key={f.key} style={styles.toggleRow}>
                <Text style={styles.toggleLabel}>{f.label}</Text>
                <Switch
                  testID={`label-toggle-${f.key}`}
                  value={!!(labelFields as any)[f.key]}
                  onValueChange={(v) => {
                    // Budget-gated: enabling "shipment_notes" must not exceed cap.
                    if (f.key === "shipment_notes" && v) {
                      const used = computeBudgetUsed({
                        tagline: shipmentTagline,
                        shipmentNotesOn: false, // turning ON now
                        customFields,
                      });
                      if (used + 1 > CONTENT_BUDGET_CAP) {
                        Alert.alert(
                          "Label budget full",
                          `You can have at most ${CONTENT_BUDGET_CAP} content extras on a label (Tagline + Notes + enabled Custom Fields) to prevent overlap. Disable one of them first, then try again.`
                        );
                        return;
                      }
                    }
                    setLabelFields({ ...labelFields, [f.key]: v });
                  }}
                  trackColor={{ false: "#E5E7EB", true: colors.primary }}
                  thumbColor="#fff"
                />
              </View>
            ))}
          </Section>

          {/* ==================== Custom Label Fields (Phase B) ==================== */}
          <Section title="Custom Label Fields (Advanced)" icon="add-circle-outline">
            <Text style={styles.hint}>
              Add your own fields that will print on every label — e.g. GST No., FSSAI, a
              special offer line, an alternate contact, or any business-specific info.
              Pick exactly where each field appears on the label. Up to 6 custom fields.
            </Text>

            {/* -------- Content Budget indicator --------
                Shows how much label space is currently "used" by:
                tagline + shipment-notes toggle + enabled custom fields.
                Hard cap = CONTENT_BUDGET_CAP (prevents print overlap).
                Plan-gated via `label_content_budget` flag. */}
            {flagLabelContentBudget && (() => {
              const used = computeBudgetUsed({
                tagline: shipmentTagline,
                shipmentNotesOn: !!labelFields.shipment_notes,
                customFields,
              });
              const full = used >= CONTENT_BUDGET_CAP;
              const parts: string[] = [];
              if (shipmentTagline.trim()) parts.push("Tagline");
              if (labelFields.shipment_notes) parts.push("Notes");
              const cfOn = customFields.filter((c) => c.enabled).length;
              if (cfOn > 0) parts.push(`${cfOn} custom`);
              return (
                <View
                  style={[
                    styles.budgetBox,
                    full ? styles.budgetBoxFull : styles.budgetBoxOk,
                  ]}
                  testID="content-budget-indicator"
                >
                  <View style={styles.budgetHeader}>
                    <PhIcon
                      name={full ? "warning-outline" : "checkmark-circle-outline"}
                      size={16}
                      color={full ? "#B45309" : "#047857"}
                    />
                    <Text
                      style={[
                        styles.budgetTitle,
                        { color: full ? "#B45309" : "#047857" },
                      ]}
                    >
                      Label Content Budget · {used}/{CONTENT_BUDGET_CAP}
                    </Text>
                  </View>
                  <View style={styles.budgetDots}>
                    {Array.from({ length: CONTENT_BUDGET_CAP }).map((_, i) => (
                      <View
                        key={i}
                        style={[
                          styles.budgetDot,
                          i < used
                            ? full
                              ? styles.budgetDotFull
                              : styles.budgetDotOn
                            : styles.budgetDotOff,
                        ]}
                      />
                    ))}
                  </View>
                  <Text style={styles.budgetHelp}>
                    {parts.length > 0
                      ? `Active: ${parts.join(" · ")}`
                      : "Nothing extra enabled yet."}
                    {"\n"}
                    Max {CONTENT_BUDGET_CAP} of {`{Tagline, Notes, Custom Fields}`} can be
                    active at once — prevents print overlap on labels with long
                    addresses.
                  </Text>
                </View>
              );
            })()}

            {customFields.length === 0 && (
              <View style={styles.emptyCf}>
                <PhIcon name="layers-outline" size={28} color="#9CA3AF" />
                <Text style={styles.emptyCfText}>No custom fields yet</Text>
                <Text style={styles.emptyCfSub}>
                  Tap "+ Add Custom Field" below to create one.
                </Text>
              </View>
            )}

            {customFields.map((cf, idx) => (
              <View key={cf.id || `cf-${idx}`} style={styles.cfCard} testID={`cf-card-${idx}`}>
                <View style={styles.cfHeader}>
                  <Text style={styles.cfIndex}>#{idx + 1}</Text>
                  <Switch
                    testID={`cf-enabled-${idx}`}
                    value={cf.enabled}
                    onValueChange={(v) => {
                      // Budget-gated: can't enable if it would exceed cap.
                      if (v) {
                        // Budget WITHOUT this field (since we're flipping it ON).
                        const others = customFields.map((x, i) =>
                          i === idx ? { ...x, enabled: false } : x
                        );
                        const used = computeBudgetUsed({
                          tagline: shipmentTagline,
                          shipmentNotesOn: !!labelFields.shipment_notes,
                          customFields: others,
                        });
                        if (used + 1 > CONTENT_BUDGET_CAP) {
                          Alert.alert(
                            "Label budget full",
                            `You can have at most ${CONTENT_BUDGET_CAP} content extras on a label (Tagline + Notes + enabled Custom Fields). Disable something else first, then enable this.`
                          );
                          return;
                        }
                      }
                      const next = [...customFields];
                      next[idx] = { ...cf, enabled: v };
                      setCustomFields(next);
                    }}
                    trackColor={{ false: "#E5E7EB", true: colors.primary }}
                    thumbColor="#fff"
                  />
                  <View style={{ flex: 1 }} />
                  <TouchableOpacity
                    testID={`cf-delete-${idx}`}
                    onPress={() =>
                      Alert.alert(
                        "Delete custom field?",
                        `"${cf.label || cf.value || "Untitled"}" will be removed from all labels.`,
                        [
                          { text: "Keep", style: "cancel" },
                          {
                            text: "Delete",
                            style: "destructive",
                            onPress: () =>
                              setCustomFields(customFields.filter((_, i) => i !== idx)),
                          },
                        ]
                      )
                    }
                    style={styles.cfDeleteBtn}
                    hitSlop={8}
                  >
                    <PhIcon name="trash-outline" size={18} color="#B91C1C" />
                  </TouchableOpacity>
                </View>

                <View style={styles.cfRow2}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.cfLabel}>Label (prefix)</Text>
                    <TextInput
                      testID={`cf-label-input-${idx}`}
                      value={cf.label}
                      onChangeText={(t) => {
                        const next = [...customFields];
                        next[idx] = { ...cf, label: t };
                        setCustomFields(next);
                      }}
                      placeholder="e.g. GST:"
                      placeholderTextColor="#9CA3AF"
                      style={styles.input}
                    />
                  </View>
                  <View style={{ flex: 1.3 }}>
                    <Text style={styles.cfLabel}>
                      {(cf as any).source === "shipment" ? "Placeholder (hint)" : "Value"}
                    </Text>
                    <TextInput
                      testID={`cf-value-input-${idx}`}
                      value={(cf as any).source === "shipment" ? ((cf as any).placeholder || "") : cf.value}
                      onChangeText={(t) => {
                        const next = [...customFields];
                        if ((cf as any).source === "shipment") {
                          next[idx] = { ...cf, placeholder: t } as any;
                        } else {
                          next[idx] = { ...cf, value: t };
                        }
                        setCustomFields(next);
                      }}
                      placeholder={(cf as any).source === "shipment"
                        ? "e.g. GST number of customer"
                        : "e.g. 24ABCDE1234F1Z5"}
                      placeholderTextColor="#9CA3AF"
                      style={styles.input}
                    />
                  </View>
                </View>

                {/* Source type picker: Static vs Per-Shipment */}
                <Text style={styles.cfLabel}>Field Type</Text>
                <View style={{ flexDirection: "row", gap: 8, marginBottom: 6 }}>
                  {([
                    { k: "static", t: "🔒  Static", sub: "Same on every label" },
                    { k: "shipment", t: "🔄  Per-Shipment", sub: "Type value for each order" },
                  ] as const).map((opt) => {
                    const active = ((cf as any).source || "static") === opt.k;
                    return (
                      <TouchableOpacity
                        key={opt.k}
                        testID={`cf-src-${idx}-${opt.k}`}
                        onPress={() => {
                          const next = [...customFields];
                          next[idx] = { ...cf, source: opt.k } as any;
                          setCustomFields(next);
                        }}
                        style={[styles.cfSourceTile, active && styles.cfSourceTileActive]}
                      >
                        <Text style={[styles.cfSourceTitle, active && { color: "#fff" }]}>
                          {opt.t}
                        </Text>
                        <Text style={[styles.cfSourceSub, active && { color: "rgba(255,255,255,0.85)" }]} numberOfLines={1}>
                          {opt.sub}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>

                {/* Sheet column mapping (only for per-shipment fields) */}
                {(cf as any).source === "shipment" && (
                  <View>
                    <Text style={styles.cfLabel}>
                      Google Sheet column (optional)
                    </Text>
                    <TextInput
                      testID={`cf-sheet-col-${idx}`}
                      value={(cf as any).sheet_column || ""}
                      onChangeText={(t) => {
                        const next = [...customFields];
                        next[idx] = { ...cf, sheet_column: t } as any;
                        setCustomFields(next);
                      }}
                      placeholder="e.g. Customer GST"
                      placeholderTextColor="#9CA3AF"
                      style={styles.input}
                      autoCapitalize="none"
                    />
                    <Text style={[styles.hint, { marginTop: 2 }]}>
                      If set, Smart Paste will auto-fill this field from the matching Google Sheet column.
                    </Text>
                  </View>
                )}

                <Text style={styles.cfLabel}>Position on label</Text>
                <ScrollView
                  horizontal
                  showsHorizontalScrollIndicator={false}
                  contentContainerStyle={{ gap: 6, paddingRight: 16 }}
                >
                  {([
                    { k: "header_top",    t: "Top (above brand)" },
                    { k: "to_block",      t: "In DELIVER TO" },
                    { k: "notes_area",    t: "Notes area" },
                    { k: "meta_row",      t: "Wt / Box row" },
                    { k: "from_block",    t: "In FROM footer" },
                    { k: "footer_bottom", t: "Very bottom" },
                  ] as const).map((p) => {
                    const active = cf.position === p.k;
                    return (
                      <TouchableOpacity
                        key={p.k}
                        testID={`cf-pos-${idx}-${p.k}`}
                        onPress={() => {
                          const next = [...customFields];
                          next[idx] = { ...cf, position: p.k };
                          setCustomFields(next);
                        }}
                        style={[styles.cfPosPill, active && styles.cfPosPillActive]}
                      >
                        <Text style={[styles.cfPosPillText, active && { color: "#fff" }]}>
                          {p.t}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </ScrollView>

                <View style={styles.cfRow2}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.cfLabel}>Size</Text>
                    <View style={{ flexDirection: "row", gap: 6 }}>
                      {(["xs", "sm", "md"] as const).map((sz) => {
                        const active = (cf.size || "sm") === sz;
                        return (
                          <TouchableOpacity
                            key={sz}
                            testID={`cf-size-${idx}-${sz}`}
                            onPress={() => {
                              const next = [...customFields];
                              next[idx] = { ...cf, size: sz };
                              setCustomFields(next);
                            }}
                            style={[styles.cfSizePill, active && styles.cfPosPillActive]}
                          >
                            <Text style={[styles.cfPosPillText, active && { color: "#fff" }]}>
                              {sz.toUpperCase()}
                            </Text>
                          </TouchableOpacity>
                        );
                      })}
                    </View>
                  </View>
                  <View style={[styles.cfBoldRow, { flex: 1 }]}>
                    <Text style={styles.cfLabel}>Bold value</Text>
                    <Switch
                      testID={`cf-bold-${idx}`}
                      value={cf.bold !== false}
                      onValueChange={(v) => {
                        const next = [...customFields];
                        next[idx] = { ...cf, bold: v };
                        setCustomFields(next);
                      }}
                      trackColor={{ false: "#E5E7EB", true: colors.primary }}
                      thumbColor="#fff"
                    />
                  </View>
                </View>
              </View>
            ))}

            {customFields.length < 6 && (
              <TouchableOpacity
                testID="add-custom-field-btn"
                style={styles.addCfBtn}
                onPress={() => {
                  // If budget is already maxed, add the new field as DISABLED
                  // so we don't silently push the label into overlap territory.
                  const usedNow = computeBudgetUsed({
                    tagline: shipmentTagline,
                    shipmentNotesOn: !!labelFields.shipment_notes,
                    customFields,
                  });
                  const autoEnabled = usedNow < CONTENT_BUDGET_CAP;
                  setCustomFields([
                    ...customFields,
                    {
                      id: `cf_${Date.now().toString(36)}`,
                      label: "",
                      value: "",
                      position: "footer_bottom",
                      enabled: autoEnabled,
                      bold: true,
                      size: "sm",
                    },
                  ]);
                  if (!autoEnabled) {
                    Alert.alert(
                      "Field added (disabled)",
                      `You've reached the ${CONTENT_BUDGET_CAP}-item label budget. The new field was added but left OFF. Disable Tagline, Notes, or another Custom Field to turn it on.`
                    );
                  }
                }}
              >
                <PhIcon name="add-circle" size={18} color={colors.primary} />
                <Text style={styles.addCfBtnText}>
                  Add Custom Field ({customFields.length}/6)
                </Text>
              </TouchableOpacity>
            )}
          </Section>

          <TouchableOpacity testID="save-print-btn" style={styles.saveBtn} onPress={saveSender}>
            <PhIcon name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Print Settings</Text>
          </TouchableOpacity>
          </>)}

          {section === "business" && (<>
          {/* Sender */}
          <Section title="Sender / From Address" icon="business-outline">
            <Field label="Business / Sender Name">
              <TextInput
                testID="sender-name-input"
                value={sender.name}
                onChangeText={(v) => setSender({ ...sender, name: v })}
                placeholder="Your shop name"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Phone">
              <TextInput
                testID="sender-phone-input"
                value={sender.phone}
                onChangeText={(v) => setSender({ ...sender, phone: v })}
                placeholder="Contact number"
                placeholderTextColor="#9CA3AF"
                keyboardType="phone-pad"
                style={styles.input}
              />
            </Field>
            <Field label="Address Line 1">
              <TextInput
                testID="sender-addr1-input"
                value={sender.address_line1}
                onChangeText={(v) => setSender({ ...sender, address_line1: v })}
                placeholder="Shop / Street"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Address Line 2">
              <TextInput
                testID="sender-addr2-input"
                value={sender.address_line2}
                onChangeText={(v) => setSender({ ...sender, address_line2: v })}
                placeholder="Area / Landmark"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <Field label="City">
                  <TextInput
                    testID="sender-city-input"
                    value={sender.city}
                    onChangeText={(v) => setSender({ ...sender, city: v })}
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Pincode">
                  <TextInput
                    testID="sender-pincode-input"
                    value={sender.pincode}
                    onChangeText={(v) => setSender({ ...sender, pincode: v })}
                    keyboardType="number-pad"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            <Field label="State">
              <TextInput
                testID="sender-state-input"
                value={sender.state}
                onChangeText={(v) => setSender({ ...sender, state: v })}
                style={styles.input}
              />
            </Field>
            <View style={styles.switchRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.switchLabel}>Show contact on labels</Text>
                <Text style={styles.switchHint}>Toggle off to hide sender phone on printed labels</Text>
              </View>
              <Switch
                testID="show-contact-switch"
                value={sender.show_contact}
                onValueChange={(v) => setSender({ ...sender, show_contact: v })}
                trackColor={{ false: "#D1D5DB", true: colors.primary }}
                thumbColor="#fff"
              />
            </View>
          </Section>

          <TouchableOpacity testID="save-business-btn" style={styles.saveBtn} onPress={saveSender}>
            <PhIcon name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Business Settings</Text>
          </TouchableOpacity>
          </>)}

          {section === "whatsapp" && (<>
          {/* Templates */}
          <Section title="Customer Messages" icon="chatbubbles-outline">
            <View style={styles.infoBox} testID="messages-info-box">
              <PhIcon name="information-circle-outline" size={16} color={colors.primary} />
              <Text style={styles.infoText}>
                <Text style={{ fontWeight: "800" }}>How templates work:</Text> These messages are NOT auto-sent. When you tap the WhatsApp or Copy icons on a shipment, the app fills in the template with actual values (customer name, tracking ID, etc.) and opens WhatsApp or copies to clipboard. The ETA days field is just a placeholder number you can customize — it's not a scheduler.
              </Text>
            </View>
            <Field label="WhatsApp Template (for notification)">
              <Text style={styles.hint}>
                Use: {"{customer_name}"}, {"{order_id}"}, {"{courier}"}, {"{tracking_id}"}, {"{tracking_url}"}, {"{amount}"}, {"{eta_days}"}
              </Text>
              <View style={styles.presetRow}>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setTemplate(PRESETS.wa_gujarati)}
                  testID="preset-wa-gu"
                >
                  <PhIcon name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>Gujarati Professional</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setTemplate(PRESETS.wa_english)}
                  testID="preset-wa-en"
                >
                  <PhIcon name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>English Professional</Text>
                </TouchableOpacity>
              </View>
              <TextInput
                testID="whatsapp-template-input"
                value={template}
                onChangeText={setTemplate}
                multiline
                style={[styles.input, { height: 160, textAlignVertical: "top", paddingTop: 10 }]}
              />
              {showPreview && (
                <View style={styles.previewBox}>
                  <View style={styles.previewHeader}>
                    <PhIcon name="eye-outline" size={14} color={colors.primary} />
                    <Text style={styles.previewTitle}>Live Preview (WhatsApp)</Text>
                  </View>
                  <Text style={styles.previewText}>
                    {fillTemplate(template || PRESETS.wa_gujarati, brandName)}
                  </Text>
                </View>
              )}
            </Field>
            <Field label="Copy-All Template (for quick copy)">
              <Text style={styles.hint}>
                Use: {"{customer_name}"}, {"{order_id}"}, {"{courier}"}, {"{tracking_id}"}, {"{tracking_url}"}, {"{amount}"}
              </Text>
              <View style={styles.presetRow}>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setCopyTemplate(PRESETS.copy_pro)}
                  testID="preset-copy-pro"
                >
                  <PhIcon name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>Professional Layout</Text>
                </TouchableOpacity>
              </View>
              <TextInput
                testID="copy-template-input"
                value={copyTemplate}
                onChangeText={setCopyTemplate}
                multiline
                style={[styles.input, { height: 160, textAlignVertical: "top", paddingTop: 10 }]}
              />
              {showPreview && (
                <View style={styles.previewBox}>
                  <View style={styles.previewHeader}>
                    <PhIcon name="eye-outline" size={14} color={colors.primary} />
                    <Text style={styles.previewTitle}>Live Preview (Copy)</Text>
                  </View>
                  <Text style={styles.previewText}>
                    {fillTemplate(copyTemplate || PRESETS.copy_pro, brandName)}
                  </Text>
                </View>
              )}
            </Field>
            <Field label="Default ETA (days)">
              <TextInput
                testID="eta-days-input"
                value={etaDays}
                onChangeText={setEtaDays}
                keyboardType="number-pad"
                style={styles.input}
              />
            </Field>
          </Section>

          <TouchableOpacity testID="save-settings-btn" style={styles.saveBtn} onPress={saveSender}>
            <PhIcon name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Settings</Text>
          </TouchableOpacity>
          </>)}

          {section === "couriers" && (<>
          {/* Couriers */}
          <Section title="Courier Partners" icon="rocket-outline">
            {courierLimits && (
              <View
                testID="courier-limit-banner"
                style={[
                  styles.limitBanner,
                  !courierLimits.can_add && styles.limitBannerFull,
                ]}
              >
                <PhIcon
                  name={
                    courierLimits.is_unlimited
                      ? "infinite"
                      : courierLimits.can_add
                      ? "information-circle"
                      : "lock-closed"
                  }
                  size={16}
                  color={
                    courierLimits.can_add ? colors.textMuted : "#B45309"
                  }
                />
                <Text
                  style={[
                    styles.limitBannerText,
                    !courierLimits.can_add && { color: "#92400E" },
                  ]}
                >
                  {courierLimits.is_unlimited
                    ? `${courierLimits.plan_label} plan · Unlimited courier partners`
                    : `${courierLimits.plan_label} plan · ${courierLimits.current_count} of ${courierLimits.limit} courier partner${
                        (courierLimits.limit || 0) === 1 ? "" : "s"
                      } used`}
                </Text>
              </View>
            )}
            {couriers.map((c) => (
              <TouchableOpacity
                key={c.id}
                testID={`courier-card-${c.name}`}
                style={styles.courierCard}
                onPress={() => router.push(`/courier/${c.id}`)}
              >
                <View style={{ flex: 1 }}>
                  <Text style={styles.courierName}>{c.name}</Text>
                  <Text style={styles.courierSub}>
                    Prefix: <Text style={styles.mono}>{c.series_prefix || "—"}</Text> ·
                    Next: <Text style={styles.mono}>
                      {String(c.next_number).padStart(c.number_padding, "0")}
                    </Text>
                  </Text>
                  {c.contact_phone ? (
                    <Text style={styles.courierSub}>📞 {c.contact_phone}</Text>
                  ) : null}
                </View>
                <PhIcon name="chevron-forward" size={20} color={colors.textMuted} />
              </TouchableOpacity>
            ))}
            {courierLimits && !courierLimits.can_add ? (
              courierLimits.suggested_upgrade ? (
                <TouchableOpacity
                  testID="upgrade-to-add-courier-btn"
                  style={[styles.saveBtn, { marginTop: 10, backgroundColor: "#F59E0B" }]}
                  onPress={() => router.push("/plans")}
                >
                  <PhIcon name="rocket" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>
                    Upgrade to {courierLimits.suggested_upgrade} to add more
                  </Text>
                </TouchableOpacity>
              ) : (
                // Platinum user at cap — no upgrade path, just an informative row.
                <View style={[styles.limitBanner, styles.limitBannerFull, { marginTop: 10 }]}>
                  <PhIcon name="lock-closed" size={16} color="#B45309" />
                  <Text style={[styles.limitBannerText, { color: "#92400E" }]}>
                    You've reached the {courierLimits.limit}-partner limit on your
                    {" "}{courierLimits.plan_label} plan. Contact support to raise it.
                  </Text>
                </View>
              )
            ) : (
              <TouchableOpacity
                testID="add-courier-btn"
                style={[styles.saveBtn, { marginTop: 10 }]}
                onPress={() => router.push("/courier/new")}
              >
                <PhIcon name="add" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>Add Courier Partner</Text>
              </TouchableOpacity>
            )}
          </Section>
          </>)}

          {/* === SECTION: Notifications === */}
          {section === "notifications" && (<>
          <Section title="Notifications" icon="notifications-outline">
            <NotificationsPanel />
          </Section>
          {flagOfflineSyncQueueView && (
          <Section title="Offline Sync Queue" icon="cloud-upload-outline">
            <Text style={styles.hint}>
              Items waiting to be synced to the server. They sync
              automatically when you're back online.
            </Text>
            <PendingSyncPanel />
          </Section>
          )}
          </>)}

          {/* === SECTION: About & Help === */}
          {section === "about" && (<>
          <Section title="About" icon="information-circle-outline">
            <View style={styles.aboutRow}>
              <Text style={styles.aboutKey}>App</Text>
              <Text style={styles.aboutVal}>{APP_EXTRA.storeListingName || APP_NAME}</Text>
            </View>
            <View style={styles.aboutRow}>
              <Text style={styles.aboutKey}>Version</Text>
              <Text style={styles.aboutVal}>{APP_VERSION}</Text>
            </View>
            <View style={styles.aboutRow}>
              <Text style={styles.aboutKey}>Build</Text>
              <Text style={styles.aboutVal}>{Platform.OS}</Text>
            </View>
          </Section>

          <Section title="Help & Support" icon="help-buoy-outline">
            <TouchableOpacity
              testID="about-contact-support"
              style={styles.aboutLinkRow}
              onPress={() =>
                Linking.openURL(
                  `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(APP_NAME + " support")}`,
                )
              }
            >
              <PhIcon name="mail-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Contact Support</Text>
              <PhIcon name="open-outline" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-whats-new"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("What's New", "• Modular Settings UI\n• Smart Paste Chat-style flow\n• Edit Shipment\n• Bulk Print 2-step popup")}
            >
              <PhIcon name="sparkles-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>What's New</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-rate-app"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("Thanks!", "Rating sheet will open when published to stores.")}
            >
              <PhIcon name="star-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Rate this App</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
          </Section>

          <Section title="Legal" icon="shield-checkmark-outline">
            <TouchableOpacity
              testID="about-privacy"
              style={styles.aboutLinkRow}
              onPress={() => router.push("/refund-policy?tab=privacy" as any)}
            >
              <PhIcon name="lock-closed-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Privacy Policy</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-terms"
              style={styles.aboutLinkRow}
              onPress={() => router.push("/refund-policy?tab=terms" as any)}
            >
              <PhIcon name="document-text-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Terms of Service</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-refund"
              style={styles.aboutLinkRow}
              onPress={() => router.push("/refund-policy?tab=refund" as any)}
            >
              <PhIcon name="cash-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Refund Policy</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-refund"
              style={styles.aboutLinkRow}
              onPress={() => router.push("/refund-policy" as any)}
            >
              <PhIcon name="refresh-circle-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Refund & Cancellation Policy</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-cancel-sub"
              style={styles.aboutLinkRow}
              onPress={() => router.push("/cancel-subscription" as any)}
            >
              <PhIcon name="close-circle-outline" size={18} color="#DC2626" />
              <Text style={[styles.aboutLinkText, { color: "#DC2626" }]}>Cancel Subscription</Text>
              <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
          </Section>
          </>)}
        </ScrollView>
      </KeyboardAvoidingView>

      {/* ── Unsaved-changes Modal ──────────────────────────────────
          Custom modal because RN Web's Alert.alert with 3 buttons
          renders inconsistently. Looks identical on web + mobile. */}
      <Modal
        visible={unsavedOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setUnsavedOpen(false)}
      >
        <View style={styles.unsavedBackdrop}>
          <View style={styles.unsavedCard} testID="unsaved-modal">
            <View style={styles.unsavedIconWrap}>
              <PhIcon name="warning" size={26} color="#D97706" />
            </View>
            <Text style={styles.unsavedTitle}>Unsaved changes</Text>
            <Text style={styles.unsavedBody}>
              You've made some changes but haven't saved yet. What would you like to do?
            </Text>

            <TouchableOpacity
              testID="unsaved-save-btn"
              style={[styles.unsavedBtn, styles.unsavedBtnPrimary, savingFromAlert && { opacity: 0.6 }]}
              disabled={savingFromAlert}
              onPress={async () => {
                setUnsavedOpen(false);
                await saveSectionAndExit(section);
              }}
            >
              {savingFromAlert ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <PhIcon name="save" size={16} color="#fff" />
                  <Text style={styles.unsavedBtnPrimaryTxt}>Save changes</Text>
                </>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              testID="unsaved-discard-btn"
              style={[styles.unsavedBtn, styles.unsavedBtnDanger]}
              onPress={() => {
                setOriginalSnap(getSectionSnapshot(section));
                setUnsavedOpen(false);
                const pending = pendingNavActionRef.current;
                pendingNavActionRef.current = null;
                if (pending) {
                  try { (navigation as any).dispatch(pending); }
                  catch { router.replace("/(tabs)/settings"); }
                } else {
                  router.replace("/(tabs)/settings");
                }
              }}
            >
              <PhIcon name="trash-outline" size={16} color="#B91C1C" />
              <Text style={styles.unsavedBtnDangerTxt}>Discard changes</Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="unsaved-cancel-btn"
              style={[styles.unsavedBtn, styles.unsavedBtnGhost]}
              onPress={() => {
                pendingNavActionRef.current = null;
                setUnsavedOpen(false);
              }}
            >
              <Text style={styles.unsavedBtnGhostTxt}>Keep editing</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function Section({
  title, icon, children,
}: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      {/* Phase-21 — When `title` is empty, skip the entire header row
          (icon + label) so callers can render a "headless" section
          without leaving an orphan icon next to a blank Text. Used
          by the Smart Fill block in business-settings where the
          heading was removed but the surrounding card styling
          (background, padding, etc.) is still wanted. */}
      {title ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <PhIcon name={icon} size={16} color={colors.primary} />
          <Text style={styles.sectionTitle}>{title}</Text>
        </View>
      ) : null}
      {children}
    </View>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

// ───────────── Notifications panel ─────────────
//
// Settings → Notifications hosts these toggles. Backend stores them
// on users.notification_prefs. Delivery engine (push via Expo + email
// via SMTP) is wired in a follow-up; preferences saved here remain
// the source of truth.
type NotifKey =
  | "trial_ending" | "plan_expiring" | "low_credits"
  | "payment_success" | "daily_summary"
  | "channel_push" | "channel_email";

const NOTIF_ROWS: Array<{
  key: NotifKey; title: string; sub: string; icon: string; color: string;
}> = [
  { key: "trial_ending",    title: "Trial ending alert",   sub: "3 days before trial expires",       icon: "time-outline",        color: "#F59E0B" },
  { key: "plan_expiring",   title: "Plan renewal reminder",sub: "7 days before paid plan expires",   icon: "calendar-outline",    color: "#3B82F6" },
  { key: "low_credits",     title: "Low credits warning",  sub: "When wallet ≤ 5 credits",           icon: "battery-half-outline",color: "#EF4444" },
  { key: "payment_success", title: "Payment receipt",      sub: "After successful Razorpay payment", icon: "receipt-outline",     color: "#10B981" },
  { key: "daily_summary",   title: "Daily summary",        sub: "End-of-day digest of dispatches",   icon: "newspaper-outline",   color: "#8B5CF6" },
];

function NotificationsPanel() {
  const [prefs, setPrefs] = useState<Record<NotifKey, boolean> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await Api.getNotificationPrefs();
      setPrefs(r as any);
    } catch (e: any) {
      Alert.alert("Couldn't load preferences", e?.message || "Try again later");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  const toggle = async (k: NotifKey, val: boolean) => {
    if (!prefs) return;
    const next = { ...prefs, [k]: val };
    setPrefs(next);
    setSaving(true);
    try {
      await Api.updateNotificationPrefs({ [k]: val } as any);
    } catch (e: any) {
      // revert on failure
      setPrefs(prefs);
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setSaving(false);
    }
  };

  if (loading || !prefs) {
    return (
      <View style={{ paddingVertical: 30, alignItems: "center" }}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <>
      <Text style={styles.notifGroupHead}>Delivery Channels</Text>
      <View style={styles.notifCard}>
        <NotifRowSwitch
          icon="phone-portrait-outline"
          color="#0EA5E9"
          title="Push notifications"
          sub="In-app + push to your device"
          value={prefs.channel_push}
          onChange={(v) => toggle("channel_push", v)}
          disabled={saving}
        />
        <View style={styles.notifDivider} />
        <NotifRowSwitch
          icon="mail-outline"
          color="#475569"
          title="Email alerts"
          sub="Sent to your registered email"
          value={prefs.channel_email}
          onChange={(v) => toggle("channel_email", v)}
          disabled={saving}
        />
      </View>

      <Text style={styles.notifGroupHead}>Alert Types</Text>
      <View style={styles.notifCard}>
        {NOTIF_ROWS.map((row, i) => (
          <React.Fragment key={row.key}>
            <NotifRowSwitch
              icon={row.icon}
              color={row.color}
              title={row.title}
              sub={row.sub}
              value={prefs[row.key]}
              onChange={(v) => toggle(row.key, v)}
              disabled={saving}
            />
            {i < NOTIF_ROWS.length - 1 ? <View style={styles.notifDivider} /> : null}
          </React.Fragment>
        ))}
      </View>

      <View style={styles.notifNote}>
        <PhIcon name="information-circle-outline" size={14} color="#64748B" />
        <Text style={styles.notifNoteTxt}>
          Preferences are saved instantly. In-app banners on the Home
          screen always respect these settings. Push & email delivery
          will roll out in the next update.
        </Text>
      </View>
    </>
  );
}

function NotifRowSwitch({
  icon, color, title, sub, value, onChange, disabled,
}: {
  icon: string; color: string; title: string; sub: string;
  value: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <View style={styles.notifRow}>
      <View style={[styles.notifIcon, { backgroundColor: `${color}1A` }]}>
        <PhIcon name={icon as any} size={18} color={color} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.notifTitle}>{title}</Text>
        <Text style={styles.notifSub}>{sub}</Text>
      </View>
      <Switch
        value={value}
        onValueChange={onChange}
        disabled={disabled}
        trackColor={{ true: colors.primary, false: "#E5E7EB" }}
        thumbColor="#fff"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: { paddingHorizontal: 20, paddingTop: 14, paddingBottom: 12, flexDirection: "row", alignItems: "center" },
  title: { fontSize: 28, fontWeight: "800", color: colors.text, letterSpacing: -0.5 },
  // -- Account block ----
  accountRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  accountAvatar: {
    width: 52,
    height: 52,
    borderRadius: 26,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
  },
  accountAvatarTxt: { color: "#fff", fontWeight: "800", fontSize: 22 },
  accountName: { fontSize: 15, fontWeight: "800", color: colors.text },
  accountEmail: { fontSize: 12, color: colors.textMuted },
  badgeAdmin: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: "#2E7D32",
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 6,
  },
  badgePlan: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 6,
    borderWidth: 1,
    borderColor: "#FDE68A",
  },
  badgeTxt: { fontSize: 10, fontWeight: "800", color: "#fff", letterSpacing: 0.5 },
  secondaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: "#EEF2FF",
    borderRadius: 10,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: "#C7D2FE",
  },
  secondaryBtnTxt: { color: colors.primary, fontWeight: "700", fontSize: 14 },
  dangerBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: "#FEE2E2",
    borderRadius: 10,
    paddingVertical: 11,
    borderWidth: 1,
    borderColor: "#FCA5A5",
  },
  dangerBtnTxt: { color: "#C62828", fontWeight: "700", fontSize: 14 },

  // --- Smart Paste AI -------------------------------------------------
  spaiIntro: { gap: 8, marginBottom: 4 },
  spaiBadge: {
    alignSelf: "flex-start",
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#7C3AED", paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
  },
  spaiBadgeTxt: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
  spaiHint: { fontSize: 12.5, color: "#475569", lineHeight: 19 },
  toggleRow: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingVertical: 10, marginTop: 8,
    borderTopWidth: 1, borderBottomWidth: 1, borderColor: "#F1F5F9",
  },
  toggleLbl: { fontSize: 14, fontWeight: "800", color: "#1E293B" },
  toggleSub: { fontSize: 11.5, color: "#64748B", marginTop: 2 },
  fieldLabel: { fontSize: 11, color: "#64748B", fontWeight: "800", letterSpacing: 0.6, marginTop: 14 },
  fieldHelp: { fontSize: 11.5, color: "#64748B", marginTop: 4, lineHeight: 17 },
  spaiTextArea: {
    marginTop: 6,
    minHeight: 160,
    maxHeight: 320,
    borderWidth: 1.5, borderColor: "#CBD5E1", borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 13, color: "#0F172A", lineHeight: 19,
    backgroundColor: "#F8FAFC",
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }) as any,
  },
  spaiCharCount: { fontSize: 10.5, color: "#94A3B8", textAlign: "right", marginTop: 4 },
  spaiActions: { flexDirection: "row", gap: 8, marginTop: 10 },
  primaryBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: colors.primary, borderRadius: 10, paddingVertical: 11,
  },
  primaryBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },
  spaiDefaultToggle: {
    flexDirection: "row", alignItems: "center", gap: 4,
    marginTop: 14, alignSelf: "flex-start",
  },
  spaiDefaultToggleTxt: { color: "#64748B", fontSize: 12, fontWeight: "700" },
  spaiDefaultBox: {
    marginTop: 8, padding: 10,
    backgroundColor: "#F1F5F9", borderRadius: 8,
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  spaiDefaultText: {
    fontSize: 11, color: "#334155", lineHeight: 17,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }) as any,
  },
  rateGrid: {
    flexDirection: "row", gap: 8, marginTop: 12,
  },
  rateNote: {
    fontSize: 11, color: "#64748B", marginTop: 8, fontStyle: "italic",
  },
  rateCell: {
    flex: 1,
    borderWidth: 1.5, borderColor: "#E5E7EB", borderRadius: 10,
    padding: 10, backgroundColor: "#F8FAFC",
  },
  rateCellHead: {
    flexDirection: "row", alignItems: "center", gap: 4, marginBottom: 4,
  },
  rateCellLabel: { fontSize: 12, fontWeight: "900", letterSpacing: 0.4 },
  rateCellHint: { fontSize: 10, color: "#94A3B8", marginBottom: 6 },
  rateCellInputRow: {
    flexDirection: "row", alignItems: "center", gap: 4,
  },
  rateCellInput: {
    flex: 1,
    borderWidth: 1, borderColor: "#CBD5E1", borderRadius: 8,
    paddingHorizontal: 8, paddingVertical: Platform.OS === "ios" ? 8 : 4,
    fontSize: 15, fontWeight: "800", color: "#0F172A",
    backgroundColor: "#fff",
    textAlign: "center",
  },
  rateCellUnit: { fontSize: 10, color: "#64748B", fontWeight: "700" },
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
    color: colors.text,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  subTitle: { fontSize: 13, fontWeight: "800", color: colors.text },
  fieldLabel: { fontSize: 12, color: colors.textMuted, fontWeight: "700", marginBottom: 6 },
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
  hint: { fontSize: 12, color: colors.textMuted, marginBottom: 4 },
  toggleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: "#F3F4F6",
  },
  toggleLabel: {
    flex: 1,
    fontSize: 14,
    color: colors.text,
    fontWeight: "600",
    paddingRight: 10,
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#F1F1F1",
  },
  switchLabel: { fontWeight: "700", color: colors.text },
  switchHint: { fontSize: 12, color: colors.textMuted, marginTop: 2 },

  /* ---- Custom Label Fields (Phase B) ---- */
  emptyCf: {
    alignItems: "center",
    paddingVertical: 18,
    paddingHorizontal: 12,
    backgroundColor: "#F9FAFB",
    borderRadius: 12,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: "#E5E7EB",
    marginVertical: 8,
  },
  emptyCfText: { fontWeight: "800", color: "#4B5563", marginTop: 6 },
  emptyCfSub: { fontSize: 12, color: "#9CA3AF", marginTop: 2 },
  /* ---- Content Budget indicator ---- */
  budgetBox: {
    borderRadius: 12,
    borderWidth: 1.5,
    paddingVertical: 10,
    paddingHorizontal: 12,
    marginTop: 10,
    marginBottom: 4,
    gap: 6,
  },
  budgetBoxOk: {
    backgroundColor: "#ECFDF5",
    borderColor: "#A7F3D0",
  },
  budgetBoxFull: {
    backgroundColor: "#FFFBEB",
    borderColor: "#FCD34D",
  },
  budgetHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  budgetTitle: {
    fontSize: 13,
    fontWeight: "800",
    letterSpacing: 0.3,
  },
  budgetDots: {
    flexDirection: "row",
    gap: 6,
    marginTop: 2,
  },
  budgetDot: {
    flex: 1,
    height: 6,
    borderRadius: 3,
  },
  budgetDotOn: { backgroundColor: "#10B981" },
  budgetDotFull: { backgroundColor: "#F59E0B" },
  budgetDotOff: { backgroundColor: "#E5E7EB" },
  budgetHelp: {
    fontSize: 11.5,
    color: "#334155",
    lineHeight: 16,
    marginTop: 2,
  },
  cfCard: {
    backgroundColor: "#F9FAFB",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    padding: 12,
    marginTop: 10,
    gap: 8,
  },
  cfHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  cfIndex: {
    fontSize: 13,
    fontWeight: "800",
    color: "#6B7280",
    paddingHorizontal: 8,
    paddingVertical: 3,
    backgroundColor: "#E5E7EB",
    borderRadius: 8,
  },
  cfDeleteBtn: {
    padding: 6,
    borderRadius: 8,
    backgroundColor: "#FEF2F2",
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  cfRow2: { flexDirection: "row", gap: 8 },
  cfLabel: {
    fontSize: 11.5,
    fontWeight: "700",
    color: "#6B7280",
    textTransform: "uppercase",
    letterSpacing: 0.4,
    marginBottom: 4,
    marginTop: 2,
  },
  cfPosPill: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 20,
    flexShrink: 0,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  cfPosPillActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  cfPosPillText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text,
  },
  cfSizePill: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    alignItems: "center",
    minWidth: 50,
  },
  cfBoldRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingTop: 4,
  },
  addCfBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginTop: 12,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 2,
    borderStyle: "dashed",
    borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  addCfBtnText: {
    color: colors.primary,
    fontWeight: "800",
    fontSize: 14,
  },
  cfSourceTile: {
    flex: 1,
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 10,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    alignItems: "flex-start",
  },
  cfSourceTileActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  cfSourceTitle: {
    fontSize: 13,
    fontWeight: "800",
    color: colors.text,
  },
  cfSourceSub: {
    fontSize: 10.5,
    color: "#6B7280",
    marginTop: 2,
  },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 50,
    backgroundColor: colors.primary,
    borderRadius: 12,
    marginBottom: 6,
  },
  saveBtnText: { color: "#fff", fontWeight: "800" },
  outlineBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 42,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  outlineBtnText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  courierCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    marginBottom: 8,
    backgroundColor: "#fff",
  },
  courierName: { fontWeight: "800", color: colors.text, fontSize: 14 },
  courierSub: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  limitBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 10,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: "#F9FAFB",
    marginBottom: 10,
  },
  limitBannerFull: {
    backgroundColor: "#FEF3C7",
    borderColor: "#FCD34D",
  },
  limitBannerText: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.textMuted,
    flex: 1,
  },
  mono: { fontFamily: "Courier", fontWeight: "800", color: colors.text },

  connectedBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: 12,
    backgroundColor: colors.successBg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#D1FAE5",
  },
  connectedTitle: { fontWeight: "800", color: colors.successText, fontSize: 14 },
  connectedSub: { fontSize: 11, color: colors.successText, marginTop: 2 },

  mapRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 8,
    gap: 10,
  },
  mapLabel: {
    width: 120,
    fontSize: 12,
    fontWeight: "700",
    color: colors.text,
  },
  mapPick: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    height: 42,
    paddingHorizontal: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 8,
    backgroundColor: "#fff",
  },
  mapPickText: { flex: 1, color: colors.text, fontSize: 13, fontWeight: "600" },

  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    padding: 20,
  },
  pickerCard: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 16,
    maxHeight: "70%",
  },
  pickerTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 10,
  },
  pickerItem: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#F3F4F6",
  },
  pickerItemText: { color: colors.text, fontWeight: "600" },

  sampleBox: {
    padding: 12,
    borderWidth: 2,
    borderStyle: "dashed",
    borderColor: colors.primary,
    borderRadius: 10,
    marginBottom: 12,
    backgroundColor: "#FFF7ED",
  },
  sampleTitle: {
    fontSize: 13,
    fontWeight: "800",
    color: colors.text,
  },
  sampleText: { fontSize: 12, color: colors.text, marginTop: 6, lineHeight: 17 },
  sampleCols: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 6,
    fontFamily: "Courier",
  },
  sampleBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 40,
    backgroundColor: colors.primary,
    borderRadius: 8,
    marginTop: 10,
  },
  sampleBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },

  // Phase-5: Service-Account share panel
  saShareBox: {
    padding: 12,
    borderRadius: 10,
    backgroundColor: "#EFF6FF",
    borderWidth: 1,
    borderColor: "#BFDBFE",
    marginBottom: 12,
  },
  saShareTitle: {
    fontSize: 12.5, fontWeight: "800", color: "#1E3A8A", flex: 1,
  },
  saShareSub: {
    fontSize: 11.5, color: "#1E40AF", marginTop: 6, lineHeight: 16.5,
  },
  saEmailRow: {
    marginTop: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#fff",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: "#BFDBFE",
  },
  saEmailTxt: {
    flex: 1, fontSize: 12, fontFamily: "Courier", color: "#0F172A", fontWeight: "700",
  },
  saEmailCopyBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 6, backgroundColor: "#DBEAFE",
  },
  saEmailCopyTxt: { color: "#1E40AF", fontSize: 11.5, fontWeight: "800" },
  saShareAlt: {
    fontSize: 11, color: "#475569", marginTop: 8, lineHeight: 15.5, fontStyle: "italic",
  },
  accessBadgePrivate: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#D1FAE5", paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 6,
  },
  accessBadgePrivateTxt: {
    fontSize: 10.5, fontWeight: "800", color: "#065F46", letterSpacing: 0.3,
  },
  accessBadgePublic: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#FEF3C7", paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 6,
  },
  accessBadgePublicTxt: {
    fontSize: 10.5, fontWeight: "800", color: "#92400E", letterSpacing: 0.3,
  },

  infoBox: {
    flexDirection: "row",
    gap: 8,
    padding: 10,
    backgroundColor: "#FFF7ED",
    borderWidth: 1,
    borderColor: "#FED7AA",
    borderRadius: 8,
    marginBottom: 10,
  },
  infoText: {
    flex: 1,
    color: colors.text,
    fontSize: 12,
    lineHeight: 17,
  },
  presetRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 8,
  },
  presetBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  presetText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.primary,
  },
  smallBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
  },
  smallBtnText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#fff",
  },
  segmentRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 2,
    marginBottom: 4,
  },
  segmentBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  segmentBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  segmentText: {
    fontSize: 12,
    fontWeight: "800",
    color: colors.text,
  },
  segmentTextActive: {
    color: "#fff",
  },
  previewBox: {
    marginTop: 8,
    padding: 12,
    backgroundColor: "#ECFDF5",
    borderWidth: 1,
    borderColor: "#A7F3D0",
    borderRadius: 10,
  },
  previewHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 6,
    paddingBottom: 6,
    borderBottomWidth: 1,
    borderBottomColor: "#A7F3D0",
  },
  previewTitle: {
    fontSize: 12,
    fontWeight: "800",
    color: "#065F46",
  },
  previewText: {
    fontSize: 13,
    color: "#064E3B",
    lineHeight: 20,
  },
  /* ---- Placeholder (Notifications coming soon) ---- */
  placeholderBox: {
    alignItems: "center",
    paddingVertical: 28,
    paddingHorizontal: 16,
    backgroundColor: "#F8FAFC",
    borderRadius: 12,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: "#CBD5E1",
    gap: 8,
  },
  placeholderTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: "#334155",
    marginTop: 4,
  },
  placeholderSub: {
    fontSize: 12.5,
    color: "#64748B",
    textAlign: "center",
    lineHeight: 18,
  },
  /* ---- Account badges ---- */
  badgeDisplayId: {
    flexDirection: "row", alignItems: "center", gap: 3,
    backgroundColor: "#E0E7FF",
    paddingHorizontal: 7, paddingVertical: 3, borderRadius: 5,
  },
  badgeDisplayIdTxt: {
    color: "#0F172A", fontSize: 10.5, fontWeight: "900", letterSpacing: 0.6,
  },
  /* ---- About / Help rows ---- */
  aboutRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
  },
  aboutKey: {
    fontSize: 13,
    color: "#64748B",
    fontWeight: "700",
  },
  aboutVal: {
    fontSize: 13,
    color: colors.text,
    fontWeight: "800",
  },
  /* ---- Notifications panel ---- */
  notifGroupHead: {
    fontSize: 11,
    fontWeight: "800",
    color: "#64748B",
    letterSpacing: 0.6,
    textTransform: "uppercase",
    marginTop: 14,
    marginBottom: 8,
  },
  notifCard: {
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    paddingHorizontal: 14,
    paddingVertical: 4,
  },
  notifRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
  },
  notifIcon: {
    width: 36, height: 36, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  notifTitle: { color: "#0F172A", fontSize: 14, fontWeight: "800" },
  notifSub:   { color: "#64748B", fontSize: 12, marginTop: 2 },
  notifDivider: { height: 1, backgroundColor: "#F1F5F9" },
  notifNote: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "#F8FAFC",
    borderRadius: 10,
    padding: 12,
    marginTop: 14,
  },
  notifNoteTxt: { flex: 1, color: "#64748B", fontSize: 12, lineHeight: 17 },
  aboutLinkRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
  },
  aboutLinkText: {
    flex: 1,
    fontSize: 14,
    color: colors.text,
    fontWeight: "700",
  },
  /* ---- Settings Hub cards (top-level) ---- */
  hubGroup: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    overflow: "hidden",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    marginTop: 4,
    marginBottom: 12,
  },
  hubRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingVertical: 16,
    paddingHorizontal: 16,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
    backgroundColor: colors.surface,
  },
  hubRowFirst: {},
  hubRowLast: {
    borderBottomWidth: 0,
  },
  hubIconWrap: {
    width: 44,
    height: 44,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  hubTitle: {
    flex: 1,
    fontSize: 17,
    fontWeight: "700",
    color: colors.text,
    letterSpacing: 0.1,
  },
  hubSub: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  hubSignOutBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    marginTop: 4,
    paddingVertical: 16,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: "#FECACA",
    backgroundColor: "#FEF2F2",
  },
  hubSignOutText: {
    color: "#DC2626",
    fontWeight: "800",
    fontSize: 15,
  },
  /* ---- Admin pill (hub) ---- */
  adminPill: {
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 6,
    backgroundColor: "#FEE2E2",
    marginRight: 6,
  },
  adminPillTxt: {
    fontSize: 9.5,
    fontWeight: "900",
    color: "#B91C1C",
    letterSpacing: 0.5,
  },
  /* ---- Plan & Wallet cards (Plan & Billing section) ---- */
  planCard: {
    borderRadius: 16,
    borderWidth: 2,
    padding: 16,
    marginBottom: 12,
  },
  planCardHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  planChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
  },
  planChipTxt: {
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.4,
  },
  planName: {
    fontSize: 26,
    fontWeight: "800",
    letterSpacing: -0.3,
  },
  planSub: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 4,
    fontWeight: "500",
  },
  planTile: {
    flexBasis: "48%",
    flexGrow: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 12,
    borderWidth: 1.5,
    position: "relative",
  },
  planTileName: {
    fontSize: 14,
    fontWeight: "800",
    flex: 1,
  },
  planTileBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  planTileBadgeTxt: {
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 0.5,
  },
  walletCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: colors.surface,
    marginBottom: 14,
  },
  walletIconWrap: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: "#10B981",
    alignItems: "center",
    justifyContent: "center",
  },
  walletLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.textMuted,
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  walletBalance: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
    marginTop: 2,
  },
  titleSub: {
    fontSize: 13,
    color: colors.textMuted,
    marginTop: 2,
    fontWeight: "500",
  },
  /* ---- Unsaved changes badge ---- */
  dirtyBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: "#FEF3C7",
    borderWidth: 1,
    borderColor: "#FCD34D",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  dirtyDot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: "#D97706",
  },
  dirtyBadgeTxt: {
    fontSize: 10.5,
    fontWeight: "800",
    color: "#92400E",
    letterSpacing: 0.4,
  },
  /* ---- Unsaved-changes Modal ---- */
  unsavedBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.55)",
    justifyContent: "center",
    alignItems: "center",
    padding: 24,
  },
  unsavedCard: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: "#fff",
    borderRadius: 18,
    padding: 22,
    alignItems: "center",
    gap: 6,
  },
  unsavedIconWrap: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: "#FEF3C7",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 6,
  },
  unsavedTitle: {
    fontSize: 19,
    fontWeight: "800",
    color: colors.text,
  },
  unsavedBody: {
    fontSize: 14,
    color: colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
    marginTop: 4,
    marginBottom: 14,
  },
  unsavedBtn: {
    width: "100%",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 14,
    borderRadius: 12,
    marginBottom: 8,
  },
  unsavedBtnPrimary: {
    backgroundColor: colors.primary,
  },
  unsavedBtnPrimaryTxt: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 15,
  },
  unsavedBtnDanger: {
    backgroundColor: "#FEF2F2",
    borderWidth: 1.5,
    borderColor: "#FECACA",
  },
  unsavedBtnDangerTxt: {
    color: "#B91C1C",
    fontWeight: "800",
    fontSize: 15,
  },
  unsavedBtnGhost: {
    backgroundColor: "transparent",
    paddingVertical: 10,
    marginBottom: 0,
  },
  unsavedBtnGhostTxt: {
    color: colors.textMuted,
    fontWeight: "700",
    fontSize: 14,
  },
});
