/**
 * Plans & Billing screen.
 *
 * Shows the 4-tier subscription catalogue with beautiful mobile-first cards.
 * Each card renders (per product owner spec):
 *   - Plan name (+ 🚀 for Platinum)
 *   - "Feel" (1-line positioning)
 *   - "Purpose" (who it's for)
 *   - Limit bullets
 *   - Price badge
 *   - Upgrade CTA
 *
 * Gold carries a "⭐ Most Popular" ribbon; Platinum carries a rocket badge.
 * The user's current plan is highlighted with a "Your Current Plan" pill
 * and its CTA becomes disabled.
 *
 * MOCKED PAYMENT: Phase-3a's `/api/plans/upgrade` just flips the plan
 * on the server — Razorpay lands in Phase-4. We show a clear "Mocked —
 * no charge" banner so QA testers aren't surprised.
 */
import React, { useCallback, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter, useFocusEffect } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Api, PlanKey, PlanSpec, UsageSummary } from "../lib/api";
import { colors } from "../lib/theme";
import { useAuth } from "../lib/auth";

type PalKey = PlanKey;
const PAL: Record<PalKey, { bg: string; border: string; accent: string; chipBg: string; chipTxt: string }> = {
  free_trial: { bg: "#FAF5FF", border: "#DDD6FE", accent: "#7C3AED", chipBg: "#7C3AED", chipTxt: "#fff" },
  silver:     { bg: "#F8FAFC", border: "#CBD5E1", accent: "#475569", chipBg: "#475569", chipTxt: "#fff" },
  gold:       { bg: "#FFFBEB", border: "#F59E0B", accent: "#B45309", chipBg: "#F59E0B", chipTxt: "#fff" },
  platinum:   { bg: "#EFF6FF", border: "#3B82F6", accent: "#1E3A8A", chipBg: "#1E3A8A", chipTxt: "#fff" },
};

export default function PlansScreen() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [plans, setPlans] = useState<PlanSpec[]>([]);
  const [current, setCurrent] = useState<PlanKey>("free_trial");
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<PlanKey | null>(null);

  const load = useCallback(async () => {
    try {
      const [pl, u] = await Promise.all([Api.listPlans(), Api.myUsage()]);
      setPlans(pl.plans);
      setCurrent(pl.current);
      setUsage(u);
    } catch (e: any) {
      Alert.alert("Could not load plans", e?.message || "Please try again");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      setLoading(true);
      load().catch(() => {});
    }, [load]),
  );

  const doUpgrade = async (key: PlanKey) => {
    const plan = plans.find((p) => p.key === key);
    if (!plan) return;
    const confirmMsg =
      Platform.OS === "web"
        ? `Switch to ${plan.name}? (No charge — payment integration comes in Phase 4.)`
        : `This will switch your plan to ${plan.name}. Payment integration is not yet live — no money will be charged.`;
    const proceed = async () => {
      try {
        setBusyKey(key);
        await Api.upgradePlan(key);
        await refresh().catch(() => {});
        await load();
        Alert.alert(
          "Plan updated",
          `You're now on the ${plan.name} plan. Razorpay payment will be wired up in the next phase.`,
        );
      } catch (e: any) {
        Alert.alert("Upgrade failed", e?.response?.data?.detail || e?.message || "Please try again");
      } finally {
        setBusyKey(null);
      }
    };
    // Alert.alert on web doesn't have buttons — use window.confirm fallback
    if (Platform.OS === "web") {
      // eslint-disable-next-line no-alert
      if (typeof window !== "undefined" && window.confirm && window.confirm(confirmMsg)) {
        proceed();
      }
      return;
    }
    Alert.alert(`Switch to ${plan.name}?`, confirmMsg, [
      { text: "Cancel", style: "cancel" },
      { text: "Confirm", onPress: proceed },
    ]);
  };

  const headerRight = useMemo(
    () => () => (
      <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
        <Ionicons name="close" size={24} color={colors.text} />
      </TouchableOpacity>
    ),
    [router],
  );

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Plans & Billing",
          headerRight,
          headerBackVisible: false,
          headerStyle: { backgroundColor: colors.background },
        }}
      />
      <ScrollView
        testID="plans-scroll"
        contentContainerStyle={{ padding: 16, paddingBottom: 48 }}
      >
        {/* Mock-payment banner */}
        <View style={styles.mockBanner}>
          <Ionicons name="information-circle-outline" size={16} color="#92400E" />
          <Text style={styles.mockBannerTxt}>
            Payments coming soon. All plan switches are free for now.
          </Text>
        </View>

        {/* Usage summary */}
        {usage ? (
          <View style={styles.usageBox}>
            <Text style={styles.usageTitle}>Your current usage</Text>
            <Text style={styles.usageLine}>
              <Text style={styles.usageStrong}>{usage.labels_used}</Text>
              {" of "}
              <Text style={styles.usageStrong}>{usage.label_cap}</Text>
              {" labels used"}
              {usage.period === "trial" && usage.trial_days_left != null
                ? ` · ${usage.trial_days_left} day${usage.trial_days_left === 1 ? "" : "s"} left`
                : null}
              {usage.period === "month"
                ? ` this month`
                : null}
            </Text>
            {usage.daily_cap ? (
              <Text style={styles.usageLine}>
                Today: <Text style={styles.usageStrong}>{usage.today_used ?? 0}</Text> of <Text style={styles.usageStrong}>{usage.daily_cap}</Text>
              </Text>
            ) : null}
          </View>
        ) : null}

        {loading ? (
          <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
        ) : (
          plans.map((p) => (
            <PlanCard
              key={p.key}
              plan={p}
              isCurrent={p.key === current}
              busy={busyKey === p.key}
              onUpgrade={() => doUpgrade(p.key)}
            />
          ))
        )}

        <Text style={styles.footnote}>
          Need a custom volume plan? Write to us at support@your-brand.app.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

