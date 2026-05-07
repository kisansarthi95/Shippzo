/**
 * Sheet Sync screen — Phase H
 * ----------------------------
 * Lets the user see the current state of their Google Sheet auto-sync
 * (how many shipments are synced / queued / failed), toggle the three
 * auto-sync triggers (create / status change / delete), and tap
 * "Sync now" to drain the retry queue immediately.
 */
import React, { useCallback, useEffect, useState } from "react";
import PhIcon from "../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, Switch, TouchableOpacity,
  ActivityIndicator, Alert, Linking, RefreshControl,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Api } from "../lib/api";

type Status = {
  connected: boolean;
  sheet_id: string;
  sheet_url: string;
  auto_sync_create: boolean;
  auto_sync_status: boolean;
  auto_sync_delete: boolean;
  shipment_counts: { ok: number; pending: number; skipped: number; error: number; never: number };
  queue_pending: number;
  total_shipments: number;
};

const TONE = {
  ok:      { color: "#10B981", icon: "checkmark-circle"   as const, label: "Synced" },
  pending: { color: "#F59E0B", icon: "time-outline"        as const, label: "Pending" },
  error:   { color: "#DC2626", icon: "alert-circle"        as const, label: "Errored" },
  skipped: { color: "#6B7280", icon: "remove-circle"       as const, label: "Skipped" },
  never:   { color: "#9CA3AF", icon: "ellipsis-horizontal" as const, label: "Never synced" },
};

