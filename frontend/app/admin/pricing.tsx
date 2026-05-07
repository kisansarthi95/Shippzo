/**
 * Admin → Plan Pricing & Countdown editor (Phase-5c).
 *
 * Lets the admin tune the displayed monthly/yearly prices and the
 * "anchor" (strikethrough) price, plus the scarcity countdown banner
 * that appears on the public Plans screen. Free trial is fixed at 0/0.
 *
 * Yearly = 12 paid months + N bonus months (e.g. "12 + 1 months FREE").
 * The label on the Plans screen uses these fields to compute the display.
 */
import React, { useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { api, PlanKey, PlanPricingEntry, CountdownConfig } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type PaidPlanKey = "silver" | "gold" | "platinum";
const PAID_PLANS: { key: PaidPlanKey; name: string; tone: string }[] = [
  { key: "silver",   name: "Silver",   tone: "#475569" },
  { key: "gold",     name: "Gold",     tone: "#B45309" },
  { key: "platinum", name: "Platinum", tone: "#1E3A8A" },
];

type Pricing = Record<PlanKey, PlanPricingEntry>;

// Phase-13: Plan limit overrides (label_cap / bulk_max / daily_cap)
// live alongside the pricing on this same screen so the admin sees
// everything about a plan in one place — no separate screen.
// Phase-2C extends this with packing_variant_cap, team_member_cap,
// and extra_member_price_inr so every numeric quota per plan is
// edited from this single panel.
type PaidLimits = {
  label_cap: number;
  bulk_max: number;
  daily_cap: number | null;
  packing_variant_cap: number;
  team_member_cap: number;
  extra_member_price_inr: number;
};
type LimitsState = Record<PaidPlanKey, PaidLimits>;
type LimitsDefaults = Record<
  PaidPlanKey,
  PaidLimits & { price_inr: number; name: string }
>;

// Phase-17 (merge): WhatsApp manual-send pricing lives on this same
// screen now — the previous standalone screen was removed so the admin
// has ONE place to manage every per-plan number.
type WaRates = Record<PaidPlanKey, number>;

export default function AdminPricingScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [countdown, setCountdown] = useState<CountdownConfig | null>(null);
  const [limits, setLimits] = useState<LimitsState | null>(null);
  const [limitsDefaults, setLimitsDefaults] = useState<LimitsDefaults | null>(null);
  // WhatsApp per-plan rates + the global enable flag — same source of
  // truth as /admin/whatsapp-pricing.
  const [waEnabled, setWaEnabled] = useState(false);
  const [waRates, setWaRates] = useState<WaRates | null>(null);
  const [waRatesDefaults, setWaRatesDefaults] = useState<WaRates | null>(null);
  const [originalSnap, setOriginalSnap] = useState("");

  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only admin can edit pricing.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Fire three admin reads in parallel — pricing/countdown,
        // plan limit overrides, AND WhatsApp manual-message pricing.
        // All three are edited together on this single screen so the
        // admin has one place for every numeric knob per plan.
        const [rPricing, rLimits, rWa] = await Promise.all([
          api.get<{
            plan_pricing: Pricing;
            countdown: CountdownConfig;
          }>("/admin/global-config"),
          api.get<{
            defaults: Record<string, any>;
            current: Record<string, any>;
          }>("/admin/plan-limits"),
          api.get<{
            defaults: { enabled: boolean; rates: Record<string, number> };
            current:  { enabled: boolean; rates: Record<string, number> };
          }>("/admin/whatsapp-pricing"),
        ]);
        if (cancelled) return;
        setPricing(rPricing.data.plan_pricing);
        setCountdown(rPricing.data.countdown);

        // Flatten the limits payload into the paid-plan-only shape we use on
        // this screen (free_trial limits are edited elsewhere, not here).
        const nextLimits: LimitsState = {
          silver: {
            label_cap: rLimits.data.current.silver?.label_cap ?? 50,
            bulk_max:  rLimits.data.current.silver?.bulk_max  ?? 0,
            daily_cap: rLimits.data.current.silver?.daily_cap ?? null,
            packing_variant_cap:    rLimits.data.current.silver?.packing_variant_cap    ?? 2,
            team_member_cap:        rLimits.data.current.silver?.team_member_cap        ?? 0,
            extra_member_price_inr: rLimits.data.current.silver?.extra_member_price_inr ?? 200,
          },
          gold: {
            label_cap: rLimits.data.current.gold?.label_cap ?? 300,
            bulk_max:  rLimits.data.current.gold?.bulk_max  ?? 50,
            daily_cap: rLimits.data.current.gold?.daily_cap ?? null,
            packing_variant_cap:    rLimits.data.current.gold?.packing_variant_cap    ?? 5,
            team_member_cap:        rLimits.data.current.gold?.team_member_cap        ?? 1,
            extra_member_price_inr: rLimits.data.current.gold?.extra_member_price_inr ?? 200,
          },
          platinum: {
            label_cap: rLimits.data.current.platinum?.label_cap ?? 1500,
            bulk_max:  rLimits.data.current.platinum?.bulk_max  ?? 100,
            daily_cap: rLimits.data.current.platinum?.daily_cap ?? 100,
            packing_variant_cap:    rLimits.data.current.platinum?.packing_variant_cap    ?? 8,
            team_member_cap:        rLimits.data.current.platinum?.team_member_cap        ?? 2,
            extra_member_price_inr: rLimits.data.current.platinum?.extra_member_price_inr ?? 300,
          },
        };
        const nextDefaults: LimitsDefaults = {
          silver:   rLimits.data.defaults.silver,
          gold:     rLimits.data.defaults.gold,
          platinum: rLimits.data.defaults.platinum,
        };
        setLimits(nextLimits);
        setLimitsDefaults(nextDefaults);

        // ── WhatsApp pricing flatten ──
        const nextWaRates: WaRates = {
          silver:   Number(rWa.data.current.rates.silver   ?? 0),
          gold:     Number(rWa.data.current.rates.gold     ?? 0),
          platinum: Number(rWa.data.current.rates.platinum ?? 0),
        };
        const nextWaDefaults: WaRates = {
          silver:   Number(rWa.data.defaults.rates.silver   ?? 0),
          gold:     Number(rWa.data.defaults.rates.gold     ?? 0),
          platinum: Number(rWa.data.defaults.rates.platinum ?? 0),
        };
        setWaEnabled(Boolean(rWa.data.current.enabled));
        setWaRates(nextWaRates);
        setWaRatesDefaults(nextWaDefaults);

        setOriginalSnap(JSON.stringify({
          plan_pricing: rPricing.data.plan_pricing,
          countdown: rPricing.data.countdown,
          limits: nextLimits,
          wa: { enabled: Boolean(rWa.data.current.enabled), rates: nextWaRates },
        }));
      } catch (e: any) {
        Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const updatePlan = (key: PaidPlanKey, patch: Partial<PlanPricingEntry>) => {
    setPricing((prev) => {
      if (!prev) return prev;
      return { ...prev, [key]: { ...prev[key], ...patch } };
    });
  };

  // Phase-13 helper — same shape as updatePlan but for limits.
  const updateLimit = (key: PaidPlanKey, patch: Partial<PaidLimits>) => {
    setLimits((prev) => {
      if (!prev) return prev;
      return { ...prev, [key]: { ...prev[key], ...patch } };
    });
  };

  // Auto-fill yearly_price = monthly × 12 × 0.75 (25% discount)
  const autoCalcYearly = (key: PaidPlanKey) => {
    if (!pricing) return;
    const mp = pricing[key].monthly_price;
    const yp = Math.round(mp * 12 * 0.75);
    const ya = Math.round(mp * 12 * 1.5); // anchor: 50% above true 12mo
    updatePlan(key, { yearly_price: yp, yearly_anchor: ya });
  };

  const liveSnap = useMemo(
    () => JSON.stringify({ plan_pricing: pricing, countdown, limits }),
    [pricing, countdown, limits],
  );
  const isDirty = !!originalSnap && originalSnap !== liveSnap;

  const save = async () => {
    if (!pricing || !countdown || !limits) return;
    // basic validation — pricing
    for (const k of PAID_PLANS) {
      const p = pricing[k.key];
      if (!p || p.monthly_price < 1) {
        Alert.alert("Invalid pricing", `${k.name}: monthly price must be ≥ ₹1`);
        return;
      }
      if (p.show_strikethrough && p.monthly_anchor <= p.monthly_price) {
        Alert.alert("Invalid anchor",
          `${k.name}: anchor price must be greater than display price (or turn strikethrough off).`);
        return;
      }
      // Phase-13: validate limits too
      const lim = limits[k.key];
      if (lim.label_cap < 1) {
        Alert.alert("Invalid limit",
          `${k.name}: Monthly label limit must be ≥ 1.`);
        return;
      }
      if (lim.bulk_max < 0) {
        Alert.alert("Invalid limit",
          `${k.name}: Bulk print max cannot be negative.`);
        return;
      }
    }
    try {
      setSaving(true);
      // Save pricing + countdown to /admin/global-config (existing doc).
      await api.put("/admin/global-config", { plan_pricing: pricing, countdown });
      // Save limit overrides to /admin/plan-limits (Phase-13 doc).
      await api.put("/admin/plan-limits", {
        plans: {
          silver:   limits.silver,
          gold:     limits.gold,
          platinum: limits.platinum,
        },
      });
      setOriginalSnap(liveSnap);
      Alert.alert("Saved", "Pricing, limits & countdown updated. All users will see the new values.");
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setSaving(false);
    }
  };

  const handleBack = () => {
    if (!isDirty) {
      router.replace("/(tabs)/settings");
      return;
    }
    Alert.alert("Unsaved changes", "Save changes before leaving?", [
      { text: "Keep editing", style: "cancel" },
      { text: "Discard", style: "destructive", onPress: () => router.replace("/(tabs)/settings") },
      { text: "Save", onPress: save },
    ]);
  };

  if (loading || !pricing || !countdown || !limits || !limitsDefaults) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}><ActivityIndicator size="large" color={colors.primary} /></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} hitSlop={10} style={{ marginRight: 8 }}>
          <PhIcon name="chevron-back" size={26} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <Text style={styles.title}>Plan Pricing</Text>
            {isDirty && (
              <View style={styles.dirtyBadge}>
                <View style={styles.dirtyDot} />
                <Text style={styles.dirtyTxt}>Unsaved</Text>
              </View>
            )}
          </View>
          <Text style={styles.subtitle}>Anchor pricing & scarcity countdown</Text>
        </View>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16, paddingBottom: 130 }}>
        <View style={styles.infoBox}>
          <PhIcon name="information-circle" size={18} color="#0EA5E9" />
          <Text style={styles.infoTxt}>
            Anchor (strikethrough) is the "original" price shown crossed out. The display price
            is what users actually pay. Yearly auto-suggest = 12× monthly × 0.75.
          </Text>
        </View>

        {/* 2026-04-30 — Coupons gateway. Coupons live in their own
            collection, not in admin_config, so they get a dedicated
            screen rather than an inline editor here. */}
        <TouchableOpacity
          testID="open-coupons-screen"
          onPress={() => router.push("/admin/coupons")}
          style={styles.couponsCard}
        >
          <View style={{ flex: 1 }}>
            <Text style={styles.couponsTitle}>🎟  Coupons</Text>
            <Text style={styles.couponsSub}>
              Create / pause / delete discount codes that users can
              apply at plan checkout. Festival offers, beta-tester codes,
              re-activation codes — all managed here.
            </Text>
          </View>
          <PhIcon name="chevron-forward" size={20} color={colors.primary} />
        </TouchableOpacity>

        {/* ---- Plan price cards ---- */}
        {PAID_PLANS.map(({ key, name, tone }) => {
          const p = pricing[key];
          return (
            <View key={key} style={[styles.card, { borderLeftColor: tone, borderLeftWidth: 4 }]}>
              <View style={styles.cardHead}>
                <Text style={[styles.planName, { color: tone }]}>{name}</Text>
                <View style={styles.row2}>
                  <Text style={styles.smallLbl}>Strikethrough</Text>
                  <Switch
                    value={p.show_strikethrough}
                    onValueChange={(v) => updatePlan(key, { show_strikethrough: v })}
                    trackColor={{ false: "#D1D5DB", true: colors.primary }}
                    thumbColor="#fff"
                  />
                </View>
              </View>

              <Text style={styles.sectionLbl}>Monthly</Text>
              <View style={styles.row2}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Display ₹/mo</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.monthly_price)}
                    onChangeText={(v) => updatePlan(key, {
                      monthly_price: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder="199"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Anchor (strike)</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.monthly_anchor)}
                    onChangeText={(v) => updatePlan(key, {
                      monthly_anchor: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder="499"
                  />
                </View>
              </View>

              <View style={styles.row2}>
                <Text style={styles.sectionLbl}>Yearly</Text>
                <TouchableOpacity onPress={() => autoCalcYearly(key)} style={styles.miniBtn}>
                  <PhIcon name="flash" size={12} color={colors.primary} />
                  <Text style={styles.miniBtnTxt}>Auto-calc (×12 × 0.75)</Text>
                </TouchableOpacity>
              </View>
              <View style={styles.row2}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Display ₹/yr</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.yearly_price)}
                    onChangeText={(v) => updatePlan(key, {
                      yearly_price: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder="1791"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Anchor (strike)</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.yearly_anchor)}
                    onChangeText={(v) => updatePlan(key, {
                      yearly_anchor: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder="4999"
                  />
                </View>
              </View>

              <View style={styles.row2}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Base months</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.yearly_base_months)}
                    onChangeText={(v) => updatePlan(key, {
                      yearly_base_months: Math.max(1, Number(v.replace(/[^0-9]/g, "")) || 1),
                    })}
                    style={styles.input}
                    placeholder="12"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>Bonus months FREE</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(p.yearly_bonus_months)}
                    onChangeText={(v) => updatePlan(key, {
                      yearly_bonus_months: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder="1"
                  />
                </View>
              </View>

              {/* Preview */}
              <View style={styles.previewBox}>
                <Text style={styles.previewLine}>
                  Monthly: <Text style={{ fontWeight: "900" }}>₹{p.monthly_price}</Text>
                  {p.show_strikethrough && p.monthly_anchor > p.monthly_price ? (
                    <Text style={styles.strike}>  was ₹{p.monthly_anchor}</Text>
                  ) : null}
                </Text>
                <Text style={styles.previewLine}>
                  Yearly: <Text style={{ fontWeight: "900" }}>₹{p.yearly_price}</Text>
                  <Text style={{ color: "#64748B" }}>
                    {"  "}for {p.yearly_base_months} + {p.yearly_bonus_months} months
                    {p.yearly_bonus_months > 0 ? " FREE" : ""}
                  </Text>
                </Text>
                {p.monthly_price > 0 && p.yearly_price > 0 ? (
                  <Text style={styles.previewSavings}>
                    {(() => {
                      const truePay = p.monthly_price * 12;
                      const save = truePay - p.yearly_price;
                      const pct = truePay > 0 ? Math.round((save / truePay) * 100) : 0;
                      return save > 0
                        ? `Saves ₹${save.toLocaleString("en-IN")} vs paying monthly (${pct}% off)`
                        : `No savings configured`;
                    })()}
                  </Text>
                ) : null}
              </View>

              {/* Phase-13: Plan Limits (merged into this card per admin
                  request — keeps pricing + limits co-located so one
                  scroll manages everything about a plan). These values
                  sync to /admin/plan-limits on Save. */}
              <View style={styles.limitsSection}>
                <View style={styles.limitsHeader}>
                  <PhIcon name="options-outline" size={15} color={tone} />
                  <Text style={[styles.limitsTitle, { color: tone }]}>
                    Plan Limits
                  </Text>
                </View>

                {/* Phase 2C+ — quick effective-values summary so the
                    admin can scan all caps without expanding every
                    field. Highlights values that differ from the
                    hard-coded plan defaults. */}
                <View style={styles.summaryRow}>
                  {[
                    { lbl: "Labels/mo", val: limits[key].label_cap, def: limitsDefaults[key].label_cap },
                    { lbl: "Bulk", val: limits[key].bulk_max, def: limitsDefaults[key].bulk_max },
                    { lbl: "Daily", val: limits[key].daily_cap === null ? "∞" : limits[key].daily_cap, def: limitsDefaults[key].daily_cap === null ? "∞" : limitsDefaults[key].daily_cap },
                    { lbl: "Variants", val: limits[key].packing_variant_cap, def: limitsDefaults[key].packing_variant_cap },
                    { lbl: "Team", val: limits[key].team_member_cap, def: limitsDefaults[key].team_member_cap },
                    { lbl: "Extra", val: `₹${limits[key].extra_member_price_inr}`, def: `₹${limitsDefaults[key].extra_member_price_inr ?? 0}` },
                  ].map((s, i) => {
                    const diff = String(s.val) !== String(s.def);
                    return (
                      <View key={i} style={[styles.summaryChip, diff && styles.summaryChipDirty]}>
                        <Text style={styles.summaryLbl}>{s.lbl}</Text>
                        <Text style={[styles.summaryVal, diff && { color: "#B45309" }]}>{s.val}</Text>
                      </View>
                    );
                  })}
                </View>

                {/* ── Group 1: Order Limits ── */}
                <Text style={styles.groupHeader}>📦 Order Limits</Text>
                <View style={styles.row2}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.label}>Labels / month</Text>
                    <TextInput
                      keyboardType="numeric"
                      value={String(limits[key].label_cap)}
                      onChangeText={(v) => updateLimit(key, {
                        label_cap: Number(v.replace(/[^0-9]/g, "")) || 0,
                      })}
                      style={styles.input}
                      placeholder={String(limitsDefaults[key].label_cap)}
                    />
                    <View style={styles.hintRow}>
                      <Text style={styles.defaultHint}>Default: {limitsDefaults[key].label_cap}</Text>
                      <TouchableOpacity
                        onPress={() => updateLimit(key, { label_cap: limitsDefaults[key].label_cap })}
                        style={styles.resetBtn}
                      >
                        <PhIcon name="refresh" size={11} color="#6B7280" />
                        <Text style={styles.resetBtnTxt}>Reset</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.label}>Bulk print max</Text>
                    <TextInput
                      keyboardType="numeric"
                      value={String(limits[key].bulk_max)}
                      onChangeText={(v) => updateLimit(key, {
                        bulk_max: Number(v.replace(/[^0-9]/g, "")) || 0,
                      })}
                      style={styles.input}
                      placeholder={String(limitsDefaults[key].bulk_max)}
                    />
                    <View style={styles.hintRow}>
                      <Text style={styles.defaultHint}>Default: {limitsDefaults[key].bulk_max} (0 = no bulk)</Text>
                      <TouchableOpacity
                        onPress={() => updateLimit(key, { bulk_max: limitsDefaults[key].bulk_max })}
                        style={styles.resetBtn}
                      >
                        <PhIcon name="refresh" size={11} color="#6B7280" />
                        <Text style={styles.resetBtnTxt}>Reset</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                </View>
                <View style={{ marginTop: 10 }}>
                  <Text style={styles.label}>Daily print limit</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={
                      limits[key].daily_cap === null ||
                      limits[key].daily_cap === undefined
                        ? ""
                        : String(limits[key].daily_cap)
                    }
                    onChangeText={(v) => {
                      const clean = v.replace(/[^0-9]/g, "");
                      updateLimit(key, {
                        daily_cap: clean === "" ? null : Number(clean),
                      });
                    }}
                    style={styles.input}
                    placeholder={
                      limitsDefaults[key].daily_cap === null ||
                      limitsDefaults[key].daily_cap === undefined
                        ? "— no cap —"
                        : String(limitsDefaults[key].daily_cap)
                    }
                  />
                  <View style={styles.hintRow}>
                    <Text style={styles.defaultHint}>
                      Default:{" "}
                      {limitsDefaults[key].daily_cap === null ||
                      limitsDefaults[key].daily_cap === undefined
                        ? "no daily cap"
                        : limitsDefaults[key].daily_cap}
                      {" "}· Blank = unlimited
                    </Text>
                    <TouchableOpacity
                      onPress={() => updateLimit(key, { daily_cap: limitsDefaults[key].daily_cap })}
                      style={styles.resetBtn}
                    >
                      <PhIcon name="refresh" size={11} color="#6B7280" />
                      <Text style={styles.resetBtnTxt}>Reset</Text>
                    </TouchableOpacity>
                  </View>
                </View>

                {/* ── Group 2: Packing Variants & Team ── */}
                <Text style={styles.groupHeader}>👥 Variants & Team</Text>
                <View style={styles.row2}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.label}>Packing variants / courier</Text>
                    <TextInput
                      keyboardType="numeric"
                      value={String(limits[key].packing_variant_cap)}
                      onChangeText={(v) => updateLimit(key, {
                        packing_variant_cap: Number(v.replace(/[^0-9]/g, "")) || 0,
                      })}
                      style={styles.input}
                      placeholder={String(limitsDefaults[key].packing_variant_cap ?? 0)}
                    />
                    <View style={styles.hintRow}>
                      <Text style={styles.defaultHint}>Default: {limitsDefaults[key].packing_variant_cap ?? 0}</Text>
                      <TouchableOpacity
                        onPress={() => updateLimit(key, { packing_variant_cap: limitsDefaults[key].packing_variant_cap ?? 0 })}
                        style={styles.resetBtn}
                      >
                        <PhIcon name="refresh" size={11} color="#6B7280" />
                        <Text style={styles.resetBtnTxt}>Reset</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.label}>Team members (free)</Text>
                    <TextInput
                      keyboardType="numeric"
                      value={String(limits[key].team_member_cap)}
                      onChangeText={(v) => updateLimit(key, {
                        team_member_cap: Number(v.replace(/[^0-9]/g, "")) || 0,
                      })}
                      style={styles.input}
                      placeholder={String(limitsDefaults[key].team_member_cap ?? 0)}
                    />
                    <View style={styles.hintRow}>
                      <Text style={styles.defaultHint}>Default: {limitsDefaults[key].team_member_cap ?? 0}</Text>
                      <TouchableOpacity
                        onPress={() => updateLimit(key, { team_member_cap: limitsDefaults[key].team_member_cap ?? 0 })}
                        style={styles.resetBtn}
                      >
                        <PhIcon name="refresh" size={11} color="#6B7280" />
                        <Text style={styles.resetBtnTxt}>Reset</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                </View>
                <View style={{ marginTop: 10 }}>
                  <Text style={styles.label}>Extra team-member price (₹/month)</Text>
                  <TextInput
                    keyboardType="numeric"
                    value={String(limits[key].extra_member_price_inr)}
                    onChangeText={(v) => updateLimit(key, {
                      extra_member_price_inr: Number(v.replace(/[^0-9]/g, "")) || 0,
                    })}
                    style={styles.input}
                    placeholder={String(limitsDefaults[key].extra_member_price_inr ?? 0)}
                  />
                  <View style={styles.hintRow}>
                    <Text style={styles.defaultHint}>
                      Default: ₹{limitsDefaults[key].extra_member_price_inr ?? 0}/mo · 0 = disable extras
                    </Text>
                    <TouchableOpacity
                      onPress={() => updateLimit(key, { extra_member_price_inr: limitsDefaults[key].extra_member_price_inr ?? 0 })}
                      style={styles.resetBtn}
                    >
                      <PhIcon name="refresh" size={11} color="#6B7280" />
                      <Text style={styles.resetBtnTxt}>Reset</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              </View>
            </View>
          );
        })}

        {/* ---- Countdown ---- */}
        <View style={[styles.card, { borderLeftColor: "#F59E0B", borderLeftWidth: 4 }]}>
          <Text style={[styles.planName, { color: "#B45309" }]}>⏳ Countdown banner</Text>

          <View style={[styles.row2, { marginTop: 6 }]}>
            <Text style={styles.smallLbl}>Show banner</Text>
            <Switch
              value={countdown.enabled}
              onValueChange={(v) => setCountdown({ ...countdown, enabled: v })}
              trackColor={{ false: "#D1D5DB", true: colors.primary }}
              thumbColor="#fff"
            />
          </View>

          <Text style={styles.label}>Mode</Text>
          <View style={styles.modeRow}>
            {(["off", "per_device", "global"] as const).map((m) => (
              <TouchableOpacity
                key={m}
                onPress={() => setCountdown({ ...countdown, mode: m })}
                style={[
                  styles.modeChip,
                  countdown.mode === m && { backgroundColor: colors.primary, borderColor: colors.primary },
                ]}
              >
                <Text style={[
                  styles.modeChipTxt,
                  countdown.mode === m && { color: "#fff" },
                ]}>
                  {m === "per_device" ? "Per-device" : m === "global" ? "Global deadline" : "Off"}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
          <Text style={styles.modeHint}>
            {countdown.mode === "per_device" &&
              "Each device sees a fresh timer starting on first visit."}
            {countdown.mode === "global" &&
              "Single deadline for everyone — set the date below."}
            {countdown.mode === "off" &&
              "No countdown shown. Strikethrough prices remain visible if enabled."}
          </Text>

          {countdown.mode === "per_device" ? (
            <View style={{ marginTop: 8 }}>
              <Text style={styles.label}>Countdown duration (minutes)</Text>
              <TextInput
                keyboardType="numeric"
                value={String(countdown.countdown_minutes)}
                onChangeText={(v) =>
                  setCountdown({
                    ...countdown,
                    countdown_minutes: Math.max(1, Number(v.replace(/[^0-9]/g, "")) || 60),
                  })
                }
                style={styles.input}
                placeholder="60"
              />
              <Text style={styles.hintTxt}>
                e.g. 60 = 1 hour, 1440 = 1 day, 4320 = 3 days.
              </Text>
            </View>
          ) : null}

          {countdown.mode === "global" ? (
            <View style={{ marginTop: 8 }}>
              <Text style={styles.label}>Global expiry (ISO datetime)</Text>
              <TextInput
                value={countdown.global_expires_at || ""}
                onChangeText={(v) =>
                  setCountdown({ ...countdown, global_expires_at: v || null })
                }
                style={styles.input}
                placeholder="2026-12-31T23:59:00+05:30"
                autoCapitalize="none"
              />
            </View>
          ) : null}

          <View style={{ marginTop: 8 }}>
            <Text style={styles.label}>Banner headline</Text>
            <TextInput
              value={countdown.headline}
              onChangeText={(v) => setCountdown({ ...countdown, headline: v })}
              style={styles.input}
              placeholder="Limited time offer — save up to 60%"
              maxLength={120}
            />
          </View>
        </View>
      </ScrollView>

      <View style={styles.saveBar}>
        <TouchableOpacity
          style={[styles.saveBtn, !isDirty && { opacity: 0.5 }]}
          disabled={!isDirty || saving}
          onPress={save}
        >
          {saving ? <ActivityIndicator color="#fff" /> : (
            <>
              <PhIcon name="save" size={18} color="#fff" />
              <Text style={styles.saveTxt}>{isDirty ? "Save pricing" : "No changes"}</Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  header: {
    paddingHorizontal: 20, paddingTop: 14, paddingBottom: 8,
    flexDirection: "row", alignItems: "center",
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  subtitle: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  dirtyBadge: {
    flexDirection: "row", alignItems: "center", gap: 5,
    backgroundColor: "#FEF3C7", borderWidth: 1, borderColor: "#FCD34D",
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
  },
  dirtyDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: "#D97706" },
  dirtyTxt: { fontSize: 10.5, fontWeight: "800", color: "#92400E", letterSpacing: 0.4 },
  infoBox: {
    flexDirection: "row", gap: 8, alignItems: "flex-start",
    backgroundColor: "#F0F9FF", padding: 10, borderRadius: 10,
    borderWidth: 1, borderColor: "#BAE6FD", marginBottom: 14,
  },
  infoTxt: { flex: 1, fontSize: 12, color: "#075985", lineHeight: 17 },
  couponsCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: "#FEF3C7", borderRadius: 12, padding: 14,
    marginBottom: 16, borderWidth: 1, borderColor: "#FBBF24",
  },
  couponsTitle: { fontWeight: "800", fontSize: 15, color: "#92400E", marginBottom: 4 },
  couponsSub:   { fontSize: 12, color: "#92400E", lineHeight: 17 },
  card: {
    backgroundColor: colors.surface, borderRadius: 14,
    borderWidth: 1, borderColor: "#E5E7EB", padding: 14, marginBottom: 14,
  },
  cardHead: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    marginBottom: 8,
  },
  planName: { fontSize: 18, fontWeight: "900" },
  sectionLbl: {
    fontSize: 11, fontWeight: "800", color: colors.textMuted,
    letterSpacing: 0.6, textTransform: "uppercase", marginTop: 8, marginBottom: 4,
    flex: 1,
  },
  row2: { flexDirection: "row", gap: 10, alignItems: "center", marginBottom: 8 },
  label: {
    fontSize: 11, fontWeight: "800", color: colors.textMuted,
    marginBottom: 4, letterSpacing: 0.4, textTransform: "uppercase",
  },
  smallLbl: { fontSize: 12, fontWeight: "700", color: colors.text },
  input: {
    backgroundColor: "#F8FAFC", borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 14, color: colors.text,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  miniBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#FFF7ED", borderColor: colors.primary, borderWidth: 1,
    paddingHorizontal: 10, paddingVertical: 5, borderRadius: 999,
  },
  miniBtnTxt: { color: colors.primary, fontSize: 11, fontWeight: "800" },
  previewBox: {
    backgroundColor: "#F8FAFC", borderRadius: 10, padding: 10, marginTop: 6,
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  previewLine: { fontSize: 12.5, color: "#1F2937", marginBottom: 3 },
  strike: { textDecorationLine: "line-through", color: "#94A3B8", fontWeight: "700" },
  previewSavings: { fontSize: 11.5, color: "#047857", fontWeight: "800", marginTop: 4 },
  modeRow: { flexDirection: "row", gap: 8, marginTop: 4, marginBottom: 4 },
  modeChip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999,
    backgroundColor: "#F1F5F9", borderWidth: 1, borderColor: "#CBD5E1",
  },
  modeChipTxt: { fontSize: 12, fontWeight: "800", color: "#475569" },
  modeHint: { fontSize: 11, color: "#64748B", marginBottom: 6 },
  hintTxt: { fontSize: 11, color: "#64748B", marginTop: 4 },
  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    padding: 14, paddingBottom: Platform.OS === "ios" ? 28 : 14,
    backgroundColor: "rgba(244,245,247,0.96)",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 8, backgroundColor: colors.primary, paddingVertical: 16, borderRadius: 14,
  },
  saveTxt: { color: "#fff", fontWeight: "800", fontSize: 15 },

  // Phase-13 — Plan Limits subsection (inside each plan card).
  limitsSection: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#E2E8F0",
  },
  limitsHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 8,
  },
  limitsTitle: {
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.4,
    textTransform: "uppercase",
  },
  defaultHint: {
    fontSize: 10.5,
    color: "#94A3B8",
    marginTop: 4,
    fontStyle: "italic",
  },
});
