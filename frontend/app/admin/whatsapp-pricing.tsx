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
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type PlanKey = "free_trial" | "silver" | "gold" | "platinum";

type Payload = {
  order: PlanKey[];
  defaults: { enabled: boolean; rates: Record<PlanKey, number> };
  current:  {
    enabled: boolean;
    rates: Record<PlanKey, number>;
    // Phase-15 extensions
    ai_generation_rates?: Record<PlanKey, number>;
    daily_limit?: number;
    daily_warning_pct?: number;
    allow_override_after_limit?: boolean;
  };
};

const PLAN_META: Record<PlanKey, { label: string; color: string; accent: string }> = {
  free_trial: { label: "Free Trial", color: "#7C3AED", accent: "#F5F3FF" },
  silver:     { label: "Silver",     color: "#475569", accent: "#F1F5F9" },
  gold:       { label: "Gold",       color: "#B45309", accent: "#FEF3C7" },
  platinum:   { label: "Platinum",   color: "#1E3A8A", accent: "#DBEAFE" },
};

// Hard-coded AI gen rate defaults — kept here as fallback for the
// "Reset" button when the backend hasn't supplied an explicit default.
const AI_RATE_DEFAULTS: Record<PlanKey, number> = {
  free_trial: 2, silver: 1.5, gold: 1, platinum: 0.5,
};

const snapshotOf = (
  enabled: boolean,
  text: Record<PlanKey, string>,
  aiText: Record<PlanKey, string>,
  dailyLimit: string,
  warnPct: string,
  allowOverride: boolean,
) =>
  JSON.stringify({
    enabled,
    rates: Object.fromEntries(
      (Object.keys(text) as PlanKey[]).sort().map(
        (k) => [k, Number(text[k]) || 0],
      ),
    ),
    ai_rates: Object.fromEntries(
      (Object.keys(aiText) as PlanKey[]).sort().map(
        (k) => [k, Number(aiText[k]) || 0],
      ),
    ),
    daily_limit:   Number(dailyLimit) || 0,
    warn_pct:      Number(warnPct)    || 0,
    allow_override: !!allowOverride,
  });

export default function AdminWhatsAppPricingScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving,  setSaving]  = useState(false);
  const [enabled, setEnabled] = useState(false);
  // Phase-14 fix: keep raw text per field so partial input like "0."
  // survives a re-render (a pure number state would collapse "0." to
  // 0 and drop the dot before the user finishes typing "0.5").
  const [rateText, setRateText] = useState<Record<PlanKey, string> | null>(null);
  // Phase-15 state — AI generation rates + daily-limit knobs.
  const [aiRateText, setAiRateText] = useState<Record<PlanKey, string> | null>(null);
  const [dailyLimitText, setDailyLimitText] = useState<string>("50");
  const [warnPctText, setWarnPctText] = useState<string>("90");
  const [allowOverride, setAllowOverride] = useState<boolean>(true);
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
      // Convert numeric rates to string for input binding — the Number
      // → String round-trip on mount is safe; once the user edits we
      // preserve their literal keystrokes (incl. trailing ".").
      const asText: Record<PlanKey, string> = {
        free_trial: String(r.data.current.rates.free_trial ?? 0),
        silver:     String(r.data.current.rates.silver     ?? 0),
        gold:       String(r.data.current.rates.gold       ?? 0),
        platinum:   String(r.data.current.rates.platinum   ?? 0),
      };
      const aiSrc = r.data.current.ai_generation_rates || AI_RATE_DEFAULTS;
      const asAi: Record<PlanKey, string> = {
        free_trial: String(aiSrc.free_trial ?? AI_RATE_DEFAULTS.free_trial),
        silver:     String(aiSrc.silver     ?? AI_RATE_DEFAULTS.silver),
        gold:       String(aiSrc.gold       ?? AI_RATE_DEFAULTS.gold),
        platinum:   String(aiSrc.platinum   ?? AI_RATE_DEFAULTS.platinum),
      };
      const dl = String(r.data.current.daily_limit ?? 50);
      const wp = String(r.data.current.daily_warning_pct ?? 90);
      const ao = r.data.current.allow_override_after_limit ?? true;
      setRateText(asText);
      setAiRateText(asAi);
      setDailyLimitText(dl);
      setWarnPctText(wp);
      setAllowOverride(ao);
      setDefaults(r.data.defaults);
      setOriginalSnap(snapshotOf(r.data.current.enabled, asText, asAi, dl, wp, ao));
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
    () =>
      !!rateText &&
      !!aiRateText &&
      snapshotOf(enabled, rateText, aiRateText, dailyLimitText, warnPctText, allowOverride) !==
        originalSnap,
    [enabled, rateText, aiRateText, dailyLimitText, warnPctText, allowOverride, originalSnap],
  );

  // Generic helper — accepts only digits + at most one dot, preserves
  // trailing "." so the user can type "0." → "0.5" without the cursor
  // jumping.
  const cleanRate = (raw: string) => {
    const c = raw.replace(/[^0-9.]/g, "");
    const i = c.indexOf(".");
    return i === -1 ? c : c.slice(0, i + 1) + c.slice(i + 1).replace(/\./g, "");
  };

  const setRate = (plan: PlanKey, raw: string) => {
    if (!rateText) return;
    setRateText({ ...rateText, [plan]: cleanRate(raw) });
  };

  const setAiRate = (plan: PlanKey, raw: string) => {
    if (!aiRateText) return;
    setAiRateText({ ...aiRateText, [plan]: cleanRate(raw) });
  };

  const setDailyLimit = (raw: string) => {
    setDailyLimitText(raw.replace(/[^0-9]/g, "").slice(0, 5));
  };

  const setWarnPct = (raw: string) => {
    const c = raw.replace(/[^0-9]/g, "").slice(0, 3);
    setWarnPctText(c);
  };

  const resetPlan = (plan: PlanKey) => {
    if (!rateText || !defaults) return;
    setRateText({
      ...rateText,
      [plan]: String(defaults.rates[plan] ?? 0),
    });
  };

  const handleSave = async () => {
    if (!rateText || !aiRateText) return;
    // Validate daily-limit + warn-pct ranges before round-tripping.
    const dl = Number(dailyLimitText) || 0;
    const wp = Number(warnPctText) || 0;
    if (dl < 1 || dl > 10000) {
      Alert.alert("Invalid daily limit", "Daily limit must be between 1 and 10000.");
      return;
    }
    if (wp < 1 || wp > 100) {
      Alert.alert("Invalid warning %", "Warning threshold must be between 1 and 100.");
      return;
    }
    try {
      setSaving(true);
      const r = await api.put<Payload>("/admin/whatsapp-pricing", {
        enabled,
        plans: Object.fromEntries(
          (Object.keys(rateText) as PlanKey[]).map((k) => [
            k,
            {
              per_message_credits:    Number(rateText[k])   || 0,
              ai_generation_credits:  Number(aiRateText[k]) || 0,
            },
          ]),
        ),
        daily_limit:                dl,
        daily_warning_pct:          wp,
        allow_override_after_limit: allowOverride,
      });
      setEnabled(r.data.current.enabled);
      const asText: Record<PlanKey, string> = {
        free_trial: String(r.data.current.rates.free_trial ?? 0),
        silver:     String(r.data.current.rates.silver     ?? 0),
        gold:       String(r.data.current.rates.gold       ?? 0),
        platinum:   String(r.data.current.rates.platinum   ?? 0),
      };
      const aiSrc = r.data.current.ai_generation_rates || AI_RATE_DEFAULTS;
      const asAi: Record<PlanKey, string> = {
        free_trial: String(aiSrc.free_trial ?? AI_RATE_DEFAULTS.free_trial),
        silver:     String(aiSrc.silver     ?? AI_RATE_DEFAULTS.silver),
        gold:       String(aiSrc.gold       ?? AI_RATE_DEFAULTS.gold),
        platinum:   String(aiSrc.platinum   ?? AI_RATE_DEFAULTS.platinum),
      };
      const dlS = String(r.data.current.daily_limit ?? 50);
      const wpS = String(r.data.current.daily_warning_pct ?? 90);
      const ao  = r.data.current.allow_override_after_limit ?? true;
      setRateText(asText);
      setAiRateText(asAi);
      setDailyLimitText(dlS);
      setWarnPctText(wpS);
      setAllowOverride(ao);
      setOriginalSnap(snapshotOf(r.data.current.enabled, asText, asAi, dlS, wpS, ao));
      Alert.alert(
        "Saved",
        enabled
          ? "Charging is ON. AI generation cost & daily limit are live."
          : "Pricing saved. Manual-message charging is OFF (AI gen + daily limit still apply).",
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

  if (loading || !rateText || !aiRateText || !defaults) {
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
            <PhIcon name="logo-whatsapp" size={20} color="#25D366" />
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

          {/* Phase-15: Daily WhatsApp send limit (anti-block) */}
          <View style={styles.sectionHeader}>
            <PhIcon name="shield-checkmark-outline" size={16} color="#1F4FBF" />
            <Text style={styles.sectionHeaderText}>Anti-Block Daily Limit</Text>
          </View>
          <View style={styles.dailyCard}>
            <Text style={styles.dailyHint}>
              Cap how many WhatsApp messages a single user can fire in one day
              to keep their personal number safe from WhatsApp's spam-block
              policy. Counter resets every midnight (IST).
            </Text>

            <View style={styles.dailyRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.dailyLabel}>Max messages per day</Text>
                <Text style={styles.dailySub}>1 to 10000</Text>
              </View>
              <TextInput
                style={styles.smallInput}
                value={dailyLimitText}
                onChangeText={setDailyLimit}
                keyboardType="numeric"
                placeholder="50"
                placeholderTextColor="#9CA3AF"
                selectTextOnFocus
                testID="wa-pricing-daily-limit"
              />
            </View>

            <View style={styles.dailyRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.dailyLabel}>Soft warning at</Text>
                <Text style={styles.dailySub}>
                  Warn the user when they have used this % of the daily limit
                </Text>
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <TextInput
                  style={styles.smallInput}
                  value={warnPctText}
                  onChangeText={setWarnPct}
                  keyboardType="numeric"
                  placeholder="90"
                  placeholderTextColor="#9CA3AF"
                  selectTextOnFocus
                  testID="wa-pricing-warn-pct"
                />
                <Text style={styles.suffix}>%</Text>
              </View>
            </View>

            <View style={[styles.dailyRow, { alignItems: "center" }]}>
              <View style={{ flex: 1 }}>
                <Text style={styles.dailyLabel}>
                  {allowOverride
                    ? "Allow user override after limit"
                    : "Hard block at limit (no override)"}
                </Text>
                <Text style={styles.dailySub}>
                  {allowOverride
                    ? "User sees a confirm modal at the limit and can choose to keep sending."
                    : "Users CANNOT bypass the limit — sending is blocked until tomorrow."}
                </Text>
              </View>
              <Switch
                value={allowOverride}
                onValueChange={setAllowOverride}
                trackColor={{ false: "#D1D5DB", true: "#1F4FBF" }}
                thumbColor="#fff"
                testID="wa-pricing-allow-override"
              />
            </View>
          </View>

          {/* Phase-15: AI generation rates per plan */}
          <View style={styles.sectionHeader}>
            <PhIcon name="sparkles-outline" size={16} color="#6B5BFF" />
            <Text style={styles.sectionHeaderText}>
              AI Template Generation — Per Plan
            </Text>
          </View>
          <View style={styles.aiHintBox}>
            <Text style={styles.aiHintText}>
              Charged once per "Generate" tap in WhatsApp Templates settings
              (returns 9 ready-to-use variants — 3 languages × 3 variants).
              Refunded automatically if the AI call fails.
            </Text>
          </View>
          {(Object.keys(PLAN_META) as PlanKey[]).map((planKey) => {
            const meta = PLAN_META[planKey];
            const text = aiRateText![planKey];
            const numRate = Number(text) || 0;
            const def = AI_RATE_DEFAULTS[planKey];
            const isOverride = numRate !== def;
            return (
              <View
                key={`ai-${planKey}`}
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
                      Default: {def} credits per generation
                    </Text>
                  </View>
                  <TouchableOpacity
                    onPress={() =>
                      aiRateText &&
                      setAiRateText({ ...aiRateText, [planKey]: String(def) })
                    }
                    style={styles.resetBtn}
                    hitSlop={6}
                  >
                    <Text style={styles.resetTxt}>Reset</Text>
                  </TouchableOpacity>
                </View>
                <View style={styles.inputRow}>
                  <TextInput
                    style={styles.input}
                    value={text}
                    onChangeText={(v) => setAiRate(planKey, v)}
                    keyboardType="numeric"
                    inputMode="decimal"
                    placeholder="0"
                    placeholderTextColor="#9CA3AF"
                    selectTextOnFocus
                    testID={`wa-ai-rate-${planKey}`}
                  />
                  <Text style={styles.suffix}>credits per generation</Text>
                </View>
                {numRate > 0 && (
                  <Text style={styles.exampleText}>
                    10 generations = {(numRate * 10).toFixed(2)} credits
                  </Text>
                )}
              </View>
            );
          })}

          {/* Per-plan rates */}
          <View style={styles.sectionHeader}>
            <PhIcon name="logo-whatsapp" size={16} color="#25D366" />
            <Text style={styles.sectionHeaderText}>
              Per-Message Send — Per Plan
            </Text>
          </View>
          {planKeys.map((planKey) => {
            const meta = PLAN_META[planKey];
            const text = rateText[planKey];
            const numRate = Number(text) || 0;
            const defRate = defaults.rates[planKey] ?? 0;
            const isOverride = numRate !== Number(defRate);
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
                    value={text}
                    onChangeText={(v) => setRate(planKey, v)}
                    // `numeric` (not decimal-pad) is the most reliable
                    // cross-device keyboard that shows a visible "."
                    // key on both iOS and Android 13+ — some OEM Android
                    // skins hide the decimal key on decimal-pad.
                    keyboardType="numeric"
                    inputMode="decimal"
                    placeholder="0"
                    placeholderTextColor="#9CA3AF"
                    selectTextOnFocus
                    testID={`wa-pricing-input-${planKey}`}
                  />
                  <Text style={styles.suffix}>credits per message</Text>
                </View>
                {enabled && numRate > 0 && (
                  <Text style={styles.exampleText}>
                    10 messages = {(numRate * 10).toFixed(2)} credits
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
                <PhIcon name="save-outline" size={18} color="#fff" />
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
        <PhIcon name="chevron-back" size={24} color={colors.text} />
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

  // Phase-15 — section dividers + daily-limit + AI hint card.
  sectionHeader: {
    flexDirection: "row", alignItems: "center", gap: 8,
    marginHorizontal: 16, marginTop: 24, marginBottom: 4,
  },
  sectionHeaderText: {
    fontSize: 13, fontWeight: "800",
    color: colors.text, letterSpacing: 0.3, textTransform: "uppercase",
  },
  dailyCard: {
    marginHorizontal: 12,
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    padding: 14,
  },
  dailyHint: {
    fontSize: 11.5, color: "#374151", lineHeight: 16, marginBottom: 10,
  },
  dailyRow: {
    flexDirection: "row", alignItems: "flex-start",
    gap: 10, paddingVertical: 8,
    borderTopWidth: 1, borderTopColor: "#F3F4F6",
  },
  dailyLabel: { fontSize: 13, fontWeight: "700", color: colors.text },
  dailySub: { fontSize: 11, color: "#6B7280", marginTop: 2, lineHeight: 15 },
  smallInput: {
    width: 70, height: 36,
    borderWidth: 1, borderColor: "#D1D5DB", borderRadius: 8,
    paddingHorizontal: 10, fontSize: 14, fontWeight: "700",
    color: colors.text, textAlign: "center",
    backgroundColor: "#F9FAFB",
  },
  aiHintBox: {
    marginHorizontal: 12, marginTop: 4, marginBottom: 4,
    padding: 10, backgroundColor: "#EEF2FF",
    borderWidth: 1, borderColor: "#C7D2FE",
    borderRadius: 10,
  },
  aiHintText: { fontSize: 11.5, color: "#3730A3", lineHeight: 16 },

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
