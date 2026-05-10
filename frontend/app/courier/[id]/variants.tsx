/**
 * Courier Packing Variants Manager — Phase 2
 * ──────────────────────────────────────────
 * Per-courier list of "Packing Variants" with:
 *   • Name, Package Type (Cover / Box / Tube …), Category
 *   • Dimensions L × W × H (cm) and Weight (g)
 *   • Within-state ₹ rate and Outside-state ₹ rate
 *
 * Plan-wise cap (free=1, silver=2, gold=5, platinum=8 — admin-tunable)
 * is enforced server-side; the screen surfaces "x/cap used" so the
 * user knows when to upgrade.
 *
 * Add / Edit happens in a single bottom-sheet form. Delete is
 * confirmed via Alert so a single mis-tap doesn't wipe a tariff.
 */
import React, { useCallback, useEffect, useState } from "react";
import PhIcon from "../../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Alert, ActivityIndicator, TextInput, KeyboardAvoidingView,
  Platform, Modal,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Api } from "../../../lib/api";
import { colors } from "../../../lib/theme";

type Variant = {
  id: string;
  variant_name: string;
  package_type: string;
  category: string;
  length_cm: number;
  width_cm: number;
  height_cm: number;
  weight_g: number;
  within_state_rate: number;
  outside_state_rate: number;
  active: boolean;
};

