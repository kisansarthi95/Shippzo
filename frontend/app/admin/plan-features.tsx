/**
 * Admin Plan-Features panel.
 *
 * Visible only to users with `is_admin = true` (the very first signup).
 * Lets the admin tick which features are enabled per plan tier
 * (Free Trial, Silver, Gold, Platinum) and saves the matrix in one call.
 *
 * Layout:
 *   - Top tab strip to switch between plans (a single screen would scroll
 *     forever otherwise — there are 39 features × 4 plans).
 *   - Inside each plan, features are grouped by category for scannability.
 *   - "Save" button is sticky at the bottom and only enabled when dirty.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Switch, ActivityIndicator, Alert, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { useFeatureFlags } from "../../lib/feature_flags";
import { colors } from "../../lib/theme";

type Feature = { key: string; label: string; category: string };
type PlanKey = "free_trial" | "silver" | "gold" | "platinum";

const PLAN_TABS: { key: PlanKey; label: string; color: string }[] = [
  { key: "free_trial", label: "Free Trial", color: "#7C3AED" },
  { key: "silver",     label: "Silver",     color: "#475569" },
  { key: "gold",       label: "Gold",       color: "#B45309" },
  { key: "platinum",   label: "Platinum",   color: "#1E3A8A" },
];

export default function AdminPlanFeaturesScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const { refresh } = useFeatureFlags();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [registry, setRegistry] = useState<{ categories: string[]; features: Feature[] }>({
    categories: [], features: [],
  });
  const [plans, setPlans] = useState<Record<PlanKey, Set<string>>>({
    free_trial: new Set(), silver: new Set(), gold: new Set(), platinum: new Set(),
  });
  const [originalSnap, setOriginalSnap] = useState("");
  const [activePlan, setActivePlan] = useState<PlanKey>("free_trial");

  // Block non-admin users from even reaching the UI.
  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only the admin can manage plan features.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  // Load registry + current matrix.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setLoading(true);
        const r = await api.get<{
          registry: { categories: string[]; features: Feature[] };
          plans: Record<string, string[]>;
        }>("/admin/plan-features");
        if (cancelled) return;
        setRegistry(r.data.registry);
        const built: any = {};
        (["free_trial", "silver", "gold", "platinum"] as PlanKey[]).forEach((p) => {
          built[p] = new Set(r.data.plans[p] || []);
        });
        setPlans(built);
        setOriginalSnap(JSON.stringify({
          free_trial: [...built.free_trial].sort(),
          silver:     [...built.silver].sort(),
          gold:       [...built.gold].sort(),
          platinum:   [...built.platinum].sort(),
        }));
      } catch (e: any) {
        Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Group features by category for the UI.
  const grouped = useMemo(() => {
    const map: Record<string, Feature[]> = {};
    registry.features.forEach((f) => {
      (map[f.category] ||= []).push(f);
    });
    return registry.categories.map((c) => ({ category: c, features: map[c] || [] }));
  }, [registry]);

  // Dirty detection — same JSON.stringify sorted-keys trick used in Settings.
  const liveSnap = useMemo(() => JSON.stringify({
    free_trial: [...plans.free_trial].sort(),
    silver:     [...plans.silver].sort(),
    gold:       [...plans.gold].sort(),
    platinum:   [...plans.platinum].sort(),
  }), [plans]);
  const isDirty = !!originalSnap && originalSnap !== liveSnap;

  const toggle = (planKey: PlanKey, featureKey: string) => {
    setPlans((prev) => {
      const next: Record<PlanKey, Set<string>> = { ...prev };
      const set = new Set(prev[planKey]);
      if (set.has(featureKey)) set.delete(featureKey);
      else set.add(featureKey);
      next[planKey] = set;
      return next;
    });
  };

  const toggleAll = (planKey: PlanKey, on: boolean) => {
    setPlans((prev) => ({
      ...prev,
      [planKey]: new Set(on ? registry.features.map((f) => f.key) : []),
    }));
  };

  const save = async () => {
    try {
      setSaving(true);
      const payload = {
        plans: {
          free_trial: [...plans.free_trial],
          silver:     [...plans.silver],
          gold:       [...plans.gold],
          platinum:   [...plans.platinum],
        },
      };
      await api.put("/admin/plan-features", payload);
      // Refresh the user-facing flags so admin sees changes immediately
      // in their own session without a full reload.
      await refresh();
      setOriginalSnap(liveSnap);
      Alert.alert("Saved", "Plan features updated for all users.");
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
    Alert.alert(
      "Unsaved changes",
      "તમે કેટલાક ફેરફાર કર્યા છે. શું કરવા માંગો છો?",
      [
        { text: "Keep editing", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => router.replace("/(tabs)/settings"),
        },
        { text: "Save", onPress: save },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.loadWrap}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadTxt}>Loading…</Text>
        </View>
      </SafeAreaView>
    );
  }

  const activeSet = plans[activePlan];
  const totalFeatures = registry.features.length;
  const enabledCount = activeSet.size;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={handleBack} hitSlop={10} style={{ marginRight: 8 }}>
          <Ionicons name="chevron-back" size={26} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <Text style={styles.title}>Plan Features</Text>
            {isDirty && (
              <View style={styles.dirtyBadge}>
                <View style={styles.dirtyDot} />
                <Text style={styles.dirtyTxt}>Unsaved</Text>
              </View>
            )}
          </View>
          <Text style={styles.subtitle}>Tick which features each plan includes</Text>
        </View>
      </View>

      {/* Plan Tabs */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.tabsScroll}
        contentContainerStyle={styles.tabs}
      >
        {PLAN_TABS.map((p) => {
          const active = p.key === activePlan;
          const count = plans[p.key].size;
          return (
            <TouchableOpacity
              key={p.key}
              onPress={() => setActivePlan(p.key)}
              style={[
                styles.tab,
                active && { backgroundColor: p.color, borderColor: p.color },
              ]}
              testID={`plan-tab-${p.key}`}
            >
              <Text style={[styles.tabTxt, active && { color: "#fff" }]}>
                {p.label}
              </Text>
              <View style={[styles.tabCount, active && { backgroundColor: "rgba(255,255,255,0.3)" }]}>
                <Text style={[styles.tabCountTxt, active && { color: "#fff" }]}>
                  {count}
                </Text>
              </View>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {/* Feature list grouped by category */}
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 16, paddingBottom: 120 }}
      >
        <View style={styles.summaryRow}>
          <Text style={styles.summaryTxt}>
            {enabledCount} of {totalFeatures} features enabled
          </Text>
          <View style={{ flexDirection: "row", gap: 6 }}>
            <TouchableOpacity
              onPress={() => toggleAll(activePlan, true)}
              style={styles.bulkBtn}
              testID="select-all-btn"
            >
              <Text style={styles.bulkBtnTxt}>All</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => toggleAll(activePlan, false)}
              style={styles.bulkBtn}
              testID="select-none-btn"
            >
              <Text style={styles.bulkBtnTxt}>None</Text>
            </TouchableOpacity>
          </View>
        </View>

        {grouped.map((g) => (
          <View key={g.category} style={styles.group}>
            <Text style={styles.groupTitle}>{g.category}</Text>
            <View style={styles.groupCard}>
              {g.features.map((f, idx) => {
                const enabled = activeSet.has(f.key);
                return (
                  <View
                    key={f.key}
                    style={[
                      styles.row,
                      idx === g.features.length - 1 && { borderBottomWidth: 0 },
                    ]}
                  >
                    <Text style={styles.rowLabel} numberOfLines={2}>{f.label}</Text>
                    <Switch
                      testID={`feat-${activePlan}-${f.key}`}
                      value={enabled}
                      onValueChange={() => toggle(activePlan, f.key)}
                      trackColor={{ false: "#D1D5DB", true: colors.primary }}
                      thumbColor="#fff"
                    />
                  </View>
                );
              })}
            </View>
          </View>
        ))}
      </ScrollView>

      {/* Sticky Save bar */}
      <View style={styles.saveBar}>
        <TouchableOpacity
          testID="save-plan-features"
          style={[styles.saveBtn, !isDirty && { opacity: 0.5 }]}
          disabled={!isDirty || saving}
          onPress={save}
        >
          {saving ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <>
              <Ionicons name="save" size={18} color="#fff" />
              <Text style={styles.saveTxt}>
                {isDirty ? "Save changes" : "No changes"}
              </Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  loadWrap: { flex: 1, alignItems: "center", justifyContent: "center", gap: 12 },
  loadTxt: { color: colors.textMuted, fontSize: 14 },
  header: {
    paddingHorizontal: 20,
    paddingTop: 14,
    paddingBottom: 8,
    flexDirection: "row",
    alignItems: "center",
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  subtitle: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  dirtyBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    backgroundColor: "#FEF3C7",
    borderWidth: 1,
    borderColor: "#FCD34D",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  dirtyDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: "#D97706" },
  dirtyTxt: { fontSize: 10.5, fontWeight: "800", color: "#92400E", letterSpacing: 0.4 },
  tabsScroll: { flexGrow: 0, marginTop: 4 },
  tabs: { paddingHorizontal: 16, gap: 8, paddingVertical: 8 },
  tab: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 999,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: colors.surface,
  },
  tabTxt: { fontSize: 13, fontWeight: "700", color: colors.text },
  tabCount: {
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 8,
    backgroundColor: "#F1F5F9",
    minWidth: 22,
    alignItems: "center",
  },
  tabCountTxt: { fontSize: 11, fontWeight: "800", color: colors.text },
  summaryRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 12,
  },
  summaryTxt: { fontSize: 13, color: colors.textMuted, fontWeight: "600" },
  bulkBtn: {
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: colors.surface,
  },
  bulkBtnTxt: { fontSize: 12, fontWeight: "700", color: colors.text },
  group: { marginBottom: 18 },
  groupTitle: {
    fontSize: 11,
    fontWeight: "800",
    color: colors.textMuted,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    marginBottom: 8,
    paddingHorizontal: 4,
  },
  groupCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    overflow: "hidden",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderBottomWidth: 1,
    borderBottomColor: "#F1F5F9",
    gap: 10,
  },
  rowLabel: { flex: 1, fontSize: 14, color: colors.text, fontWeight: "500" },
  saveBar: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    padding: 14,
    paddingBottom: Platform.OS === "ios" ? 28 : 14,
    backgroundColor: "rgba(244,245,247,0.96)",
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: colors.primary,
    paddingVertical: 16,
    borderRadius: 14,
  },
  saveTxt: { color: "#fff", fontWeight: "800", fontSize: 15 },
});