// ------------ Plan Card ---------------------------------------------------

function PlanCard({
  plan, isCurrent, busy, onUpgrade,
}: {
  plan: PlanSpec;
  isCurrent: boolean;
  busy: boolean;
  onUpgrade: () => void;
}) {
  const pal = PAL[plan.key];
  const showBadge = plan.badge != null;
  const bullets = buildBullets(plan);

  return (
    <View
      style={[
        styles.card,
        {
          backgroundColor: pal.bg,
          borderColor: isCurrent ? pal.accent : pal.border,
          borderWidth: isCurrent ? 2 : 1.5,
        },
      ]}
    >
      {showBadge && (
        <View style={[styles.badge, { backgroundColor: pal.chipBg }]}>
          {plan.badge === "Most Popular" ? (
            <>
              <Ionicons name="star" size={11} color="#fff" />
              <Text style={styles.badgeTxt}>Most Popular</Text>
            </>
          ) : (
            <Text style={styles.badgeTxt}>{plan.badge}</Text>
          )}
        </View>
      )}
      {isCurrent && (
        <View style={styles.currentPill}>
          <Ionicons name="checkmark-circle" size={12} color="#065F46" />
          <Text style={styles.currentPillTxt}>Your Current Plan</Text>
        </View>
      )}

      <Text style={[styles.planName, { color: pal.accent }]}>{plan.name}</Text>
      <Text style={styles.planFeel}>{plan.feel}</Text>
      <Text style={styles.planPurpose}>{plan.purpose}</Text>

      <View style={styles.priceRow}>
        <Text style={[styles.priceValue, { color: pal.accent }]}>
          {plan.price_inr === 0 ? "Free" : `₹${plan.price_inr}`}
        </Text>
        {plan.price_inr > 0 ? (
          <Text style={styles.pricePer}>/ month</Text>
        ) : plan.trial_days ? (
          <Text style={styles.pricePer}>· {plan.trial_days}-day trial</Text>
        ) : null}
      </View>

      <View style={styles.divider} />

      {bullets.map((b, i) => (
        <View key={i} style={styles.bulletRow}>
          <Ionicons name={b.icon as any} size={16} color={b.positive ? "#047857" : "#94A3B8"} />
          <Text style={[styles.bulletTxt, !b.positive && styles.bulletTxtMuted]}>{b.text}</Text>
        </View>
      ))}

      <TouchableOpacity
        testID={`plan-cta-${plan.key}`}
        disabled={isCurrent || busy}
        onPress={onUpgrade}
        activeOpacity={0.85}
        style={[
          styles.cta,
          {
            backgroundColor: isCurrent ? "#E5E7EB" : pal.accent,
          },
        ]}
      >
        {busy ? (
          <ActivityIndicator color={isCurrent ? "#64748B" : "#fff"} />
        ) : (
          <Text style={[styles.ctaTxt, { color: isCurrent ? "#64748B" : "#fff" }]}>
            {isCurrent ? "You're on this plan" : plan.price_inr === 0 ? "Switch to Free Trial" : `Upgrade to ${plan.name}`}
          </Text>
        )}
      </TouchableOpacity>

      <Text style={styles.ctaSubtle}>{plan.cta}</Text>
    </View>
  );
}

