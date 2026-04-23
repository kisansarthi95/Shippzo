import React, { useCallback, useMemo, useState, useEffect } from "react";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  FlatList, RefreshControl, Linking, Alert, Platform, Modal,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { useFocusEffect, useRouter, useLocalSearchParams } from "expo-router";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Api, Shipment, Settings, Courier } from "../../lib/api";
import { buildCopyText, buildWhatsAppText, cleanPhone } from "../../lib/format";
import { buildLabelHtml } from "../../lib/label";
import { colors } from "../../lib/theme";

type StatusFilter = "All" | "Pending" | "Delivered" | "Cancelled";
type DateFilter = "all" | "today" | "week" | "month" | "custom";

export default function Shipments() {
  const router = useRouter();
  const params = useLocalSearchParams<{ status?: string; select?: string }>();
  const [items, setItems] = useState<Shipment[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("All");
  const [dateFilter, setDateFilter] = useState<DateFilter>("all");
  const [customFrom, setCustomFrom] = useState<Date | null>(null);
  const [customTo, setCustomTo] = useState<Date | null>(null);
  const [showDateModal, setShowDateModal] = useState(false);
  const [pickerField, setPickerField] = useState<"from" | "to" | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkPerPage, setBulkPerPage] = useState<1 | 2 | 4>(4);
  const [refreshing, setRefreshing] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);

  const load = useCallback(async () => {
    const q: any = { search: search || undefined };
    if (status !== "All") q.status = status;
    try {
      const [list, s, cs] = await Promise.all([
        Api.listShipments(q), Api.getSettings(), Api.listCouriers(),
      ]);
      setItems(list);
      setSettings(s);
      setCouriers(cs);
    } catch (e: any) {
      // silently ignore transient failures so no global toast
      console.log("shipments load error:", e?.message || e);
    } finally {
      setRefreshing(false);
    }
  }, [search, status]);

  useFocusEffect(useCallback(() => { load().catch(() => {}); }, [load]));

  // Handle deep-link params from Dashboard quick actions.
  React.useEffect(() => {
    const st = String(params.status || "");
    if (st === "Pending" || st === "Delivered" || st === "Cancelled" || st === "All") {
      setStatus(st as StatusFilter);
    }
    if (params.select === "1") {
      setSelectMode(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.status, params.select]);

  const findCourier = (s: Shipment) =>
    couriers.find((c) => c.id === s.courier_id) || null;

  const dateFilteredItems = useMemo(() => {
    if (dateFilter === "all") return items;
    if (dateFilter === "custom") {
      if (!customFrom && !customTo) return items;
      const from = customFrom ? new Date(customFrom.getFullYear(), customFrom.getMonth(), customFrom.getDate()).getTime() : 0;
      const to = customTo ? new Date(customTo.getFullYear(), customTo.getMonth(), customTo.getDate(), 23, 59, 59, 999).getTime() : Number.MAX_SAFE_INTEGER;
      return items.filter((s) => {
        const t = Date.parse(s.created_at || "");
        return !isNaN(t) && t >= from && t <= to;
      });
    }
    const now = Date.now();
    const cutoff =
      dateFilter === "today"
        ? now - 24 * 60 * 60 * 1000
        : dateFilter === "week"
        ? now - 7 * 24 * 60 * 60 * 1000
        : now - 30 * 24 * 60 * 60 * 1000;
    return items.filter((s) => {
      const t = Date.parse(s.created_at || "");
      return !isNaN(t) && t >= cutoff;
    });
  }, [items, dateFilter, customFrom, customTo]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedIds(new Set(dateFilteredItems.map((i) => i.id)));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
    setSelectMode(false);
  };

  const bulkPrint = async () => {
    if (selectedIds.size === 0 || !settings) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    try {
      const shipments = await Api.bulkFetch(Array.from(selectedIds));
      if (shipments.length === 0) return;
      const html = buildLabelHtml(shipments, settings.sender, {
        perPage: bulkPerPage,
        showSenderContact: settings.sender.show_contact,
        brand: settings.brand,
        preferLogo: (settings as any).prefer_logo !== false,
        logoShape: (settings as any).logo_shape === "wide" ? "wide" : "square",
        couriers,
        labelFields: (settings as any).label_fields,
        shipmentTagline: (settings as any).shipment_tagline,
        customFields: (settings as any).custom_fields,
      });
      await Print.printAsync({ html });
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const bulkPreviewPdf = async () => {
    if (selectedIds.size === 0 || !settings) {
      Alert.alert("Select shipments", "Tap shipments to select first.");
      return;
    }
    try {
      const shipments = await Api.bulkFetch(Array.from(selectedIds));
      const html = buildLabelHtml(shipments, settings.sender, {
        perPage: bulkPerPage,
        showSenderContact: settings.sender.show_contact,
        brand: settings.brand,
        preferLogo: (settings as any).prefer_logo !== false,
        logoShape: (settings as any).logo_shape === "wide" ? "wide" : "square",
        couriers,
        labelFields: (settings as any).label_fields,
        shipmentTagline: (settings as any).shipment_tagline,
        customFields: (settings as any).custom_fields,
      });
      const { uri } = await Print.printToFileAsync({ html });
      if (Platform.OS === "web" && typeof window !== "undefined") {
        window.open(uri, "_blank");
      } else if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, { mimeType: "application/pdf" });
      }
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const toggleDelivered = async (s: Shipment) => {
    const newStatus = s.status === "Delivered" ? "Pending" : "Delivered";
    await Api.updateShipment(s.id, { status: newStatus });
    load();
  };

  const remove = (s: Shipment) => {
    Alert.alert("Delete", `Delete ${s.tracking_id}?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          await Api.deleteShipment(s.id);
          load();
        },
      },
    ]);
  };

  const sendWhatsApp = (s: Shipment) => {
    if (!s.customer_phone) {
      Alert.alert("No phone", "Customer phone not set.");
      return;
    }
    const msg = buildWhatsAppText(s, settings, findCourier(s));
    const phone = cleanPhone(s.customer_phone);
    Linking.openURL(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`);
  };

  const copyAll = async (s: Shipment) => {
    const text = buildCopyText(s, settings, findCourier(s));
    await Clipboard.setStringAsync(text);
    Alert.alert("Copied", "Tracking details copied to clipboard.");
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Shipments</Text>
        <View style={{ flexDirection: "row", gap: 8 }}>
          <TouchableOpacity
            testID="bulk-mode-toggle" style={[styles.iconBtn, selectMode && { backgroundColor: colors.primary, borderColor: colors.primary }]}
            onPress={() => {
              if (selectMode) clearSelection();
              else setSelectMode(true);
            }}
          >
            <Ionicons
              name={selectMode ? "close" : "checkbox-outline"}
              size={20}
              color={selectMode ? "#fff" : colors.text}
            />
          </TouchableOpacity>
          <TouchableOpacity
            testID="export-csv-btn" style={styles.iconBtn}
            onPress={() => Linking.openURL(Api.csvUrl())}
          >
            <Ionicons name="download-outline" size={20} color={colors.text} />
          </TouchableOpacity>
        </View>
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={18} color={colors.textMuted} />
        <TextInput
          testID="search-input"
          placeholder="Search tracking, name, city, order..."
          placeholderTextColor="#9CA3AF"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={load}
          style={styles.searchInput}
          returnKeyType="search"
        />
      </View>

      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.filterRow}>
          {(["All", "Pending", "Delivered", "Cancelled"] as StatusFilter[]).map((f) => {
            const active = status === f;
            return (
              <TouchableOpacity
                key={f} testID={`filter-${f.toLowerCase()}`}
                style={[styles.filterPill, active && styles.filterPillActive]}
                onPress={() => setStatus(f)}
              >
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : colors.text }]}
                >{f}</Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      <View style={styles.filterRowWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={[styles.filterRow, { paddingTop: 0 }]}>
          {([
            { key: "all", label: "All dates" },
            { key: "today", label: "Last 24h" },
            { key: "week", label: "Last 7 days" },
            { key: "month", label: "Last 30 days" },
          ] as const).map((f) => {
            const active = dateFilter === f.key;
            return (
              <TouchableOpacity
                key={f.key}
                testID={`datefilter-${f.key}`}
                onPress={() => setDateFilter(f.key)}
                style={[
                  styles.filterPill,
                  { borderColor: colors.primary },
                  active && { backgroundColor: colors.primary, borderColor: colors.primary },
                ]}
              >
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : colors.primary }]}
                >
                  {f.label}
                </Text>
              </TouchableOpacity>
            );
          })}
          {/* Custom date range pill */}
          {(() => {
            const active = dateFilter === "custom";
            const rangeLabel = (() => {
              if (!active) return "Custom";
              const fmt = (d: Date | null) =>
                d ? `${d.getDate()}/${d.getMonth() + 1}` : "…";
              return `${fmt(customFrom)} – ${fmt(customTo)}`;
            })();
            return (
              <TouchableOpacity
                testID="datefilter-custom"
                onPress={() => {
                  setDateFilter("custom");
                  setShowDateModal(true);
                }}
                style={[
                  styles.filterPill,
                  { borderColor: colors.primary, flexDirection: "row", gap: 4 },
                  active && { backgroundColor: colors.primary, borderColor: colors.primary },
                ]}
              >
                <Ionicons
                  name="calendar-outline"
                  size={13}
                  color={active ? "#fff" : colors.primary}
                />
                <Text
                  numberOfLines={1}
                  allowFontScaling={false}
                  style={[styles.filterText, { color: active ? "#fff" : colors.primary }]}
                >
                  {rangeLabel}
                </Text>
              </TouchableOpacity>
            );
          })()}
        </ScrollView>
      </View>

      {selectMode && (
        <View style={styles.bulkBar} testID="bulk-bar">
          <Text style={styles.bulkCount}>
            {selectedIds.size} selected
          </Text>
          <TouchableOpacity testID="bulk-select-all" onPress={selectAllVisible}>
            <Text style={styles.bulkLink}>Select all</Text>
          </TouchableOpacity>
          <View style={{ flex: 1 }} />
          <View style={{ flexDirection: "row", gap: 4 }}>
            {[1, 2, 4].map((n) => (
              <TouchableOpacity
                key={n}
                testID={`bulk-layout-${n}`}
                onPress={() => setBulkPerPage(n as 1 | 2 | 4)}
                style={[
                  styles.bulkLayout,
                  bulkPerPage === n && { backgroundColor: colors.secondary, borderColor: colors.secondary },
                ]}
              >
                <Text style={[
                  styles.bulkLayoutText,
                  bulkPerPage === n && { color: "#fff" },
                ]}>{n}/pg</Text>
              </TouchableOpacity>
            ))}
          </View>
          <TouchableOpacity testID="bulk-preview-btn" style={styles.bulkAction} onPress={bulkPreviewPdf}>
            <Ionicons name="eye-outline" size={16} color={colors.text} />
          </TouchableOpacity>
          <TouchableOpacity testID="bulk-print-btn" style={[styles.bulkAction, { backgroundColor: colors.primary }]} onPress={bulkPrint}>
            <Ionicons name="print" size={16} color="#fff" />
          </TouchableOpacity>
        </View>
      )}

      <FlatList
        testID="shipments-list"
        data={dateFilteredItems}
        keyExtractor={(i) => i.id}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => {
            setRefreshing(true); load();
          }} />
        }
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        ListEmptyComponent={
          <View style={styles.empty} testID="empty-shipments">
            <Ionicons name="cube-outline" size={48} color="#9CA3AF" />
            <Text style={styles.emptyText}>કોઈ shipment નથી મળી.</Text>
            <TouchableOpacity
              testID="empty-new-shipment" style={styles.primaryBtn}
              onPress={() => router.push("/(tabs)/add")}
            >
              <Text style={styles.primaryBtnText}>+ New Shipment</Text>
            </TouchableOpacity>
          </View>
        }
        renderItem={({ item }) => {
          const isSelected = selectedIds.has(item.id);
          return (
          <View
            style={[styles.card, selectMode && isSelected && { borderColor: colors.primary, borderWidth: 2 }]}
            testID={`shipment-${item.tracking_id}`}
          >
            <TouchableOpacity
              style={{ flex: 1 }}
              onPress={() => {
                if (selectMode) toggleSelect(item.id);
                else router.push(`/label/${item.id}`);
              }}
              onLongPress={() => {
                setSelectMode(true);
                toggleSelect(item.id);
              }}
            >
              <View style={styles.row}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flex: 1 }}>
                  {selectMode && (
                    <Ionicons
                      name={isSelected ? "checkbox" : "square-outline"}
                      size={22}
                      color={isSelected ? colors.primary : colors.textMuted}
                    />
                  )}
                  <Text style={styles.track}>{item.tracking_id}</Text>
                </View>
                <StatusChip status={item.status} />
              </View>
              <Text style={styles.name}>{item.customer_name}</Text>
              {!!item.order_id && (
                <Text style={styles.order}>Order #{item.order_id}</Text>
              )}
              <Text style={styles.sub} numberOfLines={1}>
                {item.courier_name} · {item.city || "—"} · {
                  item.payment_mode === "COD"
                    ? `COD ₹${item.amount || item.cod_amount}`
                    : `Prepaid ₹${item.amount}`
                }
              </Text>
              {item.items && item.items.length > 0 && (
                <Text style={styles.items} numberOfLines={1}>
                  📦 {item.items.join(", ")}
                </Text>
              )}
            </TouchableOpacity>
            {!selectMode && (
              <View style={styles.actions}>
                <ActionBtn
                  icon={item.status === "Delivered" ? "checkmark-done-circle" : "checkmark-circle-outline"}
                  color={item.status === "Delivered" ? colors.successText : colors.textMuted}
                  onPress={() => toggleDelivered(item)}
                  testID={`toggle-delivered-${item.tracking_id}`}
                />
                <ActionBtn
                  icon="copy-outline" color={colors.text}
                  onPress={() => copyAll(item)}
                  testID={`copy-all-${item.tracking_id}`}
                />
                <ActionBtn
                  icon="logo-whatsapp" color="#25D366"
                  onPress={() => sendWhatsApp(item)}
                  testID={`whatsapp-${item.tracking_id}`}
                />
                <ActionBtn
                  icon="print-outline" color={colors.text}
                  onPress={() => router.push(`/label/${item.id}`)}
                  testID={`print-${item.tracking_id}`}
                />
                <ActionBtn
                  icon="trash-outline" color={colors.dangerText}
                  onPress={() => remove(item)}
                  testID={`delete-${item.tracking_id}`}
                />
              </View>
            )}
          </View>
          );
        }}
      />

      {/* Custom date range modal */}
      <Modal
        visible={showDateModal}
        transparent
        animationType="fade"
        onRequestClose={() => setShowDateModal(false)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.dateModalCard}>
            <View style={styles.dateModalHdr}>
              <Ionicons name="calendar" size={18} color={colors.primary} />
              <Text style={styles.dateModalTitle}>Custom Date Range</Text>
              <TouchableOpacity onPress={() => setShowDateModal(false)} hitSlop={10}>
                <Ionicons name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <Text style={styles.dateHint}>Select From &amp; To dates to filter shipments.</Text>

            <TouchableOpacity
              testID="picker-from"
              style={styles.dateField}
              onPress={() => setPickerField("from")}
            >
              <Text style={styles.dateFieldLabel}>From</Text>
              <Text style={styles.dateFieldValue}>
                {customFrom
                  ? customFrom.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
                  : "Tap to pick date"}
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="picker-to"
              style={styles.dateField}
              onPress={() => setPickerField("to")}
            >
              <Text style={styles.dateFieldLabel}>To</Text>
              <Text style={styles.dateFieldValue}>
                {customTo
                  ? customTo.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" })
                  : "Tap to pick date"}
              </Text>
            </TouchableOpacity>

            {pickerField && Platform.OS !== "web" && (
              <DateTimePicker
                value={(pickerField === "from" ? customFrom : customTo) || new Date()}
                mode="date"
                display={Platform.OS === "ios" ? "inline" : "default"}
                maximumDate={new Date()}
                onChange={(event: any, selected?: Date) => {
                  // On Android native dialog, it dismisses automatically.
                  if (Platform.OS === "android") setPickerField(null);
                  if (event?.type === "dismissed") return;
                  if (!selected) return;
                  if (pickerField === "from") setCustomFrom(selected);
                  else setCustomTo(selected);
                }}
              />
            )}

            <View style={styles.dateModalActions}>
              <TouchableOpacity
                style={styles.dateClearBtn}
                onPress={() => {
                  setCustomFrom(null);
                  setCustomTo(null);
                }}
              >
                <Text style={styles.dateClearText}>Clear</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="apply-date-range"
                style={styles.dateApplyBtn}
                onPress={() => {
                  setShowDateModal(false);
                  setPickerField(null);
                }}
              >
                <Text style={styles.dateApplyText}>Apply</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function ActionBtn({
  icon, color, onPress, testID,
}: {
  icon: keyof typeof import("@expo/vector-icons").Ionicons.glyphMap;
  color: string;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <TouchableOpacity testID={testID} onPress={onPress} style={styles.actionBtn}>
      <Ionicons name={icon} size={18} color={color} />
    </TouchableOpacity>
  );
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    Delivered: { bg: colors.successBg, fg: colors.successText },
    Pending: { bg: colors.warningBg, fg: colors.warningText },
    Cancelled: { bg: colors.dangerBg, fg: colors.dangerText },
  };
  const m = map[status] || map.Pending;
  return (
    <View style={[styles.chip, { backgroundColor: m.bg }]}>
      <Text style={[styles.chipText, { color: m.fg }]}>{status.toUpperCase()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingTop: 10, paddingBottom: 8,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  iconBtn: {
    width: 40, height: 40, borderRadius: 10, borderWidth: 2,
    borderColor: "#E5E7EB", backgroundColor: "#fff",
    justifyContent: "center", alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16, marginTop: 4, flexDirection: "row",
    alignItems: "center", gap: 8, height: 46,
    backgroundColor: colors.surface, borderWidth: 2,
    borderColor: "#E5E7EB", borderRadius: 10, paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRowWrap: { flexGrow: 0, flexShrink: 0 },
  filterRow: { paddingHorizontal: 16, paddingVertical: 12 },
  filterPill: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
    marginRight: 8,
    minWidth: 80,
    alignItems: "center",
    justifyContent: "center",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: { fontWeight: "700", fontSize: 13, color: colors.text },
  card: {
    backgroundColor: colors.surface, borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  track: {
    fontFamily: "Courier", fontWeight: "800", fontSize: 14,
    letterSpacing: 1.5, color: colors.text,
  },
  name: { marginTop: 6, fontSize: 15, fontWeight: "700", color: colors.text },
  order: { fontSize: 11, color: colors.primary, fontWeight: "700", marginTop: 2 },
  sub: { marginTop: 3, color: colors.textMuted, fontSize: 12 },
  items: { marginTop: 3, color: colors.text, fontSize: 12, fontWeight: "600" },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  actions: {
    flexDirection: "row", justifyContent: "flex-end", gap: 6,
    marginTop: 12, borderTopWidth: 1, borderTopColor: "#F1F1F1", paddingTop: 10,
  },
  actionBtn: {
    width: 36, height: 36, borderRadius: 8, backgroundColor: "#F9FAFB",
    borderWidth: 1, borderColor: "#E5E7EB",
    justifyContent: "center", alignItems: "center",
  },
  empty: {
    alignItems: "center", padding: 30, backgroundColor: colors.surface,
    borderRadius: 12, borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyText: { marginTop: 12, color: colors.textMuted, textAlign: "center" },
  primaryBtn: {
    marginTop: 16, backgroundColor: colors.primary,
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },
  bulkBar: {
    paddingVertical: 10,
    backgroundColor: "#fff",
    borderTopWidth: 1,
    borderBottomWidth: 2,
    borderTopColor: "#E5E7EB",
    borderBottomColor: colors.primary,
    flexGrow: 0,
  },
  bulkCount: { fontWeight: "800", color: colors.text, fontSize: 13, marginRight: 4 },
  bulkLink: { color: colors.primary, fontWeight: "700", fontSize: 12, marginRight: 8 },
  bulkLayout: {
    paddingHorizontal: 8,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 6,
  },
  bulkLayoutText: { fontSize: 11, fontWeight: "700", color: colors.text },
  bulkAction: {
    width: 36,
    height: 36,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    justifyContent: "center",
    alignItems: "center",
  },

  /* ----- Date Range Modal ----- */
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    alignItems: "center",
    padding: 16,
  },
  dateModalCard: {
    width: "100%",
    maxWidth: 400,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 18,
  },
  dateModalHdr: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  dateModalTitle: {
    flex: 1,
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  dateHint: { fontSize: 12, color: colors.textMuted, marginBottom: 12 },
  dateField: {
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 10,
  },
  dateFieldLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textMuted,
    marginBottom: 2,
  },
  dateFieldValue: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  dateModalActions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
  },
  dateClearBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    alignItems: "center",
    backgroundColor: "#fff",
  },
  dateClearText: { color: colors.text, fontWeight: "700" },
  dateApplyBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: colors.primary,
    alignItems: "center",
  },
  dateApplyText: { color: "#fff", fontWeight: "800" },
});