export default function SheetSyncScreen() {
  const [loading, setLoading] = useState(true);
  const [status, setStatus]   = useState<Status | null>(null);
  const [busy, setBusy]       = useState<keyof Status | "run" | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await Api.meSheetSyncStatus();
      setStatus(s);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (key: "auto_sync_create" | "auto_sync_status" | "auto_sync_delete", v: boolean) => {
    if (!status) return;
    const prev = status[key];
    setStatus({ ...status, [key]: v });
    setBusy(key);
    try {
      await Api.meSheetSyncToggles({ [key]: v });
    } catch (e: any) {
      setStatus({ ...status, [key]: prev });
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Save failed");
    } finally {
      setBusy(null);
    }
  };

  const runNow = async () => {
    setBusy("run");
    try {
      const res = await Api.meSheetSyncRunNow();
      Alert.alert(
        "Sync done",
        `Backfilled: ${res.backfilled}\nQueue drained: ${res.drained.drained}\nErrored: ${res.errored}\n\n` +
        "Quota cap = 20 ops per click. Re-run if more pending.",
      );
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Sync failed");
    } finally {
      setBusy(null);
    }
  };

  if (loading || !status) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Sheet Sync", headerShown: true }} />
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  const c = status.shipment_counts;
  const pct = status.total_shipments > 0
    ? Math.round((c.ok / status.total_shipments) * 100)
    : 0;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Sheet Sync", headerShown: true }} />
      <ScrollView
        contentContainerStyle={{ padding: 14, paddingBottom: 60 }}
        refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}
      >
        {!status.connected ? (
          <View style={styles.notConnected}>
            <PhIcon name="document-text-outline" size={36} color="#9CA3AF" />
            <Text style={styles.notConnectedTitle}>No Google Sheet connected</Text>
            <Text style={styles.notConnectedSub}>
              Connect a sheet from Settings → Google Sheet to start auto-syncing
              shipments to your spreadsheet.
            </Text>
            <TouchableOpacity
              style={styles.notConnectedBtn}
              onPress={() => router.push("/(tabs)/settings" as any)}
            >
              <Text style={styles.notConnectedBtnText}>Open Settings →</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <>
            {/* Health card */}
            <View style={styles.healthCard}>
              <View style={styles.healthHeader}>
                <PhIcon name="cloud-done" size={22} color="#10B981" />
                <Text style={styles.healthTitle}>Auto-sync is active</Text>
              </View>
              <Text style={styles.healthSub}>
                Every new shipment, status change, and deletion is mirrored
                to your sheet automatically.
              </Text>

              <View style={styles.progressBar}>
                <View style={[styles.progressFill, { width: `${pct}%` }]} />
              </View>
              <View style={styles.progressMeta}>
                <Text style={styles.progressText}>
                  {c.ok}/{status.total_shipments} synced ({pct}%)
                </Text>
                {status.queue_pending > 0 && (
                  <Text style={[styles.progressText, { color: "#F59E0B" }]}>
                    ⏱ {status.queue_pending} queued
                  </Text>
                )}
              </View>

              <View style={styles.statRow}>
                {(["ok", "error", "never", "skipped"] as const).map((k) => {
                  const t = TONE[k];
                  return (
                    <View key={k} style={styles.statCell}>
                      <View style={[styles.statIcon, { backgroundColor: t.color + "20" }]}>
                        <PhIcon name={t.icon} size={14} color={t.color} />
                      </View>
                      <Text style={styles.statNum}>{c[k]}</Text>
                      <Text style={styles.statLabel}>{t.label}</Text>
                    </View>
                  );
                })}
              </View>

              <View style={styles.actionRow}>
                <TouchableOpacity
                  style={[styles.btnGhost, busy === "run" && { opacity: 0.5 }]}
                  onPress={runNow}
                  disabled={busy === "run"}
                >
                  {busy === "run" ? <ActivityIndicator color="#1F4FBF" /> : (
                    <>
                      <PhIcon name="refresh" size={14} color="#1F4FBF" />
                      <Text style={styles.btnGhostText}>Sync now</Text>
                    </>
                  )}
                </TouchableOpacity>
                {!!status.sheet_url && (
                  <TouchableOpacity
                    style={styles.btnGhost}
                    onPress={() => Linking.openURL(status.sheet_url)}
                  >
                    <PhIcon name="open-outline" size={14} color="#1F4FBF" />
                    <Text style={styles.btnGhostText}>Open sheet</Text>
                  </TouchableOpacity>
                )}
              </View>
            </View>

            {/* Toggles */}
            <Text style={styles.sectionHeading}>⚙️ Auto-sync triggers</Text>
            <View style={styles.card}>
              <View style={styles.row}>
                <View style={[styles.rowIcon, { backgroundColor: "#10B98120" }]}>
                  <PhIcon name="add-circle" size={18} color="#10B981" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle}>📦 New shipment created</Text>
                  <Text style={styles.rowDesc}>Append a row whenever you save a new label</Text>
                </View>
                <Switch
                  value={status.auto_sync_create}
                  disabled={busy === "auto_sync_create"}
                  onValueChange={(v) => toggle("auto_sync_create", v)}
                  trackColor={{ false: "#D1D5DB", true: "#10B981" }}
                  thumbColor="#fff"
                />
              </View>
              <View style={styles.row}>
                <View style={[styles.rowIcon, { backgroundColor: "#1F4FBF20" }]}>
                  <PhIcon name="sync" size={18} color="#1F4FBF" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle}>🔄 Status changes</Text>
                  <Text style={styles.rowDesc}>Update the row when status moves (e.g. Shipped → Delivered)</Text>
                </View>
                <Switch
                  value={status.auto_sync_status}
                  disabled={busy === "auto_sync_status"}
                  onValueChange={(v) => toggle("auto_sync_status", v)}
                  trackColor={{ false: "#D1D5DB", true: "#1F4FBF" }}
                  thumbColor="#fff"
                />
              </View>
              <View style={styles.row}>
                <View style={[styles.rowIcon, { backgroundColor: "#DC262620" }]}>
                  <PhIcon name="trash" size={18} color="#DC2626" />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTitle}>🗑️ Shipment deleted</Text>
                  <Text style={styles.rowDesc}>Mark the row as DELETED instead of removing it</Text>
                </View>
                <Switch
                  value={status.auto_sync_delete}
                  disabled={busy === "auto_sync_delete"}
                  onValueChange={(v) => toggle("auto_sync_delete", v)}
                  trackColor={{ false: "#D1D5DB", true: "#DC2626" }}
                  thumbColor="#fff"
                />
              </View>
            </View>

            {(c.error > 0 || status.queue_pending > 0) && (
              <View style={styles.warnCard}>
                <PhIcon name="warning" size={18} color="#B45309" />
                <View style={{ flex: 1 }}>
                  <Text style={styles.warnTitle}>{c.error + status.queue_pending} sync(s) need attention</Text>
                  <Text style={styles.warnSub}>
                    Common causes: Google Sheets quota (60 reads/min), sheet permissions
                    revoked, or row layout changed. Auto-retry runs every 90 seconds.
                  </Text>
                </View>
              </View>
            )}
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  notConnected: { alignItems: "center", marginTop: 60, paddingHorizontal: 24 },
  notConnectedTitle: { fontSize: 16, fontWeight: "800", color: "#111827", marginTop: 14 },
  notConnectedSub: { fontSize: 12, color: "#6B7280", marginTop: 6, textAlign: "center", lineHeight: 17 },
  notConnectedBtn: { marginTop: 18, paddingHorizontal: 18, paddingVertical: 10, borderRadius: 8, backgroundColor: "#1F4FBF" },
  notConnectedBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },

  healthCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  healthHeader: { flexDirection: "row", alignItems: "center", gap: 8 },
  healthTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  healthSub: { fontSize: 11.5, color: "#6B7280", marginTop: 6, lineHeight: 17 },
  progressBar: {
    height: 8, backgroundColor: "#F3F4F6", borderRadius: 4,
    marginTop: 14, overflow: "hidden",
  },
  progressFill: { height: "100%", backgroundColor: "#10B981" },
  progressMeta: { flexDirection: "row", justifyContent: "space-between", marginTop: 6 },
  progressText: { fontSize: 11, fontWeight: "700", color: "#374151" },

  statRow: { flexDirection: "row", marginTop: 14, gap: 6 },
  statCell: { flex: 1, alignItems: "center" },
  statIcon: { width: 28, height: 28, borderRadius: 8, alignItems: "center", justifyContent: "center" },
  statNum: { fontSize: 16, fontWeight: "800", color: "#111827", marginTop: 4 },
  statLabel: { fontSize: 10, color: "#6B7280", marginTop: 2 },

  actionRow: { flexDirection: "row", gap: 8, marginTop: 14 },
  btnGhost: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5,
    paddingVertical: 11, borderRadius: 8,
    backgroundColor: "#EFF6FF", borderWidth: 1, borderColor: "#BFDBFE",
  },
  btnGhostText: { color: "#1F4FBF", fontSize: 12, fontWeight: "800" },

  sectionHeading: { fontSize: 12, fontWeight: "800", color: "#6B7280", marginTop: 14, marginBottom: 6, marginLeft: 2, letterSpacing: 0.4 },
  card: { backgroundColor: "#fff", borderRadius: 12, borderWidth: 1, borderColor: "#E5E7EB" },
  row: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 12, paddingHorizontal: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  rowIcon: { width: 36, height: 36, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  rowTitle: { fontSize: 13.5, fontWeight: "700", color: "#111827" },
  rowDesc: { fontSize: 11.5, color: "#6B7280", marginTop: 2, lineHeight: 15 },

  warnCard: {
    flexDirection: "row", alignItems: "flex-start", gap: 10,
    marginTop: 12, padding: 12, borderRadius: 10,
    backgroundColor: "#FFFBEB", borderWidth: 1, borderColor: "#FED7AA",
  },
  warnTitle: { fontSize: 13, fontWeight: "800", color: "#92400E" },
  warnSub: { fontSize: 11, color: "#92400E", marginTop: 4, lineHeight: 16 },
});
