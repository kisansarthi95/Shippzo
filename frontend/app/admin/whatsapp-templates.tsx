/**
 * Admin → WhatsApp Templates (Phase-12)
 * --------------------------------------
 * Admin sets the *defaults* for all 4 message types × 3 languages.
 * Every user starts with these as their fallback; users may override
 * individual fields via Settings → WhatsApp Templates.
 *
 * Bundled defaults live in /app/backend/routers/messaging.py and serve
 * as the final fallback if the admin clears their override.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Stack } from "expo-router";
import { Api } from "../../lib/api";

const TYPE_META: Record<string, { label: string; sub: string; icon: any; tone: string }> = {
  shipment_sent: {
    label: "Shipment Booked",
    sub: "Sent when an order is created / label is printed",
    icon: "receipt-outline",
    tone: "#6B5BFF",
  },
  dispatch_confirmation: {
    label: "Ready to Ship Confirmation",
    sub: "Sent when the parcel leaves the user's store (Shipped status)",
    icon: "rocket-outline",
    tone: "#1F4FBF",
  },
  delivery_confirmation: {
    label: "Delivery Confirmation",
    sub: "After ETA — asks the customer if the parcel arrived",
    icon: "alert-circle-outline",
    tone: "#B45309",
  },
  delivery_done: {
    label: "Delivery Thanks",
    sub: "Sent after the customer confirms receipt",
    icon: "checkmark-circle-outline",
    tone: "#1F9D55",
  },
  feedback_request: {
    label: "Feedback Request",
    sub: "Asks the customer for a rating / review (1–5 ★)",
    icon: "star-outline",
    tone: "#1E40AF",
  },
};

const LANG_META: Record<string, { label: string }> = {
  gu: { label: "ગુજરાતી" },
  hi: { label: "हिन्दी" },
  en: { label: "English" },
};

const VARIABLE_HINTS = [
  "{customer_name}", "{order_id}", "{tracking_id}", "{courier}", "{eta_days}",
];

type ServerData = {
  templates: Record<string, Record<string, string>>;
  saved_overrides: Record<string, Record<string, string>>;
  defaults: Record<string, Record<string, string>>;
  types: string[];
  languages: string[];
};

export default function AdminWhatsAppTemplates() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [data, setData] = useState<ServerData | null>(null);
  // For admin: edits start blank if no override saved (so placeholder shows
  // the bundled default), otherwise show the saved value.
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});
  const [activeType, setActiveType] = useState<string>("shipment_sent");
  const [activeLang, setActiveLang] = useState<string>("gu");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await Api.adminWhatsAppTemplates();
      setData(d);
      setEdits(d.saved_overrides || {});
    } catch (e: any) {
      Alert.alert(
        "Error",
        e?.response?.data?.detail || e?.message || "Failed (admin only)",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const setEdit = (type: string, lang: string, val: string) => {
    setEdits((prev) => {
      const next = { ...prev };
      if (!val.trim()) {
        if (next[type]) {
          const cleaned = { ...next[type] };
          delete cleaned[lang];
          if (Object.keys(cleaned).length === 0) delete next[type];
          else next[type] = cleaned;
        }
      } else {
        next[type] = { ...(next[type] || {}), [lang]: val };
      }
      return next;
    });
  };

  const placeholderFor = (type: string, lang: string): string => {
    if (!data) return "";
    return data.defaults?.[type]?.[lang] || "";
  };

  const currentValue = (type: string, lang: string): string => {
    return edits[type]?.[lang] ?? "";
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await Api.adminSaveWhatsAppTemplates(edits);
      Alert.alert("Saved", "Admin defaults updated for all users.");
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setSaving(false);
    }
  };

  const handleResetType = (type: string) => {
    Alert.alert(
      "Reset overrides",
      `Clear admin overrides for "${TYPE_META[type]?.label || type}" in all 3 languages? Bundled defaults will apply.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: () => {
            setEdits((prev) => {
              const next = { ...prev };
              delete next[type];
              return next;
            });
          },
        },
      ],
    );
  };

  const types = useMemo(
    () => data?.types || Object.keys(TYPE_META),
    [data],
  );

  const languages = useMemo(
    () => data?.languages || ["gu", "hi", "en"],
    [data],
  );

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen
        options={{ title: "WhatsApp Templates (Admin)", headerBackTitle: "Admin" }}
      />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={{ padding: 14, paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled"
        >
          {loading || !data ? (
            <ActivityIndicator color="#6B5BFF" style={{ marginVertical: 36 }} />
          ) : (
            <>
              <View style={[styles.section, { backgroundColor: "#FFF7E6", borderColor: "#F2D58A" }]}>
                <Text style={[styles.sectionTitle, { color: "#7C5400" }]}>
                  Admin defaults (apply to all users)
                </Text>
                <Text style={[styles.sectionHint, { color: "#7C5400" }]}>
                  Edits here become the starting point for every user's
                  WhatsApp template. Users can still override per-message
                  in their own Settings.
                </Text>
              </View>

              {/* Type selector */}
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Message Type</Text>
                {types.map((t) => {
                  const meta = TYPE_META[t];
                  const active = activeType === t;
                  const hasOverrides = !!edits[t] && Object.keys(edits[t]).length > 0;
                  return (
                    <TouchableOpacity
                      key={t}
                      style={[styles.typeRow, active && styles.typeRowActive]}
                      onPress={() => setActiveType(t)}
                    >
                      <View
                        style={[
                          styles.typeIconBox,
                          { backgroundColor: (meta?.tone || "#6B5BFF") + "22" },
                        ]}
                      >
                        <Ionicons
                          name={meta?.icon || "chatbubble-outline"}
                          size={18}
                          color={meta?.tone || "#6B5BFF"}
                        />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.typeLabel}>
                          {meta?.label || t}
                          {hasOverrides && (
                            <Text style={styles.overrideTag}> · custom</Text>
                          )}
                        </Text>
                        <Text style={styles.typeSub} numberOfLines={2}>
                          {meta?.sub || ""}
                        </Text>
                      </View>
                      <Ionicons
                        name={active ? "chevron-up" : "chevron-down"}
                        size={18}
                        color="#9CA3AF"
                      />
                    </TouchableOpacity>
                  );
                })}
              </View>

              {/* Editor */}
              <View style={styles.section}>
                <View style={styles.editorHeader}>
                  <Text style={styles.sectionTitle}>
                    Edit:{" "}
                    <Text style={{ color: TYPE_META[activeType]?.tone || "#6B5BFF" }}>
                      {TYPE_META[activeType]?.label || activeType}
                    </Text>
                  </Text>
                  <TouchableOpacity
                    style={styles.resetBtn}
                    onPress={() => handleResetType(activeType)}
                  >
                    <Ionicons name="refresh" size={12} color="#374151" />
                    <Text style={styles.resetBtnText}>Reset</Text>
                  </TouchableOpacity>
                </View>

                <View style={styles.langTabs}>
                  {languages.map((l) => {
                    const active = activeLang === l;
                    const has = !!edits[activeType]?.[l];
                    return (
                      <TouchableOpacity
                        key={l}
                        style={[styles.langTab, active && styles.langTabActive]}
                        onPress={() => setActiveLang(l)}
                      >
                        <Text
                          style={[styles.langTabText, active && styles.langTabTextActive]}
                        >
                          {LANG_META[l]?.label || l}
                        </Text>
                        {has && (
                          <View
                            style={[
                              styles.dot,
                              { backgroundColor: active ? "#fff" : "#6B5BFF" },
                            ]}
                          />
                        )}
                      </TouchableOpacity>
                    );
                  })}
                </View>

                <TextInput
                  style={styles.textarea}
                  multiline
                  placeholder={placeholderFor(activeType, activeLang)}
                  placeholderTextColor="#9CA3AF"
                  value={currentValue(activeType, activeLang)}
                  onChangeText={(t) => setEdit(activeType, activeLang, t)}
                  textAlignVertical="top"
                />

                <Text style={styles.previewLabel}>
                  Live preview (placeholder substituted):
                </Text>
                <View style={styles.previewBox}>
                  <Text style={styles.previewText}>
                    {(currentValue(activeType, activeLang) ||
                      placeholderFor(activeType, activeLang) ||
                      "—")
                      .replace(/\{customer_name\}/g, "Ramesh")
                      .replace(/\{order_id\}/g, "ORD-1234")
                      .replace(/\{tracking_id\}/g, "ND00056")
                      .replace(/\{courier\}/g, "Demo Courier")
                      .replace(/\{eta_days\}/g, "5")}
                  </Text>
                </View>

                <Text style={styles.varLabel}>Available variables (tap to insert):</Text>
                <View style={styles.varChipsRow}>
                  {VARIABLE_HINTS.map((v) => (
                    <TouchableOpacity
                      key={v}
                      style={styles.varChip}
                      onPress={() => {
                        const current = currentValue(activeType, activeLang) ||
                                        placeholderFor(activeType, activeLang);
                        setEdit(activeType, activeLang, current + v);
                      }}
                    >
                      <Text style={styles.varChipText}>{v}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            </>
          )}
        </ScrollView>

        <View style={styles.stickyBar}>
          <Text style={styles.bottomHint}>
            Empty fields = bundled defaults
          </Text>
          <TouchableOpacity
            style={[styles.saveBtn, saving && { opacity: 0.5 }]}
            onPress={handleSave}
            disabled={saving}
          >
            <Ionicons name="save-outline" size={16} color="#fff" />
            <Text style={styles.saveBtnText}>
              {saving ? "Saving…" : "Save Defaults"}
            </Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

// Re-using the same styles structure as the user templates screen for visual consistency.
const styles = StyleSheet.create({
  section: {
    backgroundColor: "#fff", borderRadius: 14, padding: 14,
    marginBottom: 12, borderWidth: 1, borderColor: "#EDEEF1",
  },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  sectionHint: {
    fontSize: 12, color: "#6B7280",
    marginTop: 4, marginBottom: 8, lineHeight: 17,
  },
  typeRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 10, paddingHorizontal: 8, borderRadius: 10,
    marginTop: 8, backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#EDEEF1",
  },
  typeRowActive: { backgroundColor: "#F8F7FF", borderColor: "#6B5BFF" },
  typeIconBox: {
    width: 36, height: 36, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  typeLabel: { fontSize: 14, fontWeight: "800", color: "#111827" },
  typeSub: { fontSize: 11, color: "#6B7280", marginTop: 2 },
  overrideTag: { color: "#6B5BFF", fontWeight: "700", fontSize: 11 },
  editorHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", marginBottom: 10,
  },
  resetBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#F3F4F6", borderRadius: 999,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  resetBtnText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  langTabs: { flexDirection: "row", gap: 6, marginBottom: 10 },
  langTab: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 9, borderRadius: 10,
    backgroundColor: "#F3F4F6",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  langTabActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  langTabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  langTabTextActive: { color: "#fff" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  textarea: {
    backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, padding: 12,
    minHeight: 130, fontSize: 13,
    color: "#111827", lineHeight: 20,
  },
  previewLabel: {
    fontSize: 11, fontWeight: "700", color: "#6B7280",
    marginTop: 12, marginBottom: 4,
  },
  previewBox: {
    backgroundColor: "#E6F7EE",
    borderWidth: 1, borderColor: "#B7E5C9",
    borderRadius: 10, padding: 10,
  },
  previewText: { fontSize: 12, color: "#0F5132", lineHeight: 18 },
  varLabel: {
    fontSize: 11, fontWeight: "700", color: "#6B7280",
    marginTop: 12, marginBottom: 6,
  },
  varChipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  varChip: {
    paddingHorizontal: 8, paddingVertical: 5,
    backgroundColor: "#EEE9FF", borderRadius: 6,
    borderWidth: 1, borderColor: "#D8D2FF",
  },
  varChipText: { fontSize: 11, fontWeight: "700", color: "#6B5BFF" },
  stickyBar: {
    backgroundColor: "#fff",
    borderTopWidth: 1, borderColor: "#EDEEF1",
    paddingHorizontal: 12, paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 12,
    flexDirection: "row", alignItems: "center", gap: 10,
  },
  bottomHint: { flex: 1, fontSize: 11, color: "#6B7280" },
  saveBtn: {
    flexDirection: "row", alignItems: "center",
    gap: 6, paddingHorizontal: 16, paddingVertical: 10,
    borderRadius: 10, backgroundColor: "#6B5BFF",
  },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
});
