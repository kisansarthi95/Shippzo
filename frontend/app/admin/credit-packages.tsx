/**
 * Admin → Credit Packages editor (Phase-5b).
 *
 * Lets the admin curate the top-up presets that appear on every user's
 * Wallet screen. Each package = amount_inr -> credits + auto-computed
 * bonus + optional label/popular flag.
 *
 * Why a dedicated screen?  The packages list is dynamic length, has
 * per-row Add/Remove + a Popular badge, and shouldn't stretch the
 * already-busy Plan Features page.
 */
import React, { useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type Pkg = {
  amount_inr: number;
  credits: number;
  bonus: number;
  label: string;
  popular: boolean;
};

export default function AdminCreditPackagesScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [packages, setPackages] = useState<Pkg[]>([]);
  const [originalSnap, setOriginalSnap] = useState("");

  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only admin can edit packages.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get<{ credit_packages: Pkg[] }>("/admin/global-config");
        if (cancelled) return;
        const list = (r.data.credit_packages || []).map((p) => ({
          amount_inr: Number(p.amount_inr) || 0,
          credits: Number(p.credits) || 0,
          bonus: Number(p.bonus) || 0,
          label: String(p.label || ""),
          popular: !!p.popular,
        }));
        setPackages(list);
        setOriginalSnap(JSON.stringify(list));
      } catch (e: any) {
        Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Auto-compute bonus whenever credits >= amount.
  const updateRow = (idx: number, patch: Partial<Pkg>) => {
    setPackages((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      const r = next[idx];
      r.bonus = Math.max(0, Number(r.credits) - Number(r.amount_inr));
      return next;
    });
  };

  const removeRow = (idx: number) => {
    setPackages((prev) => prev.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    setPackages((prev) => [
      ...prev,
      { amount_inr: 100, credits: 100, bonus: 0, label: "", popular: false },
    ]);
  };

  const liveSnap = useMemo(() => JSON.stringify(packages), [packages]);
  const isDirty = !!originalSnap && originalSnap !== liveSnap;

  const save = async () => {
    // Front-end validation: each package must have amount_inr>=1 and credits>=amount.
    for (const p of packages) {
      if (!p.amount_inr || p.amount_inr < 1) {
        Alert.alert("Invalid amount", "Each package needs a positive ₹ amount.");
        return;
      }
      if (!p.credits || p.credits < p.amount_inr) {
        Alert.alert("Invalid credits", "Credits must be at least equal to amount (no negative bonus).");
        return;
      }
    }
    try {
      setSaving(true);
      await api.put("/admin/global-config", { credit_packages: packages });
      setOriginalSnap(liveSnap);
      Alert.alert("Saved", "Credit packages updated. All users will see the new options.");
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

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
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
            <Text style={styles.title}>Credit Packages</Text>
            {isDirty && (
              <View style={styles.dirtyBadge}>
                <View style={styles.dirtyDot} />
                <Text style={styles.dirtyTxt}>Unsaved</Text>
              </View>
            )}
          </View>
          <Text style={styles.subtitle}>Top-up bundles users see in Wallet</Text>
        </View>
      </View>

      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 16, paddingBottom: 120 }}>
        <View style={styles.infoBox}>
          <PhIcon name="information-circle" size={18} color="#0EA5E9" />
          <Text style={styles.infoTxt}>
            Bonus is auto-computed (credits − amount). Mark one package as Popular to highlight it.
          </Text>
        </View>

        {packages.map((p, idx) => (
          <View key={idx} style={styles.card} testID={`pkg-row-${idx}`}>
            <View style={styles.cardHead}>
              <Text style={styles.cardIdx}>Package #{idx + 1}</Text>
              <TouchableOpacity
                onPress={() => removeRow(idx)}
                style={styles.removeBtn}
                testID={`pkg-remove-${idx}`}
              >
                <PhIcon name="trash-outline" size={16} color="#B91C1C" />
                <Text style={styles.removeTxt}>Remove</Text>
              </TouchableOpacity>
            </View>

            <View style={styles.row2}>
              <View style={{ flex: 1 }}>
                <Text style={styles.label}>Amount (₹)</Text>
                <TextInput
                  testID={`pkg-amount-${idx}`}
                  keyboardType="numeric"
                  value={String(p.amount_inr)}
                  onChangeText={(v) => updateRow(idx, { amount_inr: Number(v.replace(/[^0-9]/g, "")) || 0 })}
                  style={styles.input}
                  placeholder="100"
                />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.label}>Credits given</Text>
                <TextInput
                  testID={`pkg-credits-${idx}`}
                  keyboardType="numeric"
                  value={String(p.credits)}
                  onChangeText={(v) => updateRow(idx, { credits: Number(v.replace(/[^0-9.]/g, "")) || 0 })}
                  style={styles.input}
                  placeholder="120"
                />
              </View>
            </View>

            <View style={styles.row2}>
              <View style={{ flex: 1 }}>
                <Text style={styles.label}>Label (optional)</Text>
                <TextInput
                  testID={`pkg-label-${idx}`}
                  value={p.label}
                  onChangeText={(v) => updateRow(idx, { label: v })}
                  style={styles.input}
                  placeholder="Saver / Pro"
                  maxLength={40}
                />
              </View>
              <View style={{ width: 110 }}>
                <Text style={styles.label}>Popular</Text>
                <View style={styles.popularRow}>
                  <Switch
                    testID={`pkg-popular-${idx}`}
                    value={p.popular}
                    onValueChange={(v) => updateRow(idx, { popular: v })}
                    trackColor={{ false: "#D1D5DB", true: colors.primary }}
                    thumbColor="#fff"
                  />
                </View>
              </View>
            </View>

            {p.bonus > 0 ? (
              <View style={styles.bonusBadge}>
                <PhIcon name="gift" size={14} color="#047857" />
                <Text style={styles.bonusTxt}>+{p.bonus} bonus credits</Text>
              </View>
            ) : (
              <View style={styles.noBonus}>
                <Text style={styles.noBonusTxt}>No bonus (1:1)</Text>
              </View>
            )}
          </View>
        ))}

        <TouchableOpacity onPress={addRow} style={styles.addBtn} testID="pkg-add-btn">
          <PhIcon name="add-circle" size={18} color={colors.primary} />
          <Text style={styles.addTxt}>Add a new package</Text>
        </TouchableOpacity>
      </ScrollView>

      <View style={styles.saveBar}>
        <TouchableOpacity
          testID="pkg-save"
          style={[styles.saveBtn, !isDirty && { opacity: 0.5 }]}
          disabled={!isDirty || saving}
          onPress={save}
        >
          {saving ? <ActivityIndicator color="#fff" /> : (
            <>
              <PhIcon name="save" size={18} color="#fff" />
              <Text style={styles.saveTxt}>{isDirty ? "Save packages" : "No changes"}</Text>
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
  card: {
    backgroundColor: colors.surface, borderRadius: 14,
    borderWidth: 1, borderColor: "#E5E7EB", padding: 14, marginBottom: 12,
  },
  cardHead: {
    flexDirection: "row", justifyContent: "space-between",
    alignItems: "center", marginBottom: 10,
  },
  cardIdx: { fontSize: 12, fontWeight: "800", color: colors.textMuted, letterSpacing: 0.5, textTransform: "uppercase" },
  removeBtn: { flexDirection: "row", alignItems: "center", gap: 4 },
  removeTxt: { fontSize: 12, color: "#B91C1C", fontWeight: "700" },
  row2: { flexDirection: "row", gap: 10, marginBottom: 8 },
  label: { fontSize: 11, fontWeight: "800", color: colors.textMuted, marginBottom: 4, letterSpacing: 0.4, textTransform: "uppercase" },
  input: {
    backgroundColor: "#F8FAFC", borderRadius: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 14, color: colors.text,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  popularRow: { paddingVertical: 6 },
  bonusBadge: {
    flexDirection: "row", alignItems: "center", gap: 6,
    alignSelf: "flex-start",
    backgroundColor: "#D1FAE5", paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 999, marginTop: 4,
  },
  bonusTxt: { fontSize: 12, fontWeight: "800", color: "#047857" },
  noBonus: {
    alignSelf: "flex-start", paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 999, backgroundColor: "#F1F5F9", marginTop: 4,
  },
  noBonusTxt: { fontSize: 11, color: "#64748B", fontWeight: "700" },
  addBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, paddingVertical: 14, borderRadius: 12,
    borderWidth: 1.5, borderStyle: "dashed", borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  addTxt: { color: colors.primary, fontWeight: "800", fontSize: 14 },
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
});
