/**
 * Generic Bulk Message screen — Phase F2/F3
 * ==========================================
 * One screen serves all 5 stages by reading the [type] route param:
 *   shipment_sent          → Pending shipments (order received ping)
 *   dispatch_confirmation  → Shipped (delay-info / tracking ping)
 *   delivery_confirmation  → Shipped + N days (still not delivered? check-in)
 *   delivery_done          → Delivered (thank-you)
 *   feedback_request       → Delivered + N days (feedback ask)
 *
 * Backend already exposes the unified contract:
 *   GET  /api/me/bulk-message/eligible?ttype=...
 *   POST /api/me/bulk-message/mark-sent  { ttype, shipment_ids }
 *
 * UX mirrors the existing delivery-confirmation screen — multi-select
 * checkbox list, one "Send WhatsApp" CTA that opens chats sequentially
 * with a mid-batch daily-limit guard.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert, Platform, Pressable, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Stack, useLocalSearchParams, router } from "expo-router";
import { Api, Shipment } from "../../lib/api";
import {
  preflightBatchWhatsApp,
  openWhatsAppShare,
} from "../../lib/whatsappGuard";
import { fillFromShipment } from "../../lib/templateVariables";
import DailyLimitBanner from "../../components/DailyLimitBanner";

type Tab = "list" | "pending" | "sent_today";

type Row = Shipment & {
  _days_since: number;
  _msg_sent_today: boolean;
  _last_msg: { status?: string; sent_at?: string };
};

const VALID_TYPES = new Set([
  "shipment_sent",
  "dispatch_confirmation",
  "delivery_confirmation",
  "delivery_done",
  "feedback_request",
]);

const LANG_OPTIONS = [
  { key: "gu", label: "ગુજરાતી" },
  { key: "hi", label: "हिन्दी" },
  { key: "en", label: "English" },
];

export default function BulkMessageScreen() {
  const params = useLocalSearchParams<{ type?: string }>();
  const ttype = String(params?.type || "");
  const isValid = VALID_TYPES.has(ttype);

  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [busy, setBusy]             = useState(false);
  const [meta, setMeta]             = useState<{ label: string; icon: string; min_days: number; statuses: string[] } | null>(null);
  const [rows, setRows]             = useState<Row[]>([]);
  const [counts, setCounts]         = useState({ list: 0, sent_today: 0, pending: 0 });
  const [tab, setTab]               = useState<Tab>("pending");
  const [selected, setSelected]     = useState<Record<string, boolean>>({});
  const [defaultLang, setLang]      = useState("gu");
  const [tplCache, setTplCache]     = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    if (!isValid) return;
    setLoading(true);
    try {
      const [res, tplMeta] = await Promise.all([
        Api.bulkMsgEligible(ttype),
        Api.meWhatsAppTemplates().catch(() => null),
      ]);
      setMeta({
        label:    res.label,
        icon:     res.icon,
        min_days: res.min_days,
        statuses: res.statuses || [],
      });
      setRows(res.shipments as Row[]);
      setCounts(res.counts);
      if (tplMeta) setLang(tplMeta.default_language || "gu");
      setSelected((prev) => {
        const ids = new Set((res.shipments || []).map((r: any) => r.id));
        const keep: Record<string, boolean> = {};
        Object.keys(prev).forEach((k) => { if (ids.has(k) && prev[k]) keep[k] = true; });
        return keep;
      });
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [ttype, isValid]);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => {
    if (tab === "list")        return rows;
    if (tab === "pending")     return rows.filter((r) => !r._msg_sent_today);
    if (tab === "sent_today")  return rows.filter((r) => r._msg_sent_today);
    return rows;
  }, [rows, tab]);

  const selectedIds = useMemo(
    () => Object.keys(selected).filter((k) => selected[k]),
    [selected],
  );
  const allVisibleSelected =
    visible.length > 0 && visible.every((r) => selected[r.id]);

  const toggleOne = (id: string) =>
    setSelected((p) => ({ ...p, [id]: !p[id] }));
  const toggleAll = () => {
    if (allVisibleSelected) {
      // Clear selections for visible rows only
      const next = { ...selected };
      visible.forEach((r) => { delete next[r.id]; });
      setSelected(next);
    } else {
      const next = { ...selected };
      visible.forEach((r) => {
        if (!r._msg_sent_today) next[r.id] = true;
      });
      setSelected(next);
    }
  };

  const getTemplate = useCallback(async (lang: string): Promise<string> => {
    if (tplCache[lang]) return tplCache[lang];
    try {
      const res = await Api.resolveTemplate(ttype, lang);
      setTplCache((p) => ({ ...p, [lang]: res.template }));
      return res.template;
    } catch {
      return "";
    }
  }, [ttype, tplCache]);

  const fillTpl = (template: string, s: Row) => fillFromShipment(template, s);

  const handleSend = async () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels you want to message.");
      return;
    }
    const tpl = await getTemplate(defaultLang);
    if (!tpl) {
      Alert.alert(
        "Template missing",
        "Configure this template in Settings → WhatsApp Templates before sending.",
      );
      return;
    }
    const guard = await preflightBatchWhatsApp(selectedIds.length, {
      batchLabel: meta?.label || ttype,
    });
    if (!guard.ok) return;

    setBusy(true);
    try {
      const markRes = await Api.bulkMsgMarkSent(ttype, selectedIds);
      const toMsg = rows.filter((r) => markRes.updated_ids.includes(r.id));
      let opened = 0;
      let limitHit = false;
      for (const r of toMsg) {
        if (limitHit) break;
        const phone = String(r.customer_phone || "").trim();
        const msg = fillTpl(tpl, r);
        try {
          await Api.meWhatsAppDailyIncrement(guard.force);
        } catch {
          limitHit = true;
          break;
        }
        const ok = await openWhatsAppShare(phone, msg);
        if (ok) opened += 1;
        if (Platform.OS === "android") {
          await new Promise((res) => setTimeout(res, 350));
        }
      }
      Alert.alert(
        "Done",
        `${opened} chat(s) opened\n${markRes.updated} marked sent\n${markRes.skipped} skipped (already sent today)`
        + (limitHit ? "\n\n⚠️ Stopped: WhatsApp daily limit hit." : ""),
      );
      setSelected({});
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setBusy(false);
    }
  };

  const handleResetSelected = () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick rows to roll back to pending.");
      return;
    }
    Alert.alert(
      "Reset to pending?",
      `Roll ${selectedIds.length} rows back so they show in the pending bucket again.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: async () => {
            setBusy(true);
            try {
              const res = await Api.bulkMsgReset(ttype, selectedIds);
              Alert.alert("Reset", `${res.updated} rows reset.`);
              setSelected({});
              load();
            } catch (e: any) {
              Alert.alert("Error", e?.message || "Reset failed");
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  };

  if (!isValid) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Bulk Message" }} />
        <Text style={{ color: "#DC2626", fontSize: 14, fontWeight: "700" }}>
          Unknown bulk message type "{ttype}"
        </Text>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Text style={styles.backBtnText}>← Go back</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  const screenTitle = meta ? `${meta.icon} ${meta.label}` : "Bulk Message";

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: screenTitle, headerShown: true }} />
      <DailyLimitBanner />

      {/* Tabs */}
      <View style={styles.tabsRow}>
        {([
          ["pending",     "Pending",  counts.pending],
          ["sent_today",  "Sent today", counts.sent_today],
          ["list",        "All",      counts.list],
        ] as Array<[Tab, string, number]>).map(([k, label, n]) => {
          const active = tab === k;
          return (
            <TouchableOpacity
              key={k}
              style={[styles.tab, active && styles.tabActive]}
              onPress={() => setTab(k)}
            >
              <Text style={[styles.tabText, active && { color: "#fff" }]}>
                {label} · {n}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {/* Language pill + select-all */}
      <View style={styles.langRow}>
        <TouchableOpacity
          style={styles.langPill}
          onPress={() => {
            const idx = LANG_OPTIONS.findIndex((l) => l.key === defaultLang);
            setLang(LANG_OPTIONS[(idx + 1) % LANG_OPTIONS.length].key);
            setTplCache({});
          }}
        >
          <Ionicons name="language" size={13} color="#1F4FBF" />
          <Text style={styles.langPillText}>
            {LANG_OPTIONS.find((l) => l.key === defaultLang)?.label || "Lang"}
          </Text>
        </TouchableOpacity>
        {meta?.min_days ? (
          <View style={styles.thresholdPill}>
            <Ionicons name="time-outline" size={12} color="#B45309" />
            <Text style={styles.thresholdText}>
              ≥ {meta.min_days}d in {meta.statuses[0]}
            </Text>
          </View>
        ) : null}
        <View style={{ flex: 1 }} />
        <TouchableOpacity onPress={toggleAll} style={styles.selectAllBtn}>
          <Ionicons
            name={allVisibleSelected ? "checkbox" : "square-outline"}
            size={16}
            color={allVisibleSelected ? "#10B981" : "#6B7280"}
          />
          <Text style={styles.selectAllText}>
            {allVisibleSelected ? "Deselect all" : "Select all"}
          </Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 12, paddingBottom: 130 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
          />
        }
      >
        {visible.length === 0 ? (
          <View style={styles.empty}>
            <Ionicons name="checkmark-circle" size={38} color="#10B981" />
            <Text style={styles.emptyText}>
              {tab === "pending"
                ? "No pending parcels for this stage."
                : tab === "sent_today"
                  ? "No messages sent today yet."
                  : "Nothing to show."}
            </Text>
          </View>
        ) : (
          visible.map((r) => {
            const isSel = !!selected[r.id];
            const sent  = r._msg_sent_today;
            const sentAt = r._last_msg?.sent_at;
            return (
              <Pressable
                key={r.id}
                style={[styles.card, isSel && styles.cardSelected, sent && styles.cardMuted]}
                onPress={() => toggleOne(r.id)}
              >
                <View style={styles.cardLeft}>
                  <Ionicons
                    name={isSel ? "checkbox" : "square-outline"}
                    size={20}
                    color={isSel ? "#10B981" : sent ? "#9CA3AF" : "#6B7280"}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <View style={styles.cardTop}>
                    <Text style={styles.cardName} numberOfLines={1}>
                      {r.customer_name || "(no name)"}
                    </Text>
                    {sent && (
                      <View style={styles.sentBadge}>
                        <Text style={styles.sentBadgeText}>SENT TODAY</Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.cardSub} numberOfLines={1}>
                    📦 {r.order_id || r.tracking_id || r.id?.slice(0, 8)}
                    {"  ·  "}
                    📞 {r.customer_phone || "—"}
                  </Text>
                  <Text style={styles.cardMeta}>
                    {r.status} · {r._days_since != null ? `${r._days_since}d ago` : ""}
                    {sentAt ? `  ·  last sent ${new Date(sentAt).toLocaleTimeString()}` : ""}
                  </Text>
                </View>
              </Pressable>
            );
          })
        )}
      </ScrollView>

      {/* Sticky action bar */}
      <View style={styles.actionBar}>
        {tab === "sent_today" ? (
          <TouchableOpacity
            style={[styles.actionBtn, { backgroundColor: "#9333EA" }, busy && { opacity: 0.5 }]}
            disabled={busy}
            onPress={handleResetSelected}
          >
            {busy ? <ActivityIndicator color="#fff" /> : (
              <>
                <Ionicons name="refresh" size={15} color="#fff" />
                <Text style={styles.actionBtnText}>
                  Reset {selectedIds.length} to pending
                </Text>
              </>
            )}
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            style={[styles.actionBtn, { backgroundColor: "#25D366" }, (busy || selectedIds.length === 0) && { opacity: 0.5 }]}
            disabled={busy || selectedIds.length === 0}
            onPress={handleSend}
          >
            {busy ? <ActivityIndicator color="#fff" /> : (
              <>
                <Ionicons name="logo-whatsapp" size={16} color="#fff" />
                <Text style={styles.actionBtnText}>
                  Send WhatsApp ({selectedIds.length})
                </Text>
              </>
            )}
          </TouchableOpacity>
        )}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F7F7F9", gap: 14 },
  backBtn: { paddingHorizontal: 16, paddingVertical: 10, backgroundColor: "#1F4FBF", borderRadius: 8 },
  backBtnText: { color: "#fff", fontWeight: "700" },

  tabsRow: {
    flexDirection: "row", gap: 6, paddingHorizontal: 12, paddingTop: 10,
  },
  tab: {
    flex: 1, paddingVertical: 8, paddingHorizontal: 6,
    borderRadius: 999, alignItems: "center",
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  tabActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  tabText: { fontSize: 11.5, fontWeight: "700", color: "#374151" },

  langRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingTop: 10, paddingBottom: 6,
  },
  langPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999,
    backgroundColor: "#EFF6FF", borderWidth: 1, borderColor: "#BFDBFE",
  },
  langPillText: { fontSize: 11, fontWeight: "700", color: "#1F4FBF" },
  thresholdPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999,
    backgroundColor: "#FFFBEB", borderWidth: 1, borderColor: "#FED7AA",
  },
  thresholdText: { fontSize: 11, fontWeight: "700", color: "#B45309" },
  selectAllBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  selectAllText: { fontSize: 11.5, fontWeight: "700", color: "#374151" },

  empty: { alignItems: "center", marginTop: 60, gap: 10 },
  emptyText: { color: "#6B7280", fontSize: 13, fontWeight: "600" },

  card: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: "#fff", borderRadius: 10, padding: 12,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 8,
  },
  cardSelected: { borderColor: "#10B981", borderWidth: 1.5, backgroundColor: "#F0FDF4" },
  cardMuted: { backgroundColor: "#F9FAFB", opacity: 0.85 },
  cardLeft: { paddingRight: 2 },
  cardTop: { flexDirection: "row", alignItems: "center", gap: 8 },
  cardName: { fontSize: 14, fontWeight: "800", color: "#111827", flex: 1 },
  sentBadge: { backgroundColor: "#EEE9FF", paddingHorizontal: 7, paddingVertical: 2, borderRadius: 6 },
  sentBadgeText: { fontSize: 9, fontWeight: "800", color: "#6B5BFF", letterSpacing: 0.4 },
  cardSub:  { fontSize: 11.5, color: "#6B7280", marginTop: 2 },
  cardMeta: { fontSize: 10.5, color: "#9CA3AF", marginTop: 2 },

  actionBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    paddingHorizontal: 14, paddingVertical: 12, paddingBottom: 16,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  actionBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 14, borderRadius: 12,
  },
  actionBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
