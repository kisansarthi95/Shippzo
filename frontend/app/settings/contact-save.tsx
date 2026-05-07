/**
 * Contact Save Settings — Phase-16.
 *
 * Per-user preferences that drive how "Save Contact" builds the final
 * native-contact payload. Three sections (Name Format, Field Mapping,
 * Category) + a LIVE PREVIEW card that updates on every keystroke so
 * the user can see the exact name / notes they'll see in the contacts
 * app before saving a single shipment.
 *
 * Nothing is hardcoded — categories, product → category mapping, and
 * all placement knobs are user-owned.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";

type Settings = {
  name_format: {
    prefix_enabled: boolean;
    prefix_position: "start" | "end";
    name_type: "full" | "first";
    product_placement: "after_name" | "end" | "notes_only";
    location: "city" | "taluka" | "village" | "none";
  };
  field_mapping: {
    address_target: "address" | "notes";
    product_target: "name" | "notes" | "both";
    notes_include: {
      order_id: boolean;
      quantity: boolean;
      payment_mode: boolean;
    };
  };
  category: {
    categories: string[];
    default_category: string;
    auto_assign: boolean;
    manual_popup: boolean;
    product_mapping: Array<{ keyword: string; category: string }>;
  };
};

const SAMPLE_SHIPMENT = {
  customer_name: "Ramesh Patel",
  customer_phone: "9876543210",
  items: "Garlic",
  address_line1: "Shop 12, Main Bazaar",
  address_line2: "Near Post Office",
  city: "Surat",
  taluka: "Choryasi",
  village: "Adajan",
  state: "Gujarat",
  pincode: "395003",
  order_id: "ORD-1024",
  quantity: "2kg",
  payment_mode: "COD",
};

export default function ContactSaveSettingsScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [s, setS] = useState<Settings | null>(null);
  const [preview, setPreview] = useState<{
    name: string; phone: string; postal: string; notes: string; category: string;
  } | null>(null);
  const [newCategory, setNewCategory] = useState("");

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const data = await Api.getContactSettings();
      setS(data as Settings);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Live preview — debounced 200 ms so rapid toggles don't spam the API.
  useEffect(() => {
    if (!s) return;
    const h = setTimeout(async () => {
      try {
        // Save-then-preview: persist current state, then ask backend to
        // build. Simpler than duplicating the builder client-side and
        // guarantees the preview matches what Save will actually write.
        await Api.putContactSettings(s);
        const p = await Api.buildOneContact({ shipment: SAMPLE_SHIPMENT });
        setPreview(p);
      } catch (e) {
        // Preview failure is non-fatal; keep stale preview visible.
      }
    }, 200);
    return () => clearTimeout(h);
  }, [s]);

  const save = async () => {
    if (!s) return;
    try {
      setSaving(true);
      await Api.putContactSettings(s);
      Alert.alert("Saved", "Your Save Contact preferences are updated.");
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Try again.");
    } finally {
      setSaving(false);
    }
  };

  if (loading || !s) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <Header onBack={() => router.back()} title="Save Contact Settings" />
        <View style={styles.loadingBox}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      </SafeAreaView>
    );
  }

  const patch = <K extends keyof Settings>(k: K, v: Settings[K]) =>
    setS((prev) => (prev ? { ...prev, [k]: v } : prev));

  const nf = s.name_format;
  const fm = s.field_mapping;
  const cat = s.category;

  const addCategory = () => {
    const v = (newCategory || "").trim().toUpperCase().slice(0, 8);
    if (!v) return;
    if (cat.categories.includes(v)) {
      Alert.alert("Duplicate", `"${v}" is already in your list.`);
      return;
    }
    patch("category", {
      ...cat,
      categories: [...cat.categories, v],
      default_category: cat.default_category || v,
    });
    setNewCategory("");
  };

  const removeCategory = (c: string) => {
    const nextCats = cat.categories.filter((x) => x !== c);
    // Drop product rules pointing at this category so we don't leak
    // a dead label through the mapping table.
    const nextMap = cat.product_mapping.filter((r) => r.category !== c);
    const nextDef = cat.default_category === c ? (nextCats[0] || "") : cat.default_category;
    patch("category", {
      ...cat,
      categories: nextCats,
      product_mapping: nextMap,
      default_category: nextDef,
    });
  };

  const addMapRow = () => {
    if (cat.categories.length === 0) {
      Alert.alert("No categories", "Add at least one category first.");
      return;
    }
    patch("category", {
      ...cat,
      product_mapping: [...cat.product_mapping, { keyword: "", category: cat.categories[0] }],
    });
  };

  const updateMapRow = (idx: number, patchRow: { keyword?: string; category?: string }) => {
    const next = cat.product_mapping.map((r, i) => (i === idx ? { ...r, ...patchRow } : r));
    patch("category", { ...cat, product_mapping: next });
  };

  const removeMapRow = (idx: number) =>
    patch("category", {
      ...cat,
      product_mapping: cat.product_mapping.filter((_, i) => i !== idx),
    });

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Header onBack={() => router.back()} title="Save Contact" />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 120 }}>
          {/* Live preview */}
          <View style={styles.previewCard}>
            <View style={styles.previewHead}>
              <PhIcon name="eye-outline" size={14} color="#7C3AED" />
              <Text style={styles.previewHeadTxt}>Live Preview</Text>
            </View>
            <Text style={styles.previewName}>{preview?.name || "—"}</Text>
            {!!preview?.phone && <Text style={styles.previewLine}>Phone: {preview.phone}</Text>}
            {!!preview?.postal && <Text style={styles.previewLine}>Postal: {preview.postal}</Text>}
            {!!preview?.notes && <Text style={styles.previewLine}>Notes: {preview.notes}</Text>}
          </View>

          {/* Section A: Name Format */}
          <SectionTitle>Section A · Name Format</SectionTitle>

          <RowSwitch
            label="Show category prefix"
            value={nf.prefix_enabled}
            onChange={(v) => patch("name_format", { ...nf, prefix_enabled: v })}
          />
          <RowChips
            label="Prefix position"
            options={[{ v: "start", t: "Start" }, { v: "end", t: "End" }]}
            value={nf.prefix_position}
            onChange={(v) => patch("name_format", { ...nf, prefix_position: v as any })}
            disabled={!nf.prefix_enabled}
          />
          <RowChips
            label="Name type"
            options={[{ v: "full", t: "Full Name" }, { v: "first", t: "First Name" }]}
            value={nf.name_type}
            onChange={(v) => patch("name_format", { ...nf, name_type: v as any })}
          />
          <RowChips
            label="Product placement"
            options={[
              { v: "after_name", t: "After Name" },
              { v: "end",        t: "End" },
              { v: "notes_only", t: "Notes Only" },
            ]}
            value={nf.product_placement}
            onChange={(v) => patch("name_format", { ...nf, product_placement: v as any })}
          />
          <RowChips
            label="Location in name"
            options={[
              { v: "city",    t: "City" },
              { v: "taluka",  t: "Taluka" },
              { v: "village", t: "Village" },
              { v: "none",    t: "None" },
            ]}
            value={nf.location}
            onChange={(v) => patch("name_format", { ...nf, location: v as any })}
          />

          {/* Section B: Field Mapping */}
          <SectionTitle>Section B · Field Mapping</SectionTitle>
          <RowChips
            label="Address goes to"
            options={[
              { v: "address", t: "Address Field" },
              { v: "notes",   t: "Notes" },
            ]}
            value={fm.address_target}
            onChange={(v) => patch("field_mapping", { ...fm, address_target: v as any })}
          />
          <RowChips
            label="Product goes to"
            options={[
              { v: "name",  t: "Name" },
              { v: "notes", t: "Notes" },
              { v: "both",  t: "Both" },
            ]}
            value={fm.product_target}
            onChange={(v) => patch("field_mapping", { ...fm, product_target: v as any })}
          />
          <Text style={styles.subLabel}>Extra fields to include in Notes</Text>
          <RowSwitch
            label="Order ID"
            value={fm.notes_include.order_id}
            onChange={(v) => patch("field_mapping", {
              ...fm, notes_include: { ...fm.notes_include, order_id: v },
            })}
          />
          <RowSwitch
            label="Quantity"
            value={fm.notes_include.quantity}
            onChange={(v) => patch("field_mapping", {
              ...fm, notes_include: { ...fm.notes_include, quantity: v },
            })}
          />
          <RowSwitch
            label="Payment Mode"
            value={fm.notes_include.payment_mode}
            onChange={(v) => patch("field_mapping", {
              ...fm, notes_include: { ...fm.notes_include, payment_mode: v },
            })}
          />

          {/* Section C: Category */}
          <SectionTitle>Section C · Category</SectionTitle>
          <Text style={styles.subLabel}>Your categories</Text>
          <View style={styles.chipsRow}>
            {cat.categories.length === 0 && (
              <Text style={styles.emptyHint}>No categories yet — add one below.</Text>
            )}
            {cat.categories.map((c) => (
              <View key={c} style={styles.catChip}>
                <Text style={styles.catChipTxt}>{c}</Text>
                {cat.default_category === c && (
                  <Text style={styles.catDefTag}>default</Text>
                )}
                <TouchableOpacity onPress={() => removeCategory(c)} hitSlop={6}>
                  <PhIcon name="close" size={14} color="#DC2626" />
                </TouchableOpacity>
              </View>
            ))}
          </View>

          <View style={styles.inlineRow}>
            <TextInput
              style={styles.catInput}
              value={newCategory}
              onChangeText={setNewCategory}
              placeholder="e.g. KSS"
              placeholderTextColor="#9CA3AF"
              autoCapitalize="characters"
              maxLength={8}
            />
            <TouchableOpacity style={styles.addBtn} onPress={addCategory}>
              <PhIcon name="add" size={16} color="#fff" />
              <Text style={styles.addBtnTxt}>Add</Text>
            </TouchableOpacity>
          </View>

          {cat.categories.length > 0 && (
            <>
              <Text style={styles.subLabel}>Default category (used when auto-mapping finds no match)</Text>
              <View style={styles.chipsRow}>
                {cat.categories.map((c) => (
                  <TouchableOpacity
                    key={c}
                    style={[
                      styles.pickChip,
                      cat.default_category === c && styles.pickChipActive,
                    ]}
                    onPress={() => patch("category", { ...cat, default_category: c })}
                  >
                    <Text
                      style={[
                        styles.pickChipTxt,
                        cat.default_category === c && { color: "#fff" },
                      ]}
                    >
                      {c}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
            </>
          )}

          <RowSwitch
            label="Auto-assign category from product"
            value={cat.auto_assign}
            onChange={(v) => patch("category", { ...cat, auto_assign: v })}
          />
          <RowSwitch
            label="Show manual popup per save (single flow)"
            value={cat.manual_popup}
            onChange={(v) => patch("category", { ...cat, manual_popup: v })}
          />

          {/* Product → Category mapping table */}
          <Text style={styles.subLabel}>Product → Category mapping</Text>
          {cat.product_mapping.length === 0 && (
            <Text style={styles.emptyHint}>
              No rules yet. Tap "Add rule" to match a product keyword to a category.
            </Text>
          )}
          {cat.product_mapping.map((r, idx) => (
            <View key={idx} style={styles.mapRow}>
              <TextInput
                style={[styles.input, { flex: 1 }]}
                value={r.keyword}
                onChangeText={(v) => updateMapRow(idx, { keyword: v })}
                placeholder="e.g. garlic"
                placeholderTextColor="#9CA3AF"
              />
              <View style={styles.mapPickRow}>
                {cat.categories.map((c) => (
                  <TouchableOpacity
                    key={c}
                    style={[
                      styles.mapPick,
                      r.category === c && styles.mapPickActive,
                    ]}
                    onPress={() => updateMapRow(idx, { category: c })}
                  >
                    <Text style={[styles.mapPickTxt, r.category === c && { color: "#fff" }]}>
                      {c}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>
              <TouchableOpacity onPress={() => removeMapRow(idx)} hitSlop={8}>
                <PhIcon name="trash-outline" size={18} color="#DC2626" />
              </TouchableOpacity>
            </View>
          ))}
          <TouchableOpacity style={styles.addRowBtn} onPress={addMapRow}>
            <PhIcon name="add-circle-outline" size={18} color="#7C3AED" />
            <Text style={styles.addRowTxt}>Add rule</Text>
          </TouchableOpacity>
        </ScrollView>

        {/* Sticky Save bar */}
        <View style={styles.saveBar}>
          <TouchableOpacity
            onPress={save}
            disabled={saving}
            style={[styles.saveBtn, saving && { opacity: 0.6 }]}
          >
            {saving
              ? <ActivityIndicator color="#fff" size="small" />
              : <><PhIcon name="save-outline" size={18} color="#fff" /><Text style={styles.saveBtnTxt}>Save Preferences</Text></>
            }
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Header({ onBack, title }: { onBack: () => void; title: string }) {
  return (
    <View style={styles.header}>
      <TouchableOpacity onPress={onBack} style={styles.backBtn} hitSlop={10}>
        <PhIcon name="chevron-back" size={24} color={colors.text} />
      </TouchableOpacity>
      <Text style={styles.headerTitle}>{title}</Text>
      <View style={{ width: 36 }} />
    </View>
  );
}

function SectionTitle({ children }: { children: string }) {
  return <Text style={styles.sectionTitle}>{children}</Text>;
}

function RowSwitch({
  label, value, onChange,
}: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ false: "#D1D5DB", true: "#7C3AED" }}
        thumbColor="#fff"
      />
    </View>
  );
}

function RowChips({
  label, options, value, onChange, disabled,
}: {
  label: string;
  options: Array<{ v: string; t: string }>;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <View style={[styles.chipsBlock, disabled && { opacity: 0.5 }]}>
      <Text style={styles.rowLabel}>{label}</Text>
      <View style={styles.chipsRow}>
        {options.map((o) => (
          <TouchableOpacity
            key={o.v}
            disabled={disabled}
            onPress={() => onChange(o.v)}
            style={[styles.pickChip, value === o.v && styles.pickChipActive]}
          >
            <Text style={[styles.pickChipTxt, value === o.v && { color: "#fff" }]}>{o.t}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 12, paddingVertical: 10,
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  backBtn: { padding: 4, width: 36 },
  headerTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  loadingBox: { flex: 1, alignItems: "center", justifyContent: "center" },

  previewCard: {
    padding: 12, backgroundColor: "#F5F3FF",
    borderRadius: 10, borderWidth: 1, borderColor: "#DDD6FE",
    marginBottom: 14,
  },
  previewHead: { flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 },
  previewHeadTxt: { fontSize: 11, fontWeight: "800", color: "#5B21B6", letterSpacing: 0.4 },
  previewName: { fontSize: 16, fontWeight: "800", color: "#1E1B4B", marginBottom: 4 },
  previewLine: { fontSize: 11.5, color: "#4C1D95", marginTop: 2 },

  sectionTitle: {
    fontSize: 11, fontWeight: "900", color: "#6B7280",
    marginTop: 18, marginBottom: 8, letterSpacing: 0.6, textTransform: "uppercase",
  },
  subLabel: { fontSize: 12, color: "#6B7280", marginTop: 10, marginBottom: 6, fontWeight: "700" },
  emptyHint: { fontSize: 11, color: "#9CA3AF", fontStyle: "italic" },

  row: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingVertical: 8,
  },
  rowLabel: { fontSize: 13, fontWeight: "600", color: colors.text, flex: 1, paddingRight: 8 },

  chipsBlock: { paddingVertical: 8 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 6 },
  pickChip: {
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#F3F4F6", borderRadius: 16, borderWidth: 1, borderColor: "#E5E7EB",
  },
  pickChipActive: { backgroundColor: "#7C3AED", borderColor: "#7C3AED" },
  pickChipTxt: { fontSize: 11.5, fontWeight: "700", color: "#374151" },

  catChip: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#EDE9FE", borderRadius: 10,
    borderWidth: 1, borderColor: "#C4B5FD",
  },
  catChipTxt: { fontSize: 12, fontWeight: "800", color: "#5B21B6" },
  catDefTag: { fontSize: 9, fontWeight: "800", color: "#16A34A", textTransform: "uppercase" },

  inlineRow: { flexDirection: "row", gap: 8, marginTop: 8 },
  catInput: {
    flex: 1, borderWidth: 1, borderColor: "#D1D5DB", borderRadius: 8,
    paddingHorizontal: 10, height: 38, backgroundColor: "#fff",
    fontSize: 13, fontWeight: "700", color: colors.text,
  },
  addBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 14,
    backgroundColor: "#7C3AED", borderRadius: 8,
  },
  addBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  mapRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: "#F3F4F6",
  },
  mapPickRow: { flexDirection: "row", gap: 4, flexWrap: "wrap", maxWidth: 140 },
  mapPick: {
    paddingHorizontal: 8, paddingVertical: 4,
    backgroundColor: "#F3F4F6", borderRadius: 12,
  },
  mapPickActive: { backgroundColor: "#7C3AED" },
  mapPickTxt: { fontSize: 10.5, fontWeight: "800", color: "#374151" },

  input: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 10, height: 34, fontSize: 12, fontWeight: "600",
    color: colors.text, backgroundColor: "#fff",
  },
  addRowBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 10,
  },
  addRowTxt: { fontSize: 12, fontWeight: "800", color: "#7C3AED" },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    paddingHorizontal: 14, paddingVertical: 12,
    paddingBottom: Platform.OS === "ios" ? 24 : 12,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 12, backgroundColor: "#7C3AED", borderRadius: 10,
  },
  saveBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
