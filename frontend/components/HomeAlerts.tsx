/**
 * Home dashboard alert banners.
 *
 * Surfaces actionable warnings at the top of the Home tab:
 *  - Low credits (≤ 5 remaining)
 *  - Plan expired (paid plan past plan_expires_at) — blocking
 *  - Plan expiring soon (paid plan ≤ 7 days remaining)
 *  - Trial ending (free trial ≤ 3 days remaining)
 *
 * Each banner is dismissable per-day via AsyncStorage so the user
 * isn't nagged constantly. Tap → routes to the right CTA (plans /
 * wallet). Respects user.notification_prefs (loaded from /api).
 */
import React, { useCallback, useEffect, useState } from "react";
import PhIcon from "./PhIcon";
import { View, Text, StyleSheet, TouchableOpacity } from "react-native";
import { useFocusEffect, useRouter } from "expo-router";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Api, UsageSummary, Wallet, NotificationPrefs } from "../lib/api";

type Severity = "info" | "warn" | "danger";

type Banner = {
  key: string;            // unique storage suffix
  severity: Severity;
  icon: string;
  title: string;
  body: string;
  ctaLabel: string;
  onPress: () => void;
};

const SEVERITY_PALETTE: Record<Severity, {
  bg: string; border: string; iconBg: string; iconColor: string; titleColor: string; bodyColor: string; ctaBg: string; ctaTxt: string;
}> = {
  info:   { bg: "#EFF6FF", border: "#BFDBFE", iconBg: "#DBEAFE", iconColor: "#1E3A8A", titleColor: "#1E3A8A", bodyColor: "#1E40AF", ctaBg: "#1E3A8A", ctaTxt: "#fff" },
  warn:   { bg: "#FEF3C7", border: "#FCD34D", iconBg: "#FDE68A", iconColor: "#92400E", titleColor: "#78350F", bodyColor: "#92400E", ctaBg: "#B45309", ctaTxt: "#fff" },
  danger: { bg: "#FEE2E2", border: "#FCA5A5", iconBg: "#FECACA", iconColor: "#991B1B", titleColor: "#7F1D1D", bodyColor: "#991B1B", ctaBg: "#DC2626", ctaTxt: "#fff" },
};

const DISMISS_PREFIX = "@home_banner_dismissed_v1:";

