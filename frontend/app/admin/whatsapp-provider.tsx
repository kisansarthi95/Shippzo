/**
 * Admin → WhatsApp Provider (Phase F5.5 — Minimal 2-Field Config)
 * ================================================================
 * Per operator directive (2026-06-27): "Remove Template Management,
 * Preview, Generator, and automatic field-mapping UI entirely.
 * Replace with just Base URL and API Token. On send, POST payload
 * to Base URL with API Token in header. No dropdowns, no OTP
 * auto-mapping, no auto-field system."
 *
 * This screen is intentionally BARE — three inputs on the whole
 * page:
 *   1. Enable toggle
 *   2. Base URL
 *   3. API Token
 * and a Save button. That's it.
 *
 * The complex event-per-template UI (Phase F4.9 template picker,
 * F5.2 warnings/cheatsheet/recent-requests, F5.3 advanced toggle
 * with fields-to-send/variable-mapping/custom-fields) has been
 * DELETED from this screen. All that code is preserved in git
 * history at commit 4e3f (right before Phase F5.5) — retrieve
 * with `git show 4e3f:frontend/app/admin/whatsapp-provider.tsx`
 * if a future integration needs it.
 *
 * Backend continues to fire events (dispatch_event) on OTP + stage
 * changes — it simply POSTs every context field to the configured
 * Base URL with `Authorization: Bearer <token>` header. The provider
 * (FlowConnect / WATI / etc.) picks whichever variables its
 * template references.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";
import { useAuth } from "../../lib/auth";

type ProviderConfig = {
  provider:              string;
  enabled:               boolean;
  api_token:             string;
  api_token_masked?:     string;
  base_url:              string;
  endpoint_template?:    string;
  default_country_code?: string;
};

export default function AdminWhatsAppProviderScreen() {
  const router                        = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [loading, setLoading]       = useState(true);
  const [saving,  setSaving]        = useState(false);
  const [error,   setError]         = useState("");

  const [enabled,  setEnabled]      = useState(false);
  const [baseUrl,  setBaseUrl]      = useState("");
  const [apiToken, setApiToken]     = useState("");
  const [tokenMask, setTokenMask]   = useState("");  // masked hint when user hasn't edited

  const load = useCallback(async () => {
    setError("");
    try {
      const r = await Api.adminWppGetConfig();
      const c: ProviderConfig = r.config;
      setEnabled(!!c.enabled);
      setBaseUrl(c.base_url || "");
      // Preserve the masked hint but blank the input so the operator
      // sees a clean field. The backend keeps the stored token unless
      // they type a new one (empty string on save = "no change").
      setApiToken("");
      setTokenMask(c.api_token_masked || "");
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Could not load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.replace("/(auth)/login" as any); return; }
    if (!user.is_admin) {
      Alert.alert("Admin only", "This screen is restricted to administrators.");
      router.back();
      return;
    }
    load();
  }, [authLoading, user, router, load]);

  const save = async () => {
    if (!baseUrl.trim()) {
      Alert.alert("Base URL required", "Paste the full WhatsApp provider URL (e.g. https://…/execute).");
      return;
    }
    setSaving(true);
    try {
      const patch: any = {
        enabled,
        base_url:          baseUrl.trim(),
        // Only send the token when the operator typed a new value —
        // empty string means "keep existing".
        endpoint_template: baseUrl.trim(),
      };
      if (apiToken.trim()) patch.api_token = apiToken.trim();
      const r = await Api.adminWppUpdateConfig(patch);
      const c: ProviderConfig = r.config;
      setEnabled(!!c.enabled);
      setBaseUrl(c.base_url || "");
      setApiToken("");
      setTokenMask(c.api_token_masked || "");
      Alert.alert("Saved ✓", "WhatsApp provider settings updated.");
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "WhatsApp Provider", headerShown: true }} />
        <ActivityIndicator color={colors.primary} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <Stack.Screen options={{ title: "WhatsApp Provider", headerShown: true }} />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 20, paddingBottom: 40 }}
        keyboardShouldPersistTaps="handled"
      >
        {!!error && (
          <View style={styles.errorBox}>
            <Text style={styles.errorTxt}>{error}</Text>
          </View>
        )}

        <View style={styles.headerCard}>
          <View style={styles.headerRow}>
            <PhIcon name="logo-whatsapp" size={24} color="#25D366" />
            <Text style={styles.headerTitle}>WhatsApp Provider</Text>
          </View>
          <Text style={styles.headerSub}>
            When events fire (OTP, order updates), the payload is POSTed
            to your Base URL with the API Token in the{" "}
            <Text style={{ fontWeight: "700" }}>Authorization</Text> header.
            No template management, no field mapping — your provider's
            template picks the variables it needs.
          </Text>
        </View>

        {/* Enable toggle */}
        <TouchableOpacity
          activeOpacity={0.85}
          onPress={() => setEnabled((v) => !v)}
          style={styles.enableRow}
          testID="enable-toggle"
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.enableTitle}>Enable Provider</Text>
            <Text style={styles.enableSub}>
              {enabled ? "Active — events will send WhatsApp messages" : "Disabled — no messages will be sent"}
            </Text>
          </View>
          <View style={[styles.toggleTrack, enabled && { backgroundColor: colors.primary }]}>
            <View style={[styles.toggleThumb, enabled && { transform: [{ translateX: 20 }] }]} />
          </View>
        </TouchableOpacity>

        {/* Base URL */}
        <Text style={styles.label}>Base URL</Text>
        <Text style={styles.hint}>
          Full endpoint URL from your provider dashboard. Include the
          automation ID / path — this URL is used as-is.
        </Text>
        <TextInput
          value={baseUrl}
          onChangeText={setBaseUrl}
          placeholder="https://login.flowconnect.ai/api/automations/…/execute"
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.input}
          testID="base-url-input"
        />

        {/* API Token */}
        <Text style={styles.label}>API Token</Text>
        <Text style={styles.hint}>
          Sent in the{" "}
          <Text style={styles.code}>Authorization: Bearer …</Text> header on
          every request.{tokenMask ? ` Current: ${tokenMask} (leave blank to keep)` : ""}
        </Text>
        <TextInput
          value={apiToken}
          onChangeText={setApiToken}
          placeholder={tokenMask ? "•••• (leave blank to keep existing)" : "Paste your API token"}
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry={apiToken.length > 0}
          style={styles.input}
          testID="api-token-input"
        />

        {/* Save */}
        <TouchableOpacity
          onPress={save}
          disabled={saving}
          style={[styles.saveBtn, saving && { opacity: 0.6 }]}
          testID="save-btn"
        >
          {saving ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <>
              <PhIcon name="save-outline" size={16} color="#fff" />
              <Text style={styles.saveTxt}>Save</Text>
            </>
          )}
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:  { flex: 1, backgroundColor: colors.background },
  center:{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },

  headerCard: {
    backgroundColor: "#fff", borderRadius: 14, padding: 16,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 16,
  },
  headerRow:   { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 6 },
  headerTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  headerSub:   { fontSize: 12, color: "#64748B", lineHeight: 18 },

  enableRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 20,
  },
  enableTitle: { fontSize: 14, fontWeight: "700", color: colors.text },
  enableSub:   { fontSize: 11, color: "#64748B", marginTop: 2 },
  toggleTrack: {
    width: 48, height: 28, borderRadius: 14,
    backgroundColor: "#D1D5DB", justifyContent: "center", padding: 4,
  },
  toggleThumb: {
    width: 20, height: 20, borderRadius: 10, backgroundColor: "#fff",
    shadowColor: "#000", shadowOpacity: 0.2, shadowOffset: { width: 0, height: 1 }, shadowRadius: 2, elevation: 2,
  },

  label: { fontSize: 13, fontWeight: "700", color: colors.text, marginTop: 4, marginBottom: 4 },
  hint:  { fontSize: 11, color: "#64748B", marginBottom: 8, lineHeight: 16 },
  code:  { fontFamily: "monospace", fontSize: 10.5, color: "#334155" },
  input: {
    backgroundColor: "#fff", borderColor: "#E5E7EB", borderWidth: 1,
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 12,
    fontSize: 13, color: colors.text, marginBottom: 16,
  },

  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: colors.primary, borderRadius: 12, paddingVertical: 14, marginTop: 8,
  },
  saveTxt: { color: "#fff", fontSize: 14, fontWeight: "800" },

  errorBox: {
    backgroundColor: "#FEF2F2", borderColor: "#FCA5A5", borderWidth: 1,
    borderRadius: 10, padding: 12, marginBottom: 12,
  },
  errorTxt: { color: "#991B1B", fontSize: 12 },
});
