/**
 * Super-Admin → WhatsApp Provider Manager (Phase-28)
 * --------------------------------------------------
 *
 * One screen drives every outbound WhatsApp message in the app:
 *   • Auth OTPs (login + signup)
 *   • Each of the 6 canonical shipment stages
 *     (Pending, Processing, Ready to Ship, Shipped, Delivered, Feedback)
 *
 * What the admin can configure here:
 *
 *   1) Provider connection
 *        provider (flowconnect / wati / custom)
 *        base_url, endpoint_template, api_token, enabled,
 *        default country code
 *
 *   2) Per-event triggers (8 cards)
 *        enabled toggle, automation_id (provider-side),
 *        template_preview (reference text, with a Copy button so the
 *        admin can paste it into FlowConnect's template editor),
 *        selected_fields (multi-pick from the AVAILABLE_FIELDS catalogue),
 *        custom_fields (name + value, unlimited), variable_mapping
 *        (rename App-side key → Provider-side variable name).
 *        Plus a "Send Test" button that fires a sample dispatch to a
 *        phone number entered inline.
 *
 * Auth gate: the entire screen is rendered only when `user.is_admin`
 * is true; the backend re-validates this on every call.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";

import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

// ─── Types ──────────────────────────────────────────────────────────
type ProviderConfig = {
  provider: string;
  base_url: string;
  endpoint_template: string;
  api_token: string;
  api_token_masked: string;
  enabled: boolean;
  default_country_code: string;
  updated_at?: string;
};

type CustomField = { name: string; value: string };

type EventRow = {
  event_key: string;
  label: string;
  sub: string;
  category: "auth" | "stage";
  enabled: boolean;
  automation_id: string;
  template_preview: string;
  selected_fields: string[];
  custom_fields: CustomField[];
  variable_mapping: Record<string, string>;
  updated_at?: string;
};

type AvailableField = { key: string; label: string };

const PROVIDERS = [
  { slug: "flowconnect", label: "FlowConnect" },
  { slug: "wati",        label: "WATI" },
  { slug: "interakt",    label: "Interakt" },
  { slug: "gupshup",     label: "Gupshup" },
  { slug: "custom",      label: "Custom" },
];

// ─── Main screen ────────────────────────────────────────────────────
export default function AdminWhatsAppProviderScreen() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [savingCfg, setSavingCfg]   = useState(false);
  const [error, setError]           = useState("");

  const [cfg, setCfg]               = useState<ProviderConfig | null>(null);
  const [events, setEvents]         = useState<EventRow[]>([]);
  const [available, setAvailable]   = useState<AvailableField[]>([]);

  // Editor modal state
  const [editing, setEditing]       = useState<EventRow | null>(null);
  const [savingEvent, setSavingEvent] = useState(false);

  // Test-send modal state
  const [testFor, setTestFor]       = useState<EventRow | null>(null);
  const [testPhone, setTestPhone]   = useState("");
  const [testSending, setTestSending] = useState(false);

  // ── Load all data ─────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      setError("");
      const [c, ev, af] = await Promise.all([
        Api.adminWppGetConfig(),
        Api.adminWppListEvents(),
        Api.adminWppAvailableFields(),
      ]);
      setCfg(c.config);
      setEvents(ev.items || []);
      setAvailable(af.fields || []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Could not load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.replace("/(auth)/login" as any); return; }
    if (!user.is_admin) {
      Alert.alert("Admin only", "This screen is restricted to administrators.");
      router.back();
      return;
    }
    load();
  }, [authLoading, user, load, router]);

  // ── Provider config save ──────────────────────────────────────────
  const saveCfg = async (patch: Partial<ProviderConfig>) => {
    if (!cfg) return;
    setSavingCfg(true);
    try {
      const r = await Api.adminWppUpdateConfig(patch as any);
      setCfg(r.config);
    } catch (e: any) {
      Alert.alert(
        "Could not save",
        e?.response?.data?.detail || e?.message || "Save failed",
      );
    } finally {
      setSavingCfg(false);
    }
  };

  // ── Quick toggle for an event row ─────────────────────────────────
  const toggleEvent = async (row: EventRow) => {
    try {
      const r = await Api.adminWppUpdateEvent(row.event_key, {
        enabled: !row.enabled,
      });
      setEvents((prev) =>
        prev.map((e) => (e.event_key === row.event_key ? r.item : e)),
      );
    } catch (e: any) {
      Alert.alert(
        "Could not toggle",
        e?.response?.data?.detail || e?.message || "Toggle failed",
      );
    }
  };

  // ── Save event editor ─────────────────────────────────────────────
  const saveEvent = async (draft: EventRow) => {
    setSavingEvent(true);
    try {
      const r = await Api.adminWppUpdateEvent(draft.event_key, {
        automation_id:    draft.automation_id.trim(),
        template_preview: draft.template_preview,
        selected_fields:  draft.selected_fields,
        custom_fields:    draft.custom_fields.filter((f) => f.name.trim()),
        variable_mapping: draft.variable_mapping,
      });
      setEvents((prev) =>
        prev.map((e) => (e.event_key === draft.event_key ? r.item : e)),
      );
      setEditing(null);
    } catch (e: any) {
      Alert.alert(
        "Could not save",
        e?.response?.data?.detail || e?.message || "Save failed",
      );
    } finally {
      setSavingEvent(false);
    }
  };

  // ── Run a test send ───────────────────────────────────────────────
  const runTest = async () => {
    if (!testFor) return;
    const phoneTrim = testPhone.trim();
    if (phoneTrim.length < 7) {
      Alert.alert("Phone required", "Please enter a valid phone number.");
      return;
    }
    setTestSending(true);
    try {
      const r = await Api.adminWppTestSend({
        event_key: testFor.event_key,
        phone:     phoneTrim,
      });
      const res = r.result;
      Alert.alert(
        r.ok ? "Test sent ✅" : "Test failed",
        [
          `Event: ${res.event_key}`,
          res.skipped ? "Skipped" : `Status: ${res.status_code ?? "—"}`,
          res.reason ? `Reason: ${res.reason}` : "",
          res.duration_ms ? `Took: ${res.duration_ms} ms` : "",
        ].filter(Boolean).join("\n"),
      );
    } catch (e: any) {
      Alert.alert(
        "Test failed",
        e?.response?.data?.detail || e?.message || "Send failed",
      );
    } finally {
      setTestSending(false);
    }
  };

  // ── Loading / error states ────────────────────────────────────────
  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.muted}>Loading WhatsApp Provider…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />

      {/* Custom header */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <PhIcon name="arrow-back" size={22} color="#0F172A" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>WhatsApp Provider</Text>
          <Text style={styles.headerSub}>
            Global config & per-event automation
          </Text>
        </View>
        <View
          style={[
            styles.statusDot,
            { backgroundColor: cfg?.enabled ? "#10B981" : "#DC2626" },
          ]}
        />
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 60 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={colors.primary}
          />
        }
      >
        {error ? (
          <View style={styles.errorBox}>
            <PhIcon name="alert-circle-outline" size={18} color="#DC2626" />
            <Text style={styles.errorTxt}>{error}</Text>
          </View>
        ) : null}

        {/* Section: Provider connection */}
        {cfg && (
          <ProviderConfigCard
            cfg={cfg}
            onSave={saveCfg}
            saving={savingCfg}
          />
        )}

        {/* Section heading: events */}
        <View style={styles.sectionHead}>
          <Text style={styles.sectionTitle}>Event Triggers</Text>
          <Text style={styles.sectionSub}>
            Tap any card to configure automation & fields
          </Text>
        </View>

        {/* Group: Auth events */}
        <Text style={styles.groupHead}>🔐 Authentication</Text>
        {events
          .filter((e) => e.category === "auth")
          .map((e) => (
            <EventCard
              key={e.event_key}
              row={e}
              onToggle={() => toggleEvent(e)}
              onEdit={() => setEditing(e)}
              onTest={() => { setTestFor(e); setTestPhone(""); }}
            />
          ))}

        {/* Group: Stage events */}
        <Text style={styles.groupHead}>📦 Shipment Stages</Text>
        {events
          .filter((e) => e.category === "stage")
          .map((e) => (
            <EventCard
              key={e.event_key}
              row={e}
              onToggle={() => toggleEvent(e)}
              onEdit={() => setEditing(e)}
              onTest={() => { setTestFor(e); setTestPhone(""); }}
            />
          ))}

        <Text style={styles.footnote}>
          Templates live inside the provider's own automation. From here
          we only push the DATA your template needs (variables). Use the
          📋 Copy button to lift a reference template into FlowConnect.
        </Text>
      </ScrollView>

      {/* Event editor modal */}
      {editing && (
        <EventEditorModal
          event={editing}
          available={available}
          onClose={() => setEditing(null)}
          onSave={saveEvent}
          saving={savingEvent}
        />
      )}

      {/* Test send modal */}
      <Modal visible={!!testFor} transparent animationType="fade" onRequestClose={() => setTestFor(null)}>
        <Pressable style={styles.modalBackdrop} onPress={() => !testSending && setTestFor(null)}>
          <Pressable style={styles.testCard} onPress={() => {}}>
            <Text style={styles.testTitle}>🧪 Test Send</Text>
            <Text style={styles.testSub}>{testFor?.label}</Text>
            <Text style={styles.fieldLabel}>Phone number (with or without +91)</Text>
            <TextInput
              value={testPhone}
              onChangeText={setTestPhone}
              placeholder="e.g. 9876543210"
              keyboardType="phone-pad"
              style={styles.input}
              autoFocus
            />
            <View style={{ flexDirection: "row", gap: 10, marginTop: 16 }}>
              <TouchableOpacity
                style={styles.cancelBtn}
                onPress={() => !testSending && setTestFor(null)}
                disabled={testSending}
              >
                <Text style={styles.cancelTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, testSending && { opacity: 0.6 }]}
                onPress={runTest}
                disabled={testSending}
              >
                {testSending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.saveTxt}>Send Test</Text>
                )}
              </TouchableOpacity>
            </View>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}


