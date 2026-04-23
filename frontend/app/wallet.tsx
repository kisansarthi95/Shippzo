/**
 * Wallet screen (Phase-4a).
 *
 *  - Large balance card
 *  - Purchase credits (₹10 / ₹100 / ₹500 / ₹1000 presets + custom)
 *  - Full history list with type badges, timestamps, address-type chips
 *
 * MOCKED PAYMENT: server-side top-up is direct add-credit with no Razorpay
 * round-trip; a banner makes that explicit so QA testers aren't surprised.
 * Razorpay wiring lands in Phase-4c.
 */
import React, { useCallback, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
  Modal,
  TextInput,
  KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useFocusEffect, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Api, CreditHistoryEntry, Wallet } from "../lib/api";
import { colors } from "../lib/theme";

const PRESETS = [100, 500, 1000, 2000];

export default function WalletScreen() {
  const router = useRouter();
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [history, setHistory] = useState<CreditHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [amount, setAmount] = useState("100");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [w, h] = await Promise.all([Api.getWallet(), Api.getWalletHistory(200)]);
      setWallet(w);
      setHistory(h.entries);
    } catch (e: any) {
      Alert.alert("Could not load wallet", e?.message || "Please try again");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load().catch(() => {});
    }, [load]),
  );

  const submitPurchase = async () => {
    const inr = Number((amount || "0").replace(/[^\d.]/g, ""));
    if (!(inr > 0)) {
      Alert.alert("Enter a valid amount");
      return;
    }
    if (inr < 10 || inr > 100000) {
      Alert.alert("Amount must be between ₹10 and ₹1,00,000");
      return;
    }
    try {
      setBusy(true);
      const r = await Api.purchaseCredits(inr);
      Alert.alert(
        "Credits added",
        `₹${inr} → ${r.credits_added} credits. New balance: ${r.balance}.`,
      );
      setPurchaseOpen(false);
      await load();
    } catch (e: any) {
      Alert.alert("Purchase failed", e?.response?.data?.detail || e?.message || "Please try again");
    } finally {
      setBusy(false);
    }
  };

  const headerRight = useMemo(
    () => () => (
      <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
        <Ionicons name="close" size={24} color={colors.text} />
      </TouchableOpacity>
    ),
    [router],
  );

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Wallet & Credits",
          headerRight,
          headerBackVisible: false,
          headerStyle: { backgroundColor: colors.background },
        }}
      />
      <ScrollView
        testID="wallet-scroll"
        contentContainerStyle={{ padding: 16, paddingBottom: 48 }}
      >
        {/* Mocked banner */}
        <View style={styles.mockBanner}>
          <Ionicons name="information-circle-outline" size={16} color="#92400E" />
          <Text style={styles.mockBannerTxt}>
            Payments are mocked for now. Top-ups are added instantly with no charge.
          </Text>
        </View>

        {/* Balance hero */}
        <View style={styles.balanceCard}>
          <Text style={styles.balanceHint}>Remaining balance</Text>
          <View style={styles.balanceRow}>
            <Ionicons name="wallet" size={28} color="#FFF" />
            <Text style={styles.balanceValue}>
              {loading ? "--" : (wallet?.remaining_credits ?? 0).toFixed(2)}
            </Text>
            <Text style={styles.balanceUnit}>credits</Text>
          </View>
          <View style={styles.balanceMeta}>
            <View style={styles.metaPill}>
              <Text style={styles.metaPillLbl}>Total</Text>
              <Text style={styles.metaPillVal}>{(wallet?.total_credits ?? 0).toFixed(2)}</Text>
            </View>
            <View style={styles.metaPill}>
              <Text style={styles.metaPillLbl}>Used</Text>
              <Text style={styles.metaPillVal}>{(wallet?.used_credits ?? 0).toFixed(2)}</Text>
            </View>
          </View>

          <TouchableOpacity
            testID="wallet-topup-btn"
            style={styles.topupBtn}
            onPress={() => setPurchaseOpen(true)}
          >
            <Ionicons name="add-circle" size={18} color={colors.primary} />
            <Text style={styles.topupTxt}>Top up credits</Text>
          </TouchableOpacity>
        </View>

        {/* Rate card */}
        <View style={styles.rateCard}>
          <Text style={styles.rateTitle}>Pricing</Text>
          <Text style={styles.rateLine}>• ₹100 = 100 credits (1:1)</Text>
          <Text style={styles.rateLine}>• AI address check: 0.5 – 2 credits per order (max 2)</Text>
          <Text style={styles.rateLine}>• Shipment overage (after plan): Silver 4 · Gold 2 · Platinum 1</Text>
          <Text style={styles.rateLine}>• Free Trial: AI charges are waived</Text>
        </View>

        {/* History */}
        <View style={styles.historyHeader}>
          <Text style={styles.historyTitle}>Credit history</Text>
          {history.length > 0 && (
            <Text style={styles.historyCount}>{history.length}</Text>
          )}
        </View>
        {loading ? (
          <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />
        ) : history.length === 0 ? (
          <View style={styles.emptyBox}>
            <Ionicons name="receipt-outline" size={28} color="#94A3B8" />
            <Text style={styles.emptyTxt}>No credit activity yet</Text>
          </View>
        ) : (
          history.map((e) => <HistoryRow key={e.id} entry={e} />)
        )}
      </ScrollView>

      <PurchaseModal
        open={purchaseOpen}
        amount={amount}
        setAmount={setAmount}
        busy={busy}
        onClose={() => setPurchaseOpen(false)}
        onSubmit={submitPurchase}
      />
    </SafeAreaView>
  );
}

