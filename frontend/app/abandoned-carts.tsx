/**
 * /app/abandoned-carts.tsx — Phase F3.3.
 *
 * Lists carts captured by webhooks with event_type=abandoned_order.
 * Owner can filter (All / Abandoned / Recovered / Dismissed) and per
 * cart:
 *   • Recover → creates a Pending Order (jumps the user into the
 *     existing ship-form flow).
 *   • Dismiss → mark as ignored (kept for analytics).
 *   • Delete → hard remove.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet, RefreshControl,
  TextInput, ActivityIndicator, Alert, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, Stack } from "expo-router";
import PhIcon from "../components/PhIcon";
import SearchBar from "../components/SearchBar";
import FilterChipRow from "../components/FilterChipRow";
import { Api, AbandonedCart } from "../lib/api";
import { colors } from "../lib/theme";

type StatusFilter = "all" | "abandoned" | "recovered" | "dismissed";

const STATUS_META: Record<
  string,
  { bg: string; fg: string; emoji: string; label: string }
> = {
  abandoned: { bg: "#FED7AA", fg: "#9A3412", emoji: "🛒", label: "Abandoned" },
  recovered: { bg: "#DCFCE7", fg: "#166534", emoji: "✅", label: "Recovered" },
  dismissed: { bg: "#E5E7EB", fg: "#475569", emoji: "🚫", label: "Dismissed" },
};

function formatINR(n: number): string {
  if (!n) return "₹0";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

function relativeTime(iso: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return "";
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export default function AbandonedCartsScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [carts, setCarts] = useState<AbandonedCart[]>([]);
  const [stats, setStats] = useState<{
    abandoned: number; recovered: number; dismissed: number;
    total_value: number; recovered_value: number;
  } | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("abandoned");
  const [search, setSearch] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const params: any = { limit: 100 };
      if (filter !== "all") params.status = filter;
      if (search.trim()) params.q = search.trim();
      const [list, s] = await Promise.all([
        Api.listAbandonedCarts(params),
        Api.abandonedCartStats(),
      ]);
      setCarts(list.carts);
      setStats(s);
    } catch (e: any) {
      Alert.alert(
        "Couldn't load abandoned carts",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [filter, search]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  const recover = async (c: AbandonedCart) => {
    Alert.alert(
      "Recover this cart?",
      `${c.customer_name || "Customer"} · ${formatINR(c.cart_value)}\n\n` +
      "This creates a Pending Order so you can ship it from the Orders tab.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Recover",
          onPress: async () => {
            setBusyId(c.id);
            try {
              const r = await Api.recoverAbandonedCart(c.id);
              Alert.alert(
                "Recovered ✓",
                `Created order ${r.master_order_id}. Open Orders → Pending to ship it.`,
              );
              await load();
            } catch (e: any) {
              Alert.alert(
                "Recovery failed",
                e?.response?.data?.detail || e?.message || "Try again.",
              );
            } finally {
              setBusyId(null);
            }
          },
        },
      ],
    );
  };

  const dismiss = async (c: AbandonedCart) => {
    setBusyId(c.id);
    try {
      await Api.dismissAbandonedCart(c.id);
      await load();
    } catch (e: any) {
      Alert.alert(
        "Dismiss failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (c: AbandonedCart) => {
    Alert.alert(
      "Delete cart record?",
      "This permanently removes the abandoned-cart entry. The customer record (if any) is unaffected.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            setBusyId(c.id);
            try {
              await Api.deleteAbandonedCart(c.id);
              await load();
            } catch (e: any) {
              Alert.alert(
                "Delete failed",
                e?.response?.data?.detail || e?.message || "Try again.",
              );
            } finally {
              setBusyId(null);
            }
          },
        },
      ],
    );
  };

  const callPhone = (phone: string) => {
    if (!phone) return;
    Linking.openURL(`tel:${phone.replace(/[^+\d]/g, "")}`);
  };

  const tabs: { key: StatusFilter; label: string; count?: number }[] = useMemo(
    () => [
      { key: "abandoned", label: "Active",   count: stats?.abandoned },
      { key: "recovered", label: "Recovered", count: stats?.recovered },
      { key: "dismissed", label: "Dismissed", count: stats?.dismissed },
      { key: "all",       label: "All" },
    ],
    [stats],
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <Stack.Screen
        options={{
          headerShown: true,
          title: "Abandoned Carts",
          headerBackTitle: "Back",
        }}
      />

      {loading ? (
        <View style={[styles.safe, styles.center]}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
        >
          {/* Stats banner */}
          {stats ? (
            <View style={styles.statsBanner}>
              <View style={styles.statBox}>
                <Text style={styles.statValue}>{stats.abandoned}</Text>
                <Text style={styles.statLabel}>Active</Text>
              </View>
              <View style={styles.statSep} />
              <View style={styles.statBox}>
                <Text style={styles.statValue}>{formatINR(stats.total_value)}</Text>
                <Text style={styles.statLabel}>At risk</Text>
              </View>
              <View style={styles.statSep} />
              <View style={styles.statBox}>
                <Text style={[styles.statValue, { color: "#166534" }]}>
                  {formatINR(stats.recovered_value)}
                </Text>
                <Text style={styles.statLabel}>Recovered</Text>
              </View>
            </View>
          ) : null}

          {/* Filter tabs — Phase F3.9 Android-large-font fix:
              generous marginBottom prevents the cards below from
              clipping the chips at >=1.3x font scale. */}
          {/* Status filter chips — Phase F3.9 via shared FilterChipRow. */}
          <FilterChipRow
            testIDPrefix="filter"
            selected={filter}
            onSelect={(k) => setFilter(k as any)}
            style={{ marginBottom: 16 }}
            items={tabs.map((t) => ({
              key: t.key,
              label: t.label,
              count: typeof t.count === "number" ? t.count : undefined,
            }))}
          />

          {/* Search */}
          <SearchBar
            testID="search-carts"
            value={search}
            onChangeText={setSearch}
            onClear={() => {
              // Phase-32 one-tap clear UX: reset status tab to
              // the default "abandoned" and any future filters.
              setFilter("abandoned");
            }}
            placeholder="Search by name, phone, email"
            onSubmitEditing={() => load()}
            containerStyle={{ marginHorizontal: 0 }}
          />

          {carts.length === 0 ? (
            <View style={styles.emptyCard}>
              <Text style={styles.emptyEmoji}>🛒</Text>
              <Text style={styles.emptyTitle}>No abandoned carts here</Text>
              <Text style={styles.emptySub}>
                Configure a webhook with event type "Abandoned Order" in
                Shippzo → Webhooks. We'll pick up cart events from Shopify,
                Dukaan, Meesho or any custom store.
              </Text>
              <TouchableOpacity
                testID="go-webhooks"
                style={styles.goBtn}
                onPress={() => router.push("/webhooks")}
                activeOpacity={0.85}
              >
                <PhIcon name="settings" size={14} color="#fff" />
                <Text style={styles.goBtnTxt}>Open Webhooks</Text>
              </TouchableOpacity>
            </View>
          ) : (
            carts.map((c) => {
              const meta = STATUS_META[c.status] || STATUS_META.abandoned;
              return (
                <View key={c.id} style={styles.card}>
                  <View style={styles.cardHeader}>
                    <View style={{ flex: 1, gap: 4 }}>
                      <Text style={styles.cardName} numberOfLines={1}>
                        {c.customer_name || "Unknown customer"}
                      </Text>
                      <View style={styles.badgeRow}>
                        <View style={[styles.badge, { backgroundColor: meta.bg }]}>
                          <Text style={[styles.badgeTxt, { color: meta.fg }]}>
                            {meta.emoji} {meta.label}
                          </Text>
                        </View>
                        {c.source_app ? (
                          <View style={[styles.badge, { backgroundColor: "#E0E7FF" }]}>
                            <Text style={[styles.badgeTxt, { color: "#3730A3" }]}>
                              🏷 {c.source_app.charAt(0).toUpperCase() + c.source_app.slice(1)}
                            </Text>
                          </View>
                        ) : c.webhook_name ? (
                          <View style={[styles.badge, { backgroundColor: "#E0E7FF" }]}>
                            <Text style={[styles.badgeTxt, { color: "#3730A3" }]}>
                              🏷 {c.webhook_name}
                            </Text>
                          </View>
                        ) : null}
                      </View>
                    </View>
                    <Text style={styles.value}>{formatINR(c.cart_value)}</Text>
                  </View>

                  <View style={styles.metaRow}>
                    {c.customer_phone ? (
                      <TouchableOpacity
                        onPress={() => callPhone(c.customer_phone)}
                        activeOpacity={0.7}
                        style={styles.metaItem}
                      >
                        <PhIcon name="phone" size={13} color="#475569" />
                        <Text style={styles.metaTxt}>{c.customer_phone}</Text>
                      </TouchableOpacity>
                    ) : null}
                    {c.customer_email ? (
                      <View style={styles.metaItem}>
                        <PhIcon name="mail" size={13} color="#475569" />
                        <Text style={styles.metaTxt} numberOfLines={1}>{c.customer_email}</Text>
                      </View>
                    ) : null}
                  </View>

                  {c.items_summary ? (
                    <Text style={styles.itemsTxt} numberOfLines={2}>
                      🛍 {c.items_summary}
                    </Text>
                  ) : null}

                  <Text style={styles.timestamp}>
                    Abandoned {relativeTime(c.abandoned_at)}
                  </Text>

                  {c.status === "abandoned" ? (
                    <View style={styles.actionsRow}>
                      <TouchableOpacity
                        testID={`recover-${c.id}`}
                        style={[styles.actionBtn, styles.actionBtnPrimary]}
                        onPress={() => recover(c)}
                        disabled={busyId === c.id}
                        activeOpacity={0.85}
                      >
                        {busyId === c.id ? (
                          <ActivityIndicator size="small" color="#fff" />
                        ) : (
                          <>
                            <PhIcon name="checkmark" size={14} color="#fff" />
                            <Text style={styles.actionBtnPrimaryTxt}>
                              Recover
                            </Text>
                          </>
                        )}
                      </TouchableOpacity>
                      <TouchableOpacity
                        testID={`dismiss-${c.id}`}
                        style={styles.actionBtn}
                        onPress={() => dismiss(c)}
                        disabled={busyId === c.id}
                        activeOpacity={0.8}
                      >
                        <Text style={styles.actionBtnTxt}>Dismiss</Text>
                      </TouchableOpacity>
                      <TouchableOpacity
                        style={[styles.actionBtn, styles.actionBtnDanger]}
                        onPress={() => remove(c)}
                        disabled={busyId === c.id}
                        activeOpacity={0.8}
                      >
                        <PhIcon name="delete" size={14} color="#DC2626" />
                      </TouchableOpacity>
                    </View>
                  ) : (
                    <View style={styles.actionsRow}>
                      {c.pending_order_id ? (
                        <TouchableOpacity
                          style={styles.actionBtn}
                          onPress={() => router.push("/(tabs)/orders")}
                          activeOpacity={0.8}
                        >
                          <PhIcon name="open" size={14} color="#475569" />
                          <Text style={styles.actionBtnTxt}>View order</Text>
                        </TouchableOpacity>
                      ) : null}
                      <TouchableOpacity
                        style={[styles.actionBtn, styles.actionBtnDanger]}
                        onPress={() => remove(c)}
                        disabled={busyId === c.id}
                        activeOpacity={0.8}
                      >
                        <PhIcon name="delete" size={14} color="#DC2626" />
                        <Text style={[styles.actionBtnTxt, { color: "#DC2626" }]}>
                          Delete
                        </Text>
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              );
            })
          )}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: colors.background },
  center: { alignItems: "center", justifyContent: "center" },
  scroll: { padding: 16, paddingBottom: 30 },

  statsBanner: {
    flexDirection: "row",
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 14,
    marginBottom: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  statBox: { flex: 1, alignItems: "center" },
  statSep: { width: 1, backgroundColor: "#E5E7EB" },
  statValue: { fontSize: 17, fontWeight: "800", color: colors.text },
  statLabel: { fontSize: 11, color: colors.textMuted, marginTop: 2 },

  filterChip: {
    paddingHorizontal: 14, paddingVertical: 10,
    minHeight: 40,
    justifyContent: "center",
    borderRadius: 20,
    flexShrink: 0,
    borderWidth: 1.5, borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  filterChipSel: {
    borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  filterChipTxt: {
    fontSize: 12, fontWeight: "700", color: "#475569",
    lineHeight: 18,
    includeFontPadding: false,
  },
  filterChipTxtSel: { color: colors.primary },

  search: {
    backgroundColor: "#fff",
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12, height: 44,
    fontSize: 14, color: colors.text,
    marginBottom: 12,
  },

  emptyCard: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
    marginTop: 8,
  },
  emptyEmoji: { fontSize: 42, marginBottom: 6 },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: colors.text, marginBottom: 6 },
  emptySub:   { fontSize: 13, color: colors.textMuted, textAlign: "center", lineHeight: 19 },
  goBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 16, paddingVertical: 10,
    borderRadius: 10,
    marginTop: 14,
  },
  goBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  card: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
    padding: 14,
    marginBottom: 10,
    gap: 8,
  },
  cardHeader: { flexDirection: "row", alignItems: "flex-start", gap: 12 },
  cardName: { fontSize: 15, fontWeight: "800", color: colors.text },
  value: { fontSize: 16, fontWeight: "800", color: colors.text },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  badgeTxt: { fontSize: 11, fontWeight: "800" },

  metaRow: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  metaItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  metaTxt: { fontSize: 12, color: "#475569", maxWidth: 220 },

  itemsTxt: { fontSize: 12, color: "#475569", lineHeight: 17 },
  timestamp: { fontSize: 11, color: colors.textMuted },

  actionsRow: { flexDirection: "row", gap: 8, marginTop: 4 },
  actionBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 4,
    backgroundColor: "#F1F5F9",
    paddingVertical: 10,
    borderRadius: 8,
  },
  actionBtnTxt: { fontSize: 12, fontWeight: "700", color: "#475569" },
  actionBtnPrimary: { backgroundColor: colors.primary },
  actionBtnPrimaryTxt: { fontSize: 13, fontWeight: "800", color: "#fff" },
  actionBtnDanger: { backgroundColor: "#FEE2E2", flex: 0, paddingHorizontal: 14 },
});
