/**
 * Return Analysis Report — Phase 2.5
 * ----------------------------------
 * Shows returns by courier, reason, and repeat-return customers so
 * the shop owner can decide which courier to penalise / which
 * customers to blacklist.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, RefreshControl,
  ActivityIndicator, Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Stack, router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api } from "../../lib/api";
import {
  PeriodPicker, RangeKey, reportStyles as S, downloadExcel,
} from "../../components/ReportShared";

export default function ReturnAnalysisScreen() {
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
      const res = await Api.meReturnAnalysis(params);
      setData(res);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [params]);

  useEffect(() => { setLoading(true); load(); }, [load]);

  const sm = data?.summary || {};

  return (
    <SafeAreaView style={S.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "Return Analysis" }} />
      <View style={S.header}>
        <TouchableOpacity onPress={() => router.back()} style={S.backBtn}>
          <Ionicons name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={S.title}>Return Analysis</Text>
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
          <View style={S.loadWrap}><ActivityIndicator color="#DC2626" /></View>
        ) : (
          <>
            {/* KPIs */}
            <View style={S.kpiGrid}>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>TOTAL</Text>
                <Text style={S.kpiValue}>{sm.total_shipments || 0}</Text>
                <Text style={S.kpiSub}>shipments in period</Text>
              </View>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>RETURNS</Text>
                <Text style={[S.kpiValue, { color: "#DC2626" }]}>{sm.total_returns || 0}</Text>
                <Text style={S.kpiSub}>{sm.return_rate || 0}% of total</Text>
              </View>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>UNIQUE CUSTOMERS</Text>
                <Text style={S.kpiValue}>{sm.unique_customers || 0}</Text>
                <Text style={S.kpiSub}>with ≥1 return</Text>
              </View>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>REPEAT OFFENDERS</Text>
                <Text style={[S.kpiValue, { color: "#F59E0B" }]}>{data?.repeat_customers?.length || 0}</Text>
                <Text style={S.kpiSub}>2+ returns</Text>
              </View>
            </View>

            {/* By Courier */}
            <Text style={S.sectionTitle}>By Courier</Text>
            <View style={S.card}>
              <View style={S.rowH}>
                <Text style={[S.rowHTxt, { flex: 2 }]}>Courier</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Total</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Returns</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Rate</Text>
              </View>
              {(data?.by_courier || []).map((c: any) => (
                <View key={c.courier_name} style={S.row}>
                  <Text style={[S.rowTxt, { flex: 2, fontWeight: "700" }]} numberOfLines={1}>{c.courier_name}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>{c.total}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right", color: "#DC2626", fontWeight: "700" }]}>{c.returned}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>{c.return_rate}%</Text>
                </View>
              ))}
            </View>

            {/* By Reason */}
            {(data?.by_reason || []).length > 0 && (
              <>
                <Text style={S.sectionTitle}>By Reason</Text>
                <View style={S.card}>
                  {(data.by_reason || []).map((r: any) => (
                    <View key={r.reason} style={S.row}>
                      <Text style={[S.rowTxt, { flex: 3 }]}>{r.reason}</Text>
                      <Text style={[S.rowTxt, { flex: 1, textAlign: "right", fontWeight: "800" }]}>{r.count}</Text>
                    </View>
                  ))}
                </View>
              </>
            )}

            {/* Repeat customers */}
            {(data?.repeat_customers || []).length > 0 && (
              <>
                <Text style={S.sectionTitle}>Repeat Offenders (2+ returns)</Text>
                <View style={S.card}>
                  {(data.repeat_customers || []).map((c: any, i: number) => (
                    <View key={i} style={S.row}>
                      <View style={{ flex: 3 }}>
                        <Text style={[S.rowTxt, { fontWeight: "700" }]}>{c.name}</Text>
                        <Text style={{ fontSize: 11, color: "#6B7280" }}>{c.phone}</Text>
                      </View>
                      <Text style={[S.rowTxt, { flex: 1, textAlign: "right", color: "#F59E0B", fontWeight: "800" }]}>{c.count}×</Text>
                    </View>
                  ))}
                </View>
              </>
            )}

            {sm.total_returns === 0 && (
              <View style={S.empty}>
                <Ionicons name="checkmark-circle" size={40} color="#10B981" />
                <Text style={S.emptyTxt}>No returns in this period 🎉</Text>
              </View>
            )}

            <TouchableOpacity
              style={S.excelBtn}
              onPress={() => downloadExcel("/me/reports/return-analysis/excel", {
                range: params.range, from: params.from, to: params.to,
              } as any)}
            >
              <Ionicons name="download" size={18} color="#fff" />
              <Text style={S.excelBtnTxt}>Download Excel (.xlsx)</Text>
            </TouchableOpacity>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}
