/**
 * Admin — Plan Limits panel (Phase-13).
 *
 * Lets the admin tune every NUMERIC field inside a plan (Free Trial,
 * Silver, Gold, Platinum) without a code push:
 *   • Monthly label cap
 *   • Bulk print max
 *   • Daily print cap (0 = no cap)
 *   • Price (₹)
 *   • Trial days (only meaningful for Free Trial)
 *
 * Marketing copy (name, tagline, badge) is INTENTIONALLY not here —
 * it lives in /app/backend/plans.py and is owned by the codebase.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type PlanKey = "free_trial" | "silver" | "gold" | "platinum";

type PlanRow = {
  label_cap: number;
  bulk_max: number;
  daily_cap: number | null;
  price_inr: number;
  trial_days: number | null;
};

type Payload = {
  order: PlanKey[];
  defaults: Record<PlanKey, PlanRow & { name: string; period: string }>;
  current: Record<PlanKey, PlanRow>;
};

const PLAN_META: Record<PlanKey, { label: string; color: string; accent: string }> = {
  free_trial: { label: "Free Trial", color: "#7C3AED", accent: "#F5F3FF" },
  silver:     { label: "Silver",     color: "#475569", accent: "#F1F5F9" },
  gold:       { label: "Gold",       color: "#B45309", accent: "#FEF3C7" },
  platinum:   { label: "Platinum",   color: "#1E3A8A", accent: "#DBEAFE" },
};

// Which fields are editable for each plan. Non-applicable fields
// stay hidden so the admin never sets a value that has no effect.
const FIELDS_BY_PLAN: Record<PlanKey, Array<keyof PlanRow>> = {
  free_trial: ["label_cap", "trial_days"],
  silver:     ["label_cap", "bulk_max", "daily_cap", "price_inr"],
  gold:       ["label_cap", "bulk_max", "daily_cap", "price_inr"],
  platinum:   ["label_cap", "bulk_max", "daily_cap", "price_inr"],
};

const FIELD_META: Record<keyof PlanRow, { label: string; hint: string; suffix?: string }> = {
  label_cap:  { label: "Monthly Label Limit",  hint: "Labels/shipments per period" },
  bulk_max:   { label: "Bulk Print Max",       hint: "0 = no bulk print; otherwise max per batch" },
  daily_cap:  { label: "Daily Print Limit",    hint: "0 = no daily limit (blank = no cap)" },
  price_inr:  { label: "Price",                hint: "Monthly price in ₹", suffix: "₹/mo" },
  trial_days: { label: "Trial Days",           hint: "How many days the free trial runs" },
};

function normaliseRow(row: PlanRow): PlanRow {
  return {
    label_cap:  Number(row.label_cap) || 0,
    bulk_max:   Number(row.bulk_max) || 0,
    daily_cap:  row.daily_cap === null || row.daily_cap === undefined
                  ? null
                  : (Number(row.daily_cap) || 0),
    price_inr:  Number(row.price_inr) || 0,
    trial_days: row.trial_days === null || row.trial_days === undefined
                  ? null
                  : (Number(row.trial_days) || 0),
  };
}

function snapshotOf(rows: Record<PlanKey, PlanRow>): string {
  return JSON.stringify(
    (Object.keys(rows) as PlanKey[]).sort().map((k) => [k, normaliseRow(rows[k])]),
  );
}

export default function AdminPlanLimitsScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [defaults, setDefaults] = useState<Payload["defaults"] | null>(null);
  const [rows, setRows] = useState<Record<PlanKey, PlanRow> | null>(null);
  const [originalSnap, setOriginalSnap] = useState("");

  // Non-admin guard.
  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only the admin can edit plan limits.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const r = await api.get<Payload>("/admin/plan-limits");
      setDefaults(r.data.defaults);
      setRows(r.data.current);
      setOriginalSnap(snapshotOf(r.data.current));
    } catch (e: any) {
      Alert.alert(
        "Load failed",
        e?.response?.data?.detail || e?.message || "Could not load plan limits.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const dirty = useMemo(
    () => !!rows && snapshotOf(rows) !== originalSnap,
    [rows, originalSnap],
  );

  const updateField = (plan: PlanKey, field: keyof PlanRow, raw: string) => {
    if (!rows) return;
    const clean = raw.replace(/[^0-9]/g, "");
    const next = { ...rows };
    if (field === "daily_cap" || field === "trial_days") {
      // Empty string means "no cap" (null) for daily_cap; 0 or empty = null for trial
      if (clean === "") {
        next[plan] = { ...next[plan], [field]: null };
      } else {
        next[plan] = { ...next[plan], [field]: Number(clean) };
      }
    } else {
      next[plan] = { ...next[plan], [field]: clean === "" ? 0 : Number(clean) };
    }
    setRows(next);
  };

  const resetPlanToDefault = (plan: PlanKey) => {
    if (!rows || !defaults) return;
    const d = defaults[plan];
    setRows({
      ...rows,
      [plan]: {
        label_cap: d.label_cap,
        bulk_max:  d.bulk_max,
        daily_cap: d.daily_cap,
        price_inr: d.price_inr,
        trial_days: d.trial_days,
      },
    });
  };

  const handleSave = async () => {
    if (!rows) return;
    try {
      setSaving(true);
      const payload = {
        plans: rows,
      };
      const r = await api.put<Payload>("/admin/plan-limits", payload);
      setDefaults(r.data.defaults);
      setRows(r.data.current);
      setOriginalSnap(snapshotOf(r.data.current));
      Alert.alert("Saved", "Plan limits updated. Users will see the new numbers on next reload.");
    } catch (e: any) {
      Alert.alert(
        "Save failed",
        e?.response?.data?.detail || e?.message || "Could not save plan limits.",
      );
    } finally {
      setSaving(false);
    }
  };

  const handleResetAll = () => {
    Alert.alert(
      "Restore Defaults",
      "This will discard ALL overrides and restore the original plan numbers from code. Continue?",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Restore",
          style: "destructive",
          onPress: async () => {
            try {
              setSaving(true);
              const r = await api.post<Payload>("/admin/plan-limits/reset");
              setDefaults(r.data.defaults);
              setRows(r.data.current);
              setOriginalSnap(snapshotOf(r.data.current));
              Alert.alert("Done", "Defaults restored.");
            } catch (e: any) {
              Alert.alert(
                "Reset failed",
                e?.response?.data?.detail || e?.message || "Could not reset.",
              );
            } finally {
              setSaving(false);
            }
          },
        },
      ],
    );
  };

  if (loading || !rows || !defaults) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.header}>
          <TouchableOpacity
            onPress={() => router.back()}
            style={styles.backBtn}
            hitSlop={10}
          >
            <Ionicons name="chevron-back" size={24} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.headerTitle}>Plan Limits</Text>
          <View style={{ width: 36 }} />
        </View>
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingText}>Loading plan limits…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity
          onPress={() => router.back()}
          style={styles.backBtn}
          hitSlop={10}
        >
          <Ionicons name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Plan Limits</Text>
        <TouchableOpacity
          onPress={handleResetAll}
          style={styles.resetAllBtn}
          hitSlop={8}
        >
          <Ionicons name="refresh" size={18} color="#DC2626" />
        </TouchableOpacity>
      </View>

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
            <Ionicons name="information-circle" size={18} color="#2563EB" />
            <Text style={styles.introText}>
              Edit the numeric limits for each plan. Marketing names and
              tagline stay fixed in code. Blank = fall back to default.
            </Text>
          </View>

          {(Object.keys(PLAN_META) as PlanKey[]).map((planKey) => {
            const meta = PLAN_META[planKey];
            const d = defaults[planKey];
            const row = rows[planKey];
            const editableFields = FIELDS_BY_PLAN[planKey];
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
                      <View style={[styles.planBadge, { backgroundColor: meta.accent }]}>
                        <Text style={[styles.planBadgeText, { color: meta.color }]}>
                          {d.period === "trial" ? "One-time trial" : "Monthly"}
                        </Text>
                      </View>
                    </View>
                    <Text style={styles.planSub}>{d.name}</Text>
                  </View>
                  <TouchableOpacity
                    onPress={() => resetPlanToDefault(planKey)}
                    style={styles.planResetBtn}
                    hitSlop={8}
                  >
                    <Text style={styles.planResetTxt}>Reset</Text>
                  </TouchableOpacity>
                </View>

                {editableFields.map((field) => {
                  const fm = FIELD_META[field];
                  const defaultValue = d[field];
                  const currentValue = row[field];
                  // Display empty for null (no cap) so the user can clear it.
                  const text =
                    currentValue === null || currentValue === undefined
                      ? ""
                      : String(currentValue);
                  const isOverridden =
                    normaliseRow({ ...row })[field] !==
                    normaliseRow({ ...d } as PlanRow)[field];
                  return (
                    <View key={field} style={styles.fieldRow}>
                      <View style={styles.fieldLabelCol}>
                        <View style={styles.fieldLabelLine}>
                          <Text style={styles.fieldLabel}>{fm.label}</Text>
                          {isOverridden && (
                            <View style={styles.overrideDot} />
                          )}
                        </View>
                        <Text style={styles.fieldHint}>{fm.hint}</Text>
                        <Text style={styles.fieldDefault}>
                          Default: {defaultValue === null || defaultValue === undefined
                            ? "—"
                            : String(defaultValue)}
                        </Text>
                      </View>
                      <View style={styles.fieldInputCol}>
                        <TextInput
                          style={styles.input}
                          value={text}
                          onChangeText={(v) => updateField(planKey, field, v)}
                          keyboardType="number-pad"
                          placeholder={
                            defaultValue === null || defaultValue === undefined
                              ? "—"
                              : String(defaultValue)
                          }
                          placeholderTextColor="#9CA3AF"
                        />
                        {fm.suffix && (
                          <Text style={styles.suffix}>{fm.suffix}</Text>
                        )}
                      </View>
                    </View>
                  );
                })}
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
            testID="save-plan-limits"
          >
            {saving ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <Ionicons name="save-outline" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>Save Changes</Text>
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
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
  resetAllBtn: {
    padding: 6,
    width: 36,
    alignItems: "center",
  },
  loadingBox: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10 },
  loadingText: { color: "#6B7280", fontSize: 13 },

  introBox: {
    flexDirection: "row",
    gap: 8,
    padding: 12,
    marginHorizontal: 12,
    marginTop: 12,
    backgroundColor: "#DBEAFE",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#BFDBFE",
  },
  introText: { flex: 1, fontSize: 12, color: "#1E3A8A", lineHeight: 17 },

  planCard: {
    marginHorizontal: 12,
    marginTop: 14,
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderLeftWidth: 4,
    padding: 14,
  },
  planHeaderRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    marginBottom: 10,
  },
  planTitleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  planName: { fontSize: 17, fontWeight: "800" },
  planBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 8,
  },
  planBadgeText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.3 },
  planSub: { fontSize: 12, color: "#6B7280", marginTop: 2 },
  planResetBtn: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: "#F3F4F6",
    borderRadius: 8,
  },
  planResetTxt: { fontSize: 11, fontWeight: "700", color: "#6B7280" },

  fieldRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: "#F3F4F6",
    gap: 12,
  },
  fieldLabelCol: { flex: 1 },
  fieldLabelLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  fieldLabel: { fontSize: 13, fontWeight: "700", color: colors.text },
  overrideDot: {
    width: 6, height: 6, borderRadius: 3, backgroundColor: "#F59E0B",
  },
  fieldHint: { fontSize: 10, color: "#9CA3AF", marginTop: 2 },
  fieldDefault: { fontSize: 10, color: "#6B7280", marginTop: 2, fontStyle: "italic" },

  fieldInputCol: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  input: {
    width: 86,
    height: 38,
    borderWidth: 1,
    borderColor: "#D1D5DB",
    borderRadius: 8,
    paddingHorizontal: 10,
    fontSize: 14,
    fontWeight: "700",
    color: colors.text,
    textAlign: "center",
    backgroundColor: "#F9FAFB",
  },
  suffix: { fontSize: 11, color: "#6B7280", fontWeight: "600" },

  saveBar: {
    position: "absolute",
    left: 0, right: 0, bottom: 0,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 12,
    paddingBottom: Platform.OS === "ios" ? 24 : 12,
    backgroundColor: "#fff",
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  saveStatus: { fontSize: 12, color: "#6B7280", fontWeight: "600" },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
  },
  saveBtnDisabled: { backgroundColor: "#D1D5DB" },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
