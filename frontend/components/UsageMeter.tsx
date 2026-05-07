/**
 * Compact "plan + usage" meter for the Home dashboard.
 *
 * Shows (per plan type):
 *  - Free trial: "X / 10 trial labels used", days left, one-line CTA
 *  - Silver/Gold: monthly progress bar "X / N this month"
 *  - Platinum: monthly + "Today: X / 100"
 *
 * Tap → navigates to /plans.
 */
import React, { useCallback, useState } from "react";
import PhIcon from "./PhIcon";
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import { Api, UsageSummary, PlanKey } from "../lib/api";
import { colors } from "../lib/theme";

const PLAN_COLORS: Record<PlanKey, { bg: string; border: string; text: string; accent: string }> = {
  free_trial: { bg: "#F5F3FF", border: "#DDD6FE", text: "#5B21B6", accent: "#7C3AED" },
  silver:     { bg: "#F1F5F9", border: "#CBD5E1", text: "#334155", accent: "#64748B" },
  gold:       { bg: "#FFFBEB", border: "#FCD34D", text: "#92400E", accent: "#D97706" },
  platinum:   { bg: "#EFF6FF", border: "#BFDBFE", text: "#1E3A8A", accent: "#2563EB" },
};

export default function UsageMeter() {
  const router = useRouter();
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const u = await Api.myUsage();
      setUsage(u);
    } catch {
      /* ignore — user not logged in or network blip */
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load().catch(() => {});
    }, [load]),
  );

  if (loading) {
    return (
      <View style={[styles.card, styles.cardLoading]}>
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }
  if (!usage) return null;

  const pal = PLAN_COLORS[usage.plan] ?? PLAN_COLORS.free_trial;
  const pct = Math.min(
    100,
    Math.round((usage.labels_used / Math.max(1, usage.label_cap)) * 100),
  );

  const isTrial = usage.period === "trial";
  const exhausted = !usage.can_create_label;
  const headline = exhausted
    ? (isTrial && usage.trial_expired ? "Trial expired" : "Limit reached")
    : usage.plan_name;

  // Secondary line:
  //  trial   → "Expires in N days · 10 one-time labels"
  //  monthly → "This month · N remaining"
  let secondary: string;
  if (isTrial) {
    const days = usage.trial_days_left ?? 0;
    secondary =
      usage.trial_expired
        ? "Your 7-day trial has ended"
        : days <= 0
          ? "Expires today"
          : days === 1
            ? "Expires tomorrow · one-time 10 labels"
            : `Expires in ${days} days · one-time 10 labels`;
  } else {
    secondary = `This month · ${usage.labels_remaining} remaining`;
  }

  return (
    <TouchableOpacity
      testID="usage-meter"
      activeOpacity={0.85}
      onPress={() => router.push("/plans")}
      style={[styles.card, { backgroundColor: pal.bg, borderColor: pal.border }]}
    >
      <View style={styles.row}>
        <View style={[styles.planChip, { backgroundColor: pal.accent }]}>
          <PhIcon
            name={isTrial ? "sparkles" : usage.plan === "platinum" ? "rocket" : "star"}
            size={12}
            color="#fff"
          />
          <Text style={styles.planChipTxt} numberOfLines={1}>
            {headline.toUpperCase()}
          </Text>
        </View>
        <Text style={[styles.headline, { color: pal.text }]} numberOfLines={1}>
          {usage.labels_used} / {usage.label_cap}
        </Text>
        <PhIcon name="chevron-forward" size={18} color={pal.accent} />
      </View>

      {/* Progress bar */}
      <View style={[styles.progressTrack, { backgroundColor: pal.border }]}>
        <View
          style={[
            styles.progressFill,
            {
              backgroundColor: exhausted ? "#DC2626" : pal.accent,
              width: `${pct}%`,
            },
          ]}
        />
      </View>

      <View style={styles.metaRow}>
        <Text style={[styles.meta, { color: pal.text }]} numberOfLines={1}>
          {secondary}
        </Text>
        {usage.plan === "platinum" && usage.daily_cap ? (
          <Text style={[styles.meta, { color: pal.text }]}>
            Today · {usage.today_used ?? 0}/{usage.daily_cap}
          </Text>
        ) : null}
      </View>

      {exhausted ? (
        <View style={[styles.cta, { backgroundColor: pal.accent }]}>
          <PhIcon name="arrow-up-circle" size={14} color="#fff" />
          <Text style={styles.ctaTxt}>Upgrade to keep shipping</Text>
        </View>
      ) : null}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: 8,
    marginTop: 4,
    marginBottom: 14,
    borderRadius: 14,
    borderWidth: 1,
    padding: 14,
  },
  cardLoading: {
    backgroundColor: "#F8FAFC",
    borderColor: "#E2E8F0",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 86,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  planChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  planChipTxt: { color: "#fff", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  headline: {
    flex: 1,
    textAlign: "right",
    fontWeight: "800",
    fontSize: 14,
  },
  progressTrack: {
    marginTop: 10,
    height: 6,
    borderRadius: 4,
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    borderRadius: 4,
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    gap: 12,
  },
  meta: { fontSize: 12, fontWeight: "600" },
  cta: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  ctaTxt: { color: "#fff", fontSize: 13, fontWeight: "700" },
});
