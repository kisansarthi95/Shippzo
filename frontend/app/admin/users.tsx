/**
 * Admin → Users screen.
 *
 * Shows every registered user with aggregated usage stats:
 *   - Email, name, shop, phone, plan, admin flag
 *   - Plan validity: started_at / expires_at / days_left / expired badge
 *   - Wallet balance
 *   - Labels this month
 *   - Created / Last login timestamps
 *
 * Plus a summary strip at the top with:
 *   - Total users
 *   - Plan breakdown (free_trial / silver / gold / platinum)
 *   - Admin count
 *
 * Search box + plan filter chips.
 *
 * Tap a row → detail modal with recent shipments & wallet history.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity, FlatList,
  ActivityIndicator, RefreshControl, Modal, ScrollView, Platform, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { api, PlanKey } from "../../lib/api";
import { colors } from "../../lib/theme";

type Row = {
  id: string;
  display_id: string;
  email: string;
  name: string;
  shop_name: string;
  phone: string;
  plan: PlanKey;
  is_admin: boolean;
  plan_mocked: boolean;
  plan_billing_cycle: "monthly" | "yearly" | null;
  plan_started_at: string | null;
  plan_expires_at: string | null;
  plan_expired: boolean;
  plan_days_left: number | null;
  auto_renew: boolean;
  cancelled_at: string | null;
  created_at: string;
  last_login_at: string;
  wallet_balance: number;
  labels_this_month: number;
  auth_provider: string;
  trial_denied_reason?: string;
  device_fingerprint?: string;
};

type ListResponse = {
  total: number;
  limit: number;
  skip: number;
  users: Row[];
  summary: {
    total_users: number;
    admin_count: number;
    plan_counts: Record<string, number>;
    displayed: number;
  };
};

type DetailResponse = {
  user: Row & Record<string, any>;
  wallet: { remaining_credits: number; total_credits?: number };
  shipment_count: number;
  paid_orders_count: number;
  recent_shipments: any[];
  recent_wallet_tx: any[];
};

const PLAN_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  free_trial: { bg: "#F1F5F9", fg: "#475569", label: "Free Trial" },
  silver:     { bg: "#E5E7EB", fg: "#374151", label: "Silver" },
  gold:       { bg: "#FEF3C7", fg: "#92400E", label: "Gold" },
  platinum:   { bg: "#E0E7FF", fg: "#3730A3", label: "Platinum" },
};

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch { return "—"; }
}

function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return "—"; }
}

export default function AdminUsersScreen() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<ListResponse["summary"] | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [q, setQ] = useState("");
  const [planFilter, setPlanFilter] = useState<"" | PlanKey>("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<DetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (q.trim()) params.q = q.trim();
      if (planFilter) params.plan = planFilter;
      const r = await api.get<ListResponse>("/admin/users", { params });
      setRows(r.data.users);
      setSummary(r.data.summary);
    } catch (e: any) {
      // keep previous rows on failure
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [q, planFilter]);

  useEffect(() => {
    load().catch(() => {});
  }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load().catch(() => {});
  }, [load]);

  const openDetail = async (id: string) => {
    setDetailOpen(true);
    setDetail(null);
    setDetailLoading(true);
    try {
      const r = await api.get<DetailResponse>(`/admin/users/${id}`);
      setDetail(r.data);
    } catch {
      /* silent */
    } finally {
      setDetailLoading(false);
    }
  };

  const resetUserPassword = useCallback((uid: string, email: string) => {
    const doReset = async (newPwd: string) => {
      try {
        await api.post(`/admin/users/${uid}/reset-password`, { new_password: newPwd });
        Alert.alert(
          "Password reset ✅",
          `A new password has been set for ${email}. Share it with the user securely over the phone:\n\n${newPwd}`,
        );
      } catch (e: any) {
        Alert.alert("Reset failed", e?.response?.data?.detail || e?.message || "Try again");
      }
    };
    // Cross-platform prompt: Alert.prompt on iOS, browser prompt on web,
    // simple random-generated on Android (RN doesn't ship a text prompt).
    if (Platform.OS === "ios") {
      (Alert as any).prompt(
        "Set new password",
        `Enter a new password for ${email}. Minimum 6 characters.`,
        [
          { text: "Cancel", style: "cancel" },
          {
            text: "Reset",
            onPress: (txt?: string) => {
              const pwd = (txt || "").trim();
              if (pwd.length < 6) {
                Alert.alert("Too short", "Password must be at least 6 characters.");
                return;
              }
              doReset(pwd);
            },
          },
        ],
        "plain-text",
      );
    } else if (Platform.OS === "web" && typeof window !== "undefined") {
      const pwd = (window.prompt(`Set new password for ${email} (min 6 chars):`) || "").trim();
      if (!pwd) return;
      if (pwd.length < 6) {
        Alert.alert("Too short", "Password must be at least 6 characters.");
        return;
      }
      doReset(pwd);
    } else {
      // Android fallback: generate a strong random password, show it to
      // the admin in an alert, and apply it. Admin can share over phone.
      const rand =
        Math.random().toString(36).slice(2, 6) +
        Math.floor(Math.random() * 9000 + 1000);
      Alert.alert(
        "Generated password",
        `We'll set this random password for ${email}:\n\n${rand}\n\nShare it with the user over the phone. They can change it later from Settings.`,
        [
          { text: "Cancel", style: "cancel" },
          { text: "Apply", onPress: () => doReset(rand) },
        ],
      );
    }
  }, []);

  const planChips: Array<{ key: "" | PlanKey; label: string }> = useMemo(() => {
    const counts = summary?.plan_counts || {};
    const all = summary?.total_users || 0;
    return [
      { key: "",            label: `All (${all})` },
      { key: "free_trial",  label: `Trial (${counts.free_trial || 0})` },
      { key: "silver",      label: `Silver (${counts.silver || 0})` },
      { key: "gold",        label: `Gold (${counts.gold || 0})` },
      { key: "platinum",    label: `Platinum (${counts.platinum || 0})` },
    ];
  }, [summary]);

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Users",
          headerStyle: { backgroundColor: colors.background },
          headerRight: () => (
            <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          ),
          headerBackVisible: false,
        }}
      />

      {/* Summary strip */}
      {summary ? (
        <View style={styles.summaryBox}>
          <View style={styles.summaryItem}>
            <Text style={styles.summaryNum}>{summary.total_users}</Text>
            <Text style={styles.summaryLbl}>Total Users</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={styles.summaryNum}>
              {(summary.plan_counts.silver || 0) +
               (summary.plan_counts.gold || 0) +
               (summary.plan_counts.platinum || 0)}
            </Text>
            <Text style={styles.summaryLbl}>Paid</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={styles.summaryNum}>{summary.plan_counts.free_trial || 0}</Text>
            <Text style={styles.summaryLbl}>Trial</Text>
          </View>
          <View style={styles.summaryDivider} />
          <View style={styles.summaryItem}>
            <Text style={styles.summaryNum}>{summary.admin_count}</Text>
            <Text style={styles.summaryLbl}>Admins</Text>
          </View>
        </View>
      ) : null}

      {/* Search */}
      <View style={styles.searchBox}>
        <PhIcon name="search" size={16} color="#94A3B8" />
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Search by email, name, shop…"
          placeholderTextColor="#94A3B8"
          style={styles.searchInp}
          returnKeyType="search"
          autoCorrect={false}
          autoCapitalize="none"
        />
        {q ? (
          <TouchableOpacity onPress={() => setQ("")} hitSlop={10}>
            <PhIcon name="close-circle" size={18} color="#94A3B8" />
          </TouchableOpacity>
        ) : null}
      </View>

      {/* Plan filter chips */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.chipsRow}
        contentContainerStyle={{ paddingHorizontal: 14, gap: 8 }}
      >
        {planChips.map((c) => (
          <TouchableOpacity
            key={c.key || "all"}
            onPress={() => setPlanFilter(c.key)}
            style={[
              styles.chip,
              planFilter === c.key && styles.chipActive,
            ]}
          >
            <Text style={[
              styles.chipTxt,
              planFilter === c.key && styles.chipTxtActive,
            ]}>{c.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator color={colors.primary} size="large" />
        </View>
      ) : (
        <FlatList
          data={rows}
          keyExtractor={(r) => r.id}
          contentContainerStyle={{ padding: 14, paddingBottom: 60 }}
          ItemSeparatorComponent={() => <View style={{ height: 10 }} />}
          ListEmptyComponent={
            <View style={{ alignItems: "center", padding: 40 }}>
              <PhIcon name="people-outline" size={44} color="#CBD5E1" />
              <Text style={{ color: "#94A3B8", marginTop: 10 }}>
                {q || planFilter ? "No users match the current filter." : "No users yet."}
              </Text>
            </View>
          }
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          }
          renderItem={({ item }) => <UserRow row={item} onPress={() => openDetail(item.id)} />}
        />
      )}

      {/* Detail modal */}
      <Modal
        visible={detailOpen}
        animationType={Platform.OS === "ios" ? "slide" : "fade"}
        onRequestClose={() => setDetailOpen(false)}
      >
        <SafeAreaView edges={["top"]} style={styles.safe}>
          <View style={styles.modalHdr}>
            <TouchableOpacity onPress={() => setDetailOpen(false)} hitSlop={10}>
              <PhIcon name="arrow-back" size={22} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.modalTitle}>User Detail</Text>
            <View style={{ width: 22 }} />
          </View>
          {detailLoading || !detail ? (
            <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
              <ActivityIndicator color={colors.primary} />
            </View>
          ) : (
            <DetailView d={detail} onResetPassword={resetUserPassword} />
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

function UserRow({ row, onPress }: { row: Row; onPress: () => void }) {
  const pal = PLAN_COLORS[row.plan] || PLAN_COLORS.free_trial;
  const expStatus =
    row.plan_expired
      ? { bg: "#FEE2E2", fg: "#991B1B", label: "Expired" }
      : row.plan_days_left != null && row.plan_days_left <= 7
        ? { bg: "#FEF3C7", fg: "#92400E", label: `${row.plan_days_left}d left` }
        : row.plan_expires_at
          ? { bg: "#D1FAE5", fg: "#065F46", label: `${row.plan_days_left || "—"}d left` }
          : null;

  return (
    <TouchableOpacity onPress={onPress} style={styles.card} activeOpacity={0.85}>
      <View style={styles.cardTopRow}>
        <View style={{ flex: 1, minWidth: 0 }}>
          <View style={styles.nameRow}>
            <Text style={styles.name} numberOfLines={1}>
              {row.name || row.email.split("@")[0]}
            </Text>
            {row.is_admin ? (
              <View style={styles.adminBadge}>
                <PhIcon name="shield-checkmark" size={10} color="#fff" />
                <Text style={styles.adminBadgeTxt}>ADMIN</Text>
              </View>
            ) : null}
          </View>
          {row.display_id ? (
            <Text style={styles.displayId}>{row.display_id}</Text>
          ) : null}
          <Text style={styles.email} numberOfLines={1}>{row.email}</Text>
          {row.phone ? (
            <Text style={styles.shop} numberOfLines={1}>📞 {row.phone}</Text>
          ) : null}
          {row.shop_name ? (
            <Text style={styles.shop} numberOfLines={1}>🏪 {row.shop_name}</Text>
          ) : null}
        </View>
        <View style={{ alignItems: "flex-end", gap: 4 }}>
          <View style={[styles.planPill, { backgroundColor: pal.bg }]}>
            <Text style={[styles.planPillTxt, { color: pal.fg }]}>{pal.label}</Text>
          </View>
          {expStatus ? (
            <View style={[styles.expPill, { backgroundColor: expStatus.bg }]}>
              <Text style={[styles.expPillTxt, { color: expStatus.fg }]}>{expStatus.label}</Text>
            </View>
          ) : null}
          {row.trial_denied_reason === "duplicate_device" ? (
            <View style={[styles.expPill, { backgroundColor: "#FEE2E2" }]}>
              <PhIcon name="hardware-chip-outline" size={9} color="#7F1D1D" />
              <Text style={[styles.expPillTxt, { color: "#7F1D1D", marginLeft: 2 }]}>
                Trial denied · same device
              </Text>
            </View>
          ) : null}
        </View>
      </View>

      <View style={styles.statsRow}>
        <Stat icon="wallet-outline" value={`${row.wallet_balance.toFixed(1)} cr`} label="Wallet" />
        <Stat icon="document-text-outline" value={String(row.labels_this_month)} label="Labels/mo" />
        <Stat icon="calendar-outline" value={fmtDate(row.created_at).replace(",", "")} label="Joined" />
      </View>
    </TouchableOpacity>
  );
}

function Stat({ icon, value, label }: { icon: string; value: string; label: string }) {
  return (
    <View style={styles.stat}>
      <PhIcon name={icon as any} size={14} color="#64748B" />
      <View>
        <Text style={styles.statValue}>{value}</Text>
        <Text style={styles.statLabel}>{label}</Text>
      </View>
    </View>
  );
}

function DetailView({ d, onResetPassword }: {
  d: DetailResponse;
  onResetPassword: (id: string, email: string) => void;
}) {
  const u = d.user;
  const pal = PLAN_COLORS[u.plan] || PLAN_COLORS.free_trial;
  return (
    <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }}>
      {/* Hero */}
      <View style={styles.heroCard}>
        {u.display_id ? (
          <View style={styles.heroIdPill}>
            <PhIcon name="finger-print" size={11} color="#0F172A" />
            <Text style={styles.heroIdTxt}>{u.display_id}</Text>
          </View>
        ) : null}
        <Text style={styles.heroName}>{u.name || u.email.split("@")[0]}</Text>
        <Text style={styles.heroEmail}>{u.email}</Text>
        {u.phone ? <Text style={styles.heroShop}>📞 {u.phone}</Text> : null}
        {u.shop_name ? <Text style={styles.heroShop}>🏪 {u.shop_name}</Text> : null}
        <View style={styles.heroRow}>
          <View style={[styles.planPill, { backgroundColor: pal.bg }]}>
            <Text style={[styles.planPillTxt, { color: pal.fg }]}>{pal.label}</Text>
          </View>
          {u.is_admin ? (
            <View style={styles.adminBadge}>
              <PhIcon name="shield-checkmark" size={10} color="#fff" />
              <Text style={styles.adminBadgeTxt}>ADMIN</Text>
            </View>
          ) : null}
          {u.plan_mocked ? (
            <View style={[styles.expPill, { backgroundColor: "#FEE2E2" }]}>
              <Text style={[styles.expPillTxt, { color: "#991B1B" }]}>MOCKED</Text>
            </View>
          ) : null}
        </View>

        {/* Admin quick actions */}
        <View style={styles.adminActions}>
          <TouchableOpacity
            onPress={() => onResetPassword(u.id, u.email)}
            style={styles.actionBtn}
            activeOpacity={0.85}
          >
            <PhIcon name="key-outline" size={14} color="#fff" />
            <Text style={styles.actionTxt}>Reset Password</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Key details grid */}
      <View style={styles.grid}>
        <GridItem icon="cash-outline"    label="Wallet"         value={`${(d.wallet?.remaining_credits ?? 0).toFixed(2)} cr`} />
        <GridItem icon="cube-outline"    label="Total Shipments" value={String(d.shipment_count)} />
        <GridItem icon="card-outline"    label="Paid Orders"    value={String(d.paid_orders_count)} />
        <GridItem icon="time-outline"    label="Billing Cycle"  value={u.plan_billing_cycle ? (u.plan_billing_cycle === "yearly" ? "Yearly" : "Monthly") : "—"} />
        <GridItem icon="calendar-outline" label="Plan Started"   value={fmtDate(u.plan_started_at)} />
        <GridItem icon="alert-circle-outline" label="Plan Expires"   value={fmtDate(u.plan_expires_at)} />
        <GridItem icon="log-in-outline"  label="Joined"         value={fmtDateTime(u.created_at)} />
        <GridItem icon="enter-outline"   label="Last Login"     value={fmtDateTime(u.last_login_at)} />
      </View>

      <Text style={styles.sectionHead}>Recent Shipments</Text>
      {d.recent_shipments.length === 0 ? (
        <Text style={styles.empty}>No shipments yet.</Text>
      ) : (
        d.recent_shipments.map((s: any, i: number) => (
          <View key={`ship-${i}-${s.tracking_id || ""}`} style={styles.listItem}>
            <View style={{ flex: 1 }}>
              <Text style={styles.liTitle} numberOfLines={1}>{s.customer_name || "—"}</Text>
              <Text style={styles.liSub}>
                {s.tracking_id} · {s.city || "—"} · {fmtDate(s.created_at)}
              </Text>
            </View>
            <Text style={styles.liAmt}>
              {s.payment_type === "COD" ? "COD " : "PAID "}
              ₹{Number(s.amount || 0).toFixed(0)}
            </Text>
          </View>
        ))
      )}

      <Text style={styles.sectionHead}>Recent Wallet Transactions</Text>
      {d.recent_wallet_tx.length === 0 ? (
        <Text style={styles.empty}>No wallet activity.</Text>
      ) : (
        d.recent_wallet_tx.map((t: any, i: number) => (
          <View key={t.id || i} style={styles.listItem}>
            <View style={{ flex: 1 }}>
              <Text style={styles.liTitle} numberOfLines={2}>{t.description || t.type}</Text>
              <Text style={styles.liSub}>{fmtDateTime(t.created_at)}</Text>
            </View>
            <Text style={[
              styles.liAmt,
              { color: (t.delta || 0) >= 0 ? "#047857" : "#DC2626" },
            ]}>
              {(t.delta || 0) >= 0 ? "+" : ""}{Number(t.delta || 0).toFixed(2)} cr
            </Text>
          </View>
        ))
      )}
    </ScrollView>
  );
}

