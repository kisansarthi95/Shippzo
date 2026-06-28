/**
 * Courier Status Auto Sync — Onboarding + Settings + Event Log (Phase F4.0)
 * -------------------------------------------------------------------------
 * Per-user screen at `/courier-sync`. Three sections:
 *   1. Supported Partners → enable/disable each (India Post for Phase 1).
 *   2. Test Parse → operator pastes a real SMS, we show what we'd extract.
 *   3. Recent Sync Events → last 50 ingest events (matched + ignored).
 *
 * Native bridge: the actual NotificationListenerService that forwards
 * raw SMS to POST /api/courier-sync/ingest requires an EAS dev build
 * (Expo Go cannot host the system-level service). This screen still
 * works fully in Expo Go via the Test-Parse + manual ingest flow.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert,
  ActivityIndicator, RefreshControl, Switch, TextInput, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import PhIcon from "../components/PhIcon";
import { Api } from "../lib/api";
import type {
  CourierSyncPartner, CourierSyncEvent, CourierSyncParseResult,
} from "../lib/api";
import CourierSyncListener from "../modules/courier-sync-listener";

const COLORS = {
  bg:          "#F7F8FA",
  surface:     "#FFFFFF",
  border:      "#E5E7EB",
  borderSoft:  "#F1F5F9",
  text:        "#0F172A",
  textMuted:   "#64748B",
  primary:     "#1F4FBF",
  primarySoft: "#EEF2FF",
  success:     "#059669",
  successBg:   "#ECFDF5",
  warn:        "#D97706",
  warnBg:      "#FFFBEB",
  danger:      "#DC2626",
  dangerBg:    "#FEF2F2",
  inputBg:     "#F8FAFC",
};

const ACTION_META: Record<string, { label: string; color: string; bg: string }> = {
  updated:           { label: "Updated",      color: "#065F46", bg: "#D1FAE5" },
  already_in_sync:   { label: "In Sync",      color: "#1F2937", bg: "#E5E7EB" },
  no_shipment_found: { label: "Not Found",    color: "#7C2D12", bg: "#FFEDD5" },
  partner_disabled:  { label: "Disabled",     color: "#6B7280", bg: "#F3F4F6" },
  ignored_delivered: { label: "Skip",         color: "#3730A3", bg: "#E0E7FF" },
  ignored:           { label: "Ignored",      color: "#6B7280", bg: "#F3F4F6" },
};

const SAMPLE_SMS =
  "Item: EG350860840IN is out for delivery. Delivery will be attempted by - " +
  "Rajeshkumar Mohanlal Chauhan (BEAT_01) - on 2026-06-25 - IndiaPost";

export default function CourierSyncScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [partners, setPartners] = useState<CourierSyncPartner[]>([]);
  const [events, setEvents] = useState<CourierSyncEvent[]>([]);
  const [savingKey, setSavingKey] = useState<string>("");

  // Test-Parse state
  const [tpSender, setTpSender] = useState<string>("VA-INPOST-G");
  const [tpText, setTpText] = useState<string>(SAMPLE_SMS);
  const [tpBusy, setTpBusy] = useState<boolean>(false);
  const [tpResult, setTpResult] = useState<CourierSyncParseResult | null>(null);

  // Native NotificationListener state (Android-only; Expo Go = unavailable).
  const [nativeStatus, setNativeStatus] = useState({
    available:         CourierSyncListener.isAvailable(),
    permissionGranted: CourierSyncListener.isPermissionGranted(),
    ingestConfigured:  false,
    enabled:           false,
  });

  const refreshNativeStatus = useCallback(() => {
    try {
      setNativeStatus(CourierSyncListener.getStatus());
    } catch {
      /* no-op */
    }
  }, []);

  // Push backend URL + JWT + device id down to the native service so it
  // can POST /api/courier-sync/ingest without needing the React layer.
  // Re-fired on every focus so a fresh JWT (after re-login) propagates.
  const pushIngestConfig = useCallback(async () => {
    if (!CourierSyncListener.isAvailable()) return;
    try {
      const token =
        (await AsyncStorage.getItem("@auth_token")) ||
        (await AsyncStorage.getItem("auth_token")) ||
        "";
      const backendUrl =
        (process.env as any).EXPO_PUBLIC_BACKEND_URL ||
        (process.env as any).EXPO_PUBLIC_API_URL ||
        "";
      let deviceId = await AsyncStorage.getItem("@device_id");
      if (!deviceId) {
        deviceId = `dev_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        await AsyncStorage.setItem("@device_id", deviceId);
      }
      if (token && backendUrl) {
        CourierSyncListener.setIngestConfig({
          backendUrl,
          authToken:     token,
          deviceId,
          senderPattern: "INPOST",
        });
      }
      refreshNativeStatus();
    } catch {
      /* no-op */
    }
  }, [refreshNativeStatus]);

  const load = useCallback(async () => {
    try {
      const [p, evs] = await Promise.all([
        Api.courierSyncListPartners(),
        Api.courierSyncListEvents({ limit: 50 }),
      ]);
      setPartners(p || []);
      setEvents(evs?.events || []);
    } catch (e: any) {
      Alert.alert(
        "Could not load",
        e?.response?.data?.detail || e?.message || "Network error",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); pushIngestConfig(); }, [load, pushIngestConfig]);

  const onToggle = useCallback(async (partner: CourierSyncPartner, next: boolean) => {
    setSavingKey(partner.key);
    try {
      await Api.courierSyncUpdateConfig(partner.key, { enabled: next });
      setPartners((prev) =>
        prev.map((p) => (p.key === partner.key ? { ...p, enabled: next } : p)),
      );
      // Mirror the toggle to the native service so it can drop / forward
      // notifications without another round-trip.
      CourierSyncListener.setEnabled(next);
      refreshNativeStatus();
    } catch (e: any) {
      Alert.alert(
        "Could not save",
        e?.response?.data?.detail || e?.message || "Network error",
      );
    } finally {
      setSavingKey("");
    }
  }, [refreshNativeStatus]);

  const onTestParse = useCallback(async () => {
    if (!tpText.trim()) {
      Alert.alert("Need text", "Paste an SMS body before tapping Test Parse.");
      return;
    }
    setTpBusy(true);
    setTpResult(null);
    try {
      const r = await Api.courierSyncTestParse({
        sender: tpSender.trim(),
        text:   tpText.trim(),
      });
      setTpResult(r);
    } catch (e: any) {
      Alert.alert("Parse failed", e?.response?.data?.detail || e?.message || "Network error");
    } finally {
      setTpBusy(false);
    }
  }, [tpSender, tpText]);

  const onLiveIngest = useCallback(async () => {
    if (!tpText.trim()) return;
    setTpBusy(true);
    try {
      const r = await Api.courierSyncIngest({
        sender:    tpSender.trim(),
        text:      tpText.trim(),
        package:   "com.android.messaging",
        device_id: "test-device",
      });
      const action = r.action || (r.matched ? "updated" : "ignored");
      Alert.alert(
        "Ingest result",
        `Matched: ${r.matched ? "Yes" : "No"}\n` +
        `Action: ${action}\n` +
        (r.tracking_id ? `Tracking: ${r.tracking_id}\n` : "") +
        (r.new_status ? `New status: ${r.new_status}\n` : "") +
        (r.reason ? `Reason: ${r.reason}` : ""),
      );
      // Reload events so the audit log reflects the new row.
      load();
    } catch (e: any) {
      Alert.alert("Ingest failed", e?.response?.data?.detail || e?.message || "Network error");
    } finally {
      setTpBusy(false);
    }
  }, [tpSender, tpText, load]);

  const formatTime = (iso?: string) => {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const now = new Date();
      const diff = (now.getTime() - d.getTime()) / 1000;
      if (diff < 60) return "just now";
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      return d.toLocaleString();
    } catch {
      return iso;
    }
  };

  const anyEnabled = useMemo(
    () => partners.some((p) => p.enabled),
    [partners],
  );

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <Stack.Screen options={{ title: "Courier Auto Sync" }} />
        <View style={styles.centered}>
          <ActivityIndicator size="large" color={COLORS.primary} />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <Stack.Screen
        options={{
          title: "Courier Auto Sync",
          headerStyle: { backgroundColor: COLORS.surface },
          headerTitleStyle: { color: COLORS.text },
          headerTintColor: COLORS.primary,
        }}
      />

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.scrollBody}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={COLORS.primary}
          />
        }
      >
        {/* Intro */}
        <View style={styles.heroCard}>
          <View style={styles.heroIcon}>
            <PhIcon name="sync-circle" size={28} color={COLORS.primary} />
          </View>
          <Text style={styles.heroTitle}>Auto-update shipments from courier SMS</Text>
          <Text style={styles.heroBody}>
            We read DLT-registered SMS notifications (e.g. {`"VA-INPOST-G"`} for India Post)
            on your Android device, extract the AWB and status, and update the
            matching shipment automatically — no manual scanning.
          </Text>

          {Platform.OS === "android" ? (
            <View style={[styles.calloutBox, { backgroundColor: COLORS.warnBg, borderColor: "#FCD34D" }]}>
              <PhIcon name="alert-circle" size={16} color={COLORS.warn} />
              <Text style={styles.calloutText}>
                The notification reader requires an EAS development build (this feature
                does NOT work inside Expo Go). After publishing & generating an Android
                build, open the app and grant Notification Access in System Settings.
              </Text>
            </View>
          ) : (
            <View style={[styles.calloutBox, { backgroundColor: COLORS.dangerBg, borderColor: "#FCA5A5" }]}>
              <PhIcon name="phone-portrait-outline" size={16} color={COLORS.danger} />
              <Text style={styles.calloutText}>
                Auto Sync needs Android. iOS does not allow background SMS reading —
                a Share-Intent fallback is on the roadmap.
              </Text>
            </View>
          )}
        </View>

        {/* Native bridge status — Android only. Shows "Grant Notification Access"
            when the native module is bundled but the OS toggle is off. */}
        {Platform.OS === "android" && nativeStatus.available ? (
          <View
            testID="native-permission-card"
            style={[
              styles.heroCard,
              {
                marginTop: -6,
                borderColor: nativeStatus.permissionGranted ? "#A7F3D0" : "#FCD34D",
                backgroundColor: nativeStatus.permissionGranted ? COLORS.successBg : COLORS.warnBg,
              },
            ]}
          >
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
              <PhIcon
                name={nativeStatus.permissionGranted ? "checkmark-circle" : "alert-circle"}
                size={22}
                color={nativeStatus.permissionGranted ? COLORS.success : COLORS.warn}
              />
              <View style={{ flex: 1 }}>
                <Text style={[styles.heroTitle, { fontSize: 14, marginBottom: 2 }]}>
                  {nativeStatus.permissionGranted
                    ? "Notification access granted"
                    : "Notification access required"}
                </Text>
                <Text style={[styles.heroBody, { fontSize: 12 }]}>
                  {nativeStatus.permissionGranted
                    ? "Shippzo can read courier SMS notifications and auto-sync."
                    : "Tap below to open System Settings → enable Shippzo under \"Notification access\"."}
                </Text>
              </View>
            </View>
            {!nativeStatus.permissionGranted ? (
              <TouchableOpacity
                testID="grant-notification-access"
                style={[styles.btn, styles.btnPrimary, { marginTop: 12 }]}
                onPress={() => {
                  CourierSyncListener.openNotificationAccessSettings();
                  setTimeout(refreshNativeStatus, 800);
                }}
              >
                <PhIcon name="settings-outline" size={16} color="#FFFFFF" />
                <Text style={styles.btnPrimaryText}>Grant Notification Access</Text>
              </TouchableOpacity>
            ) : null}
          </View>
        ) : null}

        {/* Partner list */}
        <Text style={styles.sectionLabel}>Supported Couriers</Text>
        {partners.map((p) => (
          <View key={p.key} style={styles.partnerCard}>
            <View style={styles.partnerRow}>
              <View style={styles.partnerIcon}>
                <PhIcon name="rocket-outline" size={20} color={COLORS.primary} />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.partnerName}>{p.name}</Text>
                <Text style={styles.partnerDesc}>{p.description}</Text>
              </View>
              {savingKey === p.key ? (
                <ActivityIndicator size="small" color={COLORS.primary} />
              ) : (
                <Switch
                  testID={`partner-toggle-${p.key}`}
                  value={p.enabled}
                  onValueChange={(v) => onToggle(p, v)}
                  trackColor={{ false: "#D1D5DB", true: COLORS.primary }}
                  thumbColor="#FFFFFF"
                />
              )}
            </View>
            <View style={styles.partnerMeta}>
              <Text style={styles.metaLabel}>Tracking:</Text>
              <Text style={styles.metaMono}>{p.tracking_pattern}</Text>
            </View>
            <View style={styles.partnerMeta}>
              <Text style={styles.metaLabel}>Sender:</Text>
              <Text style={styles.metaMono}>*{p.sender_pattern}*</Text>
            </View>
          </View>
        ))}

        {/* Test Parse */}
        <Text style={styles.sectionLabel}>Test the parser</Text>
        <View style={styles.testCard}>
          <Text style={styles.label}>Sender (DLT header)</Text>
          <TextInput
            testID="tp-sender-input"
            style={styles.input}
            value={tpSender}
            onChangeText={setTpSender}
            placeholder="VA-INPOST-G"
            placeholderTextColor="#9CA3AF"
            autoCapitalize="characters"
            autoCorrect={false}
          />
          <Text style={[styles.label, { marginTop: 12 }]}>SMS body</Text>
          <TextInput
            testID="tp-text-input"
            style={[styles.input, styles.multiline]}
            value={tpText}
            onChangeText={setTpText}
            multiline
            placeholder="Paste an India Post SMS here…"
            placeholderTextColor="#9CA3AF"
            textAlignVertical="top"
          />
          <View style={styles.btnRow}>
            <TouchableOpacity
              testID="tp-test-parse-btn"
              style={[styles.btn, styles.btnSecondary]}
              onPress={onTestParse}
              disabled={tpBusy}
            >
              <PhIcon name="flash" size={16} color={COLORS.primary} />
              <Text style={styles.btnSecondaryText}>
                {tpBusy ? "Working…" : "Test Parse"}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="tp-live-ingest-btn"
              style={[styles.btn, styles.btnPrimary, !anyEnabled && styles.btnDisabled]}
              onPress={onLiveIngest}
              disabled={tpBusy || !anyEnabled}
            >
              <PhIcon name="paper-plane" size={16} color="#FFFFFF" />
              <Text style={styles.btnPrimaryText}>Live Ingest</Text>
            </TouchableOpacity>
          </View>
          {!anyEnabled ? (
            <Text style={styles.hintMuted}>
              Enable at least one courier above before running Live Ingest.
            </Text>
          ) : null}

          {tpResult ? (
            <View
              style={[
                styles.resultBox,
                {
                  backgroundColor: tpResult.matched ? COLORS.successBg : COLORS.dangerBg,
                  borderColor:     tpResult.matched ? "#A7F3D0" : "#FCA5A5",
                },
              ]}
            >
              <View style={styles.resultHeader}>
                <PhIcon
                  name={tpResult.matched ? "checkmark-circle" : "close-circle"}
                  size={18}
                  color={tpResult.matched ? COLORS.success : COLORS.danger}
                />
                <Text
                  style={[
                    styles.resultTitle,
                    { color: tpResult.matched ? COLORS.success : COLORS.danger },
                  ]}
                >
                  {tpResult.matched ? "Matched" : "Not matched"}
                </Text>
              </View>
              {tpResult.matched ? (
                <>
                  <ResultRow label="Partner"   value={tpResult.partner_name || tpResult.partner_key} />
                  <ResultRow label="Tracking"  value={tpResult.tracking_id || ""} mono />
                  <ResultRow label="Status"    value={tpResult.canonical_status || ""} />
                  <ResultRow label="Pipeline"  value={tpResult.shipment_status || ""} />
                  {tpResult.event_date ? (
                    <ResultRow label="Event date" value={tpResult.event_date} />
                  ) : null}
                  {tpResult.postman?.postman_name ? (
                    <ResultRow
                      label="Postman"
                      value={`${tpResult.postman.postman_name} (${tpResult.postman.beat})`}
                    />
                  ) : null}
                </>
              ) : (
                <ResultRow label="Reason" value={tpResult.reason || "unknown"} />
              )}
            </View>
          ) : null}
        </View>

        {/* Recent events */}
        <View style={styles.eventsHeader}>
          <Text style={styles.sectionLabel}>Recent Sync Events</Text>
          <TouchableOpacity onPress={load}>
            <PhIcon name="refresh" size={16} color={COLORS.primary} />
          </TouchableOpacity>
        </View>

        {events.length === 0 ? (
          <View style={styles.emptyBox}>
            <PhIcon name="time-outline" size={24} color={COLORS.textMuted} />
            <Text style={styles.emptyText}>
              No notifications received yet. Once Auto Sync is wired up,
              every SMS the device receives will land here.
            </Text>
          </View>
        ) : (
          events.map((ev) => {
            const meta = ACTION_META[ev.action] || ACTION_META.ignored;
            return (
              <View key={ev.id} style={styles.eventCard}>
                <View style={styles.eventTop}>
                  <View style={[styles.actionPill, { backgroundColor: meta.bg }]}>
                    <Text style={[styles.actionPillText, { color: meta.color }]}>
                      {meta.label}
                    </Text>
                  </View>
                  <Text style={styles.eventTime}>{formatTime(ev.received_at)}</Text>
                </View>
                <View style={styles.eventBody}>
                  {ev.tracking_id ? (
                    <Text style={styles.eventTrack}>{ev.tracking_id}</Text>
                  ) : null}
                  {ev.canonical_status ? (
                    <Text style={styles.eventStatus}>
                      {ev.canonical_status}
                    </Text>
                  ) : null}
                </View>
                {ev.sender ? (
                  <Text style={styles.eventSender}>From: {ev.sender}</Text>
                ) : null}
                {ev.raw_text ? (
                  <Text style={styles.eventRaw} numberOfLines={2}>
                    {ev.raw_text}
                  </Text>
                ) : null}
                {ev.shipment_id && ev.action === "updated" ? (
                  <TouchableOpacity
                    onPress={() =>
                      router.push({
                        pathname: "/shipment-details/[id]" as any,
                        params:   { id: ev.shipment_id },
                      })
                    }
                    style={styles.eventLink}
                  >
                    <Text style={styles.eventLinkText}>Open shipment</Text>
                    <PhIcon name="chevron-forward" size={12} color={COLORS.primary} />
                  </TouchableOpacity>
                ) : null}
              </View>
            );
          })
        )}

        <View style={{ height: 24 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

function ResultRow({
  label, value, mono,
}: { label: string; value: string; mono?: boolean }) {
  return (
    <View style={styles.resultRow}>
      <Text style={styles.resultLabel}>{label}</Text>
      <Text
        style={[
          styles.resultValue,
          mono ? { fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" } : null,
        ]}
        selectable
      >
        {value || "—"}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: COLORS.bg },
  scroll: { flex: 1 },
  scrollBody: { padding: 16, paddingBottom: 40 },
  centered: { flex: 1, justifyContent: "center", alignItems: "center" },

  heroCard: {
    backgroundColor: COLORS.surface,
    borderColor:     COLORS.border,
    borderWidth:     1,
    borderRadius:    14,
    padding:         16,
    marginBottom:    16,
  },
  heroIcon: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: COLORS.primarySoft,
    alignItems: "center", justifyContent: "center",
    marginBottom: 10,
  },
  heroTitle: {
    fontSize: 16, fontWeight: "700", color: COLORS.text, marginBottom: 6,
  },
  heroBody: {
    fontSize: 13, color: COLORS.textMuted, lineHeight: 19,
  },
  calloutBox: {
    flexDirection: "row", gap: 8, alignItems: "flex-start",
    borderWidth: 1, borderRadius: 10,
    padding: 10, marginTop: 12,
  },
  calloutText: {
    flex: 1, fontSize: 12, color: COLORS.text, lineHeight: 17,
  },

  sectionLabel: {
    fontSize: 12, fontWeight: "700", color: COLORS.textMuted,
    textTransform: "uppercase", letterSpacing: 0.5,
    marginTop: 8, marginBottom: 8,
  },

  partnerCard: {
    backgroundColor: COLORS.surface,
    borderColor:     COLORS.border,
    borderWidth:     1,
    borderRadius:    12,
    padding:         12,
    marginBottom:    10,
  },
  partnerRow:  { flexDirection: "row", alignItems: "center", gap: 12 },
  partnerIcon: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: COLORS.primarySoft,
    alignItems: "center", justifyContent: "center",
  },
  partnerName: { fontSize: 14, fontWeight: "700", color: COLORS.text },
  partnerDesc: { fontSize: 12, color: COLORS.textMuted, marginTop: 2 },
  partnerMeta: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 8, paddingLeft: 48,
  },
  metaLabel: { fontSize: 11, color: COLORS.textMuted, fontWeight: "600" },
  metaMono:  {
    fontSize: 11, color: COLORS.text,
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
  },

  testCard: {
    backgroundColor: COLORS.surface,
    borderColor:     COLORS.border,
    borderWidth:     1,
    borderRadius:    12,
    padding:         12,
    marginBottom:    16,
  },
  label: { fontSize: 12, fontWeight: "600", color: COLORS.textMuted, marginBottom: 4 },
  input: {
    backgroundColor: COLORS.inputBg,
    borderColor:     COLORS.border,
    borderWidth:     1,
    borderRadius:    10,
    paddingHorizontal: 12,
    paddingVertical:   10,
    fontSize: 13,
    color: COLORS.text,
  },
  multiline: { minHeight: 90, paddingTop: 10 },
  btnRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  btn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, paddingVertical: 12, borderRadius: 10,
  },
  btnPrimary:   { backgroundColor: COLORS.primary },
  btnSecondary: {
    backgroundColor: COLORS.primarySoft,
    borderColor: "#C7D2FE", borderWidth: 1,
  },
  btnDisabled: { opacity: 0.5 },
  btnPrimaryText:   { color: "#FFFFFF",       fontWeight: "700", fontSize: 13 },
  btnSecondaryText: { color: COLORS.primary,  fontWeight: "700", fontSize: 13 },
  hintMuted: { fontSize: 11, color: COLORS.textMuted, marginTop: 8 },

  resultBox: {
    marginTop: 14, padding: 12, borderRadius: 10, borderWidth: 1,
  },
  resultHeader: {
    flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 8,
  },
  resultTitle: { fontSize: 13, fontWeight: "700" },
  resultRow: {
    flexDirection: "row", justifyContent: "space-between",
    paddingVertical: 3,
  },
  resultLabel: { fontSize: 12, color: COLORS.textMuted },
  resultValue: { fontSize: 12, color: COLORS.text, fontWeight: "600", maxWidth: "65%", textAlign: "right" },

  eventsHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    marginTop: 8,
  },
  emptyBox: {
    alignItems: "center", padding: 24,
    backgroundColor: COLORS.surface,
    borderColor: COLORS.border, borderWidth: 1, borderRadius: 12,
    gap: 8,
  },
  emptyText: {
    fontSize: 12, color: COLORS.textMuted, textAlign: "center", lineHeight: 17,
  },
  eventCard: {
    backgroundColor: COLORS.surface,
    borderColor: COLORS.border, borderWidth: 1, borderRadius: 12,
    padding: 12, marginBottom: 8,
  },
  eventTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  actionPill: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  actionPillText: { fontSize: 10, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.3 },
  eventTime: { fontSize: 11, color: COLORS.textMuted },
  eventBody: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
  eventTrack: {
    fontSize: 13, fontWeight: "700", color: COLORS.text,
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
  },
  eventStatus: { fontSize: 12, color: COLORS.primary, fontWeight: "600" },
  eventSender: { fontSize: 11, color: COLORS.textMuted, marginTop: 4 },
  eventRaw:    { fontSize: 11, color: COLORS.textMuted, marginTop: 4, lineHeight: 15 },
  eventLink:   {
    flexDirection: "row", alignItems: "center", gap: 2,
    marginTop: 8, alignSelf: "flex-start",
  },
  eventLinkText: { fontSize: 12, color: COLORS.primary, fontWeight: "600" },
});
