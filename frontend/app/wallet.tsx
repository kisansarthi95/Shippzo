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
import PhIcon from "../components/PhIcon";
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
import { Api, CreditHistoryEntry, Wallet, api } from "../lib/api";
import { colors } from "../lib/theme";

type CreditPackage = {
  amount_inr: number;
  credits: number;
  bonus: number;
  label?: string;
  popular?: boolean;
};

export default function WalletScreen() {
  const router = useRouter();
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [history, setHistory] = useState<CreditHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [purchaseOpen, setPurchaseOpen] = useState(false);
  const [amount, setAmount] = useState("100");
  const [busy, setBusy] = useState(false);
  // Admin-configured credit packages. Falls back to a sensible default
  // list while loading so the modal never appears empty.
  const [packages, setPackages] = useState<CreditPackage[]>([
    { amount_inr: 100,  credits: 100,  bonus: 0 },
    { amount_inr: 500,  credits: 520,  bonus: 20, popular: true },
    { amount_inr: 1000, credits: 1080, bonus: 80 },
    { amount_inr: 2000, credits: 2200, bonus: 200 },
  ]);

  const load = useCallback(async () => {
    try {
      const [w, h, p] = await Promise.all([
        Api.getWallet(),
        Api.getWalletHistory(200),
        api.get<{ packages: CreditPackage[] }>("/credit-packages"),
      ]);
      setWallet(w);
      setHistory(h.entries);
      if (p.data?.packages?.length) setPackages(p.data.packages);
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
    // Phase-4c: route to Razorpay Checkout. The mocked purchase
    // endpoint (/wallet/purchase) is no longer used from the UI; it
    // stays in the backend purely for reconciliation tools.
    setPurchaseOpen(false);
    router.push({ pathname: "/checkout", params: { amount: String(Math.round(inr)) } });
  };

  const headerRight = useMemo(
    () => () => (
      <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
        <PhIcon name="close" size={24} color={colors.text} />
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
        {/* Razorpay live banner */}
        <View style={styles.mockBanner}>
          <PhIcon name="shield-checkmark-outline" size={16} color="#065F46" />
          <Text style={styles.mockBannerTxt}>
            Payments are powered by Razorpay (test mode). Use card 4111 1111 1111 1111, any CVV, future expiry to test.
          </Text>
        </View>

        {/* Balance hero */}
        <View style={styles.balanceCard}>
          <Text style={styles.balanceHint}>Remaining balance</Text>
          <View style={styles.balanceRow}>
            <PhIcon name="wallet" size={28} color="#FFF" />
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
            <PhIcon name="add-circle" size={18} color={colors.primary} />
            <Text style={styles.topupTxt}>Top up credits</Text>
          </TouchableOpacity>
        </View>

        {/* Rate card */}
        <View style={styles.rateCard}>
          <View style={styles.rateTitleRow}>
            <Text style={styles.rateTitle}>Pricing</Text>
            <View style={styles.aiBadge}>
              <PhIcon name="sparkles" size={11} color="#fff" />
              <Text style={styles.aiBadgeTxt}>AI powered</Text>
            </View>
          </View>
          <Text style={styles.rateLine}>• ₹100 = 100 credits (1:1)</Text>
          <Text style={styles.rateLine}>• AI address check: 0.5 – 2 credits per order (max 2)</Text>
          <Text style={styles.rateLine}>   · simple → 0.5  ·  medium → 1  ·  complex → 2</Text>
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
            <PhIcon name="receipt-outline" size={28} color="#94A3B8" />
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
        packages={packages}
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
        <PhIcon name={iconName} size={18} color={tone} />
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
  open, amount, setAmount, packages, busy, onClose, onSubmit,
}: {
  open: boolean;
  amount: string;
  setAmount: (s: string) => void;
  packages: CreditPackage[];
  busy: boolean;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const matchedPkg = packages.find((p) => Number(p.amount_inr) === Number(amount));
  const creditsForCustom = Math.max(0, Number(amount) || 0);
  const creditsToShow = matchedPkg ? matchedPkg.credits : creditsForCustom;
  const bonusToShow = matchedPkg?.bonus || 0;
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
              <PhIcon name="close" size={22} color="#334155" />
            </TouchableOpacity>
          </View>
          <Text style={styles.sheetHint}>
            Pick a package — bigger packs include bonus credits 🎁
          </Text>

          {/* Package cards laid out as a 2-column grid for tappability */}
          <View style={styles.pkgGrid}>
            {packages.map((p) => {
              const active = Number(amount) === p.amount_inr;
              return (
                <TouchableOpacity
                  key={p.amount_inr}
                  testID={`preset-${p.amount_inr}`}
                  style={[styles.pkgCard, active && styles.pkgCardActive]}
                  onPress={() => setAmount(String(p.amount_inr))}
                >
                  {p.popular ? (
                    <View style={styles.popBadge}>
                      <Text style={styles.popBadgeTxt}>POPULAR</Text>
                    </View>
                  ) : null}
                  <Text style={[styles.pkgAmount, active && { color: "#fff" }]}>
                    ₹{p.amount_inr}
                  </Text>
                  <Text style={[styles.pkgCredits, active && { color: "rgba(255,255,255,0.9)" }]}>
                    {p.credits} credits
                  </Text>
                  {p.bonus > 0 ? (
                    <View style={[styles.pkgBonusPill, active && { backgroundColor: "rgba(255,255,255,0.2)" }]}>
                      <PhIcon name="gift" size={10} color={active ? "#fff" : "#047857"} />
                      <Text style={[styles.pkgBonusTxt, active && { color: "#fff" }]}>
                        +{p.bonus} bonus
                      </Text>
                    </View>
                  ) : null}
                  {p.label ? (
                    <Text style={[styles.pkgLabel, active && { color: "rgba(255,255,255,0.85)" }]}>
                      {p.label}
                    </Text>
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.label}>Or enter a custom amount (₹)</Text>
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
                Add {creditsToShow} credits{bonusToShow ? ` (+${bonusToShow} bonus)` : ""}
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
  rateTitleRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 },
  rateTitle: { fontSize: 11, fontWeight: "800", color: "#64748B", letterSpacing: 0.5 },
  aiBadge: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#7C3AED", paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999,
  },
  aiBadgeTxt: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
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
  /* ---- Package grid (admin-configured) ---- */
  pkgGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 4,
    marginBottom: 4,
  },
  pkgCard: {
    flexBasis: "48%",
    flexGrow: 1,
    paddingVertical: 12,
    paddingHorizontal: 12,
    borderRadius: 14,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#F8FAFC",
    position: "relative",
    minHeight: 96,
  },
  pkgCardActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  popBadge: {
    position: "absolute",
    top: 6,
    right: 6,
    backgroundColor: "#FBBF24",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  popBadgeTxt: { fontSize: 8.5, fontWeight: "900", color: "#78350F", letterSpacing: 0.5 },
  pkgAmount: { fontSize: 22, fontWeight: "900", color: "#0F172A", letterSpacing: -0.4 },
  pkgCredits: { fontSize: 13, color: "#475569", fontWeight: "700", marginTop: 2 },
  pkgBonusPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    alignSelf: "flex-start",
    marginTop: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 999,
    backgroundColor: "#D1FAE5",
  },
  pkgBonusTxt: { fontSize: 10, fontWeight: "800", color: "#047857" },
  pkgLabel: { marginTop: 5, fontSize: 11, color: "#64748B", fontWeight: "700" },
});
