/**
 * Phase-24 — Admin → Field Controls (per-module).
 *
 * Super-admin screen for toggling the *visibility* and *required*
 * flags of every configurable field in a module (e.g. `new_shipment`).
 *
 * UX contract:
 *   • LOCKED fields render in a separate "Always required" block with
 *     a 🔒 badge and disabled switches — they cannot be turned off
 *     because the backend rejects mutations on them.
 *   • Each configurable field has two switches: Show / Required.
 *     Toggling either calls PATCH on the backend immediately and
 *     reloads from the server response (single source of truth).
 *   • Optimistic UI is intentionally avoided here — the screen is
 *     low-traffic (admin only) and instant correctness beats latency.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";

import PhIcon from "../../../components/PhIcon";
import {
  adminGetFieldConfig,
  adminPatchFieldConfig,
  FieldRule,
  ModuleConfig,
} from "../../../lib/fieldConfig";
import { colors } from "../../../lib/theme";

const MODULE_TITLES: Record<string, { title: string; subtitle: string }> = {
  new_shipment: {
    title: "New Shipment",
    subtitle:
      "Control which fields appear on the Add Shipment form and which are mandatory. Locked fields are always required for valid label generation.",
  },
};

function prettyTitle(m: string) {
  return MODULE_TITLES[m]?.title ?? m.replace(/_/g, " ");
}

function prettySubtitle(m: string) {
  return (
    MODULE_TITLES[m]?.subtitle ??
    "Toggle visibility and required-ness for the fields in this module."
  );
}

export default function AdminFieldConfigsScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ module?: string }>();
  const moduleKey = (params.module || "new_shipment") as string;

  const [cfg, setCfg] = useState<ModuleConfig | null>(null);
  const [loading, setLoading] = useState(true);
  // Track per-field "in-flight" so we can disable the row while
  // PATCH is on the wire.
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await adminGetFieldConfig(moduleKey);
      setCfg(data);
    } catch (e: any) {
      Alert.alert(
        "Couldn't load field configs",
        e?.response?.data?.detail || e?.message || "Unknown error"
      );
    } finally {
      setLoading(false);
    }
  }, [moduleKey]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onPatch = useCallback(
    async (
      field_key: string,
      patch: { enabled?: boolean; required?: boolean }
    ) => {
      setBusyKey(field_key);
      try {
        const next = await adminPatchFieldConfig(moduleKey, field_key, patch);
        setCfg(next);
      } catch (e: any) {
        Alert.alert(
          "Update failed",
          e?.response?.data?.detail || e?.message || "Unknown error"
        );
      } finally {
        setBusyKey(null);
      }
    },
    [moduleKey]
  );

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <PhIcon name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Field Controls</Text>
          <Text style={styles.headerSub} numberOfLines={1}>
            {prettyTitle(moduleKey)}
          </Text>
        </View>
        <TouchableOpacity
          onPress={refresh}
          style={styles.refreshBtn}
          disabled={loading}
        >
          <PhIcon
            name="refresh"
            size={20}
            color={loading ? "#9CA3AF" : colors.text}
          />
        </TouchableOpacity>
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.introCard}>
          <PhIcon name="information-circle-outline" size={18} color="#1D4ED8" />
          <Text style={styles.introText}>{prettySubtitle(moduleKey)}</Text>
        </View>

        {loading && !cfg ? (
          <ActivityIndicator
            color={colors.primary}
            size="large"
            style={{ marginTop: 32 }}
          />
        ) : !cfg ? (
          <Text style={styles.empty}>No configuration available.</Text>
        ) : (
          <>
            {/* LOCKED FIELDS */}
            <Text style={styles.sectionLabel}>
              Locked fields ({cfg.locked.length})
            </Text>
            <Text style={styles.sectionHint}>
              These cannot be disabled or made optional — they're required for
              every shipment label.
            </Text>
            <View style={styles.card}>
              {cfg.locked.map((f, idx) => (
                <FieldRow
                  key={f.field_key}
                  rule={f}
                  isLast={idx === cfg.locked.length - 1}
                  busy={false}
                  onPatch={undefined}
                />
              ))}
            </View>

            {/* CONFIGURABLE FIELDS */}
            <Text style={[styles.sectionLabel, { marginTop: 24 }]}>
              Configurable fields ({cfg.configurable.length})
            </Text>
            <Text style={styles.sectionHint}>
              Toggle Show to add/remove the field from the form. Toggle
              Required to make answering mandatory.
            </Text>
            <View style={styles.card}>
              {cfg.configurable.map((f, idx) => (
                <FieldRow
                  key={f.field_key}
                  rule={f}
                  isLast={idx === cfg.configurable.length - 1}
                  busy={busyKey === f.field_key}
                  onPatch={onPatch}
                />
              ))}
            </View>

            <Text style={styles.footnote}>
              Changes apply immediately to every user's "New Shipment" screen.
              Locked fields are enforced on the backend.
            </Text>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

