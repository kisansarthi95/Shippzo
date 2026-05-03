/**
 * Unified Analytics Dashboard — Phase I.2
 * ───────────────────────────────────────
 * • Accessible to ALL users.
 * • Default scope = "mine" (the signed-in user's own shipments).
 * • Admins can flip a top-level toggle to "Platform" for org-wide
 *   aggregates (uses the same /analytics/overview endpoint with
 *   scope=platform — backend enforces is_admin).
 * • Filters: range, courier, status, payment_mode, state. All chips
 *   trigger an immediate re-fetch — keeps the data live.
 * • Charts: 30-day trend (line), by-status horizontal bars,
 *   by-payment doughnut summary, by-courier list, by-city list.
 *
 * NOTE: This screen REPLACES the legacy /admin/analytics.tsx surface.
 *       The old route now redirects here (router.replace) so existing
 *       deep links keep working.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, Dimensions, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { LineChart } from "react-native-chart-kit";
import { Api } from "../lib/api";
import { useAuth } from "../lib/auth";

type Range = "today" | "7d" | "30d" | "90d" | "all";
type Scope = "mine" | "platform";
type Overview = Awaited<ReturnType<typeof Api.analyticsOverview>>;

const RANGES: Array<[Range, string]> = [
  ["today", "Today"],
  ["7d",    "7d"],
  ["30d",   "30d"],
  ["90d",   "90d"],
  ["all",   "All"],
];

const STAGE_COLOUR: Record<string, string> = {
  Pending:           "#9333EA",
  Processing:        "#0EA5E9",
  "Ready to Ship":   "#10B981",
  Shipped:           "#1F4FBF",
  Dispatch:          "#1F4FBF",
  Dispatched:        "#1F4FBF",
  Delivered:         "#059669",
  Feedback:          "#B45309",
  Modified:          "#F59E0B",
  Returned:          "#DC2626",
  Cancelled:         "#6B7280",
  "Cancel by buyer": "#6B7280",
};

const formatINR = (n: number) => {
  if (n >= 10000000) return `₹${(n / 10000000).toFixed(1)}Cr`;
  if (n >= 100000)   return `₹${(n / 100000).toFixed(1)}L`;
  if (n >= 1000)     return `₹${(n / 1000).toFixed(1)}k`;
  return `₹${n}`;
};

export default function AnalyticsScreen() {
  const { user } = useAuth();
  const isAdmin = !!user?.is_admin;

  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData]             = useState<Overview | null>(null);

  // Filter state -------------------------------------------------
  const [range, setRange]   = useState<Range>("30d");
  const [scope, setScope]   = useState<Scope>("mine");
  const [courier, setCourier]           = useState<string>("all");
  const [status, setStatus]             = useState<string>("all");
  const [paymentMode, setPaymentMode]   = useState<string>("all");
  const [stateFilter, setStateFilter]   = useState<string>("all");

  // Filter modals (simple inline pickers) ------------------------
  const [showCourierPicker, setShowCourierPicker] = useState(false);
  const [showStatusPicker,  setShowStatusPicker]  = useState(false);
  const [showStatePicker,   setShowStatePicker]   = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await Api.analyticsOverview({
        range,
        scope,
        courier:      courier      === "all" ? undefined : courier,
        status:       status       === "all" ? undefined : status,
        payment_mode: paymentMode  === "all" ? undefined : paymentMode,
        state:        stateFilter  === "all" ? undefined : stateFilter,
      });
      setData(d);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [range, scope, courier, status, paymentMode, stateFilter]);

  useEffect(() => { load(); }, [load]);

  const screenW = Dimensions.get("window").width;
  const activeFilterCount = useMemo(() => {
    let n = 0;
    if (courier      !== "all") n++;
    if (status       !== "all") n++;
    if (paymentMode  !== "all") n++;
    if (stateFilter  !== "all") n++;
    return n;
  }, [courier, status, paymentMode, stateFilter]);

  if (loading || !data) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Analytics", headerShown: true }} />
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  const k = data.kpi;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Analytics", headerShown: true }} />

      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 40 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
          />
        }
      >
        {/* Admin-only scope toggle */}
        {isAdmin && (
          <View style={styles.scopeRow}>
            <TouchableOpacity
              style={[styles.scopeBtn, scope === "mine" && styles.scopeBtnActive]}
              onPress={() => setScope("mine")}
            >
              <Ionicons
                name="person-circle"
                size={14}
                color={scope === "mine" ? "#fff" : "#1F4FBF"}
              />
              <Text style={[styles.scopeTxt, scope === "mine" && styles.scopeTxtActive]}>
                મારો ડેટા (My data)
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.scopeBtn, scope === "platform" && styles.scopeBtnActive]}
              onPress={() => setScope("platform")}
            >
              <Ionicons
                name="globe"
                size={14}
                color={scope === "platform" ? "#fff" : "#1F4FBF"}
              />
              <Text style={[styles.scopeTxt, scope === "platform" && styles.scopeTxtActive]}>
                Platform Total
              </Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Range chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={{ flexDirection: "row", gap: 6 }}>
            {RANGES.map(([key, label]) => {
              const active = range === key;
              return (
                <TouchableOpacity
                  key={key}
                  style={[styles.chip, active && styles.chipActive]}
                  onPress={() => setRange(key)}
                >
                  <Text style={[styles.chipText, active && { color: "#fff" }]}>
                    {label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </ScrollView>

        {/* Filter row (courier / status / payment / state) */}
        <View style={styles.filterRow}>
          <FilterPill
            icon="car"
            label={courier === "all" ? "All Couriers" : courier}
            active={courier !== "all"}
            onPress={() => setShowCourierPicker(true)}
          />
          <FilterPill
            icon="layers"
            label={status === "all" ? "All Status" : status}
            active={status !== "all"}
            onPress={() => setShowStatusPicker(true)}
          />
          <FilterPill
            icon="card"
            label={
              paymentMode === "all" ? "All Payments"
              : paymentMode === "COD" ? "COD only"
              : "Prepaid only"
            }
            active={paymentMode !== "all"}
            onPress={() => {
              // Simple 3-way toggle — no modal needed.
              setPaymentMode((p) =>
                p === "all" ? "COD"
                : p === "COD" ? "PREPAID"
                : "all",
              );
            }}
          />
          <FilterPill
            icon="location"
            label={stateFilter === "all" ? "All States" : stateFilter}
            active={stateFilter !== "all"}
            onPress={() => setShowStatePicker(true)}
          />
          {activeFilterCount > 0 && (
            <TouchableOpacity
              style={styles.clearFilter}
              onPress={() => {
                setCourier("all"); setStatus("all");
                setPaymentMode("all"); setStateFilter("all");
              }}
            >
              <Ionicons name="close-circle" size={14} color="#DC2626" />
              <Text style={styles.clearFilterTxt}>Clear ({activeFilterCount})</Text>
            </TouchableOpacity>
          )}
        </View>

        {/* KPI Grid - 4 cards */}
        <View style={styles.kpiGrid}>
          <KpiCard
            icon="cube" tint="#1F4FBF"
            value={String(k.total)}
            label="Total Orders"
          />
          <KpiCard
            icon="checkmark-done" tint="#059669"
            value={String(k.delivered)}
            label="Delivered"
            subtitle={k.total ? `${Math.round((k.delivered / k.total) * 100)}% rate` : ""}
          />
          <KpiCard
            icon="time" tint="#F59E0B"
            value={String(k.pending)}
            label="In Pipeline"
            subtitle="Not delivered"
          />
          <KpiCard
            icon="cash" tint="#9333EA"
            value={formatINR(k.revenue)}
            label="Total Revenue"
            subtitle={`COD ${formatINR(k.revenue_cod)} · Prepaid ${formatINR(k.revenue_prepaid)}`}
          />
        </View>

        {/* 30-day trend */}
        {data.trend_30d.some((d) => d.count > 0) && (
          <>
            <Text style={styles.sectionTitle}>📈 Daily orders (last 30 days)</Text>
            {(() => {
              const labels = data.trend_30d.map((d, i) =>
                i % 6 === 0 ? d.date.slice(5) : "",
              );
              const values = data.trend_30d.map((d) => Math.max(0, d.count));
              const maxVal = Math.max(1, ...values);
              return (
                <View style={styles.card}>
                  <LineChart
                    data={{ labels, datasets: [{ data: values }] }}
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
          </>
        )}

        {/* By status breakdown */}
        {Object.keys(data.shipments.by_status).length > 0 && (
          <>
            <Text style={styles.sectionTitle}>📦 By status</Text>
            <View style={styles.card}>
              {(() => {
                const entries = Object.entries(data.shipments.by_status)
                  .sort(([, a], [, b]) => b - a);
                const total = entries.reduce((s, [, n]) => s + n, 0) || 1;
                return entries.map(([key, n]) => (
                  <View key={key} style={styles.statusRow}>
                    <View
                      style={[styles.statusDot, { backgroundColor: STAGE_COLOUR[key] || "#374151" }]}
                    />
                    <Text style={styles.statusName} numberOfLines={1}>{key}</Text>
                    <View style={styles.statusBar}>
                      <View
                        style={[
                          styles.statusBarFill,
                          {
                            width: `${(n / total) * 100}%`,
                            backgroundColor: STAGE_COLOUR[key] || "#374151",
                          },
                        ]}
                      />
                    </View>
                    <Text style={styles.statusCount}>{n}</Text>
                  </View>
                ));
              })()}
            </View>
          </>
        )}

        {/* Payment-mode summary */}
        {(data.shipments.by_payment.COD || data.shipments.by_payment.PREPAID) && (
          <>
            <Text style={styles.sectionTitle}>💳 Payment mode</Text>
            <View style={[styles.card, styles.payCard]}>
              <PayBox
                label="COD"
                count={data.shipments.by_payment.COD || 0}
                amount={k.revenue_cod}
                tint="#F59E0B"
                icon="cash-outline"
              />
              <PayBox
                label="Prepaid"
                count={data.shipments.by_payment.PREPAID || 0}
                amount={k.revenue_prepaid}
                tint="#10B981"
                icon="card-outline"
              />
            </View>
          </>
        )}

        {/* By courier */}
        <Text style={styles.sectionTitle}>🚚 Top couriers</Text>
        <View style={styles.card}>
          {data.shipments.by_courier.length === 0 ? (
            <Text style={styles.emptyMini}>No courier data</Text>
          ) : (
            data.shipments.by_courier.map((c, i) => (
              <TouchableOpacity
                key={c.name + i}
                style={styles.listRow}
                onPress={() => setCourier((p) => p === c.name ? "all" : c.name)}
              >
                <Text style={styles.listRank}>#{i + 1}</Text>
                <Text style={styles.listName} numberOfLines={1}>{c.name}</Text>
                <Text style={styles.listCount}>{c.count}</Text>
                <Ionicons
                  name={courier === c.name ? "filter" : "filter-outline"}
                  size={14}
                  color={courier === c.name ? "#1F4FBF" : "#9CA3AF"}
                  style={{ marginLeft: 6 }}
                />
              </TouchableOpacity>
            ))
          )}
        </View>

        {/* Top states & cities side-by-side */}
        <View style={styles.geoRow}>
          <View style={styles.geoCol}>
            <Text style={styles.sectionTitle}>🗺️ Top States</Text>
            <View style={styles.card}>
              {data.shipments.by_state.length === 0 ? (
                <Text style={styles.emptyMini}>—</Text>
              ) : (
                data.shipments.by_state.slice(0, 6).map((s, i) => (
                  <View key={s.name + i} style={styles.geoRowItem}>
                    <Text style={styles.geoRowName} numberOfLines={1}>{s.name}</Text>
                    <Text style={styles.geoRowCount}>{s.count}</Text>
                  </View>
                ))
              )}
            </View>
          </View>
          <View style={styles.geoCol}>
            <Text style={styles.sectionTitle}>🏙️ Top Cities</Text>
            <View style={styles.card}>
              {data.shipments.by_city.length === 0 ? (
                <Text style={styles.emptyMini}>—</Text>
              ) : (
                data.shipments.by_city.slice(0, 6).map((c, i) => (
                  <View key={c.name + i} style={styles.geoRowItem}>
                    <Text style={styles.geoRowName} numberOfLines={1}>{c.name}</Text>
                    <Text style={styles.geoRowCount}>{c.count}</Text>
                  </View>
                ))
              )}
            </View>
          </View>
        </View>

        {/* Admin-only extras */}
        {scope === "platform" && data.admin && (
          <>
            <Text style={styles.sectionTitle}>👑 Platform extras (admin)</Text>
            <View style={styles.card}>
              <View style={styles.adminMetricRow}>
                <View style={styles.adminMetric}>
                  <Text style={styles.adminMetricVal}>{data.admin.users.total}</Text>
                  <Text style={styles.adminMetricLabel}>Total users</Text>
                </View>
                <View style={styles.adminMetric}>
                  <Text style={styles.adminMetricVal}>+{data.admin.users.today}</Text>
                  <Text style={styles.adminMetricLabel}>New today</Text>
                </View>
                <View style={styles.adminMetric}>
                  <Text style={[styles.adminMetricVal, { color: "#DC2626" }]}>
                    {data.admin.sla_open}
                  </Text>
                  <Text style={styles.adminMetricLabel}>SLA breaches</Text>
                </View>
              </View>
            </View>
            <Text style={styles.sectionTitle}>🏆 Top users by volume</Text>
            <View style={styles.card}>
              {data.admin.top_users.length === 0 ? (
                <Text style={styles.emptyMini}>No data for this range</Text>
              ) : (
                data.admin.top_users.map((u, i) => (
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
            <View style={[styles.card, { marginTop: 14 }]}>
              <Text style={[styles.sectionTitle, { marginTop: 0, marginBottom: 8 }]}>
                ⚡ Admin quick links
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
          </>
        )}
      </ScrollView>

      {/* Inline picker overlays */}
      <PickerOverlay
        visible={showCourierPicker}
        title="Filter by Courier"
        options={["all", ...data.filter_options.couriers]}
        labelMap={{ all: "All Couriers" }}
        selected={courier}
        onSelect={(v) => { setCourier(v); setShowCourierPicker(false); }}
        onClose={() => setShowCourierPicker(false)}
      />
      <PickerOverlay
        visible={showStatusPicker}
        title="Filter by Status"
        options={["all", ...data.filter_options.statuses]}
        labelMap={{ all: "All Status" }}
        selected={status}
        onSelect={(v) => { setStatus(v); setShowStatusPicker(false); }}
        onClose={() => setShowStatusPicker(false)}
      />
      <PickerOverlay
        visible={showStatePicker}
        title="Filter by State"
        options={["all", ...data.filter_options.states]}
        labelMap={{ all: "All States" }}
        selected={stateFilter}
        onSelect={(v) => { setStateFilter(v); setShowStatePicker(false); }}
        onClose={() => setShowStatePicker(false)}
      />
    </SafeAreaView>
  );
}

// ────────────────────────────────────────────────────────────────
// Sub-components
// ────────────────────────────────────────────────────────────────
function FilterPill({ icon, label, active, onPress }: {
  icon: any; label: string; active: boolean; onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={[styles.filterPill, active && styles.filterPillActive]}
      onPress={onPress}
    >
      <Ionicons name={icon} size={12} color={active ? "#fff" : "#1F4FBF"} />
      <Text
        style={[styles.filterPillTxt, active && { color: "#fff" }]}
        numberOfLines={1}
      >
        {label}
      </Text>
    </TouchableOpacity>
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

function PayBox({ label, count, amount, tint, icon }: {
  label: string; count: number; amount: number; tint: string; icon: any;
}) {
  return (
    <View style={[styles.payBox, { borderColor: tint + "55" }]}>
      <View style={[styles.payBoxIcon, { backgroundColor: tint + "20" }]}>
        <Ionicons name={icon} size={20} color={tint} />
      </View>
      <Text style={[styles.payBoxCount, { color: tint }]}>{count}</Text>
      <Text style={styles.payBoxLabel}>{label}</Text>
      <Text style={styles.payBoxAmount}>{formatINR(amount)}</Text>
    </View>
  );
}

function PickerOverlay({ visible, title, options, labelMap, selected, onSelect, onClose }: {
  visible: boolean; title: string; options: string[];
  labelMap?: Record<string, string>;
  selected: string; onSelect: (v: string) => void; onClose: () => void;
}) {
  if (!visible) return null;
  return (
    <View style={styles.overlay}>
      <TouchableOpacity style={styles.overlayBg} onPress={onClose} activeOpacity={1} />
      <View style={styles.overlaySheet}>
        <View style={styles.overlayHeader}>
          <Text style={styles.overlayTitle}>{title}</Text>
          <TouchableOpacity onPress={onClose} hitSlop={10}>
            <Ionicons name="close" size={22} color="#374151" />
          </TouchableOpacity>
        </View>
        <ScrollView style={{ maxHeight: 360 }}>
          {options.map((opt) => {
            const isSel = selected === opt;
            return (
              <TouchableOpacity
                key={opt}
                style={[styles.overlayItem, isSel && styles.overlayItemActive]}
                onPress={() => onSelect(opt)}
              >
                <Text style={[styles.overlayItemTxt, isSel && { color: "#1F4FBF", fontWeight: "800" }]}>
                  {labelMap?.[opt] ?? opt}
                </Text>
                {isSel && <Ionicons name="checkmark" size={18} color="#1F4FBF" />}
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },

  scopeRow: {
    flexDirection: "row", gap: 6, marginBottom: 10,
  },
  scopeBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, paddingVertical: 10, borderRadius: 10,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#1F4FBF",
  },
  scopeBtnActive: { backgroundColor: "#1F4FBF" },
  scopeTxt:       { fontSize: 12, fontWeight: "800", color: "#1F4FBF" },
  scopeTxtActive: { color: "#fff" },

  chip: {
    paddingHorizontal: 14, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  chipText:   { fontSize: 12, fontWeight: "700", color: "#374151" },

  filterRow: {
    flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10,
  },
  filterPill: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingHorizontal: 10, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    maxWidth: 160,
  },
  filterPillActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  filterPillTxt: { fontSize: 11, fontWeight: "700", color: "#1F4FBF" },
  clearFilter: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 8, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#FEE2E2",
  },
  clearFilterTxt: { fontSize: 11, fontWeight: "800", color: "#DC2626" },

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
  kpiValue:  { fontSize: 20, fontWeight: "800" },
  kpiLabel:  { fontSize: 11, fontWeight: "700", color: "#374151", marginTop: 6 },
  kpiSub:    { fontSize: 10, color: "#9CA3AF", marginTop: 2 },

  sectionTitle: { fontSize: 13, fontWeight: "800", color: "#111827", marginTop: 18, marginBottom: 8 },

  card: { backgroundColor: "#fff", borderRadius: 10, padding: 12, borderWidth: 1, borderColor: "#E5E7EB" },

  statusRow:    { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 6 },
  statusDot:    { width: 8, height: 8, borderRadius: 4 },
  statusName:   { fontSize: 11.5, fontWeight: "700", color: "#374151", width: 100 },
  statusBar:    { flex: 1, height: 8, backgroundColor: "#F3F4F6", borderRadius: 4, overflow: "hidden" },
  statusBarFill:{ height: "100%", borderRadius: 4 },
  statusCount:  { fontSize: 11.5, fontWeight: "800", color: "#111827", width: 36, textAlign: "right" },

  payCard: { flexDirection: "row", gap: 10, padding: 10 },
  payBox: {
    flex: 1, padding: 12, borderRadius: 10,
    borderWidth: 1, alignItems: "center",
  },
  payBoxIcon: {
    width: 38, height: 38, borderRadius: 10,
    alignItems: "center", justifyContent: "center", marginBottom: 6,
  },
  payBoxCount:  { fontSize: 22, fontWeight: "800" },
  payBoxLabel:  { fontSize: 11, fontWeight: "700", color: "#374151", marginTop: 2 },
  payBoxAmount: { fontSize: 11, color: "#6B7280", marginTop: 2 },

  listRow: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  listRank:  { fontSize: 11, fontWeight: "800", color: "#9CA3AF", width: 24 },
  listName:  { fontSize: 12.5, fontWeight: "700", color: "#111827", flex: 1 },
  listSub:   { fontSize: 10.5, color: "#9CA3AF", marginTop: 2 },
  listCount: { fontSize: 13, fontWeight: "800", color: "#1F4FBF" },
  emptyMini: { fontSize: 11.5, color: "#9CA3AF", textAlign: "center", paddingVertical: 14 },

  geoRow: { flexDirection: "row", gap: 8 },
  geoCol: { flex: 1 },
  geoRowItem: {
    flexDirection: "row", justifyContent: "space-between",
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  geoRowName:  { fontSize: 12, color: "#374151", flex: 1, paddingRight: 8 },
  geoRowCount: { fontSize: 12, fontWeight: "800", color: "#1F4FBF" },

  adminMetricRow: { flexDirection: "row", justifyContent: "space-around" },
  adminMetric: { alignItems: "center" },
  adminMetricVal:   { fontSize: 22, fontWeight: "800", color: "#1F4FBF" },
  adminMetricLabel: { fontSize: 11, color: "#6B7280", marginTop: 2 },

  linkRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  linkBtn: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8,
    backgroundColor: "#F3F4F6",
  },
  linkText: { fontSize: 11.5, fontWeight: "800", color: "#111827" },

  // Picker overlay (inline modal)
  overlay: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    justifyContent: "flex-end",
  },
  overlayBg: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.4)",
  },
  overlaySheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 16, borderTopRightRadius: 16,
    paddingHorizontal: 14, paddingTop: 12, paddingBottom: 28,
  },
  overlayHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingBottom: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  overlayTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  overlayItem: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  overlayItemActive: { backgroundColor: "#EEF2FF" },
  overlayItemTxt: { fontSize: 13, color: "#374151" },
});