function todayKey(): string {
  const d = new Date();
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`;
}

export default function HomeAlerts() {
  const router = useRouter();
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [dismissed, setDismissed] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      const [u, w, p] = await Promise.all([
        Api.myUsage().catch(() => null),
        Api.getWallet().catch(() => null),
        Api.getNotificationPrefs().catch(() => null),
      ]);
      setUsage(u);
      setWallet(w);
      setPrefs(p);
    } catch {/* silent */}
  }, []);

  useFocusEffect(
    useCallback(() => {
      load().catch(() => {});
    }, [load]),
  );

  // Refresh dismissed map on focus too — date may have rolled over.
  useEffect(() => {
    (async () => {
      const map: Record<string, boolean> = {};
      const day = todayKey();
      for (const k of ["low_credits", "plan_expiring", "trial_ending"]) {
        const v = await AsyncStorage.getItem(`${DISMISS_PREFIX}${k}:${day}`);
        if (v === "1") map[k] = true;
      }
      setDismissed(map);
    })().catch(() => {});
  }, [usage, wallet]);

  const dismiss = useCallback(async (k: string) => {
    setDismissed((m) => ({ ...m, [k]: true }));
    try {
      await AsyncStorage.setItem(`${DISMISS_PREFIX}${k}:${todayKey()}`, "1");
    } catch {/* silent */}
  }, []);

  // Helper — push to checkout with the user's current plan + cycle
  // pre-filled. Falls back to /plans when we don't know the cycle
  // (e.g. legacy paid users who never had plan_billing_cycle stamped).
  const oneTapRenew = useCallback(() => {
    const cycle = usage?.plan_billing_cycle === "yearly" ? "yearly" : "monthly";
    const plan = (usage?.plan || "").toString();
    if (plan && ["silver", "gold", "platinum"].includes(plan)) {
      router.push({
        pathname: "/checkout",
        params: { mode: "plan", plan, cycle },
      });
    } else {
      router.push("/plans");
    }
  }, [router, usage?.plan, usage?.plan_billing_cycle]);

  const banners: Banner[] = [];

  // Plan expired — NEVER dismissable, blocks label creation.
  if (usage?.period === "month" && usage.plan_expired) {
    const cycleLbl = usage.plan_billing_cycle === "yearly" ? "yearly" : "monthly";
    banners.push({
      key: "plan_expired",
      severity: "danger",
      icon: "alert-circle",
      title: "Subscription expired",
      body: `Your ${usage.plan_name} plan has ended. Tap to renew the same plan.`,
      ctaLabel: `Renew ${usage.plan_name} (${cycleLbl})`,
      onPress: oneTapRenew,
    });
  }

  // Plan expiring soon — paid plan, ≤ 7 days, dismissable.
  if (
    usage?.period === "month" &&
    !usage.plan_expired &&
    usage.plan_days_left != null &&
    usage.plan_days_left <= 7 &&
    prefs?.plan_expiring !== false &&
    !dismissed.plan_expiring
  ) {
    const days = usage.plan_days_left;
    const cycleLbl = usage.plan_billing_cycle === "yearly" ? "yearly" : "monthly";
    banners.push({
      key: "plan_expiring",
      severity: "warn",
      icon: "calendar-outline",
      title: `Plan ends in ${days === 0 ? "<1" : days} day${days === 1 ? "" : "s"}`,
      body: `Renew your ${usage.plan_name} ${cycleLbl} plan with one tap to avoid service interruption.`,
      ctaLabel: `Renew now`,
      onPress: oneTapRenew,
    });
  }

  // Trial ending — free trial ≤ 3 days, dismissable.
  if (
    usage?.period === "trial" &&
    usage.trial_days_left != null &&
    usage.trial_days_left <= 3 &&
    !usage.trial_expired &&
    prefs?.trial_ending !== false &&
    !dismissed.trial_ending
  ) {
    const days = usage.trial_days_left;
    banners.push({
      key: "trial_ending",
      severity: "warn",
      icon: "time-outline",
      title: `Trial ends in ${days === 0 ? "today" : `${days} day${days === 1 ? "" : "s"}`}`,
      body: "Upgrade to keep shipping after your trial ends.",
      ctaLabel: "See plans",
      onPress: () => router.push("/plans"),
    });
  }

  // Low credits — ≤ 5, dismissable per-day.
  if (
    wallet &&
    wallet.remaining_credits <= 5 &&
    prefs?.low_credits !== false &&
    !dismissed.low_credits
  ) {
    banners.push({
      key: "low_credits",
      severity: wallet.remaining_credits <= 1 ? "danger" : "warn",
      icon: "battery-half-outline",
      title: wallet.remaining_credits <= 1 ? "Wallet empty" : "Low wallet balance",
      body:
        wallet.remaining_credits <= 0
          ? "Smart Paste & label printing will fail. Top-up to continue."
          : `Only ${wallet.remaining_credits.toFixed(1)} credits left. Top-up to avoid interruptions.`,
      ctaLabel: "Top-up",
      onPress: () => router.push("/wallet"),
    });
  }

  if (banners.length === 0) return null;

  return (
    <View style={{ paddingHorizontal: 8, gap: 8, marginBottom: 8 }}>
      {banners.map((b) => (
        <BannerView
          key={b.key}
          banner={b}
          dismissable={b.key !== "plan_expired"}
          onDismiss={() => dismiss(b.key)}
        />
      ))}
    </View>
  );
}

function BannerView({
  banner, dismissable, onDismiss,
}: {
  banner: Banner; dismissable: boolean; onDismiss: () => void;
}) {
  const pal = SEVERITY_PALETTE[banner.severity];
  return (
    <View
      testID={`home-banner-${banner.key}`}
      style={[styles.card, { backgroundColor: pal.bg, borderColor: pal.border }]}
    >
      <View style={styles.row}>
        <View style={[styles.iconBox, { backgroundColor: pal.iconBg }]}>
          <PhIcon name={banner.icon as any} size={18} color={pal.iconColor} />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={[styles.title, { color: pal.titleColor }]}>{banner.title}</Text>
          <Text style={[styles.body, { color: pal.bodyColor }]}>{banner.body}</Text>
        </View>
        {dismissable ? (
          <TouchableOpacity onPress={onDismiss} hitSlop={10} style={styles.closeBtn}>
            <PhIcon name="close" size={16} color={pal.bodyColor} />
          </TouchableOpacity>
        ) : null}
      </View>
      <TouchableOpacity
        onPress={banner.onPress}
        style={[styles.cta, { backgroundColor: pal.ctaBg }]}
        activeOpacity={0.85}
      >
        <Text style={[styles.ctaTxt, { color: pal.ctaTxt }]}>{banner.ctaLabel}</Text>
        <PhIcon name="arrow-forward" size={14} color={pal.ctaTxt} />
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 12,
    borderWidth: 1,
    padding: 12,
  },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  iconBox: {
    width: 32, height: 32, borderRadius: 8,
    alignItems: "center", justifyContent: "center",
  },
  title: { fontSize: 13.5, fontWeight: "800" },
  body:  { fontSize: 12.5, marginTop: 2, lineHeight: 17 },
  closeBtn: { padding: 2 },
  cta: {
    marginTop: 10,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 8,
    borderRadius: 8,
  },
  ctaTxt: { fontSize: 12.5, fontWeight: "800", letterSpacing: 0.3 },
});
