/**
 * COD Reconciliation Report — Phase 2.5
 * -------------------------------------
 * Expected vs received COD per courier so the owner can chase up
 * courier partners for pending settlements.
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

export default function ReconciliationScreen() {
  const [range, setRange] = useState<RangeKey>("this_month");
  const [customFrom, setCustomFrom] = useState<Date | null>(null);
  const [customTo, setCustomTo] = useState<Date | null>(null);
  // Phase F5.1 SWR — seed from cache so re-visits are instant.
  const _cached = screenCache.get<any>("reports:reconciliation");
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
      const res = await Api.meReconciliation(params);
      setData(res);
      screenCache.set("reports:reconciliation", res);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally { setLoading(false); setRefreshing(false); }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  const t = data?.totals || {};

  return (
    <SafeAreaView style={S.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "COD Reconciliation" }} />
      <View style={S.header}>
        <TouchableOpacity onPress={() => router.back()} style={S.backBtn}>
          <PhIcon name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={S.title}>COD Reconciliation</Text>
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
              <View style={[S.kpiCard, { backgroundColor: "#ECFDF5", borderColor: "#A7F3D0" }]}>
                <Text style={S.kpiLabel}>DELIVERED COD</Text>
                <Text style={[S.kpiValue, { color: "#059669" }]}>₹{Math.round(t.delivered_amt || 0)}</Text>
                <Text style={S.kpiSub}>{t.delivered_count || 0} shipments</Text>
              </View>
              <View style={[S.kpiCard, { backgroundColor: "#EFF6FF", borderColor: "#BFDBFE" }]}>
                <Text style={S.kpiLabel}>RECEIVED</Text>
                <Text style={[S.kpiValue, { color: "#1F4FBF" }]}>₹{Math.round(t.received_amt || 0)}</Text>
                <Text style={S.kpiSub}>{t.received_count || 0} settled</Text>
              </View>
              <View style={[S.kpiCard, { backgroundColor: "#FEF2F2", borderColor: "#FECACA", minWidth: "97%" }]}>
                <Text style={S.kpiLabel}>PENDING SETTLEMENT</Text>
                <Text style={[S.kpiValue, { color: "#DC2626" }]}>₹{Math.round(t.pending_amt || 0)}</Text>
                <Text style={S.kpiSub}>{t.pending_count || 0} orders awaiting courier remittance</Text>
              </View>
            </View>

            <Text style={S.sectionTitle}>Per Courier</Text>
            <View style={S.card}>
              <View style={S.rowH}>
                <Text style={[S.rowHTxt, { flex: 2 }]}>Courier</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Delivered</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Received</Text>
                <Text style={[S.rowHTxt, { flex: 1, textAlign: "right" }]}>Pending</Text>
              </View>
              {(data?.couriers || []).map((c: any) => (
                <View key={c.courier_name} style={S.row}>
                  <Text style={[S.rowTxt, { flex: 2, fontWeight: "700" }]} numberOfLines={1}>{c.courier_name}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right" }]}>₹{Math.round(c.delivered_amt)}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right", color: "#059669" }]}>₹{Math.round(c.received_amt)}</Text>
                  <Text style={[S.rowTxt, { flex: 1, textAlign: "right", color: "#DC2626", fontWeight: "800" }]}>₹{Math.round(c.pending_amt)}</Text>
                </View>
              ))}
            </View>

            {(data?.pending || []).length > 0 && (
              <>
                <Text style={S.sectionTitle}>Pending Shipments (first 200)</Text>
                <View style={S.card}>
                  {(data.pending || []).slice(0, 50).map((r: any) => (
                    <View key={r.id} style={S.row}>
                      <View style={{ flex: 3 }}>
                        <Text style={[S.rowTxt, { fontWeight: "700" }]} numberOfLines={1}>
                          {r.customer} · {r.courier}
                        </Text>
                        <Text style={{ fontSize: 11, color: "#6B7280" }}>{r.delivered_at} · {r.tracking_id || "—"}</Text>
                      </View>
                      <Text style={[S.rowTxt, { flex: 1, textAlign: "right", color: "#DC2626", fontWeight: "800" }]}>₹{Math.round(r.amount)}</Text>
                    </View>
                  ))}
                </View>
              </>
            )}

            <View style={{
              backgroundColor: "#FEF3C7", padding: 12, borderRadius: 10,
              marginTop: 10, borderWidth: 1, borderColor: "#FCD34D",
            }}>
              <Text style={{ fontSize: 12, color: "#92400E", lineHeight: 17 }}>
                ℹ️ Settlement tracking requires marking each COD shipment as
                'received' once the courier remits payment. Update the
                `cod_received` field from the shipment detail screen (coming
                next) to refine this report.
              </Text>
            </View>

            <TouchableOpacity
              style={S.excelBtn}
              onPress={() => downloadExcel("/me/reports/reconciliation/excel", params as any)}
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
