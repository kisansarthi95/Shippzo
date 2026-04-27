import React, { useCallback, useState } from "react";
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
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import * as ImagePicker from "expo-image-picker";
import { Api, Shipment } from "../../lib/api";
import { colors } from "../../lib/theme";
import UsageMeter from "../../components/UsageMeter";
import HomeAlerts from "../../components/HomeAlerts";

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
  const { width: screenWidth } = useWindowDimensions();
  // 3-column stat grid: 16px horizontal padding + 10px × 2 gaps
  const cardW = Math.floor((screenWidth - 32 - 20) / 3);
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

  // Smart Paste — AI-first flow: auto-paste raw WhatsApp text from clipboard,
  // LLM parses everything. Modal is just a fallback when clipboard is empty.
  const [pasteModalOpen, setPasteModalOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasting, setPasting] = useState(false);
  const [pasteStage, setPasteStage] = useState<"" | "parsing" | "saving">("");
  // Smart Paste tabs: text vs photo. Photo tab is feature-gated by
  // `smart_paste_image_ocr` which is enabled in every plan by default.
  const [pasteTab, setPasteTab] = useState<"text" | "photo">("text");
  const [photoUri, setPhotoUri] = useState<string | null>(null);
  const [photoUploading, setPhotoUploading] = useState(false);

  // Smart Paste Chat — conversational flow. AI asks for missing details
  // in natural language, user types/dictates replies (keyboard mic works
  // out of the box on iOS/Android for voice-to-text). Nothing is saved
  // until the AI confirms all required fields are present.
  const [chatOpen, setChatOpen] = useState(false);
  type ChatMsg = { role: "ai" | "user" | "system"; text: string; typing?: boolean };
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);
  const [chatFields, setChatFields] = useState<Record<string, any>>({});
  const [chatComplexity, setChatComplexity] = useState<"simple" | "medium" | "complex" | "">("");
  const [chatReason, setChatReason] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [suggestedCustomer, setSuggestedCustomer] = useState<any | null>(null);
  const [dupFound, setDupFound] = useState<any[]>([]);

  // Human-readable labels + placeholders for each schema field.
  const FIELD_META: Record<
    string,
    { label: string; placeholder: string; keyboard?: "default" | "phone-pad" | "numeric"; primary?: boolean }
  > = {
    NAME:      { label: "Customer Name",    placeholder: "e.g. Ramesh Patel", primary: true },
    PHONE:     { label: "Mobile Number",    placeholder: "10-digit mobile", keyboard: "phone-pad", primary: true },
    ADDRESS_1: { label: "Address",          placeholder: "House / street / area", primary: true },
    ADDRESS_2: { label: "Address Line 2",   placeholder: "Landmark / optional" },
    CITY:      { label: "City",             placeholder: "e.g. Ahmedabad", primary: true },
    STATE:     { label: "State",            placeholder: "e.g. Gujarat", primary: true },
    PINCODE:   { label: "Pincode",          placeholder: "6 digits", keyboard: "numeric", primary: true },
    ITEMS:     { label: "Item(s)",          placeholder: "e.g. Saree x 2", primary: true },
    AMOUNT:    { label: "Amount (₹)",       placeholder: "COD amount", keyboard: "numeric", primary: true },
    PAYMENT:   { label: "Payment",          placeholder: "COD or PAID", primary: true },
    COURIER:   { label: "Courier",          placeholder: "optional" },
    ORDER_ID:  { label: "Order ID",         placeholder: "optional" },
    WEIGHT:    { label: "Weight",           placeholder: "e.g. 500g" },
    NOTES:     { label: "Notes",            placeholder: "special instructions" },
  };

  // Field order in the preview form. Primary fields first, then optional.
  const FIELD_ORDER = [
    "NAME", "PHONE", "ALT_PHONE", "ADDRESS_1", "ADDRESS_2", "CITY", "STATE", "PINCODE",
    "ITEMS", "AMOUNT", "PAYMENT",
    "COURIER", "ORDER_ID", "WEIGHT", "NOTES",
  ];

  // Fields the app treats as REQUIRED — blocks Save until filled.
  const REQUIRED_FIELDS = [
    "NAME", "PHONE", "ADDRESS_1", "CITY", "STATE", "PINCODE", "AMOUNT", "WEIGHT",
  ];

  /** Derived: do we already have every required field? Controls the
   *  "Save Now" button visibility in the chat input row. */
  const chatComplete = React.useMemo(() => {
    const keyMap: Record<string, string> = {
      NAME: "customer_name",
      PHONE: "customer_phone",
      ADDRESS_1: "address_line1",
      CITY: "city",
      STATE: "state",
      PINCODE: "pincode",
      AMOUNT: "amount",
      WEIGHT: "weight",
    };
    return REQUIRED_FIELDS.every((k) => {
      const v = chatFields[keyMap[k]];
      return v != null && String(v).trim() !== "";
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatFields]);

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
    // Phase-5d UX fix: ALWAYS open the Smart Paste modal first so the
    // user can choose between Text and Photo. Previously the clipboard
    // would be auto-parsed and the user had no way to switch to Photo
    // without manually clearing the clipboard.
    //
    // We still pre-fill the Text input from clipboard (if any) so a
    // single "AI Parse & Queue" tap still works for the common case.
    let text = "";
    try {
      text = (await Clipboard.getStringAsync()) || "";
    } catch {
      text = "";
    }
    setPasteText(text.trim());
    setPasteTab("text");
    setPhotoUri(null);
    setPasteModalOpen(true);
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
          quality: 0.6,
          base64: true,
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
          quality: 0.6,
          base64: true,
          exif: false,
        });
      }
      if (result.canceled || !result.assets || result.assets.length === 0) {
        return;
      }
      const asset = result.assets[0];
      const b64 = asset.base64 || "";
      if (!b64) {
        Alert.alert("Photo error", "Could not read the selected image.");
        return;
      }
      setPhotoUri(asset.uri);
      setPhotoUploading(true);
      // Determine MIME from URI extension (best-effort).
      const lower = (asset.uri || "").toLowerCase();
      const mime = lower.endsWith(".png")
        ? "image/png"
        : lower.endsWith(".webp")
        ? "image/webp"
        : "image/jpeg";
      const resp = await Api.smartPastePhoto(b64, mime);
      setPhotoUploading(false);
      setPhotoUri(null);
      setPasteModalOpen(false);
      setPasteTab("text");

      // Same merge logic as runSmartPasteAI:
      const legacyFields = resp.fields || {};
      const missing = (resp.missing || []).filter((k) =>
        REQUIRED_FIELDS.includes(k),
      );

      if (resp.complete && missing.length === 0) {
        await saveFromFields(legacyFields);
        return;
      }

      // Open chat modal with photo-decoded fields pre-filled.
      setChatFields(legacyFields);
      setChatComplexity((resp.complexity as any) || "complex");
      setChatReason(resp.reason || "photo OCR");
      setDupFound([]);
      setSuggestedCustomer(null);
      setChatInput("");
      const firstMsg = resp.ai_message || buildChatMessage(legacyFields, missing, true);
      setChatMessages([
        { role: "system", text: `📷 Photo decoded · cost ${resp.credits_charged ?? 2} credits` },
        { role: "ai", text: firstMsg },
      ]);
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
      setPhotoUri(null);
      const msg =
        e?.response?.data?.detail ||
        e?.message ||
        "Could not read the photo. Try a brighter, clearer shot.";
      Alert.alert("Photo OCR failed", msg);
    }
  };

  /**
   * End-to-end Smart Paste flow (AI first, chat-on-missing).
   *   1. /smart-paste/check-duplicate runs the LLM on backend.
   *   2. If ALL required fields present → save immediately (no form).
   *   3. Else → open chat modal and let the AI ask for missing details
   *      naturally. User can type or use the keyboard 🎤 to dictate.
   */
  const runSmartPasteAI = async (text: string, fromModal = false) => {
    try {
      setPasting(true);
      setPasteStage("parsing");
      const dup = await Api.smartPasteCheckDuplicate(text);

      // Close the fallback paste modal before the next sheet animates in.
      if (fromModal) setPasteModalOpen(false);

      const missing = (dup.ai?.missing || []).filter((k) =>
        REQUIRED_FIELDS.includes(k)
      );
      const legacyFields = dup.fields || {};

      // If AI detected an alternative phone but the user hasn't enabled
      // the Alt-Phone field on the label, surface a one-line notification
      // so they know it was found but won't print / save unless enabled.
      let altPhoneWarning: string | null = null;
      const altPhoneFound = (legacyFields.customer_alt_phone || "").trim();
      if (altPhoneFound) {
        try {
          const settings = await Api.getSettings();
          const altOn = !!(settings as any)?.label_fields?.alt_phone;
          if (!altOn) {
            altPhoneWarning =
              `⚠️ Found a second phone (${altPhoneFound}) but "Alt Phone" ` +
              `field is OFF in Settings → Label Fields. ` +
              `Turn it ON to save & print this number.`;
          }
        } catch {
          /* ignore — non-blocking */
        }
      }

      setPasting(false);
      setPasteStage("");

      // All required present + no duplicates → save directly, no UI.
      if (missing.length === 0 && (dup.duplicates || []).length === 0 && !altPhoneWarning) {
        await saveFromFields(legacyFields);
        return;
      }

      // Otherwise: open chat modal. Seed first AI bubble from the initial
      // parse (no extra LLM call needed — we already have `fields`).
      setChatFields(legacyFields);
      setChatComplexity((dup.ai?.complexity as any) || "");
      setChatReason(dup.ai?.reason || "");
      setDupFound(dup.duplicates || []);
      setSuggestedCustomer(null);
      setChatInput("");
      const firstMsg = buildChatMessage(legacyFields, missing, true);
      const initialMessages: ChatMsg[] = [{ role: "ai", text: firstMsg }];
      if (altPhoneWarning) {
        initialMessages.unshift({ role: "system", text: altPhoneWarning });
      }
      setChatMessages(initialMessages);
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

  /**
   * Build the natural-language chat bubble the AI posts: "Got X, Y. Still
   * need Z." Matches the backend's template so the UX feels consistent.
   */
  const buildChatMessage = (
    legacyFields: Record<string, any>,
    missing: string[],
    isFirst: boolean,
  ): string => {
    const lines: string[] = [];
    const push = (label: string, key: string) => {
      const v = legacyFields[key];
      if (v && String(v).trim()) lines.push(`• ${label}: ${v}`);
    };
    push("Name", "customer_name");
    push("Phone", "customer_phone");
    // Show alt phone right under the primary so users see both at a glance.
    if (legacyFields.customer_alt_phone)
      lines.push(`• Alt Phone: ${legacyFields.customer_alt_phone}`);
    push("Address", "address_line1");
    if (legacyFields.address_line2) lines.push(`• Landmark: ${legacyFields.address_line2}`);
    push("City", "city");
    push("State", "state");
    push("Pincode", "pincode");
    // Items can be array or string.
    const itemsVal = legacyFields.items;
    const itemsText = Array.isArray(itemsVal) ? itemsVal.join(", ") : itemsVal;
    if (itemsText && String(itemsText).trim()) lines.push(`• Items: ${itemsText}`);
    if (legacyFields.amount != null && legacyFields.amount !== "")
      lines.push(`• Amount: ₹${legacyFields.amount}`);
    if (legacyFields.payment_mode)
      lines.push(`• Payment: ${String(legacyFields.payment_mode).toUpperCase()}`);

    const known = lines.length
      ? lines.join("\n")
      : "• (nothing yet)";

    if (missing.length === 0) {
      return `All set!\n${known}\n\nSaving the order now…`;
    }
    const miss = missing
      .map((k) => `• ${FIELD_META[k]?.label || k}`)
      .join("\n");
    const prefix = isFirst ? "Got these so far:" : "Updated:";
    return `${prefix}\n${known}\n\nStill need:\n${miss}\n\nPlease share (type or tap 🎤 on the keyboard to speak).`;
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
      const stillMissing = REQUIRED_FIELDS.filter((k) => {
        const snakeKey: Record<string, string> = {
          NAME: "customer_name",
          PHONE: "customer_phone",
          ADDRESS_1: "address_line1",
          CITY: "city",
          STATE: "state",
          PINCODE: "pincode",
          AMOUNT: "amount",
        };
        const v = updated[snakeKey[k]];
        return !v || !String(v).trim();
      });
      const msg = buildChatMessage(updated, stillMissing, false);
      const usedNote = addedItemsLabel
        ? `Used past address + items for ${suggestedCustomer.customer_name}.${addedItemsLabel}`
        : `Used past address for ${suggestedCustomer.customer_name}.`;
      setChatMessages((prev) => [
        ...prev,
        { role: "system", text: usedNote },
        { role: "ai", text: msg },
      ]);
      setSuggestedCustomer(null);
      if (stillMissing.length === 0 && dupFound.length === 0) {
        saveFromFields(updated);
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

  /**
   * User sent a chat reply. Push it to the backend `/smart-paste/chat`
   * endpoint which merges the reply into current fields, re-parses via
   * LLM, and returns updated fields + the next AI message.
   */
  const sendChatReply = async () => {
    const reply = chatInput.trim();
    if (!reply) return;
    // Add user bubble + an optimistic "…" AI typing indicator so the UI
    // feels responsive even though the LLM call itself takes a few seconds.
    setChatMessages((prev) => [
      ...prev,
      { role: "user", text: reply },
      { role: "ai", text: "…", typing: true },
    ]);
    setChatInput("");
    setChatSending(true);
    try {
      const res = await Api.smartPasteChat(chatFields, reply);
      setChatFields(res.fields || {});
      setChatComplexity((res.complexity as any) || "");
      setChatReason(res.reason || "");
      const msg = buildChatMessage(res.fields || {}, res.missing || [], false);
      // Replace the typing placeholder with the real AI bubble.
      setChatMessages((prev) => {
        const out = [...prev];
        const idx = out.findIndex((m) => m.typing);
        const bubble: ChatMsg = { role: "ai", text: msg };
        if (idx >= 0) out[idx] = bubble;
        else out.push(bubble);
        return out;
      });
      setChatSending(false);
      if (res.complete) {
        // Handle duplicate confirmation if any were flagged earlier.
        if (dupFound.length > 0) {
          const lines = dupFound
            .map((d: any, i: number) => {
              const id =
                d.kind === "shipment" ? d.tracking_id : `PEND ${String(d.id).slice(0, 6)}`;
              const why = (d.match_on || []).join(" + ") || "match";
              const oid = d.order_id ? ` · #${d.order_id}` : "";
              return `${i + 1}. ${id} — ${d.customer_name}${oid}  (${why})`;
            })
            .join("\n");
          Alert.alert(
            "Possible duplicate",
            `Found ${dupFound.length} existing order${
              dupFound.length > 1 ? "s" : ""
            } with the same phone/order ID:\n\n${lines}\n\nCreate this order anyway?`,
            [
              { text: "Cancel", style: "cancel" },
              {
                text: "Create anyway",
                style: "destructive",
                onPress: () => saveFromFields(res.fields || {}),
              },
            ]
          );
          return;
        }
        await saveFromFields(res.fields || {});
      }
    } catch (e: any) {
      setChatSending(false);
      setChatMessages((prev) => {
        const out = prev.filter((m) => !m.typing);
        out.push({ role: "system", text: "Something went wrong. Please try again." });
        return out;
      });
    }
  };

  const closeChat = () => {
    setChatOpen(false);
    setChatMessages([]);
    setChatFields({});
    setChatInput("");
    setDupFound([]);
    setSuggestedCustomer(null);
  };

  /**
   * Build a canonical 14-line KEY: value block from the final fields
   * and post it to /api/smart-paste. Backend's regex parser accepts
   * this verbatim — no wasted LLM call.
   */
  const saveFromFields = async (legacyFields: Record<string, any>) => {
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
      await Api.smartPasteCreate(text, true);  // skip_llm = true (we already have canonical fields → save 2-4s)
      setPasting(false);
      setChatSending(false);
      setPasteStage("");
      setChatMessages((prev) => [
        ...prev,
        { role: "ai", text: "✅ Order added to your Pending Orders queue." },
      ]);
      // Close the chat after a brief moment so the user sees the confirmation.
      setTimeout(() => closeChat(), 1200);
      Alert.alert(
        "✅ Order added",
        "Order queued in Orders tab. Ready to ship.",
        [
          { text: "OK", style: "cancel" },
          { text: "View Orders →", onPress: () => router.push("/orders") },
        ]
      );
    } catch (err: any) {
      setPasting(false);
      setChatSending(false);
      setPasteStage("");
      setChatMessages((prev) => [
        ...prev,
        {
          role: "system",
          text:
            "Save failed: " +
            (err?.response?.data?.detail || err?.message || "please try again"),
        },
      ]);
    }
  };

  const submitPasteModal = async () => {
    if (!pasteText.trim()) {
      Alert.alert("Empty", "Please paste some text first.");
      return;
    }
    await runSmartPasteAI(pasteText, true);
  };

  const pasteFromClipboardToModal = async () => {
    try {
      const t = await Clipboard.getStringAsync();
      if (t) setPasteText(t);
    } catch {
      /* ignore */
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerKicker}>COURIER LABEL MANAGER</Text>
          <Text style={styles.headerTitle}>નમસ્તે 👋</Text>
          <Text style={styles.headerSub}>Ship smart. Print fast.</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <TouchableOpacity
            testID="smart-paste-btn"
            style={[styles.headerAction, { backgroundColor: "#7C3AED" }]}
            onPress={handleSmartPaste}
            disabled={pasting}
          >
            {pasting ? (
              <ActivityIndicator color="#fff" size="small" />
            ) : (
              <Ionicons name="sparkles" size={20} color="#fff" />
            )}
          </TouchableOpacity>
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

      {/* Smart Paste Fallback Modal — clipboard was empty, user pastes raw
          WhatsApp/SMS text here OR uploads a photo (Phase-5d). */}
      <Modal
        visible={pasteModalOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPasteModalOpen(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Ionicons name="sparkles" size={18} color="#7C3AED" />
              <Text style={styles.modalTitle}>Smart Paste</Text>
              <TouchableOpacity onPress={() => setPasteModalOpen(false)} hitSlop={10}>
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>

            {/* Tabs: Text vs Photo */}
            <View style={styles.tabRow}>
              <TouchableOpacity
                testID="smart-paste-tab-text"
                onPress={() => setPasteTab("text")}
                style={[styles.tabBtn, pasteTab === "text" && styles.tabBtnActive]}
                disabled={pasting || photoUploading}
              >
                <Ionicons
                  name="document-text-outline"
                  size={14}
                  color={pasteTab === "text" ? "#fff" : "#475569"}
                />
                <Text
                  style={[
                    styles.tabBtnTxt,
                    pasteTab === "text" && styles.tabBtnTxtActive,
                  ]}
                >
                  Text
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="smart-paste-tab-photo"
                onPress={() => {
                  setPasteTab("photo");
                  setPasteText(""); // forget clipboard text — user is going photo
                }}
                style={[styles.tabBtn, pasteTab === "photo" && styles.tabBtnActive]}
                disabled={pasting || photoUploading}
              >
                <Ionicons
                  name="camera-outline"
                  size={14}
                  color={pasteTab === "photo" ? "#fff" : "#475569"}
                />
                <Text
                  style={[
                    styles.tabBtnTxt,
                    pasteTab === "photo" && styles.tabBtnTxtActive,
                  ]}
                >
                  Photo
                </Text>
                <View style={styles.tabPill}>
                  <Text style={styles.tabPillTxt}>1.5 cr</Text>
                </View>
              </TouchableOpacity>
            </View>

            {pasteTab === "text" ? (
              <>
                <Text style={styles.modalHint}>
                  Paste any WhatsApp/SMS order text below. AI will auto-fill the form — no formatting needed.
                </Text>

                <View style={styles.modalQuickRow}>
                  <TouchableOpacity style={styles.modalQuickBtn} onPress={pasteFromClipboardToModal}>
                    <Ionicons name="clipboard-outline" size={14} color="#7C3AED" />
                    <Text style={styles.modalQuickBtnText}>Paste from Clipboard</Text>
                  </TouchableOpacity>
                </View>

                <TextInput
                  testID="smart-paste-input"
                  value={pasteText}
                  onChangeText={setPasteText}
                  multiline
                  placeholder={
                    "e.g. Ramesh Patel, 9876543210, 45 MG Road, Ahmedabad 380001, Saree 2 pcs, 1200 COD"
                  }
                  placeholderTextColor="#9CA3AF"
                  style={styles.modalInput}
                  autoFocus
                  editable={!pasting}
                />

                {pasting && (
                  <View style={styles.aiStatusRow}>
                    <ActivityIndicator size="small" color="#7C3AED" />
                    <Text style={styles.aiStatusText}>
                      {pasteStage === "saving" ? "Saving order…" : "AI is parsing…"}
                    </Text>
                  </View>
                )}

                <View style={styles.modalActions}>
                  <TouchableOpacity
                    style={[styles.modalBtn, { backgroundColor: "#E5E7EB" }]}
                    onPress={() => setPasteModalOpen(false)}
                    disabled={pasting}
                  >
                    <Text style={[styles.modalBtnText, { color: colors.text }]}>Cancel</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="smart-paste-submit"
                    style={[styles.modalBtn, { backgroundColor: "#7C3AED", opacity: pasting ? 0.7 : 1 }]}
                    onPress={submitPasteModal}
                    disabled={pasting}
                  >
                    {pasting ? (
                      <ActivityIndicator size="small" color="#fff" />
                    ) : (
                      <>
                        <Ionicons name="sparkles" size={14} color="#fff" />
                        <Text style={styles.modalBtnText}>AI Parse & Queue</Text>
                      </>
                    )}
                  </TouchableOpacity>
                </View>
              </>
            ) : (
              <>
                <Text style={styles.modalHint}>
                  📷 Take a photo (or pick from gallery) of any address —
                  handwritten paper, visiting card, packing slip, screenshot.
                  AI will read everything in Gujarati / Hindi / English.
                </Text>

                {photoUploading ? (
                  <View style={styles.photoUploadCard}>
                    <ActivityIndicator size="large" color="#7C3AED" />
                    <Text style={styles.photoUploadTxt}>
                      🤖 Reading the photo… (5–20 sec)
                    </Text>
                  </View>
                ) : (
                  <View style={styles.photoBtnGrid}>
                    <TouchableOpacity
                      testID="smart-paste-camera-btn"
                      onPress={() => pickAndProcessPhoto("camera")}
                      style={styles.photoBigBtn}
                      activeOpacity={0.85}
                    >
                      <Ionicons name="camera" size={28} color="#7C3AED" />
                      <Text style={styles.photoBigBtnTxt}>Camera</Text>
                      <Text style={styles.photoBigBtnSub}>Live capture</Text>
                    </TouchableOpacity>
                    <TouchableOpacity
                      testID="smart-paste-gallery-btn"
                      onPress={() => pickAndProcessPhoto("gallery")}
                      style={styles.photoBigBtn}
                      activeOpacity={0.85}
                    >
                      <Ionicons name="images" size={28} color="#7C3AED" />
                      <Text style={styles.photoBigBtnTxt}>Gallery</Text>
                      <Text style={styles.photoBigBtnSub}>Pick existing</Text>
                    </TouchableOpacity>
                  </View>
                )}

                <View style={styles.photoTipBox}>
                  <Ionicons name="bulb-outline" size={14} color="#92400E" />
                  <Text style={styles.photoTipTxt}>
                    Tip: bright light + flat surface = best results. Multiple
                    phones? AI picks the first 2. No name? Shop name is used.
                  </Text>
                </View>

                <View style={styles.modalActions}>
                  <TouchableOpacity
                    style={[styles.modalBtn, { backgroundColor: "#E5E7EB", flex: 1 }]}
                    onPress={() => setPasteModalOpen(false)}
                    disabled={photoUploading}
                  >
                    <Text style={[styles.modalBtnText, { color: colors.text }]}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        </View>
      </Modal>

      {/* Smart Paste Chat Modal — conversational flow. AI asks for missing
          details in natural language; user types replies or taps the
          keyboard 🎤 to dictate (built-in Android/iOS speech-to-text).
          Complexity badge + repeat-customer banner live in the header. */}
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
          <View style={[styles.modalCard, { maxHeight: "92%", minHeight: "70%" }]}>
            <View style={styles.modalHeader}>
              <Ionicons name="chatbubbles" size={18} color="#7C3AED" />
              <Text style={styles.modalTitle}>Smart Paste Chat</Text>
              <TouchableOpacity onPress={closeChat} hitSlop={10} disabled={chatSending}>
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>

            {/* Complexity + duplicate badges, if any */}
            <View style={styles.badgesRow}>
              {!!chatComplexity && (
                <View
                  style={[
                    styles.badge,
                    chatComplexity === "complex"
                      ? styles.badgeComplex
                      : chatComplexity === "medium"
                      ? styles.badgeMedium
                      : styles.badgeSimple,
                  ]}
                >
                  <Ionicons
                    name={chatComplexity === "complex" ? "warning" : "checkmark-circle"}
                    size={12}
                    color={
                      chatComplexity === "complex"
                        ? "#92400E"
                        : chatComplexity === "medium"
                        ? "#92400E"
                        : "#065F46"
                    }
                  />
                  <Text
                    style={[
                      styles.badgeText,
                      {
                        color:
                          chatComplexity === "complex"
                            ? "#92400E"
                            : chatComplexity === "medium"
                            ? "#92400E"
                            : "#065F46",
                      },
                    ]}
                  >
                    {chatComplexity === "complex"
                      ? "Complex address"
                      : chatComplexity === "medium"
                      ? "Medium complexity"
                      : "Simple address"}
                  </Text>
                </View>
              )}
              {dupFound.length > 0 && (
                <View style={[styles.badge, styles.badgeDup]}>
                  <Ionicons name="copy-outline" size={12} color="#9F1239" />
                  <Text style={[styles.badgeText, { color: "#9F1239" }]}>
                    {dupFound.length} possible duplicate
                    {dupFound.length > 1 ? "s" : ""}
                  </Text>
                </View>
              )}
            </View>

            {/* Repeat-customer suggestion banner */}
            {suggestedCustomer && (
              <View style={styles.suggestBanner}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.suggestBannerTitle}>🎯 Repeat customer</Text>
                  <Text style={styles.suggestBannerBody} numberOfLines={2}>
                    {suggestedCustomer.customer_name} —{" "}
                    {suggestedCustomer.address_line1}
                    {suggestedCustomer.city ? `, ${suggestedCustomer.city}` : ""}
                    {suggestedCustomer._count > 1
                      ? ` (${suggestedCustomer._count} past orders)`
                      : ""}
                  </Text>
                </View>
                <TouchableOpacity onPress={applySuggestedCustomer} style={styles.suggestBtn}>
                  <Text style={styles.suggestBtnText}>Use</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => setSuggestedCustomer(null)}
                  hitSlop={8}
                  style={{ marginLeft: 6 }}
                >
                  <Ionicons name="close" size={18} color="#065F46" />
                </TouchableOpacity>
              </View>
            )}

            {/* Message stream */}
            <ScrollView
              style={{ flex: 1, marginVertical: 8 }}
              contentContainerStyle={{ paddingBottom: 8 }}
              keyboardShouldPersistTaps="handled"
            >
              {chatMessages.map((m, i) => {
                if (m.role === "system") {
                  return (
                    <Text key={i} style={styles.chatSystemText}>
                      {m.text}
                    </Text>
                  );
                }
                const isAI = m.role === "ai";
                // Typing indicator bubble — animated "…" while the AI
                // response is in-flight.
                if (m.typing) {
                  return (
                    <View
                      key={i}
                      style={[styles.chatBubble, styles.chatBubbleAI]}
                    >
                      <Text style={styles.chatBubbleKicker}>🤖 AI</Text>
                      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <ActivityIndicator size="small" color="#7C3AED" />
                        <Text style={[styles.chatBubbleText, { color: "#6B7280" }]}>
                          Thinking…
                        </Text>
                      </View>
                    </View>
                  );
                }
                return (
                  <View
                    key={i}
                    style={[
                      styles.chatBubble,
                      isAI ? styles.chatBubbleAI : styles.chatBubbleUser,
                    ]}
                  >
                    {isAI && (
                      <Text style={styles.chatBubbleKicker}>🤖 AI</Text>
                    )}
                    <Text
                      style={[
                        styles.chatBubbleText,
                        isAI ? { color: "#111827" } : { color: "#fff" },
                      ]}
                    >
                      {m.text}
                    </Text>
                  </View>
                );
              })}
            </ScrollView>

            {/* Input row + send button. Users can tap the keyboard 🎤 icon
                to dictate (native STT on iOS/Android). When all required
                fields are already filled, a green "Save Now" button
                appears above the input so the user can commit without
                another chat turn. */}
            {chatComplete && !chatSending && (
              <TouchableOpacity
                testID="chat-save-now"
                style={styles.chatSaveNowBtn}
                onPress={() => saveFromFields(chatFields)}
              >
                <Ionicons name="checkmark-circle" size={16} color="#fff" />
                <Text style={styles.chatSaveNowText}>Save order now</Text>
              </TouchableOpacity>
            )}
            <View style={styles.chatInputRow}>
              <TextInput
                testID="chat-input"
                value={chatInput}
                onChangeText={setChatInput}
                placeholder={
                  chatComplete
                    ? "Add more info (optional) or tap Save"
                    : "Type or tap 🎤 on keyboard to speak…"
                }
                placeholderTextColor="#9CA3AF"
                multiline
                style={styles.chatInput}
                editable={!chatSending}
                onSubmitEditing={sendChatReply}
                blurOnSubmit={false}
              />
              <TouchableOpacity
                testID="chat-send"
                onPress={sendChatReply}
                disabled={chatSending || !chatInput.trim()}
                style={[
                  styles.chatSendBtn,
                  {
                    backgroundColor:
                      chatSending || !chatInput.trim() ? "#D1D5DB" : "#7C3AED",
                  },
                ]}
              >
                <Ionicons name="send" size={18} color="#fff" />
              </TouchableOpacity>
            </View>
          </View>
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
                  હજી કોઈ shipment નથી. પહેલી shipment બનાવો.
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
