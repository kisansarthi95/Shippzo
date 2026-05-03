import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Alert,
  Modal,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  useWindowDimensions,
  Animated,
  PanResponder,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import * as ImagePicker from "expo-image-picker";
import * as ImageManipulator from "expo-image-manipulator";
import { Api, Shipment } from "../../lib/api";
import { colors } from "../../lib/theme";
import UsageMeter from "../../components/UsageMeter";
import HomeAlerts from "../../components/HomeAlerts";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";
import SmartCycleButton from "../../components/SmartCycleButton";
import { useFeatureFlag } from "../../lib/feature_flags";

type Stats = {
  total: number;
  delivered: number;
  pending: number;
  cod_total: number;
  cod_count: number;
  prepaid_total: number;
  prepaid_count: number;
  revenue_total: number;
};

export default function Dashboard() {
  const router = useRouter();
  const { width: screenWidth, height: screenHeight } = useWindowDimensions();
  // 3-column stat grid: 16px horizontal padding + 10px × 2 gaps
  const cardW = Math.floor((screenWidth - 32 - 20) / 3);

  // ────────────────────────────────────────────────────────────
  // Smart Paste bottom-sheet drag logic.
  //   • Initial open height = 75% of screen.
  //   • Drag UP → grows up to 100% (full screen).
  //   • Drag DOWN → snaps back to 75% (cannot dismiss by drag).
  //   • Only the X button closes the sheet.
  //   • Keyboard auto-adjust handled by KeyboardAvoidingView wrapper.
  // ────────────────────────────────────────────────────────────
  const sheetMinH = Math.floor(screenHeight * 0.75);
  const sheetMaxH = screenHeight;
  const sheetHeight = useRef(new Animated.Value(sheetMinH)).current;
  const sheetCurH = useRef(sheetMinH);
  React.useEffect(() => {
    // Re-snap sheet to new minimum on rotation / window resize.
    sheetCurH.current = sheetMinH;
    sheetHeight.setValue(sheetMinH);
  }, [sheetMinH, sheetHeight]);
  const sheetPan = useRef(
    PanResponder.create({
      onMoveShouldSetPanResponder: (_, g) => Math.abs(g.dy) > 4,
      onPanResponderMove: (_, g) => {
        // dy < 0 → finger moved up → grow sheet.
        const next = Math.max(sheetMinH, Math.min(sheetMaxH, sheetCurH.current - g.dy));
        sheetHeight.setValue(next);
      },
      onPanResponderRelease: (_, g) => {
        const candidate = Math.max(
          sheetMinH,
          Math.min(sheetMaxH, sheetCurH.current - g.dy),
        );
        // Snap to either min (75%) or max (100%) based on midpoint.
        const mid = (sheetMinH + sheetMaxH) / 2;
        const target = candidate >= mid ? sheetMaxH : sheetMinH;
        sheetCurH.current = target;
        Animated.spring(sheetHeight, {
          toValue: target,
          useNativeDriver: false,
          friction: 10,
          tension: 60,
        }).start();
      },
    }),
  ).current;
  // Reset to 75% whenever the sheet (re-)opens.
  const resetSheetHeight = useCallback(() => {
    sheetCurH.current = sheetMinH;
    sheetHeight.setValue(sheetMinH);
  }, [sheetMinH, sheetHeight]);

  const [stats, setStats] = useState<Stats | null>(null);
  const [pendingOrdersCount, setPendingOrdersCount] = useState<number>(0);
  const [recent, setRecent] = useState<Shipment[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, list, oc] = await Promise.all([
        Api.getStats(),
        Api.listShipments({}),
        Api.pendingOrdersCount().catch(() => ({ count: 0 })),
      ]);
      setStats(s);
      setRecent(list.slice(0, 5));
      setPendingOrdersCount(oc?.count ?? 0);
    } catch {
      // ignore
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load().catch(() => {});
    }, [load])
  );

  const onRefresh = () => {
    setRefreshing(true);
    load().catch(() => {});
  };

  // Smart Paste — Phase-7 hard reset.
  // Entry sheet has ONLY 2 buttons: Paste Text / Upload Photo.
  // After processing, Summary Card opens with parsed fields.
  const [pasteModalOpen, setPasteModalOpen] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [pasteStage, setPasteStage] = useState<"" | "parsing" | "saving">("");
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [photoUploading, setPhotoUploading] = useState(false);
  // Phase-12: Granular photo upload progress so the UI can show
  // "Compressing..." → "Reading..." instead of a flat 5-20 sec spinner.
  const [photoStage, setPhotoStage] = useState<
    "" | "compressing" | "reading"
  >("");

  // Summary Card state (the modal that lets the user review/edit fields).
  const [chatOpen, setChatOpen] = useState(false);
  const [chatFields, setChatFields] = useState<Record<string, any>>({});
  const [chatComplexity, setChatComplexity] = useState<"simple" | "medium" | "complex" | "">("");
  const [chatReason, setChatReason] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [suggestedCustomer, setSuggestedCustomer] = useState<any | null>(null);
  const [dupFound, setDupFound] = useState<any[]>([]);

  // Phase-15: City → State + Pincode auto-fill. When the user types
  // (or pastes) a city in the Summary Card and we have no pincode yet,
  // hit /lookup/by-city, auto-apply the state when confidence is high
  // and surface tappable pincode chips below the row. Saves the user
  // from typing 6 digits manually.
  type CityHint = {
    city: string;       // the lookup key — used to ignore stale responses
    state: string;
    confidence: "high" | "medium" | "low";
    suggestions: Array<{ pincode: string; office: string; district: string; state: string }>;
    loading: boolean;
  };
  const [cityHint, setCityHint] = useState<CityHint | null>(null);
  // Track the auto-applied state so we can show a "Confirmed ✓" pill
  // and let the user revert with one tap.
  const [autoState, setAutoState] = useState<{ city: string; state: string } | null>(null);

  // Per-user Custom Fields (plan-gated). Loaded once on mount.
  // Surfaced inline in the Smart Paste Summary modal so high-volume
  // users can fill their bespoke columns without jumping to the
  // manual New Shipment form. Map: { [custom_field_id]: stringValue }.
  const [userCustomFields, setUserCustomFields] = useState<
    Array<{
      id: string;
      name: string;
      column_letter: string;
      field_type: "text" | "number" | "date";
      show_in_smart_paste?: boolean;
      required?: boolean;
    }>
  >([]);
  const [userCustomValues, setUserCustomValues] = useState<Record<string, string>>({});

  // Phase-8: per-field "Required" toggles loaded from /settings.
  // Maps snake_case shipment key → bool. Empty until settings load
  // — until then we fall back to the legacy hardcoded REQ list.
  const [fieldReqs, setFieldReqs] = useState<Record<string, boolean>>({});

  useEffect(() => {
    // Best-effort: silently no-op on auth/quota errors. Custom fields
    // are only rendered if the user has at least one defined AND it has
    // `show_in_smart_paste` (defaults true on creation).
    Api.listMyCustomFields()
      .then((res) => {
        if (Array.isArray(res?.fields)) {
          setUserCustomFields(
            res.fields
              .filter(
                (f: any) =>
                  f &&
                  (f.active ?? true) &&
                  (f.show_in_smart_paste ?? true),
              )
              .map((f: any) => ({
                id: f.id,
                name: f.name,
                column_letter: f.column_letter,
                field_type: f.field_type || "text",
                show_in_smart_paste: f.show_in_smart_paste ?? true,
                required: !!f.required,
              })),
          );
        } else {
          setUserCustomFields([]);
        }
      })
      .catch(() => setUserCustomFields([]));
    // Phase-8: load per-field required toggles.
    Api.getSettings()
      .then((s: any) => {
        const fr = (s?.field_requirements || {}) as Record<string, boolean>;
        setFieldReqs(fr);
      })
      .catch(() => setFieldReqs({}));
  }, []);

  // Plan-gated: hide duplicate-banner UI when admin disables this feature.
  const flagDupCheck = useFeatureFlag("smart_paste_duplicate_check");
  const flagRepeatBanner = useFeatureFlag("repeat_customer_banner");

  // Phase-15: debounced city → state/pincode lookup. Triggers ~600 ms
  // after the user stops typing in the City field, only when:
  //   * the Summary Card is open (chatOpen),
  //   * the city has at least 3 chars,
  //   * pincode is empty (or invalid) — i.e. the lookup actually
  //     adds value; we don't waste an API call when the user already
  //     has a valid 6-digit pincode.
  // The state setter ignores stale responses (out-of-order resolves)
  // by checking the city name on the response against the field at
  // resolve time.
  useEffect(() => {
    if (!chatOpen) return;
    const cityRaw = String((chatFields as any).city ?? "").trim();
    const pinRaw  = String((chatFields as any).pincode ?? "").trim();
    if (cityRaw.length < 3) {
      setCityHint(null);
      return;
    }
    // If we already have a valid 6-digit pincode, skip the city lookup —
    // the existing pincode→state path is more authoritative.
    if (/^\d{6}$/.test(pinRaw)) {
      return;
    }
    const handle = setTimeout(async () => {
      setCityHint({
        city: cityRaw, state: "", confidence: "low",
        suggestions: [], loading: true,
      });
      try {
        const r = await Api.lookupByCity(cityRaw);
        // Drop stale: only commit if the city the user has now still
        // matches what we asked for.
        const stillCity = String((chatFields as any).city ?? "").trim();
        if (stillCity !== cityRaw) return;
        setCityHint({
          city: cityRaw,
          state: r.state || "",
          confidence: r.state_confidence,
          suggestions: r.suggestions || [],
          loading: false,
        });
        // Auto-apply state when we're confident AND user hasn't typed
        // a different state already. Record the auto-application so
        // the UI can show a "Confirmed ✓" pill and let them undo it.
        if (
          r.state &&
          r.state_confidence === "high" &&
          !String((chatFields as any).state ?? "").trim()
        ) {
          setChatFields((p) => ({ ...p, state: r.state }));
          setAutoState({ city: cityRaw, state: r.state });
        }
      } catch {
        setCityHint(null);
      }
    }, 600);
    return () => clearTimeout(handle);
  }, [chatFields, chatOpen]);

  // When the user wipes the city field, also clear the auto-applied
  // state — otherwise stale "Confirmed ✓" hints would linger.
  useEffect(() => {
    const c = String((chatFields as any).city ?? "").trim();
    if (autoState && autoState.city !== c) {
      setAutoState(null);
    }
  }, [chatFields, autoState]);


  // Human-readable labels + placeholders for each schema field.
  const FIELD_META: Record<
    string,
    { label: string; placeholder: string; keyboard?: "default" | "phone-pad" | "numeric"; primary?: boolean }
  > = {
    NAME:      { label: "Customer Name",    placeholder: "e.g. Ramesh Patel", primary: true },
    PHONE:     { label: "Mobile Number",    placeholder: "10-digit mobile", keyboard: "phone-pad", primary: true },
    ALT_PHONE: { label: "Alternate Mobile", placeholder: "10-digit (optional)", keyboard: "phone-pad" },
    ADDRESS_1: { label: "Address",          placeholder: "House / street / area", primary: true },
    ADDRESS_2: { label: "Address Line 2",   placeholder: "Landmark / optional" },
    CITY:      { label: "City",             placeholder: "e.g. Ahmedabad", primary: true },
    STATE:     { label: "State",            placeholder: "e.g. Gujarat", primary: true },
    PINCODE:   { label: "Pincode",          placeholder: "6 digits", keyboard: "numeric", primary: true },
    ITEMS:     { label: "Item(s)",          placeholder: "e.g. Saree x 2", primary: true },
    AMOUNT:    { label: "Amount (₹)",       placeholder: "Enter amount", keyboard: "numeric", primary: true },
    PAYMENT:   { label: "Payment",          placeholder: "COD or Prepaid", primary: true },
    TOKEN:     { label: "Token Amount (₹)", placeholder: "Enter token", keyboard: "numeric" },
    COURIER:   { label: "Courier",          placeholder: "optional" },
    ORDER_ID:  { label: "Your Order ID",    placeholder: "ABC-001 / your own ID (optional)" },
    WEIGHT:    { label: "Weight (g)",       placeholder: "Enter weight in grams", keyboard: "numeric" },
    NOTES:     { label: "Notes",            placeholder: "special instructions" },
  };

  // Field order in the canonical paste-text payload.
  const FIELD_ORDER = [
    "NAME", "PHONE", "ALT_PHONE", "ADDRESS_1", "ADDRESS_2", "CITY", "STATE", "PINCODE",
    "ITEMS", "AMOUNT", "PAYMENT", "TOKEN",
    "COURIER", "ORDER_ID", "WEIGHT", "NOTES",
  ];

  // Required fields (always blocking). TOKEN is conditionally required
  // when payment_mode === COD and is enforced separately at save time.
  const REQUIRED_FIELDS = [
    "NAME", "PHONE", "ADDRESS_1", "CITY", "STATE", "PINCODE", "AMOUNT", "WEIGHT",
  ];

  // Map from backend (snake_case / shipment schema) → UI schema (UPPER).
  const fromLegacy = (legacy: any): Record<string, string> => {
    const items = legacy?.items;
    const itemsText = Array.isArray(items) ? items.join(", ") : String(items ?? "");
    return {
      NAME: legacy?.customer_name || "",
      PHONE: legacy?.customer_phone || "",
      ADDRESS_1: legacy?.address_line1 || "",
      ADDRESS_2: legacy?.address_line2 || "",
      CITY: legacy?.city || "",
      STATE: legacy?.state || "",
      PINCODE: legacy?.pincode || "",
      ITEMS: itemsText,
      AMOUNT: legacy?.amount != null && legacy.amount !== "" ? String(legacy.amount) : "",
      PAYMENT: (legacy?.payment_mode || "").toUpperCase(),
      COURIER: legacy?.courier_name || "",
      ORDER_ID: legacy?.order_id || "",
      WEIGHT: legacy?.weight || "",
      NOTES: legacy?.notes || "",
    };
  };

  const handleSmartPaste = async () => {
    // Phase-7 hard reset: open ONLY the 2-button entry sheet
    // (Paste Text / Upload Photo). NO clipboard auto-load, NO tabs,
    // NO textarea. Each button below directly processes input and
    // jumps to the Summary Card — no intermediate UI.
    setPhotoUri(null);
    resetSheetHeight();
    setPasteModalOpen(true);
  };

  /** "Paste Text" button → read clipboard → process → Summary Card. */
  const handlePasteTextChosen = async () => {
    if (pasting) return;
    let text = "";
    try {
      text = (await Clipboard.getStringAsync()) || "";
    } catch {
      text = "";
    }
    text = text.trim();
    if (!text) {
      Alert.alert(
        "Clipboard empty",
        "Copy your order text (WhatsApp / SMS) first, then tap Paste Text again.",
      );
      return;
    }
    // Close entry sheet, then run AI parse → Summary Card.
    setPasteModalOpen(false);
    await runSmartPasteAI(text, false);
  };

  /** "Upload Photo" button → ask Camera vs Gallery → process → Summary Card. */
  const handlePhotoChosen = () => {
    if (photoUploading) return;
    Alert.alert(
      "Upload Photo",
      "Where should we read the address from?",
      [
        { text: "Camera",  onPress: () => pickAndProcessPhoto("camera") },
        { text: "Gallery", onPress: () => pickAndProcessPhoto("gallery") },
        { text: "Cancel",  style: "cancel" },
      ],
      { cancelable: true },
    );
  };

  /**
   * Photo Smart Paste — runs Gemini Vision on a base64 image and feeds
   * the parsed fields into the SAME chat flow the text path uses.
   *
   *   1. Pick from camera or gallery (caller supplies the source).
   *   2. POST to /smart-paste/photo with base64 + mime.
   *   3. If complete → save silently. Else → open chat modal.
   *   4. Cost: 2 credits (complex tier). Trial users get 10 free credits
   *      on signup so they can try it ~5 times.
   */
  const pickAndProcessPhoto = async (source: "camera" | "gallery") => {
    try {
      let result: ImagePicker.ImagePickerResult;
      if (source === "camera") {
        const perm = await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) {
          Alert.alert(
            "Camera permission needed",
            "Please allow camera access to scan addresses from photos.",
          );
          return;
        }
        result = await ImagePicker.launchCameraAsync({
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
          allowsEditing: false,
          // Capture at high quality — we'll downsize in compression step.
          // Picker quality only affects JPEG re-encode if base64 was
          // requested upfront; we skip base64 here and re-encode after.
          quality: 1,
          base64: false,
          exif: false,
        });
      } else {
        const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) {
          Alert.alert(
            "Gallery permission needed",
            "Please allow gallery access to pick a photo.",
          );
          return;
        }
        result = await ImagePicker.launchImageLibraryAsync({
          mediaTypes: ImagePicker.MediaTypeOptions.Images,
          allowsEditing: false,
          quality: 1,
          base64: false,
          exif: false,
        });
      }
      if (result.canceled || !result.assets || result.assets.length === 0) {
        return;
      }
      const asset = result.assets[0];
      if (!asset.uri) {
        Alert.alert("Photo error", "Could not read the selected image.");
        return;
      }
      setPhotoUri(asset.uri);
      setPhotoUploading(true);
      setPhotoStage("compressing");

      // ── Phase-12 OPTIMISATION: client-side compression ────────────
      // Resize to max 1600px on the longest edge + JPEG 75% quality.
      // Typical 3-5 MB camera photos shrink to 300-600 KB, cutting
      // upload time by 4-8 seconds on slow networks while preserving
      // OCR accuracy for SMALL TEXT like visiting-card addresses
      // (1600px keeps 10-point text crisp; 1280px was slightly soft
      // for dense cards). Also re-encodes HEIC/PNG to JPEG so the
      // backend always sees image/jpeg.
      let compressedUri = asset.uri;
      let compressedB64 = "";
      try {
        const manipulated = await ImageManipulator.manipulateAsync(
          asset.uri,
          [{ resize: { width: 1600 } }],
          {
            compress: 0.75,
            format: ImageManipulator.SaveFormat.JPEG,
            base64: true,
          },
        );
        compressedUri = manipulated.uri;
        compressedB64 = manipulated.base64 || "";
      } catch (compErr) {
        // Compression failed (rare) — fall back to original picker
        // base64 by re-reading once with quality 0.6.
        try {
          const fallback = await ImageManipulator.manipulateAsync(
            asset.uri,
            [],
            {
              compress: 0.6,
              format: ImageManipulator.SaveFormat.JPEG,
              base64: true,
            },
          );
          compressedB64 = fallback.base64 || "";
        } catch {
          // Last-ditch: bail with a clear error.
          setPhotoUploading(false);
          setPhotoStage("");
          setPhotoUri(null);
          Alert.alert(
            "Photo error",
            "Could not process the selected image. Please try a smaller photo.",
          );
          return;
        }
      }

      if (!compressedB64) {
        setPhotoUploading(false);
        setPhotoStage("");
        setPhotoUri(null);
        Alert.alert("Photo error", "Could not read the selected image.");
        return;
      }

      // After compression we always re-encode as JPEG.
      setPhotoStage("reading");
      const resp = await Api.smartPastePhoto(compressedB64, "image/jpeg");
      setPhotoUploading(false);
      setPhotoStage("");
      setPhotoUri(null);
      setPasteModalOpen(false);

      // Same merge logic as runSmartPasteAI:
      const legacyFields = resp.fields || {};

      // Always open Summary Card so user verifies before saving.
      setChatFields(legacyFields);
      setChatComplexity((resp.complexity as any) || "complex");
      setChatReason(resp.reason || "photo OCR");
      setDupFound([]);
      setSuggestedCustomer(null);
      resetSheetHeight();
      setChatOpen(true);

      const phone = (legacyFields.customer_phone || "").toString();
      if (phone && phone.replace(/\D/g, "").length >= 10) {
        Api.lookupCustomerByPhone(phone)
          .then((r) => {
            if (r.found && r.customer) {
              setSuggestedCustomer({ ...r.customer, _count: r.count });
            }
          })
          .catch(() => {});
      }
    } catch (e: any) {
      setPhotoUploading(false);
      setPhotoStage("");
      setPhotoUri(null);
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        "Could not read the photo. Try a brighter, clearer shot.";
      Alert.alert("Photo upload failed", msg);
    }
  };

  /**
   * End-to-end Smart Paste flow.
   *   1. /smart-paste/check-duplicate parses the text.
   *   2. ALWAYS open Summary Card so user verifies before saving.
   */
  const runSmartPasteAI = async (text: string, _fromModal = false) => {
    try {
      setPasting(true);
      setPasteStage("parsing");
      const dup = await Api.smartPasteCheckDuplicate(text);

      const legacyFields = dup.fields || {};
      setPasting(false);
      setPasteStage("");

      // Always open the Summary Card — never auto-save.
      setChatFields(legacyFields);
      setChatComplexity((dup.ai?.complexity as any) || "");
      setChatReason(dup.ai?.reason || "");
      // Plan-gate: if duplicate-detection is OFF for this user's plan,
      // never expose the duplicate banner even if the backend returns it.
      setDupFound(flagDupCheck ? (dup.duplicates || []) : []);
      setSuggestedCustomer(null);
      resetSheetHeight();
      setChatOpen(true);

      // Background phone lookup for repeat-customer suggestion.
      const phone = (legacyFields.customer_phone || "").toString();
      if (phone && phone.replace(/\D/g, "").length >= 10) {
        Api.lookupCustomerByPhone(phone)
          .then((r) => {
            if (r.found && r.customer) {
              setSuggestedCustomer({ ...r.customer, _count: r.count });
            }
          })
          .catch(() => {});
      }
    } catch (e: any) {
      setPasting(false);
      setPasteStage("");
      Alert.alert(
        "Smart Paste failed",
        e?.response?.data?.detail || e?.message || "Please try again."
      );
    }
  };

  const applySuggestedCustomer = () => {
    if (!suggestedCustomer) return;

    // Compute the address-only fill payload up-front. We may apply it
    // immediately (no past items) or wait for the user's items decision.
    const addressOnlyUpdate = {
      ...chatFields,
      customer_name: suggestedCustomer.customer_name || chatFields.customer_name,
      customer_phone: suggestedCustomer.customer_phone || chatFields.customer_phone,
      address_line1: suggestedCustomer.address_line1 || chatFields.address_line1,
      address_line2: suggestedCustomer.address_line2 || chatFields.address_line2,
      city: suggestedCustomer.city || chatFields.city,
      state: suggestedCustomer.state || chatFields.state,
      pincode: suggestedCustomer.pincode || chatFields.pincode,
    };

    // Helper to commit the fill (with or without past items) and continue.
    const commit = (
      updated: Record<string, any>,
      addedItemsLabel?: string,
    ) => {
      setChatFields(updated);
      setSuggestedCustomer(null);
      if (addedItemsLabel) {
        // Optional toast — silently keep user in the Summary Card.
      }
    };

    // Repeat-customer dialog: if the past order had items, ask whether
    // to copy them over too. Skip the dialog when no past items exist
    // OR when the user has already typed items in this draft.
    const pastItems = Array.isArray(suggestedCustomer.last_items)
      ? suggestedCustomer.last_items
      : [];
    const userItemsRaw = (chatFields.items || chatFields.item || "") as string;
    const userTypedItems = String(userItemsRaw).trim().length > 0;

    if (pastItems.length === 0 || userTypedItems) {
      commit(addressOnlyUpdate);
      return;
    }

    // Build a friendly preview of past items.
    const itemsPreview = pastItems
      .slice(0, 3)
      .map((it: any) =>
        typeof it === "string"
          ? it
          : `${it?.name || ""}${it?.qty ? ` × ${it.qty}` : ""}`.trim(),
      )
      .filter(Boolean)
      .join(", ");
    const more = pastItems.length > 3 ? ` +${pastItems.length - 3} more` : "";
    const lastAmt = suggestedCustomer.last_amount
      ? ` · ₹${suggestedCustomer.last_amount}`
      : "";
    const dlgMsg =
      `Last order had: ${itemsPreview}${more}${lastAmt}.\n\n` +
      `Reuse the same items, or start fresh?`;

    const reuseItems = () => {
      const itemsString = pastItems
        .map((it: any) =>
          typeof it === "string"
            ? it
            : `${it?.name || ""}${it?.qty ? ` × ${it.qty}` : ""}`.trim(),
        )
        .filter(Boolean)
        .join("\n");
      const updated = { ...addressOnlyUpdate } as Record<string, any>;
      if (itemsString) updated.items = itemsString;
      if (suggestedCustomer.last_amount && !updated.amount) {
        updated.amount = String(suggestedCustomer.last_amount);
      }
      commit(updated, ` (${pastItems.length} item${pastItems.length === 1 ? "" : "s"} copied)`);
    };
    const startFresh = () => commit(addressOnlyUpdate);

    if (Platform.OS === "web") {
      const yes =
        typeof window !== "undefined" &&
        window.confirm &&
        window.confirm(`${dlgMsg}\n\nOK = reuse items, Cancel = start fresh.`);
      if (yes) reuseItems(); else startFresh();
      return;
    }
    Alert.alert("Repeat customer", dlgMsg, [
      { text: "Start fresh", onPress: startFresh, style: "cancel" },
      { text: "Reuse items", onPress: reuseItems },
    ]);
  };

  const closeChat = () => {
    setChatOpen(false);
    setChatFields({});
    setDupFound([]);
    setSuggestedCustomer(null);
    setUserCustomValues({});
  };

  /**
   * Build a canonical 14-line KEY: value block from the final fields
   * and post it to /api/smart-paste. Backend's regex parser accepts
   * this verbatim — no wasted LLM call.
   */
  const saveFromFields = async (
    legacyFields: Record<string, any>,
    customValues?: Record<string, string>,
  ) => {
    try {
      setPasting(true);
      setChatSending(true);
      setPasteStage("saving");
      const schema = {
        NAME: legacyFields.customer_name || "",
        PHONE: legacyFields.customer_phone || "",
        ALT_PHONE: legacyFields.customer_alt_phone || "",
        ADDRESS_1: legacyFields.address_line1 || "",
        ADDRESS_2: legacyFields.address_line2 || "",
        CITY: legacyFields.city || "",
        STATE: legacyFields.state || "",
        PINCODE: legacyFields.pincode || "",
        ITEMS: Array.isArray(legacyFields.items)
          ? legacyFields.items.join(", ")
          : String(legacyFields.items || ""),
        AMOUNT:
          legacyFields.amount != null && legacyFields.amount !== ""
            ? String(legacyFields.amount)
            : "",
        PAYMENT: String(legacyFields.payment_mode || "").toUpperCase(),
        TOKEN:
          legacyFields.token_amount != null && legacyFields.token_amount !== ""
            ? String(legacyFields.token_amount)
            : "",
        COURIER: legacyFields.courier_name || "",
        ORDER_ID: legacyFields.order_id || "",
        WEIGHT: legacyFields.weight || "",
        NOTES: legacyFields.notes || "",
      } as Record<string, string>;
      const lines = FIELD_ORDER.map((k) => {
        const v = (schema[k] || "").toString().trim();
        return v ? `${k}: ${v}` : null;
      }).filter(Boolean) as string[];
      const text = lines.join("\n");
      // Trim and forward custom_values keyed by user_custom_fields.id.
      const cv: Record<string, string> = {};
      for (const [k, v] of Object.entries(customValues || {})) {
        const sv = (v ?? "").toString().trim();
        if (sv) cv[k] = sv;
      }
      const created: any = await Api.smartPasteCreate(
        text,
        true, // skip_llm = canonical fields → save 2-4s
        Object.keys(cv).length ? cv : undefined,
      );
      setPasting(false);
      setChatSending(false);
      setPasteStage("");
      // Close Summary Card immediately on success.
      closeChat();
      const masterId = created?.master_order_id || "";
      const userId = created?.order_id || "";
      const sameId = masterId && userId && masterId === userId;
      const idLine = masterId
        ? sameId
          ? `Order ID: ${masterId}`
          : `Master ID: ${masterId}\nYour ID: ${userId}`
        : userId
          ? `Order ID: ${userId}`
          : "";
      Alert.alert(
        "✅ Order added",
        (idLine ? `${idLine}\n\n` : "") +
        "Queued in Orders tab. Ready to ship.",
        [
          { text: "OK", style: "cancel" },
          { text: "View Orders →", onPress: () => router.push("/orders") },
        ]
      );
    } catch (err: any) {
      setPasting(false);
      setChatSending(false);
      setPasteStage("");
      Alert.alert(
        "Save failed",
        err?.response?.data?.detail || err?.message || "Please try again.",
      );
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <BrandHeaderAnimator />
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <SmartCycleButton
            testID="smart-paste-btn"
            onPress={handleSmartPaste}
            busy={pasting}
          />
          <TouchableOpacity
            testID="dashboard-refresh-btn"
            style={[styles.headerAction, { backgroundColor: colors.primary }]}
            onPress={() => { setRefreshing(true); load(); }}
          >
            <Ionicons name="refresh" size={20} color="#fff" />
          </TouchableOpacity>
          <TouchableOpacity
            testID="open-scanner-btn"
            style={styles.headerAction}
            onPress={() => router.push("/scanner?returnTo=add")}
          >
            <Ionicons name="scan" size={22} color="#fff" />
          </TouchableOpacity>
        </View>
      </View>

      {/* ────────────────────────────────────────────────────────────
         Smart Paste — Entry Sheet.
         Phase-7 hard reset: ONLY 2 buttons (Paste Text / Upload Photo).
         No tabs, no textarea, no clipboard preview. Each button directly
         processes input → opens Summary Card. NO intermediate UI.
         ──────────────────────────────────────────────────────────── */}
      <Modal
        visible={pasteModalOpen}
        animationType="slide"
        transparent
        onRequestClose={() => {
          if (!pasting && !photoUploading) setPasteModalOpen(false);
        }}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalOverlay}
        >
          <Animated.View style={[styles.sheetCard, { height: sheetHeight }]}>
            <View {...sheetPan.panHandlers} style={styles.sheetGrabArea}>
              <View style={styles.sheetGrabBar} />
            </View>
            <View style={styles.modalHeader}>
              <Ionicons name="sparkles" size={18} color="#7C3AED" />
              <Text style={styles.modalTitle}>Smart Paste</Text>
              <TouchableOpacity
                onPress={() => setPasteModalOpen(false)}
                hitSlop={10}
                disabled={pasting || photoUploading}
              >
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>

            <ScrollView
              style={{ flex: 1 }}
              contentContainerStyle={{ padding: 14, paddingBottom: 24 }}
              keyboardShouldPersistTaps="handled"
            >
              {pasting || photoUploading ? (
                <View style={styles.entryBusyCard}>
                  <ActivityIndicator size="large" color="#7C3AED" />
                  <Text style={styles.entryBusyTxt}>
                    {photoUploading
                      ? photoStage === "compressing"
                        ? "Optimising photo…"
                        : photoStage === "reading"
                        ? "Reading the photo… (3–6 sec)"
                        : "Reading the photo…"
                      : "Processing…"}
                  </Text>
                  {photoUploading && photoStage === "reading" && (
                    <Text style={styles.entryBusySub}>
                      Powered by Gemini Flash
                    </Text>
                  )}
                </View>
              ) : (
                <View style={styles.entryBtnCol}>
                  <TouchableOpacity
                    testID="smart-paste-paste-text-btn"
                    onPress={handlePasteTextChosen}
                    style={styles.entryBigBtn}
                    activeOpacity={0.85}
                  >
                    <View style={styles.entryBigBtnIcon}>
                      <Ionicons name="clipboard-outline" size={26} color="#7C3AED" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.entryBigBtnTitle}>📋  Paste Text</Text>
                      <Text style={styles.entryBigBtnSub}>
                        Copy WhatsApp / SMS first, then tap here.
                      </Text>
                    </View>
                    <Ionicons name="chevron-forward" size={22} color="#7C3AED" />
                  </TouchableOpacity>

                  <TouchableOpacity
                    testID="smart-paste-upload-photo-btn"
                    onPress={handlePhotoChosen}
                    style={styles.entryBigBtn}
                    activeOpacity={0.85}
                  >
                    <View style={styles.entryBigBtnIcon}>
                      <Ionicons name="camera-outline" size={26} color="#7C3AED" />
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.entryBigBtnTitle}>📷  Upload Photo</Text>
                      <Text style={styles.entryBigBtnSub}>
                        Camera or Gallery — reads Gujarati / Hindi / English.
                      </Text>
                    </View>
                    <Ionicons name="chevron-forward" size={22} color="#7C3AED" />
                  </TouchableOpacity>
                </View>
              )}
            </ScrollView>
          </Animated.View>
        </KeyboardAvoidingView>
      </Modal>

      {/* ────────────────────────────────────────────────────────────
         Smart Paste — Summary Card.
         Replaces the previous chat-bubble UI per 2026-04-28 product
         requirement. The whole flow is now non-AI-feeling:
           • Step 1 (input) lives in the Smart Paste tabs above.
           • Step 2 (this card) shows one row per field with either a
             green tick (filled) or an inline editor (missing).
           • No chat history, no "AI is thinking" copy — just a tool.
         The same `chatOpen` / `chatFields` state powers it; we only
         renamed the rendered surface.
         ──────────────────────────────────────────────────────────── */}
      <Modal
        visible={chatOpen}
        animationType="slide"
        transparent
        onRequestClose={closeChat}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalOverlay}
        >
          <Animated.View style={[styles.sheetCard, { height: sheetHeight }]}>
            <View {...sheetPan.panHandlers} style={styles.sheetGrabArea}>
              <View style={styles.sheetGrabBar} />
            </View>
            <View style={styles.modalHeader}>
              <Ionicons name="sparkles-outline" size={18} color="#7C3AED" />
              <Text style={styles.modalTitle}>Smart Paste</Text>
              <TouchableOpacity onPress={closeChat} hitSlop={10} disabled={chatSending}>
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>

            {/* Repeat-customer / duplicate-shipment banner */}
            {!!suggestedCustomer && flagRepeatBanner && (
              <View style={styles.dupBanner}>
                <Ionicons name="person-circle-outline" size={16} color="#1E40AF" />
                <Text style={styles.dupBannerTxt} numberOfLines={2}>
                  Repeat customer: {suggestedCustomer?.customer_name || ""}
                  {suggestedCustomer?._count ? `  (${suggestedCustomer._count} prev)` : ""}
                </Text>
                <TouchableOpacity
                  testID="apply-repeat-customer"
                  onPress={applySuggestedCustomer}
                  style={styles.dupBannerBtn}
                >
                  <Text style={styles.dupBannerBtnTxt}>Use</Text>
                </TouchableOpacity>
              </View>
            )}
            {dupFound.length > 0 && (
              <View style={[styles.dupBanner, { backgroundColor: "#FEF3C7", borderColor: "#FCD34D" }]}>
                <Ionicons name="alert-circle-outline" size={16} color="#92400E" />
                <Text style={[styles.dupBannerTxt, { color: "#78350F" }]} numberOfLines={2}>
                  Possible duplicate shipment — review before saving.
                </Text>
              </View>
            )}

            <ScrollView
              testID="smart-paste-summary"
              keyboardShouldPersistTaps="handled"
              style={{ flex: 1 }}
              contentContainerStyle={{ padding: 14, paddingBottom: 20 }}
            >
              {(() => {
                // Render each schema field as a "row card". Required
                // fields with an empty value are inline-editable; filled
                // fields show a green tick. Tap any filled value to
                // edit it as well (toggle via editingKey).
                //
                // PAYMENT renders a 2-button toggle (COD / Prepaid).
                // TOKEN appears only when PAYMENT === "COD" and is required.
                // Map schema-key ↔ legacy field name (used by inline edit binding).
                const SNAKE: Record<string, string> = {
                  NAME: "customer_name",
                  PHONE: "customer_phone",
                  ALT_PHONE: "customer_alt_phone",
                  ADDRESS_1: "address_line1",
                  CITY: "city",
                  STATE: "state",
                  PINCODE: "pincode",
                  WEIGHT: "weight",
                  AMOUNT: "amount",
                  ITEMS: "items",
                  PAYMENT: "payment_mode",
                  TOKEN: "token_amount",
                  COURIER: "courier_name",
                  ORDER_ID: "order_id",
                  NOTES: "notes",
                };
                // Phase-8: Build REQ / OPT lists dynamically from
                // settings.field_requirements, falling back to the
                // legacy hardcoded defaults until settings load.
                const HARDCODED_DEFAULTS: Record<string, boolean> = {
                  customer_name: true, customer_phone: true,
                  address_line1: true, city: true, state: true,
                  pincode: true, amount: true, payment_mode: true,
                  weight: true, customer_alt_phone: false, items: false,
                  courier_name: false, order_id: false, notes: false,
                  token_amount: false,
                };
                const isReqKey = (snake: string) => {
                  if (snake in fieldReqs) return !!fieldReqs[snake];
                  return !!HARDCODED_DEFAULTS[snake];
                };
                const ALL_KEYS = [
                  "NAME", "PHONE", "ALT_PHONE", "ADDRESS_1", "CITY",
                  "STATE", "PINCODE", "ITEMS", "AMOUNT", "PAYMENT",
                  "WEIGHT", "COURIER", "ORDER_ID", "NOTES",
                ];
                const REQ: string[] = [];
                const OPT: string[] = [];
                for (const k of ALL_KEYS) {
                  if (isReqKey(SNAKE[k])) REQ.push(k);
                  else OPT.push(k);
                }

                // Normalise current payment value → "COD" | "PREPAID" | "".
                const rawPay = String((chatFields as any).payment_mode || "")
                  .trim()
                  .toUpperCase();
                const payNorm =
                  rawPay === "COD"
                    ? "COD"
                    : rawPay === "PREPAID" || rawPay === "PAID"
                      ? "PREPAID"
                      : "";
                const isCOD = payNorm === "COD";

                /** Renders a custom row for the PAYMENT field — 2-button toggle. */
                const renderPaymentRow = (isReq: boolean) => {
                  const meta = FIELD_META["PAYMENT"];
                  const isMissing = !payNorm;
                  return (
                    <View
                      key="PAYMENT"
                      style={[
                        styles.spRow,
                        isMissing && isReq ? styles.spRowMissing : null,
                      ]}
                      testID="smart-paste-row-payment_mode"
                    >
                      <View style={styles.spRowLeft}>
                        {isMissing ? (
                          <Ionicons
                            name={isReq ? "alert-circle" : "ellipse-outline"}
                            size={16}
                            color={isReq ? "#DC2626" : "#94A3B8"}
                          />
                        ) : (
                          <Ionicons name="checkmark-circle" size={16} color="#16A34A" />
                        )}
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.spRowLabel}>
                          {meta.label}
                          {isReq ? <Text style={{ color: "#DC2626" }}>  *</Text> : null}
                        </Text>
                        <View style={styles.payToggleRow}>
                          <TouchableOpacity
                            testID="payment-toggle-cod"
                            onPress={() =>
                              setChatFields((p) => ({ ...p, payment_mode: "COD" }))
                            }
                            style={[
                              styles.payToggleBtn,
                              payNorm === "COD" && styles.payToggleBtnActive,
                            ]}
                            activeOpacity={0.85}
                          >
                            <Ionicons
                              name="cash-outline"
                              size={14}
                              color={payNorm === "COD" ? "#fff" : "#7C3AED"}
                            />
                            <Text
                              style={[
                                styles.payToggleTxt,
                                payNorm === "COD" && styles.payToggleTxtActive,
                              ]}
                            >
                              COD
                            </Text>
                          </TouchableOpacity>
                          <TouchableOpacity
                            testID="payment-toggle-prepaid"
                            onPress={() =>
                              setChatFields((p) => ({
                                ...p,
                                payment_mode: "PREPAID",
                                token_amount: "",
                              }))
                            }
                            style={[
                              styles.payToggleBtn,
                              payNorm === "PREPAID" && styles.payToggleBtnActive,
                            ]}
                            activeOpacity={0.85}
                          >
                            <Ionicons
                              name="card-outline"
                              size={14}
                              color={payNorm === "PREPAID" ? "#fff" : "#7C3AED"}
                            />
                            <Text
                              style={[
                                styles.payToggleTxt,
                                payNorm === "PREPAID" && styles.payToggleTxtActive,
                              ]}
                            >
                              Prepaid
                            </Text>
                          </TouchableOpacity>
                        </View>
                      </View>
                    </View>
                  );
                };

                const renderRow = (key: string, isReq: boolean) => {
                  if (key === "PAYMENT") return renderPaymentRow(isReq);
                  const meta = FIELD_META[key] || { label: key, placeholder: "" };
                  const sk = SNAKE[key];
                  const val = String((chatFields as any)[sk] ?? "").trim();
                  const isMissing = !val;
                  return (
                    <View
                      key={key}
                      style={[
                        styles.spRow,
                        isMissing && isReq ? styles.spRowMissing : null,
                      ]}
                      testID={`smart-paste-row-${sk}`}
                    >
                      <View style={styles.spRowLeft}>
                        {isMissing ? (
                          <Ionicons
                            name={isReq ? "alert-circle" : "ellipse-outline"}
                            size={16}
                            color={isReq ? "#DC2626" : "#94A3B8"}
                          />
                        ) : (
                          <Ionicons name="checkmark-circle" size={16} color="#16A34A" />
                        )}
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.spRowLabel}>
                          {meta.label}
                          {isReq ? <Text style={{ color: "#DC2626" }}>  *</Text> : null}
                        </Text>
                        <TextInput
                          testID={`smart-paste-input-${sk}`}
                          style={[
                            styles.spRowInput,
                            isMissing && isReq ? { borderColor: "#FCA5A5" } : null,
                          ]}
                          value={val}
                          onChangeText={(t) => {
                            // Phase-6 single-address-field cap.
                            let next = t;
                            if (key === "ADDRESS_1" && t.length > 300) next = t.slice(0, 300);
                            // Numeric-keyboard fields → strip non-digits.
                            if (
                              key === "PHONE" ||
                              key === "ALT_PHONE" ||
                              key === "PINCODE" ||
                              key === "AMOUNT" ||
                              key === "TOKEN" ||
                              key === "WEIGHT"
                            ) {
                              next = next.replace(/[^\d.]/g, "");
                              if (key === "PHONE" || key === "ALT_PHONE" || key === "PINCODE")
                                next = next.replace(/\./g, "");
                            }
                            setChatFields((p) => ({ ...p, [sk]: next }));
                          }}
                          placeholder={meta.placeholder}
                          placeholderTextColor="#9CA3AF"
                          keyboardType={meta.keyboard || "default"}
                          multiline={key === "ADDRESS_1" || key === "NOTES"}
                          numberOfLines={key === "ADDRESS_1" ? 3 : 1}
                          maxLength={
                            key === "ADDRESS_1" ? 300 :
                            key === "PINCODE" ? 6 :
                            key === "PHONE" || key === "ALT_PHONE" ? 15 :
                            200
                          }
                        />

                        {/* Phase-15: City → Pincode chip suggestions
                            shown directly below the PINCODE input, ONLY
                            when (a) the user has a city, (b) we have
                            lookup results, (c) the pincode field is
                            still empty / partial. Tapping a chip fills
                            BOTH pincode AND state in one go. */}
                        {key === "PINCODE" && cityHint &&
                         !cityHint.loading &&
                         cityHint.suggestions.length > 0 &&
                         !/^\d{6}$/.test(val) && (
                          <View style={styles.spHintBox}>
                            <View style={styles.spHintHeader}>
                              <Ionicons name="sparkles" size={13} color="#7C3AED" />
                              <Text style={styles.spHintTitle}>
                                Suggested for "{cityHint.city}" — tap to confirm
                              </Text>
                            </View>
                            <View style={styles.spHintChips}>
                              {cityHint.suggestions.slice(0, 6).map((sg) => (
                                <TouchableOpacity
                                  key={sg.pincode}
                                  style={styles.spHintChip}
                                  onPress={() => {
                                    setChatFields((p) => ({
                                      ...p,
                                      pincode: sg.pincode,
                                      // Also commit state if user
                                      // hasn't typed one — saves an
                                      // extra tap.
                                      state: String((p as any).state || "").trim()
                                        ? (p as any).state
                                        : sg.state,
                                    }));
                                  }}
                                  testID={`city-hint-${sg.pincode}`}
                                >
                                  <Ionicons
                                    name="checkmark-circle-outline"
                                    size={13}
                                    color="#7C3AED"
                                  />
                                  <View>
                                    <Text style={styles.spHintChipPin}>
                                      {sg.pincode}
                                    </Text>
                                    <Text
                                      style={styles.spHintChipArea}
                                      numberOfLines={1}
                                    >
                                      {sg.office}
                                    </Text>
                                  </View>
                                </TouchableOpacity>
                              ))}
                            </View>
                          </View>
                        )}

                        {/* Auto-applied state pill: only on the STATE
                            row, only when we silently auto-filled it
                            from the city lookup. Lets the user tap to
                            revert if it was wrong. */}
                        {key === "STATE" && autoState &&
                         autoState.state === val && (
                          <View style={styles.spAutoPill}>
                            <Ionicons name="sparkles" size={11} color="#16A34A" />
                            <Text style={styles.spAutoPillTxt}>
                              Auto-filled from "{autoState.city}"
                            </Text>
                            <TouchableOpacity
                              onPress={() => {
                                setChatFields((p) => ({ ...p, state: "" }));
                                setAutoState(null);
                              }}
                              hitSlop={6}
                            >
                              <Text style={styles.spAutoPillUndo}>Undo</Text>
                            </TouchableOpacity>
                          </View>
                        )}
                      </View>
                    </View>
                  );
                };

                const reqRows = REQ.map((k) => renderRow(k, true));
                // Insert TOKEN row right after PAYMENT when COD is selected.
                if (isCOD) {
                  const insertIdx = REQ.indexOf("PAYMENT") + 1;
                  reqRows.splice(insertIdx, 0, renderRow("TOKEN", true));
                }
                const optRows = OPT.map((k) => renderRow(k, false));

                // Compute current required-missing count (incl. conditional TOKEN/PAYMENT).
                const reqMiss: string[] = [];
                REQ.forEach((k) => {
                  if (k === "PAYMENT") {
                    if (!payNorm) reqMiss.push(k);
                  } else if (!String((chatFields as any)[SNAKE[k]] ?? "").trim()) {
                    reqMiss.push(k);
                  }
                });
                if (isCOD && !String((chatFields as any).token_amount ?? "").trim()) {
                  reqMiss.push("TOKEN");
                }

                return (
                  <>
                    <Text style={styles.spSectionLabel}>Required details</Text>
                    {reqRows}
                    <Text style={[styles.spSectionLabel, { marginTop: 14 }]}>Optional</Text>
                    {optRows}

                    {/* My Custom Fields — plan-gated, defined under
                        Settings → Manage Custom Fields. Only rendered
                        when the user has at least one active field
                        flagged for Smart Paste. Values flow into
                        custom_values payload and are written to the
                        column letters of their personal Google Sheet. */}
                    {userCustomFields.length > 0 && (
                      <>
                        <Text style={[styles.spSectionLabel, { marginTop: 14 }]}>
                          My Custom Fields
                        </Text>
                        {userCustomFields.map((cf) => {
                          const cval = userCustomValues[cf.id] || "";
                          return (
                            <View
                              key={`ucf-${cf.id}`}
                              style={styles.spRow}
                              testID={`smart-paste-row-ucf-${cf.id}`}
                            >
                              <View style={styles.spRowLeft}>
                                {cval ? (
                                  <Ionicons
                                    name="checkmark-circle"
                                    size={16}
                                    color="#16A34A"
                                  />
                                ) : (
                                  <Ionicons
                                    name="ellipse-outline"
                                    size={16}
                                    color="#94A3B8"
                                  />
                                )}
                              </View>
                              <View style={{ flex: 1 }}>
                                <Text style={styles.spRowLabel}>
                                  {cf.name}
                                  {cf.required ? (
                                    <Text style={{ color: "#DC2626" }}>  *</Text>
                                  ) : null}
                                  <Text style={{ color: "#9CA3AF", fontWeight: "400" }}>
                                    {`  · col ${cf.column_letter}`}
                                  </Text>
                                </Text>
                                <TextInput
                                  testID={`smart-paste-ucf-input-${cf.id}`}
                                  style={styles.spRowInput}
                                  value={cval}
                                  onChangeText={(t) => {
                                    let next = t;
                                    if (cf.field_type === "number") {
                                      next = next.replace(/[^\d.]/g, "");
                                    }
                                    setUserCustomValues((prev) => ({
                                      ...prev,
                                      [cf.id]: next,
                                    }));
                                  }}
                                  placeholder={
                                    cf.field_type === "number"
                                      ? "0"
                                      : cf.field_type === "date"
                                        ? "YYYY-MM-DD"
                                        : `Enter ${cf.name.toLowerCase()}`
                                  }
                                  placeholderTextColor="#9CA3AF"
                                  keyboardType={
                                    cf.field_type === "number"
                                      ? "decimal-pad"
                                      : "default"
                                  }
                                />
                              </View>
                            </View>
                          );
                        })}
                      </>
                    )}

                    {reqMiss.length > 0 && (
                      <View style={styles.spReqHint}>
                        <Ionicons name="information-circle" size={14} color="#92400E" />
                        <Text style={styles.spReqHintTxt}>
                          {reqMiss.length === 1
                            ? "1 required field still empty"
                            : `${reqMiss.length} required fields still empty`}
                        </Text>
                      </View>
                    )}
                  </>
                );
              })()}
            </ScrollView>

            <View style={styles.spFooter}>
              <TouchableOpacity
                testID="smart-paste-cancel"
                onPress={closeChat}
                disabled={chatSending}
                style={[styles.spFooterBtn, { backgroundColor: "#F3F4F6" }]}
              >
                <Text style={[styles.spFooterBtnTxt, { color: colors.text }]}>Start Over</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="smart-paste-save"
                onPress={() => {
                  // Phase-8: Local validation honours per-field
                  // settings.field_requirements + custom-field
                  // .required. Falls back to legacy hardcoded list
                  // if settings haven't loaded yet.
                  const SNAKE2: Record<string, string> = {
                    NAME: "customer_name",
                    PHONE: "customer_phone",
                    ALT_PHONE: "customer_alt_phone",
                    ADDRESS_1: "address_line1",
                    CITY: "city",
                    STATE: "state",
                    PINCODE: "pincode",
                    ITEMS: "items",
                    AMOUNT: "amount",
                    WEIGHT: "weight",
                    COURIER: "courier_name",
                    ORDER_ID: "order_id",
                    NOTES: "notes",
                  };
                  const HARDCODED: Record<string, boolean> = {
                    customer_name: true, customer_phone: true,
                    address_line1: true, city: true, state: true,
                    pincode: true, amount: true, weight: true,
                    customer_alt_phone: false, items: false,
                    courier_name: false, order_id: false, notes: false,
                  };
                  const isReq = (snake: string) => {
                    if (snake in fieldReqs) return !!fieldReqs[snake];
                    return !!HARDCODED[snake];
                  };
                  const reqMiss: string[] = [];
                  Object.entries(SNAKE2).forEach(([k, snake]) => {
                    if (
                      isReq(snake) &&
                      !String((chatFields as any)[snake] ?? "").trim()
                    ) {
                      reqMiss.push(k);
                    }
                  });
                  // Mobile must be 10+ digits.
                  const phoneDigits = String((chatFields as any).customer_phone || "").replace(/\D/g, "");
                  if (phoneDigits && phoneDigits.length < 10) {
                    Alert.alert("Invalid mobile", "Mobile number must be at least 10 digits.");
                    return;
                  }
                  // Alternate Mobile (optional) — if present, must be 10 digits.
                  const altDigits = String((chatFields as any).customer_alt_phone || "").replace(/\D/g, "");
                  if (altDigits && altDigits.length !== 10) {
                    Alert.alert(
                      "Invalid alternate mobile",
                      "Alternate mobile must be exactly 10 digits, or leave it empty.",
                    );
                    return;
                  }
                  // Pincode (when entered) must be exactly 6 digits.
                  const pinDigits = String((chatFields as any).pincode || "").replace(/\D/g, "");
                  if (pinDigits && pinDigits.length !== 6) {
                    Alert.alert("Invalid pincode", "Pincode must be exactly 6 digits.");
                    return;
                  }
                  // Weight (when required) must be a positive number > 0.
                  if (isReq("weight")) {
                    const weightVal = parseFloat(
                      String((chatFields as any).weight || "").replace(/[^\d.]/g, ""),
                    );
                    if (!weightVal || weightVal <= 0) {
                      if (!reqMiss.includes("WEIGHT")) reqMiss.push("WEIGHT");
                    }
                  }
                  // Amount (when required) must be a positive number > 0.
                  if (isReq("amount")) {
                    const amountVal = parseFloat(
                      String((chatFields as any).amount || "").replace(/[^\d.]/g, ""),
                    );
                    if (!amountVal || amountVal <= 0) {
                      if (!reqMiss.includes("AMOUNT")) reqMiss.push("AMOUNT");
                    }
                  }
                  // Payment normalises to COD/PREPAID; if user left it
                  // blank we only fail when payment_mode is required.
                  const payRaw = String((chatFields as any).payment_mode || "").trim().toUpperCase();
                  const payNorm =
                    payRaw === "COD" ? "COD" : (payRaw === "PREPAID" || payRaw === "PAID") ? "PREPAID" : "";
                  if (!payNorm && isReq("payment_mode")) reqMiss.push("PAYMENT");
                  // Token always required when COD is selected.
                  if (payNorm === "COD") {
                    const tk = String((chatFields as any).token_amount ?? "").trim();
                    if (!tk) reqMiss.push("TOKEN");
                  }
                  // Per-user Custom Fields with `required: true` must
                  // also have a value. Map cf.id → label for the alert.
                  const ucfMissLabels: string[] = [];
                  for (const cf of userCustomFields) {
                    if (!cf.required) continue;
                    if (!String(userCustomValues[cf.id] ?? "").trim()) {
                      ucfMissLabels.push(cf.name);
                    }
                  }
                  if (reqMiss.length > 0 || ucfMissLabels.length > 0) {
                    const builtIn = reqMiss.map((k) => FIELD_META[k]?.label || k);
                    const labels = [...builtIn, ...ucfMissLabels].join(", ");
                    Alert.alert("Please fill required fields", labels);
                    return;
                  }
                  // Normalise payment_mode + clear token if Prepaid.
                  const finalFields = {
                    ...chatFields,
                    payment_mode: payNorm,
                    token_amount: payNorm === "COD" ? (chatFields as any).token_amount : "",
                  };
                  saveFromFields(finalFields, userCustomValues);
                }}
                disabled={chatSending}
                style={[
                  styles.spFooterBtn,
                  {
                    backgroundColor: chatSending ? "#9CA3AF" : "#7C3AED",
                    flex: 2,
                  },
                ]}
              >
                {chatSending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <Ionicons name="save-outline" size={16} color="#fff" />
                    <Text style={[styles.spFooterBtnTxt, { color: "#fff", marginLeft: 6 }]}>
                      Save Shipment
                    </Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          </Animated.View>
        </KeyboardAvoidingView>
      </Modal>



      <ScrollView
        testID="dashboard-scroll"
        contentContainerStyle={{ paddingBottom: 40 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
      >
        {loading ? (
          <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
        ) : (
          <>
            {/* Plan + usage meter (Phase 3b) */}
            <UsageMeter />
            <HomeAlerts />

            <View style={styles.statsGrid}>
              <StatCard
                testID="stat-total"
                label="Total"
                value={stats?.total ?? 0}
                icon="cube-outline"
                tone="neutral"
                width={cardW}
              />
              <StatCard
                testID="stat-pending"
                label="Pending"
                value={stats?.pending ?? 0}
                icon="time-outline"
                tone="warning"
                width={cardW}
              />
              <StatCard
                testID="stat-delivered"
                label="Delivered"
                value={stats?.delivered ?? 0}
                icon="checkmark-circle"
                tone="success"
                width={cardW}
              />
              <StatCard
                testID="stat-cod"
                label={`COD · ${stats?.cod_count ?? 0}`}
                value={`₹${(stats?.cod_total ?? 0).toFixed(0)}`}
                icon="cash-outline"
                tone="neutral"
                width={cardW}
              />
              <StatCard
                testID="stat-prepaid"
                label={`Prepaid · ${stats?.prepaid_count ?? 0}`}
                value={`₹${(stats?.prepaid_total ?? 0).toFixed(0)}`}
                icon="card-outline"
                tone="neutral"
                width={cardW}
              />
              <StatCard
                testID="stat-revenue"
                label="Total Revenue"
                value={`₹${(stats?.revenue_total ?? 0).toFixed(0)}`}
                icon="trending-up"
                tone="primary"
                width={cardW}
              />
            </View>

            <View style={styles.pillsCol}>
              <ActionPill
                testID="quick-pending-orders"
                icon="download-outline"
                label="Pending Orders"
                badge={pendingOrdersCount}
                onPress={() => router.push("/(tabs)/orders")}
                tone="violet"
              />
              <ActionPill
                testID="quick-pending-shipments"
                icon="cube-outline"
                label="Pending Shipments"
                badge={stats?.pending ?? 0}
                onPress={() =>
                  router.push({
                    pathname: "/(tabs)/shipments",
                    params: { status: "Pending" },
                  })
                }
                tone="warning"
              />
              <ActionPill
                testID="quick-print-recent"
                icon="print-outline"
                label="Print All"
                onPress={() =>
                  router.push({
                    pathname: "/(tabs)/shipments",
                    params: { select: "1" },
                  })
                }
                tone="neutral"
                chevron
              />
              {/* Phase-12: Two-up scanner shortcuts. Sits between the
                  general "Print All" entry and the post-shipping
                  notification pills so the warehouse operator's most
                  common actions (scan in / scan out) are 1 tap away
                  from the home screen instead of buried inside the
                  Shipments tab. */}
              <View style={styles.scanGrid}>
                <TouchableOpacity
                  testID="quick-scan-ready"
                  style={[styles.scanBox, styles.scanBoxReady]}
                  onPress={() =>
                    router.push({
                      pathname: "/scanner-dispatch",
                      params: { mode: "dispatch" },
                    } as any)
                  }
                  activeOpacity={0.85}
                >
                  <View style={styles.scanBoxIcon}>
                    <Ionicons name="barcode-outline" size={28} color="#8B5E34" />
                  </View>
                  <Text style={styles.scanBoxTitle}>Scan & Ready to Ship</Text>
                  <Text style={styles.scanBoxSub}>
                    Pending → Ready to Ship
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="quick-scan-shipped"
                  style={[styles.scanBox, styles.scanBoxShipped]}
                  onPress={() =>
                    router.push({
                      pathname: "/scanner-dispatch",
                      params: { mode: "ship" },
                    } as any)
                  }
                  activeOpacity={0.85}
                >
                  <View style={[styles.scanBoxIcon, { backgroundColor: "#E0DAFF" }]}>
                    <Ionicons name="rocket-outline" size={26} color="#4B3FCF" />
                  </View>
                  <Text style={[styles.scanBoxTitle, { color: "#4B3FCF" }]}>
                    Scan & Mark as Shipped
                  </Text>
                  <Text style={[styles.scanBoxSub, { color: "#6B5BFF" }]}>
                    Ready to Ship → Shipped
                  </Text>
                </TouchableOpacity>
              </View>
              {/* Phase-12: Post-print workflow pills. */}
              <ActionPill
                testID="quick-dispatch-confirmation"
                icon="rocket-outline"
                label="Ready to Ship Confirmation"
                onPress={() => router.push("/dispatch-confirmation" as any)}
                tone="violet"
                chevron
              />
              <ActionPill
                testID="quick-delivery-confirmation"
                icon="checkmark-circle-outline"
                label="Delivery Confirmation"
                onPress={() => router.push("/delivery-confirmation" as any)}
                tone="success"
                chevron
              />
            </View>

            <View style={styles.sectionHeader}>
              <Text style={styles.sectionTitle}>Recent Shipments</Text>
              <TouchableOpacity onPress={() => router.push("/(tabs)/shipments")}>
                <Text style={styles.link}>View all ›</Text>
              </TouchableOpacity>
            </View>

            {recent.length === 0 ? (
              <View style={styles.empty} testID="empty-recent">
                <Ionicons name="cube-outline" size={48} color="#9CA3AF" />
                <Text style={styles.emptyText}>
                  No shipments yet. Create your first shipment.
                </Text>
                <TouchableOpacity
                  testID="empty-create-btn"
                  style={styles.primaryBtn}
                  onPress={() => router.push("/(tabs)/add")}
                >
                  <Text style={styles.primaryBtnText}>+ New Shipment</Text>
                </TouchableOpacity>
              </View>
            ) : (
              recent.map((s) => (
                <TouchableOpacity
                  key={s.id}
                  testID={`recent-item-${s.tracking_id}`}
                  style={styles.card}
                  onPress={() => router.push(`/label/${s.id}`)}
                >
                  <View style={{ flex: 1 }}>
                    <View style={styles.row}>
                      <Text style={styles.trackId}>{s.tracking_id}</Text>
                      <StatusChip status={s.status} />
                    </View>
                    <Text style={styles.cardName}>{s.customer_name}</Text>
                    <Text style={styles.cardSub}>
                      {s.courier_name} · {s.city || "—"}
                    </Text>
                  </View>
                  <Ionicons
                    name="chevron-forward"
                    size={20}
                    color={colors.textMuted}
                  />
                </TouchableOpacity>
              ))
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function StatCard({
  label,
  value,
  icon,
  tone,
  testID,
  width,
}: {
  label: string;
  value: number | string;
  icon: keyof typeof Ionicons.glyphMap;
  tone: "neutral" | "warning" | "success" | "primary";
  testID?: string;
  width?: number;
}) {
  // "primary" → full orange filled card (Total Revenue).
  // Others → white card with color-coded icon + value.
  const isPrimary = tone === "primary";
  const accent =
    tone === "success" ? "#10B981"
    : tone === "warning" ? "#FF5A00"
    : colors.text;

  const sizeStyle = width ? { width } : null;

  if (isPrimary) {
    return (
      <View testID={testID} style={[styles.statCard, styles.statCardPrimary, sizeStyle]}>
        <Ionicons name={icon} size={18} color="#fff" />
        <Text style={styles.statValuePrimary} numberOfLines={1} adjustsFontSizeToFit>
          {value}
        </Text>
        <Text style={styles.statLabelPrimary} numberOfLines={2}>
          {label}
        </Text>
      </View>
    );
  }

  return (
    <View testID={testID} style={[styles.statCard, sizeStyle]}>
      <Ionicons name={icon} size={18} color={accent} />
      <Text
        style={[styles.statValue, { color: accent }]}
        numberOfLines={1}
        adjustsFontSizeToFit
      >
        {value}
      </Text>
      <Text style={[styles.statLabel, { color: "#6B7280" }]} numberOfLines={2}>
        {label}
      </Text>
    </View>
  );
}

function ActionPill({
  icon,
  label,
  onPress,
  badge,
  tone = "neutral",
  testID,
  chevron,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  badge?: number;
  tone?: "neutral" | "violet" | "warning" | "success";
  testID?: string;
  chevron?: boolean;
}) {
  const toneMap: Record<
    string,
    { bg: string; border: string; fg: string; badgeBg: string; badgeFg: string }
  > = {
    neutral: { bg: "#fff", border: "#E5E7EB", fg: colors.text, badgeBg: colors.text, badgeFg: "#fff" },
    violet:  { bg: "#F5F3FF", border: "#DDD6FE", fg: "#6D28D9", badgeBg: "#7C3AED", badgeFg: "#fff" },
    warning: { bg: "#FFFBEB", border: "#FDE68A", fg: "#B45309", badgeBg: "#F59E0B", badgeFg: "#fff" },
    success: { bg: "#ECFDF5", border: "#A7F3D0", fg: "#047857", badgeBg: "#10B981", badgeFg: "#fff" },
  };
  const t = toneMap[tone];
  const showBadge = typeof badge === "number" && badge > 0;
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      style={[styles.pillBtn, { backgroundColor: t.bg, borderColor: t.border }]}
      activeOpacity={0.75}
    >
      <View style={[styles.pillIconWrap, { backgroundColor: "transparent" }]}>
        <Ionicons name={icon} size={22} color={t.fg} />
      </View>
      <Text
        style={[styles.pillLabel, { color: t.fg }]}
        numberOfLines={1}
        allowFontScaling={false}
      >
        {label}
      </Text>
      {showBadge && (
        <View style={[styles.pillBadge, { backgroundColor: t.badgeBg }]}>
          <Text style={[styles.pillBadgeText, { color: t.badgeFg }]} numberOfLines={1}>
            {badge! > 99 ? "99+" : String(badge)}
          </Text>
        </View>
      )}
      {chevron && (
        <Ionicons name="chevron-forward" size={20} color="#9CA3AF" style={{ marginLeft: 8 }} />
      )}
    </TouchableOpacity>
  );
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    Delivered: { bg: colors.successBg, fg: colors.successText },
    Pending: { bg: colors.warningBg, fg: colors.warningText },
    Cancelled: { bg: colors.dangerBg, fg: colors.dangerText },
  };
  const m = map[status] || map.Pending;
  return (
    <View style={[styles.chip, { backgroundColor: m.bg }]}>
      <Text style={[styles.chipText, { color: m.fg }]}>
        {status.toUpperCase()}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  // ───── Phase-12: Scanner shortcut grid (2 boxes side-by-side) ─────
  scanGrid: {
    flexDirection: "row",
    gap: 10,
    marginTop: 4,
    marginBottom: 4,
  },
  scanBox: {
    flex: 1,
    height: 116,
    padding: 14,
    borderRadius: 14,
    borderWidth: 1,
    justifyContent: "space-between",
  },
  // Box 1 — cream palette (matches "Ready to Ship" status colour).
  scanBoxReady: {
    backgroundColor: "#FBF1E2",
    borderColor: "#E6C9A8",
  },
  // Box 2 — lavender palette (matches "Shipped" status colour).
  scanBoxShipped: {
    backgroundColor: "#F0EBFF",
    borderColor: "#C8BCFF",
  },
  scanBoxIcon: {
    width: 44, height: 44, borderRadius: 12,
    backgroundColor: "#F4E3CF",
    alignItems: "center", justifyContent: "center",
  },
  scanBoxTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: "#8B5E34",
    lineHeight: 18,
  },
  scanBoxSub: {
    fontSize: 11,
    fontWeight: "600",
    color: "#A87842",
    marginTop: 2,
  },
  header: {
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 16,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: colors.background,
  },
  headerKicker: {
    fontSize: 10,
    fontWeight: "700",
    color: colors.primary,
    letterSpacing: 1.5,
  },
  headerTitle: { fontSize: 28, fontWeight: "800", color: colors.text, marginTop: 2 },
  headerSub: { color: colors.textMuted, marginTop: 2 },
  headerAction: {
    backgroundColor: colors.secondary,
    width: 44,
    height: 44,
    borderRadius: 10,
    justifyContent: "center",
    alignItems: "center",
  },
  statsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    paddingHorizontal: 16,
    gap: 10,
    marginTop: 4,
  },
  statCard: {
    flexBasis: "30%",
    flexGrow: 1,
    flexShrink: 1,
    minWidth: 0,
    backgroundColor: "#fff",
    paddingVertical: 14,
    paddingHorizontal: 8,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: "#EEF0F3",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 108,
  },
  statCardPrimary: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  statValue: {
    fontSize: 26,
    fontWeight: "900",
    marginTop: 6,
    textAlign: "center",
    letterSpacing: -0.5,
  },
  statValuePrimary: {
    fontSize: 22,
    fontWeight: "900",
    marginTop: 6,
    color: "#fff",
    textAlign: "center",
    letterSpacing: -0.5,
  },
  statLabel: {
    fontSize: 10.5,
    fontWeight: "800",
    marginTop: 4,
    letterSpacing: 0.8,
    textTransform: "uppercase",
    textAlign: "center",
  },
  statLabelPrimary: {
    fontSize: 9.5,
    fontWeight: "800",
    marginTop: 4,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: "rgba(255,255,255,0.95)",
    textAlign: "center",
  },

  /* Full-width stacked action pills (Pending Orders / Shipments / Print All) */
  pillsCol: {
    paddingHorizontal: 16,
    marginTop: 16,
    gap: 10,
  },
  pillBtn: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    borderRadius: 14,
    paddingVertical: 14,
    paddingHorizontal: 16,
    minHeight: 56,
  },
  pillIconWrap: {
    width: 28,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
    marginRight: 10,
  },
  pillLabel: {
    flex: 1,
    fontSize: 15,
    fontWeight: "800",
    letterSpacing: 0.2,
  },
  pillBadge: {
    minWidth: 36,
    paddingHorizontal: 10,
    height: 28,
    borderRadius: 999,
    alignItems: "center",
    justifyContent: "center",
  },
  pillBadgeText: {
    fontSize: 13,
    fontWeight: "900",
    includeFontPadding: false,
  },
  sectionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingHorizontal: 20,
    marginTop: 24,
    marginBottom: 10,
  },
  sectionTitle: { fontSize: 16, fontWeight: "800", color: colors.text },
  link: { color: colors.primary, fontWeight: "700" },
  card: {
    marginHorizontal: 16,
    marginBottom: 10,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  trackId: {
    fontFamily: "Courier",
    fontWeight: "800",
    color: colors.text,
    fontSize: 14,
    letterSpacing: 1,
  },
  cardName: { fontSize: 15, fontWeight: "700", color: colors.text, marginTop: 4 },
  cardSub: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  chip: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  empty: {
    alignItems: "center",
    padding: 30,
    marginHorizontal: 16,
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
  },
  emptyText: { marginTop: 12, color: colors.textMuted, textAlign: "center" },
  primaryBtn: {
    marginTop: 16,
    backgroundColor: colors.primary,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },

  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "flex-end",
  },
  modalCard: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 18,
    paddingBottom: 30,
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 8,
  },
  modalTitle: {
    flex: 1,
    fontSize: 16,
    fontWeight: "900",
    color: colors.text,
  },
  modalHint: {
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 10,
    lineHeight: 17,
  },
  modalQuickRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 10,
  },
  modalQuickBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingVertical: 7,
    paddingHorizontal: 12,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#7C3AED",
    backgroundColor: "#F5F3FF",
  },
  modalQuickBtnText: { fontSize: 11, fontWeight: "700", color: "#7C3AED" },
  modalInput: {
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    padding: 12,
    minHeight: 160,
    fontSize: 13,
    textAlignVertical: "top",
    color: colors.text,
    backgroundColor: "#FAFAFA",
  },
  modalActions: {
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
  },
  modalBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  modalBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },

  /* AI parsing indicator shown inside the Smart Paste modal while the
     backend LLM call is in-flight. */
  aiStatusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
    backgroundColor: "#F5F3FF",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#DDD6FE",
  },
  aiStatusText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#6D28D9",
  },

  /* Phase-5d: Smart Paste tabs (Text vs Photo). */
  tabRow: {
    flexDirection: "row",
    backgroundColor: "#F1F5F9",
    borderRadius: 999,
    padding: 4,
    marginBottom: 12,
  },
  tabBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 8,
    borderRadius: 999,
  },
  tabBtnActive: { backgroundColor: "#7C3AED" },
  tabBtnTxt: { fontSize: 12, fontWeight: "800", color: "#475569" },
  tabBtnTxtActive: { color: "#fff" },
  tabPill: {
    backgroundColor: "rgba(255,255,255,0.25)",
    paddingHorizontal: 6,
    paddingVertical: 1,
    borderRadius: 6,
    marginLeft: 2,
  },
  tabPillTxt: { color: "#fff", fontSize: 9, fontWeight: "900" },

  /* Photo capture buttons */
  photoBtnGrid: {
    flexDirection: "row",
    gap: 12,
    marginVertical: 14,
  },
  photoBigBtn: {
    flex: 1,
    backgroundColor: "#F5F3FF",
    borderWidth: 1.5,
    borderColor: "#DDD6FE",
    borderRadius: 14,
    paddingVertical: 22,
    alignItems: "center",
    gap: 6,
  },
  photoBigBtnTxt: { fontSize: 14, fontWeight: "800", color: "#5B21B6" },
  photoBigBtnSub: { fontSize: 11, color: "#7C3AED" },

  photoUploadCard: {
    alignItems: "center",
    paddingVertical: 36,
    backgroundColor: "#F5F3FF",
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#DDD6FE",
    marginVertical: 14,
    gap: 10,
  },
  photoUploadTxt: { fontSize: 13, fontWeight: "700", color: "#6D28D9" },

  photoTipBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 6,
    backgroundColor: "#FEF3C7",
    borderColor: "#FCD34D",
    borderWidth: 1,
    padding: 10,
    borderRadius: 10,
    marginBottom: 12,
  },
  photoTipTxt: {
    flex: 1, color: "#92400E", fontSize: 11.5, fontWeight: "600", lineHeight: 16,
  },

  /* Missing-Fields Modal — chips showing what AI already found. */
  aiKnownRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 10,
  },
  aiKnownChip: {
    fontSize: 11,
    fontWeight: "700",
    color: "#065F46",
    backgroundColor: "#ECFDF5",
    borderWidth: 1,
    borderColor: "#A7F3D0",
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
  },
  fieldWrap: {
    marginBottom: 10,
  },
  fieldLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: colors.text,
    marginBottom: 4,
  },
  fieldInput: {
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: colors.text,
    backgroundColor: "#FAFAFA",
  },
  fieldInputMissing: {
    borderColor: "#FCA5A5",
    backgroundColor: "#FEF2F2",
  },

  /* Preview Modal — badges row. */
  badgesRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 6,
  },
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    borderWidth: 1,
  },
  badgeSimple: {
    backgroundColor: "#ECFDF5",
    borderColor: "#A7F3D0",
  },
  badgeMedium: {
    backgroundColor: "#FFFBEB",
    borderColor: "#FDE68A",
  },
  badgeComplex: {
    backgroundColor: "#FEF3C7",
    borderColor: "#F59E0B",
  },
  badgeDup: {
    backgroundColor: "#FFE4E6",
    borderColor: "#FDA4AF",
  },
  badgeText: {
    fontSize: 11,
    fontWeight: "800",
  },
  aiReasonText: {
    fontSize: 11,
    color: "#6B7280",
    fontStyle: "italic",
    marginBottom: 8,
  },

  /* Preview Modal — repeat-customer suggestion banner. */
  suggestBanner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#ECFDF5",
    borderWidth: 1,
    borderColor: "#A7F3D0",
    borderRadius: 10,
    padding: 10,
    marginBottom: 10,
  },
  suggestBannerTitle: {
    fontSize: 12,
    fontWeight: "800",
    color: "#065F46",
  },
  suggestBannerBody: {
    fontSize: 11,
    color: "#047857",
    marginTop: 2,
  },
  suggestBtn: {
    backgroundColor: "#10B981",
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 8,
    marginLeft: 8,
  },
  suggestBtnText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 12,
  },

  /* Chat Modal — bubbles & input row. */
  chatBubble: {
    maxWidth: "88%",
    padding: 10,
    borderRadius: 14,
    marginVertical: 4,
  },
  chatBubbleAI: {
    alignSelf: "flex-start",
    backgroundColor: "#F3F4F6",
    borderBottomLeftRadius: 4,
  },
  chatBubbleUser: {
    alignSelf: "flex-end",
    backgroundColor: "#7C3AED",
    borderBottomRightRadius: 4,
  },
  chatBubbleKicker: {
    fontSize: 10,
    fontWeight: "800",
    color: "#6D28D9",
    marginBottom: 2,
    letterSpacing: 0.5,
  },
  chatBubbleText: {
    fontSize: 13,
    lineHeight: 19,
  },
  chatSystemText: {
    alignSelf: "center",
    fontSize: 11,
    color: "#6B7280",
    fontStyle: "italic",
    marginVertical: 6,
    textAlign: "center",
  },
  chatInputRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
    paddingTop: 10,
  },
  chatInput: {
    flex: 1,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 10,
    fontSize: 14,
    color: colors.text,
    backgroundColor: "#FAFAFA",
    maxHeight: 100,
  },
  chatSendBtn: {
    width: 44,
    height: 44,
    borderRadius: 22,
    justifyContent: "center",
    alignItems: "center",
  },

  /* ─────────── Smart Paste — Payment toggle (COD / Prepaid) ─────────── */
  payToggleRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 4,
  },
  payToggleBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: "#DDD6FE",
    backgroundColor: "#fff",
  },
  payToggleBtnActive: {
    backgroundColor: "#7C3AED",
    borderColor: "#7C3AED",
  },
  payToggleTxt: {
    fontSize: 13,
    fontWeight: "800",
    color: "#7C3AED",
    letterSpacing: 0.3,
  },
  payToggleTxtActive: {
    color: "#fff",
  },

  /* ─────────── Smart Paste — Bottom Sheet (Phase-7) ─────────── */
  sheetCard: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingBottom: Platform.OS === "ios" ? 18 : 0,
    overflow: "hidden",
  },
  sheetGrabArea: {
    paddingTop: 8,
    paddingBottom: 6,
    alignItems: "center",
    backgroundColor: "transparent",
  },
  sheetGrabBar: {
    width: 42,
    height: 5,
    borderRadius: 3,
    backgroundColor: "#CBD5E1",
  },

  /* ─────────── Smart Paste — Entry Sheet (Phase-7) ─────────── */
  entryBtnCol: {
    gap: 12,
    paddingVertical: 12,
  },
  entryBigBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: "#F5F3FF",
    borderWidth: 1.5,
    borderColor: "#DDD6FE",
    borderRadius: 14,
    paddingVertical: 18,
    paddingHorizontal: 16,
  },
  entryBigBtnIcon: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: "#DDD6FE",
  },
  entryBigBtnTitle: {
    fontSize: 16, fontWeight: "900", color: "#5B21B6",
    letterSpacing: 0.2,
  },
  entryBigBtnSub: {
    fontSize: 12, fontWeight: "600", color: "#6D28D9",
    marginTop: 3,
  },
  entryBusyCard: {
    alignItems: "center",
    paddingVertical: 40,
    backgroundColor: "#F5F3FF",
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#DDD6FE",
    marginVertical: 10,
    gap: 12,
  },
  entryBusyTxt: {
    fontSize: 13, fontWeight: "700", color: "#6D28D9",
  },
  entryBusySub: {
    fontSize: 11, fontWeight: "500", color: "#9F7AEA",
    marginTop: -4,
  },

  /* ─────────── Smart Paste — Summary Card styles (Phase-7) ─────────── */
  dupBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#DBEAFE",
    borderColor: "#93C5FD",
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 8,
    marginHorizontal: 14,
    marginTop: 6,
    marginBottom: 4,
  },
  dupBannerTxt: {
    flex: 1,
    fontSize: 12,
    fontWeight: "700",
    color: "#1E3A8A",
  },
  dupBannerBtn: {
    backgroundColor: "#2563EB",
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
  },
  dupBannerBtnTxt: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 12,
  },
  spSectionLabel: {
    fontSize: 11, fontWeight: "800", color: "#64748B",
    letterSpacing: 0.6, textTransform: "uppercase",
    marginTop: 4, marginBottom: 6,
  },
  spRow: {
    flexDirection: "row", alignItems: "flex-start", gap: 10,
    paddingHorizontal: 10, paddingVertical: 8,
    backgroundColor: "#fff",
    borderRadius: 8, borderWidth: 1, borderColor: "#E5E7EB",
    marginBottom: 6,
  },
  spRowMissing: {
    backgroundColor: "#FEF2F2",
    borderColor: "#FECACA",
  },
  spRowLeft: {
    width: 22, alignItems: "center", paddingTop: 6,
  },
  spRowLabel: {
    fontSize: 11, fontWeight: "700", color: "#475569",
    letterSpacing: 0.3, marginBottom: 2,
  },
  spRowInput: {
    fontSize: 13, fontWeight: "600", color: "#0F172A",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 6,
    paddingHorizontal: 8, paddingVertical: 6,
    backgroundColor: "#fff",
    minHeight: 34,
  },
  // Phase-15: City → Pincode suggestion chip block
  spHintBox: {
    marginTop: 8,
    padding: 10,
    backgroundColor: "#F5F3FF",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#DDD6FE",
  },
  spHintHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 8,
  },
  spHintTitle: {
    fontSize: 11,
    fontWeight: "800",
    color: "#5B21B6",
    flex: 1,
  },
  spHintChips: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  spHintChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: "#fff",
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#C4B5FD",
    minWidth: 110,
  },
  spHintChipPin: {
    fontSize: 13,
    fontWeight: "800",
    color: "#5B21B6",
  },
  spHintChipArea: {
    fontSize: 10,
    fontWeight: "600",
    color: "#7C3AED",
    maxWidth: 90,
  },
  // Auto-applied state pill (with Undo)
  spAutoPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
    paddingHorizontal: 8,
    paddingVertical: 4,
    backgroundColor: "#DCFCE7",
    borderRadius: 6,
    alignSelf: "flex-start",
  },
  spAutoPillTxt: {
    fontSize: 10.5,
    fontWeight: "700",
    color: "#15803D",
  },
  spAutoPillUndo: {
    fontSize: 10.5,
    fontWeight: "800",
    color: "#DC2626",
    textDecorationLine: "underline",
    marginLeft: 4,
  },
  spReqHint: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 12, paddingHorizontal: 10, paddingVertical: 8,
    backgroundColor: "#FEF3C7", borderRadius: 8,
    borderWidth: 1, borderColor: "#FCD34D",
  },
  spReqHintTxt: {
    fontSize: 12, fontWeight: "700", color: "#78350F",
  },
  spFooter: {
    flexDirection: "row", gap: 8,
    paddingHorizontal: 14, paddingVertical: 10,
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  spFooterBtn: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center",
    paddingVertical: 12, borderRadius: 10,
  },
  spFooterBtnTxt: {
    fontSize: 13.5, fontWeight: "800", letterSpacing: 0.3,
  },

  /* "Save Now" button — appears above the chat input once every required
     field is filled, so the user can commit without another turn. */
  chatSaveNowBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: "#10B981",
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 10,
    marginBottom: 8,
    shadowColor: "#10B981",
    shadowOpacity: 0.25,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  chatSaveNowText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 14,
    letterSpacing: 0.3,
  },
});
