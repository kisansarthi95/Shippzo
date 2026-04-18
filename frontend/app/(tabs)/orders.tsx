import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity,
  FlatList, RefreshControl, ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { Api, SheetOrder } from "../../lib/api";
import { colors } from "../../lib/theme";

type Filter = "pending" | "shipped" | "all";

export default function OrdersFromSheet() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [orders, setOrders] = useState<SheetOrder[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [headersChanged, setHeadersChanged] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("pending");
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const settings = await Api.getSettings();
      const isConnected = Boolean(settings.sheet?.sheet_id);
      setConnected(isConnected);
      if (!isConnected) {
        setOrders([]);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const res = await Api.sheetsOrders();
      setOrders(res.orders);
      setHeadersChanged(!!res.headers_changed);
      setLastSync(new Date());
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to fetch sheet");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      // auto-refresh every 60s while tab is focused
      intervalRef.current = setInterval(() => {
        load();
      }, 60_000);
      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }, [load])
  );

  const visible = orders.filter((o) => {
    if (filter === "pending" && o.already_shipped) return false;
    if (filter === "shipped" && !o.already_shipped) return false;
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return (
      (o.order_id || "").toLowerCase().includes(q) ||
      (o.customer_name || "").toLowerCase().includes(q) ||
      (o.phone || "").toLowerCase().includes(q) ||
      (o.city || "").toLowerCase().includes(q)
    );
  });

  const pendingCount = orders.filter((o) => !o.already_shipped).length;

  const shipNow = (o: SheetOrder) => {
    // Navigate to Add with prefilled fields via URL params (stringified)
    router.push({
      pathname: "/(tabs)/add",
      params: {
        prefill: JSON.stringify({
          order_id: o.order_id,
          customer_name: o.customer_name,
          phone: o.phone,
          address: o.address,
          city: o.city,
          state: o.state,
          pincode: o.pincode,
          item: o.item,
          amount: o.amount,
          row_key: o.row_key,
        }),
      },
    });
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Orders</Text>
          <Text style={styles.subtitle}>
            {connected
              ? `${pendingCount} pending · synced ${lastSync ? timeAgo(lastSync) : "—"}`
              : "Google Sheet not connected"}
          </Text>
        </View>
        <TouchableOpacity
          testID="orders-refresh-btn"
          style={styles.refreshBtn}
          onPress={() => {
            setRefreshing(true);
            load();
          }}
        >
          <Ionicons name="refresh" size={20} color="#fff" />
        </TouchableOpacity>
      </View>

      {!connected ? (
        <View style={styles.empty} testID="orders-not-connected">
          <Ionicons name="logo-google" size={52} color="#9CA3AF" />
          <Text style={styles.emptyTitle}>Connect Google Sheet</Text>
          <Text style={styles.emptyText}>
            Settings → Google Sheet → paste sheet link → map columns. Orders will appear here automatically.
          </Text>
          <TouchableOpacity
            testID="orders-goto-settings"
            style={styles.primaryBtn}
            onPress={() => router.push("/(tabs)/settings")}
          >
            <Text style={styles.primaryBtnText}>Open Settings</Text>
          </TouchableOpacity>
        </View>
      ) : error ? (
        <View style={styles.empty}>
          <Ionicons name="warning" size={48} color={colors.dangerText} />
          <Text style={styles.emptyTitle}>Couldn't sync</Text>
          <Text style={styles.emptyText}>{error}</Text>
          <TouchableOpacity style={styles.primaryBtn} onPress={load}>
            <Text style={styles.primaryBtnText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          {headersChanged && (
            <View style={styles.warnBox}>
              <Ionicons name="warning-outline" size={16} color={colors.warningText} />
              <Text style={styles.warnText}>
                Sheet columns changed. Re-map in Settings → Google Sheet.
              </Text>
            </View>
          )}

          <View style={styles.searchWrap}>
            <Ionicons name="search" size={18} color={colors.textMuted} />
            <TextInput
              testID="orders-search"
              value={search}
              onChangeText={setSearch}
              placeholder="Search order, name, phone, city"
              placeholderTextColor="#9CA3AF"
              style={styles.searchInput}
            />
          </View>

          <View style={styles.filterRow}>
            {([
              { k: "pending", label: `Pending${pendingCount ? ` (${pendingCount})` : ""}` },
              { k: "shipped", label: "Shipped" },
              { k: "all", label: `All (${orders.length})` },
            ] as const).map((f) => {
              const active = filter === f.k;
              return (
                <TouchableOpacity
                  key={f.k}
                  testID={`orders-filter-${f.k}`}
                  onPress={() => setFilter(f.k)}
                  style={[styles.filterPill, active && styles.filterPillActive]}
                >
                  <Text style={[styles.filterText, active && { color: "#fff" }]}>
                    {f.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {loading ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
          ) : (
            <FlatList
              testID="orders-list"
              data={visible}
              keyExtractor={(o) => o.row_key || String(o.row_index)}
              refreshControl={
                <RefreshControl
                  refreshing={refreshing}
                  onRefresh={() => {
                    setRefreshing(true);
                    load();
                  }}
                />
              }
              contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
              ListEmptyComponent={
                <View style={styles.empty}>
                  <Ionicons name="cube-outline" size={48} color="#9CA3AF" />
                  <Text style={styles.emptyText}>
                    {orders.length === 0
                      ? "No orders in your sheet yet."
                      : "No matching orders."}
                  </Text>
                </View>
              }
              renderItem={({ item }) => (
                <View
                  style={[styles.card, item.already_shipped && { opacity: 0.55 }]}
                  testID={`order-card-${item.row_index}`}
                >
                  <View style={styles.row}>
                    <Text style={styles.orderId}>
                      {item.order_id ? `#${item.order_id}` : `Row ${item.row_index}`}
                    </Text>
                    {item.already_shipped ? (
                      <View style={styles.shippedChip}>
                        <Text style={styles.shippedChipText}>SHIPPED</Text>
                      </View>
                    ) : (
                      <View style={styles.pendingChip}>
                        <Text style={styles.pendingChipText}>PENDING</Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.customerName}>
                    {item.customer_name || "(no name)"}
                  </Text>
                  <Text style={styles.metaLine}>
                    {item.phone || "no phone"} · {item.city || "—"}
                  </Text>
                  {!!item.item && (
                    <Text style={styles.itemLine} numberOfLines={2}>
                      📦 {item.item}
                    </Text>
                  )}
                  {!!item.amount && (
                    <Text style={styles.amountLine}>₹{item.amount}</Text>
                  )}
                  {!item.already_shipped && (
                    <TouchableOpacity
                      testID={`ship-now-${item.row_index}`}
                      onPress={() => shipNow(item)}
                      style={styles.shipBtn}
                    >
                      <Ionicons name="send" size={16} color="#fff" />
                      <Text style={styles.shipBtnText}>Ship this order</Text>
                    </TouchableOpacity>
                  )}
                </View>
              )}
            />
          )}
        </>
      )}
    </SafeAreaView>
  );
}

function timeAgo(d: Date): string {
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 10,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  subtitle: { color: colors.textMuted, fontSize: 12, marginTop: 2 },
  refreshBtn: {
    width: 44, height: 44, borderRadius: 10,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16, marginTop: 4,
    flexDirection: "row", alignItems: "center", gap: 8,
    height: 46, backgroundColor: colors.surface,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 10, paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRow: {
    flexDirection: "row", gap: 8,
    paddingHorizontal: 16, paddingVertical: 12,
  },
  filterPill: {
    paddingHorizontal: 14, paddingVertical: 8,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 999, backgroundColor: "#fff",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: { fontWeight: "700", fontSize: 13, color: colors.text },
  warnBox: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginHorizontal: 16, marginTop: 8,
    padding: 10, backgroundColor: colors.warningBg,
    borderWidth: 1, borderColor: "#FDE68A", borderRadius: 8,
  },
  warnText: { color: colors.warningText, fontSize: 12, fontWeight: "600", flex: 1 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  orderId: {
    fontFamily: "Courier", fontWeight: "800", fontSize: 13,
    color: colors.primary, letterSpacing: 1,
  },
  customerName: {
    marginTop: 6, fontSize: 16, fontWeight: "800", color: colors.text,
  },
  metaLine: { marginTop: 2, fontSize: 12, color: colors.textMuted },
  itemLine: { marginTop: 4, fontSize: 13, color: colors.text, fontWeight: "600" },
  amountLine: { marginTop: 4, fontSize: 14, color: colors.text, fontWeight: "800" },
  shipBtn: {
    marginTop: 12, flexDirection: "row", alignItems: "center",
    justifyContent: "center", gap: 6,
    height: 42, backgroundColor: colors.primary, borderRadius: 10,
  },
  shipBtnText: { color: "#fff", fontWeight: "800" },
  shippedChip: {
    backgroundColor: colors.successBg,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4,
  },
  shippedChipText: { fontSize: 10, fontWeight: "800", color: colors.successText, letterSpacing: 0.5 },
  pendingChip: {
    backgroundColor: colors.warningBg,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4,
  },
  pendingChipText: { fontSize: 10, fontWeight: "800", color: colors.warningText, letterSpacing: 0.5 },
  empty: {
    alignItems: "center", padding: 30, marginHorizontal: 16, marginTop: 20,
    backgroundColor: colors.surface, borderRadius: 12,
    borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyTitle: { marginTop: 14, fontSize: 16, fontWeight: "800", color: colors.text },
  emptyText: {
    marginTop: 8, color: colors.textMuted, textAlign: "center", fontSize: 13, lineHeight: 18,
  },
  primaryBtn: {
    marginTop: 16, backgroundColor: colors.primary,
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },
});
