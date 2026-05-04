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
  ActivityIndicator, Alert, RefreshControl, Linking, Share, Modal,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Api } from "../../lib/api";
import { errMsg } from "../../lib/errMsg";

type RangeKey = "this_week" | "last_week" | "this_month" | "last_month" | "last_30" | "custom";
const RANGES: Array<[RangeKey, string]> = [
  ["this_week",  "This Week"],
  ["last_week",  "Last Week"],
  ["this_month", "This Month"],
  ["last_month", "Last Month"],
  ["last_30",    "Last 30 days"],
  ["custom",     "Custom"],
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

  // Phase 2.5b — Custom date / multi-month picker. Two modes:
  //  • "range"  → pick FROM and TO dates (max 3-month span)
  //  • "months" → multi-select up to 3 specific months (Jan, Feb, …)
  // Once the user hits "Apply" we collapse the selection into a single
  // custom-range request (the backend doesn't need to know which mode
  // was used — just from/to).
  const [customOpen, setCustomOpen]       = useState(false);
  const [customMode, setCustomMode]       = useState<"range" | "months">("range");
  const [customFrom, setCustomFrom]       = useState<Date | null>(null);
  const [customTo, setCustomTo]           = useState<Date | null>(null);
  const [pickerOpen, setPickerOpen]       = useState<"from" | "to" | null>(null);
  const [selectedMonths, setSelectedMonths] = useState<string[]>([]); // ["2026-01", …]
  const [appliedFrom, setAppliedFrom]     = useState<string | null>(null); // ISO date
  const [appliedTo, setAppliedTo]         = useState<string | null>(null);
  const [appliedLabel, setAppliedLabel]   = useState<string>("");

  const load = useCallback(async () => {
    try {
      // Build query: custom mode forwards explicit from/to + label.
      const params: any = {
        courier_id: courierId === "all" ? undefined : courierId,
      };
      if (range === "custom" && appliedFrom && appliedTo) {
        params.range = "custom";
        params.from = appliedFrom;
        params.to   = appliedTo;
      } else if (range !== "custom") {
        params.range = range;
      } else {
        // "custom" picked but nothing applied yet → silently fall back
        // to this_month so the screen still has data to render.
        params.range = "this_month";
      }
      const [report, courierList] = await Promise.all([
        Api.courierBillingReport(params),
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
  }, [range, courierId, couriers, appliedFrom, appliedTo]);

  useEffect(() => { load(); }, [load]);

  const downloadExcel = async () => {
    try {
      const params: any = {
        courier_id: courierId === "all" ? undefined : courierId,
      };
      if (range === "custom" && appliedFrom && appliedTo) {
        params.range = "custom";
        params.from = appliedFrom;
        params.to   = appliedTo;
      } else {
        params.range = range;
      }
      const url = await Api.courierBillingReportExcelUrl(params);
      await Linking.openURL(url);
    } catch (e: any) {
      Alert.alert("Couldn't open download", errMsg(e, "Try again"));
    }
  };

  // Phase 2.5b — Custom range / multi-month picker handlers ─────
  const monthLabel = (ym: string) => {
    const [y, m] = ym.split("-");
    const d = new Date(parseInt(y), parseInt(m) - 1, 1);
    return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
  };

  const buildMonthOptions = () => {
    // Last 12 months going back from current month — newest first.
    const out: string[] = [];
    const d = new Date();
    for (let i = 0; i < 12; i++) {
      out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
      d.setMonth(d.getMonth() - 1);
    }
    return out;
  };

  const toggleMonth = (ym: string) => {
    setSelectedMonths((cur) => {
      if (cur.includes(ym)) return cur.filter((x) => x !== ym);
      if (cur.length >= 3) {
        Alert.alert(
          "Limit reached",
          "You can pick up to 3 months at a time. Deselect one to add another.",
        );
        return cur;
      }
      return [...cur, ym];
    });
  };

  const applyCustom = () => {
    if (customMode === "range") {
      if (!customFrom || !customTo) {
        Alert.alert("Pick both dates", "Please select FROM and TO dates first.");
        return;
      }
      if (customFrom > customTo) {
        Alert.alert("Invalid range", "FROM date must be before TO date.");
        return;
      }
      const days = Math.ceil((customTo.getTime() - customFrom.getTime()) / (1000 * 60 * 60 * 24));
      if (days > 92) {
        Alert.alert(
          "Range too long",
          "Maximum custom range is 3 months (92 days). Pick a shorter window.",
        );
        return;
      }
      const fromIso = customFrom.toISOString().slice(0, 10);
      // End-of-day to include full TO date in the window.
      const toEnd = new Date(customTo);
      toEnd.setHours(23, 59, 59, 999);
      const toIso   = toEnd.toISOString();
      setAppliedFrom(fromIso);
      setAppliedTo(toIso);
      setAppliedLabel(
        `${customFrom.toLocaleDateString("en-GB")} – ${customTo.toLocaleDateString("en-GB")}`,
      );
    } else {
      if (selectedMonths.length === 0) {
        Alert.alert("Pick months", "Select at least one month.");
        return;
      }
      // Sort months ascending and build a single contiguous range from
      // earliest start → latest end. Backend then aggregates whatever
      // shipments fall in that span.
      const sorted = [...selectedMonths].sort();
      const [fy, fm] = sorted[0].split("-").map(Number);
      const [ly, lm] = sorted[sorted.length - 1].split("-").map(Number);
      const start = new Date(fy, fm - 1, 1);
      const end   = new Date(ly, lm, 0, 23, 59, 59, 999);  // last day of last month
      setAppliedFrom(start.toISOString().slice(0, 10));
      setAppliedTo(end.toISOString());
      setAppliedLabel(sorted.map(monthLabel).join(" + "));
    }
    setRange("custom");
    setCustomOpen(false);
  };

  // When user taps the "Custom" range chip, open the picker.
  const onRangeChipPress = (key: RangeKey) => {
    if (key === "custom") {
      setCustomOpen(true);
      return;
    }
    setRange(key);
    setAppliedFrom(null);
    setAppliedTo(null);
    setAppliedLabel("");
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
                  onPress={() => onRangeChipPress(key)}
                >
                  <Text style={[styles.chipTxt, active && { color: "#fff" }]}>
                    {key === "custom" && appliedLabel ? `📅 ${appliedLabel}` : label}
                  </Text>
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

      {/* Phase 2.5b — Custom date / multi-month modal */}
      <Modal
        visible={customOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setCustomOpen(false)}
      >
        <View style={styles.modalBg}>
          <View style={styles.sheet}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>📅 Custom Range</Text>
              <TouchableOpacity onPress={() => setCustomOpen(false)} hitSlop={10}>
                <Ionicons name="close" size={22} color="#374151" />
              </TouchableOpacity>
            </View>

            {/* Mode tabs */}
            <View style={styles.modeRow}>
              <TouchableOpacity
                style={[styles.modeBtn, customMode === "range" && styles.modeBtnActive]}
                onPress={() => setCustomMode("range")}
              >
                <Text style={[styles.modeTxt, customMode === "range" && styles.modeTxtActive]}>
                  Date Range
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.modeBtn, customMode === "months" && styles.modeBtnActive]}
                onPress={() => setCustomMode("months")}
              >
                <Text style={[styles.modeTxt, customMode === "months" && styles.modeTxtActive]}>
                  Multi-Month
                </Text>
              </TouchableOpacity>
            </View>

            {customMode === "range" ? (
              <View style={{ marginTop: 12 }}>
                <Text style={styles.fieldLabel}>FROM</Text>
                <TouchableOpacity
                  style={styles.dateBox}
                  onPress={() => setPickerOpen("from")}
                >
                  <Ionicons name="calendar-outline" size={16} color="#1F4FBF" />
                  <Text style={styles.dateTxt}>
                    {customFrom ? customFrom.toLocaleDateString("en-GB") : "Pick start date"}
                  </Text>
                </TouchableOpacity>

                <Text style={[styles.fieldLabel, { marginTop: 12 }]}>TO</Text>
                <TouchableOpacity
                  style={styles.dateBox}
                  onPress={() => setPickerOpen("to")}
                >
                  <Ionicons name="calendar-outline" size={16} color="#1F4FBF" />
                  <Text style={styles.dateTxt}>
                    {customTo ? customTo.toLocaleDateString("en-GB") : "Pick end date"}
                  </Text>
                </TouchableOpacity>

                <Text style={styles.helperTxt}>
                  Maximum span: 3 months (92 days).
                </Text>

                {pickerOpen && (
                  <DateTimePicker
                    value={
                      pickerOpen === "from"
                        ? (customFrom || new Date())
                        : (customTo || new Date())
                    }
                    mode="date"
                    display={Platform.OS === "ios" ? "inline" : "default"}
                    maximumDate={new Date()}
                    onChange={(_e, d) => {
                      if (Platform.OS !== "ios") setPickerOpen(null);
                      if (d) {
                        if (pickerOpen === "from") setCustomFrom(d);
                        else                       setCustomTo(d);
                      }
                    }}
                  />
                )}
                {Platform.OS === "ios" && pickerOpen && (
                  <TouchableOpacity
                    style={styles.iosDoneBtn}
                    onPress={() => setPickerOpen(null)}
                  >
                    <Text style={styles.iosDoneTxt}>Done</Text>
                  </TouchableOpacity>
                )}
              </View>
            ) : (
              <View style={{ marginTop: 12 }}>
                <Text style={styles.helperTxt}>
                  Tap up to 3 months (e.g. Jan + Feb + Mar). Selected:
                  <Text style={{ fontWeight: "800", color: "#1F4FBF" }}>
                    {" "}{selectedMonths.length}/3
                  </Text>
                </Text>
                <View style={styles.monthGrid}>
                  {buildMonthOptions().map((ym) => {
                    const active = selectedMonths.includes(ym);
                    return (
                      <TouchableOpacity
                        key={ym}
                        onPress={() => toggleMonth(ym)}
                        style={[styles.monthChip, active && styles.monthChipActive]}
                      >
                        <Text style={[
                          styles.monthChipTxt,
                          active && { color: "#fff" },
                        ]}>
                          {monthLabel(ym)}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
                {selectedMonths.length > 0 && (
                  <TouchableOpacity
                    onPress={() => setSelectedMonths([])}
                    style={{ alignSelf: "flex-start", marginTop: 8 }}
                  >
                    <Text style={{ fontSize: 11, color: "#DC2626", textDecorationLine: "underline" }}>
                      Clear selection
                    </Text>
                  </TouchableOpacity>
                )}
              </View>
            )}

            <TouchableOpacity style={styles.applyBtn} onPress={applyCustom}>
              <Ionicons name="checkmark-circle" size={18} color="#fff" />
              <Text style={styles.applyBtnTxt}>Apply</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
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