// ------------ helpers -----------------------------------------------------

function buildBullets(p: PlanSpec) {
  const out: { icon: string; text: string; positive: boolean }[] = [];
  if (p.period === "trial") {
    out.push({
      icon: "checkmark-circle",
      text: `${p.label_cap} labels (one-time)`,
      positive: true,
    });
    out.push({
      icon: "time-outline",
      text: `Valid for ${p.trial_days} days`,
      positive: true,
    });
    out.push({
      icon: "close-circle",
      text: "No bulk print",
      positive: false,
    });
    return out;
  }
  out.push({
    icon: "checkmark-circle",
    text: `${p.label_cap.toLocaleString()} labels per month`,
    positive: true,
  });
  if (p.bulk_max > 0) {
    out.push({
      icon: "albums-outline",
      text: `Bulk print up to ${p.bulk_max} at once`,
      positive: true,
    });
  } else {
    out.push({
      icon: "close-circle",
      text: "Single label print only",
      positive: false,
    });
  }
  if (p.daily_cap) {
    out.push({
      icon: "flash-outline",
      text: `Daily fast-lane: ${p.daily_cap} labels/day`,
      positive: true,
    });
  }
  if (p.key === "gold" || p.key === "platinum") {
    out.push({
      icon: "headset-outline",
      text: p.key === "platinum" ? "Priority + faster processing" : "Priority support",
      positive: true,
    });
  }
  return out;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  mockBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#FEF3C7",
    borderColor: "#FCD34D",
    borderWidth: 1,
    padding: 10,
    borderRadius: 10,
    marginBottom: 14,
  },
  mockBannerTxt: { flex: 1, color: "#92400E", fontSize: 12, fontWeight: "600" },
  usageBox: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    marginBottom: 16,
    gap: 4,
  },
  usageTitle: { fontSize: 11, fontWeight: "800", color: "#64748B", letterSpacing: 0.6 },
  usageLine: { fontSize: 14, color: "#334155" },
  usageStrong: { fontWeight: "800", color: "#0F172A" },
  card: {
    borderRadius: 18,
    padding: 18,
    marginBottom: 16,
    position: "relative",
  },
  badge: {
    position: "absolute",
    top: -10,
    right: 16,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    shadowColor: "#000",
    shadowOpacity: 0.12,
    shadowRadius: 3,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  badgeTxt: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  currentPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "#D1FAE5",
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    marginBottom: 8,
  },
  currentPillTxt: { color: "#065F46", fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
  planName: { fontSize: 22, fontWeight: "900", marginBottom: 4 },
  planFeel: { fontSize: 13, fontWeight: "700", color: "#1F2937", marginBottom: 2 },
  planPurpose: { fontSize: 12, color: "#64748B", marginBottom: 12 },
  priceRow: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  priceValue: { fontSize: 28, fontWeight: "900" },
  pricePer: { fontSize: 12, color: "#64748B", fontWeight: "600" },
  divider: {
    height: 1, backgroundColor: "rgba(0,0,0,0.08)", marginVertical: 12,
  },
  bulletRow: { flexDirection: "row", alignItems: "center", gap: 8, marginVertical: 5 },
  bulletTxt: { flex: 1, color: "#1F2937", fontSize: 13, fontWeight: "600" },
  bulletTxtMuted: { color: "#94A3B8", textDecorationLine: "line-through" },
  cta: {
    marginTop: 14,
    borderRadius: 10,
    paddingVertical: 12,
    alignItems: "center",
  },
  ctaTxt: { fontSize: 14, fontWeight: "800", letterSpacing: 0.3 },
  ctaSubtle: {
    marginTop: 8,
    fontSize: 11,
    color: "#64748B",
    textAlign: "center",
    fontStyle: "italic",
  },
  footnote: {
    textAlign: "center",
    color: "#94A3B8",
    fontSize: 12,
    marginTop: 8,
  },
});
