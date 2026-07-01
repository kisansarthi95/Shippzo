/**
 * ShipmentsFilterSheet — Phase B Filter Bottom Sheet.
 *
 * Opened via the funnel icon next to the SearchBar on the Shipments
 * screen. Contains three sections:
 *
 *   1. Date range   — All / Last 24h / Last 7 days / Last 30 days / Custom
 *   2. Payment mode — COD / Prepaid (multi-select)
 *   3. Labels       — quick "+ Create Label" shortcut (opens the
 *                     existing LabelPickerSheet in create mode).
 *
 * Actions: "Clear all" (resets all three filter groups) + "Apply"
 * (just closes — every filter is applied optimistically as the user
 * taps a chip, matching the existing UX pattern on the Shipments
 * screen).
 */
import React from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, ScrollView, Pressable,
} from "react-native";
import PhIcon from "./PhIcon";
import { colors } from "../lib/theme";

type DateFilterKey = "all" | "today" | "week" | "month" | "custom";

type Props = {
  visible: boolean;
  onClose: () => void;
  // Date range
  dateFilter: DateFilterKey;
  setDateFilter: (v: DateFilterKey) => void;
  customFrom: Date | null;
  customTo: Date | null;
  onOpenCustomDate: () => void;
  // Payment
  paymentFilter: Set<string>;
  setPaymentFilter: (v: Set<string>) => void;
  // Create Label shortcut
  onCreateLabel: () => void;
  // Clear-all counter (how many groups have active filters)
  onClearAll: () => void;
};

const DATE_CHIPS: { key: DateFilterKey; label: string }[] = [
  { key: "all",   label: "All dates" },
  { key: "today", label: "Last 24h" },
  { key: "week",  label: "Last 7 days" },
  { key: "month", label: "Last 30 days" },
];

const PAYMENT_CHIPS = [
  { key: "COD",     label: "COD",     icon: "cash" as const },
  { key: "Prepaid", label: "Prepaid", icon: "card" as const },
];