export default function CourierVariantsScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const courierId = String(id || "");

  const [loading, setLoading]   = useState(true);
  const [saving, setSaving]     = useState(false);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [cap, setCap]           = useState<number | null>(null);
  const [packageTypes, setPackageTypes] = useState<string[]>([]);
  const [categories, setCategories]     = useState<string[]>([]);

  // Phase 2D — "Copy from another courier" support. We keep a list of
  // couriers that have at least one active variant so we can offer a
  // one-tap clone when the user lands on a fresh courier with no
  // variants.
  const [copySources, setCopySources] = useState<Array<{
    courier_id: string;
    courier_name: string;
    variant_count: number;
  }>>([]);
  const [copyPickerOpen, setCopyPickerOpen] = useState(false);
  const [copying, setCopying] = useState(false);

  // Editor modal state
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing]       = useState<Variant | null>(null);
  const [name, setName]                 = useState("");
  const [pkgType, setPkgType]           = useState("");
  const [category, setCategory]         = useState("");
  const [lengthCm, setLengthCm]         = useState("");
  const [widthCm, setWidthCm]           = useState("");
  const [heightCm, setHeightCm]         = useState("");
  const [weightG, setWeightG]           = useState("");
  const [withinRate, setWithinRate]     = useState("");
  const [outsideRate, setOutsideRate]   = useState("");

  const load = useCallback(async () => {
    try {
      const r = await Api.listCourierVariants(courierId);
      setVariants(r.variants as Variant[]);
      setCap(r.cap);
      setPackageTypes(r.package_types);
      // Phase 2D-update — Merge built-in CATEGORIES with the user's
      // custom list so the Fixed editor sees the same chip set as the
      // Flexible picker in New Shipment.
      let userCustomCats: string[] = [];
      try {
        const cats = await Api.listMyCategories();
        userCustomCats = cats.custom || [];
      } catch { /* fallthrough — presets still work */ }
      const merged = Array.from(new Set([...(r.categories || []), ...userCustomCats]));
      setCategories(merged);
      // Phase 2D — Build the "copy from another courier" candidate
      // list once we know how many variants this target already has.
      try {
        const all = await Api.listAllVariants();
        const couriers = await Api.listCouriers();
        const counts: Record<string, number> = {};
        for (const v of (all.variants || [])) {
          if (v.courier_id === courierId) continue; // skip self
          counts[v.courier_id] = (counts[v.courier_id] || 0) + 1;
        }
        setCopySources(
          (couriers || [])
            .filter((c: any) => counts[c.id])
            .map((c: any) => ({
              courier_id: c.id,
              courier_name: c.name,
              variant_count: counts[c.id] || 0,
            })),
        );
      } catch {
        setCopySources([]);
      }
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to load variants");
    } finally {
      setLoading(false);
    }
  }, [courierId]);

  useEffect(() => { load(); }, [load]);

  const reset = () => {
    setName(""); setPkgType(""); setCategory("");
    setLengthCm(""); setWidthCm(""); setHeightCm("");
    setWeightG(""); setWithinRate(""); setOutsideRate("");
    setEditing(null);
  };

  const openAdd = () => {
    if (cap !== null && variants.length >= cap) {
      Alert.alert(
        "Plan limit reached",
        `Your plan allows ${cap} packing variant(s) per courier. Upgrade to add more.`,
        [{ text: "OK" }, { text: "View Plans", onPress: () => router.push("/plans" as any) }],
      );
      return;
    }
    reset();
    setEditorOpen(true);
  };

  const openEdit = (v: Variant) => {
    setEditing(v);
    setName(v.variant_name);
    setPkgType(v.package_type);
    setCategory(v.category);
    setLengthCm(v.length_cm ? String(v.length_cm) : "");
    setWidthCm(v.width_cm ? String(v.width_cm) : "");
    setHeightCm(v.height_cm ? String(v.height_cm) : "");
    setWeightG(v.weight_g ? String(v.weight_g) : "");
    setWithinRate(v.within_state_rate ? String(v.within_state_rate) : "");
    setOutsideRate(v.outside_state_rate ? String(v.outside_state_rate) : "");
    setEditorOpen(true);
  };

  // Phase 2D — Copy & Edit: clone the row's values into the editor in
  // create mode (no id). Auto-suffix the name with "(Copy)" so the user
  // doesn't accidentally save a same-named duplicate; backend will
  // reject same-name copies but we want clearer affordance up-front.
  const openCopy = (v: Variant) => {
    if (cap !== null && variants.length >= cap) {
      Alert.alert(
        "Plan limit reached",
        `Your plan allows ${cap} variant(s) per courier. Upgrade to copy more.`,
      );
      return;
    }
    setEditing(null);                                  // create mode
    setName(`${v.variant_name} (Copy)`);
    setPkgType(v.package_type);
    setCategory(v.category);
    setLengthCm(v.length_cm ? String(v.length_cm) : "");
    setWidthCm(v.width_cm ? String(v.width_cm) : "");
    setHeightCm(v.height_cm ? String(v.height_cm) : "");
    setWeightG(v.weight_g ? String(v.weight_g) : "");
    setWithinRate(v.within_state_rate ? String(v.within_state_rate) : "");
    setOutsideRate(v.outside_state_rate ? String(v.outside_state_rate) : "");
    setEditorOpen(true);
  };

  // Phase 2D — Bulk-clone all variants from another of the user's
  // couriers in one shot. Plan cap + name dedup handled server-side.
  const copyFromCourier = async (sourceCourierId: string, sourceCourierName: string) => {
    setCopying(true);
    try {
      const r = await Api.copyVariantsFromCourier(courierId, sourceCourierId);
      const lines: string[] = [];
      lines.push(`Copied: ${r.copied_count} variant(s) from ${r.source_courier_name}`);
      if (r.skipped_duplicates.length) {
        lines.push(`Skipped duplicates: ${r.skipped_duplicates.join(", ")}`);
      }
      if (r.skipped_cap_full.length) {
        lines.push(`Skipped (plan cap): ${r.skipped_cap_full.join(", ")}`);
      }
      Alert.alert("Copy complete", lines.join("\n"));
      setCopyPickerOpen(false);
      load();
    } catch (e: any) {
      Alert.alert("Copy failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setCopying(false);
    }
  };

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Variant Name required", "Please give this variant a name (e.g. \"ODC 320gm\").");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        variant_name: name.trim(),
        package_type: pkgType.trim(),
        category: category.trim(),
        length_cm: parseFloat(lengthCm) || 0,
        width_cm: parseFloat(widthCm) || 0,
        height_cm: parseFloat(heightCm) || 0,
        weight_g: parseFloat(weightG) || 0,
        within_state_rate: parseFloat(withinRate) || 0,
        outside_state_rate: parseFloat(outsideRate) || 0,
      };
      if (editing) {
        await Api.updateCourierVariant(courierId, editing.id, payload);
      } else {
        await Api.createCourierVariant(courierId, payload);
      }
      setEditorOpen(false);
      reset();
      load();
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setSaving(false);
    }
  };

  const remove = (v: Variant) => {
    Alert.alert(
      "Delete variant?",
      `Remove "${v.variant_name}" from this courier?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.deleteCourierVariant(courierId, v.id);
              load();
            } catch (e: any) {
              Alert.alert("Delete failed", e?.response?.data?.detail || e?.message);
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <ActivityIndicator color={colors.primary} style={{ marginTop: 60 }} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>📦 Packing Variants</Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 100 }}>
        {/* Cap indicator */}
        <View style={styles.capCard}>
          <PhIcon name="information-circle-outline" size={18} color="#1F4FBF" />
          <Text style={styles.capTxt}>
            {cap === null
              ? `${variants.length} variants (Admin — unlimited)`
              : `${variants.length} of ${cap} variants used on this courier`}
          </Text>
        </View>

        {/* List */}
        {variants.length === 0 ? (
          <View style={styles.emptyCard}>
            <PhIcon name="cube-outline" size={36} color="#9CA3AF" />
            <Text style={styles.emptyTitle}>No variants yet</Text>
            <Text style={styles.emptySub}>
              Add your first packing variant — set its dimensions, weight,
              and within-state vs outside-state rates so the New Shipment
              form auto-fills correctly.
            </Text>
            {copySources.length > 0 && (
              <TouchableOpacity
                testID="copy-from-courier-cta"
                style={styles.copyFromCta}
                onPress={() => setCopyPickerOpen(true)}
              >
                <PhIcon name="copy" size={16} color="#fff" />
                <Text style={styles.copyFromCtaTxt}>
                  Copy variants from another courier
                </Text>
              </TouchableOpacity>
            )}
          </View>
        ) : (
          variants.map((v) => (
            <View key={v.id} style={styles.variantCard}>
              <View style={styles.variantHeader}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.variantName}>{v.variant_name}</Text>
                  <Text style={styles.variantMeta}>
                    {[v.package_type, v.category].filter(Boolean).join(" · ") || "—"}
                  </Text>
                </View>
                <View style={{ flexDirection: "row", gap: 6 }}>
                  <TouchableOpacity
                    testID={`variant-copy-${v.variant_name}`}
                    onPress={() => openCopy(v)}
                    style={styles.iconBtn}
                  >
                    <PhIcon name="copy-outline" size={18} color="#7C3AED" />
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => openEdit(v)} style={styles.iconBtn}>
                    <PhIcon name="create-outline" size={18} color="#1F4FBF" />
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => remove(v)} style={styles.iconBtn}>
                    <PhIcon name="trash-outline" size={18} color="#DC2626" />
                  </TouchableOpacity>
                </View>
              </View>
              <View style={styles.variantStats}>
                <Stat label="Dims (cm)" value={
                  v.length_cm || v.width_cm || v.height_cm
                    ? `${v.length_cm}×${v.width_cm}×${v.height_cm}`
                    : "—"
                } />
                <Stat label="Weight" value={v.weight_g ? `${v.weight_g}g` : "—"} />
                <Stat label="Within-State" value={v.within_state_rate ? `₹${v.within_state_rate}` : "—"} tint="#10B981" />
                <Stat label="Outside" value={v.outside_state_rate ? `₹${v.outside_state_rate}` : "—"} tint="#9333EA" />
              </View>
            </View>
          ))
        )}

        <TouchableOpacity
          style={[
            styles.addBtn,
            cap !== null && variants.length >= cap && { opacity: 0.5 },
          ]}
          onPress={openAdd}
        >
          <PhIcon name="add-circle" size={20} color="#fff" />
          <Text style={styles.addBtnTxt}>Add Variant</Text>
        </TouchableOpacity>
      </ScrollView>

      {/* Phase 2D — Copy-from-courier picker. Tappable list of every
          other courier the user owns that has at least one active
          variant. One-tap clones the whole list (cap-aware). */}
      <Modal
        visible={copyPickerOpen}
        transparent
        animationType="slide"
        onRequestClose={() => !copying && setCopyPickerOpen(false)}
      >
        <View style={styles.modalBg}>
          <View style={styles.sheet}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>📋 Copy variants from…</Text>
              <TouchableOpacity onPress={() => setCopyPickerOpen(false)} disabled={copying} hitSlop={10}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            {copying ? (
              <View style={{ padding: 30, alignItems: "center" }}>
                <ActivityIndicator color="#7C3AED" />
                <Text style={{ marginTop: 10, color: "#6B7280" }}>Copying…</Text>
              </View>
            ) : copySources.length === 0 ? (
              <Text style={[styles.emptySub, { padding: 20 }]}>
                No other couriers have variants yet.
              </Text>
            ) : (
              <ScrollView style={{ maxHeight: 360 }}>
                {copySources.map((s) => (
                  <TouchableOpacity
                    key={s.courier_id}
                    style={styles.sourceRow}
                    onPress={() => copyFromCourier(s.courier_id, s.courier_name)}
                  >
                    <View style={{ flex: 1 }}>
                      <Text style={styles.sourceName}>{s.courier_name}</Text>
                      <Text style={styles.sourceCount}>
                        {s.variant_count} variant(s) available
                      </Text>
                    </View>
                    <PhIcon name="chevron-forward" size={18} color="#9CA3AF" />
                  </TouchableOpacity>
                ))}
              </ScrollView>
            )}
          </View>
        </View>
      </Modal>

      {/* Editor modal */}
      <Modal
        visible={editorOpen}
        transparent
        animationType="slide"
        onRequestClose={() => !saving && setEditorOpen(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.modalBg}
        >
          <View style={styles.sheet}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>
                {editing ? "Edit Variant" : "Add Variant"}
              </Text>
              <TouchableOpacity onPress={() => setEditorOpen(false)} disabled={saving} hitSlop={10}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <ScrollView contentContainerStyle={{ paddingBottom: 30 }} keyboardShouldPersistTaps="handled">
              <Field label="Variant Name *">
                <TextInput
                  value={name} onChangeText={setName}
                  placeholder='e.g. "ODC 320gm"' placeholderTextColor="#9CA3AF"
                  style={styles.input}
                />
              </Field>

              <Field label="Package Type">
                <ChipRow
                  options={packageTypes}
                  selected={pkgType}
                  onPick={setPkgType}
                />
              </Field>

              <Field label="Category">
                <ChipRow
                  options={categories}
                  selected={category}
                  onPick={setCategory}
                />
              </Field>

              <Text style={styles.fieldLabel}>Dimensions (cm)</Text>
              <View style={{ flexDirection: "row", gap: 8 }}>
                <View style={{ flex: 1 }}>
                  <Field label="Length">
                    <TextInput value={lengthCm} onChangeText={setLengthCm}
                      keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                      style={styles.input} />
                  </Field>
                </View>
                <View style={{ flex: 1 }}>
                  <Field label="Width">
                    <TextInput value={widthCm} onChangeText={setWidthCm}
                      keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                      style={styles.input} />
                  </Field>
                </View>
                <View style={{ flex: 1 }}>
                  <Field label="Height">
                    <TextInput value={heightCm} onChangeText={setHeightCm}
                      keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                      style={styles.input} />
                  </Field>
                </View>
              </View>

              <Field label="Weight (grams)">
                <TextInput value={weightG} onChangeText={setWeightG}
                  keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                  style={styles.input} />
              </Field>

              <Text style={styles.fieldLabel}>Rates (₹)</Text>
              <View style={{ flexDirection: "row", gap: 8 }}>
                <View style={{ flex: 1 }}>
                  <Field label="Within-State Rate">
                    <TextInput value={withinRate} onChangeText={setWithinRate}
                      keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                      style={styles.input} />
                  </Field>
                </View>
                <View style={{ flex: 1 }}>
                  <Field label="Outside-State Rate">
                    <TextInput value={outsideRate} onChangeText={setOutsideRate}
                      keyboardType="decimal-pad" placeholder="0" placeholderTextColor="#9CA3AF"
                      style={styles.input} />
                  </Field>
                </View>
              </View>

              <TouchableOpacity
                style={styles.saveBtn} onPress={save} disabled={saving}
              >
                {saving ? <ActivityIndicator color="#fff" /> : (
                  <>
                    <PhIcon name="save" size={18} color="#fff" />
                    <Text style={styles.saveBtnTxt}>
                      {editing ? "Save Changes" : "Add Variant"}
                    </Text>
                  </>
                )}
              </TouchableOpacity>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function ChipRow({ options, selected, onPick }: {
  options: string[]; selected: string; onPick: (v: string) => void;
}) {
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false}>
      <View style={{ flexDirection: "row", gap: 6 }}>
        {options.map((o) => {
          const active = selected === o;
          return (
            <TouchableOpacity
              key={o}
              onPress={() => onPick(active ? "" : o)}
              style={[styles.chip, active && styles.chipActive]}
            >
              <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>{o}</Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </ScrollView>
  );
}

function Stat({ label, value, tint }: { label: string; value: string; tint?: string }) {
  return (
    <View style={styles.statBox}>
      <Text style={styles.statLabel}>{label}</Text>
      <Text style={[styles.statVal, tint ? { color: tint } : null]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F7F7F9" },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 12, paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  backBtn: { width: 40, height: 40, alignItems: "center", justifyContent: "center" },
  title: { fontSize: 16, fontWeight: "800", color: colors.text, flex: 1, textAlign: "center" },

  capCard: {
    flexDirection: "row", alignItems: "center", gap: 8,
    padding: 10, borderRadius: 10,
    backgroundColor: "#EEF2FF", borderWidth: 1, borderColor: "#C7D2FE",
    marginBottom: 12,
  },
  capTxt: { fontSize: 12, fontWeight: "700", color: "#1F4FBF" },

  emptyCard: {
    backgroundColor: "#fff", padding: 20, borderRadius: 12,
    alignItems: "center",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  emptyTitle: { fontSize: 14, fontWeight: "800", color: "#374151", marginTop: 10 },
  emptySub: {
    fontSize: 12, color: "#6B7280", textAlign: "center", marginTop: 6, lineHeight: 17,
  },
  copyFromCta: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 12, paddingHorizontal: 16, borderRadius: 10,
    backgroundColor: "#7C3AED", marginTop: 16,
  },
  copyFromCtaTxt: { fontSize: 13, fontWeight: "800", color: "#fff" },
  sourceRow: {
    flexDirection: "row", alignItems: "center",
    paddingVertical: 14, paddingHorizontal: 4,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  sourceName:  { fontSize: 14, fontWeight: "800", color: "#111827" },
  sourceCount: { fontSize: 11.5, color: "#7C3AED", marginTop: 2 },

  variantCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 12,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 10,
  },
  variantHeader: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#F3F4F6",
  },
  variantName: { fontSize: 14.5, fontWeight: "800", color: "#111827" },
  variantMeta: { fontSize: 11, color: "#6B7280", marginTop: 2 },
  iconBtn: {
    width: 32, height: 32, borderRadius: 8,
    backgroundColor: "#F3F4F6",
    alignItems: "center", justifyContent: "center",
  },
  variantStats: {
    flexDirection: "row", gap: 8, marginTop: 10,
  },
  statBox: { flex: 1, alignItems: "flex-start" },
  statLabel: { fontSize: 10, color: "#9CA3AF", marginBottom: 2 },
  statVal: { fontSize: 12.5, fontWeight: "800", color: "#111827" },

  addBtn: {
    marginTop: 16,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 14, borderRadius: 12,
    backgroundColor: "#7C3AED",
  },
  addBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },

  // Modal / editor
  modalBg: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 16, borderTopRightRadius: 16,
    padding: 16, maxHeight: "92%",
  },
  sheetHeader: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingBottom: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
    marginBottom: 12,
  },
  sheetTitle: { fontSize: 16, fontWeight: "800", color: colors.text },

  fieldLabel: { fontSize: 12, fontWeight: "700", color: "#374151", marginBottom: 6, marginTop: 4 },
  input: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10,
    fontSize: 14, color: colors.text,
    backgroundColor: "#fff",
  },

  chip: {
    paddingHorizontal: 12, paddingVertical: 7, borderRadius: 20,
    flexShrink: 0,
    backgroundColor: "#F3F4F6", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: "#7C3AED", borderColor: "#7C3AED" },
  chipTxt: { fontSize: 12, fontWeight: "700", color: "#374151" },
  chipTxtActive: { color: "#fff" },

  saveBtn: {
    marginTop: 14,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 13, borderRadius: 10,
    backgroundColor: "#1F4FBF",
  },
  saveBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
