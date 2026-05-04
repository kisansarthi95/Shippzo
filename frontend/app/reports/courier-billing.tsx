/**
 * Courier Billing Report — Phase 2.5
 * ──────────────────────────────────
 * In-app report screen that mirrors what's in the downloadable Excel.
 * Filters: range (this/last week/month, last 30, custom), specific
 * courier or All. Tap "Download Excel" to share the multi-sheet
 * workbook directly with the courier partner via WhatsApp / email.
 *
 * Visibility is gated by feature flag `reports_courier_billing` —
 * admins toggle which plans see it from the Plan Features admin.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, RefreshControl, Linking, Share,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Api } from "../../lib/api";
import { errMsg } from "../../lib/errMsg";

type RangeKey = "this_week" | "last_week" | "this_month" | "last_month" | "last_30";
const RANGES: Array<[RangeKey, string]> = [
  ["this_week",  "This Week"],
  ["last_week",  "Last Week"],
  ["this_month", "This Month"],
  ["last_month", "Last Month"],
  ["last_30",    "Last 30 days"],
];

type Report = Awaited<ReturnType<typeof Api.courierBillingReport>>;

const formatINR = (n: number) => {
  if (n >= 10000000) return `₹${(n / 10000000).toFixed(1)}Cr`;
  if (n >= 100000)   return `₹${(n / 100000).toFixed(1)}L`;
  if (n >= 1000)     return `₹${(n / 1000).toFixed(1)}k`;
  return `₹${Math.round(n)}`;
};

export default function CourierBillingReportScreen() {
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData]             = useState<Report | null>(null);
  const [range, setRange]           = useState<RangeKey>("this_month");
  const [courierId, setCourierId]   = useState<string>("all");
  const [couriers, setCouriers]     = useState<Array<{ id: string; name: string }>>([]);
  const [expanded, setExpanded]     = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      const [report, courierList] = await Promise.all([
        Api.courierBillingReport({
          range,
          courier_id: courierId === "all" ? undefined : courierId,
        }),
        couriers.length === 0 ? Api.listCouriers() : Promise.resolve(couriers),
      ]);
      setData(report);
      if (couriers.length === 0) {
        setCouriers(
          (courierList as any[]).map((c) => ({ id: c.id, name: c.name })),
        );
      }
    } catch (e: any) {
      Alert.alert("Error", errMsg(e, "Failed to load report"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [range, courierId, couriers]);

  useEffect(() => { load(); }, [load]);

  const downloadExcel = async () => {
    try {
      const url = await Api.courierBillingReportExcelUrl({
        range,
        courier_id: courierId === "all" ? undefined : courierId,
      });
      await Linking.openURL(url);
    } catch (e: any) {
      Alert.alert("Couldn't open download", errMsg(e, "Try again"));
    }
  };

  const shareSummary = async () => {
    if (!data) return;
    const lines = [
      `📊 Courier Billing — ${data.period.label}`,
      `Total: ${data.grand_total.shipments} shipments · ${formatINR(data.grand_total.charges)}`,
      "",
    ];
    data.couriers.slice(0, 8).forEach((c) => {
      lines.push(
        `🚚 ${c.courier_name}: ${c.total_shipments} parcels — ${formatINR(c.total_charges)}` +
        ` (COD ${formatINR(c.cod.amount)} · Prepaid ${formatINR(c.prepaid.amount)})`,
      );
    });
    if (data.rows_without_rate) {
      lines.push("", `⚠️ ${data.rows_without_rate} shipment(s) have no rate set.`);
    }
    try {
      await Share.share({ message: lines.join("\n") });
    } catch { /* user cancelled */ }
  };

  if (loading || !data) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Courier Billing", headerShown: true }} />
        <ActivityIndicator color="#1F4FBF" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Courier Billing", headerShown: true }} />

      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 60 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
          />
        }
      >
        {/* Range chips */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={{ flexDirection: "row", gap: 6 }}>
            {RANGES.map(([key, label]) => {
              const active = range === key;
              return (
                <TouchableOpacity
                  key={key}
                  testID={`range-${key}`}
                  style={[styles.chip, active && styles.chipActive]}
                  onPress={() => setRange(key)}
                >
                  <Text style={[styles.chipTxt, active && { color: "#fff" }]}>{label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </ScrollView>

        {/* Courier filter chips */}
        <Text style={[styles.sectionLabel, { marginTop: 12 }]}>Filter by courier</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}>
          <View style={{ flexDirection: "row", gap: 6 }}>
            <TouchableOpacity
              style={[styles.chip, courierId === "all" && styles.chipActive]}
              onPress={() => setCourierId("all")}
            >
              <Text style={[styles.chipTxt, courierId === "all" && { color: "#fff" }]}>
                All Couriers
              </Text>
            </TouchableOpacity>
            {couriers.map((c) => {
              const active = courierId === c.id;
              return (
                <TouchableOpacity
                  key={c.id}
                  style={[styles.chip, active && styles.chipActive]}
                  onPress={() => setCourierId(c.id)}
                >
                  <Text style={[styles.chipTxt, active && { color: "#fff" }]}>
                    {c.name}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </ScrollView>

        {/* Period card + grand total */}
        <View style={styles.periodCard}>
          <Text style={styles.periodLabel}>{data.period.label}</Text>
          <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 8 }}>
            <View style={styles.kpiBox}>
              <Text style={styles.kpiVal}>{data.grand_total.shipments}</Text>
              <Text style={styles.kpiLabel}>Shipments</Text>
            </View>
            <View style={styles.kpiBox}>
              <Text style={[styles.kpiVal, { color: "#1F4FBF" }]}>
                {formatINR(data.grand_total.charges)}
              </Text>
              <Text style={styles.kpiLabel}>Total Charges</Text>
            </View>
          </View>
          {data.rows_without_rate > 0 && (
            <View style={styles.warnRow}>
              <Ionicons name="warning" size={14} color="#B45309" />
              <Text style={styles.warnTxt}>
                {data.rows_without_rate} parcel(s) have no rate set — they
                count in volume but contribute ₹0 to charges.
              </Text>
            </View>
          )}
        </View>

        {/* Action buttons */}
        <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
          <TouchableOpacity
            testID="report-download-excel"
            style={[styles.actionBtn, { backgroundColor: "#1F4FBF" }]}
            onPress={downloadExcel}
          >
            <Ionicons name="download" size={16} color="#fff" />
            <Text style={styles.actionBtnTxt}>Download Excel</Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="report-share-summary"
            style={[styles.actionBtn, { backgroundColor: "#10B981" }]}
            onPress={shareSummary}
          >
            <Ionicons name="share-social" size={16} color="#fff" />
            <Text style={styles.actionBtnTxt}>Share Summary</Text>
          </TouchableOpacity>
        </View>

        {/* Per-courier sections */}
        {data.couriers.length === 0 ? (
          <View style={styles.emptyCard}>
            <Ionicons name="cube-outline" size={32} color="#9CA3AF" />
            <Text style={styles.emptyTxt}>No shipments in this range</Text>
          </View>
        ) : (
          data.couriers.map((c) => {
            const open = expanded[c.courier_id || c.courier_name] !== false;
            return (
              <View key={c.courier_id || c.courier_name} style={styles.courierCard}>
                <TouchableOpacity
                  onPress={() => setExpanded({
                    ...expanded,
                    [c.courier_id || c.courier_name]: !open,
                  })}
                  style={styles.courierHeader}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.courierName}>{c.courier_name}</Text>
                    <Text style={styles.courierMeta}>
                      {c.total_shipments} parcels · COD {c.cod.count} · Prepaid {c.prepaid.count}
                    </Text>
                  </View>
                  <View style={{ alignItems: "flex-end" }}>
                    <Text style={styles.courierTotal}>{formatINR(c.total_charges)}</Text>
                    <Ionicons
                      name={open ? "chevron-up" : "chevron-down"}
                      size={16} color="#9CA3AF"
                    />
                  </View>
                </TouchableOpacity>

                {open && (
                  <View style={styles.courierBody}>
                    {/* Payment split */}
                    <View style={styles.splitRow}>
                      <SplitBox
                        label="COD"
                        count={c.cod.count}
                        amount={c.cod.amount}
                        tint="#F59E0B"
                      />
                      <SplitBox
                        label="Prepaid"
                        count={c.prepaid.count}
                        amount={c.prepaid.amount}
                        tint="#10B981"
                      />
                    </View>

                    {/* By package type */}
                    {c.by_package_type.length > 0 && (
                      <>
                        <Text style={styles.subSectionLabel}>📦 By Package Type</Text>
                        {c.by_package_type.slice(0, 6).map((pt) => (
                          <View key={pt.type} style={styles.miniRow}>
                            <Text style={styles.miniName} numberOfLines={1}>{pt.type}</Text>
                            <Text style={styles.miniMeta}>{pt.count} × </Text>
                            <Text style={styles.miniAmount}>{formatINR(pt.amount)}</Text>
                          </View>
                        ))}
                      </>
                    )}

                    {/* Detail table */}
                    <Text style={styles.subSectionLabel}>📋 Shipments</Text>
                    {c.shipments.slice(0, 30).map((s, i) => (
                      <View key={s.id || i} style={styles.shipRow}>
                        <Text style={styles.shipDate}>{s.date.slice(5)}</Text>
                        <View style={{ flex: 1, marginLeft: 6 }}>
                          <Text style={styles.shipName} numberOfLines={1}>
                            {s.customer_name || "—"}
                          </Text>
                          <Text style={styles.shipMeta} numberOfLines={1}>
                            {s.tracking_id} · {s.city || "—"}
                            {s.package_type ? ` · ${s.package_type}` : ""}
                          </Text>
                        </View>
                        <View style={{ alignItems: "flex-end" }}>
                          <Text style={styles.shipRate}>{formatINR(s.rate)}</Text>
                          <Text style={styles.shipPm}>{s.payment_mode}</Text>
                        </View>
                      </View>
                    ))}
                    {c.shipments.length > 30 && (
                      <Text style={styles.moreTxt}>
                        + {c.shipments.length - 30} more (download Excel for full list)
                      </Text>
                    )}
                  </View>
                )}
              </View>
            );
          })
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function SplitBox({ label, count, amount, tint }: {
  label: string; count: number; amount: number; tint: string;
}) {
  return (
    <View style={[styles.splitBox, { borderColor: tint + "55" }]}>
      <Text style={[styles.splitCount, { color: tint }]}>{count}</Text>
      <Text style={styles.splitLabel}>{label}</Text>
      <Text style={styles.splitAmount}>{formatINR(amount)}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  chip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  chipTxt: { fontSize: 12, fontWeight: "700", color: "#374151" },
  sectionLabel: { fontSize: 11, fontWeight: "700", color: "#6B7280", marginBottom: 6 },

  periodCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14, marginTop: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  periodLabel: { fontSize: 14, fontWeight: "800", color: "#111827" },
  kpiBox: { flex: 1, alignItems: "center" },
  kpiVal: { fontSize: 22, fontWeight: "800", color: "#111827" },
  kpiLabel: { fontSize: 11, color: "#6B7280", marginTop: 2 },
  warnRow: {
    flexDirection: "row", gap: 6, marginTop: 10, padding: 8,
    borderRadius: 8, backgroundColor: "#FEF3C7",
  },
  warnTxt: { flex: 1, fontSize: 11, fontWeight: "600", color: "#92400E" },

  actionBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, paddingVertical: 12, borderRadius: 10,
  },
  actionBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  courierCard: {
    backgroundColor: "#fff", borderRadius: 12, marginTop: 12,
    borderWidth: 1, borderColor: "#E5E7EB", overflow: "hidden",
  },
  courierHeader: {
    flexDirection: "row", padding: 14, alignItems: "center",
  },
  courierName: { fontSize: 14, fontWeight: "800", color: "#111827" },
  courierMeta: { fontSize: 11, color: "#6B7280", marginTop: 2 },
  courierTotal: { fontSize: 16, fontWeight: "800", color: "#1F4FBF" },
  courierBody: {
    paddingHorizontal: 14, paddingBottom: 14,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: "#F3F4F6",
  },

  splitRow: { flexDirection: "row", gap: 8, marginTop: 10 },
  splitBox: {
    flex: 1, alignItems: "center", padding: 10,
    borderRadius: 10, borderWidth: 1,
  },
  splitCount:  { fontSize: 18, fontWeight: "800" },
  splitLabel:  { fontSize: 11, fontWeight: "700", color: "#374151", marginTop: 2 },
  splitAmount: { fontSize: 11.5, color: "#6B7280", marginTop: 2 },

  subSectionLabel: { fontSize: 11.5, fontWeight: "800", color: "#374151", marginTop: 14, marginBottom: 6 },
  miniRow: {
    flexDirection: "row", alignItems: "center", paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  miniName: { flex: 1, fontSize: 11.5, color: "#374151" },
  miniMeta: { fontSize: 11, color: "#9CA3AF" },
  miniAmount: { fontSize: 11.5, fontWeight: "800", color: "#1F4FBF" },

  shipRow: {
    flexDirection: "row", alignItems: "center", paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  shipDate: { fontSize: 10.5, fontWeight: "700", color: "#9CA3AF", width: 38 },
  shipName: { fontSize: 12.5, fontWeight: "700", color: "#111827" },
  shipMeta: { fontSize: 10.5, color: "#9CA3AF", marginTop: 1 },
  shipRate: { fontSize: 13, fontWeight: "800", color: "#1F4FBF" },
  shipPm:   { fontSize: 9.5, fontWeight: "700", color: "#9CA3AF", marginTop: 1 },
  moreTxt: { fontSize: 11, color: "#9CA3AF", textAlign: "center", marginTop: 8, fontStyle: "italic" },

  emptyCard: {
    backgroundColor: "#fff", padding: 24, borderRadius: 12,
    alignItems: "center", marginTop: 16,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  emptyTxt: { fontSize: 13, fontWeight: "700", color: "#6B7280", marginTop: 8 },
});
