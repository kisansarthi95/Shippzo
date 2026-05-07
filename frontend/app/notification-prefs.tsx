/**
 * Notification Preferences screen — Phase G6
 * ------------------------------------------
 * Per-user toggle for every push event. Lets the user mute specific
 * types (e.g. morning reminders) without disabling pushes entirely.
 * Includes a "Send test notification" button that hits the backend
 * which round-trips through Expo Push to validate end-to-end.
 */
import React, { useCallback, useEffect, useState } from "react";
import PhIcon from "../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, Switch, TouchableOpacity,
  ActivityIndicator, Alert, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack } from "expo-router";
import { Api, NotificationPrefs } from "../lib/api";
import {
  registerForPushNotificationsAsync,
  getCachedPushToken,
} from "../lib/pushRegistration";

type EventDef = {
  key: keyof NotificationPrefs;
  title: string;
  desc: string;
  icon: any;
  tone: string;
};

const CHANNEL_DEFS: EventDef[] = [
  { key: "channel_push",  title: "Push notifications",  desc: "Receive alerts on this device", icon: "notifications", tone: "#1F4FBF" },
  { key: "channel_email", title: "Email notifications", desc: "Send copies to your inbox",      icon: "mail",          tone: "#0EA5E9" },
];

const OPS_DEFS: EventDef[] = [
  { key: "sla_breach",       title: "🚨 SLA breach alerts",  desc: "Parcels stuck longer than the SLA limit", icon: "alert-circle", tone: "#DC2626" },
  { key: "daily_limit_warn", title: "⚠️ WhatsApp daily limit", desc: "Warn at 80% of daily message limit",    icon: "warning",      tone: "#F59E0B" },
  { key: "morning_reminder", title: "🌅 Morning ops digest",  desc: "8 AM IST summary of pending parcels",   icon: "sunny",        tone: "#F97316" },
  { key: "new_order",        title: "📦 New orders",          desc: "Sheet auto-sync brings in a new order", icon: "cube",         tone: "#10B981" },
  { key: "low_wallet",       title: "💸 Low wallet balance",  desc: "Wallet credits below ₹100",              icon: "wallet",       tone: "#9333EA" },
];

const ACCOUNT_DEFS: EventDef[] = [
  { key: "trial_ending",    title: "🔥 Trial ending soon",   desc: "3-day-before reminder for trial",       icon: "time",        tone: "#DC2626" },
  { key: "plan_expiring",   title: "📅 Plan expiring",       desc: "7-day-before paid-plan reminder",       icon: "calendar",    tone: "#F97316" },
  { key: "low_credits",     title: "🪙 Low credits",         desc: "≤ 5 credits remaining",                  icon: "alert",       tone: "#F59E0B" },
  { key: "payment_success", title: "✅ Payment receipts",    desc: "After successful Razorpay payment",     icon: "checkmark",   tone: "#10B981" },
  { key: "daily_summary",   title: "📊 Daily summary",       desc: "Opt-in daily digest email/push",        icon: "stats-chart", tone: "#0EA5E9" },
];

