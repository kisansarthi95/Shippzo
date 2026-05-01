/**
 * Delivery Confirmation (Phase-11)
 * --------------------------------
 * Post-shipping confirmation system — NOT a scanner. Once a parcel has
 * been Shipped for >= threshold days (default 5), it lands here.
 * Admin selects rows, taps "Send WhatsApp" to open WhatsApp with a
 * pre-filled Gujarati template, then manually flips to Delivered after
 * the customer confirms.
 *
 * Core rules:
 *   • Never auto-mark delivered (manual only).
 *   • Same-day WhatsApp resend blocked by backend safety.
 *   • Bulk select built for 50+ parcels/day workflows.
 *
 * Color palette (locked):
 *   Shipped     #EEE9FF / #6B5BFF  (purple — status badge, WhatsApp btn)
 *   Delivered   #E6F7EE / #1F9D55  (green — "Mark as Delivered")
 *   Pending     #FFF3E0 / #B45309  (cream — confirmation_status="pending")
 *   Failed      #FFE5E5 / #991B1B
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Linking,
  Alert,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Stack, router } from "expo-router";
import { Api, Shipment } from "../lib/api";

type ConfStatus = "pending" | "sent" | "replied" | "confirmed" | "failed";
type Tab = "list" | "sent" | "replied" | "pending";

type ConfShipment = Shipment & { days_since_shipped: number };

const THRESHOLD_DAYS = 5;

const STATUS_STYLE: Record<ConfStatus, { bg: string; fg: string; label: string }> = {
  pending:   { bg: "#FFF3E0", fg: "#B45309", label: "Pending" },
  sent:      { bg: "#EEE9FF", fg: "#6B5BFF", label: "Sent" },
  replied:   { bg: "#DDEFFF", fg: "#1E40AF", label: "Replied" },
  confirmed: { bg: "#E6F7EE", fg: "#1F9D55", label: "Confirmed" },
  failed:    { bg: "#FFE5E5", fg: "#991B1B", label: "Failed" },
};

const TEMPLATE =
  "Namaste 👋\n\n" +
  "તમારો પાર્સલ મળી ગયો છે?\n\n" +
  "Reply:\nYES / NO";

export default function DeliveryConfirmation() {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("list");
  const [rows, setRows] = useState<ConfShipment[]>([]);
  const [counts, setCounts] = useState({ list: 0, sent: 0, replied: 0, pending: 0 });
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await Api.deliveryConfList(THRESHOLD_DAYS);
      setRows(res.shipments || []);
      setCounts(res.counts);
      // Drop stale selections (shipments no longer in the list).
      setSelected((prev) => {
        const keep: Record<string, boolean> = {};
        const ids = new Set((res.shipments || []).map((r) => r.id));
        for (const k of Object.keys(prev)) if (ids.has(k) && prev[k]) keep[k] = true;
        return keep;
      });
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Filter rows for the active tab.
  const visible = useMemo(() => {
    if (tab === "list") return rows;
    return rows.filter((r) => {
      const cs = (r as any).confirmation_status || "pending";
      if (tab === "sent")    return cs === "sent";
      if (tab === "replied") return cs === "replied";
      if (tab === "pending") return cs === "pending";
      return false;
    });
  }, [rows, tab]);

  const selectedIds = useMemo(
    () => Object.keys(selected).filter((k) => selected[k]),
    [selected],
  );
  const allSelected = visible.length > 0 && visible.every((r) => selected[r.id]);

  const toggleOne = (id: string) =>
    setSelected((p) => ({ ...p, [id]: !p[id] }));
  const toggleAll = () => {
    if (allSelected) {
      setSelected({});
    } else {
      const next: Record<string, boolean> = {};
      for (const r of visible) next[r.id] = true;
      setSelected(next);
    }
  };

  // Open WhatsApp for a single shipment (chain-called in bulk).
  const openWhatsApp = async (phone: string, message: string): Promise<boolean> => {
    const digits = (phone || "").replace(/\D/g, "");
    if (digits.length < 10) return false;
    // Assume IN number when 10 digits.
    const e164 = digits.length === 10 ? "91" + digits : digits;
    const url = `whatsapp://send?phone=${e164}&text=${encodeURIComponent(message)}`;
    try {
      const ok = await Linking.canOpenURL(url);
      if (!ok) {
        // Fallback to https link which works on mobile too.
        await Linking.openURL(
          `https://wa.me/${e164}?text=${encodeURIComponent(message)}`,
        );
      } else {
        await Linking.openURL(url);
      }
      return true;
    } catch {
      try {
        await Linking.openURL(
          `https://wa.me/${e164}?text=${encodeURIComponent(message)}`,
        );
        return true;
      } catch {
        return false;
      }
    }
  };

  const handleSendWhatsApp = async () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels you want to message.");
      return;
    }
    // One-tap flow — open the FIRST selected number now so WhatsApp
    // doesn't get blocked by rapid-open throttling on Android. The
    // remaining will be opened sequentially as the user returns.
    setBusy(true);
    try {
      // Mark sent on backend first (safety: same-day resend blocked there).
      const markRes = await Api.deliveryConfMarkSent(selectedIds);
      const ok = markRes.updated;
      const skipped = markRes.skipped;
      // Use updated_ids order for opening WhatsApp (skipped are silently ignored).
      const toMsg = rows.filter((r) => markRes.updated_ids.includes(r.id));
      let opened = 0;
      for (const r of toMsg) {
        const phone = String(r.customer_phone || "").trim();
        const msg = `${TEMPLATE}\n\nOrder ID: ${r.order_id || r.tracking_id}`;
        const ok2 = await openWhatsApp(phone, msg);
        if (ok2) opened += 1;
        // On Android successive deep-link opens can be coalesced;
        // small delay helps ensure each chat window opens reliably.
        if (Platform.OS === "android") {
          await new Promise((r) => setTimeout(r, 350));
        }
      }
      Alert.alert(
        "WhatsApp sent",
        `${opened} chat(s) opened.\n${ok} marked sent.\n${skipped} skipped (already sent today).`,
      );
      setSelected({});
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setBusy(false);
    }
  };

  const handleMarkDelivered = async () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels the customer has confirmed.");
      return;
    }
    Alert.alert(
      "Mark as Delivered",
      `Confirm ${selectedIds.length} parcel(s) as delivered? This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Confirm",
          style: "destructive",
          onPress: async () => {
            setBusy(true);
            try {
              const res = await Api.deliveryConfMarkDelivered(selectedIds);
              Alert.alert(
                "Done",
                `${res.updated} of ${res.requested} marked Delivered.`,
              );
              setSelected({});
              load();
            } catch (e: any) {
              Alert.alert(
                "Error",
                e?.response?.data?.detail || e?.message || "Failed",
              );
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen
        options={{
          title: "Delivery Confirmation",
          headerBackTitle: "Back",
        }}
      />

      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 120 }}
        refreshControl={undefined}
      >
        {/* Top banner */}
        <View style={styles.banner}>
          <View style={styles.bannerIcon}>
            <Ionicons name="alert-circle" size={22} color="#B45309" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.bannerTitle}>
              Need Confirmation · {counts.list}{" "}
              <Text style={styles.bannerSub}>parcels</Text>
            </Text>
            <Text style={styles.bannerHint}>
              Based on courier 5–8 days rule · threshold {THRESHOLD_DAYS} days
            </Text>
          </View>
        </View>

        {/* Tabs */}
        <View style={styles.tabs}>
          {(["list", "sent", "replied", "pending"] as Tab[]).map((t) => {
            const active = tab === t;
            const n =
              t === "list" ? counts.list :
              t === "sent" ? counts.sent :
              t === "replied" ? counts.replied : counts.pending;
            return (
              <TouchableOpacity
                key={t}
                style={[styles.tabBtn, active && styles.tabBtnActive]}
                onPress={() => setTab(t)}
                testID={`dc-tab-${t}`}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>
                  {t === "list" ? "List" : t === "sent" ? "Sent" : t === "replied" ? "Replied" : "Pending"}
                </Text>
                <View
                  style={[
                    styles.tabBadge,
                    active && { backgroundColor: "rgba(255,255,255,0.25)" },
                  ]}
                >
                  <Text
                    style={[
                      styles.tabBadgeText,
                      active && { color: "#fff" },
                    ]}
                  >
                    {n}
                  </Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* Select-all row */}
        {visible.length > 0 && (
          <TouchableOpacity
            style={styles.selectAll}
            onPress={toggleAll}
            testID="dc-select-all"
          >
            <Ionicons
              name={allSelected ? "checkbox" : "square-outline"}
              size={20}
              color={allSelected ? "#6B5BFF" : "#9CA3AF"}
            />
            <Text style={styles.selectAllText}>
              {allSelected ? "Deselect all" : "Select all"}
              <Text style={styles.selectAllCount}>
                {"  · " + visible.length + " visible, " + selectedIds.length + " selected"}
              </Text>
            </Text>
          </TouchableOpacity>
        )}

        {/* List */}
        {loading ? (
          <ActivityIndicator color="#6B5BFF" style={{ marginVertical: 36 }} />
        ) : visible.length === 0 ? (
          <View style={styles.empty}>
            <Ionicons name="checkmark-circle" size={36} color="#D4D4D8" />
            <Text style={styles.emptyText}>
              No parcels in this bucket. All caught up!
            </Text>
          </View>
        ) : (
          visible.map((r) => {
            const cs = ((r as any).confirmation_status || "pending") as ConfStatus;
            const meta = STATUS_STYLE[cs];
            const sel = !!selected[r.id];
            const phone = String(r.customer_phone || "").trim();
            return (
              <View key={r.id} style={[styles.row, sel && styles.rowSel]}>
                <TouchableOpacity onPress={() => toggleOne(r.id)} hitSlop={8}>
                  <Ionicons
                    name={sel ? "checkbox" : "square-outline"}
                    size={22}
                    color={sel ? "#6B5BFF" : "#9CA3AF"}
                  />
                </TouchableOpacity>
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.rowOrder}>
                    {r.order_id || r.tracking_id}
                    <Text style={styles.rowDaysTag}>
                      {"  "}· {r.days_since_shipped}d
                    </Text>
                  </Text>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {(r as any).customer_name || "—"}
                    {phone ? (
                      <Text style={styles.rowPhone}>{"  " + phone}</Text>
                    ) : null}
                  </Text>
                  <Text style={styles.rowCourier} numberOfLines={1}>
                    {(r as any).courier_name || "Courier —"}
                  </Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 6 }}>
                  <View
                    style={[
                      styles.statusBadge,
                      { backgroundColor: meta.bg },
                    ]}
                  >
                    <Text style={[styles.statusBadgeText, { color: meta.fg }]}>
                      {meta.label}
                    </Text>
                  </View>
                  <TouchableOpacity
                    onPress={() =>
                      openWhatsApp(
                        phone,
                        `${TEMPLATE}\n\nOrder ID: ${r.order_id || r.tracking_id}`,
                      )
                    }
                    hitSlop={8}
                  >
                    <Ionicons name="logo-whatsapp" size={20} color="#25D366" />
                  </TouchableOpacity>
                </View>
              </View>
            );
          })
        )}
      </ScrollView>

      {/* Sticky bottom action bar */}
      <View style={styles.stickyBar}>
        <Text style={styles.selCountText}>
          {selectedIds.length > 0
            ? `${selectedIds.length} selected`
            : "Select parcels above"}
        </Text>
        <TouchableOpacity
          style={[
            styles.waBtn,
            (selectedIds.length === 0 || busy) && styles.btnDisabled,
          ]}
          onPress={handleSendWhatsApp}
          disabled={selectedIds.length === 0 || busy}
          testID="dc-send-whatsapp"
        >
          <Ionicons name="logo-whatsapp" size={16} color="#fff" />
          <Text style={styles.waBtnText}>Send WhatsApp</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[
            styles.deliveredBtn,
            (selectedIds.length === 0 || busy) && styles.btnDisabled,
          ]}
          onPress={handleMarkDelivered}
          disabled={selectedIds.length === 0 || busy}
          testID="dc-mark-delivered"
        >
          <Ionicons name="checkmark-done" size={16} color="#fff" />
          <Text style={styles.deliveredBtnText}>Mark as Delivered</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#FFF3E0",
    borderWidth: 1,
    borderColor: "#FCD7A0",
    borderRadius: 14,
    padding: 12,
    gap: 12,
    marginBottom: 14,
  },
  bannerIcon: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: "#FFE5BA",
    alignItems: "center",
    justifyContent: "center",
  },
  bannerTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: "#7C2D12",
  },
  bannerSub: { fontWeight: "600", color: "#B45309" },
  bannerHint: { fontSize: 12, color: "#9A5F22", marginTop: 2 },
  tabs: {
    flexDirection: "row",
    gap: 6,
    marginBottom: 12,
  },
  tabBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 10,
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    gap: 6,
  },
  tabBtnActive: {
    backgroundColor: "#6B5BFF",
    borderColor: "#6B5BFF",
  },
  tabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  tabTextActive: { color: "#fff" },
  tabBadge: {
    backgroundColor: "#E5E7EB",
    paddingHorizontal: 6,
    borderRadius: 999,
    minWidth: 22,
    alignItems: "center",
  },
  tabBadgeText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  selectAll: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  selectAllText: { fontSize: 13, fontWeight: "700", color: "#111827" },
  selectAllCount: { color: "#6B7280", fontWeight: "500" },
  empty: {
    alignItems: "center",
    paddingVertical: 32,
    gap: 8,
  },
  emptyText: { color: "#9CA3AF", fontSize: 13 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    backgroundColor: "#FFF",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#EDEEF1",
    marginBottom: 8,
  },
  rowSel: { borderColor: "#6B5BFF", backgroundColor: "#F8F7FF" },
  rowOrder: { fontSize: 14, fontWeight: "800", color: "#111827" },
  rowDaysTag: { color: "#F97316", fontWeight: "800", fontSize: 12 },
  rowName: { marginTop: 2, fontSize: 12, color: "#374151" },
  rowPhone: { color: "#6B7280", fontWeight: "500" },
  rowCourier: { marginTop: 2, fontSize: 11, color: "#9CA3AF" },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
    minWidth: 70,
    alignItems: "center",
  },
  statusBadgeText: { fontSize: 11, fontWeight: "800" },
  stickyBar: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "#FFF",
    borderTopWidth: 1,
    borderColor: "#EDEEF1",
    paddingHorizontal: 10,
    paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  selCountText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#111827",
    marginRight: 4,
  },
  waBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#6B5BFF",
  },
  waBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  deliveredBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#1F9D55",
  },
  deliveredBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  btnDisabled: { opacity: 0.5 },
});
