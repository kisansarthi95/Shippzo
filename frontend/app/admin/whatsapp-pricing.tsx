/**
 * Admin — WhatsApp Manual Messaging Pricing (Phase-14).
 *
 * Lets the admin set how many credits are charged when a user taps
 * the "Send WhatsApp" button on a shipment (delivery confirmation,
 * dispatch notification, etc.). Pricing is per-plan so admins can
 * keep Free Trial at 0 and charge more on paid tiers.
 *
 * NOTE: This is for MANUAL sends — the user opens WhatsApp via a
 * wa.me deeplink and hits send themselves. We just book-keep the
 * debit. A future phase will layer a true API gateway on top of the
 * same pricing table.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type PlanKey = "free_trial" | "silver" | "gold" | "platinum";

type Payload = {
  order: PlanKey[];
  defaults: { enabled: boolean; rates: Record<PlanKey, number> };
  current:  { enabled: boolean; rates: Record<PlanKey, number> };
};

const PLAN_META: Record<PlanKey, { label: string; color: string; accent: string }> = {
  free_trial: { label: "Free Trial", color: "#7C3AED", accent: "#F5F3FF" },
  silver:     { label: "Silver",     color: "#475569", accent: "#F1F5F9" },
  gold:       { label: "Gold",       color: "#B45309", accent: "#FEF3C7" },
  platinum:   { label: "Platinum",   color: "#1E3A8A", accent: "#DBEAFE" },
};

const snapshotOf = (enabled: boolean, rates: Record<PlanKey, number>) =>
  JSON.stringify({
    enabled,
    rates: Object.fromEntries(
      (Object.keys(rates) as PlanKey[]).sort().map((k) => [k, Number(rates[k]) || 0]),
    ),
  });

export default function AdminWhatsAppPricingScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [rates,   setRates]   = useState<Record<PlanKey, number> | null>(null);
  const [defaults, setDefaults] = useState<{ enabled: boolean; rates: Record<PlanKey, number> } | null>(null);
  const [originalSnap, setOriginalSnap] = useState("");

  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only admin can edit messaging pricing.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const r = await api.get<Payload>("/admin/whatsapp-pricing");
      setEnabled(r.data.current.enabled);
      setRates(r.data.current.rates);
      setDefaults(r.data.defaults);
      setOriginalSnap(snapshotOf(r.data.current.enabled, r.data.current.rates));
    } catch (e: any) {
      Alert.alert(
        "Load failed",
        e?.response?.data?.detail || e?.message || "Could not load pricing.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(
    () => !!rates && snapshotOf(enabled, rates) !== originalSnap,
    [enabled, rates, originalSnap],
  );

  const setRate = (plan: PlanKey, raw: string) => {
    if (!rates) return;
    // Allow fractional (e.g. 0.5). Strip anything that's not digits or dot.
    const clean = raw.replace(/[^0-9.]/g, "");
    // Keep only the first dot.
    const firstDot = clean.indexOf(".");
    const normalised =
      firstDot === -1
        ? clean
        : clean.slice(0, firstDot + 1) + clean.slice(firstDot + 1).replace(/\./g, "");
    const val = normalised === "" || normalised === "." ? 0 : Number(normalised);
    setRates({ ...rates, [plan]: isNaN(val) ? 0 : val });
  };

  const resetPlan = (plan: PlanKey) => {
    if (!rates || !defaults) return;
    setRates({ ...rates, [plan]: defaults.rates[plan] ?? 0 });
  };

  const handleSave = async () => {
    if (!rates) return;
    try {
      setSaving(true);
      const r = await api.put<Payload>("/admin/whatsapp-pricing", {
        enabled,
        plans: Object.fromEntries(
          (Object.keys(rates) as PlanKey[]).map((k) => [
            k, { per_message_credits: Number(rates[k]) || 0 },
          ]),
        ),
      });
      setEnabled(r.data.current.enabled);
      setRates(r.data.current.rates);
      setOriginalSnap(snapshotOf(r.data.current.enabled, r.data.current.rates));
      Alert.alert(
        "Saved",
        enabled
          ? "Charging is now ON. Users will see the per-message credit notice."
          : "Pricing saved. Charging is OFF — no debit will happen.",
      );
    } catch (e: any) {
      Alert.alert(
        "Save failed",
        e?.response?.data?.detail || e?.message || "Could not save pricing.",
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading || !rates || !defaults) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <Header onBack={() => router.back()} />
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Loading pricing…</Text>
        </View>
      </SafeAreaView>
    );
  }

  const planKeys = (Object.keys(PLAN_META) as PlanKey[]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Header onBack={() => router.back()} />

      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={{ paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.introBox}>
            <Ionicons name="logo-whatsapp" size={20} color="#25D366" />
            <Text style={styles.introText}>
              Charge users a per-message credit when they tap "Send WhatsApp"
              on a shipment. Only applies to MANUAL sends — no actual message
              is sent from your servers. Leave at 0 to keep messages free.
            </Text>
          </View>

          {/* Master toggle */}
          <View style={styles.toggleCard}>
            <View style={{ flex: 1 }}>
              <Text style={styles.toggleTitle}>Enable charging</Text>
              <Text style={styles.toggleSub}>
                {enabled
                  ? "Credits WILL be deducted per manual WhatsApp send."
                  : "No credits are deducted (messages stay free)."}
              </Text>
            </View>
            <Switch
              value={enabled}
              onValueChange={setEnabled}
              trackColor={{ false: "#D1D5DB", true: "#25D366" }}
              thumbColor="#fff"
              testID="wa-pricing-enable-toggle"
            />
          </View>

          {/* Per-plan rates */}
          {planKeys.map((planKey) => {
            const meta = PLAN_META[planKey];
            const rate = rates[planKey];
            const defRate = defaults.rates[planKey] ?? 0;
            const isOverride = Number(rate) !== Number(defRate);
            return (
              <View
                key={planKey}
                style={[styles.planCard, { borderLeftColor: meta.color }]}
              >
                <View style={styles.planHeaderRow}>
                  <View style={{ flex: 1 }}>
                    <View style={styles.planTitleRow}>
                      <Text style={[styles.planName, { color: meta.color }]}>
                        {meta.label}
                      </Text>
                      {isOverride && <View style={styles.overrideDot} />}
                    </View>
                    <Text style={styles.planSub}>
                      Default: {defRate} credits/message
                    </Text>
                  </View>
                  <TouchableOpacity
                    onPress={() => resetPlan(planKey)}
                    style={styles.resetBtn}
                    hitSlop={6}
                  >
                    <Text style={styles.resetTxt}>Reset</Text>
                  </TouchableOpacity>
                </View>
                <View style={styles.inputRow}>
                  <TextInput
                    style={styles.input}
                    value={String(rate)}
                    onChangeText={(v) => setRate(planKey, v)}
                    keyboardType="decimal-pad"
                    placeholder="0"
                    placeholderTextColor="#9CA3AF"
                    testID={`wa-pricing-input-${planKey}`}
                  />
                  <Text style={styles.suffix}>credits per message</Text>
                </View>
                {enabled && Number(rate) > 0 && (
                  <Text style={styles.exampleText}>
                    10 messages = {(Number(rate) * 10).toFixed(2)} credits
                  </Text>
                )}
              </View>
            );
          })}
        </ScrollView>

        {/* Sticky Save bar */}
        <View style={styles.saveBar}>
          <Text style={styles.saveStatus}>
            {dirty ? "Unsaved changes" : "All saved"}
          </Text>
          <TouchableOpacity
            onPress={handleSave}
            disabled={!dirty || saving}
            style={[
              styles.saveBtn,
              (!dirty || saving) && styles.saveBtnDisabled,
            ]}
            testID="save-wa-pricing"
          >
            {saving ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <Ionicons name="save-outline" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>Save</Text>
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Header({ onBack }: { onBack: () => void }) {
  return (
    <View style={styles.header}>
      <TouchableOpacity onPress={onBack} style={styles.backBtn} hitSlop={10}>
        <Ionicons name="chevron-back" size={24} color={colors.text} />
      </TouchableOpacity>
      <Text style={styles.headerTitle}>WhatsApp Pricing</Text>
      <View style={{ width: 36 }} />
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  backBtn: { padding: 4, width: 36 },
  headerTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  loadingBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10 },
  loadingText: { color: "#6B7280", fontSize: 13 },

  introBox: {
    flexDirection: "row",
    gap: 10,
    padding: 12,
    marginHorizontal: 12,
    marginTop: 12,
    backgroundColor: "#F0FDF4",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#BBF7D0",
  },
  introText: { flex: 1, fontSize: 12, color: "#14532D", lineHeight: 17 },

  toggleCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    marginHorizontal: 12,
    marginTop: 14,
    padding: 14,
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  toggleTitle: { fontSize: 14, fontWeight: "800", color: colors.text },
  toggleSub: { fontSize: 11.5, color: "#6B7280", marginTop: 3, lineHeight: 16 },

  planCard: {
    marginHorizontal: 12,
    marginTop: 12,
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderLeftWidth: 4,
    padding: 14,
  },
  planHeaderRow: { flexDirection: "row", alignItems: "flex-start" },
  planTitleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  planName: { fontSize: 16, fontWeight: "800" },
  planSub: { fontSize: 11.5, color: "#6B7280", marginTop: 2 },
  overrideDot: {
    width: 6, height: 6, borderRadius: 3, backgroundColor: "#F59E0B",
  },
  resetBtn: {
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#F3F4F6", borderRadius: 8,
  },
  resetTxt: { fontSize: 11, fontWeight: "700", color: "#6B7280" },

  inputRow: {
    flexDirection: "row", alignItems: "center", gap: 10, marginTop: 12,
  },
  input: {
    width: 90,
    height: 40,
    borderWidth: 1,
    borderColor: "#D1D5DB",
    borderRadius: 8,
    paddingHorizontal: 10,
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
    textAlign: "center",
    backgroundColor: "#F9FAFB",
  },
  suffix: { fontSize: 12, color: "#6B7280", fontWeight: "600" },
  exampleText: {
    fontSize: 11,
    color: "#047857",
    fontWeight: "700",
    marginTop: 8,
    fontStyle: "italic",
  },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 12,
    paddingBottom: Platform.OS === "ios" ? 24 : 12,
    backgroundColor: "#fff",
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  saveStatus: { fontSize: 12, color: "#6B7280", fontWeight: "600" },
  saveBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: "#25D366",
    paddingHorizontal: 22,
    paddingVertical: 12,
    borderRadius: 10,
  },
  saveBtnDisabled: { backgroundColor: "#D1D5DB" },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