// ------------ History Row -------------------------------------------------

function HistoryRow({ entry }: { entry: CreditHistoryEntry }) {
  const isDebit = entry.credits < 0;
  const iconName = {
    ai_processing: "sparkles-outline",
    shipment_charge: "cube-outline",
    purchase: "cash-outline",
    bonus: "gift-outline",
    refund: "return-down-back-outline",
  }[entry.type] as any;
  const tone = isDebit ? "#B91C1C" : "#047857";
  const bg = isDebit ? "#FEE2E2" : "#D1FAE5";

  const dt = new Date(entry.created_at);
  const when = `${dt.toLocaleDateString()} · ${dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;

  return (
    <View style={styles.row}>
      <View style={[styles.rowIcon, { backgroundColor: bg }]}>
        <Ionicons name={iconName} size={18} color={tone} />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        <Text style={styles.rowDesc} numberOfLines={2}>{entry.description}</Text>
        <Text style={styles.rowMeta}>
          {when}
          {entry.order_id ? ` · ${entry.order_id.slice(0, 8)}…` : ""}
          {entry.address_type ? ` · ${entry.address_type}` : ""}
        </Text>
      </View>
      <View style={{ alignItems: "flex-end" }}>
        <Text style={[styles.rowDelta, { color: tone }]}>
          {isDebit ? "" : "+"}{entry.credits.toFixed(2)}
        </Text>
        <Text style={styles.rowBal}>bal {entry.balance_after.toFixed(2)}</Text>
      </View>
    </View>
  );
}

// ------------ Top-up Modal ------------------------------------------------

function PurchaseModal({
  open, amount, setAmount, busy, onClose, onSubmit,
}: {
  open: boolean;
  amount: string;
  setAmount: (s: string) => void;
  busy: boolean;
  onClose: () => void;
  onSubmit: () => void;
}) {
  return (
    <Modal visible={open} animationType="slide" transparent>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1, backgroundColor: "rgba(0,0,0,0.45)", justifyContent: "flex-end" }}
      >
        <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Top up credits</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={22} color="#334155" />
            </TouchableOpacity>
          </View>
          <Text style={styles.sheetHint}>
            ₹ × 1 = credits × 1. No GST / fees on the mock flow.
          </Text>

          <View style={styles.presetRow}>
            {PRESETS.map((p) => (
              <TouchableOpacity
                key={p}
                testID={`preset-${p}`}
                style={[
                  styles.preset,
                  Number(amount) === p && styles.presetActive,
                ]}
                onPress={() => setAmount(String(p))}
              >
                <Text style={[
                  styles.presetTxt,
                  Number(amount) === p && styles.presetActiveTxt,
                ]}>
                  ₹{p}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          <Text style={styles.label}>Amount (₹)</Text>
          <TextInput
            testID="purchase-amount"
            style={styles.input}
            value={amount}
            onChangeText={(t) => setAmount(t.replace(/[^\d]/g, ""))}
            keyboardType="number-pad"
            placeholder="100"
          />

          <TouchableOpacity
            testID="purchase-submit"
            disabled={busy}
            style={[styles.confirmBtn, busy && { opacity: 0.6 }]}
            onPress={onSubmit}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.confirmTxt}>
                Add {Math.max(0, Number(amount) || 0)} credits
              </Text>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  mockBanner: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#FEF3C7", borderColor: "#FCD34D", borderWidth: 1,
    padding: 10, borderRadius: 10, marginBottom: 14,
  },
  mockBannerTxt: { flex: 1, color: "#92400E", fontSize: 12, fontWeight: "600" },

  balanceCard: {
    backgroundColor: "#0F172A", borderRadius: 18, padding: 18, marginBottom: 14,
  },
  balanceHint: { color: "#94A3B8", fontSize: 12, fontWeight: "600", letterSpacing: 0.6 },
  balanceRow: { flexDirection: "row", alignItems: "baseline", gap: 10, marginTop: 8 },
  balanceValue: { color: "#fff", fontWeight: "900", fontSize: 40, letterSpacing: -0.8 },
  balanceUnit: { color: "#CBD5E1", fontSize: 14, fontWeight: "700", marginLeft: 4 },
  balanceMeta: { flexDirection: "row", gap: 10, marginTop: 10 },
  metaPill: {
    backgroundColor: "#1E293B", borderRadius: 10, paddingHorizontal: 10, paddingVertical: 6,
  },
  metaPillLbl: { color: "#94A3B8", fontSize: 10, fontWeight: "700", letterSpacing: 0.5 },
  metaPillVal: { color: "#fff", fontWeight: "800", fontSize: 14 },

  topupBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: "#fff", borderRadius: 10, paddingVertical: 11, marginTop: 14,
  },
  topupTxt: { color: colors.primary, fontWeight: "800", fontSize: 14 },

  rateCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 16,
  },
  rateTitle: { fontSize: 11, fontWeight: "800", color: "#64748B", letterSpacing: 0.5, marginBottom: 6 },
  rateLine: { fontSize: 12.5, color: "#334155", lineHeight: 20 },

  historyHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingBottom: 6, marginBottom: 6,
  },
  historyTitle: { fontSize: 15, fontWeight: "800", color: "#1E293B" },
  historyCount: {
    fontSize: 11, fontWeight: "800", color: "#64748B",
    backgroundColor: "#E2E8F0", paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
  },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: "#fff", borderRadius: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
    padding: 10, marginBottom: 8,
  },
  rowIcon: { width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center" },
  rowDesc: { color: "#1F2937", fontSize: 13, fontWeight: "700" },
  rowMeta: { color: "#64748B", fontSize: 11, fontWeight: "500" },
  rowDelta: { fontSize: 14, fontWeight: "900" },
  rowBal: { fontSize: 10, color: "#94A3B8", fontWeight: "700" },
  emptyBox: { alignItems: "center", paddingVertical: 40, gap: 8 },
  emptyTxt: { color: "#64748B", fontSize: 13 },

  sheet: {
    backgroundColor: "#fff", borderTopLeftRadius: 18, borderTopRightRadius: 18,
    padding: 18, paddingBottom: 28, gap: 10,
  },
  sheetHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  sheetTitle: { fontSize: 16, fontWeight: "900", color: "#0F172A" },
  sheetHint: { fontSize: 12, color: "#64748B" },
  presetRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 8 },
  preset: {
    paddingHorizontal: 14, paddingVertical: 10, borderRadius: 10,
    borderWidth: 1, borderColor: "#CBD5E1", backgroundColor: "#F8FAFC",
  },
  presetActive: { borderColor: colors.primary, backgroundColor: "#FFF7ED" },
  presetTxt: { fontWeight: "800", color: "#334155", fontSize: 14 },
  presetActiveTxt: { color: colors.primary },
  label: { fontSize: 11, color: "#64748B", fontWeight: "800", letterSpacing: 0.5, marginTop: 8 },
  input: {
    borderWidth: 1.5, borderColor: "#CBD5E1", borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 11, fontSize: 15, fontWeight: "700",
  },
  confirmBtn: {
    marginTop: 12, backgroundColor: colors.primary,
    borderRadius: 10, paddingVertical: 13, alignItems: "center",
  },
  confirmTxt: { color: "#fff", fontWeight: "900", fontSize: 15, letterSpacing: 0.3 },
});
