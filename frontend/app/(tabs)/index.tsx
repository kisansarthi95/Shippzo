import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  RefreshControl,
  ActivityIndicator,
  Linking,
  Alert,
  Modal,
  TextInput,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";
import { Api, Shipment } from "../../lib/api";
import { colors } from "../../lib/theme";

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
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<Shipment[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        Api.getStats(),
        Api.listShipments({}),
      ]);
      setStats(s);
      setRecent(list.slice(0, 5));
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

  // Smart Paste — hybrid flow: auto-paste from clipboard, fallback to modal
  const [pasteModalOpen, setPasteModalOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasting, setPasting] = useState(false);

  const handleSmartPaste = async () => {
    try {
      setPasting(true);
      let text = "";
      try {
        text = (await Clipboard.getStringAsync()) || "";
      } catch {
        text = "";
      }
      // Valid structured text check: must have "NAME:" OR "PHONE:" keyword
      const hasStructure = /\b(NAME|PHONE|MOBILE|ADDRESS_1|PINCODE)\s*:/i.test(text);
      if (!text.trim() || !hasStructure) {
        // Fallback: open modal with empty textarea
        setPasteText(text || "");
        setPasteModalOpen(true);
        setPasting(false);
        return;
      }
      // Happy path: parse + save directly
      await Api.smartPasteCreate(text);
      setPasting(false);
      Alert.alert(
        "✅ Order added",
        "Order queued in Orders tab. Ready to ship.",
        [
          { text: "OK", style: "cancel" },
          { text: "View Orders →", onPress: () => router.push("/orders") },
        ]
      );
    } catch (e: any) {
      setPasting(false);
      Alert.alert("Paste failed", e?.response?.data?.detail || e?.message || "Try again");
    }
  };

  const submitPasteModal = async () => {
    if (!pasteText.trim()) {
      Alert.alert("Empty", "Please paste text first");
      return;
    }
    try {
      setPasting(true);
      await Api.smartPasteCreate(pasteText);
      setPasting(false);
      setPasteModalOpen(false);
      setPasteText("");
      Alert.alert(
        "✅ Order added",
        "Order queued in Orders tab.",
        [
          { text: "OK", style: "cancel" },
          { text: "View Orders →", onPress: () => router.push("/orders") },
        ]
      );
    } catch (e: any) {
      setPasting(false);
      Alert.alert("Parse failed", e?.response?.data?.detail || e?.message || "Invalid format");
    }
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

      {/* Smart Paste Fallback Modal */}
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
              Paste text from Shipment Parser GPT (14-line format).
            </Text>

            <View style={styles.quickRow}>
              <TouchableOpacity style={styles.quickBtn} onPress={pasteFromClipboardToModal}>
                <Ionicons name="clipboard-outline" size={14} color="#7C3AED" />
                <Text style={styles.quickBtnText}>Paste Clipboard</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.quickBtn}
                onPress={() =>
                  Linking.openURL("https://chatgpt.com/gpts").catch(() => {})
                }
              >
                <Ionicons name="chatbubbles-outline" size={14} color="#7C3AED" />
                <Text style={styles.quickBtnText}>Open GPT</Text>
              </TouchableOpacity>
            </View>

            <TextInput
              testID="smart-paste-input"
              value={pasteText}
              onChangeText={setPasteText}
              multiline
              placeholder={"NAME: ...\nPHONE: ...\nADDRESS_1: ...\nCITY: ...\nPINCODE: ...\nAMOUNT: ...\nPAYMENT: COD / PAID"}
              placeholderTextColor="#9CA3AF"
              style={styles.modalInput}
              autoFocus
            />

            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.modalBtn, { backgroundColor: "#E5E7EB" }]}
                onPress={() => setPasteModalOpen(false)}
              >
                <Text style={[styles.modalBtnText, { color: colors.text }]}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="smart-paste-submit"
                style={[styles.modalBtn, { backgroundColor: "#7C3AED" }]}
                onPress={submitPasteModal}
                disabled={pasting}
              >
                {pasting ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <>
                    <Ionicons name="sparkles" size={14} color="#fff" />
                    <Text style={styles.modalBtnText}>Auto-fill & Queue</Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </View>
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
            <View style={styles.statsGrid}>
              <StatCard
                testID="stat-total"
                label="Total"
                value={stats?.total ?? 0}
                icon="cube-outline"
                tone="neutral"
              />
              <StatCard
                testID="stat-pending"
                label="Pending"
                value={stats?.pending ?? 0}
                icon="time-outline"
                tone="warning"
              />
              <StatCard
                testID="stat-delivered"
                label="Delivered"
                value={stats?.delivered ?? 0}
                icon="checkmark-done"
                tone="success"
              />
              <StatCard
                testID="stat-revenue"
                label="Total Revenue"
                value={`₹${(stats?.revenue_total ?? 0).toFixed(0)}`}
                icon="trending-up"
                tone="primary"
              />
              <StatCard
                testID="stat-cod"
                label={`COD · ${stats?.cod_count ?? 0}`}
                value={`₹${(stats?.cod_total ?? 0).toFixed(0)}`}
                icon="cash-outline"
                tone="neutral"
              />
              <StatCard
                testID="stat-prepaid"
                label={`Prepaid · ${stats?.prepaid_count ?? 0}`}
                value={`₹${(stats?.prepaid_total ?? 0).toFixed(0)}`}
                icon="card-outline"
                tone="neutral"
              />
            </View>

            <View style={styles.quickRow}>
              <QuickAction
                testID="quick-new-shipment"
                icon="add-circle"
                label="New Shipment"
                onPress={() => router.push("/(tabs)/add")}
                primary
              />
              <QuickAction
                testID="quick-scan"
                icon="scan"
                label="Scan Tracking"
                onPress={() => router.push("/scanner?returnTo=add")}
              />
              <QuickAction
                testID="quick-export"
                icon="download"
                label="Export CSV"
                onPress={() => Linking.openURL(Api.csvUrl())}
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
}: {
  label: string;
  value: number | string;
  icon: keyof typeof Ionicons.glyphMap;
  tone: "neutral" | "warning" | "success" | "primary";
  testID?: string;
}) {
  const toneStyle =
    tone === "primary"
      ? { background: colors.primary, color: "#fff", sub: "rgba(255,255,255,0.8)" }
      : tone === "success"
      ? { background: colors.successBg, color: colors.successText, sub: "#059669" }
      : tone === "warning"
      ? { background: colors.warningBg, color: colors.warningText, sub: "#D97706" }
      : { background: colors.surface, color: colors.text, sub: colors.textMuted };

  return (
    <View
      testID={testID}
      style={[styles.statCard, { backgroundColor: toneStyle.background }]}
    >
      <Ionicons name={icon} size={18} color={toneStyle.color} />
      <Text style={[styles.statValue, { color: toneStyle.color }]}>{value}</Text>
      <Text style={[styles.statLabel, { color: toneStyle.sub }]}>{label}</Text>
    </View>
  );
}