function GridItem({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <View style={styles.gridItem}>
      <PhIcon name={icon as any} size={16} color="#64748B" />
      <Text style={styles.gridLabel}>{label}</Text>
      <Text style={styles.gridValue} numberOfLines={2}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  summaryBox: {
    flexDirection: "row", alignItems: "center",
    marginHorizontal: 14, marginTop: 10, marginBottom: 2,
    backgroundColor: "#fff", borderRadius: 12, paddingVertical: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  summaryItem: { flex: 1, alignItems: "center" },
  summaryNum:  { fontSize: 20, fontWeight: "900", color: "#0F172A" },
  summaryLbl:  { fontSize: 10.5, color: "#64748B", fontWeight: "700", letterSpacing: 0.4, marginTop: 1 },
  summaryDivider: { width: 1, height: 26, backgroundColor: "#E5E7EB" },
  searchBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    marginHorizontal: 14, marginTop: 12, paddingHorizontal: 12, paddingVertical: 9,
    backgroundColor: "#fff", borderRadius: 10, borderWidth: 1, borderColor: "#E5E7EB",
  },
  searchInp: { flex: 1, fontSize: 14, color: "#0F172A", padding: 0, margin: 0 },
  chipsRow: { marginTop: 10, marginBottom: 4, flexGrow: 0 },
  chip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 20,
    flexShrink: 0,
    backgroundColor: "#F1F5F9",
  },
  chipActive: { backgroundColor: "#0F172A" },
  chipTxt: { fontSize: 12, fontWeight: "800", color: "#475569" },
  chipTxtActive: { color: "#fff" },
  card: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  cardTopRow: { flexDirection: "row", gap: 10 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  name:  { fontSize: 15, fontWeight: "900", color: "#0F172A", maxWidth: 200 },
  email: { fontSize: 12, color: "#64748B", marginTop: 2 },
  displayId: {
    fontSize: 10.5, fontWeight: "900", color: "#0F172A",
    letterSpacing: 1, marginTop: 2,
    backgroundColor: "#E0E7FF", alignSelf: "flex-start",
    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  heroIdPill: {
    alignSelf: "flex-start",
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#E0E7FF",
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 6, marginBottom: 8,
  },
  heroIdTxt: {
    fontSize: 11, fontWeight: "900", color: "#0F172A", letterSpacing: 1,
  },
  adminActions: {
    flexDirection: "row", gap: 8, marginTop: 14,
    paddingTop: 14, borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  actionBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: "#0F172A",
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 8,
  },
  actionTxt: {
    color: "#fff", fontSize: 12, fontWeight: "800", letterSpacing: 0.3,
  },
  shop:  { fontSize: 11.5, color: "#475569", marginTop: 2, fontWeight: "600" },
  planPill: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  planPillTxt: { fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
  expPill: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  expPillTxt: { fontSize: 10, fontWeight: "800" },
  adminBadge: {
    flexDirection: "row", alignItems: "center", gap: 3,
    backgroundColor: "#0F172A", paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  adminBadgeTxt: { color: "#fff", fontSize: 9, fontWeight: "900", letterSpacing: 0.5 },
  statsRow: {
    flexDirection: "row", gap: 12, marginTop: 12,
    paddingTop: 10, borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  stat: { flex: 1, flexDirection: "row", gap: 6, alignItems: "center" },
  statValue: { fontSize: 12, fontWeight: "800", color: "#0F172A" },
  statLabel: { fontSize: 10, color: "#94A3B8", fontWeight: "600" },
  /* ---- Detail ---- */
  modalHdr: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    padding: 14, borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
  },
  modalTitle: { fontSize: 16, fontWeight: "900", color: "#0F172A" },
  heroCard: {
    backgroundColor: "#fff", borderRadius: 14, padding: 16, marginBottom: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  heroName:  { fontSize: 20, fontWeight: "900", color: "#0F172A" },
  heroEmail: { fontSize: 13, color: "#64748B", marginTop: 2 },
  heroShop:  { fontSize: 12, color: "#475569", marginTop: 3, fontWeight: "600" },
  heroRow:   { flexDirection: "row", gap: 6, marginTop: 10, flexWrap: "wrap" },
  grid: {
    flexDirection: "row", flexWrap: "wrap", gap: 10, marginBottom: 8,
  },
  gridItem: {
    width: "48%", backgroundColor: "#fff", borderRadius: 12, padding: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  gridLabel: { fontSize: 10.5, color: "#64748B", fontWeight: "700", letterSpacing: 0.4, marginTop: 4 },
  gridValue: { fontSize: 13, fontWeight: "800", color: "#0F172A", marginTop: 2 },
  sectionHead: {
    fontSize: 11, fontWeight: "900", color: "#475569",
    letterSpacing: 0.6, textTransform: "uppercase",
    marginTop: 18, marginBottom: 8,
  },
  listItem: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: "#fff", borderRadius: 10, padding: 12, marginBottom: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  liTitle: { fontSize: 13, fontWeight: "800", color: "#0F172A" },
  liSub:   { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  liAmt:   { fontSize: 13, fontWeight: "900", color: "#0F172A" },
  empty:   { color: "#94A3B8", fontSize: 12, textAlign: "center", padding: 12 },
});
