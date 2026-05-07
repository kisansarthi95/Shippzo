/**
 * Shared bits used by every Phase-2.5 report screen so each individual
 * file can stay focused on its KPI cards / table layout. Keeps the
 * period-chip UX, custom-date modal & Excel-download button consistent.
 */
import React, { useState } from "react";
import PhIcon from "./PhIcon";
import {
  View, Text, StyleSheet, TouchableOpacity, Modal, Pressable,
  Platform, ActivityIndicator, Alert, Linking,
} from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import AsyncStorage from "@react-native-async-storage/async-storage";

export type RangeKey = "this_week" | "last_week" | "this_month" | "last_month" | "last_30" | "custom";

export const RANGES: Array<[RangeKey, string]> = [
  ["this_week",  "This Week"],
  ["last_week",  "Last Week"],
  ["this_month", "This Month"],
  ["last_month", "Last Month"],
  ["last_30",    "Last 30 days"],
];

export type ReportPeriod = {
  range: RangeKey;
  from?: string;
  to?: string;
  label?: string;
};

export function PeriodPicker({
  range, setRange, customFrom, customTo, setCustomFrom, setCustomTo, appliedLabel,
}: {
  range: RangeKey;
  setRange: (r: RangeKey) => void;
  customFrom: Date | null;
  customTo: Date | null;
  setCustomFrom: (d: Date | null) => void;
  setCustomTo: (d: Date | null) => void;
  appliedLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const [showFromPicker, setShowFromPicker] = useState(false);
  const [showToPicker, setShowToPicker] = useState(false);

  const apply = () => {
    if (!customFrom || !customTo) {
      Alert.alert("Select dates", "Please pick both a from and to date.");
      return;
    }
    const days = Math.round((customTo.getTime() - customFrom.getTime()) / 86400000);
    if (days < 0) { Alert.alert("Invalid range", "From date must be before To date."); return; }
    if (days > 92) { Alert.alert("Range too large", "Maximum span is 3 months (92 days)."); return; }
    setRange("custom");
    setOpen(false);
  };

  return (
    <>
      <View style={styles.chipsWrap}>
        {RANGES.map(([key, label]) => {
          const active = range === key;
          return (
            <TouchableOpacity
              key={key}
              style={[styles.chip, active && styles.chipActive]}
              onPress={() => { setRange(key); }}
            >
              <Text style={[styles.chipTxt, active && { color: "#fff" }]}>{label}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      <TouchableOpacity
        style={[styles.customBtn, range === "custom" && styles.customBtnActive]}
        onPress={() => setOpen(true)}
      >
        <PhIcon name="calendar" size={16} color={range === "custom" ? "#fff" : "#1F4FBF"} />
        <Text style={[styles.customBtnTxt, range === "custom" && { color: "#fff" }]}>
          {range === "custom" && appliedLabel ? appliedLabel : "Custom Date Range (up to 3 months)"}
        </Text>
        <PhIcon name="chevron-forward" size={14} color={range === "custom" ? "#fff" : "#9CA3AF"} />
      </TouchableOpacity>

      <Modal visible={open} animationType="slide" transparent onRequestClose={() => setOpen(false)}>
        <Pressable style={styles.modalBg} onPress={() => setOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>Pick Custom Date Range</Text>
              <TouchableOpacity onPress={() => setOpen(false)}>
                <PhIcon name="close" size={24} color="#111827" />
              </TouchableOpacity>
            </View>

            <Text style={styles.fieldLabel}>FROM</Text>
            <TouchableOpacity style={styles.dateBox} onPress={() => setShowFromPicker(true)}>
              <PhIcon name="calendar-outline" size={16} color="#1F4FBF" />
              <Text style={styles.dateTxt}>{customFrom ? customFrom.toDateString() : "Select start date"}</Text>
            </TouchableOpacity>
            {showFromPicker && (
              <DateTimePicker
                value={customFrom || new Date()}
                mode="date"
                onChange={(_e, d) => { setShowFromPicker(Platform.OS === "ios"); if (d) setCustomFrom(d); }}
                maximumDate={new Date()}
              />
            )}

            <Text style={[styles.fieldLabel, { marginTop: 14 }]}>TO</Text>
            <TouchableOpacity style={styles.dateBox} onPress={() => setShowToPicker(true)}>
              <PhIcon name="calendar-outline" size={16} color="#1F4FBF" />
              <Text style={styles.dateTxt}>{customTo ? customTo.toDateString() : "Select end date"}</Text>
            </TouchableOpacity>
            {showToPicker && (
              <DateTimePicker
                value={customTo || new Date()}
                mode="date"
                onChange={(_e, d) => { setShowToPicker(Platform.OS === "ios"); if (d) setCustomTo(d); }}
                maximumDate={new Date()}
              />
            )}

            <Text style={styles.helperTxt}>Maximum span: 3 months (92 days)</Text>
            <TouchableOpacity style={styles.applyBtn} onPress={apply}>
              <PhIcon name="checkmark" size={18} color="#fff" />
              <Text style={styles.applyBtnTxt}>Apply</Text>
            </TouchableOpacity>
          </Pressable>
        </Pressable>
      </Modal>
    </>
  );
}

/** Open the Excel download in the native browser/system handler with
 *  the user's auth token embedded as a query parameter (since browsers
 *  can't set Authorization headers on plain links). */
export async function downloadExcel(path: string, params: Record<string, string | undefined>) {
  let tok = "";
  try { tok = (await AsyncStorage.getItem("@auth_token")) || ""; } catch {}
  if (!tok) { Alert.alert("Auth required", "Please log in again."); return; }
  const qs = new URLSearchParams({ token: tok });
  for (const [k, v] of Object.entries(params)) if (v) qs.set(k, v);
  const base = (process.env.EXPO_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
  const url = `${base}/api${path}?${qs.toString()}`;
  Linking.openURL(url);
}

export const reportStyles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F9FAFB" },
  header: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 12, backgroundColor: "#fff",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  backBtn: { padding: 4 },
  title:    { fontSize: 18, fontWeight: "800", color: "#111827" },
  subtitle: { fontSize: 12, color: "#6B7280", marginTop: 2 },
  scroll:   { padding: 12, paddingBottom: 60, gap: 10 },

  // KPI grid
  kpiGrid:    { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  kpiCard:    {
    flex: 1, minWidth: "47%", backgroundColor: "#fff",
    borderRadius: 10, padding: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  kpiLabel:   { fontSize: 10.5, fontWeight: "800", color: "#6B7280", letterSpacing: 0.5 },
  kpiValue:   { fontSize: 20, fontWeight: "800", color: "#111827", marginTop: 2 },
  kpiSub:     { fontSize: 11, color: "#6B7280", marginTop: 1 },

  // Section
  sectionTitle: { fontSize: 14, fontWeight: "800", color: "#374151", marginTop: 12, marginBottom: 6 },
  card:         { backgroundColor: "#fff", borderRadius: 10, borderWidth: 1, borderColor: "#E5E7EB", overflow: "hidden" },

  rowH: {
    flexDirection: "row", paddingHorizontal: 10, paddingVertical: 8,
    backgroundColor: "#F3F4F6",
  },
  rowHTxt: { fontSize: 11, fontWeight: "800", color: "#6B7280", letterSpacing: 0.5 },
  row:     {
    flexDirection: "row", paddingHorizontal: 10, paddingVertical: 9,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: "#F3F4F6",
  },
  rowTxt:  { fontSize: 12.5, color: "#111827" },

  excelBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: "#10B981", padding: 13, borderRadius: 10, marginTop: 14,
  },
  excelBtnTxt: { color: "#fff", fontSize: 14, fontWeight: "800" },

  empty: {
    backgroundColor: "#fff", padding: 26, borderRadius: 10,
    alignItems: "center", borderWidth: 1, borderColor: "#E5E7EB", marginTop: 16,
  },
  emptyTxt: { fontSize: 13, fontWeight: "700", color: "#6B7280", marginTop: 8, textAlign: "center" },
  loadWrap: { flex: 1, alignItems: "center", justifyContent: "center" },
});

const styles = StyleSheet.create({
  chipsWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  chipTxt:    { fontSize: 12, fontWeight: "700", color: "#374151" },

  customBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#fff", borderWidth: 1.5, borderColor: "#1F4FBF",
    borderStyle: "dashed", borderRadius: 12,
    paddingVertical: 12, paddingHorizontal: 14, marginTop: 10,
  },
  customBtnActive: { backgroundColor: "#1F4FBF", borderStyle: "solid" },
  customBtnTxt: { flex: 1, fontSize: 13, fontWeight: "800", color: "#1F4FBF" },

  modalBg: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: { backgroundColor: "#fff", borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 18, paddingBottom: 32 },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingBottom: 8 },
  sheetTitle: { fontSize: 16, fontWeight: "800", color: "#111827" },
  fieldLabel: { fontSize: 11, fontWeight: "800", color: "#6B7280", letterSpacing: 0.5, marginBottom: 6, marginTop: 6 },
  dateBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F9FAFB", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, paddingVertical: 12, paddingHorizontal: 12,
  },
  dateTxt:   { flex: 1, fontSize: 13, fontWeight: "700", color: "#111827" },
  helperTxt: { fontSize: 11.5, color: "#6B7280", marginTop: 10, lineHeight: 16 },
  applyBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: "#1F4FBF", paddingVertical: 14, borderRadius: 12, marginTop: 18,
  },
  applyBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
