/**
 * Audience Profile — Phase F12.
 *
 * Shown when the user taps a customer card on the Audience tab.
 * Displays:
 *   • Header: name, phone (tap-to-call), email (tap-to-mail),
 *             default address.
 *   • Summary strip: Total Orders + Total Sales (successful, i.e.
 *                    Delivered orders only).
 *   • Order History: chronological list of every shipment for this
 *                    customer with tracking, amount, status, and a
 *                    tap-through to `/shipment-details/[id]`.
 *
 * Backed by GET /api/me/audience/{customer_key}.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Linking,
  RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter, Stack } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { Api, AudienceProfile } from "../../lib/api";
import { colors } from "../../lib/theme";

function formatINR(n: number): string {
  const v = Number.isFinite(n) ? n : 0;
  const rounded = Math.round(v * 100) / 100;
  return "₹" + rounded.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function statusChipStyle(status: string) {
  const s = (status || "").toLowerCase();
  if (s === "delivered")   return { bg: "#DCFCE7", fg: "#15803D" };
  if (s === "in transit" || s === "shipped")
                            return { bg: "#DBEAFE", fg: "#1D4ED8" };
  if (s === "out for delivery")
                            return { bg: "#FEF3C7", fg: "#B45309" };
  if (s === "rto" || s === "returned")
                            return { bg: "#FEE2E2", fg: "#B91C1C" };
  if (s === "cancelled")   return { bg: "#FEE2E2", fg: "#B91C1C" };
  return { bg: "#F1F5F9", fg: "#475569" };
}

function formatDate(iso: string): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString("en-IN", {
      day:   "2-digit",
      month: "short",
      year:  "numeric",
    });
  } catch {
    return "";
  }
}

export default function AudienceProfileScreen() {
  const router = useRouter();
  const { key } = useLocalSearchParams<{ key: string }>();
  const custKey = String(key || "");

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [profile, setProfile] = useState<AudienceProfile | null>(null);

  const load = useCallback(async () => {
    if (!custKey) {
      setLoading(false);
      return;
    }
    try {
      const p = await Api.getAudienceProfile(custKey);
      setProfile(p);
    } catch (e: any) {
      Alert.alert(
        "Couldn't load customer",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [custKey]);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const callPhone = () => {
    if (!profile?.customer_phone) return;
    Linking.openURL(`tel:${profile.customer_phone.replace(/[^+\d]/g, "")}`);
  };

  const sendEmail = () => {
    if (!profile?.customer_email) return;
    Linking.openURL(`mailto:${profile.customer_email}`);
  };

  const openOrder = (shipmentId: string) => {
    if (!shipmentId) return;
    router.push({
      pathname: "/shipment-details/[id]",
      params: { id: shipmentId },
    });
  };

  if (loading) {
    return (
      <SafeAreaView style={[styles.safe, styles.centered]} edges={["top"]}>
        <Stack.Screen options={{ headerShown: true, title: "Customer" }} />
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  if (!profile) {
    return (
      <SafeAreaView style={[styles.safe, styles.centered]} edges={["top"]}>
        <Stack.Screen options={{ headerShown: true, title: "Customer" }} />
        <Text style={styles.emptyEmoji}>😕</Text>
        <Text style={styles.emptyTitle}>Customer not found</Text>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backBtnTxt}>Go back</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen
        options={{
          headerShown: true,
          title: profile.customer_name || "Customer",
          headerBackTitle: "Back",
        }}
      />

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
      >
        {/* Identity card */}
        <View style={styles.identityCard}>
          <View style={styles.avatarCircle}>
            <Text style={styles.avatarInitial}>
              {(profile.customer_name || "?").trim().charAt(0).toUpperCase()}
            </Text>
          </View>
          <Text style={styles.nameTxt}>
            {profile.customer_name || "Unknown customer"}
          </Text>
          {profile.is_imported ? (
            <View style={styles.importedBadge}>
              <Text style={styles.importedBadgeTxt}>IMPORTED</Text>
            </View>
          ) : null}

          <View style={styles.contactRow}>
            {profile.customer_phone ? (
              <TouchableOpacity
                onPress={callPhone}
                activeOpacity={0.7}
                style={styles.contactBtn}
                testID="profile-call"
              >
                <PhIcon name="phone" size={16} color={colors.primary} />
                <Text style={styles.contactBtnTxt}>
                  {profile.customer_phone}
                </Text>
              </TouchableOpacity>
            ) : null}
            {profile.customer_email ? (
              <TouchableOpacity
                onPress={sendEmail}
                activeOpacity={0.7}
                style={styles.contactBtn}
                testID="profile-email"
              >
                <PhIcon name="mail" size={16} color={colors.primary} />
                <Text style={styles.contactBtnTxt} numberOfLines={1}>
                  {profile.customer_email}
                </Text>
              </TouchableOpacity>
            ) : null}
          </View>

          {profile.default_address ? (
            <View style={styles.addrBlock}>
              <PhIcon name="location" size={14} color="#64748B" />
              <Text style={styles.addrTxt}>{profile.default_address}</Text>
            </View>
          ) : null}
        </View>

        {/* Summary strip */}
        <View style={styles.statsStrip}>
          <View style={styles.statBox}>
            <Text style={styles.statNumber}>{profile.orders_count}</Text>
            <Text style={styles.statLabel}>Total Orders</Text>
          </View>
          <View style={styles.statSep} />
          <View style={styles.statBox}>
            <Text style={styles.statNumber}>
              {formatINR(profile.total_sales)}
            </Text>
            <Text style={styles.statLabel}>Total Sales</Text>
          </View>
        </View>

        {/* Order history */}
        <View style={styles.sectionHeader}>
          <Text style={styles.sectionTitle}>Order History</Text>
          <Text style={styles.sectionCount}>
            {profile.orders.length} order{profile.orders.length === 1 ? "" : "s"}
          </Text>
        </View>

        {profile.orders.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyTitle}>No orders yet</Text>
          </View>
        ) : (
          profile.orders.map((o) => {
            const chip = statusChipStyle(o.status);
            return (
              <TouchableOpacity
                key={o.id}
                style={styles.orderRow}
                activeOpacity={0.75}
                onPress={() => openOrder(o.id)}
                testID={`profile-order-${o.id}`}
              >
                <View style={{ flex: 1 }}>
                  <View style={styles.orderTopRow}>
                    <Text style={styles.orderTracking} numberOfLines={1}>
                      {o.tracking_id || o.order_id || "—"}
                    </Text>
                    <View
                      style={[
                        styles.statusChip,
                        { backgroundColor: chip.bg },
                      ]}
                    >
                      <Text
                        style={[styles.statusChipTxt, { color: chip.fg }]}
                        numberOfLines={1}
                      >
                        {o.status || "Pending"}
                      </Text>
                    </View>
                  </View>
                  <View style={styles.orderMetaRow}>
                    <Text style={styles.orderDate}>
                      {formatDate(o.created_at) || "—"}
                    </Text>
                    {o.courier_name ? (
                      <Text style={styles.orderCourier} numberOfLines={1}>
                        · {o.courier_name}
                      </Text>
                    ) : null}
                  </View>
                </View>
                <View style={styles.orderRight}>
                  <Text style={styles.orderAmount}>
                    {formatINR(o.amount)}
                  </Text>
                  {o.payment_mode ? (
                    <Text style={styles.orderMode}>
                      {o.payment_mode}
                    </Text>
                  ) : null}
                </View>
                <PhIcon
                  name="chevron-forward"
                  size={16}
                  color="#94A3B8"
                  style={{ marginLeft: 6 }}
                />
              </TouchableOpacity>
            );
          })
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  centered: { alignItems: "center", justifyContent: "center" },

  scroll: { padding: 12, paddingBottom: 40 },

  // ── Identity ─────────────────────────────────────────────────────
  identityCard: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    alignItems: "center",
    gap: 8,
  },
  avatarCircle: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.primary + "22",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 4,
  },
  avatarInitial: {
    fontSize: 26,
    fontWeight: "800",
    color: colors.primary,
  },
  nameTxt: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
    textAlign: "center",
  },
  importedBadge: {
    backgroundColor: "#E0E7FF",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
  },
  importedBadgeTxt: {
    fontSize: 10,
    fontWeight: "800",
    color: "#3730A3",
    letterSpacing: 0.4,
  },
  contactRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    justifyContent: "center",
    marginTop: 8,
  },
  contactBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary + "10",
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
    maxWidth: 260,
  },
  contactBtnTxt: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.primary,
  },
  addrBlock: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 6,
    marginTop: 8,
    paddingHorizontal: 8,
  },
  addrTxt: {
    flex: 1,
    fontSize: 13,
    color: "#475569",
    lineHeight: 19,
  },

  // ── Stats strip ──────────────────────────────────────────────────
  statsStrip: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    marginTop: 12,
    padding: 14,
  },
  statBox: { flex: 1, alignItems: "center" },
  statSep: { width: 1, backgroundColor: "#E5E7EB" },
  statNumber: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
  },
  statLabel: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 3,
    fontWeight: "600",
  },

  // ── Section header ──────────────────────────────────────────────
  sectionHeader: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginTop: 20,
    marginBottom: 8,
    paddingHorizontal: 4,
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.text,
  },
  sectionCount: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "600",
  },

  // ── Order row ───────────────────────────────────────────────────
  orderRow: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    padding: 12,
    marginBottom: 8,
  },
  orderTopRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  orderTracking: {
    flex: 1,
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  statusChip: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  statusChipTxt: {
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.4,
  },
  orderMetaRow: { flexDirection: "row", alignItems: "center", gap: 4 },
  orderDate: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "500",
  },
  orderCourier: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "500",
    flexShrink: 1,
  },
  orderRight: {
    alignItems: "flex-end",
    marginLeft: 8,
  },
  orderAmount: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
  },
  orderMode: {
    fontSize: 11,
    color: colors.textMuted,
    fontWeight: "600",
    marginTop: 2,
  },

  // ── Empty ────────────────────────────────────────────────────────
  emptyCard: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 24,
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  emptyEmoji: { fontSize: 40, marginBottom: 8 },
  emptyTitle: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  backBtn: {
    marginTop: 14,
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: colors.primary,
    borderRadius: 8,
  },
  backBtnTxt: { color: "#fff", fontWeight: "700" },
});
