/**
 * Field Requirements — Phase-8.
 *
 * Centralised list of every Smart Paste / New Shipment field with a
 * Required (mandatory) toggle. Toggles persist to:
 *   • PUT /settings  (built-in fields → settings.field_requirements)
 *   • PUT /me/custom-fields/{id}  (per-user Custom Fields → required)
 *
 * Reads are merged with backend defaults so newly-added field keys
 * always appear with sensible defaults until the user changes them.
 */
import React, { useCallback, useEffect, useState } from "react";
import PhIcon from "../components/PhIcon";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  Switch,
  Alert,
  ActivityIndicator,
  TouchableOpacity,
} from "react-native";
import { Stack, router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api, CustomField, Settings } from "../lib/api";
import { colors } from "../lib/theme";

// Must match backend DEFAULT_FIELD_REQUIREMENTS keys exactly.
const BUILT_IN_FIELDS: Array<{
  key: string;
  label: string;
  hint?: string;
  // When true, the toggle is read-only because the field has special
  // logic the user can't override (e.g. token_amount is auto-required
  // when payment_mode === "COD" regardless of this flag).
  pinnedNote?: string;
}> = [
  { key: "customer_name",      label: "Customer Name" },
  { key: "customer_phone",     label: "Mobile Number" },
  { key: "customer_alt_phone", label: "Alternate Mobile" },
  { key: "address_line1",      label: "Address" },
  { key: "city",               label: "City" },
  { key: "state",              label: "State" },
  { key: "pincode",            label: "Pincode" },
  { key: "items",              label: "Item(s)" },
  { key: "amount",             label: "Amount" },
  { key: "payment_mode",       label: "Payment Mode" },
  {
    key: "token_amount",
    label: "Token Amount",
    pinnedNote: "Always required when COD is selected.",
  },
  { key: "courier_name",       label: "Courier" },
  { key: "order_id",           label: "Order ID" },
  { key: "weight",             label: "Weight" },
  { key: "notes",              label: "Notes" },
];

// Defaults sent if backend never seeded this user's settings.
const DEFAULTS: Record<string, boolean> = {
  customer_name:      true,
  customer_phone:     true,
  customer_alt_phone: false,
  address_line1:      true,
  city:               true,
  state:              true,
  pincode:            true,
  items:              false,
  amount:             true,
  payment_mode:       true,
  token_amount:       false,
  courier_name:       false,
  order_id:           false,
  weight:             true,
  notes:              false,
};

export default function FieldRequirementsScreen() {
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [reqs, setReqs] = useState<Record<string, boolean>>({});
  const [customFields, setCustomFields] = useState<CustomField[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, cf] = await Promise.all([
        Api.getSettings(),
        Api.listMyCustomFields().catch(() => ({ fields: [] as CustomField[] })),
      ]);
      const fr = ((s as Settings)?.field_requirements || {}) as Record<string, boolean>;
      // Merge defaults so newly-added built-in keys always appear.
      const merged: Record<string, boolean> = { ...DEFAULTS };
      for (const k of Object.keys(fr)) {
        if (k in DEFAULTS) merged[k] = !!fr[k];
      }
      setReqs(merged);
      setCustomFields(((cf as any)?.fields || []).filter((f: CustomField) => f.active ?? true));
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const toggleBuiltIn = async (key: string, val: boolean) => {
    // Optimistic update — revert on failure.
    const prev = reqs[key];
    setReqs((r) => ({ ...r, [key]: val }));
    setSavingKey(key);
    try {
      await Api.updateSettings({
        field_requirements: { [key]: val },
      } as any);
    } catch (e: any) {
      setReqs((r) => ({ ...r, [key]: prev }));
      Alert.alert("Error", e?.response?.data?.detail || "Failed to save");
    } finally {
      setSavingKey(null);
    }
  };

  const toggleCustom = async (cf: CustomField, val: boolean) => {
    const prev = !!cf.required;
    setCustomFields((arr) =>
      arr.map((f) => (f.id === cf.id ? { ...f, required: val } : f)),
    );
    setSavingKey(`cf-${cf.id}`);
    try {
      await Api.updateMyCustomField(cf.id, { required: val } as any);
    } catch (e: any) {
      setCustomFields((arr) =>
        arr.map((f) => (f.id === cf.id ? { ...f, required: prev } : f)),
      );
      Alert.alert("Error", e?.response?.data?.detail || "Failed to save");
    } finally {
      setSavingKey(null);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}>
      <Stack.Screen
        options={{ title: "Field Requirements", headerBackTitle: "Back" }}
      />
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        {/* Intro banner */}
        <View style={styles.banner}>
          <PhIcon name="information-circle" size={20} color={colors.primary} />
          <Text style={styles.bannerText}>
            Toggle which fields are required (mandatory) when saving a
            shipment. Applies to both Smart Paste and the New Shipment form.
          </Text>
        </View>

        {/* Built-in section */}
        <Text style={styles.sectionLabel}>Built-in Fields</Text>
        {loading ? (
          <ActivityIndicator color={colors.primary} style={{ marginVertical: 24 }} />
        ) : (
          BUILT_IN_FIELDS.map((f) => (
            <View key={f.key} style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>{f.label}</Text>
                {f.pinnedNote ? (
                  <Text style={styles.rowHint}>{f.pinnedNote}</Text>
                ) : null}
              </View>
              {savingKey === f.key ? (
                <ActivityIndicator size="small" color={colors.primary} />
              ) : (
                <Switch
                  testID={`req-toggle-${f.key}`}
                  value={!!reqs[f.key]}
                  onValueChange={(v) => toggleBuiltIn(f.key, v)}
                />
              )}
            </View>
          ))
        )}

        {/* Custom Fields section */}
        <View style={{ flexDirection: "row", justifyContent: "space-between", marginTop: 24, alignItems: "center" }}>
          <Text style={[styles.sectionLabel, { marginTop: 0 }]}>Your Custom Fields</Text>
          <TouchableOpacity onPress={() => router.push("/custom-fields")}>
            <Text style={styles.linkText}>Manage →</Text>
          </TouchableOpacity>
        </View>
        {customFields.length === 0 ? (
          <Text style={styles.emptyText}>
            No custom fields yet. Add some in Manage Custom Fields.
          </Text>
        ) : (
          customFields.map((cf) => (
            <View key={cf.id} style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.rowLabel}>{cf.name}</Text>
                <Text style={styles.rowHint}>
                  Col {cf.column_letter} · {cf.field_type}
                </Text>
              </View>
              {savingKey === `cf-${cf.id}` ? (
                <ActivityIndicator size="small" color={colors.primary} />
              ) : (
                <Switch
                  testID={`req-toggle-cf-${cf.id}`}
                  value={!!cf.required}
                  onValueChange={(v) => toggleCustom(cf, v)}
                />
              )}
            </View>
          ))
        )}

        <View style={{ height: 40 }} />
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    backgroundColor: "#EFF6FF",
    borderRadius: 12,
    padding: 12,
    gap: 10,
    marginBottom: 16,
    alignItems: "flex-start",
  },
  bannerText: {
    flex: 1,
    color: "#1E3A8A",
    fontSize: 13,
    lineHeight: 18,
  },
  sectionLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.textMuted,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginTop: 8,
    marginBottom: 8,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.surface,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 12,
    marginBottom: 8,
    gap: 12,
  },
  rowLabel: {
    fontSize: 15,
    fontWeight: "600",
    color: colors.text,
  },
  rowHint: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  linkText: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "600",
  },
  emptyText: {
    color: colors.textMuted,
    fontSize: 13,
    textAlign: "center",
    paddingVertical: 16,
  },
});
