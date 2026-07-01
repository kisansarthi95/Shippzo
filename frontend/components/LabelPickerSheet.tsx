/**
 * LabelPickerSheet — bottom-sheet Modal for selecting labels on a
 * shipment. Combines two views:
 *
 *   1. Select — lists user's labels (grouped Order / Priority / Custom),
 *      lets user tap to toggle, plus a "+ Create New Label" row.
 *   2. Create — inline dialog with name field + icon grid + color grid.
 *
 * Props:
 *   visible          — open flag
 *   selectedIds      — currently applied label ids on the shipment
 *   onClose          — dismiss without saving
 *   onApply(ids[])   — user tapped a chip; we auto-save immediately
 *                      (matches the "instant toggle" pattern in the ref).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, Modal,
  ScrollView, TextInput, ActivityIndicator, Alert, Pressable,
} from "react-native";
import PhIcon from "./PhIcon";
import LabelChip from "./LabelChip";
import {
  LabelsApi,
  ShipmentLabel,
  LABEL_COLORS,
  LABEL_ICON_KEYS,
  LABEL_ICON_MAP,
} from "../lib/labels";
import { colors } from "../lib/theme";

type Props = {
  visible: boolean;
  selectedIds: string[];
  onClose: () => void;
  onApply: (ids: string[]) => void;
};

export default function LabelPickerSheet({
  visible, selectedIds, onClose, onApply,
}: Props) {
  const [view, setView] = useState<"select" | "create">("select");
  const [labels, setLabels] = useState<ShipmentLabel[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [ids, setIds] = useState<string[]>(selectedIds);

  // Create-form state
  const [name, setName] = useState("");
  const [icon, setIcon] = useState<string>("tag");
  const [color, setColor] = useState<string>(LABEL_COLORS[0]);

  useEffect(() => {
    if (!visible) return;
    setView("select");
    setIds(selectedIds);
    setLoading(true);
    LabelsApi.list()
      .then(setLabels)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [visible, selectedIds]);

  const grouped = useMemo(() => {
    const g: Record<string, ShipmentLabel[]> = { order: [], priority: [], custom: [] };
    labels.forEach((l) => {
      const k = (l.kind === "priority" || l.kind === "order") ? l.kind : "custom";
      g[k].push(l);
    });
    return g;
  }, [labels]);

  const toggle = useCallback((id: string) => {
    setIds((prev) => {
      const next = prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
      // Auto-apply on every toggle — matches the "instant" feel.
      onApply(next);
      return next;
    });
  }, [onApply]);

  const submitCreate = useCallback(async () => {
    const nm = name.trim();
    if (!nm) { Alert.alert("Label name is required"); return; }
    setSaving(true);
    try {
      const created = await LabelsApi.create({ name: nm, icon, color, kind: "custom" });
      setLabels((prev) => [...prev, created]);
      // Preselect the newly-created label + apply.
      setIds((prev) => {
        const next = [...prev, created.id];
        onApply(next);
        return next;
      });
      // Reset form + jump back to select view.
      setName(""); setIcon("tag"); setColor(LABEL_COLORS[0]);
      setView("select");
    } catch (e: any) {
      Alert.alert("Could not create label", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setSaving(false);
    }
  }, [name, icon, color, onApply]);

  const renderLabelRow = (l: ShipmentLabel) => {
    const active = ids.includes(l.id);
    const iconName = LABEL_ICON_MAP[l.icon] || l.icon || "pricetag";
    return (
      <TouchableOpacity
        key={l.id}
        style={[styles.row, active && { backgroundColor: `${l.color}12` }]}
        onPress={() => toggle(l.id)}
        activeOpacity={0.7}
      >
        <PhIcon name={iconName as any} size={16} color={l.color} />
        <Text style={styles.rowTxt} numberOfLines={1}>{l.name}</Text>
        {active ? (
          <PhIcon name="checkmark-circle" size={18} color={l.color} />
        ) : (
          <View style={{ width: 18 }} />
        )}
      </TouchableOpacity>
    );
  };

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={styles.sheet}>
        {view === "select" ? (
          <>
            <View style={styles.header}>
              <Text style={styles.headerTxt}>Select Label</Text>
              <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <PhIcon name="chevron-down" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 460 }} contentContainerStyle={{ paddingBottom: 8 }}>
              {loading ? (
                <ActivityIndicator style={{ marginTop: 20 }} color={colors.primary} />
              ) : (
                <>
                  {grouped.order.length > 0 && (
                    <>
                      <Text style={styles.section}>ORDER LABELS</Text>
                      {grouped.order.map(renderLabelRow)}
                    </>
                  )}
                  {grouped.priority.length > 0 && (
                    <>
                      <Text style={styles.section}>PRIORITY LABELS</Text>
                      {grouped.priority.map(renderLabelRow)}
                    </>
                  )}
                  {grouped.custom.length > 0 && (
                    <>
                      <Text style={styles.section}>CUSTOM</Text>
                      {grouped.custom.map(renderLabelRow)}
                    </>
                  )}
                </>
              )}
              <View style={styles.divider} />
              <TouchableOpacity
                style={styles.createBtn}
                onPress={() => setView("create")}
                activeOpacity={0.7}
              >
                <PhIcon name="add-circle-outline" size={18} color={colors.primary} />
                <Text style={styles.createTxt}>Create New Label</Text>
              </TouchableOpacity>
            </ScrollView>
          </>
        ) : (
          <>
            <View style={styles.header}>
              <Text style={styles.headerTxt}>Create New Label</Text>
              <TouchableOpacity onPress={() => setView("select")} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 500 }} contentContainerStyle={{ paddingBottom: 8 }}>
              <Text style={styles.fieldLabel}>Label Name</Text>
              <TextInput
                style={styles.input}
                placeholder="Enter label name"
                placeholderTextColor="#94A3B8"
                value={name}
                onChangeText={setName}
                maxLength={40}
              />
              <Text style={styles.fieldLabel}>Select Icon</Text>
              <View style={styles.grid}>
                {LABEL_ICON_KEYS.map((k) => {
                  const active = icon === k;
                  const iconName = LABEL_ICON_MAP[k] || k;
                  return (
                    <TouchableOpacity
                      key={k}
                      onPress={() => setIcon(k)}
                      style={[
                        styles.iconCell,
                        active && { borderColor: color, backgroundColor: `${color}12` },
                      ]}
                      activeOpacity={0.7}
                    >
                      <PhIcon name={iconName as any} size={22} color={active ? color : "#334155"} />
                    </TouchableOpacity>
                  );
                })}
              </View>
              <Text style={styles.fieldLabel}>Select Color</Text>
              <View style={styles.grid}>
                {LABEL_COLORS.map((c) => {
                  const active = color === c;
                  return (
                    <TouchableOpacity
                      key={c}
                      onPress={() => setColor(c)}
                      style={[
                        styles.colorCell,
                        { backgroundColor: c },
                        active && styles.colorCellActive,
                      ]}
                      activeOpacity={0.7}
                    >
                      {active ? <PhIcon name="checkmark" size={18} color="#fff" /> : null}
                    </TouchableOpacity>
                  );
                })}
              </View>
              {/* ── Phase F4.7 — Live preview chip. Rebuilds on every
                     keystroke / icon-tap / color-tap so the operator
                     sees exactly what the chip will look like once
                     saved. Uses the existing LabelChip so any future
                     chip styling change flows here automatically. */}
              <Text style={styles.fieldLabel}>Preview</Text>
              <View style={styles.previewRow}>
                <LabelChip
                  label={{
                    id:         "__preview__",
                    user_id:    "",
                    name:       name.trim() || "Label name",
                    icon:       icon,
                    color:      color,
                    kind:       "custom" as any,
                    is_default: false,
                    created_at: "",
                    updated_at: "",
                  }}
                />
              </View>
              <TouchableOpacity
                style={[styles.saveBtn, saving && { opacity: 0.6 }]}
                onPress={submitCreate}
                disabled={saving}
                activeOpacity={0.8}
              >
                {saving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.saveTxt}>Save Label</Text>
                )}
              </TouchableOpacity>
            </ScrollView>
          </>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 20,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 8,
    marginBottom: 6,
  },
  headerTxt: { fontSize: 16, fontWeight: "800", color: colors.text },
  section: {
    fontSize: 11,
    fontWeight: "800",
    color: "#94A3B8",
    letterSpacing: 0.8,
    marginTop: 12,
    marginBottom: 4,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 8,
    borderRadius: 8,
  },
  rowTxt: { flex: 1, fontSize: 14, color: colors.text, fontWeight: "600" },
  divider: { height: 1, backgroundColor: "#F1F5F9", marginVertical: 8 },
  createBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 12, paddingHorizontal: 8,
  },
  createTxt: { color: colors.primary, fontSize: 14, fontWeight: "700" },
  fieldLabel: {
    fontSize: 13, fontWeight: "700", color: colors.text,
    marginTop: 12, marginBottom: 6,
  },
  input: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 10, paddingVertical: 8, fontSize: 14, color: colors.text,
    backgroundColor: "#F9FAFB",
  },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 4 },
  iconCell: {
    width: 46, height: 46, borderRadius: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#fff",
  },
  colorCell: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: "center", justifyContent: "center",
  },
  colorCellActive: {
    borderWidth: 2, borderColor: "#0F172A",
  },
  saveBtn: {
    backgroundColor: colors.primary,
    marginTop: 16, paddingVertical: 12,
    borderRadius: 10, alignItems: "center",
  },
  // Phase F4.7 — Live preview row sits directly above the Save button.
  // Left-aligned so it visually reads as "this is what you're building".
  previewRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 4,
    paddingVertical: 8,
    paddingHorizontal: 4,
  },
  saveTxt: { color: "#fff", fontWeight: "800", fontSize: 15 },
});
