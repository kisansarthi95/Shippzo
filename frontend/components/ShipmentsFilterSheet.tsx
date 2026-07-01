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

type Suggestion = { display: string; count: number };

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
  // Phase C — Suggested product-name filters (most frequently
  // occurring items across the currently loaded shipments).
  suggestions: Suggestion[];
  onPickSuggestion: (term: string) => void;
  // Clear-all
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
  suggestions, onPickSuggestion, onClearAll,
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

          {/* ── Suggested Filters section (Phase C) ─────────────
              Shows the most frequently occurring product names
              (items[]) across your loaded shipments. Tap a chip and
              we drop that term straight into the search bar so it
              composes with every other filter. The old "Create new
              label" shortcut lives on the shipment card itself
              (Label icon → +New) — no need to duplicate it here. */}
          <Text style={styles.sectionTitle}>
            Suggested Filters{suggestions.length > 0 ? ` (${suggestions.length})` : ""}
          </Text>
          {suggestions.length === 0 ? (
            <Text style={styles.helper}>
              No frequently-used product names yet. Once the same
              product appears in 2+ shipments, it'll show up here as
              a one-tap filter.
            </Text>
          ) : (
            <View style={styles.chipWrap}>
              {suggestions.map((s) => (
                <TouchableOpacity
                  key={s.display}
                  testID={`fs-sugg-${s.display}`}
                  onPress={() => {
                    onPickSuggestion(s.display);
                    onClose();
                  }}
                  style={[
                    styles.chip,
                    { flexDirection: "row", gap: 6, alignItems: "center" },
                  ]}
                >
                  <PhIcon name="search" size={13} color={colors.primary} />
                  <Text
                    numberOfLines={1}
                    allowFontScaling={false}
                    style={[styles.chipTxt, { color: colors.primary }]}
                  >
                    {s.display}
                  </Text>
                  <View style={styles.countPill}>
                    <Text style={styles.countPillTxt}>{s.count}</Text>
                  </View>
                </TouchableOpacity>
              ))}
            </View>
          )}
          <Text style={styles.helper}>
            Tip: To create a new label, open any shipment's Label
            picker directly from its card.
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
  countPill: {
    minWidth: 20, height: 18, borderRadius: 9,
    paddingHorizontal: 6, marginLeft: 2,
    backgroundColor: `${colors.primary}22`,
    alignItems: "center", justifyContent: "center",
  },
  countPillTxt: {
    fontSize: 10, fontWeight: "800", color: colors.primary,
  },
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
