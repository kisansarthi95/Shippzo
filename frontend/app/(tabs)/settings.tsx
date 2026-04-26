import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect, useLocalSearchParams, useNavigation } from "expo-router";
import { Api, Courier, Settings as SettingsT, SenderAddress, SheetPreview, SHEET_FIELDS, api, PlanKey } from "../../lib/api";
import { colors } from "../../lib/theme";
import { useAuth } from "../../lib/auth";

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
    "નમસ્તે {customer_name} 🙏\n" +
    "\n" +
    "તમારો ઓર્ડર #{order_id} સફળતાપૂર્વક મોકલવામાં આવ્યો છે.\n" +
    "\n" +
    "📦 Courier: {courier}\n" +
    "🔖 Tracking ID: {tracking_id}\n" +
    "\n" +
    "🔗 Track your order:\n" +
    "{tracking_url}\n" +
    "\n" +
    "⏱ અપેક્ષિત ડિલિવરી: {eta_days} દિવસ\n" +
    "\n" +
    "આભાર!"
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

  // Phase-4b+ Smart Paste AI customisation
  const [spaiEnabled, setSpaiEnabled] = useState(true);
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
    spaiEnabled, spaiInstructions,
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
      setSheetUrl(s.sheet.url);
      setConnectedSheetId(s.sheet.sheet_id);
      setConnectedHeaders(s.sheet.headers || []);
      setMapping(s.sheet.column_mapping || {});
    }
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
        const w = await Api.getWallet();
        if (!cancelled) setWalletBal(typeof w?.balance === "number" ? w.balance : 0);
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
      const r = await api.put("/settings", {
        ai_cost_simple: simple,
        ai_cost_medium: medium,
        ai_cost_complex: complex,
      });
      const d = r.data as any;
      // Snap UI back to server-clamped values so the user sees what was saved.
      setAiCostSimple(String(d.ai_cost_simple ?? simple));
      setAiCostMedium(String(d.ai_cost_medium ?? medium));
      setAiCostComplex(String(d.ai_cost_complex ?? complex));
      Alert.alert(
        "Rate card saved",
        `Simple ${d.ai_cost_simple ?? simple} · Medium ${d.ai_cost_medium ?? medium} · Complex ${d.ai_cost_complex ?? complex} credits per order.`,
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
    if (!sheetUrl.trim()) {
      Alert.alert("Paste link", "Please paste your Google Sheet URL");
      return;
    }
    setSheetStatus("loading");
    try {
      const p = await Api.sheetsPreview(sheetUrl.trim());
      setPreview(p);
      setMapping(p.auto_mapping || {});
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
            <Ionicons name="chevron-back" size={26} color={colors.text} />
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
                      idx === HUB_CARDS.length - 1 && styles.hubRowLast,
                    ]}
                    onPress={() =>
                      router.push({ pathname: "/(tabs)/settings", params: { section: c.key } })
                    }
                    activeOpacity={0.6}
                  >
                    <View style={[styles.hubIconWrap, { backgroundColor: c.color + "1A" }]}>
                      <Ionicons name={c.icon} size={22} color={c.color} />
                    </View>
                    <Text style={styles.hubTitle}>{c.title}</Text>
                    <Ionicons name="chevron-forward" size={22} color="#9CA3AF" />
                  </TouchableOpacity>
                ))}
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
                <Ionicons name="log-out-outline" size={20} color="#DC2626" />
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
                <View style={{ flexDirection: "row", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                  {user?.is_admin ? (
                    <View style={styles.badgeAdmin}>
                      <Ionicons name="shield-checkmark" size={11} color="#fff" />
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
                    <Ionicons name="chevron-forward" size={11} color="#92400E" />
                  </TouchableOpacity>
                </View>
              </View>
            </View>

            <TouchableOpacity
              testID="clear-demo-btn"
              style={[styles.secondaryBtn, { marginTop: 12 }]}
              onPress={() => {
                Alert.alert(
                  "Clear demo data?",
                  "આ તમારા 15 demo shipments હટાવી દેશે. તમારી real shipments ને અસર નહીં થાય.",
                  [
                    { text: "Cancel", style: "cancel" },
                    {
                      text: "Clear",
                      style: "destructive",
                      onPress: async () => {
                        try {
                          const r = await api.post("/demo/clear");
                          Alert.alert(
                            "Done",
                            `Removed ${r.data?.deleted ?? 0} demo rows.`
                          );
                        } catch (e: any) {
                          Alert.alert("Failed", e?.message || "Could not clear demo data");
                        }
                      },
                    },
                  ]
                );
              }}
            >
              <Ionicons name="sparkles-outline" size={16} color={colors.primary} />
              <Text style={styles.secondaryBtnTxt}>Clear Demo Data</Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="sign-out-btn"
              style={[styles.dangerBtn, { marginTop: 8 }]}
              onPress={() => {
                Alert.alert("Sign out?", "તમારે ફરી login કરવું પડશે.", [
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
              <Ionicons name="log-out-outline" size={16} color="#C62828" />
              <Text style={styles.dangerBtnTxt}>Sign out</Text>
            </TouchableOpacity>
          </Section>
          </>)}

          {section === "business" && (<>
          {/* Phase-4b+ Smart Paste AI */}
          <Section title="Smart Paste AI" icon="sparkles-outline">
            <View style={styles.spaiIntro}>
              <View style={styles.spaiBadge}>
                <Ionicons name="sparkles" size={11} color="#fff" />
                <Text style={styles.spaiBadgeTxt}>AI powered</Text>
              </View>
              <Text style={styles.spaiHint}>
                WhatsApp-style પેસ્ટ ને LLM automatically ફોર્મ માં convert કરે છે — ChatGPT bounce ખતમ. દરેક user પોતાના વ્યવસાય પ્રમાણે અલગ instructions રાખી શકે.
              </Text>
            </View>

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleLbl}>Enable AI parser</Text>
                <Text style={styles.toggleSub}>
                  OFF કરશો તો regex fallback વાપરશે (free, no credits).
                </Text>
              </View>
              <Switch
                testID="spai-enabled-toggle"
                value={spaiEnabled}
                onValueChange={setSpaiEnabled}
              />
            </View>

            <Text style={styles.fieldLabel}>Your custom instructions (optional)</Text>
            <Text style={styles.fieldHelp}>
              આ text તમારા default ShipBot rules પહેલા inject થશે. દા.ત. "Always use ODC3 as default item", "Ignore 'Rush order' keyword", વગેરે.
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
                    <Ionicons name="checkmark-circle" size={16} color="#fff" />
                    <Text style={styles.primaryBtnTxt}>Save</Text>
                  </>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                testID="spai-reset-btn"
                style={styles.secondaryBtn}
                onPress={resetSmartPasteAI}
              >
                <Ionicons name="refresh" size={16} color={colors.primary} />
                <Text style={styles.secondaryBtnTxt}>Reset</Text>
              </TouchableOpacity>
            </View>

            <TouchableOpacity
              testID="spai-default-toggle"
              style={styles.spaiDefaultToggle}
              onPress={() => setSpaiShowDefault((v) => !v)}
            >
              <Ionicons
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
                <Ionicons
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
              <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
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
                  <Ionicons
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
              <Ionicons name="wallet-outline" size={22} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.walletLabel}>Credit Wallet</Text>
              <Text style={styles.walletBalance}>
                {walletBal != null ? `₹${walletBal.toLocaleString("en-IN")}` : "Tap to view"}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={22} color="#94A3B8" />
          </TouchableOpacity>

          {/* AI Processing Charges — per-user rate card */}
          <Section title="AI Processing Charges" icon="pricetags-outline">
            <Text style={styles.spaiHint}>
              દરેક shipment પર AI એડ્રેસ check માટે જે credits કાપવા છે તે અહીં સેટ કરો. Max cap 2.0 credits/order (spec).
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
              ⚠️ Max per-order cap 2.0 — values above 2.0 server-side clamp થશે.
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
                    <Ionicons name="checkmark-circle" size={16} color="#fff" />
                    <Text style={styles.primaryBtnTxt}>Save rates</Text>
                  </>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                testID="rate-reset-btn"
                style={styles.secondaryBtn}
                onPress={resetRateCard}
              >
                <Ionicons name="refresh" size={16} color={colors.primary} />
                <Text style={styles.secondaryBtnTxt}>Defaults</Text>
              </TouchableOpacity>
            </View>
          </Section>
          </>)}

          {section === "business" && (<>
          {/* Google Sheet */}
          <Section title="Google Sheet (Orders source)" icon="logo-google">
            <View style={styles.sampleBox} testID="sheet-sample-box">
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Ionicons name="bulb-outline" size={14} color={colors.primary} />
                <Text style={styles.sampleTitle}>First time? Use the sample template</Text>
              </View>
              <Text style={styles.sampleText}>
                Download the sample CSV → open Google Sheets → File → Import → upload CSV → Replace current sheet. Share as "Anyone with the link → Viewer" and paste URL below.
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
                <Ionicons name="download-outline" size={16} color="#fff" />
                <Text style={styles.sampleBtnText}>Download Sample CSV</Text>
              </TouchableOpacity>
            </View>

            {sheetStatus === "connected" && !preview ? (
              <View>
                <View style={styles.connectedBox}>
                  <Ionicons name="checkmark-circle" size={18} color={colors.successText} />
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
                    <Ionicons name="open-outline" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Open Sheet</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-remap-btn"
                    style={[styles.outlineBtn, { flex: 1 }]}
                    onPress={fetchPreview}
                  >
                    <Ionicons name="refresh" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Refresh / Re-map</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-disconnect-btn"
                    style={[styles.outlineBtn, { borderColor: colors.dangerText }]}
                    onPress={disconnectSheet}
                  >
                    <Ionicons name="trash-outline" size={16} color={colors.dangerText} />
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <>
                <Text style={styles.hint}>
                  Share your sheet: File → Share → General access → "Anyone with the link → Viewer". Then paste link below.
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
                      <Ionicons name="download" size={18} color="#fff" />
                      <Text style={styles.saveBtnText}>Fetch & Preview</Text>
                    </>
                  )}
                </TouchableOpacity>
              </>
            )}

            {preview && (
              <View style={{ marginTop: 16 }}>
                <Text style={styles.subTitle}>
                  {preview.total_rows} rows · {preview.headers.length} columns
                </Text>
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
                      <Ionicons name="chevron-down" size={16} color={colors.textMuted} />
                    </TouchableOpacity>
                  </View>
                ))}
                <TouchableOpacity
                  testID="sheet-save-mapping-btn"
                  onPress={saveSheet}
                  style={[styles.saveBtn, { marginTop: 12 }]}
                >
                  <Ionicons name="save" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>Save Mapping & Connect</Text>
                </TouchableOpacity>
              </View>
            )}
          </Section>

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
                  {(preview?.headers || []).map((h) => (
                    <TouchableOpacity
                      key={h}
                      style={styles.pickerItem}
                      onPress={() => {
                        if (pickerForField) {
                          setMapping((m) => ({ ...m, [pickerForField]: h }));
                        }
                        setPickerForField(null);
                      }}
                    >
                      <Text style={styles.pickerItemText}>{h}</Text>
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
                  <Ionicons name="square-outline" size={14} color={logoShape === "square" ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, logoShape === "square" && styles.segmentTextActive]}>
                    Square (1:1)
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="logo-shape-wide"
                  style={[styles.segmentBtn, logoShape === "wide" && styles.segmentBtnActive]}
                  onPress={() => setLogoShape("wide")}
                >
                  <Ionicons name="remove-outline" size={18} color={logoShape === "wide" ? "#fff" : colors.text} />
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
                    <Ionicons name="image-outline" size={28} color={colors.textMuted} />
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
                    <Ionicons name="cloud-upload-outline" size={14} color="#fff" />
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
                      <Ionicons name="trash-outline" size={14} color="#fff" />
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
                  <Ionicons name="image" size={14} color={preferLogo ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, preferLogo && styles.segmentTextActive]}>
                    Logo {!brandLogo && "(upload first)"}
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="prefer-logo-off"
                  style={[styles.segmentBtn, !preferLogo && styles.segmentBtnActive]}
                  onPress={() => setPreferLogo(false)}
                >
                  <Ionicons name="text" size={14} color={!preferLogo ? "#fff" : colors.text} />
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
                Hard cap = CONTENT_BUDGET_CAP (prevents print overlap). */}
            {(() => {
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
                    <Ionicons
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
                <Ionicons name="layers-outline" size={28} color="#9CA3AF" />
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
                    <Ionicons name="trash-outline" size={18} color="#B91C1C" />
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
                <Ionicons name="add-circle" size={18} color={colors.primary} />
                <Text style={styles.addCfBtnText}>
                  Add Custom Field ({customFields.length}/6)
                </Text>
              </TouchableOpacity>
            )}
          </Section>

          <TouchableOpacity testID="save-print-btn" style={styles.saveBtn} onPress={saveSender}>
            <Ionicons name="save" size={18} color="#fff" />
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
            <Ionicons name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Business Settings</Text>
          </TouchableOpacity>
          </>)}

          {section === "whatsapp" && (<>
          {/* Templates */}
          <Section title="Customer Messages" icon="chatbubbles-outline">
            <View style={styles.infoBox} testID="messages-info-box">
              <Ionicons name="information-circle-outline" size={16} color={colors.primary} />
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
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>ગુજરાતી Professional</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setTemplate(PRESETS.wa_english)}
                  testID="preset-wa-en"
                >
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
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
                    <Ionicons name="eye-outline" size={14} color={colors.primary} />
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
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
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
                    <Ionicons name="eye-outline" size={14} color={colors.primary} />
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
            <Ionicons name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Settings</Text>
          </TouchableOpacity>
          </>)}

          {section === "couriers" && (<>
          {/* Couriers */}
          <Section title="Courier Partners" icon="rocket-outline">
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
                <Ionicons name="chevron-forward" size={20} color={colors.textMuted} />
              </TouchableOpacity>
            ))}
            <TouchableOpacity
              testID="add-courier-btn"
              style={[styles.saveBtn, { marginTop: 10 }]}
              onPress={() => router.push("/courier/new")}
            >
              <Ionicons name="add" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Add Courier Partner</Text>
            </TouchableOpacity>
          </Section>
          </>)}

          {/* === SECTION: Notifications (placeholder) === */}
          {section === "notifications" && (<>
          <Section title="Notifications" icon="notifications-outline">
            <View style={styles.placeholderBox}>
              <Ionicons name="notifications-off-outline" size={42} color="#94A3B8" />
              <Text style={styles.placeholderTitle}>Coming soon</Text>
              <Text style={styles.placeholderSub}>
                Push notifications અને email alerts ની configuration જલ્દી ઉમેરાશે — અહીંથી તમે dispatch reminders, low credits warnings, અને daily summaries enable / disable કરી શકશો.
              </Text>
            </View>
          </Section>
          </>)}

          {/* === SECTION: About & Help === */}
          {section === "about" && (<>
          <Section title="About" icon="information-circle-outline">
            <View style={styles.aboutRow}>
              <Text style={styles.aboutKey}>App</Text>
              <Text style={styles.aboutVal}>Courier Label Manager</Text>
            </View>
            <View style={styles.aboutRow}>
              <Text style={styles.aboutKey}>Version</Text>
              <Text style={styles.aboutVal}>1.0.0</Text>
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
              onPress={() => Linking.openURL("mailto:support@example.com?subject=Courier%20Label%20Manager%20Support")}
            >
              <Ionicons name="mail-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Contact Support</Text>
              <Ionicons name="open-outline" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-whats-new"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("What's New", "• Modular Settings UI\n• Smart Paste Chat-style flow\n• Edit Shipment\n• Bulk Print 2-step popup")}
            >
              <Ionicons name="sparkles-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>What's New</Text>
              <Ionicons name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-rate-app"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("Thanks!", "Rating sheet will open when published to stores.")}
            >
              <Ionicons name="star-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Rate this App</Text>
              <Ionicons name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
          </Section>

          <Section title="Legal" icon="shield-checkmark-outline">
            <TouchableOpacity
              testID="about-privacy"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("Privacy Policy", "Your data stays in your account. We don't sell or share with third parties.")}
            >
              <Ionicons name="lock-closed-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Privacy Policy</Text>
              <Ionicons name="chevron-forward" size={16} color="#94A3B8" />
            </TouchableOpacity>
            <TouchableOpacity
              testID="about-terms"
              style={styles.aboutLinkRow}
              onPress={() => Alert.alert("Terms of Use", "By using this app you agree to our terms. AI features consume credits per the rate-card in Plan & Billing.")}
            >
              <Ionicons name="document-text-outline" size={18} color={colors.primary} />
              <Text style={styles.aboutLinkText}>Terms of Use</Text>
              <Ionicons name="chevron-forward" size={16} color="#94A3B8" />
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
              <Ionicons name="warning" size={26} color="#D97706" />
            </View>
            <Text style={styles.unsavedTitle}>Unsaved changes</Text>
            <Text style={styles.unsavedBody}>
              તમે કેટલાક ફેરફાર કર્યા છે પણ હજી save નથી કર્યા. શું કરવા માંગો છો?
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
                  <Ionicons name="save" size={16} color="#fff" />
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
              <Ionicons name="trash-outline" size={16} color="#B91C1C" />
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
}: { title: string; icon: keyof typeof Ionicons.glyphMap; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Ionicons name={icon} size={16} color={colors.primary} />
        <Text style={styles.sectionTitle}>{title}</Text>
      </View>
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
    borderRadius: 999,
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
