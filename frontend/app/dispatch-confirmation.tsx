/**
 * Dispatch Confirmation (Phase-12)
 * --------------------------------
 * Post-Shipped notification flow — once a parcel flips to "Shipped"
 * status (via /scan-ship or manual mark), it lands here. Admin bulk-
 * selects rows and taps "Send WhatsApp" to fire off the
 * `dispatch_confirmation` template ("your parcel is on its way").
 *
 * Mirrors the Delivery-Confirmation screen pattern:
 *   • List / Sent / Pending tabs
 *   • Bulk select + sticky action bar
 *   • Anti-spam: same-day repeats are server-side blocked
 *
 * Shipments stay here forever (well, until status flips Delivered).
 * The status badge reflects `dispatch_msg_status`.
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
  Modal,
  Pressable,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Stack } from "expo-router";
import { Api, Shipment } from "../lib/api";

type DStatus = "pending" | "sent";
type Tab = "list" | "sent" | "pending";

type DShipment = Shipment & {
  days_since_shipped: number;
  dispatch_msg_status?: DStatus;
  dispatch_msg_sent_at?: string;
};

const STATUS_STYLE: Record<DStatus, { bg: string; fg: string; label: string }> = {
  pending: { bg: "#FFF3E0", fg: "#B45309", label: "Pending" },
  sent:    { bg: "#EEE9FF", fg: "#6B5BFF", label: "Sent" },
};

// Languages the user can choose from when sending the dispatch ping.
const LANG_OPTIONS = [
  { key: "gu", label: "ગુજરાતી" },
  { key: "hi", label: "हिन्दी" },
  { key: "en", label: "English" },
];

export default function DispatchConfirmation() {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("list");
  const [rows, setRows] = useState<DShipment[]>([]);
  const [counts, setCounts] = useState({ list: 0, sent: 0, pending: 0 });
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  // Cached resolved templates keyed by language so we hit the server once.
  const [templateCache, setTemplateCache] = useState<Record<string, string>>({});
  const [defaultLang, setDefaultLang] = useState<string>("gu");
  const [langPickerOpen, setLangPickerOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, tplRes] = await Promise.all([
        Api.dispatchConfList(),
        Api.meWhatsAppTemplates().catch(() => null),
      ]);
      setRows((list.shipments || []) as DShipment[]);
      setCounts(list.counts);
      if (tplRes) {
        setDefaultLang(tplRes.default_language || "gu");
      }
      // Drop stale selections.
      setSelected((prev) => {
        const keep: Record<string, boolean> = {};
        const ids = new Set((list.shipments || []).map((r) => r.id));
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
      const cs = (r.dispatch_msg_status || "pending") as DStatus;
      if (tab === "sent")    return cs === "sent";
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

  // Substitute {var} placeholders with shipment fields.
  const fillTemplate = (template: string, s: DShipment): string => {
    const vars: Record<string, string> = {
      customer_name: String(s.customer_name || ""),
      order_id: String(s.order_id || s.tracking_id || ""),
      tracking_id: String(s.tracking_id || ""),
      courier: String(s.courier_name || ""),
      eta_days: String((s as any).courier_eta_days || ""),
    };
    return template.replace(/\{(\w+)\}/g, (_m, k) => vars[k] ?? "");
  };

  // Get the resolved template for a given language (cached).
  const getTemplate = useCallback(
    async (lang: string): Promise<string> => {
      if (templateCache[lang]) return templateCache[lang];
      try {
        const res = await Api.resolveTemplate("dispatch_confirmation", lang);
        setTemplateCache((p) => ({ ...p, [lang]: res.template }));
        return res.template;
      } catch {
        return ""; // empty falls through to no message
      }
    },
    [templateCache],
  );

  const openWhatsApp = async (phone: string, message: string): Promise<boolean> => {
    const digits = (phone || "").replace(/\D/g, "");
    if (digits.length < 10) return false;
    const e164 = digits.length === 10 ? "91" + digits : digits;
    const url = `whatsapp://send?phone=${e164}&text=${encodeURIComponent(message)}`;
    try {
      const ok = await Linking.canOpenURL(url);
      if (!ok) {
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

  const handleSendWhatsApp = async (overrideLang?: string) => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels you want to message.");
      return;
    }
    const lang = overrideLang || defaultLang || "gu";
    const tpl = await getTemplate(lang);
    if (!tpl) {
      Alert.alert(
        "Template missing",
        "Couldn't load the dispatch template. Please configure it in Settings → WhatsApp Templates.",
      );
      return;
    }
    setBusy(true);
    try {
      const markRes = await Api.dispatchConfMarkSent(selectedIds);
      const ok = markRes.updated;
      const skipped = markRes.skipped;
      const toMsg = rows.filter((r) => markRes.updated_ids.includes(r.id));
      let opened = 0;
      for (const r of toMsg) {
        const phone = String(r.customer_phone || "").trim();
        const msg = fillTemplate(tpl, r);
        const ok2 = await openWhatsApp(phone, msg);
        if (ok2) opened += 1;
        if (Platform.OS === "android") {
          await new Promise((r) => setTimeout(r, 350));
        }
      }
      Alert.alert(
        "Dispatch message sent",
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

  const handleResetSent = () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick rows to reset.");
      return;
    }
    Alert.alert(
      "Reset to pending",
      `Mark ${selectedIds.length} parcel(s) as not-yet-notified? You'll be able to send them again.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: async () => {
            setBusy(true);
            try {
              await Api.dispatchConfReset(selectedIds);
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
          title: "Dispatch Confirmation",
          headerBackTitle: "Back",
        }}
      />

      <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 120 }}>
        {/* Top banner */}
        <View style={styles.banner}>
          <View style={styles.bannerIcon}>
            <Ionicons name="rocket-outline" size={22} color="#1F4FBF" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.bannerTitle}>
              Need Notification · {counts.pending}{" "}
              <Text style={styles.bannerSub}>parcels</Text>
            </Text>
            <Text style={styles.bannerHint}>
              Tap "Send WhatsApp" to tell customers their parcel is on its way.
            </Text>
          </View>
          <TouchableOpacity
            style={styles.langPill}
            onPress={() => setLangPickerOpen(true)}
            testID="dc2-lang-pill"
          >
            <Text style={styles.langPillText}>
              {LANG_OPTIONS.find((l) => l.key === defaultLang)?.label || "Lang"}
            </Text>
            <Ionicons name="chevron-down" size={14} color="#1F4FBF" />
          </TouchableOpacity>
        </View>

        {/* Tabs */}
        <View style={styles.tabs}>
          {(["list", "sent", "pending"] as Tab[]).map((t) => {
            const active = tab === t;
            const n =
              t === "list" ? counts.list :
              t === "sent" ? counts.sent : counts.pending;
            return (
              <TouchableOpacity
                key={t}
                style={[styles.tabBtn, active && styles.tabBtnActive]}
                onPress={() => setTab(t)}
                testID={`dc2-tab-${t}`}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>
                  {t === "list" ? "All" : t === "sent" ? "Sent" : "Pending"}
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
          <TouchableOpacity style={styles.selectAll} onPress={toggleAll}>
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
            const cs = ((r.dispatch_msg_status || "pending") as DStatus);
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
                    {r.customer_name || "—"}
                    {phone ? <Text style={styles.rowPhone}>{"  " + phone}</Text> : null}
                  </Text>
                  <Text style={styles.rowCourier} numberOfLines={1}>
                    {r.courier_name || "Courier —"}
                  </Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 6 }}>
                  <View style={[styles.statusBadge, { backgroundColor: meta.bg }]}>
                    <Text style={[styles.statusBadgeText, { color: meta.fg }]}>
                      {meta.label}
                    </Text>
                  </View>
                  <TouchableOpacity
                    onPress={async () => {
                      const tpl = await getTemplate(defaultLang);
                      if (!tpl) return;
                      await openWhatsApp(phone, fillTemplate(tpl, r));
                    }}
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
          {selectedIds.length > 0 ? `${selectedIds.length} selected` : "Select parcels above"}
        </Text>
        <TouchableOpacity
          style={[
            styles.waBtn,
            (selectedIds.length === 0 || busy) && styles.btnDisabled,
          ]}
          onPress={() => handleSendWhatsApp()}
          disabled={selectedIds.length === 0 || busy}
          testID="dc2-send-whatsapp"
        >
          <Ionicons name="logo-whatsapp" size={16} color="#fff" />
          <Text style={styles.waBtnText}>Send WhatsApp</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[
            styles.resetBtn,
            (selectedIds.length === 0 || busy) && styles.btnDisabled,
          ]}
          onPress={handleResetSent}
          disabled={selectedIds.length === 0 || busy}
          testID="dc2-reset"
        >
          <Ionicons name="refresh" size={16} color="#374151" />
          <Text style={styles.resetBtnText}>Reset</Text>
        </TouchableOpacity>
      </View>

      {/* Language picker modal */}
      <Modal
        visible={langPickerOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setLangPickerOpen(false)}
      >
        <Pressable style={styles.modalScrim} onPress={() => setLangPickerOpen(false)}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Send in language</Text>
            {LANG_OPTIONS.map((l) => (
              <TouchableOpacity
                key={l.key}
                style={[
                  styles.langOpt,
                  defaultLang === l.key && styles.langOptActive,
                ]}
                onPress={() => {
                  setDefaultLang(l.key);
                  setLangPickerOpen(false);
                }}
              >
                <Text
                  style={[
                    styles.langOptText,
                    defaultLang === l.key && styles.langOptTextActive,
                  ]}
                >
                  {l.label}
                </Text>
                {defaultLang === l.key && (
                  <Ionicons name="checkmark" size={18} color="#6B5BFF" />
                )}
              </TouchableOpacity>
            ))}
            <Text style={styles.modalHint}>
              Set a permanent default in Settings → WhatsApp Templates.
            </Text>
          </View>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#E0E9FF",
    borderWidth: 1,
    borderColor: "#B5C8FF",
    borderRadius: 14,
    padding: 12,
    gap: 12,
    marginBottom: 14,
  },
  bannerIcon: {
    width: 40, height: 40, borderRadius: 10,
    backgroundColor: "#C8D5FF",
    alignItems: "center", justifyContent: "center",
  },
  bannerTitle: { fontSize: 15, fontWeight: "800", color: "#1F2A57" },
  bannerSub: { fontWeight: "600", color: "#1F4FBF" },
  bannerHint: { fontSize: 12, color: "#3F50A0", marginTop: 2 },
  langPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#fff", borderRadius: 999,
    borderWidth: 1, borderColor: "#B5C8FF",
  },
  langPillText: { fontSize: 12, fontWeight: "800", color: "#1F4FBF" },
  tabs: { flexDirection: "row", gap: 6, marginBottom: 12 },
  tabBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 10, paddingHorizontal: 10, borderRadius: 10,
    backgroundColor: "#F3F4F6", borderWidth: 1, borderColor: "#E5E7EB", gap: 6,
  },
  tabBtnActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  tabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  tabTextActive: { color: "#fff" },
  tabBadge: {
    backgroundColor: "#E5E7EB", paddingHorizontal: 6,
    borderRadius: 999, minWidth: 22, alignItems: "center",
  },
  tabBadgeText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  selectAll: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 8, paddingHorizontal: 4,
  },
  selectAllText: { fontSize: 13, fontWeight: "700", color: "#111827" },
  selectAllCount: { color: "#6B7280", fontWeight: "500" },
  empty: { alignItems: "center", paddingVertical: 32, gap: 8 },
  emptyText: { color: "#9CA3AF", fontSize: 13 },
  row: {
    flexDirection: "row", alignItems: "center", padding: 12,
    backgroundColor: "#FFF", borderRadius: 12,
    borderWidth: 1, borderColor: "#EDEEF1", marginBottom: 8,
  },
  rowSel: { borderColor: "#6B5BFF", backgroundColor: "#F8F7FF" },
  rowOrder: { fontSize: 14, fontWeight: "800", color: "#111827" },
  rowDaysTag: { color: "#1F4FBF", fontWeight: "800", fontSize: 12 },
  rowName: { marginTop: 2, fontSize: 12, color: "#374151" },
  rowPhone: { color: "#6B7280", fontWeight: "500" },
  rowCourier: { marginTop: 2, fontSize: 11, color: "#9CA3AF" },
  statusBadge: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
    minWidth: 70, alignItems: "center",
  },
  statusBadgeText: { fontSize: 11, fontWeight: "800" },
  stickyBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    backgroundColor: "#FFF", borderTopWidth: 1, borderColor: "#EDEEF1",
    paddingHorizontal: 10, paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 12,
    flexDirection: "row", alignItems: "center", gap: 8,
  },
  selCountText: {
    fontSize: 12, fontWeight: "700",
    color: "#111827", marginRight: 4,
  },
  waBtn: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center",
    gap: 5, paddingVertical: 12, borderRadius: 10,
    backgroundColor: "#6B5BFF",
  },
  waBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  resetBtn: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "center",
    gap: 5, paddingVertical: 12, paddingHorizontal: 16,
    borderRadius: 10,
    backgroundColor: "#F3F4F6",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  resetBtnText: { color: "#374151", fontWeight: "800", fontSize: 13 },
  btnDisabled: { opacity: 0.5 },
  modalScrim: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.4)",
    alignItems: "center", justifyContent: "center", padding: 20,
  },
  modalCard: {
    backgroundColor: "#fff", borderRadius: 14, padding: 16,
    width: "100%", maxWidth: 320, gap: 8,
  },
  modalTitle: { fontSize: 16, fontWeight: "800", color: "#111827", marginBottom: 6 },
  langOpt: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 12, paddingHorizontal: 12,
    borderRadius: 10, backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#EDEEF1",
  },
  langOptActive: { backgroundColor: "#F8F7FF", borderColor: "#6B5BFF" },
  langOptText: { fontSize: 14, fontWeight: "700", color: "#374151" },
  langOptTextActive: { color: "#6B5BFF" },
  modalHint: {
    fontSize: 11, color: "#9CA3AF", marginTop: 4,
    textAlign: "center", fontStyle: "italic",
  },
});
