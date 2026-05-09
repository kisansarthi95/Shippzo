/**
 * /app/webhook-mapping.tsx — Phase F3 per-webhook mapping editor.
 *
 * Each multi-webhook row needs its OWN field-mapping configuration
 * (Dukaan's `customer_name` field is `shipping_name`, Shopify's is
 * `shipping_address.first_name`, etc.). The legacy `/webhook-config`
 * page only edits the single legacy webhook — this screen handles
 * any v2 webhook by id.
 *
 * Routing: /webhook-mapping?wh_id=<id>
 *
 * Workflow:
 *   1. Load the webhook's saved mapping + recent samples.
 *   2. User can either click "Use last received payload" (auto-loads
 *      keys from the most recent sample) or paste a fresh JSON.
 *   3. Backend's preview endpoint returns: detected keys, sample
 *      values, schema fields, custom fields, suggested mapping.
 *   4. User maps each desired source key to a schema field via a
 *      bottom-sheet picker.
 *   5. Save → PUT /me/webhooks/{id} with { mapping }.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet, RefreshControl,
  TextInput, ActivityIndicator, Alert, Modal, FlatList,
  KeyboardAvoidingView, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Clipboard from "expo-clipboard";
import { useLocalSearchParams, Stack, useRouter } from "expo-router";
import PhIcon from "../components/PhIcon";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";

const NONE_VALUE = "__none__";

type SchemaFieldOpt = { key: string; label: string };

export default function WebhookMappingScreen() {
  const router = useRouter();
  const { wh_id } = useLocalSearchParams<{ wh_id?: string }>();

  const [loading, setLoading]       = useState(true);
  const [saving, setSaving]         = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const [webhook, setWebhook]       = useState<any>(null);
  const [keys, setKeys]             = useState<string[]>([]);
  const [sampleVals, setSampleVals] = useState<Record<string, any>>({});
  const [mapping, setMapping]       = useState<Record<string, string>>({});
  const [schemaFields, setSchemaFields] = useState<SchemaFieldOpt[]>([]);
  const [customFields, setCustomFields] = useState<{ id: string; label: string }[]>([]);

  // Field-picker modal state
  const [pickerKey, setPickerKey]   = useState<string | null>(null);
  const [pickerSearch, setPickerSearch] = useState("");

  // Paste sample JSON modal
  const [pasteOpen, setPasteOpen]   = useState(false);
  const [pasteText, setPasteText]   = useState("");
  const [pastingPreview, setPastingPreview] = useState(false);

  /** Load webhook detail + run a preview against the latest sample
   *  payload (if any) so the matrix is pre-populated on first paint. */
  const load = useCallback(async () => {
    if (!wh_id) {
      setLoading(false);
      return;
    }
    try {
      const wh = await Api.getWebhook(String(wh_id));
      setWebhook(wh);
      setMapping(wh.mapping || {});
      // Backend returns SCHEMA_FIELDS as an array of strings; convert
      // to {key,label} objects for the picker UI.
      const schemaList: SchemaFieldOpt[] = (wh.schema_fields || []).map(
        (k: string) => ({
          key:   k,
          label: k.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase()),
        }),
      );
      setSchemaFields(schemaList);
      setCustomFields(wh.custom_fields || []);

      // Auto-prime keys from the most recent sample so the user sees
      // something useful on first load.
      const samples = wh.recent_samples || [];
      if (samples.length > 0) {
        const latest = samples[samples.length - 1].payload;
        try {
          const preview = await Api.previewWebhookV2(String(wh_id), latest);
          setKeys(preview.keys || []);
          setSampleVals(preview.sample_values || {});
        } catch {
          // Non-fatal — user can still paste a fresh sample.
        }
      }
    } catch (e: any) {
      Alert.alert("Couldn't load webhook", e?.message || "Try again.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [wh_id]);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  /** Replace the keys/sample with the most recent received payload. */
  const useLastReceived = async () => {
    if (!webhook?.recent_samples?.length) {
      Alert.alert(
        "No samples yet",
        "Send a test payload to this webhook URL first. Once we receive one, we'll pre-fill the keys here automatically.",
      );
      return;
    }
    setPastingPreview(true);
    try {
      const latest = webhook.recent_samples[webhook.recent_samples.length - 1].payload;
      const preview = await Api.previewWebhookV2(String(wh_id), latest);
      setKeys(preview.keys || []);
      setSampleVals(preview.sample_values || {});
      // Apply suggested mapping for any keys we DON'T already have a
      // user-set mapping for. Existing user choices are preserved.
      const merged = { ...mapping };
      for (const [k, v] of Object.entries(preview.suggested || {})) {
        if (!merged[k]) merged[k] = v as string;
      }
      setMapping(merged);
      Alert.alert("Loaded", `Detected ${(preview.keys || []).length} keys from the last payload.`);
    } catch (e: any) {
      Alert.alert("Failed", e?.message || "Try again.");
    } finally {
      setPastingPreview(false);
    }
  };

  /** Paste a custom JSON sample (e.g. straight from Dukaan/Shopify
   *  developer console) and rebuild the keys list from it. */
  const submitPaste = async () => {
    let parsed: any = null;
    try {
      parsed = JSON.parse(pasteText);
    } catch {
      Alert.alert("Invalid JSON", "Please paste a valid JSON object or array.");
      return;
    }
    setPastingPreview(true);
    try {
      const preview = await Api.previewWebhookV2(String(wh_id), parsed);
      setKeys(preview.keys || []);
      setSampleVals(preview.sample_values || {});
      const merged = { ...mapping };
      for (const [k, v] of Object.entries(preview.suggested || {})) {
        if (!merged[k]) merged[k] = v as string;
      }
      setMapping(merged);
      setPasteOpen(false);
      setPasteText("");
    } catch (e: any) {
      Alert.alert("Preview failed", e?.message || "Try again.");
    } finally {
      setPastingPreview(false);
    }
  };

  /** Persist mapping + go back. */
  const save = async () => {
    setSaving(true);
    try {
      // Filter out NONE_VALUE entries before sending to backend.
      const cleanMapping: Record<string, string> = {};
      for (const [k, v] of Object.entries(mapping)) {
        if (v && v !== NONE_VALUE) cleanMapping[k] = v;
      }
      await Api.updateWebhook(String(wh_id), { mapping: cleanMapping });
      Alert.alert("Saved", "Field mapping updated.", [
        { text: "OK", onPress: () => router.back() },
      ]);
    } catch (e: any) {
      Alert.alert(
        "Save failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  /** Bottom-sheet field picker for a single source key. */
  const pickField = (k: string, value: string) => {
    setMapping((prev) => {
      const next = { ...prev };
      if (!value || value === NONE_VALUE) {
        delete next[k];
      } else {
        next[k] = value;
      }
      return next;
    });
    setPickerKey(null);
    setPickerSearch("");
  };

  const fieldOptions = useMemo(() => {
    const base = schemaFields.map((f) => ({
      key:   f.key,
      label: f.label,
      group: "Schema",
    }));
    const custom = customFields.map((cf) => ({
      key:   `cf:${cf.id}`,
      label: cf.label,
      group: "Custom Fields",
    }));
    return [...base, ...custom];
  }, [schemaFields, customFields]);

  const filteredOptions = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    if (!q) return fieldOptions;
    return fieldOptions.filter(
      (o) => o.label.toLowerCase().includes(q) || o.key.toLowerCase().includes(q),
    );
  }, [fieldOptions, pickerSearch]);

  const selectedFieldFor = (k: string): SchemaFieldOpt | null => {
    const v = mapping[k];
    if (!v) return null;
    return fieldOptions.find((o) => o.key === v) || null;
  };

  const mappedCount = Object.keys(mapping).filter(
    (k) => mapping[k] && mapping[k] !== NONE_VALUE,
  ).length;

  if (loading) {
    return (
      <SafeAreaView style={[styles.safe, styles.center]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  if (!webhook) {
    return (
      <SafeAreaView style={[styles.safe, styles.center]}>
        <Text style={styles.emptyTitle}>Webhook not found</Text>
        <TouchableOpacity onPress={() => router.back()} style={styles.linkBtn}>
          <Text style={styles.linkBtnTxt}>Go back</Text>
        </TouchableOpacity>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <Stack.Screen options={{ title: "Field Mapping", headerShown: true }} />

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          keyboardShouldPersistTaps="handled"
        >
          {/* ── Webhook context card ─────────────────────────────── */}
          <View style={styles.headerCard}>
            <Text style={styles.whName} numberOfLines={1}>
              {webhook.name || "Untitled"}
            </Text>
            <Text style={styles.whEvent}>
              Event: <Text style={{ fontWeight: "800" }}>{webhook.event_type}</Text>
            </Text>
            <View style={styles.urlBox}>
              <Text style={styles.urlText} numberOfLines={2} selectable>
                {webhook.url}
              </Text>
              <TouchableOpacity
                onPress={async () => {
                  await Clipboard.setStringAsync(webhook.url || "");
                  Alert.alert("Copied!");
                }}
                style={styles.copyBtn}
              >
                <PhIcon name="copy" size={14} color={colors.primary} />
                <Text style={styles.copyBtnTxt}>Copy</Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* Phase F3.2 (rev-2) — Order Status Update doesn't need
              the heavy mapping screen. We auto-detect order_id /
              status / timestamp from common key names at ingest time,
              so 99% of users can ignore this screen entirely. We
              still render it (in case the source uses a non-standard
              key like `event.payload.order.id`) but lead with a
              reassurance banner so the user doesn't feel lost. */}
          {webhook.event_type === "order_status_update" ? (
            <View style={styles.osuBanner}>
              <Text style={styles.osuBannerEmoji}>✨</Text>
              <View style={{ flex: 1 }}>
                <Text style={styles.osuBannerTitle}>
                  No mapping needed in most cases!
                </Text>
                <Text style={styles.osuBannerSub}>
                  We automatically look for{" "}
                  <Text style={styles.osuBannerCode}>order_id</Text>,{" "}
                  <Text style={styles.osuBannerCode}>status</Text>, and a
                  timestamp in the payload. Open this screen ONLY if your
                  source uses unusual key names.
                </Text>
              </View>
            </View>
          ) : null}

          {/* ── Sample source picker ─────────────────────────────── */}
          <View style={styles.actionRow}>
            <TouchableOpacity
              style={[styles.sampleBtn, pastingPreview && { opacity: 0.5 }]}
              disabled={pastingPreview}
              onPress={useLastReceived}
              activeOpacity={0.85}
            >
              <PhIcon name="cloud-download" size={14} color={colors.primary} />
              <Text style={styles.sampleBtnTxt}>Use last received</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={styles.sampleBtn}
              onPress={() => { setPasteText(""); setPasteOpen(true); }}
              activeOpacity={0.85}
            >
              <PhIcon name="edit" size={14} color={colors.primary} />
              <Text style={styles.sampleBtnTxt}>Paste sample JSON</Text>
            </TouchableOpacity>
          </View>

          {/* ── Mapping matrix ───────────────────────────────────── */}
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Field Mapping</Text>
            <Text style={styles.sectionSub}>
              {mappedCount} of {keys.length} mapped
            </Text>
          </View>

          {keys.length === 0 ? (
            <View style={styles.emptyBox}>
              <Text style={styles.emptyEmoji}>📡</Text>
              <Text style={styles.emptyTitle}>No sample loaded yet</Text>
              <Text style={styles.emptySub}>
                Click "Use last received" if you've already pinged this
                webhook, or paste a sample JSON to get started.
              </Text>
            </View>
          ) : (
            keys.map((k) => {
              const selected = selectedFieldFor(k);
              const sample   = sampleVals[k];
              const sampleStr = sample === null || sample === undefined
                ? ""
                : typeof sample === "object"
                  ? JSON.stringify(sample).slice(0, 60)
                  : String(sample).slice(0, 60);
              return (
                <View key={k} style={styles.row}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.rowKey} numberOfLines={1}>{k}</Text>
                    {sampleStr ? (
                      <Text style={styles.rowSample} numberOfLines={1}>
                        e.g. {sampleStr}
                      </Text>
                    ) : null}
                  </View>
                  <TouchableOpacity
                    style={[
                      styles.fieldBtn,
                      selected && styles.fieldBtnActive,
                    ]}
                    onPress={() => { setPickerKey(k); setPickerSearch(""); }}
                    activeOpacity={0.8}
                  >
                    <Text
                      style={[
                        styles.fieldBtnTxt,
                        selected && { color: colors.primary, fontWeight: "800" },
                      ]}
                      numberOfLines={1}
                    >
                      {selected ? selected.label : "Select…"}
                    </Text>
                    <PhIcon
                      name="chevron-down"
                      size={14}
                      color={selected ? colors.primary : "#94A3B8"}
                    />
                  </TouchableOpacity>
                </View>
              );
            })
          )}

          {/* Save button */}
          <TouchableOpacity
            testID="save-mapping"
            style={[styles.saveBtn, saving && { opacity: 0.5 }]}
            disabled={saving}
            onPress={save}
            activeOpacity={0.85}
          >
            {saving ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.saveBtnTxt}>Save mapping</Text>
            )}
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>

      {/* ── Field picker bottom-sheet ─────────────────────────────── */}
      <Modal
        visible={!!pickerKey}
        animationType="slide"
        transparent
        onRequestClose={() => setPickerKey(null)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                Map "{pickerKey}" to…
              </Text>
              <TouchableOpacity onPress={() => setPickerKey(null)}>
                <PhIcon name="close" size={20} color="#475569" />
              </TouchableOpacity>
            </View>
            <View style={styles.modalSearchWrap}>
              <PhIcon name="search" size={16} color="#94A3B8" />
              <TextInput
                value={pickerSearch}
                onChangeText={setPickerSearch}
                placeholder="Search fields"
                placeholderTextColor="#94A3B8"
                style={styles.modalSearch}
                autoCorrect={false}
              />
            </View>
            <FlatList
              data={[
                { key: NONE_VALUE, label: "— Don't import this key —", group: "" },
                ...filteredOptions,
              ]}
              keyExtractor={(item) => item.key}
              keyboardShouldPersistTaps="handled"
              ItemSeparatorComponent={() => <View style={styles.sep} />}
              renderItem={({ item, index }) => {
                const cur  = pickerKey ? mapping[pickerKey] : "";
                const isSel = (cur || NONE_VALUE) === item.key
                  || (!cur && item.key === NONE_VALUE);
                const showGroupHeader =
                  index > 0
                  && item.group
                  && (filteredOptions[index - 2]?.group !== item.group);
                return (
                  <>
                    {showGroupHeader ? (
                      <Text style={styles.groupHeader}>{item.group}</Text>
                    ) : null}
                    <TouchableOpacity
                      style={[styles.optionRow, isSel && styles.optionRowSel]}
                      onPress={() => pickField(pickerKey!, item.key)}
                      activeOpacity={0.7}
                    >
                      <Text style={styles.optionTxt}>{item.label}</Text>
                      {isSel ? (
                        <PhIcon name="checkmark" size={18} color={colors.primary} />
                      ) : null}
                    </TouchableOpacity>
                  </>
                );
              }}
            />
          </View>
        </View>
      </Modal>

      {/* ── Paste sample JSON modal ───────────────────────────────── */}
      <Modal
        visible={pasteOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPasteOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Paste sample JSON</Text>
              <TouchableOpacity onPress={() => setPasteOpen(false)}>
                <PhIcon name="close" size={20} color="#475569" />
              </TouchableOpacity>
            </View>
            <Text style={styles.hint}>
              Paste a single order JSON here (or an array). We'll detect
              the keys and pre-suggest a mapping.
            </Text>
            <TextInput
              value={pasteText}
              onChangeText={setPasteText}
              placeholder='{"shipping_name": "John", "phone": "9999999999", ...}'
              placeholderTextColor="#94A3B8"
              multiline
              style={styles.pasteArea}
              autoCorrect={false}
              autoCapitalize="none"
            />
            <TouchableOpacity
              style={[styles.saveBtn, pastingPreview && { opacity: 0.5 }]}
              disabled={pastingPreview || !pasteText.trim()}
              onPress={submitPaste}
              activeOpacity={0.85}
            >
              {pastingPreview ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.saveBtnTxt}>Detect keys</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: colors.background },
  center:  { alignItems: "center", justifyContent: "center" },
  scroll:  { padding: 16, paddingBottom: 30, gap: 12 },
  linkBtn: { marginTop: 12 },
  linkBtnTxt: { color: colors.primary, fontWeight: "800" },

  headerCard: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
    padding: 14, gap: 8,
  },
  whName: { fontSize: 17, fontWeight: "800", color: colors.text },
  whEvent: { fontSize: 12, color: colors.textMuted },
  urlBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 10,
    paddingHorizontal: 10, paddingVertical: 8,
  },
  urlText: { flex: 1, fontSize: 11, color: "#475569", fontFamily: "monospace" },
  copyBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#FFF7ED",
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
  },
  copyBtnTxt: { color: colors.primary, fontWeight: "800", fontSize: 12 },

  // Phase F3.2 (rev-2) — Order Status Update reassurance banner.
  // Tells the user that mapping is auto-detected so they don't waste
  // time configuring fields that are already handled.
  osuBanner: {
    flexDirection: "row",
    gap: 12,
    backgroundColor: "#ECFDF5",
    borderWidth: 1, borderColor: "#A7F3D0",
    borderRadius: 12,
    padding: 14,
  },
  osuBannerEmoji: { fontSize: 22 },
  osuBannerTitle: {
    fontSize: 14, fontWeight: "800", color: "#065F46", marginBottom: 4,
  },
  osuBannerSub: { fontSize: 12, color: "#065F46", lineHeight: 17 },
  osuBannerCode: {
    fontFamily: "monospace",
    fontWeight: "700",
    backgroundColor: "#D1FAE5",
    paddingHorizontal: 4,
    borderRadius: 4,
  },

  actionRow: { flexDirection: "row", gap: 8 },
  sampleBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6,
    backgroundColor: "#fff",
    borderWidth: 2, borderColor: colors.primary,
    paddingVertical: 11,
    borderRadius: 10,
  },
  sampleBtnTxt: { color: colors.primary, fontWeight: "800", fontSize: 13 },

  sectionHeader: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    marginTop: 6,
  },
  sectionTitle: { fontSize: 15, fontWeight: "800", color: colors.text },
  sectionSub:   { fontSize: 12, color: colors.textMuted, fontWeight: "600" },

  emptyBox: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 14,
    padding: 24,
    borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyEmoji: { fontSize: 36, marginBottom: 4 },
  emptyTitle: { fontSize: 15, fontWeight: "800", color: colors.text, marginBottom: 4 },
  emptySub:   { fontSize: 12, color: colors.textMuted, textAlign: "center", lineHeight: 18 },

  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: colors.surface,
    borderRadius: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 12, paddingVertical: 10,
  },
  rowKey:    { fontSize: 13, fontWeight: "700", color: colors.text, fontFamily: "monospace" },
  rowSample: { fontSize: 11, color: colors.textMuted, marginTop: 2 },
  fieldBtn: {
    minWidth: 130, maxWidth: 170,
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    paddingHorizontal: 10, paddingVertical: 8,
    borderRadius: 8,
    justifyContent: "space-between",
  },
  fieldBtnActive: { borderColor: colors.primary, backgroundColor: "#FFF7ED" },
  fieldBtnTxt: { fontSize: 12, color: "#475569", flex: 1 },

  saveBtn: {
    marginTop: 16, height: 50, borderRadius: 12,
    backgroundColor: colors.primary,
    alignItems: "center", justifyContent: "center",
  },
  saveBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 15 },

  modalBackdrop: { flex: 1, backgroundColor: "rgba(15,23,42,0.45)", justifyContent: "flex-end" },
  modalSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingTop: 14, paddingHorizontal: 16, paddingBottom: 22,
    maxHeight: "90%", minHeight: "55%",
  },
  modalHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", marginBottom: 12,
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  modalSearchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 10, paddingHorizontal: 12, height: 42, marginBottom: 8,
  },
  modalSearch: { flex: 1, fontSize: 14, color: colors.text, paddingVertical: 0 },
  groupHeader: {
    paddingHorizontal: 6, paddingTop: 12, paddingBottom: 4,
    fontSize: 11, fontWeight: "800", color: "#94A3B8", letterSpacing: 0.5,
  },
  optionRow: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 12, paddingHorizontal: 6,
  },
  optionRowSel: { backgroundColor: "#FFF7ED", borderRadius: 8 },
  optionTxt: { fontSize: 14, color: colors.text, flex: 1 },
  sep: { height: 1, backgroundColor: "#F1F5F9" },

  hint: { fontSize: 12, color: colors.textMuted, marginBottom: 8, lineHeight: 16 },
  pasteArea: {
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 10,
    padding: 12,
    minHeight: 180,
    fontSize: 13, fontFamily: "monospace",
    color: colors.text,
    textAlignVertical: "top",
  },
});