// ── A single row: label + Show + Required switches ──────────────────
function FieldRow({
  rule,
  isLast,
  busy,
  onPatch,
}: {
  rule: FieldRule;
  isLast: boolean;
  busy: boolean;
  onPatch?: (
    field_key: string,
    patch: { enabled?: boolean; required?: boolean }
  ) => void | Promise<void>;
}) {
  const locked = rule.locked || !onPatch;

  return (
    <View style={[styles.row, isLast && styles.rowLast]}>
      <View style={{ flex: 1, paddingRight: 8 }}>
        <View style={styles.rowTitleLine}>
          <Text style={styles.rowTitle} numberOfLines={1}>
            {rule.label}
          </Text>
          {locked ? (
            <View style={styles.lockBadge}>
              <PhIcon name="lock-closed" size={10} color="#92400E" />
              <Text style={styles.lockBadgeTxt}>LOCKED</Text>
            </View>
          ) : null}
        </View>
        <Text style={styles.rowKey} numberOfLines={1}>
          {rule.field_key}
        </Text>
        {rule.hint ? (
          <Text style={styles.rowHint} numberOfLines={2}>
            {rule.hint}
          </Text>
        ) : null}
      </View>

      <View style={styles.switches}>
        <View style={styles.switchCol}>
          <Text style={styles.switchLabel}>Show</Text>
          <Switch
            value={rule.enabled}
            disabled={locked || busy}
            onValueChange={(v) => onPatch && onPatch(rule.field_key, { enabled: v })}
            trackColor={{ false: "#E5E7EB", true: colors.primary + "AA" }}
            thumbColor={rule.enabled ? colors.primary : "#9CA3AF"}
          />
        </View>
        <View style={styles.switchCol}>
          <Text style={styles.switchLabel}>Req'd</Text>
          <Switch
            value={rule.required}
            disabled={locked || busy || !rule.enabled}
            onValueChange={(v) => onPatch && onPatch(rule.field_key, { required: v })}
            trackColor={{ false: "#E5E7EB", true: "#DC2626AA" }}
            thumbColor={rule.required ? "#DC2626" : "#9CA3AF"}
          />
        </View>
      </View>
    </View>
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
  backBtn: { padding: 6, marginRight: 6 },
  refreshBtn: { padding: 6 },
  headerTitle: { fontSize: 17, fontWeight: "700", color: colors.text },
  headerSub: { fontSize: 12, color: colors.textMuted },

  scroll: { padding: 16, paddingBottom: 40 },

  introCard: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    backgroundColor: "#DBEAFE",
    padding: 12,
    borderRadius: 10,
    marginBottom: 18,
  },
  introText: { flex: 1, color: "#1E3A8A", fontSize: 13, lineHeight: 18 },

  sectionLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: colors.text,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 4,
  },
  sectionHint: {
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 10,
    lineHeight: 16,
  },

  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    overflow: "hidden",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    minHeight: 64,
  },
  rowLast: { borderBottomWidth: 0 },
  rowTitleLine: { flexDirection: "row", alignItems: "center", gap: 6 },
  rowTitle: { fontSize: 15, fontWeight: "600", color: colors.text },
  rowKey: { fontSize: 11, color: "#6B7280", marginTop: 2, fontFamily: undefined },
  rowHint: { fontSize: 11, color: "#6B7280", marginTop: 4, lineHeight: 14 },

  lockBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  lockBadgeTxt: { color: "#92400E", fontSize: 9, fontWeight: "800" },

  switches: { flexDirection: "row", gap: 10, alignItems: "center" },
  switchCol: { alignItems: "center", gap: 2, width: 56 },
  switchLabel: { fontSize: 10, color: "#6B7280", fontWeight: "600" },

  empty: { textAlign: "center", color: colors.textMuted, marginTop: 32 },
  footnote: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 18,
    lineHeight: 16,
    textAlign: "center",
  },
});