// ─── Provider Connection card ───────────────────────────────────────
function ProviderConfigCard({
  cfg,
  onSave,
  saving,
}: {
  cfg: ProviderConfig;
  onSave: (patch: Partial<ProviderConfig>) => void;
  saving: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [draft, setDraft] = useState<ProviderConfig>(cfg);

  useEffect(() => { setDraft(cfg); }, [cfg]);

  const dirty =
    draft.provider !== cfg.provider ||
    draft.base_url !== cfg.base_url ||
    draft.endpoint_template !== cfg.endpoint_template ||
    (draft.api_token && draft.api_token !== cfg.api_token) ||
    draft.default_country_code !== cfg.default_country_code;

  return (
    <View style={styles.card}>
      <TouchableOpacity
        style={styles.cardHeader}
        onPress={() => setExpanded((x) => !x)}
        activeOpacity={0.7}
      >
        <View style={[styles.iconBubble, { backgroundColor: "#DBEAFE" }]}>
          <PhIcon name="settings-outline" size={20} color="#1E40AF" />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>Provider Connection</Text>
          <Text style={styles.cardSub}>
            {cfg.provider} · {cfg.api_token_masked || "API token not set"}
          </Text>
        </View>
        <Switch
          value={cfg.enabled}
          onValueChange={(v) => onSave({ enabled: v })}
          thumbColor={cfg.enabled ? colors.primary : "#94A3B8"}
        />
        <PhIcon
          name={expanded ? "chevron-up" : "chevron-down"}
          size={20}
          color="#475569"
        />
      </TouchableOpacity>

      {expanded && (
        <View style={styles.cardBody}>
          {/* Provider slug picker */}
          <Text style={styles.fieldLabel}>Provider</Text>
          <View style={styles.chipRow}>
            {PROVIDERS.map((p) => {
              const active = draft.provider === p.slug;
              return (
                <TouchableOpacity
                  key={p.slug}
                  onPress={() => setDraft({ ...draft, provider: p.slug })}
                  style={[styles.chip, active && styles.chipActive]}
                >
                  <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>
                    {p.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>

          <Text style={styles.fieldLabel}>Base URL</Text>
          <TextInput
            value={draft.base_url}
            onChangeText={(v) => setDraft({ ...draft, base_url: v })}
            placeholder="https://login.flowconnect.ai/api/automations"
            autoCapitalize="none"
            style={styles.input}
          />

          <Text style={styles.fieldLabel}>Endpoint Template</Text>
          <TextInput
            value={draft.endpoint_template}
            onChangeText={(v) => setDraft({ ...draft, endpoint_template: v })}
            placeholder="{base_url}/{automation_id}/execute"
            autoCapitalize="none"
            style={styles.input}
          />
          <Text style={styles.hint}>
            Use placeholders <Text style={styles.code}>{"{base_url}"}</Text> and{" "}
            <Text style={styles.code}>{"{automation_id}"}</Text>.
          </Text>

          <Text style={styles.fieldLabel}>API Token</Text>
          <TextInput
            value={draft.api_token}
            onChangeText={(v) => setDraft({ ...draft, api_token: v })}
            placeholder={cfg.api_token_masked || "Paste your provider API token"}
            autoCapitalize="none"
            secureTextEntry={false}
            style={styles.input}
          />

          <Text style={styles.fieldLabel}>Default Country Code</Text>
          <TextInput
            value={draft.default_country_code}
            onChangeText={(v) =>
              setDraft({ ...draft, default_country_code: v.replace(/\D/g, "") })
            }
            placeholder="91"
            keyboardType="number-pad"
            style={[styles.input, { maxWidth: 100 }]}
          />

          <TouchableOpacity
            style={[
              styles.saveBtn,
              { marginTop: 14 },
              (!dirty || saving) && { opacity: 0.5 },
            ]}
            onPress={() => onSave({
              provider:             draft.provider,
              base_url:             draft.base_url.trim(),
              endpoint_template:    draft.endpoint_template.trim(),
              api_token:            draft.api_token.trim(),
              default_country_code: draft.default_country_code.trim() || "91",
            })}
            disabled={!dirty || saving}
          >
            {saving ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.saveTxt}>Save connection</Text>
            )}
          </TouchableOpacity>
        </View>
      )}
    </View>
  );
}


// ─── One Event card ─────────────────────────────────────────────────
function EventCard({
  row,
  onToggle,
  onEdit,
  onTest,
}: {
  row: EventRow;
  onToggle: () => void;
  onEdit: () => void;
  onTest: () => void;
}) {
  const configured = !!row.automation_id;
  return (
    <View style={[styles.card, { paddingBottom: 0 }]}>
      <View style={styles.cardHeader}>
        <View
          style={[
            styles.iconBubble,
            {
              backgroundColor:
                row.category === "auth" ? "#FEF3C7" : "#DCFCE7",
            },
          ]}
        >
          <PhIcon
            name={row.category === "auth" ? "shield-checkmark-outline" : "cube-outline"}
            size={20}
            color={row.category === "auth" ? "#92400E" : "#15803D"}
          />
        </View>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{row.label}</Text>
          <Text style={styles.cardSub}>{row.sub}</Text>
        </View>
        <Switch
          value={row.enabled}
          onValueChange={onToggle}
          thumbColor={row.enabled ? colors.primary : "#94A3B8"}
        />
      </View>

      <View style={styles.cardMetaRow}>
        <View style={[styles.metaPill, configured ? styles.metaPillOk : styles.metaPillWarn]}>
          <Text style={[
            styles.metaPillTxt,
            { color: configured ? "#15803D" : "#92400E" },
          ]}>
            {configured ? `ID: ${row.automation_id}` : "Automation ID not set"}
          </Text>
        </View>
        <Text style={styles.fieldsCount}>
          {row.selected_fields.length} field
          {row.selected_fields.length === 1 ? "" : "s"}
        </Text>
      </View>

      <View style={styles.cardActions}>
        <TouchableOpacity style={styles.actionBtn} onPress={onEdit}>
          <PhIcon name="create-outline" size={16} color="#1E40AF" />
          <Text style={styles.actionTxt}>Configure</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.actionBtn, !configured && { opacity: 0.45 }]}
          onPress={onTest}
          disabled={!configured}
        >
          <PhIcon name="paper-plane-outline" size={16} color="#1E40AF" />
          <Text style={styles.actionTxt}>Test Send</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}


// ─── Event editor modal ─────────────────────────────────────────────
function EventEditorModal({
  event,
  available,
  onClose,
  onSave,
  saving,
}: {
  event: EventRow;
  available: AvailableField[];
  onClose: () => void;
  onSave: (draft: EventRow) => void;
  saving: boolean;
}) {
  const [draft, setDraft] = useState<EventRow>(event);

  const toggleField = (key: string) => {
    setDraft((d) => ({
      ...d,
      selected_fields: d.selected_fields.includes(key)
        ? d.selected_fields.filter((f) => f !== key)
        : [...d.selected_fields, key],
    }));
  };

  const addCustomField = () => {
    setDraft((d) => ({
      ...d,
      custom_fields: [...d.custom_fields, { name: "", value: "" }],
    }));
  };

  const updateCustom = (idx: number, patch: Partial<CustomField>) => {
    setDraft((d) => ({
      ...d,
      custom_fields: d.custom_fields.map((c, i) =>
        i === idx ? { ...c, ...patch } : c,
      ),
    }));
  };

  const removeCustom = (idx: number) => {
    setDraft((d) => ({
      ...d,
      custom_fields: d.custom_fields.filter((_, i) => i !== idx),
    }));
  };

  const setMapping = (fieldKey: string, target: string) => {
    setDraft((d) => {
      const next = { ...d.variable_mapping };
      if (target.trim()) next[fieldKey] = target.trim();
      else delete next[fieldKey];
      return { ...d, variable_mapping: next };
    });
  };

  const copyTemplate = async () => {
    try {
      await Clipboard.setStringAsync(draft.template_preview || "");
      Alert.alert("Copied ✓", "Template text copied. Paste it inside your FlowConnect automation.");
    } catch {
      Alert.alert("Copy failed", "Could not access clipboard.");
    }
  };

  return (
    <Modal visible animationType="slide" onRequestClose={onClose}>
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <KeyboardAvoidingView
          style={{ flex: 1 }}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <View style={styles.modalHead}>
            <TouchableOpacity onPress={onClose} style={styles.backBtn}>
              <PhIcon name="close" size={22} color="#0F172A" />
            </TouchableOpacity>
            <View style={{ flex: 1 }}>
              <Text style={styles.headerTitle}>{event.label}</Text>
              <Text style={styles.headerSub}>{event.sub}</Text>
            </View>
          </View>

          <ScrollView
            style={{ flex: 1 }}
            contentContainerStyle={{ padding: 16, paddingBottom: 80 }}
            keyboardShouldPersistTaps="handled"
          >
            {/* Automation ID */}
            <Text style={styles.fieldLabel}>Automation ID *</Text>
            <Text style={styles.hint}>
              Get this from your provider's dashboard. The full endpoint
              becomes: <Text style={styles.code}>{"{base_url}/{automation_id}/execute"}</Text>
            </Text>
            <TextInput
              value={draft.automation_id}
              onChangeText={(v) => setDraft({ ...draft, automation_id: v })}
              placeholder="e.g. 69ff6d211a1dc"
              autoCapitalize="none"
              style={styles.input}
            />

            {/* Template preview + copy */}
            <View style={[styles.rowBetween, { marginTop: 16 }]}>
              <Text style={styles.fieldLabel}>Template Preview</Text>
              <TouchableOpacity style={styles.copyBtn} onPress={copyTemplate}>
                <PhIcon name="copy-outline" size={14} color="#1E40AF" />
                <Text style={styles.copyTxt}>Copy</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.hint}>
              Reference text — paste this into your provider's template
              editor. Use placeholders like <Text style={styles.code}>{"{customer_name}"}</Text>.
            </Text>
            <TextInput
              value={draft.template_preview}
              onChangeText={(v) => setDraft({ ...draft, template_preview: v })}
              placeholder="Hello {customer_name}, your order…"
              multiline
              numberOfLines={6}
              style={[styles.input, styles.inputArea]}
            />

            {/* Selected fields */}
            <Text style={[styles.fieldLabel, { marginTop: 16 }]}>
              Fields to Send ({draft.selected_fields.length})
            </Text>
            <Text style={styles.hint}>
              Tick which app fields should be pushed to the provider. The
              outgoing parameter name = field key (unless renamed below).
            </Text>
            <View style={styles.fieldsBox}>
              {available.map((f) => {
                const on = draft.selected_fields.includes(f.key);
                return (
                  <TouchableOpacity
                    key={f.key}
                    style={[styles.fieldChip, on && styles.fieldChipOn]}
                    onPress={() => toggleField(f.key)}
                  >
                    {on && (
                      <PhIcon name="checkmark" size={14} color="#fff" />
                    )}
                    <Text style={[styles.fieldChipTxt, on && { color: "#fff" }]}>
                      {f.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            {/* Variable mapping (only for ticked fields) */}
            {draft.selected_fields.length > 0 && (
              <>
                <Text style={[styles.fieldLabel, { marginTop: 18 }]}>
                  Variable Mapping (optional)
                </Text>
                <Text style={styles.hint}>
                  Rename a field's outgoing parameter (e.g. <Text style={styles.code}>customer_name → name</Text>).
                  Leave blank to keep the original key.
                </Text>
                {draft.selected_fields.map((fk) => (
                  <View key={fk} style={styles.mapRow}>
                    <Text style={styles.mapLeft}>{fk}</Text>
                    <PhIcon name="arrow-forward" size={14} color="#94A3B8" />
                    <TextInput
                      value={draft.variable_mapping[fk] || ""}
                      onChangeText={(v) => setMapping(fk, v)}
                      placeholder={fk}
                      autoCapitalize="none"
                      style={[styles.input, { flex: 1, marginVertical: 0 }]}
                    />
                  </View>
                ))}
              </>
            )}

            {/* Custom fields */}
            <View style={[styles.rowBetween, { marginTop: 18 }]}>
              <Text style={styles.fieldLabel}>
                Custom Fields ({draft.custom_fields.length})
              </Text>
              <TouchableOpacity style={styles.addBtn} onPress={addCustomField}>
                <PhIcon name="add" size={14} color="#fff" />
                <Text style={styles.addTxt}>Add</Text>
              </TouchableOpacity>
            </View>
            <Text style={styles.hint}>
              Hardcoded extras (e.g. <Text style={styles.code}>template_lang = en</Text>).
              Pushed exactly as-is on every dispatch.
            </Text>
            {draft.custom_fields.map((cf, idx) => (
              <View key={idx} style={styles.customRow}>
                <TextInput
                  value={cf.name}
                  onChangeText={(v) => updateCustom(idx, { name: v })}
                  placeholder="name"
                  autoCapitalize="none"
                  style={[styles.input, { flex: 1, marginVertical: 0 }]}
                />
                <TextInput
                  value={cf.value}
                  onChangeText={(v) => updateCustom(idx, { value: v })}
                  placeholder="value"
                  autoCapitalize="none"
                  style={[styles.input, { flex: 1.4, marginVertical: 0 }]}
                />
                <TouchableOpacity onPress={() => removeCustom(idx)} style={styles.removeBtn}>
                  <PhIcon name="trash-outline" size={16} color="#DC2626" />
                </TouchableOpacity>
              </View>
            ))}
          </ScrollView>

          <View style={styles.modalActions}>
            <TouchableOpacity
              style={styles.cancelBtn}
              onPress={onClose}
              disabled={saving}
            >
              <Text style={styles.cancelTxt}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.saveBtn, saving && { opacity: 0.6 }]}
              onPress={() => onSave(draft)}
              disabled={saving}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.saveTxt}>Save changes</Text>
              )}
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}


// ─── Styles ─────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F4F5F7" },

  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 10 },
  muted:  { color: "#64748B", fontSize: 13 },

  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 12,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    gap: 8,
  },
  backBtn: { padding: 6 },
  headerTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  headerSub:   { fontSize: 12, color: "#64748B", marginTop: 2 },
  statusDot: { width: 10, height: 10, borderRadius: 5, marginRight: 6 },

  errorBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#FEE2E2",
    paddingHorizontal: 12,
    paddingVertical: 10,
    margin: 12,
    borderRadius: 8,
  },
  errorTxt: { color: "#991B1B", flex: 1 },

  card: {
    backgroundColor: "#fff",
    marginHorizontal: 12,
    marginTop: 12,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    paddingTop: 4,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 12,
  },
  iconBubble: {
    width: 38, height: 38, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: "#0F172A" },
  cardSub:   { fontSize: 12, color: "#64748B", marginTop: 2 },
  cardBody:  { paddingHorizontal: 14, paddingBottom: 16 },

  cardMetaRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    gap: 10,
  },
  metaPill: {
    paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 999,
  },
  metaPillOk:   { backgroundColor: "#DCFCE7" },
  metaPillWarn: { backgroundColor: "#FEF3C7" },
  metaPillTxt:  { fontSize: 11.5, fontWeight: "700" },
  fieldsCount:  { fontSize: 11.5, color: "#64748B", marginLeft: "auto" },

  cardActions: {
    flexDirection: "row",
    borderTopWidth: 1,
    borderTopColor: "#F1F5F9",
    marginTop: 10,
  },
  actionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 12,
    gap: 6,
    borderRightWidth: 1,
    borderRightColor: "#F1F5F9",
  },
  actionTxt: { fontWeight: "700", color: "#1E40AF", fontSize: 13 },

  sectionHead: {
    paddingHorizontal: 18,
    paddingTop: 24,
    paddingBottom: 4,
  },
  sectionTitle: { fontSize: 16, fontWeight: "800", color: "#0F172A" },
  sectionSub:   { fontSize: 12, color: "#64748B", marginTop: 2 },

  groupHead: {
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 2,
    fontSize: 13,
    fontWeight: "700",
    color: "#475569",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },

  footnote: {
    paddingHorizontal: 18,
    paddingTop: 20,
    fontSize: 11.5,
    color: "#64748B",
    lineHeight: 18,
  },

  fieldLabel: {
    fontSize: 13,
    fontWeight: "700",
    color: "#0F172A",
    marginTop: 12,
    marginBottom: 6,
  },
  hint: { fontSize: 11.5, color: "#64748B", marginBottom: 6, lineHeight: 16 },
  code: {
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
    color: "#1E40AF",
    fontSize: 11,
  },
  input: {
    borderWidth: 1,
    borderColor: "#CBD5E1",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "ios" ? 10 : 8,
    backgroundColor: "#fff",
    color: "#0F172A",
    fontSize: 14,
    marginVertical: 2,
  },
  inputArea: { minHeight: 100, textAlignVertical: "top" },

  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginBottom: 6 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1, borderColor: "#CBD5E1",
    backgroundColor: "#fff",
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipTxt: { fontSize: 12.5, fontWeight: "600", color: "#475569" },
  chipTxtActive: { color: "#fff" },

  rowBetween: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },

  copyBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    backgroundColor: "#DBEAFE",
  },
  copyTxt: { fontSize: 11.5, fontWeight: "700", color: "#1E40AF" },

  fieldsBox: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  fieldChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: "#CBD5E1",
    backgroundColor: "#fff",
  },
  fieldChipOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  fieldChipTxt: { fontSize: 12, color: "#475569", fontWeight: "600" },

  mapRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 6,
  },
  mapLeft: {
    fontSize: 12.5,
    fontWeight: "700",
    color: "#1E40AF",
    minWidth: 110,
  },

  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 6,
    backgroundColor: colors.primary,
  },
  addTxt: { color: "#fff", fontWeight: "700", fontSize: 12 },

  customRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
  },
  removeBtn: { padding: 8 },

  modalHead: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 12,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    gap: 8,
  },
  modalActions: {
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: 14,
    paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 14,
    backgroundColor: "#fff",
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  cancelBtn: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 14,
    borderRadius: 8,
    backgroundColor: "#F1F5F9",
  },
  cancelTxt: { fontWeight: "700", color: "#475569" },
  saveBtn: {
    flex: 1.4,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 14,
    borderRadius: 8,
    backgroundColor: colors.primary,
  },
  saveTxt: { fontWeight: "800", color: "#fff" },

  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    alignItems: "center",
    justifyContent: "center",
    padding: 18,
  },
  testCard: {
    width: "100%",
    maxWidth: 420,
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 18,
  },
  testTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  testSub:   { fontSize: 12, color: "#64748B", marginTop: 2, marginBottom: 10 },
});
