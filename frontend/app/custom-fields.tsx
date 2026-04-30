/**
 * Custom Fields manager — plan-gated per-user custom columns.
 *
 * Shows:
 *   • Current usage (3 / 5 used) + plan label
 *   • List of existing custom fields (tap to edit / delete)
 *   • "+ Add Field" button (disabled with upgrade CTA when cap hit
 *     or feature not enabled on this plan)
 *   • Add/Edit modal
 *
 * Plan caps (defaults, admin can override via /admin/custom-field-limits):
 *   Free Trial / Silver: 0, Gold: 3, Platinum: 5, Admin: unlimited.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
  Modal,
  Switch,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { router, Stack } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api, CustomField } from "../lib/api";
import { colors } from "../lib/theme";

type LimitsResp = {
  fields: CustomField[];
  limit: number;
  used: number;
  feature_enabled: boolean;
  plan: string;
  is_admin: boolean;
};

export default function CustomFieldsScreen() {
  const [state, setState] = useState<LimitsResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<CustomField | null>(null);

  // Form state for add/edit modal.
  const [name, setName] = useState("");
  const [col, setCol] = useState("");
  const [type, setType] = useState<"text" | "number" | "date">("text");
  const [showForm, setShowForm] = useState(true);
  const [showSmart, setShowSmart] = useState(true);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await Api.listMyCustomFields();
      setState(r);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openAdd = () => {
    setEditing(null);
    setName("");
    setCol("");
    setType("text");
    setShowForm(true);
    setShowSmart(true);
    setModalOpen(true);
  };

  const openEdit = (f: CustomField) => {
    setEditing(f);
    setName(f.name);
    setCol(f.column_letter);
    setType(f.field_type);
    setShowForm(f.show_in_form);
    setShowSmart(f.show_in_smart_paste);
    setModalOpen(true);
  };

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Validation", "Field name is required");
      return;
    }
    const colTrim = col.trim().toUpperCase();
    if (!/^[A-Z]{1,3}$/.test(colTrim)) {
      Alert.alert("Validation", "Column letter must be A–Z (e.g. F or AA).");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: name.trim(),
        column_letter: colTrim,
        field_type: type,
        show_in_form: showForm,
        show_in_smart_paste: showSmart,
      };
      if (editing) {
        await Api.updateMyCustomField(editing.id, payload);
      } else {
        await Api.createMyCustomField(payload);
      }
      setModalOpen(false);
      await load();
    } catch (e: any) {
      Alert.alert(
        "Error",
        e?.response?.data?.detail || e?.message || "Failed",
      );
    } finally {
      setSaving(false);
    }
  };

  const remove = (f: CustomField) => {
    Alert.alert(
      "Delete field",
      `Remove "${f.name}"? Existing shipment data in column ${f.column_letter} won't be deleted from your sheet.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            try {
              await Api.deleteMyCustomField(f.id);
              await load();
            } catch (e: any) {
              Alert.alert("Error", e?.response?.data?.detail || "Failed");
            }
          },
        },
      ],
    );
  };

  const canAdd = state
    ? state.feature_enabled && state.used < state.limit
    : false;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}>
      <Stack.Screen
        options={{ title: "Custom Fields", headerBackTitle: "Back" }}
      />
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        {/* Plan/usage banner */}
        <View
          style={[
            styles.banner,
            !state?.feature_enabled && styles.bannerOff,
          ]}
        >
          <Ionicons
            name={
              state?.is_admin
                ? "infinite"
                : state?.feature_enabled
                ? "layers"
                : "lock-closed"
            }
            size={18}
            color={state?.feature_enabled ? colors.primary : "#B45309"}
          />
          <View style={{ flex: 1 }}>
            <Text style={styles.bannerTitle}>
              {state?.is_admin
                ? "Admin · Unlimited custom fields"
                : state?.feature_enabled
                ? `${(state?.plan || "").toUpperCase()} · ${
                    state?.used || 0
                  } / ${state?.limit || 0} fields used`
                : "Custom fields not available on your plan"}
            </Text>
            {!state?.feature_enabled && !state?.is_admin && (
              <Text style={styles.bannerSub}>
                Upgrade to Gold (3 fields) or Platinum (5 fields) to add custom
                columns like "Salesperson", "Reference No.", etc.
              </Text>
            )}
          </View>
        </View>

        {/* List */}
        {loading ? (
          <Text style={styles.muted}>Loading…</Text>
        ) : (state?.fields || []).length === 0 ? (
          <View style={styles.empty}>
            <Ionicons name="document-outline" size={36} color={colors.textMuted} />
            <Text style={styles.emptyTitle}>No custom fields yet</Text>
            <Text style={styles.emptySub}>
              Add fields like "Salesperson", "Reference No", "Delivery Date"
              that appear in New Shipment form and auto-write to your Google
              Sheet.
            </Text>
          </View>
        ) : (
          (state?.fields || []).map((f) => (
            <TouchableOpacity
              key={f.id}
              style={styles.card}
              onPress={() => openEdit(f)}
            >
              <View style={{ flex: 1 }}>
                <Text style={styles.cardName}>{f.name}</Text>
                <Text style={styles.cardSub}>
                  Col {f.column_letter} · {f.field_type} ·{" "}
                  {f.show_in_form ? "Form✓" : "Form✗"} ·{" "}
                  {f.show_in_smart_paste ? "Paste✓" : "Paste✗"}
                </Text>
              </View>
              <TouchableOpacity
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                onPress={() => remove(f)}
              >
                <Ionicons
                  name="trash-outline"
                  size={20}
                  color={colors.dangerText || "#EF4444"}
                />
              </TouchableOpacity>
            </TouchableOpacity>
          ))
        )}

        {/* Add button */}
        {state?.feature_enabled && canAdd ? (
          <TouchableOpacity style={styles.addBtn} onPress={openAdd}>
            <Ionicons name="add" size={20} color="#fff" />
            <Text style={styles.addBtnText}>Add Custom Field</Text>
          </TouchableOpacity>
        ) : state?.feature_enabled && !canAdd ? (
          <TouchableOpacity
            style={[styles.addBtn, { backgroundColor: "#F59E0B" }]}
            onPress={() => router.push("/plans")}
          >
            <Ionicons name="rocket" size={18} color="#fff" />
            <Text style={styles.addBtnText}>
              Cap reached — upgrade to add more
            </Text>
          </TouchableOpacity>
        ) : !state?.is_admin ? (
          <TouchableOpacity
            style={[styles.addBtn, { backgroundColor: "#F59E0B" }]}
            onPress={() => router.push("/plans")}
          >
            <Ionicons name="rocket" size={18} color="#fff" />
            <Text style={styles.addBtnText}>Upgrade to use Custom Fields</Text>
          </TouchableOpacity>
        ) : null}
      </ScrollView>

      {/* Add / Edit modal */}
      <Modal visible={modalOpen} transparent animationType="slide">
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : "height"}
          style={styles.modalOverlay}
        >
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>
              {editing ? "Edit Field" : "Add Custom Field"}
            </Text>

            <Text style={styles.fieldLbl}>Field Name *</Text>
            <TextInput
              style={styles.input}
              value={name}
              onChangeText={setName}
              placeholder="e.g. Salesperson, Reference No"
              placeholderTextColor={colors.textMuted}
            />

            <Text style={styles.fieldLbl}>Sheet Column Letter *</Text>
            <TextInput
              style={[styles.input, { width: 100 }]}
              value={col}
              onChangeText={(t) => setCol(t.toUpperCase())}
              placeholder="e.g. F"
              autoCapitalize="characters"
              maxLength={3}
              placeholderTextColor={colors.textMuted}
            />
            <Text style={styles.hint}>
              Which column of your Google Sheet should store this field's
              value. Use a free column (A..Z, AA..).
            </Text>

            <Text style={styles.fieldLbl}>Type</Text>
            <View style={{ flexDirection: "row", gap: 8 }}>
              {(["text", "number", "date"] as const).map((t) => (
                <TouchableOpacity
                  key={t}
                  style={[
                    styles.chip,
                    type === t && { backgroundColor: colors.primary },
                  ]}
                  onPress={() => setType(t)}
                >
                  <Text
                    style={[
                      styles.chipText,
                      type === t && { color: "#fff" },
                    ]}
                  >
                    {t}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            <View style={styles.row}>
              <Text style={styles.fieldLbl}>Show in New Shipment form</Text>
              <Switch value={showForm} onValueChange={setShowForm} />
            </View>
            <View style={styles.row}>
              <Text style={styles.fieldLbl}>Show in Smart Paste summary</Text>
              <Switch value={showSmart} onValueChange={setShowSmart} />
            </View>

            <View style={{ flexDirection: "row", gap: 8, marginTop: 16 }}>
              <TouchableOpacity
                style={[styles.addBtn, { flex: 1, backgroundColor: "#9CA3AF" }]}
                onPress={() => setModalOpen(false)}
              >
                <Text style={styles.addBtnText}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.addBtn, { flex: 1 }]}
                onPress={save}
                disabled={saving}
              >
                <Text style={styles.addBtnText}>
                  {saving ? "Saving…" : editing ? "Save" : "Add"}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    gap: 10,
    backgroundColor: "#EFF6FF",
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: "#DBEAFE",
    marginBottom: 14,
  },
  bannerOff: {
    backgroundColor: "#FEF3C7",
    borderColor: "#FDE68A",
  },
  bannerTitle: { fontWeight: "800", color: colors.text, fontSize: 13 },
  bannerSub: { fontSize: 12, color: colors.textMuted, marginTop: 4, lineHeight: 16 },

  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: 12,
    borderRadius: 10,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    marginBottom: 8,
  },
  cardName: { fontWeight: "800", color: colors.text, fontSize: 14 },
  cardSub: { fontSize: 11, color: colors.textMuted, marginTop: 2 },

  empty: { alignItems: "center", paddingVertical: 32, gap: 6 },
  emptyTitle: { fontWeight: "800", color: colors.text, marginTop: 8 },
  emptySub: {
    fontSize: 12,
    color: colors.textMuted,
    textAlign: "center",
    maxWidth: 300,
    lineHeight: 17,
  },

  addBtn: {
    marginTop: 16,
    backgroundColor: colors.primary,
    borderRadius: 12,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  addBtnText: { color: "#fff", fontWeight: "800" },

  muted: { color: colors.textMuted, fontSize: 13 },

  modalOverlay: {
    flex: 1,
    // Stronger dim (0.55 vs 0.4) so the scroll content behind doesn't
    // visually bleed through — prevents the "Add Custom Field" CTA on
    // the empty-state from ghosting into the modal card.
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "flex-end",
  },
  modalCard: {
    backgroundColor: colors.background,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    padding: 16,
    maxHeight: "90%",
    // Ensure the card is opaque + visually separated from the dimmed
    // overlay on both iOS and Android.
    ...Platform.select({
      ios: {
        shadowColor: "#000",
        shadowOffset: { width: 0, height: -4 },
        shadowOpacity: 0.15,
        shadowRadius: 12,
      },
      android: {
        elevation: 12,
      },
      default: {},
    }),
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 12,
  },
  fieldLbl: {
    fontSize: 13,
    color: colors.text,
    fontWeight: "700",
    marginTop: 12,
    marginBottom: 4,
  },
  input: {
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    padding: 10,
    color: colors.text,
    fontSize: 14,
    backgroundColor: colors.surface,
  },
  hint: { fontSize: 11, color: colors.textMuted, marginTop: 4, lineHeight: 15 },
  chip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  chipText: { color: colors.text, fontWeight: "600", textTransform: "capitalize" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 8,
  },
});