export default function ShipmentsFilterSheet({
  visible, onClose,
  dateFilter, setDateFilter,
  customFrom, customTo, onOpenCustomDate,
  paymentFilter, setPaymentFilter,
  onCreateLabel, onClearAll,
}: Props) {
  const togglePayment = (mode: string) => {
    const next = new Set(paymentFilter);
    if (next.has(mode)) next.delete(mode);
    else next.add(mode);
    setPaymentFilter(next);
  };

  const activeCount =
    (dateFilter !== "all" ? 1 : 0) +
    (paymentFilter.size > 0 ? 1 : 0);

  const customLabel = (() => {
    if (dateFilter !== "custom") return "Custom";
    const fmt = (d: Date | null) =>
      d ? `${d.getDate()}/${d.getMonth() + 1}` : "…";
    return `${fmt(customFrom)} – ${fmt(customTo)}`;
  })();

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
        {/* Header */}
        <View style={styles.header}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <PhIcon name="filter" size={18} color={colors.text} />
            <Text style={styles.title}>Filters</Text>
            {activeCount > 0 ? (
              <View style={styles.badge}>
                <Text style={styles.badgeTxt}>{activeCount}</Text>
              </View>
            ) : null}
          </View>
          <TouchableOpacity
            onPress={onClose}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            testID="filter-sheet-close"
          >
            <PhIcon name="close" size={22} color={colors.text} />
          </TouchableOpacity>
        </View>

        <ScrollView style={{ maxHeight: 520 }} contentContainerStyle={{ paddingBottom: 8 }}>
          {/* ── Date range section ─────────────────────────────── */}
          <Text style={styles.sectionTitle}>Date range</Text>
          <View style={styles.chipWrap}>
            {DATE_CHIPS.map((c) => {
              const active = dateFilter === c.key;
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-date-${c.key}`}
                  onPress={() => setDateFilter(c.key)}
                  style={[
                    styles.chip,
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <Text
                    numberOfLines={1}
                    allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
            {/* Custom date pill */}
            <TouchableOpacity
              testID="fs-date-custom"
              onPress={() => {
                setDateFilter("custom");
                onOpenCustomDate();
              }}
              style={[
                styles.chip,
                { flexDirection: "row", gap: 6, alignItems: "center" },
                dateFilter === "custom" && { backgroundColor: colors.primary, borderColor: colors.primary },
              ]}
            >
              <PhIcon
                name="calendar"
                size={14}
                color={dateFilter === "custom" ? "#fff" : colors.primary}
              />
              <Text
                numberOfLines={1}
                allowFontScaling={false}
                style={[styles.chipTxt, { color: dateFilter === "custom" ? "#fff" : colors.primary }]}
              >
                {customLabel}
              </Text>
            </TouchableOpacity>
          </View>

          {/* ── Payment mode section ───────────────────────────── */}
          <Text style={styles.sectionTitle}>Payment mode</Text>
          <View style={styles.chipWrap}>
            {PAYMENT_CHIPS.map((c) => {
              const active = paymentFilter.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-pay-${c.key.toLowerCase()}`}
                  onPress={() => togglePayment(c.key)}
                  style={[
                    styles.chip,
                    { flexDirection: "row", gap: 6, alignItems: "center" },
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <PhIcon
                    name={c.icon}
                    size={14}
                    color={active ? "#fff" : colors.primary}
                  />
                  <Text
                    numberOfLines={1}
                    allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                  {active ? (
                    <PhIcon name="checkmark" size={14} color="#fff" />
                  ) : null}
                </TouchableOpacity>
              );
            })}
          </View>

          {/* ── Labels section ─────────────────────────────────── */}
          <Text style={styles.sectionTitle}>Labels</Text>
          <TouchableOpacity
            testID="fs-create-label"
            onPress={() => {
              onClose();
              // Small delay so the create-label sheet doesn't fight
              // the closing animation of this bottom sheet.
              setTimeout(onCreateLabel, 160);
            }}
            style={styles.createLabelRow}
          >
            <View style={styles.createIconBubble}>
              <PhIcon name="add" size={18} color={colors.primary} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.createLabelTitle}>Create new label</Text>
              <Text style={styles.createLabelSub}>
                Add a coloured tag to organise your shipments
              </Text>
            </View>
            <PhIcon name="chevron-forward" size={18} color="#94A3B8" />
          </TouchableOpacity>

          <Text style={styles.helper}>
            Tip: To filter by a specific label, use the "All Labels" chip
            in the Quick Filter row above.
          </Text>
        </ScrollView>

        {/* Footer actions */}
        <View style={styles.footer}>
          <TouchableOpacity
            testID="fs-clear-all"
            onPress={onClearAll}
            style={[styles.btn, styles.btnGhost]}
          >
            <Text style={[styles.btnTxt, { color: colors.text }]}>Clear all</Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="fs-apply"
            onPress={onClose}
            style={[styles.btn, styles.btnPrimary]}
          >
            <Text style={[styles.btnTxt, { color: "#fff" }]}>Apply</Text>
          </TouchableOpacity>
        </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: "flex-end" },
  backdrop: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.35)",
  },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 20,
  },
  header: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingVertical: 8, marginBottom: 6,
  },
  title: { fontSize: 16, fontWeight: "800", color: colors.text },
  badge: {
    backgroundColor: colors.primary,
    minWidth: 20, height: 20, borderRadius: 10, alignItems: "center", justifyContent: "center",
    paddingHorizontal: 6,
  },
  badgeTxt: { color: "#fff", fontSize: 11, fontWeight: "800" },
  sectionTitle: {
    fontSize: 13, fontWeight: "800", color: colors.text,
    marginTop: 14, marginBottom: 8, letterSpacing: 0.2,
  },
  chipWrap: {
    flexDirection: "row", flexWrap: "wrap", gap: 8,
  },
  chip: {
    borderWidth: 1, borderColor: colors.primary,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999,
    backgroundColor: "#fff",
  },
  chipTxt: { fontSize: 13, fontWeight: "700" },
  createLabelRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 12, paddingHorizontal: 12,
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 12,
    backgroundColor: "#F8FAFC",
  },
  createIconBubble: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: "center", justifyContent: "center",
    backgroundColor: `${colors.primary}18`,
  },
  createLabelTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  createLabelSub: { fontSize: 12, color: "#64748B", marginTop: 2 },
  helper: {
    fontSize: 12, color: "#94A3B8", marginTop: 10, lineHeight: 16,
  },
  footer: {
    flexDirection: "row", gap: 10, marginTop: 12, paddingTop: 10,
    borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  btn: {
    flex: 1, height: 44, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  btnGhost: { backgroundColor: "#F1F5F9" },
  btnPrimary: { backgroundColor: colors.primary },
  btnTxt: { fontSize: 14, fontWeight: "800" },
});
