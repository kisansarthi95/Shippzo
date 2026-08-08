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
  category: "auth" | "stage" | "recovery" | "custom";
  enabled: boolean;
  automation_id: string;
  // Phase F8.4 — per-event webhook URL. Stage events route to THIS
  // URL only, keeping OTP (auth) traffic on the global base_url.
  webhook_url: string;
  template_preview: string;
  // Phase F4.9 — persisted boolean so the Enable-Template switch state
  // survives reload (previously derived from template_preview length).
  template_enabled: boolean;
  selected_fields: string[];
  custom_fields: CustomField[];
  variable_mapping: Record<string, string>;
  updated_at?: string;
};

type AvailableField = { key: string; label: string };

// Provider chip list retired (Jul-2026) — the WhatsApp Provider Name
// is now a free-text field.  Kept the constant deliberately absent
// so a lingering import/reference triggers a compile-time nudge.


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
  // Phase F8.6 — Test Send popup enrichment (STAGE events only).
  // `testMode` toggles between hand-keying every variable ("manual")
  // and pulling the latest matching shipment via GET
  // /admin/whatsapp-provider/latest-shipment-context ("auto"). Auth /
  // OTP events keep their existing single-form UI — they don't need
  // per-variable inputs because the backend auto-generates every
  // field (OTP + defaults). The autoLoading + autoNote state powers
  // the loading / empty-result UX inside the Auto Fetch tab.
  const [testMode, setTestMode]     = useState<"manual" | "auto">("manual");
  const [testCtx, setTestCtx]       = useState<Record<string, string>>({});
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoNote, setAutoNote]     = useState<string>("");
  // Phase F8.9 — Custom Automations UI state.
  const [showAddCustom, setShowAddCustom] = useState(false);
  const [newCustomLabel, setNewCustomLabel] = useState("");
  const [creatingCustom, setCreatingCustom] = useState(false);
  const [testSending, setTestSending] = useState(false);
  // Phase F5.8 — inline "Live Response Viewer" state. When null, the
  // modal only shows the phone input. After a Send, this holds the
  // generated OTP + full request/response snapshot so the operator
  // can debug silent-drop failures (BSPs returning 200 but never
  // delivering the WhatsApp).
  const [testResult, setTestResult] = useState<{
    ok:              boolean;
    generated_otp:   string;
    event_key:       string;
    endpoint?:       string;
    request_payload?: Record<string, any>;
    response_body?:  any;
    status_code?:    number | null;
    duration_ms?:    number;
    reason?:         string | null;
    skipped?:        boolean;
  } | null>(null);

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
        // Phase F8.4 — per-event webhook URL for stage events.
        webhook_url:      (draft.webhook_url || "").trim(),
        template_preview: draft.template_preview,
        // Phase F4.9 — persist Enable-Template toggle.
        template_enabled: !!draft.template_enabled,
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

  // ── Phase F8.9 — Custom Automations create + delete ───────────────
  const createCustomEvent = async () => {
    const label = newCustomLabel.trim();
    if (label.length < 2) {
      Alert.alert("Name required", "Enter a name for this automation.");
      return;
    }
    setCreatingCustom(true);
    try {
      const r = await Api.adminWppCreateCustomEvent({ label });
      setEvents((prev) => [...prev, r.item]);
      setShowAddCustom(false);
      setNewCustomLabel("");
      // Auto-open the editor so the operator can paste the Webhook URL.
      setEditing(r.item);
    } catch (e: any) {
      Alert.alert(
        "Could not create",
        e?.response?.data?.detail || e?.message || "Create failed",
      );
    } finally {
      setCreatingCustom(false);
    }
  };

  const confirmDeleteCustom = (row: EventRow) => {
    Alert.alert(
      "Delete this automation?",
      `“${row.label}” will be removed permanently.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.adminWppDeleteEvent(row.event_key);
              setEvents((prev) =>
                prev.filter((e) => e.event_key !== row.event_key),
              );
            } catch (e: any) {
              Alert.alert(
                "Could not delete",
                e?.response?.data?.detail || e?.message || "Delete failed",
              );
            }
          },
        },
      ],
    );
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
    setTestResult(null);
    try {
      // Phase F8.6 — stage events pass through the operator's typed /
      // auto-fetched variable map. Auth events keep the legacy behavior
      // (empty sample_context, backend auto-generates OTP + defaults).
      const isStage = testFor.category !== "auth";
      const sample: Record<string, string> = {};
      if (isStage) {
        Object.entries(testCtx).forEach(([k, v]) => {
          const s = String(v ?? "").trim();
          if (s) sample[k] = s;
        });
      }
      const r: any = await Api.adminWppTestSend({
        event_key:      testFor.event_key,
        phone:          phoneTrim,
        sample_context: isStage ? sample : undefined,
      });
      const res = r.result || {};
      // Phase F5.8 — hold the full snapshot in state so the modal
      // renders an inline Live Response Viewer instead of firing a
      // one-shot Alert. The operator can see EXACTLY what was sent
      // (endpoint + payload + response) and copy the generated OTP.
      setTestResult({
        ok:               !!r.ok,
        generated_otp:    r.generated_otp || "",
        event_key:        res.event_key || testFor.event_key,
        endpoint:         res.endpoint,
        request_payload:  res.request_payload,
        response_body:    res.response_body,
        status_code:      res.status_code,
        duration_ms:      res.duration_ms,
        reason:           res.reason,
        skipped:          res.skipped,
      });
    } catch (e: any) {
      setTestResult({
        ok:            false,
        generated_otp: "",
        event_key:     testFor.event_key,
        reason:        e?.response?.data?.detail || e?.message || "Send failed",
      });
    } finally {
      setTestSending(false);
    }
  };

  // ── Auto-fetch the latest matching shipment's context ──────────────
  // Phase F8.6 — pulls real production-shaped data (customer_name,
  // order_id, tracking_id, courier_name, business_name, address, etc.)
  // from the most-recent shipment currently in this stage's status.
  // On success we merge the returned map into testCtx so the manual
  // input row for each variable pre-fills. On empty result we surface
  // the reason inline so the operator knows to switch to Manual mode.
  const runAutoFetch = async () => {
    if (!testFor || testFor.category !== "stage") return;
    setAutoLoading(true);
    setAutoNote("");
    try {
      const r = await Api.adminWppLatestShipmentContext(testFor.event_key);
      if (!r.ok) {
        setAutoNote(r.reason || "No matching shipment found.");
        return;
      }
      setTestCtx({ ...(testCtx || {}), ...(r.context || {}) });
      // Also seed the phone if we don't already have one, so a single
      // tap of Send Test works out of the box.
      const cp = String(r.context?.customer_phone || "").trim();
      if (cp && !testPhone.trim()) setTestPhone(cp);
      setAutoNote(
        `✅ Loaded from shipment ${(r.shipment_id || "").slice(0, 8)}… ` +
        `(status: ${r.status || "—"}). All fields are editable below.`,
      );
    } catch (e: any) {
      setAutoNote(e?.response?.data?.detail || e?.message || "Fetch failed.");
    } finally {
      setAutoLoading(false);
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

  // Phase F5.7 — provider is "ready" (Test Send enabled, event cards
  // flip from yellow to green) once Base URL + API Token are set at
  // the top card. Per-event automation_id override is optional and
  // no longer gates readiness.
  const providerReady = !!(
    cfg &&
    (cfg.base_url || "").trim().length > 0 &&
    ((cfg.api_token || "").trim().length > 0 || (cfg.api_token_masked || "").trim().length > 0)
  );

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
              providerReady={providerReady}
              onToggle={() => toggleEvent(e)}
              onEdit={() => setEditing(e)}
              onTest={() => {
                setTestFor(e);
                setTestPhone("");
                setTestCtx({});
                setAutoNote("");
                setTestMode("manual");
              }}
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
              providerReady={providerReady}
              onToggle={() => toggleEvent(e)}
              onEdit={() => setEditing(e)}
              onTest={() => {
                setTestFor(e);
                setTestPhone("");
                setTestCtx({});
                setAutoNote("");
                setTestMode("manual");
              }}
            />
          ))}

        {/* Phase F8.9 — Group: Recovery events (abandoned_cart_recovery
            and any future recovery triggers). */}
        {events.some((e) => e.category === "recovery") && (
          <>
            <Text style={styles.groupHead}>🛒 Recovery</Text>
            {events
              .filter((e) => e.category === "recovery")
              .map((e) => (
                <EventCard
                  key={e.event_key}
                  row={e}
                  providerReady={providerReady}
                  onToggle={() => toggleEvent(e)}
                  onEdit={() => setEditing(e)}
                  onTest={() => {
                    setTestFor(e);
                    setTestPhone("");
                    setTestCtx({});
                    setAutoNote("");
                    setTestMode("manual");
                  }}
                />
              ))}
          </>
        )}

        {/* Phase F8.9 — Group: Custom Automations. Operator-created
            triggers using the same variable set as stages so no new
            mapping is required. `+ Add` opens a small inline prompt
            asking for the label; a trash icon on each card deletes. */}
        <View style={styles.customHeaderRow}>
          <Text style={[styles.groupHead, { marginBottom: 0 }]}>
            ⚙️ Custom Automations
          </Text>
          <TouchableOpacity
            style={styles.customAddBtn}
            onPress={() => setShowAddCustom(true)}
          >
            <PhIcon name="add" size={16} color="#1E40AF" />
            <Text style={styles.customAddTxt}>Add</Text>
          </TouchableOpacity>
        </View>
        {events.filter((e) => e.category === "custom").length === 0 ? (
          <Text style={styles.customEmptyTxt}>
            No custom automations yet. Tap “Add” to create one — it uses
            the same variables as your Stage triggers, so you only need
            to paste a Webhook URL after creating.
          </Text>
        ) : (
          events
            .filter((e) => e.category === "custom")
            .map((e) => (
              <EventCard
                key={e.event_key}
                row={e}
                providerReady={providerReady}
                onToggle={() => toggleEvent(e)}
                onEdit={() => setEditing(e)}
                onTest={() => {
                  setTestFor(e);
                  setTestPhone("");
                  setTestCtx({});
                  setAutoNote("");
                  setTestMode("manual");
                }}
                onDelete={() => confirmDeleteCustom(e)}
              />
            ))
        )}

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
          allEvents={events}
          onClose={() => setEditing(null)}
          onSave={saveEvent}
          saving={savingEvent}
        />
      )}

      {/* Test send modal — Phase F5.8 with inline Live Response Viewer */}
      <Modal
        visible={!!testFor}
        transparent
        animationType="fade"
        onRequestClose={() => {
          if (!testSending) { setTestFor(null); setTestResult(null); }
        }}
      >
        <Pressable
          style={styles.modalBackdrop}
          onPress={() => { if (!testSending) { setTestFor(null); setTestResult(null); } }}
        >
          <Pressable style={styles.testCard} onPress={() => {}}>
            <Text style={styles.testTitle}>🧪 Test Send</Text>
            <Text style={styles.testSub}>{testFor?.label}</Text>

            {/* Phase F8.7 — Scrollable body so the fixed footer buttons
                (Close / Send Test) never get pushed off-screen when
                Auto Fetch pulls a shipment with many mapping variables.
                Only the middle section scrolls; the title above and
                the action row below stay pinned. */}
            <ScrollView
              style={styles.testScroll}
              contentContainerStyle={{ paddingBottom: 4 }}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator
            >
            {/* Phase F8.6 / F11.G — Authentication events auto-generate
                a 6-digit code for every test. Stage events replace this
                with a stage-appropriate hint and a Manual/Auto-Fetch
                tab bar. */}
            {testFor?.category === "auth" ? (
              <View style={styles.testHint}>
                <Text style={styles.testHintTxt}>
                  A random <Text style={{ fontWeight: "800" }}>6-digit code</Text>{" "}
                  is auto-generated for every test. The full payload &
                  provider response are shown below after Send.
                </Text>
              </View>
            ) : (
              <View style={styles.testHint}>
                <Text style={styles.testHintTxt}>
                  Choose how test values are supplied. All fields below
                  are editable — nothing is auto-injected on your behalf.
                </Text>
              </View>
            )}

            <Text style={styles.fieldLabel}>Phone number (with or without +91)</Text>
            <TextInput
              value={testPhone}
              onChangeText={setTestPhone}
              placeholder="e.g. 9876543210"
              keyboardType="phone-pad"
              style={styles.input}
              autoFocus
              editable={!testSending}
            />

            {/* Phase F8.6 — Manual / Auto Fetch tab bar for every
                non-auth event. Auto Fetch is only meaningful for
                Shipment Stage events (they map to a shipment status);
                for Recovery / Custom automations we hide the Auto tab
                and let the operator hand-key the test values. */}
            {testFor && testFor.category !== "auth" ? (
              <>
                <View style={styles.modeTabs}>
                  <TouchableOpacity
                    style={[
                      styles.modeTab,
                      testMode === "manual" && styles.modeTabActive,
                    ]}
                    onPress={() => setTestMode("manual")}
                    disabled={testSending || autoLoading}
                  >
                    <PhIcon
                      name="create-outline"
                      size={14}
                      color={testMode === "manual" ? "#1E40AF" : "#64748B"}
                    />
                    <Text style={[
                      styles.modeTabTxt,
                      testMode === "manual" && styles.modeTabTxtActive,
                    ]}>Manual</Text>
                  </TouchableOpacity>
                  {testFor.category === "stage" && (
                    <TouchableOpacity
                      style={[
                        styles.modeTab,
                        testMode === "auto" && styles.modeTabActive,
                      ]}
                      onPress={() => setTestMode("auto")}
                      disabled={testSending || autoLoading}
                    >
                      <PhIcon
                        name="cloud-download-outline"
                        size={14}
                        color={testMode === "auto" ? "#1E40AF" : "#64748B"}
                      />
                      <Text style={[
                        styles.modeTabTxt,
                        testMode === "auto" && styles.modeTabTxtActive,
                      ]}>Auto Fetch</Text>
                    </TouchableOpacity>
                  )}
                </View>

                {testMode === "auto" ? (
                  <View style={{ marginTop: 6 }}>
                    <TouchableOpacity
                      style={[
                        styles.autoFetchBtn,
                        (autoLoading || testSending) && { opacity: 0.6 },
                      ]}
                      onPress={runAutoFetch}
                      disabled={autoLoading || testSending}
                    >
                      {autoLoading ? (
                        <ActivityIndicator color="#1E40AF" />
                      ) : (
                        <>
                          <PhIcon name="download-outline" size={16} color="#1E40AF" />
                          <Text style={styles.autoFetchTxt}>
                            Fetch latest {testFor.label.replace("Stage: ", "")} order
                          </Text>
                        </>
                      )}
                    </TouchableOpacity>
                    {!!autoNote && (
                      <Text style={styles.autoNote}>{autoNote}</Text>
                    )}
                  </View>
                ) : null}

                {/* Variable input rows for THIS stage's selected fields
                    (excluding customer_phone which has its own top-of-
                    form input). Shown in BOTH manual and auto modes so
                    the operator can review/tweak fetched values before
                    sending. */}
                {(() => {
                  const fields = (testFor.selected_fields || []).filter(
                    (k) => k !== "customer_phone",
                  );
                  if (fields.length === 0) {
                    return (
                      <Text style={styles.autoNote}>
                        No mapping variables selected for this stage.
                        Add some in Configure → Data Fields.
                      </Text>
                    );
                  }
                  return (
                    <View style={{ marginTop: 8 }}>
                      {fields.map((k) => (
                        <View key={k} style={{ marginTop: 6 }}>
                          <Text style={styles.varRowLbl}>{k}</Text>
                          <TextInput
                            style={styles.input}
                            placeholder={`Enter test ${k}`}
                            placeholderTextColor="#94A3B8"
                            autoCapitalize="none"
                            autoCorrect={false}
                            value={testCtx[k] ?? ""}
                            editable={!testSending}
                            onChangeText={(v) =>
                              setTestCtx({ ...testCtx, [k]: v })
                            }
                          />
                        </View>
                      ))}
                    </View>
                  );
                })()}
              </>
            ) : null}
            </ScrollView>

            <View style={{ flexDirection: "row", gap: 10, marginTop: 16 }}>
              <TouchableOpacity
                style={styles.cancelBtn}
                onPress={() => {
                  if (!testSending) { setTestFor(null); setTestResult(null); }
                }}
                disabled={testSending}
              >
                <Text style={styles.cancelTxt}>Close</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.saveBtn, testSending && { opacity: 0.6 }]}
                onPress={runTest}
                disabled={testSending}
              >
                {testSending ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.saveTxt}>
                    {testResult ? "Send Again" : "Send Test"}
                  </Text>
                )}
              </TouchableOpacity>
            </View>

            {/* Live Response Viewer */}
            {testResult && (
              <ScrollView
                style={{ marginTop: 18, maxHeight: 360 }}
                showsVerticalScrollIndicator
              >
                <View
                  style={[
                    styles.testResultCard,
                    { backgroundColor: testResult.ok ? "#F0FDF4" : "#FEF2F2",
                      borderColor:     testResult.ok ? "#86EFAC" : "#FECACA" },
                  ]}
                >
                  <Text
                    style={[
                      styles.testResultTitle,
                      { color: testResult.ok ? "#15803D" : "#B91C1C" },
                    ]}
                  >
                    {testResult.skipped
                      ? "⚠️ Skipped"
                      : testResult.ok
                        ? "✅ Provider accepted the request"
                        : "❌ Provider rejected / errored"}
                  </Text>
                  {!!testResult.reason && (
                    <Text style={styles.testResultMuted}>
                      {testResult.reason}
                    </Text>
                  )}
                  <Text style={styles.testResultMuted}>
                    Status: {testResult.status_code ?? "—"}
                    {"  •  "}Took: {testResult.duration_ms ?? "—"} ms
                  </Text>
                </View>

                {/* Generated code — for Authentication events.
                    Phase F11.G: consolidated `auth_authentication`
                    plus legacy `otp_login`/`otp_signup`/
                    `otp_password_reset` all show the auto-generated
                    6-digit value here so the operator can verify
                    exactly what was sent. */}
                {(testFor?.event_key === "auth_authentication" ||
                  testFor?.event_key === "otp_login" ||
                  testFor?.event_key === "otp_signup" ||
                  testFor?.event_key === "otp_password_reset") &&
                  !!testResult.generated_otp && (
                    <View style={styles.otpBox}>
                      <Text style={styles.otpLabel}>Generated Code (6-digit)</Text>
                      <Text
                        selectable
                        style={styles.otpValue}
                      >
                        {testResult.generated_otp}
                      </Text>
                      <Text style={styles.testResultMuted}>
                        This is the exact code sent to your provider.
                        If you receive a different code (or none), the
                        BSP silently dropped or replaced it.
                      </Text>
                    </View>
                  )}

                {/* Endpoint */}
                {!!testResult.endpoint && (
                  <View style={styles.debugSection}>
                    <Text style={styles.debugLabel}>Endpoint hit</Text>
                    <Text style={styles.debugCode} selectable>
                      POST {testResult.endpoint}
                    </Text>
                  </View>
                )}

                {/* Request payload */}
                {!!testResult.request_payload && (
                  <View style={styles.debugSection}>
                    <Text style={styles.debugLabel}>
                      Payload sent (api_token masked)
                    </Text>
                    <Text style={styles.debugCode} selectable>
                      {JSON.stringify(testResult.request_payload, null, 2)}
                    </Text>
                  </View>
                )}

                {/* Response body */}
                {testResult.response_body !== undefined && (
                  <View style={styles.debugSection}>
                    <Text style={styles.debugLabel}>Provider response</Text>
                    <Text style={styles.debugCode} selectable>
                      {typeof testResult.response_body === "string"
                        ? testResult.response_body
                        : JSON.stringify(testResult.response_body, null, 2)}
                    </Text>
                  </View>
                )}
              </ScrollView>
            )}
          </Pressable>
        </Pressable>
      </Modal>

      {/* Phase F8.9 — Custom Automation create prompt. Tiny centered
          modal with a label input and a Create button; on success we
          jump straight into the freshly-created event's editor so the
          operator can paste the Webhook URL immediately. */}
      <Modal
        visible={showAddCustom}
        transparent
        animationType="fade"
        onRequestClose={() => {
          if (!creatingCustom) {
            setShowAddCustom(false);
            setNewCustomLabel("");
          }
        }}
      >
        <Pressable
          style={styles.modalBackdrop}
          onPress={() => {
            if (!creatingCustom) {
              setShowAddCustom(false);
              setNewCustomLabel("");
            }
          }}
        >
          <Pressable style={styles.testCard} onPress={() => {}}>
            <Text style={styles.testTitle}>➕ New Custom Automation</Text>
            <Text style={styles.testSub}>
              Give this automation a name. You'll paste the Webhook URL
              in the next step. All standard order / shipment variables
              are already available — no new mapping needed.
            </Text>
            <Text style={styles.fieldLabel}>Automation name</Text>
            <TextInput
              value={newCustomLabel}
              onChangeText={setNewCustomLabel}
              placeholder="e.g. Cross-sell offer, VIP welcome, …"
              placeholderTextColor="#94A3B8"
              autoFocus
              style={styles.input}
              editable={!creatingCustom}
            />
            <View style={{ flexDirection: "row", gap: 10, marginTop: 16 }}>
              <TouchableOpacity
                style={[styles.testBtnGhost, { flex: 1 }]}
                disabled={creatingCustom}
                onPress={() => {
                  setShowAddCustom(false);
                  setNewCustomLabel("");
                }}
              >
                <Text style={styles.testBtnGhostTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.testBtnPrimary, { flex: 1 }]}
                disabled={creatingCustom}
                onPress={createCustomEvent}
              >
                {creatingCustom
                  ? <ActivityIndicator color="#fff" />
                  : <Text style={styles.testBtnPrimaryTxt}>Create</Text>}
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
  // Phase F5.7 — Endpoint Template UI removed. The stored template
  // (if any) survives as-is on draft; if empty, backend uses Base
  // URL directly. The old `advancedEndpointOn` toggle is gone.

  useEffect(() => { setDraft(cfg); }, [cfg]);

  // Phase F5.7 — Save Connection is enabled whenever the two
  // required fields (Base URL + API Token) have content and we're
  // not mid-save. Dropped the strict "dirty" gate because the
  // backend never echoes the raw api_token back (only a masked
  // form) so `draft.api_token !== cfg.api_token` was falsely
  // "clean" whenever the operator opened the card to re-verify.
  const canSave =
    !saving &&
    (draft.base_url || "").trim().length > 0 &&
    ((draft.api_token || "").trim().length > 0 || (cfg.api_token_masked || "").trim().length > 0);

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
          {/* Provider Name — free-text label per user request
              (Jul-2026).  The old FlowConnect / WATI / Interakt /
              Gupshup / Custom chip picker all landed on the exact
              same downstream config, so the chip picker was noise
              rather than signal.  Users can now type any provider
              name they use ("FlowConnect", "WATI", …, or anything
              else).  Value still persists to `provider` on save so
              existing backend logic stays untouched. */}
          <Text style={styles.fieldLabel}>WhatsApp Provider Name</Text>
          <TextInput
            value={draft.provider}
            onChangeText={(v) => setDraft({ ...draft, provider: v })}
            placeholder="e.g. FlowConnect, WATI, Interakt, Gupshup, Custom…"
            autoCapitalize="words"
            style={styles.input}
          />

          <Text style={styles.fieldLabel}>Base URL</Text>
          <TextInput
            value={draft.base_url}
            onChangeText={(v) => setDraft({ ...draft, base_url: v })}
            placeholder="https://login.flowconnect.ai/api/automations/<your-automation-id>/execute"
            autoCapitalize="none"
            style={styles.input}
          />
          <Text style={styles.hint}>
            Paste the full webhook URL from your provider (it already
            includes the automation ID — no separate entry needed).
          </Text>

          {/* Phase F5.7 — Endpoint Template field removed from the
              Provider Connection UI per operator directive. The
              template still exists in state (persisted as an empty
              string) so power users' historical configs continue to
              work; the backend `dispatch_event` (whatsapp_provider.py)
              already POSTs to Base URL as-is when the template is
              empty. If a future provider needs a custom template
              path, we'll surface it as a per-event override inside
              the Advanced Settings section of the event editor. */}

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
              !canSave && { opacity: 0.5 },
            ]}
            onPress={() => onSave({
              provider:             draft.provider,
              base_url:             draft.base_url.trim(),
              endpoint_template:    (draft.endpoint_template || "").trim(),
              api_token:            draft.api_token.trim(),
              default_country_code: draft.default_country_code.trim() || "91",
            })}
            disabled={!canSave}
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
  providerReady,
  onToggle,
  onEdit,
  onTest,
  onDelete,
}: {
  row: EventRow;
  providerReady: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onTest: () => void;
  onDelete?: () => void;
}) {
  // Phase F5.7 — "configured" is now driven by GLOBAL provider
  // readiness (Base URL + API Token set at the top card), NOT by
  // the per-event automation_id. The Base URL already contains the
  // automation ID for 99% of providers, so treating an empty per-
  // event override as "not configured" surfaced misleading yellow
  // warnings on every card even when the provider was working. The
  // per-event automation_id stays available inside the event
  // editor's Advanced Settings for the rare case where an operator
  // needs a per-event override.
  const configured  = providerReady;
  const hasOverride = !!row.automation_id;
  // Phase F8.4 — All non-auth cards display their own dedicated
  // webhook URL status pill so operators can see at a glance which
  // events are routed to their own automation vs. still-unconfigured.
  const isStage    = row.category !== "auth";
  const hasWebhook = !!(row.webhook_url || "").trim();
  return (
    <View style={[styles.card, { paddingBottom: 0 }]}>
      <View style={styles.cardHeader}>
        <View
          style={[
            styles.iconBubble,
            {
              backgroundColor:
                row.category === "auth"     ? "#FEF3C7" :
                row.category === "recovery" ? "#FCE7F3" :
                row.category === "custom"   ? "#E0E7FF" :
                                              "#DCFCE7",
            },
          ]}
        >
          <PhIcon
            name={
              row.category === "auth"     ? "shield-checkmark-outline" :
              row.category === "recovery" ? "cart-outline" :
              row.category === "custom"   ? "settings-outline" :
                                            "cube-outline"
            }
            size={20}
            color={
              row.category === "auth"     ? "#92400E" :
              row.category === "recovery" ? "#BE185D" :
              row.category === "custom"   ? "#3730A3" :
                                            "#15803D"
            }
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
            {configured
              ? (hasOverride ? `Override: ${row.automation_id}` : "Ready")
              : "Configure provider first"}
          </Text>
        </View>
        {isStage ? (
          <View style={[
            styles.metaPill,
            hasWebhook ? styles.metaPillOk : styles.metaPillWarn,
            { marginLeft: 6 },
          ]}>
            <Text style={[
              styles.metaPillTxt,
              { color: hasWebhook ? "#15803D" : "#92400E" },
            ]}>
              {hasWebhook ? "Webhook set" : "Webhook missing"}
            </Text>
          </View>
        ) : null}
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
        {onDelete ? (
          <TouchableOpacity
            style={[styles.actionBtn, { backgroundColor: "#FEE2E2" }]}
            onPress={onDelete}
          >
            <PhIcon name="trash-outline" size={16} color="#B91C1C" />
          </TouchableOpacity>
        ) : null}
      </View>
    </View>
  );
}


// ─── Event editor modal ─────────────────────────────────────────────
function EventEditorModal({
  event,
  available,
  allEvents,
  onClose,
  onSave,
  saving,
}: {
  event: EventRow;
  available: AvailableField[];
  allEvents: EventRow[];
  onClose: () => void;
  onSave: (draft: EventRow) => void;
  saving: boolean;
}) {
  const [draft, setDraft] = useState<EventRow>(event);
  // Phase F5.3 — Simple/Advanced toggle. Everything beyond "Enable"
  // + "Test Send" is now advanced. Default OFF so the happy path
  // (API key + Base URL → just send data) is un-blocked by config.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // Phase F5.2 / F11.G — Detect if this Authentication event's
  // automation_id is SHARED with any non-auth event. This is the #1
  // cause of "code delivered without the number" bug reports.
  const isOtpEvent =
    event.event_key === "auth_authentication" ||
    event.event_key === "otp_login" ||
    event.event_key === "otp_signup" ||
    event.event_key === "otp_password_reset";
  const sharedWith = useMemo(() => {
    const target = (draft.automation_id || "").trim();
    if (!target) return [] as string[];
    return allEvents
      .filter(
        (e) =>
          e.event_key !== draft.event_key &&
          (e.automation_id || "").trim() === target,
      )
      .map((e) => e.label);
  }, [draft.automation_id, draft.event_key, allEvents]);
  const otpSharedWithStage = isOtpEvent && sharedWith.length > 0;
  // Phase F5.2 — Recent request payloads for THIS event (last 3).
  const [logs, setLogs] = useState<any[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      setLogsLoading(true);
      try {
        const r = await Api.adminWppLogs(draft.event_key, 3);
        if (alive) setLogs(r.items || []);
      } catch {
        if (alive) setLogs([]);
      } finally {
        if (alive) setLogsLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [draft.event_key]);
  // Phase F4.9 — Enable-Template toggle now bound directly to the
  // persisted `draft.template_enabled` bool. Previously we derived
  // this from the length of `template_preview` which meant a toggle
  // ON without any text was silently discarded — that reset UX bug
  // is exactly what shop owners were reporting.
  const setTemplateEnabled = (v: boolean) =>
    setDraft((d) => ({ ...d, template_enabled: v }));
  const templateEnabled = !!draft.template_enabled;

  // Phase F4.9 — Available-field picker state. When the operator taps
  // an existing mapping row (or the "+ Add Mapping" button), we open
  // a bottom-sheet picker sourced from the backend's AVAILABLE_FIELDS
  // registry. Zero manual typing — the entire mapping keyspace comes
  // from the server, so any field added to the registry appears here
  // automatically without a frontend deploy.
  const [pickerOpen, setPickerOpen] = useState<null | { forFieldKey: string }>(null);
  // Phase F11.E — Optional per-row "edit key manually" mode. When
  // `manualEditFor === fk`, the row swaps its picker button for a
  // TextInput so the operator can paste any custom variable key
  // (including special-char keys like `{%contact.customer_name%}`
  // that external WhatsApp automation systems require verbatim).
  // Existing tap-to-pick UX is unchanged for all other rows — this
  // is an ADDITIVE capability the user opts into per row.
  const [manualEditFor, setManualEditFor] = useState<string | null>(null);
  const [manualEditDraft, setManualEditDraft] = useState<string>("");

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
            {/* Phase F5.3 — SIMPLE MODE (default). Just tell the
                operator that this event will POST all standard
                data to the Base URL configured up top. No template,
                no fields picker, no automation_id. If they need
                advanced control (rare) they can open the toggle
                below. Fixes the "over-engineered — data not going"
                complaint by removing every knob that could break
                the happy path. */}
            <View style={styles.simpleModeBox}>
              <View style={styles.simpleHeader}>
                <PhIcon name="checkmark-circle" size={22} color="#059669" />
                <Text style={styles.simpleTitle}>Simple Mode</Text>
              </View>
              <Text style={styles.simpleBody}>
                When this event fires, all standard fields
                (customer_name, customer_phone
                {isOtpEvent ? ", otp, event_type" : ", order_id, tracking_id, courier_name, business_name, current_stage"})
                are POSTed as query parameters to the WhatsApp
                Provider's Base URL. Nothing else to configure —
                just hit <Text style={{ fontWeight: "700" }}>Enable</Text> and
                <Text style={{ fontWeight: "700" }}> Test Send</Text>.
              </Text>
            </View>

            {/* Advanced Settings toggle. Hidden by default so the
                simple flow above is the primary UX. Everything the
                user asked to "disable for now" lives inside — kept
                intact so it can be re-enabled without a redeploy
                when future automations need it. */}
            <TouchableOpacity
              style={styles.advToggleBtn}
              onPress={() => setAdvancedOpen((v) => !v)}
              testID="advanced-toggle"
            >
              <PhIcon
                name={advancedOpen ? "chevron-up" : "chevron-down"}
                size={16}
                color="#64748B"
              />
              <Text style={styles.advToggleTxt}>
                {advancedOpen ? "Hide Advanced Settings" : "Advanced Settings (optional)"}
              </Text>
            </TouchableOpacity>

            {advancedOpen && <>
            {/* Phase F8.4 + F8.9 / F11.G — Per-event Webhook URL for
                every non-auth event (stages, recovery, custom
                automations). Authentication events continue to use
                the global Base URL configured at the top of the page
                — those go through the operator's Authentication
                automation. */}
            {draft.category !== "auth" && (
              <View style={{ marginTop: 4 }}>
                <Text style={styles.fieldLabel}>Webhook URL</Text>
                <Text style={styles.hint}>
                  This stage's data will be POSTed ONLY to this URL.
                  Leave blank to disable this stage's outbound sends.
                  Never shares the Authentication automation link.
                </Text>
                <TextInput
                  style={styles.input}
                  placeholder="https://…/api/automations/<id>/execute"
                  placeholderTextColor="#94A3B8"
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                  value={draft.webhook_url || ""}
                  onChangeText={(v) =>
                    setDraft({ ...draft, webhook_url: v })
                  }
                />
              </View>
            )}

            {/* Phase F5.3 (2026-06-27) — Automation ID field removed.
                Reason: Base URL configured up top ALREADY contains the
                per-provider automation ID (FlowConnect gives one URL
                per automation e.g.
                https://…/api/automations/69ff6d.../execute). Asking
                operators to re-enter it here caused endless "why isn't
                it working" reports because they'd either paste the
                whole URL or leave it blank and the template merge
                would break. Backend now always POSTs to base_url
                directly when the per-event automation_id is empty
                (see whatsapp_provider.py dispatch_event). The advanced
                fields below (Fields to Send, Variable Mapping,
                Template, Custom Fields) stay so ops can revive them
                if a future provider needs per-event overrides. */}

            {/* Phase F5.2 / F11.G — Authentication-specific warning.
                FlowConnect (and every other BSP we've integrated)
                attaches ONE template per automation_id. When the same
                automation is reused for both Authentication + stage
                messages, the Authentication branch inherits the stage
                template — which has no {{otp}} placeholder — so
                customers get a generic message without the code. This
                banner catches the shared-automation antipattern
                before the operator ever tests it. */}
            {isOtpEvent && otpSharedWithStage && (
              <View style={styles.otpWarn} testID="otp-shared-warning">
                <View style={styles.otpWarnHeader}>
                  <PhIcon name="warning" size={20} color="#B45309" />
                  <Text style={styles.otpWarnTitle}>
                    Shared automation detected
                  </Text>
                </View>
                <Text style={styles.otpWarnBody}>
                  This Authentication event shares its automation ID with:{" "}
                  <Text style={{ fontWeight: "700" }}>
                    {sharedWith.join(", ")}
                  </Text>
                  . FlowConnect binds ONE template per automation ID, so
                  your Authentication messages are being delivered using
                  the stage template — which does not include the code.
                </Text>
                <Text style={[styles.otpWarnBody, { marginTop: 8 }]}>
                  <Text style={{ fontWeight: "700" }}>Fix:</Text>{" "}
                  Create a NEW automation on FlowConnect dedicated to
                  Authentication delivery (template must reference{" "}
                  <Text style={styles.code}>{"{{otp}}"}</Text>) and paste
                  that automation ID here.
                </Text>
              </View>
            )}

            {/* Phase F5.2 / F11.G — Authentication variable cheat sheet
                + copy-ready reference template. Shown for Authentication
                events regardless of shared-automation status so
                operators always know which placeholders their
                FlowConnect template must reference. */}
            {isOtpEvent && (
              <View style={styles.otpCheatBox} testID="otp-cheatsheet">
                <View style={styles.otpCheatHeader}>
                  <PhIcon name="information-circle" size={18} color="#1E40AF" />
                  <Text style={styles.otpCheatTitle}>
                    Your FlowConnect template needs these variables
                  </Text>
                </View>
                <View style={styles.otpVarRow}>
                  <Text style={styles.otpVarChip}>{"{{otp}}"}</Text>
                  <Text style={styles.otpVarDesc}>the one-time code (required)</Text>
                </View>
                <View style={styles.otpVarRow}>
                  <Text style={styles.otpVarChip}>{"{{otp_type}}"}</Text>
                  <Text style={styles.otpVarDesc}>login / signup / reset (dynamic)</Text>
                </View>
                <View style={styles.otpVarRow}>
                  <Text style={styles.otpVarChip}>{"{{customer_name}}"}</Text>
                  <Text style={styles.otpVarDesc}>recipient's name (optional)</Text>
                </View>
                <View style={styles.otpVarRow}>
                  <Text style={styles.otpVarChip}>{"{{customer_phone}}"}</Text>
                  <Text style={styles.otpVarDesc}>phone number (optional)</Text>
                </View>
                <TouchableOpacity
                  style={styles.otpCopyBtn}
                  testID="otp-copy-template"
                  onPress={async () => {
                    const t =
                      "Hello {{customer_name}},\n\n" +
                      "Your Shippzo verification code is *{{otp}}*.\n\n" +
                      "This code expires in 5 minutes. Please do not share " +
                      "it with anyone.\n\n— Shippzo";
                    try {
                      await Clipboard.setStringAsync(t);
                      Alert.alert(
                        "Template copied ✓",
                        "Paste this into your FlowConnect automation " +
                          "template. Make sure {{otp}} placeholder is preserved.",
                      );
                    } catch {
                      Alert.alert("Copy failed", "Could not access clipboard.");
                    }
                  }}
                >
                  <PhIcon name="copy-outline" size={14} color="#1E40AF" />
                  <Text style={styles.otpCopyBtnTxt}>
                    Copy ready-made Authentication template
                  </Text>
                </TouchableOpacity>
              </View>
            )}

            {/* Phase F5.2 — Recent Requests. Renders the last 3
                payloads pushed to the provider so admins can verify
                (a) that the OTP is being included, and (b) which
                variables their template must reference. */}
            <View style={styles.recentBox} testID="recent-requests">
              <Text style={styles.recentTitle}>
                📋 Recent Requests ({logsLoading ? "…" : logs.length})
              </Text>
              <Text style={styles.hint}>
                Actual data pushed to the provider on the last few
                dispatches. Cross-check these keys against your
                FlowConnect template placeholders.
              </Text>
              {logs.length === 0 && !logsLoading && (
                <Text style={styles.emptyRecent}>
                  No requests logged yet — hit "Test Send" to trigger one.
                </Text>
              )}
              {logs.map((r, i) => {
                const req = r.request || {};
                const shown = Object.entries(req)
                  .filter(([k]) => k !== "api_token")
                  .slice(0, 12);
                return (
                  <View key={i} style={styles.recentCard}>
                    <View style={styles.rowBetween}>
                      <Text style={[
                        styles.recentStatus,
                        r.success ? styles.recentOk : styles.recentBad,
                      ]}>
                        {r.success ? "✓" : "✗"} {r.status_code || "?"}
                      </Text>
                      <Text style={styles.recentTs}>
                        {(r.ts || "").slice(0, 19).replace("T", " ")}
                      </Text>
                    </View>
                    {shown.map(([k, v]) => (
                      <Text key={k} style={styles.recentKV} numberOfLines={1}>
                        <Text style={styles.recentKey}>{k}:</Text>{" "}
                        <Text style={styles.code}>{String(v)}</Text>
                      </Text>
                    ))}
                  </View>
                );
              })}
            </View>

            {/* Enable Template toggle + Template preview + copy.
                Toggle is OFF by default and hides the template
                fields entirely — the structured JSON payload is
                sent regardless, so the template is purely a
                reference for the admin to paste inside the
                provider's own dashboard. */}
            <View style={[styles.rowBetween, { marginTop: 16 }]}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>Enable Template</Text>
                <Text style={styles.hint}>
                  Turn on if you want to keep a reference template
                  here (to paste into the provider's dashboard).
                  Off = only the structured JSON is sent.
                </Text>
              </View>
              <Switch
                value={templateEnabled}
                onValueChange={setTemplateEnabled}
                thumbColor={templateEnabled ? colors.primary : "#94A3B8"}
              />
            </View>
            {templateEnabled && (
              <>
                <View style={[styles.rowBetween, { marginTop: 10 }]}>
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
              </>
            )}

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

            {/* Variable mapping (only for ticked fields) — Phase F4.9
                revamp: tap-to-pick from AVAILABLE_FIELDS instead of
                free-text input. Prevents the "typed weird key name"
                dispatches we've seen in production (e.g.
                NayanBhut=Admin) and stays future-proof — anything
                added to the backend registry appears here without a
                frontend deploy. */}
            {draft.selected_fields.length > 0 && (
              <>
                <Text style={[styles.fieldLabel, { marginTop: 18 }]}>
                  Variable Mapping ({draft.selected_fields.length})
                </Text>
                <Text style={styles.hint}>
                  Left = app field (data source). Right = WhatsApp
                  template variable name (what your provider expects).
                  Tap the right side to pick from the available
                  fields list. Empty = keep same key. Tap the pencil
                  icon to enter a custom key manually (supports{" "}
                  <Text style={styles.code}>{"{"}, {"}"}, %, ., _</Text>{" "}
                  and any other special characters your external
                  automation system needs).
                </Text>
                {draft.selected_fields.map((fk) => {
                  const targetKey = draft.variable_mapping[fk] || fk;
                  const targetLabel =
                    available.find((a) => a.key === targetKey)?.label
                    || targetKey;
                  const sourceLabel =
                    available.find((a) => a.key === fk)?.label || fk;
                  const isEditing = manualEditFor === fk;
                  return (
                    <View key={fk} style={styles.mapRow}>
                      <View style={styles.mapCell}>
                        <Text style={styles.mapLbl}>{sourceLabel}</Text>
                        <Text style={styles.mapKey}>{fk}</Text>
                      </View>
                      <PhIcon name="arrow-forward" size={16} color="#94A3B8" />
                      {isEditing ? (
                        /* Phase F11.E — inline TextInput mode. Bound to
                           `manualEditDraft` while the operator is
                           typing; committed to variable_mapping only
                           on save so the picker/preview keep their
                           previous value if the user cancels.

                           Save behaviour:
                             • trims whitespace
                             • blank OR matches original key `fk`
                               → delete entry (keeps payload clean)
                             • anything else → set as verbatim override
                        */
                        <View style={[styles.mapPick, { paddingVertical: 6 }]}>
                          <TextInput
                            style={styles.manualKeyInput}
                            value={manualEditDraft}
                            onChangeText={setManualEditDraft}
                            placeholder={fk}
                            placeholderTextColor="#94A3B8"
                            autoCapitalize="none"
                            autoCorrect={false}
                            autoFocus
                            selectTextOnFocus
                            testID={`map-manual-input-${fk}`}
                          />
                          <TouchableOpacity
                            onPress={() => {
                              const v = manualEditDraft.trim();
                              setDraft((d) => {
                                const next = { ...d.variable_mapping };
                                if (!v || v === fk) {
                                  // Blank or unchanged → remove entry so
                                  // the outbound payload doesn't carry
                                  // redundant identity mappings.
                                  delete next[fk];
                                } else {
                                  next[fk] = v;
                                }
                                return { ...d, variable_mapping: next };
                              });
                              setManualEditFor(null);
                              setManualEditDraft("");
                            }}
                            hitSlop={{ top: 8, bottom: 8, left: 6, right: 6 }}
                            testID={`map-manual-save-${fk}`}
                          >
                            <PhIcon name="checkmark" size={18} color="#10B981" />
                          </TouchableOpacity>
                          <TouchableOpacity
                            onPress={() => {
                              setManualEditFor(null);
                              setManualEditDraft("");
                            }}
                            hitSlop={{ top: 8, bottom: 8, left: 6, right: 6 }}
                            testID={`map-manual-cancel-${fk}`}
                          >
                            <PhIcon name="close" size={18} color="#94A3B8" />
                          </TouchableOpacity>
                        </View>
                      ) : (
                        <TouchableOpacity
                          testID={`map-picker-${fk}`}
                          style={styles.mapPick}
                          onPress={() => setPickerOpen({ forFieldKey: fk })}
                        >
                          <View style={{ flex: 1 }}>
                            <Text style={styles.mapLbl}>{targetLabel}</Text>
                            <Text style={styles.mapKey}>{targetKey}</Text>
                          </View>
                          {/* Phase F11.E — manual-edit trigger.
                              Pencil icon opens the TextInput mode for
                              THIS row only. All existing picker
                              behaviour is untouched. */}
                          <TouchableOpacity
                            onPress={(e) => {
                              e.stopPropagation?.();
                              setManualEditDraft(
                                draft.variable_mapping[fk] ?? fk,
                              );
                              setManualEditFor(fk);
                            }}
                            hitSlop={{ top: 8, bottom: 8, left: 6, right: 6 }}
                            style={{ marginRight: 4 }}
                            testID={`map-manual-edit-${fk}`}
                            accessibilityLabel="Edit key manually"
                          >
                            <PhIcon name="create-outline" size={15} color="#2563EB" />
                          </TouchableOpacity>
                          <PhIcon name="chevron-down" size={16} color="#94A3B8" />
                        </TouchableOpacity>
                      )}
                    </View>
                  );
                })}

                {/* Bug 4 — Variable Mapping Preview: crystal-clear
                    "what will be sent" summary that mirrors the
                    payload the backend actually pushes. */}
                <View style={styles.previewBox}>
                  <Text style={styles.previewTitle}>Outgoing Variables Preview</Text>
                  {draft.selected_fields.map((fk) => {
                    const out = draft.variable_mapping[fk] || fk;
                    const lbl =
                      available.find((a) => a.key === fk)?.label || fk;
                    return (
                      <Text key={`p-${fk}`} style={styles.previewLine}>
                        <Text style={{ color: "#0F172A" }}>{lbl}</Text>
                        {"  →  "}
                        <Text style={styles.code}>{out}</Text>
                      </Text>
                    );
                  })}
                  {draft.selected_fields.includes("otp") && (
                    <Text style={[styles.previewLine, { marginTop: 6, color: "#166534" }]}>
                      ✓ Code is auto-supplied by the Authentication flow —
                      no manual value needed. Just keep `otp` ticked.
                    </Text>
                  )}
                  {draft.selected_fields.includes("otp_type") && (
                    <Text style={[styles.previewLine, { marginTop: 4, color: "#166534" }]}>
                      ✓ `otp_type` auto-populates with{" "}
                      <Text style={styles.code}>login</Text> ·{" "}
                      <Text style={styles.code}>signup</Text> ·{" "}
                      <Text style={styles.code}>reset</Text> based on the
                      triggering flow.
                    </Text>
                  )}
                </View>
              </>
            )}

            {/* Field picker sheet — Phase F4.9 */}
            {pickerOpen && (
              <View style={styles.pickerOverlay}>
                <TouchableOpacity
                  style={styles.pickerBackdrop}
                  activeOpacity={1}
                  onPress={() => setPickerOpen(null)}
                />
                <View style={styles.pickerSheet}>
                  <View style={styles.pickerHeader}>
                    <Text style={styles.pickerTitle}>
                      {`Map "${available.find((a) => a.key === pickerOpen.forFieldKey)?.label
                        || pickerOpen.forFieldKey}" to…`}
                    </Text>
                    <TouchableOpacity onPress={() => setPickerOpen(null)}>
                      <PhIcon name="close" size={22} color="#0F172A" />
                    </TouchableOpacity>
                  </View>
                  <ScrollView style={{ maxHeight: 420 }}>
                    {available.map((f) => (
                      <TouchableOpacity
                        key={f.key}
                        testID={`map-option-${f.key}`}
                        style={styles.pickerRow}
                        onPress={() => {
                          setDraft((d) => {
                            const next = { ...d.variable_mapping };
                            if (f.key === pickerOpen.forFieldKey) {
                              // Same key → identity mapping = clear
                              // entry to keep the payload lean.
                              delete next[pickerOpen.forFieldKey];
                            } else {
                              next[pickerOpen.forFieldKey] = f.key;
                            }
                            return { ...d, variable_mapping: next };
                          });
                          setPickerOpen(null);
                        }}
                      >
                        <View style={{ flex: 1 }}>
                          <Text style={styles.pickerLbl}>{f.label}</Text>
                          <Text style={styles.pickerKey}>{f.key}</Text>
                        </View>
                        {(draft.variable_mapping[pickerOpen.forFieldKey] || pickerOpen.forFieldKey) === f.key && (
                          <PhIcon name="checkmark" size={18} color="#166534" />
                        )}
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                </View>
              </View>
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
              {"\n\n"}
              <Text style={{ fontWeight: "700", color: "#0369A1" }}>💡 Placeholders:</Text>{" "}
              Use <Text style={styles.code}>{"{%contact.otp%}"}</Text>,{" "}
              <Text style={styles.code}>{"{%contact.customer_name%}"}</Text>,{" "}
              <Text style={styles.code}>{"{%contact.order_id%}"}</Text> etc. as VALUE
              to inject live context data. Unresolved placeholders are forwarded to the
              CRM verbatim so FlowConnect / WATI / Interakt can resolve them downstream.
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
            </>}
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

  // Phase F8.6 — Test Send popup Manual/Auto tab bar + variable rows.
  modeTabs: {
    flexDirection: "row",
    gap: 8,
    marginTop: 12,
  },
  modeTab: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 10,
    borderRadius: 8,
    backgroundColor: "#F1F5F9",
    borderWidth: 1,
    borderColor: "#E2E8F0",
  },
  modeTabActive: {
    backgroundColor: "#DBEAFE",
    borderColor: "#93C5FD",
  },
  modeTabTxt: {
    fontSize: 12.5,
    fontWeight: "700",
    color: "#64748B",
  },
  modeTabTxtActive: {
    color: "#1E40AF",
  },
  autoFetchBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderWidth: 1,
    borderColor: "#93C5FD",
    borderRadius: 8,
    backgroundColor: "#EFF6FF",
    paddingVertical: 10,
    marginTop: 6,
  },
  autoFetchTxt: {
    fontSize: 13,
    fontWeight: "700",
    color: "#1E40AF",
  },
  autoNote: {
    marginTop: 6,
    fontSize: 11.5,
    lineHeight: 15,
    color: "#475569",
    fontStyle: "italic",
  },
  varRowLbl: {
    fontSize: 11.5,
    fontWeight: "700",
    color: "#334155",
    marginBottom: 2,
  },

  // Phase F8.9 — Custom Automations header row + Add button + empty
  // state hint. Sits directly above the list of custom event cards.
  customHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 12,
    marginBottom: 6,
  },
  customAddBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 8,
    backgroundColor: "#DBEAFE",
    borderWidth: 1,
    borderColor: "#93C5FD",
  },
  customAddTxt: {
    fontSize: 12,
    fontWeight: "700",
    color: "#1E40AF",
  },
  customEmptyTxt: {
    fontSize: 12,
    lineHeight: 17,
    color: "#64748B",
    fontStyle: "italic",
    paddingHorizontal: 4,
    paddingVertical: 6,
  },

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
  // Phase F4.9 — mapping picker cell + preview styles.
  mapCell: {
    flex: 1,
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 8,
    backgroundColor: "#EEF2FF",
  },
  mapPick: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 10,
    paddingVertical: 8,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: "#CBD5E1",
    backgroundColor: "#F8FAFC",
    gap: 6,
  },
  mapLbl: { fontSize: 13, fontWeight: "700", color: "#0F172A" },
  mapKey: { fontSize: 11, color: "#64748B", marginTop: 1 },
  // Phase F11.E — inline TextInput for manual "custom key" edit mode.
  // Monospace so special chars like `{%contact.customer_name%}` line
  // up character-by-character during typing/paste.
  manualKeyInput: {
    flex: 1,
    fontSize: 13,
    color: "#0F172A",
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
    paddingVertical: 4,
    paddingHorizontal: 6,
    backgroundColor: "#fff",
    borderRadius: 4,
    borderWidth: 1,
    borderColor: "#93C5FD",
  },
  previewBox: {
    marginTop: 12,
    padding: 10,
    borderRadius: 10,
    backgroundColor: "#F1F5F9",
    borderWidth: 1,
    borderColor: "#E2E8F0",
  },
  previewTitle: {
    fontSize: 11,
    fontWeight: "800",
    color: "#475569",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    marginBottom: 6,
  },
  previewLine: { fontSize: 12.5, lineHeight: 18, color: "#334155" },
  pickerOverlay: {
    position: "absolute",
    top: 0, left: 0, right: 0, bottom: 0,
    justifyContent: "flex-end",
    zIndex: 9999,
  },
  pickerBackdrop: {
    position: "absolute",
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.5)",
  },
  pickerSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 24,
    maxHeight: 540,
  },
  pickerHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 8,
  },
  pickerTitle: {
    flex: 1,
    fontSize: 15,
    fontWeight: "800",
    color: "#0F172A",
  },
  pickerRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: "#E2E8F0",
  },
  pickerLbl: { fontSize: 14, fontWeight: "700", color: "#0F172A" },
  pickerKey: { fontSize: 12, color: "#64748B", marginTop: 2 },

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
    maxHeight: "85%",
    // Phase F8.7 — column layout so the inner ScrollView flexes and
    // the pinned action-buttons row stays visible even when Auto
    // Fetch populates 8+ mapping variables.
    flexDirection: "column",
  },
  testTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  testSub:   { fontSize: 12, color: "#64748B", marginTop: 2, marginBottom: 10 },

  // Phase F8.7 — scrollable middle section of the Test Send popup.
  // `flexShrink: 1` lets it collapse under the fixed header + footer
  // instead of stretching the card past the screen; the intrinsic
  // scroll then reveals any overflow content (long variable lists).
  testScroll: {
    flexShrink: 1,
  },

  // Phase F5.8 — Live Response Viewer styles (Test Send modal).
  testHint: {
    marginTop: 4, marginBottom: 12,
    backgroundColor: "#EFF6FF", borderColor: "#BFDBFE", borderWidth: 1,
    borderRadius: 10, padding: 10,
  },
  testHintTxt: { fontSize: 12, color: "#1E40AF", lineHeight: 17 },
  testResultCard: {
    borderWidth: 1, borderRadius: 12, padding: 12, marginBottom: 12,
  },
  testResultTitle: { fontSize: 14, fontWeight: "800", marginBottom: 4 },
  testResultMuted: { fontSize: 11, color: "#475569", marginTop: 2, lineHeight: 15 },
  otpBox: {
    borderWidth: 1, borderColor: "#FCD34D", backgroundColor: "#FFFBEB",
    borderRadius: 12, padding: 12, marginBottom: 12,
  },
  otpLabel: { fontSize: 11, fontWeight: "700", color: "#92400E" },
  otpValue: {
    fontSize: 28, fontWeight: "900", color: "#78350F",
    letterSpacing: 6, textAlign: "center", marginTop: 6, marginBottom: 6,
    fontVariant: ["tabular-nums"],
  },
  debugSection: { marginBottom: 10 },
  debugLabel: { fontSize: 11, fontWeight: "700", color: "#334155", marginBottom: 4 },
  debugCode: {
    fontSize: 11, color: "#0F172A", lineHeight: 16,
    backgroundColor: "#F1F5F9", borderRadius: 8, padding: 8,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }),
  },

  // ── Phase F5.3 — Simple mode header + Advanced toggle ─────────
  simpleModeBox: {
    marginTop: 4, marginBottom: 12,
    backgroundColor: "#ECFDF5", borderColor: "#A7F3D0", borderWidth: 1,
    borderRadius: 12, padding: 14,
  },
  simpleHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 },
  simpleTitle:  { fontSize: 14, fontWeight: "800", color: "#065F46" },
  simpleBody:   { fontSize: 12, color: "#065F46", lineHeight: 17 },
  advToggleBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, paddingVertical: 10, marginTop: 2, marginBottom: 10,
    backgroundColor: "#F1F5F9", borderRadius: 10,
  },
  advToggleTxt: { fontSize: 12, fontWeight: "700", color: "#64748B" },

  // ── Phase F5.2 — OTP warning + cheat-sheet + recent-requests ────
  otpWarn: {
    marginTop: 14,
    backgroundColor: "#FFFBEB",
    borderColor: "#FCD34D",
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  otpWarnHeader:  { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6 },
  otpWarnTitle:   { fontSize: 13, fontWeight: "800", color: "#92400E" },
  otpWarnBody:    { fontSize: 12, color: "#78350F", lineHeight: 17 },

  otpCheatBox: {
    marginTop: 14,
    backgroundColor: "#EFF6FF",
    borderColor: "#BFDBFE",
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  otpCheatHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  otpCheatTitle:  { fontSize: 13, fontWeight: "800", color: "#1E40AF" },
  otpVarRow:      { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
  otpVarChip:     {
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) || "monospace",
    fontSize: 11,
    fontWeight: "700",
    color: "#1E3A8A",
    backgroundColor: "#DBEAFE",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  otpVarDesc: { flex: 1, fontSize: 11, color: "#3730A3" },
  otpCopyBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    marginTop: 10,
    paddingVertical: 9,
    borderRadius: 8,
    backgroundColor: "#fff",
    borderColor: "#BFDBFE",
    borderWidth: 1,
  },
  otpCopyBtnTxt: { fontSize: 12, fontWeight: "700", color: "#1E40AF" },

  recentBox: {
    marginTop: 16,
    backgroundColor: "#F8FAFC",
    borderColor: "#E5E7EB",
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  recentTitle:  { fontSize: 13, fontWeight: "800", color: "#0F172A", marginBottom: 4 },
  emptyRecent:  { fontSize: 11, color: "#94A3B8", fontStyle: "italic", marginTop: 6 },
  recentCard:   {
    marginTop: 8,
    backgroundColor: "#fff",
    borderColor: "#E5E7EB",
    borderWidth: 1,
    borderRadius: 8,
    padding: 10,
  },
  recentStatus: { fontSize: 11, fontWeight: "800" },
  recentOk:     { color: "#059669" },
  recentBad:    { color: "#DC2626" },
  recentTs:     { fontSize: 10, color: "#64748B" },
  recentKV:     { fontSize: 11, color: "#0F172A", marginTop: 2 },
  recentKey:    { fontWeight: "700", color: "#334155" },
});
