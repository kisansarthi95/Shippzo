/**
 * /app/customers.tsx — Phase F3.3.
 *
 * Lists customers captured by webhooks with event_types
 * customer_created / customer_updated. Search + source-app filter.
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
import { Api, Customer } from "../lib/api";
import { colors } from "../lib/theme";

function formatINR(n: number): string {
  if (!n) return "₹0";
  return "₹" + Math.round(n).toLocaleString("en-IN");
}

function sourceLabel(s: string): string {
  if (!s) return "Direct";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export default function CustomersScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [stats, setStats] = useState<{
    total: number;
    total_spent: number;
    by_source: { source_app: string; count: number; total_spent: number }[];
  } | null>(null);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    try {
      const params: any = { limit: 100 };
      if (sourceFilter) params.source_app = sourceFilter;
      if (search.trim()) params.q = search.trim();
      const [list, s] = await Promise.all([
        Api.listCustomers(params),
        Api.customerStats(),
      ]);
      setCustomers(list.customers);
      setStats(s);
    } catch (e: any) {
      Alert.alert(
        "Couldn't load customers",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [sourceFilter, search]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  const callPhone = (phone: string) => {
    if (!phone) return;
    Linking.openURL(`tel:${phone.replace(/[^+\d]/g, "")}`);
  };

  const sources = useMemo(() => {
    const arr = [{ key: "", label: "All", count: stats?.total }];
    (stats?.by_source || [])
      .filter((s) => s.source_app)
      .forEach((s) => {
        arr.push({
          key: s.source_app,
          label: sourceLabel(s.source_app),
          count: s.count,
        });
      });
    return arr;
  }, [stats]);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <Stack.Screen
        options={{
          headerShown: true,
          title: "Customers",
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
                <Text style={styles.statValue}>{stats.total}</Text>
                <Text style={styles.statLabel}>Customers</Text>
              </View>
              <View style={styles.statSep} />
              <View style={styles.statBox}>
                <Text style={styles.statValue}>{formatINR(stats.total_spent)}</Text>
                <Text style={styles.statLabel}>Lifetime spend</Text>
              </View>
            </View>
          ) : null}

          {/* Source filter chips — Phase F3.9 (now via shared
              `FilterChipRow` so this pattern can never drift again). */}
          <FilterChipRow
            testIDPrefix="source-filter"
            selected={sourceFilter || ""}
            onSelect={(k) => setSourceFilter(k)}
            style={{ marginBottom: 16 }}
            items={sources.map((s) => ({
              key: s.key || "",
              label: s.label,
              count: typeof s.count === "number" ? s.count : undefined,
              testID: `source-filter-${s.key || "all"}`,
            }))}
          />

          {/* Search */}
          <SearchBar
            testID="search-customers"
            value={search}
            onChangeText={setSearch}
            onClear={() => {
              // Phase-32 — clear search ALSO drops the source filter
              // so customers list reverts to "All Sources".
              setSourceFilter("");
            }}
            placeholder="Search by name, phone, email"
            onSubmitEditing={() => load()}
            containerStyle={{ marginHorizontal: 0 }}
          />

          {customers.length === 0 ? (
            <View style={styles.emptyCard}>
              <Text style={styles.emptyEmoji}>👥</Text>
              <Text style={styles.emptyTitle}>No customers synced yet</Text>
              <Text style={styles.emptySub}>
                Set up a webhook with event type "Customer Created" or
                "Customer Updated" in Shippzo → Webhooks. We'll capture every
                customer event from your store.
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
            customers.map((c) => (
              <View key={c.id} style={styles.card}>
                <View style={styles.cardHeader}>
                  <View style={{ flex: 1, gap: 4 }}>
                    <Text style={styles.cardName} numberOfLines={1}>
                      {c.customer_name || "Unknown customer"}
                    </Text>
                    <View style={styles.badgeRow}>
                      {c.source_app ? (
                        <View style={[styles.badge, { backgroundColor: "#E0E7FF" }]}>
                          <Text style={[styles.badgeTxt, { color: "#3730A3" }]}>
                            🏷 {sourceLabel(c.source_app)}
                          </Text>
                        </View>
                      ) : null}
                      {c.last_event ? (
                        <View style={[styles.badge, { backgroundColor: "#F1F5F9" }]}>
                          <Text style={[styles.badgeTxt, { color: "#475569" }]}>
                            {c.last_event === "customer_created" ? "🆕 New" : "✏️ Updated"}
                          </Text>
                        </View>
                      ) : null}
                    </View>
                  </View>
                  {c.total_spent > 0 ? (
                    <View style={{ alignItems: "flex-end" }}>
                      <Text style={styles.value}>{formatINR(c.total_spent)}</Text>
                      <Text style={styles.valueLbl}>{c.orders_count} orders</Text>
                    </View>
                  ) : null}
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

                {c.address || c.city || c.pincode ? (
                  <Text style={styles.addrTxt} numberOfLines={2}>
                    📍 {[c.address, c.city, c.state, c.pincode]
                      .filter(Boolean)
                      .join(", ")}
                  </Text>
                ) : null}
              </View>
            ))
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
    flexShrink: 0,
    borderRadius: 999,
    borderWidth: 1.5, borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  filterChipSel: { borderColor: colors.primary, backgroundColor: "#FFF7ED" },
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
  value: { fontSize: 15, fontWeight: "800", color: colors.text },
  valueLbl: { fontSize: 11, color: colors.textMuted },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  badgeTxt: { fontSize: 11, fontWeight: "800" },

  metaRow: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  metaItem: { flexDirection: "row", alignItems: "center", gap: 4 },
  metaTxt: { fontSize: 12, color: "#475569", maxWidth: 220 },

  addrTxt: { fontSize: 12, color: "#475569", lineHeight: 17 },
});