function QuickAction({
  icon,
  label,
  onPress,
  primary,
  testID,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  primary?: boolean;
  testID?: string;
}) {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      style={[styles.quickBtn, primary && styles.quickBtnPrimary]}
    >
      <Ionicons name={icon} size={22} color={primary ? "#fff" : colors.text} />
      <Text
        style={[styles.quickLabel, primary && { color: "#fff" }]}
        numberOfLines={1}
      >
        {label}
      </Text>
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
  },
  statCard: {
    width: "47.8%",
    padding: 14,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  statValue: { fontSize: 26, fontWeight: "800", marginTop: 6 },
  statLabel: {
    fontSize: 11,
    fontWeight: "700",
    marginTop: 2,
    letterSpacing: 0.5,
    textTransform: "uppercase",
  },
  quickRow: {
    flexDirection: "row",
    paddingHorizontal: 16,
    marginTop: 14,
    gap: 10,
  },
  quickBtn: {
    flex: 1,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  quickBtnPrimary: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  quickLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.text,
    textAlign: "center",
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
  quickRow: {
    flexDirection: "row",
    gap: 8,
    marginBottom: 10,
  },
  quickBtn: {
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
  quickBtnText: { fontSize: 11, fontWeight: "700", color: "#7C3AED" },
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
});
