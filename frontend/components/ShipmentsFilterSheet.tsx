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
 * Phase F6.3 (2026-06) — extended with Shipment Import filters:
 *   4. Import Status      — All / Imported / Not Imported
 *   5. Import Type        — Booking / Delivery / COD Payment (multi)
 *   6. Booking / Delivery / COD sub-filters
 *   7. Validation alerts  — Weight / Payment / COD amount mismatch
 *   8. Import Batch picker
 *   9. Payment Batch picker (COD settlement grouping)
 *
 * Actions: "Clear all" (resets all filter groups) + "Apply"
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

export type ImportStatusKey = "all" | "imported" | "not_imported";
export type ImportTypeKey = "booking" | "delivery" | "cod_payment";
export type ValidationKey = "weight" | "payment_mode" | "amount";
export type CodPaymentKey = "received" | "pending" | "amount_mismatch" | "returned";
export type DeliveryKey = "imported" | "pending" | "confirmed";
export type BookingKey = "imported" | "pending";
// Phase F7.1 (Jun-2026) — replaced the old "Complaint Created / No Complaint"
// pair with the official India Post CBS complaint status axis. Semantics
// match /app/backend/routers/complaints.py `_VALID_STATUSES` exactly.
export type ComplaintKey =
  | "Open"
  | "In Progress"
  | "Resolved"
  | "Closed";

export type BatchRef = { id: string; label: string; sub?: string };

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
  // Clear-all
  onClearAll: () => void;

  // Phase F6.3 — Import filters
  importStatus: ImportStatusKey;
  setImportStatus: (v: ImportStatusKey) => void;

  importTypes: Set<ImportTypeKey>;
  setImportTypes: (v: Set<ImportTypeKey>) => void;

  bookingFilter: Set<BookingKey>;
  setBookingFilter: (v: Set<BookingKey>) => void;

  deliveryFilter: Set<DeliveryKey>;
  setDeliveryFilter: (v: Set<DeliveryKey>) => void;

  codPaymentFilter: Set<CodPaymentKey>;
  setCodPaymentFilter: (v: Set<CodPaymentKey>) => void;

  validationFilter: Set<ValidationKey>;
  setValidationFilter: (v: Set<ValidationKey>) => void;

  // Import Batch (single-select) — picker opens a sub-sheet.
  importBatch: BatchRef | null;
  onOpenImportBatchPicker: () => void;

  // Payment Batch (single-select)
  paymentBatch: BatchRef | null;
  onOpenPaymentBatchPicker: () => void;

  // Phase F7.0 — India Post Complaint filter (multi-select).
  //   "created"     → shipments with `complaint_created === true`
  //   "not_created" → shipments without a complaint record
  complaintFilter: Set<ComplaintKey>;
  setComplaintFilter: (v: Set<ComplaintKey>) => void;
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

const IMPORT_STATUS_CHIPS: { key: ImportStatusKey; label: string }[] = [
  { key: "all",          label: "All Shipments" },
  { key: "imported",     label: "Imported" },
  { key: "not_imported", label: "Not Imported" },
];
const IMPORT_TYPE_CHIPS: { key: ImportTypeKey; label: string; icon: any }[] = [
  { key: "booking",     label: "Booking Imported",  icon: "cube-outline" },
  { key: "delivery",    label: "Delivery Imported", icon: "checkmark-done-outline" },
  { key: "cod_payment", label: "COD Payment Imported", icon: "cash-outline" },
];
const BOOKING_CHIPS: { key: BookingKey; label: string }[] = [
  { key: "imported", label: "Booking Imported" },
  { key: "pending",  label: "Booking Pending" },
];
const DELIVERY_CHIPS: { key: DeliveryKey; label: string }[] = [
  { key: "imported",  label: "Delivery Imported" },
  { key: "pending",   label: "Delivery Pending" },
  { key: "confirmed", label: "Delivery Confirmed" },
];
const COD_CHIPS: { key: CodPaymentKey; label: string; tone?: "green" | "yellow" | "red" }[] = [
  // Phase F10 — color-coded chips match the amount pill on the
  // shipment card so operators see the same green/yellow/red across
  // the list and the filter sheet.
  { key: "received",         label: "COD Payment Received", tone: "green"  },
  { key: "pending",          label: "COD Payment Pending",  tone: "yellow" },
  { key: "amount_mismatch",  label: "COD Amount Mismatch"                  },
  { key: "returned",         label: "COD Payment Returned", tone: "red"    },
];
const VALIDATION_CHIPS: { key: ValidationKey; label: string }[] = [
  { key: "weight",       label: "Weight Mismatch" },
  { key: "payment_mode", label: "Payment Type Mismatch" },
  { key: "amount",       label: "COD Amount Mismatch" },
];
// Phase F7.1 — India Post Complaint STATUS filter chips. Selecting
// any of these ALSO acts as the trigger for the India Post bulk
// complaint export on the Shipments screen (see the export intercept
// in /app/frontend/app/(tabs)/shipments.tsx).
const COMPLAINT_CHIPS: { key: ComplaintKey; label: string; icon: any; color: string }[] = [
  { key: "Open",        label: "Open",         icon: "alert-circle-outline",     color: "#DC2626" },
  { key: "In Progress", label: "In Progress",  icon: "hourglass-outline",        color: "#B45309" },
  { key: "Resolved",    label: "Resolved",     icon: "checkmark-done-outline",   color: "#059669" },
  { key: "Closed",      label: "Closed",       icon: "lock-closed-outline",      color: "#475569" },
];

