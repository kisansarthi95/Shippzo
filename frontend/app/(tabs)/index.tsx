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
import { Api, Shipment } from "../../lib/api";
import { colors } from "../../lib/theme";
import UsageMeter from "../../components/UsageMeter";

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

  // Missing-Fields Modal — shown when AI couldn't extract one or more
  // required fields. Mirrors what the Custom GPT does in chat: asks the
  // user for the missing bits, then retries the save.
  const [missingModalOpen, setMissingModalOpen] = useState(false);
  const [missingFieldValues, setMissingFieldValues] = useState<Record<string, string>>({});
  const [knownAIFields, setKnownAIFields] = useState<Record<string, string>>({});
  const [originalPasteText, setOriginalPasteText] = useState("");
  const [missingRequired, setMissingRequired] = useState<string[]>([]);

  // Human-readable labels + placeholders for each schema field.
  const FIELD_META: Record<
    string,
    { label: string; placeholder: string; keyboard?: "default" | "phone-pad" | "numeric" }
  > = {
    NAME:      { label: "Customer Name",    placeholder: "e.g. Ramesh Patel" },
    PHONE:     { label: "Mobile Number",    placeholder: "10-digit mobile", keyboard: "phone-pad" },
    ADDRESS_1: { label: "Address",          placeholder: "House / street / area" },
    ADDRESS_2: { label: "Address Line 2",   placeholder: "Landmark / optional" },
    CITY:      { label: "City",             placeholder: "e.g. Ahmedabad" },
    STATE:     { label: "State",            placeholder: "e.g. Gujarat" },
    PINCODE:   { label: "Pincode",          placeholder: "6 digits", keyboard: "numeric" },
    ITEMS:     { label: "Item(s)",          placeholder: "e.g. Saree x 2" },
    AMOUNT:    { label: "Amount (₹)",       placeholder: "COD amount", keyboard: "numeric" },
    PAYMENT:   { label: "Payment",          placeholder: "COD or PAID" },
    COURIER:   { label: "Courier",          placeholder: "optional" },
    ORDER_ID:  { label: "Order ID",         placeholder: "optional" },
    WEIGHT:    { label: "Weight",           placeholder: "e.g. 500g" },
    NOTES:     { label: "Notes",            placeholder: "special instructions" },
  };

  // Fields the app treats as REQUIRED — if AI can't find these, we must ask.
  const REQUIRED_FIELDS = [
    "NAME", "PHONE", "ADDRESS_1", "CITY", "STATE", "PINCODE", "AMOUNT",
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
      AMOUNT: legacy?.amount != null ? String(legacy.amount) : "",
      PAYMENT: (legacy?.payment_mode || "").toUpperCase(),
      COURIER: legacy?.courier_name || "",
      ORDER_ID: legacy?.order_id || "",
      WEIGHT: legacy?.weight || "",
      NOTES: legacy?.notes || "",
    };
  };

  const handleSmartPaste = async () => {
    try {
      setPasting(true);
      let text = "";
      try {
        text = (await Clipboard.getStringAsync()) || "";
      } catch {
        text = "";
      }
      if (!text.trim()) {
        // Clipboard empty → open modal so the user can paste manually.
        setPasteText("");
        setPasteModalOpen(true);
        setPasting(false);
        return;
      }
      // Happy path: AI parses raw text → check duplicates → save.
      await runSmartPasteAI(text);
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
   * End-to-end Smart Paste flow (AI first).
   *   1. /smart-paste/check-duplicate runs the LLM on backend.
   *   2. If REQUIRED fields still missing → open MissingFieldsModal so
   *      the user can fill them in (like the Custom GPT asks in chat).
   *   3. If duplicates → confirm before save.
   *   4. Otherwise create the pending order directly.
   */
  const runSmartPasteAI = async (text: string, fromModal = false) => {
    try {
      setPasting(true);
      setPasteStage("parsing");
      const dup = await Api.smartPasteCheckDuplicate(text);

      // --- Required fields missing? Ask the user. --------------------
      const missing = dup.ai?.missing || [];
      const stillNeeded = missing.filter((k) => REQUIRED_FIELDS.includes(k));
      if (stillNeeded.length > 0) {
        setPasting(false);
        setPasteStage("");
        // Close the fallback paste modal so the missing-fields sheet is
        // the only one on screen.
        if (fromModal) setPasteModalOpen(false);
        const known = fromLegacy(dup.fields || {});
        setKnownAIFields(known);
        const initial: Record<string, string> = {};
        stillNeeded.forEach((k) => { initial[k] = ""; });
        setMissingFieldValues(initial);
        setMissingRequired(stillNeeded);
        setOriginalPasteText(text);
        setMissingModalOpen(true);
        return;
      }

      // --- Duplicates? ------------------------------------------------
      if (dup.duplicates && dup.duplicates.length > 0) {
        setPasting(false);
        setPasteStage("");
        const lines = dup.duplicates
          .map((d, i) => {
            const id =
              d.kind === "shipment"
                ? d.tracking_id
                : `PEND ${String(d.id).slice(0, 6)}`;
            const why = (d.match_on || []).join(" + ") || "match";
            const oid = d.order_id ? ` · #${d.order_id}` : "";
            return `${i + 1}. ${id} — ${d.customer_name}${oid}  (${why})`;
          })
          .join("\n");
        Alert.alert(
          "Possible duplicate",
          `Found ${dup.duplicates.length} existing order${
            dup.duplicates.length > 1 ? "s" : ""
          } with the same phone/order ID:\n\n${lines}\n\nCreate this order anyway?`,
          [
            { text: "Cancel", style: "cancel" },
            {
              text: "Create anyway",
              style: "destructive",
              onPress: () => saveSmartPaste(text, fromModal),
            },
          ]
        );
        return;
      }

      // --- No duplicates → save directly. -----------------------------
      await saveSmartPaste(text, fromModal);
    } catch (e: any) {
      setPasting(false);
      setPasteStage("");
      Alert.alert(
        "Smart Paste failed",
        e?.response?.data?.detail || e?.message || "Please try again."
      );
    }
  };

  const saveSmartPaste = async (text: string, fromModal: boolean) => {
    try {
      setPasting(true);
      setPasteStage("saving");
      await Api.smartPasteCreate(text);
      setPasting(false);
      setPasteStage("");
      if (fromModal) {
        setPasteModalOpen(false);
        setPasteText("");
      }
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
      setPasteStage("");
      Alert.alert(
        "Save failed",
        err?.response?.data?.detail || err?.message || "Please try again."
      );
    }
  };

  /**
   * User has filled in the previously-missing fields. Build a 14-line
   * KEY: value appendix (which the backend's regex parser already
   * understands), append to the original paste, and re-run the AI flow.
   */
  const submitMissingFields = async () => {
    // Validate required fields are filled.
    const empty = missingRequired.filter(
      (k) => !(missingFieldValues[k] || "").trim()
    );
    if (empty.length > 0) {
      const labels = empty.map((k) => FIELD_META[k]?.label || k).join(", ");
      Alert.alert("Still missing", `Please fill in: ${labels}`);
      return;
    }
    // Build appendix so the server sees explicit KEY: value lines.
    const lines: string[] = [];
    missingRequired.forEach((k) => {
      const v = (missingFieldValues[k] || "").trim();
      if (v) lines.push(`${k}: ${v}`);
    });
    const merged = (originalPasteText || "").trim() + "\n\n" + lines.join("\n");
    setMissingModalOpen(false);
    setMissingFieldValues({});
    setMissingRequired([]);
    setKnownAIFields({});
    setOriginalPasteText("");
    // Re-run the full pipeline with the merged text.
    await runSmartPasteAI(merged, false);
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
          WhatsApp/SMS text here and the AI parses it on the backend. */}
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
          </View>
        </View>
      </Modal>

      {/* Missing Fields Modal — opens when the AI couldn't extract one or
          more REQUIRED fields. Mirrors the Custom GPT's "please provide"
          turn so nothing gets saved with blanks. */}
      <Modal
        visible={missingModalOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setMissingModalOpen(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalOverlay}
        >
          <View style={[styles.modalCard, { maxHeight: "92%" }]}>
            <View style={styles.modalHeader}>
              <Ionicons name="alert-circle" size={18} color="#D97706" />
              <Text style={styles.modalTitle}>Please add missing details</Text>
              <TouchableOpacity
                onPress={() => setMissingModalOpen(false)}
                hitSlop={10}
                disabled={pasting}
              >
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>

            <Text style={styles.modalHint}>
              AI could read the message but a few required fields were not
              clear. Please fill them in below — then we'll save the order.
            </Text>

            {/* Context chips: show what the AI DID find. */}
            {(knownAIFields.NAME || knownAIFields.PHONE || knownAIFields.CITY) ? (
              <View style={styles.aiKnownRow}>
                {knownAIFields.NAME ? (
                  <Text style={styles.aiKnownChip}>👤 {knownAIFields.NAME}</Text>
                ) : null}
                {knownAIFields.PHONE ? (
                  <Text style={styles.aiKnownChip}>📞 {knownAIFields.PHONE}</Text>
                ) : null}
                {knownAIFields.CITY ? (
                  <Text style={styles.aiKnownChip}>📍 {knownAIFields.CITY}</Text>
                ) : null}
              </View>
            ) : null}

            <ScrollView
              style={{ maxHeight: 380 }}
              keyboardShouldPersistTaps="handled"
            >
              {missingRequired.map((k) => {
                const meta = FIELD_META[k] || { label: k, placeholder: "" };
                return (
                  <View key={k} style={styles.fieldWrap}>
                    <Text style={styles.fieldLabel}>
                      {meta.label}
                      <Text style={{ color: "#DC2626" }}> *</Text>
                    </Text>
                    <TextInput
                      testID={`missing-input-${k}`}
                      value={missingFieldValues[k] || ""}
                      onChangeText={(v) =>
                        setMissingFieldValues((prev) => ({ ...prev, [k]: v }))
                      }
                      placeholder={meta.placeholder}
                      placeholderTextColor="#9CA3AF"
                      keyboardType={meta.keyboard || "default"}
                      style={styles.fieldInput}
                      editable={!pasting}
                    />
                  </View>
                );
              })}
            </ScrollView>

            {pasting && (
              <View style={styles.aiStatusRow}>
                <ActivityIndicator size="small" color="#7C3AED" />
                <Text style={styles.aiStatusText}>
                  {pasteStage === "saving" ? "Saving order…" : "Re-parsing…"}
                </Text>
              </View>
            )}

            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.modalBtn, { backgroundColor: "#E5E7EB" }]}
                onPress={() => setMissingModalOpen(false)}
                disabled={pasting}
              >
                <Text style={[styles.modalBtnText, { color: colors.text }]}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="missing-fields-submit"
                style={[
                  styles.modalBtn,
                  { backgroundColor: "#10B981", opacity: pasting ? 0.7 : 1 },
                ]}
                onPress={submitMissingFields}
                disabled={pasting}
              >
                {pasting ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <>
                    <Ionicons name="checkmark-circle" size={14} color="#fff" />
                    <Text style={styles.modalBtnText}>Save Order</Text>
                  </>
                )}
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
});
