/**
 * Phase-29 (2026-05-17) — Dedicated Super-Admin screen for the
 * AI-Processing rate card. Previously this editor was inlined on the
 * regular Plan & Billing screen (gated by `user.is_admin`), but that
 * mixed admin-only controls with the user-facing plan picker / wallet
 * view and caused confusion.
 *
 * Now:
 *   • Plan & Billing on the Settings tab is admin- and user-clean
 *     (plan picker + wallet only).
 *   • Admins navigate here via the Admin Hub tile "AI Processing
 *     Rates" and edit Simple/Medium/Complex credits per order.
 *   • API contract unchanged: GET /api/me/ai-rates to load,
 *     PUT /api/admin/global-config { global_ai_rates: {…} } to save.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";

import PhIcon from "../../components/PhIcon";
import { api } from "../../lib/api";
import { colors } from "../../lib/theme";

function clamp02(v: string, fallback: number): number {
  const n = parseFloat(v);
  if (Number.isNaN(n) || n < 0) return fallback;
  return Math.min(n, 2.0);
}

export default function AdminAiRatesScreen() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [aiCostSimple, setAiCostSimple] = useState("0.5");
  const [aiCostMedium, setAiCostMedium] = useState("1.0");
  const [aiCostComplex, setAiCostComplex] = useState("2.0");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ simple: number; medium: number; complex: number }>(
        "/me/ai-rates"
      );
      const d = r.data || ({} as any);
      setAiCostSimple(String(d.simple ?? 0.5));
      setAiCostMedium(String(d.medium ?? 1.0));
      setAiCostComplex(String(d.complex ?? 2.0));
    } catch (e: any) {
      // Non-fatal — fall back to spec defaults already in state.
      // eslint-disable-next-line no-console
      console.warn("[ai-rates] load failed", e?.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const save = async () => {
    try {
      setSaving(true);
      const simple = clamp02(aiCostSimple, 0.5);
      const medium = clamp02(aiCostMedium, 1.0);
      const complex = clamp02(aiCostComplex, 2.0);
      const r = await api.put<{ global_ai_rates?: any }>("/admin/global-config", {
        global_ai_rates: { simple, medium, complex },
      });
      const d = (r.data as any)?.global_ai_rates || {};
      setAiCostSimple(String(d.simple ?? simple));
      setAiCostMedium(String(d.medium ?? medium));
      setAiCostComplex(String(d.complex ?? complex));
      Alert.alert(
        "Global rate card saved",
        `Simple ${d.simple ?? simple} · Medium ${d.medium ?? medium} · Complex ${
          d.complex ?? complex
        } credits per order. Applied to every user.`
      );
    } catch (e: any) {
      Alert.alert(
        "Save failed",
        e?.response?.data?.detail || e?.message || "Please try again"
      );
    } finally {
      setSaving(false);
    }
  };

  const resetDefaults = () => {
    setAiCostSimple("0.5");
    setAiCostMedium("1.0");
    setAiCostComplex("2.0");
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
          <PhIcon name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1, marginLeft: 8 }}>
          <Text style={styles.headerTitle}>AI Processing Rates</Text>
          <Text style={styles.headerSub}>Super Admin · global rate card</Text>
        </View>
        <View style={styles.adminBadge}>
          <PhIcon name="shield-checkmark" size={12} color="#fff" />
          <Text style={styles.adminBadgeTxt}>ADMIN</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 40 }}>
        <View style={styles.introCard}>
          <PhIcon name="information-circle-outline" size={18} color="#1D4ED8" />
          <Text style={styles.introText}>
            Set the credits to deduct per shipment for AI address checks.
            These rates apply globally to every user. Max cap is 2.0
            credits/order (values above 2.0 are clamped server-side).
          </Text>
        </View>

        {loading ? (
          <ActivityIndicator
            size="large"
            color={colors.primary}
            style={{ marginTop: 24 }}
          />
        ) : (
          <>
            <View style={styles.rateGrid}>
              <View style={[styles.rateCell, { borderColor: "#04785755" }]}>
                <View style={styles.rateCellHead}>
                  <View
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 4,
                      backgroundColor: "#047857",
                    }}
                  />
                  <Text style={[styles.rateCellLabel, { color: "#047857" }]}>
                    Simple
                  </Text>
                </View>
                <Text style={styles.rateCellHint}>short & clean</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-simple"
                    style={styles.rateCellInput}
                    value={aiCostSimple}
                    onChangeText={(t) =>
                      setAiCostSimple(t.replace(/[^\d.]/g, ""))
                    }
                    keyboardType="decimal-pad"
                    placeholder="0.5"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>

              <View style={[styles.rateCell, { borderColor: "#B4530955" }]}>
                <View style={styles.rateCellHead}>
                  <View
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 4,
                      backgroundColor: "#B45309",
                    }}
                  />
                  <Text style={[styles.rateCellLabel, { color: "#B45309" }]}>
                    Medium
                  </Text>
                </View>
                <Text style={styles.rateCellHint}>some extra text</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-medium"
                    style={styles.rateCellInput}
                    value={aiCostMedium}
                    onChangeText={(t) =>
                      setAiCostMedium(t.replace(/[^\d.]/g, ""))
                    }
                    keyboardType="decimal-pad"
                    placeholder="1.0"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>

              <View style={[styles.rateCell, { borderColor: "#B91C1C55" }]}>
                <View style={styles.rateCellHead}>
                  <View
                    style={{
                      width: 8,
                      height: 8,
                      borderRadius: 4,
                      backgroundColor: "#B91C1C",
                    }}
                  />
                  <Text style={[styles.rateCellLabel, { color: "#B91C1C" }]}>
                    Complex
                  </Text>
                </View>
                <Text style={styles.rateCellHint}>long, messy</Text>
                <View style={styles.rateCellInputRow}>
                  <TextInput
                    testID="rate-complex"
                    style={styles.rateCellInput}
                    value={aiCostComplex}
                    onChangeText={(t) =>
                      setAiCostComplex(t.replace(/[^\d.]/g, ""))
                    }
                    keyboardType="decimal-pad"
                    placeholder="2.0"
                    maxLength={4}
                  />
                  <Text style={styles.rateCellUnit}>cr</Text>
                </View>
              </View>
            </View>

            <Text style={styles.rateNote}>
              ⚠️ Max per-order cap 2.0 — values above 2.0 are clamped
              server-side.
            </Text>

            <View style={styles.actions}>
              <TouchableOpacity
                testID="rate-save-btn"
                disabled={saving}
                onPress={save}
                style={[styles.primaryBtn, saving && { opacity: 0.6 }]}
              >
                {saving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <PhIcon name="checkmark-circle" size={16} color="#fff" />
                    <Text style={styles.primaryBtnTxt}>Save rates</Text>
                  </>
                )}
              </TouchableOpacity>
              <TouchableOpacity
                testID="rate-reset-btn"
                style={styles.secondaryBtn}
                onPress={resetDefaults}
              >
                <PhIcon name="refresh" size={16} color={colors.primary} />
                <Text style={styles.secondaryBtnTxt}>Defaults</Text>
              </TouchableOpacity>
            </View>

            <Text style={styles.footnote}>
              Changes apply immediately to every user's wallet deductions
              on new AI address checks.
            </Text>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: colors.surface,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
  },
  headerTitle: { fontSize: 17, fontWeight: "700", color: colors.text },
  headerSub: { fontSize: 12, color: colors.textMuted },
  adminBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "#111827",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
  },
  adminBadgeTxt: { color: "#fff", fontSize: 10, fontWeight: "800" },

  introCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "#DBEAFE",
    padding: 12,
    borderRadius: 10,
    marginBottom: 16,
  },
  introText: { flex: 1, color: "#1E3A8A", fontSize: 13, lineHeight: 18 },

  rateGrid: {
    flexDirection: "row",
    gap: 10,
    marginBottom: 8,
  },
  rateCell: {
    flex: 1,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    backgroundColor: colors.surface,
  },
  rateCellHead: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 4,
  },
  rateCellLabel: { fontSize: 14, fontWeight: "800" },
  rateCellHint: { fontSize: 11, color: "#6B7280", marginBottom: 8 },
  rateCellInputRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "#F9FAFB",
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: Platform.OS === "ios" ? 8 : 4,
  },
  rateCellInput: {
    flex: 1,
    fontSize: 18,
    fontWeight: "700",
    color: colors.text,
    paddingVertical: 0,
  },
  rateCellUnit: { fontSize: 11, color: "#6B7280", fontWeight: "700" },

  rateNote: {
    fontSize: 11,
    color: "#92400E",
    marginTop: 8,
    fontStyle: "italic",
  },
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 16,
  },
  primaryBtn: {
    flex: 2,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: "#EA580C",
    paddingVertical: 14,
    borderRadius: 10,
  },
  primaryBtnTxt: { color: "#fff", fontSize: 15, fontWeight: "800" },
  secondaryBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 14,
    borderRadius: 10,
    backgroundColor: "#FFF7ED",
    borderWidth: 1,
    borderColor: "#FED7AA",
  },
  secondaryBtnTxt: { color: colors.primary, fontSize: 14, fontWeight: "800" },
  footnote: {
    fontSize: 11,
    color: colors.textMuted,
    textAlign: "center",
    marginTop: 18,
    lineHeight: 16,
  },
});
