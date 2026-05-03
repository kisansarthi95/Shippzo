/**
 * Courier Rules editor — standalone screen
 * ----------------------------------------
 * Extracted from the legacy /delivery-confirmation screen as part of
 * Phase F4 cleanup. Lets the user override per-courier
 * `delivery_eta_days` (used by the bulk Delivery Check-in flow to
 * decide when a Shipped parcel should be flagged for follow-up).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TextInput, TouchableOpacity,
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Api } from "../lib/api";

export default function CourierRulesScreen() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving]   = useState(false);
  const [adminRules, setAdminRules] = useState<Record<string, { delivery_eta_days: number }>>({});
  const [userRules, setUserRules]   = useState<Record<string, { delivery_eta_days: number | string }>>({});
  const [courierNames, setCourierNames] = useState<string[]>([]);
  const [defaultEta, setDefaultEta] = useState(5);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await Api.meCourierRules();
      setAdminRules(data.admin_rules || {});
      setUserRules(data.user_rules || {});
      setCourierNames(data.courier_names || []);
      setDefaultEta(data.default_eta_days || 5);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => {
    const set = new Set<string>(courierNames);
    Object.keys(adminRules).forEach((k) => set.add(k));
    Object.keys(userRules).forEach((k) => set.add(k));
    set.delete("_default_");
    const arr = Array.from(set).sort();
    return ["_default_", ...arr];
  }, [courierNames, adminRules, userRules]);

  const setEta = (key: string, val: string) => {
    const sanitized = val.replace(/[^0-9]/g, "");
    setUserRules((prev) => {
      const next = { ...prev };
      if (sanitized === "") delete next[key];
      else next[key] = { delivery_eta_days: sanitized };
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      const rules: Record<string, { delivery_eta_days: number }> = {};
      for (const [k, v] of Object.entries(userRules)) {
        const n = parseInt(String(v.delivery_eta_days), 10);
        if (!Number.isFinite(n) || n < 0 || n > 60) continue;
        rules[k] = { delivery_eta_days: n };
      }
      await Api.meSaveCourierRules(rules);
      Alert.alert("Saved", "Courier delivery rules updated.");
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Courier Delivery Rules", headerShown: true }} />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
        keyboardVerticalOffset={20}
      >
        <ScrollView
          contentContainerStyle={{ padding: 14, paddingBottom: 110 }}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.header}>
            <Text style={styles.headerTitle}>⏱ Delivery ETA per Courier</Text>
            <Text style={styles.headerSub}>
              Set how many days after shipping each courier typically takes
              to deliver. Parcels are auto-flagged for delivery confirmation
              once they cross this threshold. Leave blank to use the
              admin/global default.
            </Text>
          </View>

          {loading ? (
            <ActivityIndicator color="#6B5BFF" style={{ marginTop: 40 }} />
          ) : (
            rows.map((key) => {
              const isDefault = key === "_default_";
              const adminVal = adminRules[key]?.delivery_eta_days;
              const placeholder = adminVal != null
                ? String(adminVal)
                : String(defaultEta);
              const userVal = userRules[key]?.delivery_eta_days;
              return (
                <View key={key} style={styles.row}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowName}>
                      {isDefault ? "🌐 All other couriers (default)" : `🚚 ${key}`}
                    </Text>
                    {!isDefault && adminVal !== undefined && (
                      <Text style={styles.rowHint}>
                        Admin default: {adminVal} days
                      </Text>
                    )}
                  </View>
                  <View style={styles.inputWrap}>
                    <TextInput
                      style={styles.input}
                      keyboardType="number-pad"
                      value={userVal === undefined ? "" : String(userVal)}
                      onChangeText={(t) => setEta(key, t)}
                      placeholder={placeholder}
                      placeholderTextColor="#9CA3AF"
                      maxLength={2}
                    />
                    <Text style={styles.inputSuffix}>days</Text>
                  </View>
                </View>
              );
            })
          )}
        </ScrollView>

        <View style={styles.saveBar}>
          <TouchableOpacity
            style={styles.cancelBtn}
            onPress={() => router.back()}
            disabled={saving}
          >
            <Text style={styles.cancelText}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.saveBtn, saving && { opacity: 0.5 }]}
            onPress={save}
            disabled={saving}
          >
            {saving ? <ActivityIndicator color="#fff" /> : (
              <>
                <Ionicons name="save-outline" size={15} color="#fff" />
                <Text style={styles.saveText}>Save Rules</Text>
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  header: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 12,
  },
  headerTitle: { fontSize: 15, fontWeight: "800", color: "#111827" },
  headerSub:   { fontSize: 12, color: "#6B7280", marginTop: 4, lineHeight: 17 },

  row: {
    flexDirection: "row", alignItems: "center",
    paddingVertical: 12, paddingHorizontal: 12,
    borderRadius: 10, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
    marginBottom: 8, gap: 10,
  },
  rowName: { fontSize: 14, fontWeight: "700", color: "#111827" },
  rowHint: { fontSize: 11, color: "#9CA3AF", marginTop: 2 },
  inputWrap: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#F9FAFB", borderRadius: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 8,
  },
  input: {
    width: 50, paddingVertical: 8,
    fontSize: 14, fontWeight: "700", color: "#111827",
    textAlign: "right",
  },
  inputSuffix: { fontSize: 11, color: "#9CA3AF", marginLeft: 4 },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    flexDirection: "row", gap: 10,
    paddingHorizontal: 16, paddingVertical: 12, paddingBottom: 16,
    backgroundColor: "#fff",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  cancelBtn: {
    flex: 1, paddingVertical: 14, borderRadius: 12,
    backgroundColor: "#F3F4F6", alignItems: "center",
  },
  cancelText: { fontSize: 13, fontWeight: "800", color: "#374151" },
  saveBtn: {
    flex: 2, paddingVertical: 14, borderRadius: 12,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: "#10B981",
  },
  saveText: { fontSize: 14, fontWeight: "800", color: "#fff" },
});
