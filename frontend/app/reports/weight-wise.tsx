/**
 * Weight-wise Breakup Report — Phase 2.5
 * --------------------------------------
 * Buckets shipments by parsed weight so the owner can identify the
 * most-profitable weight ranges and negotiate courier rates
 * accordingly.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, TouchableOpacity, ScrollView, RefreshControl,
  Alert,
} from "react-native";
import { Stack, router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api } from "../../lib/api";
import { screenCache } from "../../lib/screenCache";
import { SkeletonReport } from "../../components/Skeleton";
import {
  PeriodPicker, RangeKey, reportStyles as S, downloadExcel,
} from "../../components/ReportShared";

export default function WeightWiseScreen() {
  const [range, setRange] = useState<RangeKey>("this_month");
  const [customFrom, setCustomFrom] = useState<Date | null>(null);
  const [customTo, setCustomTo] = useState<Date | null>(null);
  // Phase F5.1 SWR — seed from cache so re-visits are instant.
  const _cached = screenCache.get<any>("reports:weight-wise");
  const [loading, setLoading] = useState(!_cached);
  const [refreshing, setRefreshing] = useState(false);
  const [data, setData] = useState<any>(_cached);

  const params = useMemo(() => {
    if (range === "custom" && customFrom && customTo) {
      return { range, from: customFrom.toISOString(), to: customTo.toISOString() };
    }
    return { range };
  }, [range, customFrom, customTo]);

  const load = useCallback(async () => {
    try {
      const res = await Api.meWeightWise(params);
      setData(res);
      screenCache.set("reports:weight-wise", res);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally { setLoading(false); setRefreshing(false); }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  return (
    <SafeAreaView style={S.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "Weight-wise Breakup" }} />
      <View style={S.header}>
        <TouchableOpacity onPress={() => router.back()} style={S.backBtn}>
          <PhIcon name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={S.title}>Weight-wise Breakup</Text>
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
          <SkeletonReport rows={5} />
        ) : (
          <>
            <View style={S.kpiGrid}>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>TOTAL SHIPMENTS</Text>
                <Text style={S.kpiValue}>{data?.total_shipments || 0}</Text>
              </View>
              <View style={S.kpiCard}>
                <Text style={S.kpiLabel}>UNIQUE COURIERS</Text>
                <Text style={S.kpiValue}>{data?.couriers?.length || 0}</Text>
              </View>
            </View>

            <Text style={S.sectionTitle}>Weight Buckets</Text>
            <View style={S.card}>
              <View style={S.rowH}>
                <Text style={[S.rowHTxt, { flex: 2 }]}>Bucket</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Count</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Revenue</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Avg Cost</Text>
              </View>
              {(data?.buckets || []).filter((b: any) => b.count > 0).map((b: any) => (
                <View key={b.bucket} style={S.row}>
                  <Text style={[S.rowTxt, { flex: 2, fontWeight: "700" }]}>{b.bucket}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>{b.count}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>₹{Math.round(b.revenue)}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>₹{b.avg_cost}</Text>
                </View>
              ))}
            </View>

            <Text style={S.sectionTitle}>Per Courier (count per bucket)</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              <View style={[S.card, { minWidth: 520 }]}>
                <View style={S.rowH}>
                  <Text style={[S.rowHTxt, { width: 130 }]}>Courier</Text>
                  {["0–500g", "500g–1kg", "1–2kg", "2–5kg", "5kg+"].map(h => (
                    <Text key={h} style={[S.rowHTxt, { width: 70, textAlign: "center" }]}>{h}</Text>
                  ))}
                  <Text style={[S.rowHTxt, { width: 50, textAlign: "right" }]}>Total</Text>
                </View>
                {(data?.couriers || []).map((c: any) => (
                  <View key={c.courier_name} style={S.row}>
                    <Text style={[S.rowTxt, { width: 130, fontWeight: "700" }]} numberOfLines={1}>{c.courier_name}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "center" }]}>{c.by_bucket?.["0–500 g"] || 0}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "center" }]}>{c.by_bucket?.["500 g–1 kg"] || 0}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "center" }]}>{c.by_bucket?.["1–2 kg"] || 0}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "center" }]}>{c.by_bucket?.["2–5 kg"] || 0}</Text>
                    <Text style={[S.rowTxt, { width: 70, textAlign: "center" }]}>{c.by_bucket?.["5 kg+"] || 0}</Text>
                    <Text style={[S.rowTxt, { width: 50, textAlign: "right", fontWeight: "800" }]}>{c.total}</Text>
                  </View>
                ))}
              </View>
            </ScrollView>

            <TouchableOpacity
              style={S.excelBtn}
              onPress={() => downloadExcel("/me/reports/weight-wise/excel", params as any)}
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
