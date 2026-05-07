/**
 * Partner Comparison Report — Phase 2.5
 * -------------------------------------
 * Side-by-side courier metrics (delivery rate, return rate, avg cost,
 * revenue, margin) plus winner badges so the owner can reassign volume.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, TouchableOpacity, ScrollView, RefreshControl,
  ActivityIndicator, Alert,
} from "react-native";
import { Stack, router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api } from "../../lib/api";
import {
  PeriodPicker, RangeKey, reportStyles as S, downloadExcel,
} from "../../components/ReportShared";

export default function PartnerComparisonScreen() {
  const [range, setRange] = useState<RangeKey>("this_month");
  const [customFrom, setCustomFrom] = useState<Date | null>(null);
  const [customTo, setCustomTo] = useState<Date | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState<any>(null);

  const params = useMemo(() => {
    if (range === "custom" && customFrom && customTo) {
      return { range, from: customFrom.toISOString(), to: customTo.toISOString() };
    }
    return { range };
  }, [range, customFrom, customTo]);

  const load = useCallback(async () => {
    try {
      const res = await Api.mePartnerComparison(params);
      setData(res);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally { setLoading(false); setRefreshing(false); }
  }, [params]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  const rk = data?.rankings || {};
  const rankMeta = [
    { label: "🏆 Best delivery rate", value: rk.best_delivery,   color: "#10B981" },
    { label: "📉 Lowest return rate", value: rk.best_returns,    color: "#059669" },
    { label: "💵 Cheapest courier",   value: rk.cheapest,        color: "#1F4FBF" },
    { label: "💰 Highest revenue",    value: rk.highest_revenue, color: "#7C3AED" },
    { label: "⚠️ Worst delivery",     value: rk.worst_delivery,  color: "#F59E0B" },
    { label: "🚨 Most returns",       value: rk.worst_returns,   color: "#DC2626" },
  ];

  return (
    <SafeAreaView style={S.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "Partner Comparison" }} />
      <View style={S.header}>
        <TouchableOpacity onPress={() => router.back()} style={S.backBtn}>
          <PhIcon name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={S.title}>Partner Comparison</Text>
          <Text style={S.subtitle}>{data?.period?.label || "Loading…"}</Text>
        </View>
      </View>

      <ScrollView
        contentContainerStyle={S.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}
      >
        <PeriodPicker
          range={range} setRange={setRange}
          customFrom={customFrom} customTo={customTo}
          setCustomFrom={setCustomFrom} setCustomTo={setCustomTo}
        />

        {loading ? (
          <View style={S.loadWrap}><ActivityIndicator color="#7C3AED" /></View>
        ) : (
          <>
            {/* Rankings */}
            <Text style={S.sectionTitle}>Rankings</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 6 }}>
              {rankMeta.map((m, i) => m.value && (
                <View key={i} style={{
                  backgroundColor: m.color + "15",
                  paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
                  borderLeftWidth: 3, borderLeftColor: m.color,
                }}>
                  <Text style={{ fontSize: 10.5, fontWeight: "700", color: "#6B7280" }}>{m.label}</Text>
                  <Text style={{ fontSize: 13, fontWeight: "800", color: m.color }}>{m.value}</Text>
                </View>
              ))}
            </View>

            <Text style={S.sectionTitle}>Couriers Side-by-Side</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              <View style={[S.card, { minWidth: 720 }]}>
                <View style={S.rowH}>
                  <Text style={[S.rowHTxt, { width: 130 }]}>Courier</Text>
                  <Text style={[S.rowHTxt, { width: 60, textAlign: "right" }]}>Total</Text>
                  <Text style={[S.rowHTxt, { width: 70, textAlign: "right" }]}>Delivered</Text>
                  <Text style={[S.rowHTxt, { width: 70, textAlign: "right" }]}>Returned</Text>
                  <Text style={[S.rowHTxt, { width: 70, textAlign: "right" }]}>Del %</Text>
                  <Text style={[S.rowHTxt, { width: 70, textAlign: "right" }]}>Ret %</Text>
                  <Text style={[S.rowHTxt, { width: 80, textAlign: "right" }]}>Revenue</Text>
                  <Text style={[S.rowHTxt, { width: 80, textAlign: "right" }]}>Margin</Text>
                </View>
                {(data?.couriers || []).map((c: any) => (
                  <View key={c.courier_name} style={S.row}>
                    <Text style={[S.rowTxt, { width: 130, fontWeight: "700" }]} numberOfLines={1}>{c.courier_name}</Text>
                    <Text style={[S.rowTxt, { width: 60, textAlign: "right" }]}>{c.total}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "right", color: "#10B981" }]}>{c.delivered}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "right", color: "#DC2626" }]}>{c.returned}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "right" }]}>{c.delivery_rate}%</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "right" }]}>{c.return_rate}%</Text>
                    <Text style={[S.rowTxt, { width: 80, textAlign: "right" }]}>₹{Math.round(c.revenue)}</Text>
                    <Text style={[S.rowTxt, { width: 80, textAlign: "right", fontWeight: "800" }]}>₹{Math.round(c.margin)}</Text>
                  </View>
                ))}
              </View>
            </ScrollView>

            <TouchableOpacity
              style={S.excelBtn}
              onPress={() => downloadExcel("/me/reports/partner-comparison/excel", params as any)}
            >
              <PhIcon name="download" size={18} color="#fff" />
              <Text style={S.excelBtnTxt}>Download Excel (.xlsx)</Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}