export default function ShipmentsFilterSheet({
  visible, onClose,
  dateFilter, setDateFilter,
  customFrom, customTo, onOpenCustomDate,
  paymentFilter, setPaymentFilter,
  onClearAll,
  importStatus, setImportStatus,
  importTypes, setImportTypes,
  bookingFilter, setBookingFilter,
  deliveryFilter, setDeliveryFilter,
  codPaymentFilter, setCodPaymentFilter,
  validationFilter, setValidationFilter,
  importBatch, onOpenImportBatchPicker,
  paymentBatch, onOpenPaymentBatchPicker,
  complaintFilter, setComplaintFilter,
}: Props) {
  const togglePayment = (mode: string) => {
    const next = new Set(paymentFilter);
    if (next.has(mode)) next.delete(mode);
    else next.add(mode);
    setPaymentFilter(next);
  };
  const toggleSet = <T,>(setState: (v: Set<T>) => void, current: Set<T>, k: T) => {
    const next = new Set(current);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setState(next);
  };

  const activeCount =
    (dateFilter !== "all" ? 1 : 0) +
    (paymentFilter.size > 0 ? 1 : 0) +
    (importStatus !== "all" ? 1 : 0) +
    (importTypes.size > 0 ? 1 : 0) +
    (bookingFilter.size > 0 ? 1 : 0) +
    (deliveryFilter.size > 0 ? 1 : 0) +
    (codPaymentFilter.size > 0 ? 1 : 0) +
    (validationFilter.size > 0 ? 1 : 0) +
    (importBatch ? 1 : 0) +
    (paymentBatch ? 1 : 0) +
    (complaintFilter.size > 0 ? 1 : 0);

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

          {/* ─────────────────────────────────────────────────────
              Phase F6.3 — Shipment Import filters
              ─────────────────────────────────────────────────── */}

          <Text style={styles.sectionTitle}>Import Status</Text>
          <View style={styles.chipWrap}>
            {IMPORT_STATUS_CHIPS.map((c) => {
              const active = importStatus === c.key;
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-imp-status-${c.key}`}
                  onPress={() => setImportStatus(c.key)}
                  style={[
                    styles.chip,
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.sectionTitle}>Import Type</Text>
          <View style={styles.chipWrap}>
            {IMPORT_TYPE_CHIPS.map((c) => {
              const active = importTypes.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-imp-type-${c.key}`}
                  onPress={() => toggleSet(setImportTypes, importTypes, c.key)}
                  style={[
                    styles.chip,
                    { flexDirection: "row", gap: 5, alignItems: "center" },
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <PhIcon name={c.icon} size={12} color={active ? "#fff" : colors.primary} />
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.sectionTitle}>Booking</Text>
          <View style={styles.chipWrap}>
            {BOOKING_CHIPS.map((c) => {
              const active = bookingFilter.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-book-${c.key}`}
                  onPress={() => toggleSet(setBookingFilter, bookingFilter, c.key)}
                  style={[
                    styles.chip,
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.sectionTitle}>Delivery</Text>
          <View style={styles.chipWrap}>
            {DELIVERY_CHIPS.map((c) => {
              const active = deliveryFilter.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-del-${c.key}`}
                  onPress={() => toggleSet(setDeliveryFilter, deliveryFilter, c.key)}
                  style={[
                    styles.chip,
                    active && { backgroundColor: colors.primary, borderColor: colors.primary },
                  ]}
                >
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : colors.primary }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.sectionTitle}>COD Payment</Text>
          <View style={styles.chipWrap}>
            {COD_CHIPS.map((c) => {
              const active = codPaymentFilter.has(c.key);
              // Phase F10 — tonal palette matches the amount pill on
              // each shipment card. Inactive chips carry a soft-tinted
              // background so the color code stays discoverable even
              // when the operator hasn't tapped anything yet.
              const tone = c.tone;
              const inactiveBg =
                tone === "green"  ? "#DCFCE7" :
                tone === "yellow" ? "#FEF9C3" :
                tone === "red"    ? "#FEE2E2" : undefined;
              const inactiveBorder =
                tone === "green"  ? "#86EFAC" :
                tone === "yellow" ? "#FDE68A" :
                tone === "red"    ? "#FCA5A5" : undefined;
              const inactiveTxt =
                tone === "green"  ? "#166534" :
                tone === "yellow" ? "#854D0E" :
                tone === "red"    ? "#991B1B" : colors.primary;
              const activeBg =
                tone === "green"  ? "#16A34A" :
                tone === "yellow" ? "#CA8A04" :
                tone === "red"    ? "#DC2626" : colors.primary;
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-cod-${c.key}`}
                  onPress={() => toggleSet(setCodPaymentFilter, codPaymentFilter, c.key)}
                  style={[
                    styles.chip,
                    !active && inactiveBg  ? { backgroundColor: inactiveBg }     : null,
                    !active && inactiveBorder ? { borderColor: inactiveBorder }  : null,
                    active && { backgroundColor: activeBg, borderColor: activeBg },
                  ]}
                >
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[
                      styles.chipTxt,
                      { color: active ? "#fff" : inactiveTxt },
                    ]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.sectionTitle}>Validation Alerts</Text>
          <View style={styles.chipWrap}>
            {VALIDATION_CHIPS.map((c) => {
              const active = validationFilter.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-val-${c.key}`}
                  onPress={() => toggleSet(setValidationFilter, validationFilter, c.key)}
                  style={[
                    styles.chip,
                    { flexDirection: "row", gap: 4, alignItems: "center" },
                    active && { backgroundColor: "#B45309", borderColor: "#B45309" },
                  ]}
                >
                  <PhIcon
                    name="warning" size={11}
                    color={active ? "#fff" : "#B45309"}
                  />
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : "#B45309" }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Import Batch drill-down */}
          <Text style={styles.sectionTitle}>Import Batch</Text>
          <TouchableOpacity
            testID="fs-import-batch-picker"
            style={styles.batchPickerBtn}
            onPress={onOpenImportBatchPicker}
          >
            <View style={{ flex: 1 }}>
              {importBatch ? (
                <>
                  <Text style={styles.batchPickerLbl}>{importBatch.label}</Text>
                  {importBatch.sub ? (
                    <Text style={styles.batchPickerSub}>{importBatch.sub}</Text>
                  ) : null}
                </>
              ) : (
                <Text style={styles.batchPickerPlaceholder}>
                  Any import batch (tap to pick)
                </Text>
              )}
            </View>
            <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
          </TouchableOpacity>

          {/* Payment Batch drill-down */}
          <Text style={styles.sectionTitle}>Payment Batch</Text>
          <TouchableOpacity
            testID="fs-payment-batch-picker"
            style={styles.batchPickerBtn}
            onPress={onOpenPaymentBatchPicker}
          >
            <View style={{ flex: 1 }}>
              {paymentBatch ? (
                <>
                  <Text style={styles.batchPickerLbl}>{paymentBatch.label}</Text>
                  {paymentBatch.sub ? (
                    <Text style={styles.batchPickerSub}>{paymentBatch.sub}</Text>
                  ) : null}
                </>
              ) : (
                <Text style={styles.batchPickerPlaceholder}>
                  Any payment batch (tap to pick)
                </Text>
              )}
            </View>
            <PhIcon name="chevron-forward" size={16} color="#94A3B8" />
          </TouchableOpacity>

          {/* ── Phase F7.1 — India Post Complaint Status ────────── */}
          <Text style={styles.sectionTitle}>India Post Complaint Status</Text>
          <View style={styles.chipWrap}>
            {COMPLAINT_CHIPS.map((c) => {
              const active = complaintFilter.has(c.key);
              return (
                <TouchableOpacity
                  key={c.key}
                  testID={`fs-complaint-${c.key.toLowerCase().replace(/\s+/g, "-")}`}
                  onPress={() =>
                    toggleSet(setComplaintFilter, complaintFilter, c.key)
                  }
                  style={[
                    styles.chip,
                    { flexDirection: "row", gap: 5, alignItems: "center", borderColor: c.color },
                    active && { backgroundColor: c.color, borderColor: c.color },
                  ]}
                >
                  <PhIcon
                    name={c.icon}
                    size={12}
                    color={active ? "#fff" : c.color}
                  />
                  <Text
                    numberOfLines={1} allowFontScaling={false}
                    style={[styles.chipTxt, { color: active ? "#fff" : c.color }]}
                  >
                    {c.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
          {/* Contextual hint — reinforces the export-trigger side-effect
              so operators aren't surprised when the Download button
              stops asking the format question. */}
          {complaintFilter.size > 0 ? (
            <Text style={styles.complaintHint}>
              Download Data will export the India Post complaint format for
              matching shipments.
            </Text>
          ) : null}
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
  // Phase F6.3 — Import Batch / Payment Batch pickers
  batchPickerBtn: {
    flexDirection: "row", alignItems: "center",
    paddingVertical: 12, paddingHorizontal: 12, gap: 8,
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 10,
    backgroundColor: "#F8FAFC",
  },
  batchPickerLbl:  { fontSize: 13, fontWeight: "700", color: colors.text },
  batchPickerSub:  { fontSize: 11, color: "#64748B", marginTop: 2 },
  batchPickerPlaceholder: { fontSize: 13, color: "#94A3B8", fontStyle: "italic" },
  complaintHint: {
    fontSize: 11, color: "#B45309", marginTop: 8, lineHeight: 15,
    fontStyle: "italic", fontWeight: "600",
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
