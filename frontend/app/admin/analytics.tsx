/**
 * Admin Analytics Dashboard — Phase I
 * -----------------------------------
 * Single-screen overview pulling /api/admin/analytics/overview.
 * KPI cards on top, trend chart, top-couriers + top-users, system
 * health (SLA + sheet sync). All ranges (Today / 7d / 30d / 90d / All)
 * driven by chip filter at the top.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, Dimensions, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { LineChart } from "react-native-chart-kit";
import { Api } from "../../lib/api";

type Range = "today" | "7d" | "30d" | "90d" | "all";

type Overview = Awaited<ReturnType<typeof Api.adminAnalyticsOverview>>;

const RANGES: Array<[Range, string]> = [
  ["today", "Today"],
  ["7d",    "Last 7d"],
  ["30d",   "Last 30d"],
  ["90d",   "Last 90d"],
  ["all",   "All time"],
];

const STAGE_COLOUR: Record<string, string> = {
  Pending:        "#9333EA",
  Processing:     "#0EA5E9",
  "Ready to Ship":"#10B981",
  Shipped:        "#1F4FBF",
  Dispatch:       "#1F4FBF",
  Dispatched:     "#1F4FBF",
  Delivered:      "#059669",
  Feedback:       "#B45309",
  Modified:       "#F59E0B",
  Returned:       "#DC2626",
  Cancelled:      "#6B7280",
  "Cancel by buyer": "#6B7280",
};

const formatINR = (n: number) => {
  if (n >= 10000000) return `₹${(n / 10000000).toFixed(1)}Cr`;
  if (n >= 100000)   return `₹${(n / 100000).toFixed(1)}L`;
  if (n >= 1000)     return `₹${(n / 1000).toFixed(1)}k`;
  return `₹${n}`;
};

export default function AnalyticsScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [range, setRange]   = useState<Range>("30d");
  const [data, setData]     = useState<Overview | null>(null);

  const load = useCallback(async (r: Range = range) => {
    try {
      const d = await Api.adminAnalyticsOverview(r);
      setData(d);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [range]);

  useEffect(() => { load(range); }, [load, range]);

  if (loading || !data) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Analytics", headerShown: true }} />
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  const screenW = Dimensions.get("window").width;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Analytics", headerShown: true }} />

      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 40 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(range); }}
          />
        }
      >
        {/* Range chip row */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={{ flexDirection: "row", gap: 6 }}>
            {RANGES.map(([k, label]) => {
              const active = range === k;
              return (
                <TouchableOpacity
                  key={k}
                  style={[styles.chip, active && styles.chipActive]}
                  onPress={() => setRange(k)}
                >
                  <Text style={[styles.chipText, active && { color: "#fff" }]}>
                    {label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </ScrollView>

        {/* KPI grid (4) */}
        <View style={styles.kpiGrid}>
          <KpiCard icon="people" tint="#1F4FBF"
                   value={String(data.users.total)}
                   label="Total users"
                   subtitle={`${data.users.active} active · +${data.users.in_range} in range`} />
          <KpiCard icon="cube" tint="#10B981"
                   value={String(data.shipments.total)}
                   label="Shipments"
                   subtitle={`+${data.users.today} today`} />
          <KpiCard icon="alert-circle" tint="#DC2626"
                   value={String(data.sla.open)}
                   label="SLA breaches"
                   subtitle={`${data.sla.dismissed_in_range} resolved`} />
          <KpiCard icon="cash" tint="#9333EA"
                   value={formatINR(data.revenue.in_range)}
                   label="Revenue"
                   subtitle={`${formatINR(data.revenue.total)} all-time`} />
        </View>

        {/* 30-day trend chart */}
        <Text style={styles.sectionTitle}>📈 Shipments — last 30 days</Text>
        {(() => {
          // Down-sample to 10 labels but keep all data points.
          const labels = data.trend_30d.map((d, i) =>
            i % 6 === 0 ? d.date.slice(5) : "",
          );
          const values = data.trend_30d.map((d) => d.count);
          const maxVal = Math.max(1, ...values);
          return (
            <View style={styles.card}>
              <LineChart
                data={{
                  labels,
                  datasets: [{ data: values.map((v) => Math.max(0, v)) }],
                }}
                width={screenW - 56}
                height={180}
                bezier
                fromZero
                yAxisInterval={Math.ceil(maxVal / 4)}
                chartConfig={{
                  backgroundGradientFrom: "#fff",
                  backgroundGradientTo:   "#fff",
                  decimalPlaces: 0,
                  color:        (op = 1) => `rgba(31, 79, 191, ${op})`,
                  labelColor:   (op = 1) => `rgba(107, 114, 128, ${op})`,
                  propsForDots: { r: "3", stroke: "#1F4FBF", strokeWidth: "1" },
                }}
                style={{ borderRadius: 8, marginLeft: -10 }}
              />
            </View>
          );
        })()}

        {/* Shipments by status */}
        <Text style={styles.sectionTitle}>📦 By status</Text>
        <View style={styles.card}>
          {(() => {
            const entries = Object.entries(data.shipments.by_status)
              .sort(([, a], [, b]) => b - a);
            const total = entries.reduce((s, [, n]) => s + n, 0) || 1;
            return entries.map(([k, n]) => (
              <View key={k} style={styles.statusRow}>
                <View style={[styles.statusDot, { backgroundColor: STAGE_COLOUR[k] || "#374151" }]} />
                <Text style={styles.statusName}>{k}</Text>
                <View style={styles.statusBar}>
                  <View style={[
                    styles.statusBarFill,
                    {
                      width: `${(n / total) * 100}%`,
                      backgroundColor: STAGE_COLOUR[k] || "#374151",
                    },
                  ]} />
                </View>
                <Text style={styles.statusCount}>{n}</Text>
              </View>
            ));
          })()}
        </View>

        {/* Top couriers */}
        <Text style={styles.sectionTitle}>🚚 Top couriers</Text>
        <View style={styles.card}>
          {data.shipments.by_courier.length === 0 ? (
            <Text style={styles.emptyMini}>No courier data</Text>
          ) : (
            data.shipments.by_courier.map((c, i) => (
              <View key={c.name + i} style={styles.listRow}>
                <Text style={styles.listRank}>#{i + 1}</Text>
                <Text style={styles.listName} numberOfLines={1}>{c.name}</Text>
                <Text style={styles.listCount}>{c.count}</Text>
              </View>
            ))
          )}
        </View>

        {/* Top users */}
        <Text style={styles.sectionTitle}>🏆 Top users by volume</Text>
        <View style={styles.card}>
          {data.top_users.length === 0 ? (
            <Text style={styles.emptyMini}>No data for this range</Text>
          ) : (
            data.top_users.map((u, i) => (
              <View key={u.user_id + i} style={styles.listRow}>
                <Text style={styles.listRank}>#{i + 1}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.listName} numberOfLines={1}>{u.email}</Text>
                  {u.name ? <Text style={styles.listSub}>{u.name}</Text> : null}
                </View>
                <Text style={styles.listCount}>{u.count}</Text>
              </View>
            ))
          )}
        </View>

        {/* System health */}
        <Text style={styles.sectionTitle}>🩺 System health</Text>
        <View style={styles.healthGrid}>
          <HealthTile
            icon="logo-whatsapp" tint="#25D366"
            value={String(data.whatsapp.messages_today)}
            label="WhatsApp sent today"
          />
          <HealthTile
            icon="cloud-done" tint="#10B981"
            value={String(data.sheet_sync.connected_users)}
            label="Sheets connected"
          />
          <HealthTile
            icon="checkmark-circle" tint="#10B981"
            value={String(data.sheet_sync.counts?.ok || 0)}
            label="Rows synced"
          />
          <HealthTile
            icon="alert-circle" tint="#DC2626"
            value={String((data.sheet_sync.counts?.error || 0) + data.sheet_sync.queue_pending)}
            label="Sync attention"
          />
        </View>

        {/* Quick admin nav */}
        <View style={[styles.card, { marginTop: 14, padding: 12 }]}>
          <Text style={[styles.sectionTitle, { marginTop: 0, marginBottom: 8 }]}>
            ⚡ Quick links
          </Text>
          <View style={styles.linkRow}>
            <TouchableOpacity
              style={styles.linkBtn}
              onPress={() => router.push("/admin/sla-alerts" as any)}
            >
              <Ionicons name="alert-circle" size={14} color="#DC2626" />
              <Text style={styles.linkText}>SLA Alerts</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.linkBtn}
              onPress={() => router.push("/admin/stage-rules" as any)}
            >
              <Ionicons name="settings" size={14} color="#1F4FBF" />
              <Text style={styles.linkText}>Stage Rules</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.linkBtn}
              onPress={() => router.push("/sheet-sync" as any)}
            >
              <Ionicons name="cloud-upload" size={14} color="#10B981" />
              <Text style={styles.linkText}>Sheet Sync</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function KpiCard({ icon, tint, value, label, subtitle }: {
  icon: any; tint: string; value: string; label: string; subtitle?: string;
}) {
  return (
    <View style={[styles.kpiCard, { borderLeftColor: tint }]}>
      <View style={styles.kpiTopRow}>
        <Ionicons name={icon} size={16} color={tint} />
        <Text style={[styles.kpiValue, { color: tint }]}>{value}</Text>
      </View>
      <Text style={styles.kpiLabel}>{label}</Text>
      {subtitle ? <Text style={styles.kpiSub}>{subtitle}</Text> : null}
    </View>
  );
}

function HealthTile({ icon, tint, value, label }: {
  icon: any; tint: string; value: string; label: string;
}) {
  return (
    <View style={styles.healthTile}>
      <View style={[styles.healthIcon, { backgroundColor: tint + "20" }]}>
        <Ionicons name={icon} size={18} color={tint} />
      </View>
      <Text style={styles.healthValue}>{value}</Text>
      <Text style={styles.healthLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },

  chip: {
    paddingHorizontal: 14, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  chipText: { fontSize: 12, fontWeight: "700", color: "#374151" },

  kpiGrid: {
    flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 14,
  },
  kpiCard: {
    width: "48%", padding: 12,
    backgroundColor: "#fff", borderRadius: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
    borderLeftWidth: 4,
  },
  kpiTopRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  kpiValue: { fontSize: 20, fontWeight: "800" },
  kpiLabel: { fontSize: 11, fontWeight: "700", color: "#374151", marginTop: 6 },
  kpiSub:   { fontSize: 10, color: "#9CA3AF", marginTop: 2 },

  sectionTitle: { fontSize: 13, fontWeight: "800", color: "#111827", marginTop: 18, marginBottom: 8 },

  card: { backgroundColor: "#fff", borderRadius: 10, padding: 12, borderWidth: 1, borderColor: "#E5E7EB" },

  statusRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 6 },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusName: { fontSize: 11.5, fontWeight: "700", color: "#374151", width: 100 },
  statusBar: { flex: 1, height: 8, backgroundColor: "#F3F4F6", borderRadius: 4, overflow: "hidden" },
  statusBarFill: { height: "100%", borderRadius: 4 },
  statusCount: { fontSize: 11.5, fontWeight: "800", color: "#111827", width: 36, textAlign: "right" },

  listRow: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  listRank: { fontSize: 11, fontWeight: "800", color: "#9CA3AF", width: 24 },
  listName: { fontSize: 12.5, fontWeight: "700", color: "#111827", flex: 1 },
  listSub:  { fontSize: 10.5, color: "#9CA3AF", marginTop: 2 },
  listCount: { fontSize: 13, fontWeight: "800", color: "#1F4FBF" },
  emptyMini: { fontSize: 11.5, color: "#9CA3AF", textAlign: "center", paddingVertical: 14 },

  healthGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  healthTile: {
    width: "48%",
    backgroundColor: "#fff", borderRadius: 10, padding: 14,
    alignItems: "center",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  healthIcon: { width: 36, height: 36, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  healthValue: { fontSize: 18, fontWeight: "800", color: "#111827", marginTop: 6 },
  healthLabel: { fontSize: 10.5, color: "#6B7280", marginTop: 2, textAlign: "center" },

  linkRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  linkBtn: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8,
    backgroundColor: "#F3F4F6",
  },
  linkText: { fontSize: 11.5, fontWeight: "800", color: "#111827" },
});
