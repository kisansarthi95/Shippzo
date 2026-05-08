/**
 * Admin → SLA Alerts list (Phase G3)
 * ------------------------------------
 * Shows the most recent SLA breaches grouped by stage. Lets the admin
 * filter (open / dismissed), bulk-dismiss a stage, and dismiss
 * individual alerts. The list refreshes after a "Run scan now" via
 * the engine settings screen.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert,
  ActivityIndicator, RefreshControl, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Api } from "../../lib/api";

const STAGE_COLORS: Record<string, string> = {
  "Pending":       "#9333EA",
  "Processing":    "#0EA5E9",
  "Ready to Ship": "#10B981",
  "Shipped":       "#1F4FBF",
  "Delivered":     "#059669",
  "Feedback":      "#B45309",
};
const PRIORITY_COLOR = (p: string) =>
  p === "high" ? "#DC2626" : p === "medium" ? "#F59E0B" : "#6B7280";

export default function SlaAlertsScreen() {
  const [loading, setLoading]     = useState(true);
  const [refreshing, setRefresh]  = useState(false);
  const [showDismissed, setShowD] = useState(false);
  const [stageFilter, setStage]   = useState<string | null>(null);
  const [alerts, setAlerts]       = useState<any[]>([]);
  const [summary, setSummary]     = useState<any>(null);
  const [busy, setBusy]           = useState(false);

  const load = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([
        Api.adminSlaAlerts({
          dismissed: showDismissed,
          stage: stageFilter || undefined,
          limit: 200,
        }),
        Api.adminSlaSummary(),
      ]);
      setAlerts(a.alerts || []);
      setSummary(s);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to load");
    } finally {
      setLoading(false);
      setRefresh(false);
    }
  }, [showDismissed, stageFilter]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefresh(true); load(); };

  const dismissOne = async (alertId: string) => {
    setBusy(true);
    try {
      await Api.adminSlaDismiss(alertId);
      setAlerts((rows) => rows.filter((r) => r.id !== alertId));
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Dismiss failed");
    } finally {
      setBusy(false);
    }
  };

  const dismissStage = (stage: string) => {
    Alert.alert(
      "Dismiss all?",
      `Mark every open alert in "${stage}" as resolved?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Dismiss all",
          style: "destructive",
          onPress: async () => {
            setBusy(true);
            try {
              await Api.adminSlaDismissBulk({ stage });
              load();
            } catch (e: any) {
              Alert.alert("Error", e?.message || "Bulk dismiss failed");
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  };

  const openWhatsApp = (phone: string, alert: any) => {
    if (!phone) return;
    const cleaned = phone.replace(/\D/g, "");
    const text = encodeURIComponent(
      `🚨 ${alert.priority?.toUpperCase()} alert: order ${alert.shipment?.order_id || alert.shipment_id} `
      + `is ${alert.days_overdue}d past SLA in status "${alert.stage}".`,
    );
    Linking.openURL(`https://wa.me/${cleaned}?text=${text}`);
  };

  /** Format a raw phone string for display on the action button.
   *  Strips non-digits, then groups Indian numbers as
   *  "+91 98765 43210" so the admin can verify which of their
   *  configured numbers a particular alert is targeting. Falls back
   *  to "+<digits>" for non-Indian / unknown formats. */
  const formatPhone = (raw: string) => {
    const d = (raw || "").replace(/\D/g, "");
    if (!d) return raw;
    // 10-digit Indian mobile (e.g. 9876543210) → "+91 98765 43210"
    if (d.length === 10) return `+91 ${d.slice(0, 5)} ${d.slice(5)}`;
    // 91-prefixed 12-digit (e.g. 919876543210) → "+91 98765 43210"
    if (d.length === 12 && d.startsWith("91")) {
      return `+91 ${d.slice(2, 7)} ${d.slice(7)}`;
    }
    return `+${d}`;
  };

  const stagesInList = useMemo(
    () => Array.from(new Set(alerts.map((a) => a.stage))),
    [alerts],
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "SLA Alerts", headerShown: true }} />
      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 60 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {/* Summary card */}
        <View style={styles.summary}>
          <View style={styles.summaryRow}>
            <Text style={styles.summaryNum}>{summary?.total_open || 0}</Text>
            <Text style={styles.summaryLabel}>Open alerts</Text>
          </View>
          <View style={styles.summaryRow}>
            <Text style={[styles.summaryNum, { fontSize: 14, color: "#6B7280" }]}>
              {summary?.total_all || 0}
            </Text>
            <Text style={styles.summaryLabel}>All-time total</Text>
          </View>
          <View style={styles.summaryRow}>
            <Text style={[styles.summaryNum, { fontSize: 12, color: "#6B7280" }]}>
              {summary?.last_run?.ran_at
                ? new Date(summary.last_run.ran_at).toLocaleTimeString()
                : "—"}
            </Text>
            <Text style={styles.summaryLabel}>Last scan</Text>
          </View>
        </View>

        {/* Filters */}
        <View style={styles.filterRow}>
          <TouchableOpacity
            style={[styles.filterChip, !showDismissed && styles.filterChipActive]}
            onPress={() => setShowD(false)}
          >
            <Text style={[styles.filterText, !showDismissed && { color: "#fff" }]}>Open</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.filterChip, showDismissed && styles.filterChipActive]}
            onPress={() => setShowD(true)}
          >
            <Text style={[styles.filterText, showDismissed && { color: "#fff" }]}>Resolved</Text>
          </TouchableOpacity>
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: 8 }}>
          <View style={{ flexDirection: "row", gap: 6 }}>
            <TouchableOpacity
              style={[styles.stageChip, !stageFilter && { backgroundColor: "#111827" }]}
              onPress={() => setStage(null)}
            >
              <Text style={[styles.stageChipText, !stageFilter && { color: "#fff" }]}>All stages</Text>
            </TouchableOpacity>
            {Object.keys(summary?.by_stage || {}).map((s) => {
              const active = stageFilter === s;
              const c = STAGE_COLORS[s] || "#374151";
              return (
                <TouchableOpacity
                  key={s}
                  style={[
                    styles.stageChip,
                    active && { backgroundColor: c, borderColor: c },
                  ]}
                  onPress={() => setStage(active ? null : s)}
                >
                  <Text style={[styles.stageChipText, active && { color: "#fff" }]}>
                    {s} · {summary.by_stage[s]}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </ScrollView>

        {alerts.length === 0 ? (
          <View style={styles.empty}>
            <PhIcon name="checkmark-circle" size={40} color="#10B981" />
            <Text style={styles.emptyText}>
              {showDismissed ? "No resolved alerts." : "All clear — no open SLA alerts."}
            </Text>
          </View>
        ) : (
          <>
            {!showDismissed && stagesInList.length > 0 && (
              <View style={{ flexDirection: "row", gap: 6, marginTop: 10 }}>
                {stagesInList.slice(0, 1).map((s) => (
                  <TouchableOpacity
                    key={s}
                    style={styles.bulkBtn}
                    onPress={() => dismissStage(s)}
                  >
                    <PhIcon name="checkmark-done" size={13} color="#fff" />
                    <Text style={styles.bulkBtnText}>Dismiss all in current view</Text>
                  </TouchableOpacity>
                ))}
              </View>
            )}

            {alerts.map((a) => {
              const c = STAGE_COLORS[a.stage] || "#374151";
              return (
                <View key={a.id} style={styles.card}>
                  <View style={[styles.stageBadge, { backgroundColor: c + "20" }]}>
                    <Text style={[styles.stageBadgeText, { color: c }]}>{a.stage}</Text>
                  </View>
                  <View style={styles.cardBody}>
                    <Text style={styles.cardTitle}>
                      {a.shipment?.customer_name || "(no name)"}
                      <Text style={[styles.priTag, { color: PRIORITY_COLOR(a.priority) }]}>
                        {"  "}· {a.priority?.toUpperCase()}{a.level > 1 ? ` · L${a.level}` : ""}
                      </Text>
                    </Text>
                    <Text style={styles.cardSub}>
                      📦 {a.shipment?.order_id || a.shipment?.tracking_id || a.shipment_id?.slice(0, 8)}
                      {"  ·  "}⏱ {a.days_overdue}d past SLA ({a.sla_days}d)
                    </Text>
                    <Text style={styles.cardSub}>
                      🕒 raised {new Date(a.raised_at).toLocaleString()}
                    </Text>
                  </View>
                  <View style={styles.cardActions}>
                    {/* Phase A — prefer the staff contacts list (with
                        name + role) when configured. Fall back to bare
                        phones array for legacy alerts. */}
                    {Array.isArray(a.contacts) && a.contacts.length > 0 ? (
                      a.contacts.slice(0, 3).map((c: any) => (
                        <TouchableOpacity
                          key={c.phone}
                          style={[styles.actionBtn, styles.waBtnRich]}
                          onPress={() => openWhatsApp(c.phone, a)}
                        >
                          <PhIcon name="logo-whatsapp" size={16} color="#fff" />
                          <View style={{ flexShrink: 1 }}>
                            <Text style={styles.waName} numberOfLines={1}>
                              {c.name || "Team"}
                            </Text>
                            {!!c.role && (
                              <Text style={styles.waRole} numberOfLines={1}>
                                {c.role}
                              </Text>
                            )}
                            <Text style={styles.waPhone} numberOfLines={1}>
                              {formatPhone(c.phone)}
                            </Text>
                          </View>
                        </TouchableOpacity>
                      ))
                    ) : (
                      (a.phones || []).slice(0, 2).map((p: string) => (
                        <TouchableOpacity
                          key={p}
                          style={[styles.actionBtn, styles.waBtn]}
                          onPress={() => openWhatsApp(p, a)}
                        >
                          <PhIcon name="logo-whatsapp" size={14} color="#fff" />
                          <Text
                            style={styles.actionBtnText}
                            numberOfLines={1}
                            allowFontScaling={false}
                          >
                            {formatPhone(p)}
                          </Text>
                        </TouchableOpacity>
                      ))
                    )}
                    {!a.dismissed && (
                      <TouchableOpacity
                        style={[styles.actionBtn, { backgroundColor: "#E5E7EB" }]}
                        onPress={() => dismissOne(a.id)}
                        disabled={busy}
                      >
                        <PhIcon name="close" size={14} color="#374151" />
                        <Text style={[styles.actionBtnText, { color: "#374151" }]}>Dismiss</Text>
                      </TouchableOpacity>
                    )}
                  </View>
                </View>
              );
            })}
          </>
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },

  summary: {
    flexDirection: "row", gap: 12,
    backgroundColor: "#fff", borderRadius: 12,
    padding: 14, borderWidth: 1, borderColor: "#E5E7EB",
  },
  summaryRow: { flex: 1 },
  summaryNum: { fontSize: 22, fontWeight: "800", color: "#DC2626" },
  summaryLabel: { fontSize: 11, color: "#6B7280", marginTop: 2 },

  filterRow: { flexDirection: "row", gap: 8, marginTop: 14 },
  filterChip: {
    paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  filterChipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  filterText: { fontSize: 12, fontWeight: "700", color: "#374151" },

  stageChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  stageChipText: { fontSize: 11, fontWeight: "700", color: "#374151" },

  empty: { alignItems: "center", marginTop: 60, gap: 10 },
  emptyText: { color: "#6B7280", fontSize: 13, fontWeight: "600" },

  bulkBtn: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8,
    backgroundColor: "#9333EA",
  },
  bulkBtnText: { color: "#fff", fontSize: 11.5, fontWeight: "700" },

  card: {
    backgroundColor: "#fff", borderRadius: 12, padding: 12, marginTop: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  stageBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  stageBadgeText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
  cardBody: { marginTop: 6 },
  cardTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  priTag: { fontSize: 11, fontWeight: "700" },
  cardSub: { fontSize: 11.5, color: "#6B7280", marginTop: 2 },
  cardActions: { flexDirection: "row", gap: 6, marginTop: 10, flexWrap: "wrap" },
  actionBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8,
  },
  // WhatsApp pill — green; widens to fit the full +91 XXXXX XXXXX number
  // so the admin can verify it matches a configured contact.
  waBtn: {
    backgroundColor: "#25D366",
    minWidth: 0,
    flexShrink: 1,
  },
  // Phase A — Rich pill that surfaces Name + Role + Phone (3 lines).
  // Used when the alert payload includes the new `contacts` array
  // built from the user's Team Members configuration. Falls back to
  // the plain `waBtn` for legacy alerts that only have phones[].
  waBtnRich: {
    backgroundColor: "#25D366",
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexShrink: 1,
    flexBasis: "100%",        // stack vertically when many contacts
    alignItems: "flex-start",
  },
  waName:  { color: "#fff", fontSize: 13, fontWeight: "800" },
  waRole:  { color: "rgba(255,255,255,0.92)", fontSize: 11, fontWeight: "600", marginTop: 1 },
  waPhone: { color: "rgba(255,255,255,0.92)", fontSize: 11, fontVariant: ["tabular-nums"], marginTop: 1 },
  actionBtnText: { color: "#fff", fontSize: 12, fontWeight: "800", letterSpacing: 0.2 },
});
