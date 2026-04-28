/**
 * Plans & Billing screen.
 *
 * Phase-5c: Anchor pricing + Scarcity countdown.
 * - Monthly / Yearly billing toggle
 * - Anchor "was ₹X" strikethrough next to the new price (admin tunable)
 * - Yearly format: "12 + N months FREE" badge with auto savings text
 * - Countdown banner driven by /api/plans-pricing.countdown
 *     - per_device: AsyncStorage stores first-visit timestamp per device
 *     - global: single deadline shared across all devices
 *     - off:    banner hidden (strikethrough still shown if enabled)
 *
 * NO GST, NO USD — INR-inclusive on user's request.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  Api,
  PlanKey,
  PlanSpec,
  UsageSummary,
  Wallet,
  PlanPricingEntry,
  CountdownConfig,
} from "../lib/api";
import { colors } from "../lib/theme";
import { useAuth } from "../lib/auth";

type PalKey = PlanKey;
const PAL: Record<PalKey, { bg: string; border: string; accent: string; chipBg: string; chipTxt: string }> = {
  free_trial: { bg: "#FAF5FF", border: "#DDD6FE", accent: "#7C3AED", chipBg: "#7C3AED", chipTxt: "#fff" },
  silver:     { bg: "#F8FAFC", border: "#CBD5E1", accent: "#475569", chipBg: "#475569", chipTxt: "#fff" },
  gold:       { bg: "#FFFBEB", border: "#F59E0B", accent: "#B45309", chipBg: "#F59E0B", chipTxt: "#fff" },
  platinum:   { bg: "#EFF6FF", border: "#3B82F6", accent: "#1E3A8A", chipBg: "#1E3A8A", chipTxt: "#fff" },
};

type BillingMode = "monthly" | "yearly";

const COUNTDOWN_STORAGE_KEY = "@plans_countdown_first_visit_v1";

function formatExpiryDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IN", {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch { return "—"; }
}

export default function PlansScreen() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [plans, setPlans] = useState<PlanSpec[]>([]);
  const [current, setCurrent] = useState<PlanKey>("free_trial");
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<PlanKey | null>(null);
  const [billing, setBilling] = useState<BillingMode>("yearly");
  // Track whether the user has manually toggled the cycle since this
  // mount — only auto-sync to their saved cycle when they haven't.
  const billingTouchedRef = useRef(false);
  const [pricing, setPricing] = useState<Record<PlanKey, PlanPricingEntry> | null>(null);
  const [countdown, setCountdown] = useState<CountdownConfig | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number>(0);
  const tickerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const [pl, u, w, pp] = await Promise.all([
        Api.listPlans(),
        Api.myUsage(),
        Api.getWallet().catch(() => null),
        Api.getPlansPricing().catch(() => null),
      ]);
      setPlans(pl.plans);
      setCurrent(pl.current);
      setUsage(u);
      setWallet(w);
      // Auto-sync billing toggle to user's saved cycle (only if user
      // hasn't manually picked one this session). Helps One-Tap renewal.
      if (!billingTouchedRef.current && u?.plan_billing_cycle) {
        setBilling(u.plan_billing_cycle === "yearly" ? "yearly" : "monthly");
      }
      if (pp) {
        setPricing(pp.plan_pricing);
        setCountdown(pp.countdown);
      }
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

  // Countdown setup — runs whenever countdown config changes.
  useEffect(() => {
    if (tickerRef.current) {
      clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
    if (!countdown || !countdown.enabled || countdown.mode === "off") {
      setSecondsLeft(0);
      return;
    }
    let cancelled = false;
    (async () => {
      let expiresAt = 0;
      if (countdown.mode === "global" && countdown.global_expires_at) {
        expiresAt = new Date(countdown.global_expires_at).getTime();
      } else if (countdown.mode === "per_device") {
        try {
          const stored = await AsyncStorage.getItem(COUNTDOWN_STORAGE_KEY);
          let firstVisit = stored ? Number(stored) : 0;
          if (!firstVisit || isNaN(firstVisit)) {
            firstVisit = Date.now();
            await AsyncStorage.setItem(COUNTDOWN_STORAGE_KEY, String(firstVisit));
          }
          expiresAt = firstVisit + countdown.countdown_minutes * 60 * 1000;
        } catch {
          expiresAt = Date.now() + countdown.countdown_minutes * 60 * 1000;
        }
      }
      if (cancelled) return;
      const tick = () => {
        const left = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
        setSecondsLeft(left);
      };
      tick();
      tickerRef.current = setInterval(tick, 1000);
    })();
    return () => {
      cancelled = true;
      if (tickerRef.current) clearInterval(tickerRef.current);
      tickerRef.current = null;
    };
  }, [countdown]);

  const showOffer = !!countdown?.enabled && countdown.mode !== "off" && secondsLeft > 0;

  const formattedTime = useMemo(() => {
    if (secondsLeft <= 0) return "00:00:00";
    const days = Math.floor(secondsLeft / 86400);
    const h = Math.floor((secondsLeft % 86400) / 3600);
    const m = Math.floor((secondsLeft % 3600) / 60);
    const s = secondsLeft % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    if (days > 0) return `${days}d ${pad(h)}h ${pad(m)}m ${pad(s)}s`;
    return `${pad(h)}:${pad(m)}:${pad(s)}`;
  }, [secondsLeft]);

  const doUpgrade = async (key: PlanKey) => {
    const plan = plans.find((p) => p.key === key);
    if (!plan) return;

    // Free trial — keep the existing local-switch flow (no payment).
    if (key === "free_trial") {
      const proceedFree = async () => {
        try {
          setBusyKey(key);
          await Api.upgradePlan(key);
          await refresh().catch(() => {});
          await load();
          Alert.alert("Plan updated", `You're now on the ${plan.name} plan.`);
        } catch (e: any) {
          Alert.alert("Switch failed", e?.response?.data?.detail || e?.message || "Please try again");
        } finally {
          setBusyKey(null);
        }
      };
      const msg = `Switch to ${plan.name}? You won't be charged.`;
      if (Platform.OS === "web") {
        if (typeof window !== "undefined" && window.confirm && window.confirm(msg)) {
          proceedFree();
        }
        return;
      }
      Alert.alert(`Switch to ${plan.name}?`, msg, [
        { text: "Cancel", style: "cancel" },
        { text: "Confirm", onPress: proceedFree },
      ]);
      return;
    }

    // Paid plans — route to Razorpay checkout with chosen billing cycle.
    const planPricing = pricing?.[key];
    if (!planPricing) {
      Alert.alert("Pricing unavailable", "Could not load pricing. Pull to refresh and try again.");
      return;
    }
    const price =
      billing === "yearly"
        ? planPricing.yearly_price || planPricing.monthly_price * 12
        : planPricing.monthly_price;
    if (!price || price <= 0) {
      Alert.alert("Not available", "This plan/cycle isn't priced yet.");
      return;
    }
    const cycleLbl = billing === "yearly" ? "Yearly" : "Monthly";
    const bonusTxt =
      billing === "yearly" && planPricing.yearly_bonus_months > 0
        ? ` (${planPricing.yearly_base_months} + ${planPricing.yearly_bonus_months} months FREE)`
        : "";
    const confirmMsg =
      `Pay ₹${price.toLocaleString("en-IN")} for ${plan.name} ${cycleLbl}` +
      `${bonusTxt}.\nYou'll be redirected to Razorpay's secure payment page.`;

    const proceed = () => {
      router.push({
        pathname: "/checkout",
        params: { mode: "plan", plan: key, cycle: billing },
      });
    };
    if (Platform.OS === "web") {
      if (typeof window !== "undefined" && window.confirm && window.confirm(confirmMsg)) {
        proceed();
      }
      return;
    }
    Alert.alert(`Upgrade to ${plan.name}?`, confirmMsg, [
      { text: "Cancel", style: "cancel" },
      { text: "Pay now", onPress: proceed },
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
        {/* Countdown banner (Phase-5c) */}
        {showOffer ? (
          <View style={styles.countdownBanner} testID="countdown-banner">
            <View style={styles.countdownIconBox}>
              <Ionicons name="flame" size={20} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.countdownHead}>{countdown?.headline || "Limited time offer"}</Text>
              <Text style={styles.countdownTime}>Ends in {formattedTime}</Text>
            </View>
          </View>
        ) : null}

        {/* Razorpay live-payments banner */}
        <View style={styles.mockBanner}>
          <Ionicons name="shield-checkmark" size={16} color="#065F46" />
          <Text style={styles.mockBannerTxt}>
            Secure payments by Razorpay · Cards, UPI, Netbanking & Wallets
          </Text>
        </View>

        {/* Wallet card — tap to manage credits */}
        <TouchableOpacity
          testID="wallet-card"
          activeOpacity={0.85}
          onPress={() => router.push("/wallet")}
          style={styles.walletCard}
        >
          <View style={styles.walletIconBox}>
            <Ionicons name="wallet" size={22} color="#fff" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.walletLbl}>Wallet balance</Text>
            <Text style={styles.walletVal}>
              {(wallet?.remaining_credits ?? 0).toFixed(2)}
              <Text style={styles.walletUnit}> credits</Text>
            </Text>
          </View>
          <Ionicons name="chevron-forward" size={20} color="#CBD5E1" />
        </TouchableOpacity>

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
            {/* Paid plan validity row */}
            {usage.period === "month" && usage.plan_expires_at ? (
              <View style={[
                styles.validityRow,
                usage.plan_expired
                  ? styles.validityExpired
                  : (usage.plan_days_left != null && usage.plan_days_left <= 7)
                    ? styles.validityWarn
                    : styles.validityOk,
              ]}>
                <Ionicons
                  name={usage.plan_expired ? "alert-circle" : "calendar-outline"}
                  size={14}
                  color={
                    usage.plan_expired ? "#B91C1C"
                    : (usage.plan_days_left != null && usage.plan_days_left <= 7) ? "#B45309"
                    : "#065F46"
                  }
                />
                <Text style={[
                  styles.validityTxt,
                  usage.plan_expired
                    ? { color: "#B91C1C" }
                    : (usage.plan_days_left != null && usage.plan_days_left <= 7)
                      ? { color: "#B45309" }
                      : { color: "#065F46" },
                ]}>
                  {usage.plan_expired
                    ? `Expired on ${formatExpiryDate(usage.plan_expires_at)} — renew now`
                    : usage.plan_billing_cycle === "yearly"
                      ? `Yearly · Renews on ${formatExpiryDate(usage.plan_expires_at)}${
                          usage.plan_days_left != null ? ` (${usage.plan_days_left} days left)` : ""
                        }`
                      : `Monthly · Renews on ${formatExpiryDate(usage.plan_expires_at)}${
                          usage.plan_days_left != null ? ` (${usage.plan_days_left} days left)` : ""
                        }`}
                </Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {/* Monthly / Yearly toggle */}
        <View style={styles.toggleWrap}>
          <TouchableOpacity
            testID="bill-monthly"
            onPress={() => { billingTouchedRef.current = true; setBilling("monthly"); }}
            style={[styles.toggleBtn, billing === "monthly" && styles.toggleBtnActive]}
          >
            <Text style={[styles.toggleTxt, billing === "monthly" && styles.toggleTxtActive]}>
              Monthly
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="bill-yearly"
            onPress={() => { billingTouchedRef.current = true; setBilling("yearly"); }}
            style={[styles.toggleBtn, billing === "yearly" && styles.toggleBtnActive]}
          >
            <Text style={[styles.toggleTxt, billing === "yearly" && styles.toggleTxtActive]}>
              Yearly
            </Text>
            <View style={styles.savePill}>
              <Text style={styles.savePillTxt}>Save 25%</Text>
            </View>
          </TouchableOpacity>
        </View>

        {loading ? (
          <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
        ) : (
          plans.map((p) => {
            const isCurr = p.key === current;
            // One-tap renewal: enable CTA on the user's current paid plan
            // when it's expired or expiring within 30 days, so they can
            // renew without leaving the screen.
            const renewable =
              isCurr &&
              p.key !== "free_trial" &&
              usage?.period === "month" &&
              (
                usage.plan_expired === true ||
                (usage.plan_days_left != null && usage.plan_days_left <= 30)
              );
            return (
              <PlanCard
                key={p.key}
                plan={p}
                isCurrent={isCurr}
                isRenewable={!!renewable}
                planExpired={!!usage?.plan_expired}
                busy={busyKey === p.key}
                billing={billing}
                pricing={pricing?.[p.key] || null}
                showAnchor={showOffer || (pricing?.[p.key]?.show_strikethrough ?? false)}
                onUpgrade={() => doUpgrade(p.key)}
              />
            );
          })
        )}

        <Text style={styles.footnote}>
          Need a custom volume plan? Write to us at support@your-brand.app.
        </Text>

        {/* Refund + cancellation links (Razorpay merchant policy) */}
        <View style={styles.policyRow}>
          <TouchableOpacity onPress={() => router.push("/refund-policy" as any)} hitSlop={6}>
            <Text style={styles.policyLink}>Refund Policy</Text>
          </TouchableOpacity>
          <Text style={styles.policySep}>·</Text>
          <TouchableOpacity onPress={() => router.push("/cancel-subscription" as any)} hitSlop={6}>
            <Text style={styles.policyLink}>Cancel Subscription</Text>
          </TouchableOpacity>
          <Text style={styles.policySep}>·</Text>
          <TouchableOpacity onPress={() => router.push("/refund-policy?tab=terms" as any)} hitSlop={6}>
            <Text style={styles.policyLink}>Terms</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

// ------------ Plan Card ---------------------------------------------------

function PlanCard({
  plan, isCurrent, isRenewable, planExpired, busy, onUpgrade, billing, pricing, showAnchor,
}: {
  plan: PlanSpec;
  isCurrent: boolean;
  isRenewable?: boolean;
  planExpired?: boolean;
  busy: boolean;
  billing: BillingMode;
  pricing: PlanPricingEntry | null;
  showAnchor: boolean;
  onUpgrade: () => void;
}) {
  const pal = PAL[plan.key];
  const showBadge = plan.badge != null;
  const bullets = buildBullets(plan);

  // Resolve display price + anchor + period label.
  const isFree = plan.price_inr === 0;
  let displayPrice = plan.price_inr;
  let anchorPrice = 0;
  let perLabel = isFree ? "" : "/ month";
  let yearlySub = "";
  if (!isFree && pricing) {
    if (billing === "yearly") {
      displayPrice = pricing.yearly_price || pricing.monthly_price * 12;
      anchorPrice = pricing.yearly_anchor || 0;
      perLabel = "/ year";
      const total = pricing.yearly_base_months + pricing.yearly_bonus_months;
      yearlySub = pricing.yearly_bonus_months > 0
        ? `${pricing.yearly_base_months} + ${pricing.yearly_bonus_months} months FREE (${total} months total)`
        : `${total} months total`;
    } else {
      displayPrice = pricing.monthly_price;
      anchorPrice = pricing.monthly_anchor || 0;
      perLabel = "/ month";
    }
  }

  const showStrike = showAnchor && anchorPrice > 0 && anchorPrice > displayPrice;
  const savingsAmt = showStrike ? anchorPrice - displayPrice : 0;
  const savingsPct = showStrike && anchorPrice > 0
    ? Math.round((savingsAmt / anchorPrice) * 100)
    : 0;

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
        <View style={[styles.currentPill, isRenewable && (planExpired ? styles.currentPillDanger : styles.currentPillWarn)]}>
          <Ionicons
            name={isRenewable ? (planExpired ? "alert-circle" : "calendar-outline") : "checkmark-circle"}
            size={12}
            color={isRenewable ? (planExpired ? "#7F1D1D" : "#78350F") : "#065F46"}
          />
          <Text style={[
            styles.currentPillTxt,
            isRenewable && (planExpired ? { color: "#7F1D1D" } : { color: "#78350F" }),
          ]}>
            {isRenewable
              ? (planExpired ? "Expired — Renew now" : "Your Current Plan · Renew")
              : "Your Current Plan"}
          </Text>
        </View>
      )}

      <Text style={[styles.planName, { color: pal.accent }]}>{plan.name}</Text>
      <Text style={styles.planFeel}>{plan.feel}</Text>
      <Text style={styles.planPurpose}>{plan.purpose}</Text>

      <View style={styles.priceRow}>
        <Text style={[styles.priceValue, { color: pal.accent }]}>
          {isFree ? "Free" : `₹${displayPrice.toLocaleString("en-IN")}`}
        </Text>
        {!isFree ? (
          <Text style={styles.pricePer}>{perLabel}</Text>
        ) : plan.trial_days ? (
          <Text style={styles.pricePer}>· {plan.trial_days}-day trial</Text>
        ) : null}
      </View>

      {/* Anchor strikethrough + savings tag */}
      {showStrike ? (
        <View style={styles.anchorRow}>
          <Text style={styles.anchorStrike}>
            ₹{anchorPrice.toLocaleString("en-IN")}
          </Text>
          <View style={styles.savingsTag}>
            <Ionicons name="pricetag" size={10} color="#047857" />
            <Text style={styles.savingsTxt}>
              SAVE ₹{savingsAmt.toLocaleString("en-IN")} ({savingsPct}% off)
            </Text>
          </View>
        </View>
      ) : null}

      {/* Yearly bonus sticker */}
      {!isFree && billing === "yearly" && pricing && pricing.yearly_bonus_months > 0 ? (
        <View style={styles.bonusRow}>
          <Ionicons name="gift" size={13} color="#B45309" />
          <Text style={styles.bonusTxt}>
            {pricing.yearly_base_months} + {pricing.yearly_bonus_months} months FREE
          </Text>
        </View>
      ) : null}
      {!isFree && billing === "yearly" && yearlySub && pricing?.yearly_bonus_months === 0 ? (
        <Text style={styles.yearlySub}>{yearlySub}</Text>
      ) : null}

      <View style={styles.divider} />

      {bullets.map((b, i) => (
        <View key={i} style={styles.bulletRow}>
          <Ionicons name={b.icon as any} size={16} color={b.positive ? "#047857" : "#94A3B8"} />
          <Text style={[styles.bulletTxt, !b.positive && styles.bulletTxtMuted]}>{b.text}</Text>
        </View>
      ))}

      <TouchableOpacity
        testID={`plan-cta-${plan.key}`}
        disabled={(isCurrent && !isRenewable) || busy}
        onPress={onUpgrade}
        activeOpacity={0.85}
        style={[
          styles.cta,
          {
            backgroundColor:
              isCurrent && !isRenewable
                ? "#E5E7EB"
                : isRenewable
                  ? (planExpired ? "#DC2626" : "#B45309")
                  : pal.accent,
          },
        ]}
      >
        {busy ? (
          <ActivityIndicator color={isCurrent && !isRenewable ? "#64748B" : "#fff"} />
        ) : (
          <Text style={[
            styles.ctaTxt,
            { color: isCurrent && !isRenewable ? "#64748B" : "#fff" },
          ]}>
            {isCurrent && !isRenewable
              ? "You're on this plan"
              : isRenewable
                ? (planExpired ? `Renew ${plan.name} now` : `Renew ${plan.name}`)
                : isFree
                  ? "Switch to Free Trial"
                  : `Upgrade to ${plan.name}`}
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
  countdownBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "#7C2D12",
    borderRadius: 14,
    padding: 12,
    marginBottom: 12,
  },
  countdownIconBox: {
    width: 38, height: 38, borderRadius: 19,
    backgroundColor: "#EA580C",
    alignItems: "center", justifyContent: "center",
  },
  countdownHead: {
    color: "#FED7AA", fontSize: 11, fontWeight: "800", letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  countdownTime: { color: "#fff", fontSize: 18, fontWeight: "900", marginTop: 2, letterSpacing: 0.5 },
  mockBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#D1FAE5",
    borderColor: "#86EFAC",
    borderWidth: 1,
    padding: 10,
    borderRadius: 10,
    marginBottom: 14,
  },
  mockBannerTxt: { flex: 1, color: "#065F46", fontSize: 12, fontWeight: "700" },
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
  validityRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderRadius: 8,
    marginTop: 6,
    alignSelf: "flex-start",
  },
  validityOk:      { backgroundColor: "#D1FAE5" },
  validityWarn:    { backgroundColor: "#FEF3C7" },
  validityExpired: { backgroundColor: "#FEE2E2" },
  validityTxt: { fontSize: 11.5, fontWeight: "800", letterSpacing: 0.2 },
  toggleWrap: {
    flexDirection: "row",
    backgroundColor: "#F1F5F9",
    borderRadius: 999,
    padding: 4,
    marginBottom: 14,
  },
  toggleBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 10,
    borderRadius: 999,
  },
  toggleBtnActive: {
    backgroundColor: "#0F172A",
  },
  toggleTxt: { fontSize: 13, fontWeight: "800", color: "#475569", letterSpacing: 0.4 },
  toggleTxtActive: { color: "#fff" },
  savePill: {
    backgroundColor: "#10B981",
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 6,
  },
  savePillTxt: { color: "#fff", fontSize: 10, fontWeight: "900", letterSpacing: 0.4 },
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
  currentPillWarn:   { backgroundColor: "#FEF3C7" },
  currentPillDanger: { backgroundColor: "#FEE2E2" },
  planName: { fontSize: 22, fontWeight: "900", marginBottom: 4 },
  planFeel: { fontSize: 13, fontWeight: "700", color: "#1F2937", marginBottom: 2 },
  planPurpose: { fontSize: 12, color: "#64748B", marginBottom: 12 },
  priceRow: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  priceValue: { fontSize: 28, fontWeight: "900" },
  pricePer: { fontSize: 12, color: "#64748B", fontWeight: "600" },
  anchorRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 4,
    flexWrap: "wrap",
  },
  anchorStrike: {
    fontSize: 14,
    color: "#94A3B8",
    fontWeight: "700",
    textDecorationLine: "line-through",
  },
  savingsTag: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "#D1FAE5",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  savingsTxt: { color: "#047857", fontSize: 10.5, fontWeight: "900", letterSpacing: 0.3 },
  bonusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    marginTop: 6,
    backgroundColor: "#FEF3C7",
    alignSelf: "flex-start",
    paddingHorizontal: 9,
    paddingVertical: 4,
    borderRadius: 999,
  },
  bonusTxt: { color: "#B45309", fontSize: 11.5, fontWeight: "900", letterSpacing: 0.3 },
  yearlySub: { color: "#64748B", fontSize: 11, marginTop: 4, fontStyle: "italic" },
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
  policyRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12,
    paddingHorizontal: 8,
  },
  policyLink: {
    color: "#475569",
    fontSize: 12,
    fontWeight: "700",
    textDecorationLine: "underline",
  },
  policySep: { color: "#CBD5E1", fontSize: 12, fontWeight: "700" },
  walletCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: "#0F172A",
    borderRadius: 14, padding: 14, marginBottom: 14,
  },
  walletIconBox: {
    width: 42, height: 42, borderRadius: 21,
    backgroundColor: "#1E293B", alignItems: "center", justifyContent: "center",
  },
  walletLbl: { color: "#94A3B8", fontSize: 11, fontWeight: "700", letterSpacing: 0.6 },
  walletVal: { color: "#fff", fontSize: 22, fontWeight: "900", marginTop: 2 },
  walletUnit: { color: "#94A3B8", fontSize: 13, fontWeight: "700" },
});