export default function NotificationPrefsScreen() {
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs]     = useState<NotificationPrefs | null>(null);
  const [busy, setBusy]       = useState<keyof NotificationPrefs | null>(null);
  const [testing, setTesting] = useState(false);
  const [tokens, setTokens]   = useState<{ count: number; tokens: any[] }>({ count: 0, tokens: [] });

  const load = useCallback(async () => {
    try {
      const [p, tk] = await Promise.all([
        Api.getNotificationPrefs(),
        Api.listMyPushTokens().catch(() => ({ count: 0, tokens: [] })),
      ]);
      setPrefs(p);
      setTokens(tk);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggle = async (key: keyof NotificationPrefs, value: boolean) => {
    if (!prefs) return;
    const prev = prefs[key];
    setPrefs({ ...prefs, [key]: value });
    setBusy(key);
    try {
      const updated = await Api.updateNotificationPrefs({ [key]: value } as any);
      setPrefs(updated);
    } catch (e: any) {
      setPrefs({ ...prefs, [key]: prev });   // rollback
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Save failed");
    } finally {
      setBusy(null);
    }
  };

  const reRegister = async () => {
    const tok = await registerForPushNotificationsAsync();
    if (!tok) {
      Alert.alert(
        "Push not available",
        Platform.OS === "web"
          ? "Push notifications work only on the iOS/Android mobile app, not the web preview."
          : "Either permission was denied or this isn't a physical device. Open device settings to grant notifications.",
      );
    } else {
      Alert.alert("Registered", "This device is now ready to receive push notifications.");
      load();
    }
  };

  const testPush = async () => {
    setTesting(true);
    try {
      const res = await Api.testPushToSelf();
      if ((res?.sent || 0) > 0) {
        Alert.alert(
          "Test sent ✓",
          `Delivered to ${res.sent} device(s). Check your notification tray.`,
        );
      } else {
        Alert.alert(
          "No devices",
          "No registered push tokens for this account. Tap 'Register this device' first.",
        );
      }
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setTesting(false);
    }
  };

  if (loading || !prefs) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Notifications", headerShown: true }} />
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  const renderRow = (def: EventDef) => {
    const enabled = !!prefs[def.key];
    const isBusy  = busy === def.key;
    return (
      <View key={String(def.key)} style={styles.row}>
        <View style={[styles.rowIcon, { backgroundColor: def.tone + "18" }]}>
          <PhIcon name={def.icon} size={18} color={def.tone} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.rowTitle}>{def.title}</Text>
          <Text style={styles.rowDesc}>{def.desc}</Text>
        </View>
        <Switch
          value={enabled}
          disabled={isBusy}
          onValueChange={(v) => toggle(def.key, v)}
          trackColor={{ false: "#D1D5DB", true: def.tone }}
          thumbColor="#fff"
        />
      </View>
    );
  };

  const cachedToken = getCachedPushToken();
  const masterPushOn = !!prefs.channel_push;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Notifications", headerShown: true }} />
      <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 60 }}>
        {/* Status / device card */}
        <View style={styles.statusCard}>
          <View style={styles.statusRow}>
            <PhIcon
              name={tokens.count > 0 ? "checkmark-circle" : "alert-circle"}
              size={22}
              color={tokens.count > 0 ? "#10B981" : "#F59E0B"}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.statusTitle}>
                {tokens.count > 0
                  ? `${tokens.count} device${tokens.count === 1 ? "" : "s"} registered`
                  : "No devices registered"}
              </Text>
              <Text style={styles.statusSub}>
                {tokens.count > 0
                  ? "You'll receive push notifications on these devices."
                  : "Tap below to register this device for push."}
              </Text>
            </View>
          </View>
          <View style={styles.statusBtnRow}>
            <TouchableOpacity style={styles.btnGhost} onPress={reRegister}>
              <PhIcon name="refresh" size={14} color="#1F4FBF" />
              <Text style={styles.btnGhostText}>
                {cachedToken ? "Re-register" : "Register this device"}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.btnPrimary, (testing || tokens.count === 0) && { opacity: 0.5 }]}
              onPress={testPush}
              disabled={testing || tokens.count === 0}
            >
              {testing ? <ActivityIndicator color="#fff" /> : (
                <>
                  <PhIcon name="paper-plane" size={14} color="#fff" />
                  <Text style={styles.btnPrimaryText}>Send test</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        </View>

        <Text style={styles.sectionHeading}>📡 Delivery channels</Text>
        <View style={styles.card}>
          {CHANNEL_DEFS.map(renderRow)}
        </View>

        <Text style={styles.sectionHeading}>⚙️ Operational events</Text>
        <View style={[styles.card, !masterPushOn && { opacity: 0.55 }]}>
          {!masterPushOn && (
            <Text style={styles.mutedNote}>
              ⚠️ Push channel is OFF — these events are muted regardless of toggles below.
            </Text>
          )}
          {OPS_DEFS.map(renderRow)}
        </View>

        <Text style={styles.sectionHeading}>👤 Account & billing</Text>
        <View style={styles.card}>
          {ACCOUNT_DEFS.map(renderRow)}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center" },

  statusCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 12,
  },
  statusRow:    { flexDirection: "row", alignItems: "center", gap: 10 },
  statusTitle:  { fontSize: 14, fontWeight: "800", color: "#111827" },
  statusSub:    { fontSize: 11.5, color: "#6B7280", marginTop: 2 },
  statusBtnRow: { flexDirection: "row", gap: 8, marginTop: 12 },
  btnGhost: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5,
    paddingVertical: 10, borderRadius: 8,
    backgroundColor: "#EFF6FF", borderWidth: 1, borderColor: "#BFDBFE",
  },
  btnGhostText: { color: "#1F4FBF", fontSize: 12, fontWeight: "800" },
  btnPrimary: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5,
    paddingVertical: 10, borderRadius: 8, backgroundColor: "#10B981",
  },
  btnPrimaryText: { color: "#fff", fontSize: 12, fontWeight: "800" },

  sectionHeading: { fontSize: 12, fontWeight: "800", color: "#6B7280", marginTop: 10, marginBottom: 6, marginLeft: 2, letterSpacing: 0.4 },
  card: {
    backgroundColor: "#fff", borderRadius: 12, padding: 4,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  row: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 11, paddingHorizontal: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  rowIcon: { width: 36, height: 36, borderRadius: 10, alignItems: "center", justifyContent: "center" },
  rowTitle: { fontSize: 13.5, fontWeight: "700", color: "#111827" },
  rowDesc:  { fontSize: 11.5, color: "#6B7280", marginTop: 2, lineHeight: 15 },
  mutedNote: {
    fontSize: 11, fontWeight: "700", color: "#B45309",
    backgroundColor: "#FFFBEB", paddingHorizontal: 10, paddingVertical: 8,
    margin: 6, borderRadius: 8,
  },
});
