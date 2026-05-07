/**
 * Admin → Stage Rules screen (Phase-G2)
 * --------------------------------------
 * Table of all 6 pipeline stages + inline expand-to-edit per stage.
 * Drives the unified SLA + alert + customer-message system shipped
 * in Phase-G1 backend (server.py + stage_rules.py).
 *
 * Each row exposes the 4 admin levers from the spec:
 *   ① SLA days
 *   ② Internal alert toggle / priority / channel / recipients
 *   ③ Customer template enable + auto_trigger toggle
 *   ④ Cooldown hours + escalation steps (read-only summary in v1)
 *
 * "Edit" expands the row inline rather than navigating away — keeps
 * the whole pipeline visible at a glance while editing.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  TextInput, Switch, ActivityIndicator, Alert, KeyboardAvoidingView,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, router } from "expo-router";
import { Api } from "../../lib/api";

const STAGE_META: Record<string, { icon: string; color: string; sub: string }> = {
  "Pending":       { icon: "📥", color: "#9333EA", sub: "Order created — not yet processed" },
  "Processing":    { icon: "🔧", color: "#0EA5E9", sub: "Items being prepared" },
  "Ready to Ship": { icon: "📦", color: "#10B981", sub: "Packed, awaiting courier pickup" },
  "Shipped":       { icon: "🚚", color: "#1F4FBF", sub: "Out for delivery" },
  "Delivered":     { icon: "✅", color: "#059669", sub: "Customer received the parcel" },
  "Feedback":      { icon: "⭐", color: "#B45309", sub: "Asking for review" },
};

const PRIORITY_OPTS = ["low", "medium", "high"] as const;
const CHANNEL_OPTS  = ["whatsapp", "app", "both", "none"] as const;
const RECIPIENT_OPTS = ["admin", "team"] as const;

export default function StageRulesScreen() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [data, setData] = useState<any>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  // Local edit buffer per stage so changes are previewed before save.
  const [drafts, setDrafts] = useState<Record<string, any>>({});
  const [adminNumber, setAdminNumber] = useState("");
  const [teamNumbersText, setTeamNumbersText] = useState("");
  // Engine-level settings (Phase G3)
  const [globalEnabled, setGlobalEnabled] = useState(true);
  const [scanInterval, setScanInterval] = useState(60);
  const [defaultCooldown, setDefaultCooldown] = useState(24);
  const [chList, setChList] = useState(true);
  const [chBanner, setChBanner] = useState(true);
  const [chPush, setChPush] = useState(false);
  const [running, setRunning] = useState(false);
  const [lastRun, setLastRun] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const d = await Api.adminGetStageRules();
      setData(d);
      setDrafts({}); // reset drafts on fresh load
      const cur = d.current || {};
      setAdminNumber(String(cur.alert_admin_number || ""));
      setTeamNumbersText(((cur.alert_team_numbers || []) as string[]).join(", "));
      setGlobalEnabled(cur.global_enabled !== false);
      setScanInterval(Number(cur.scan_interval_minutes) || 60);
      setDefaultCooldown(Number(cur.default_cooldown_hours) || 24);
      const dc = cur.display_channels || {};
      setChList(dc.list !== false);
      setChBanner(dc.banner !== false);
      setChPush(!!dc.push);
      try {
        const sum = await Api.adminSlaSummary();
        setLastRun(sum.last_run);
      } catch { /* non-fatal */ }
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const stages = data?.stages || [];
  const cur = data?.current?.stages || {};

  const stageVal = (stage: string) => ({ ...cur[stage], ...(drafts[stage] || {}) });
  const setStageVal = (stage: string, patch: any) =>
    setDrafts((d) => ({ ...d, [stage]: { ...(d[stage] || {}), ...patch } }));

  const dirty = Object.keys(drafts).length > 0
    || adminNumber !== String(data?.current?.alert_admin_number || "")
    || teamNumbersText !== ((data?.current?.alert_team_numbers || []) as string[]).join(", ")
    || globalEnabled !== (data?.current?.global_enabled !== false)
    || scanInterval !== (Number(data?.current?.scan_interval_minutes) || 60)
    || defaultCooldown !== (Number(data?.current?.default_cooldown_hours) || 24)
    || chList !== ((data?.current?.display_channels?.list) !== false)
    || chBanner !== ((data?.current?.display_channels?.banner) !== false)
    || chPush !== !!(data?.current?.display_channels?.push);

  const save = async () => {
    setSaving(true);
    try {
      const teamList = teamNumbersText.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
      await Api.adminPutStageRules({
        stages: drafts,
        alert_admin_number: adminNumber.trim(),
        alert_team_numbers: teamList,
        global_enabled: globalEnabled,
        scan_interval_minutes: scanInterval,
        default_cooldown_hours: defaultCooldown,
        display_channels: { list: chList, banner: chBanner, push: chPush },
      });
      Alert.alert("Saved", "Stage rules updated.");
      load();
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Error");
    } finally {
      setSaving(false);
    }
  };

  const runScanNow = async () => {
    setRunning(true);
    try {
      const res = await Api.adminSlaRunNow();
      if (res.ok) {
        setLastRun(res.stats);
        Alert.alert(
          "Scan complete",
          `Users scanned: ${res.stats?.users_scanned || 0}\nNew alerts: ${res.stats?.alerts_raised || 0}`,
        );
      } else {
        Alert.alert("Already running", res.message || "Try again in a moment.");
      }
    } catch (e: any) {
      Alert.alert("Scan failed", e?.response?.data?.detail || e?.message || "Error");
    } finally {
      setRunning(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator color="#6B5BFF" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ title: "Stage Rules", headerShown: true }} />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
        keyboardVerticalOffset={20}
      >
        <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 130 }}>
          {/* Header card */}
          <View style={styles.headerCard}>
            <Text style={styles.headerTitle}>📋 Stage Rules</Text>
            <Text style={styles.headerSub}>
              SLA tracking, internal alerts, customer messages, and escalation —
              all configured per pipeline stage. Tap "Edit" on any row to drill in.
            </Text>
          </View>

          {/* Table header */}
          <View style={styles.tableHeader}>
            <Text style={[styles.thStage, { flex: 1.6 }]}>Stage</Text>
            <Text style={styles.thNum}>SLA</Text>
            <Text style={styles.thNum}>Alert</Text>
            <Text style={styles.thNum}>Msg</Text>
            <Text style={styles.thNum}>Auto</Text>
            <Text style={styles.thEdit}> </Text>
          </View>

          {/* Per-stage rows */}
          {stages.map((stage: string) => {
            const v = stageVal(stage);
            const meta = STAGE_META[stage] || { icon: "•", color: "#374151", sub: "" };
            const tplType = data?.stage_to_template?.[stage];
            const isExpanded = expanded === stage;
            const hasDraft = !!drafts[stage];

            return (
              <View key={stage} style={[styles.row, hasDraft && styles.rowDirty]}>
                {/* Compact summary line */}
                <TouchableOpacity
                  style={styles.rowMain}
                  onPress={() => setExpanded(isExpanded ? null : stage)}
                  activeOpacity={0.85}
                >
                  <View style={[styles.stageCol]}>
                    <Text style={styles.stageIcon}>{meta.icon}</Text>
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.stageName, { color: meta.color }]}>
                        {stage}
                        {hasDraft && <Text style={styles.dirtyTag}> · edited</Text>}
                      </Text>
                      <Text style={styles.stageSub} numberOfLines={1}>{meta.sub}</Text>
                    </View>
                  </View>
                  <Text style={styles.tdNum}>{v.sla_days}d</Text>
                  <Text style={[styles.tdNum, { color: v.alert_enabled ? "#059669" : "#9CA3AF" }]}>
                    {v.alert_enabled ? "ON" : "—"}
                  </Text>
                  <Text style={[styles.tdNum, { color: v.customer_msg_enabled ? "#059669" : "#9CA3AF" }]}>
                    {tplType ? (v.customer_msg_enabled ? "YES" : "OFF") : "—"}
                  </Text>
                  <Text style={[styles.tdNum, { color: v.auto_trigger ? "#1F4FBF" : "#9CA3AF" }]}>
                    {tplType && v.customer_msg_enabled ? (v.auto_trigger ? "AUTO" : "MAN") : "—"}
                  </Text>
                  <PhIcon
                    name={isExpanded ? "chevron-up" : "chevron-down"}
                    size={18} color="#9CA3AF"
                  />
                </TouchableOpacity>

                {/* Expanded edit drawer */}
                {isExpanded && (
                  <View style={styles.drawer}>
                    {/* SLA */}
                    <Text style={styles.fieldLabel}>SLA — Expected days in this stage</Text>
                    <View style={styles.slaRow}>
                      {[1, 2, 3, 5, 7, 10].map((n) => {
                        const active = Number(v.sla_days) === n;
                        return (
                          <TouchableOpacity
                            key={n}
                            style={[styles.slaChip, active && { backgroundColor: meta.color, borderColor: meta.color }]}
                            onPress={() => setStageVal(stage, { sla_days: n })}
                          >
                            <Text style={[styles.slaChipText, active && { color: "#fff" }]}>{n}d</Text>
                          </TouchableOpacity>
                        );
                      })}
                      <TextInput
                        style={styles.slaInput}
                        value={String(v.sla_days)}
                        onChangeText={(t) => {
                          const n = parseInt(t.replace(/\D/g, ""), 10);
                          if (!isNaN(n) && n > 0 && n < 365) setStageVal(stage, { sla_days: n });
                        }}
                        keyboardType="numeric"
                        maxLength={3}
                      />
                    </View>

                    {/* Alert */}
                    <View style={styles.dividerSm} />
                    <View style={styles.kvRow}>
                      <Text style={styles.kvLabel}>🔔 Internal Alert</Text>
                      <Switch
                        value={!!v.alert_enabled}
                        onValueChange={(b) => setStageVal(stage, { alert_enabled: b })}
                        trackColor={{ false: "#D1D5DB", true: meta.color }}
                        thumbColor="#fff"
                      />
                    </View>
                    {v.alert_enabled && (
                      <>
                        <Text style={styles.subLabel}>Priority</Text>
                        <View style={styles.optsRow}>
                          {PRIORITY_OPTS.map((p) => {
                            const active = v.alert_priority === p;
                            return (
                              <TouchableOpacity
                                key={p}
                                style={[styles.opt, active && { backgroundColor: priorityColor(p), borderColor: priorityColor(p) }]}
                                onPress={() => setStageVal(stage, { alert_priority: p })}
                              >
                                <Text style={[styles.optText, active && { color: "#fff" }]}>{p}</Text>
                              </TouchableOpacity>
                            );
                          })}
                        </View>
                        <Text style={styles.subLabel}>Channel</Text>
                        <View style={styles.optsRow}>
                          {CHANNEL_OPTS.map((c) => {
                            const active = v.alert_channel === c;
                            return (
                              <TouchableOpacity
                                key={c}
                                style={[styles.opt, active && { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" }]}
                                onPress={() => setStageVal(stage, { alert_channel: c })}
                              >
                                <Text style={[styles.optText, active && { color: "#fff" }]}>{c}</Text>
                              </TouchableOpacity>
                            );
                          })}
                        </View>
                        <Text style={styles.subLabel}>Recipients</Text>
                        <View style={styles.optsRow}>
                          {RECIPIENT_OPTS.map((r) => {
                            const arr: string[] = v.alert_recipients || [];
                            const active = arr.includes(r);
                            return (
                              <TouchableOpacity
                                key={r}
                                style={[styles.opt, active && { backgroundColor: "#059669", borderColor: "#059669" }]}
                                onPress={() => {
                                  const next = active ? arr.filter((x) => x !== r) : [...arr, r];
                                  setStageVal(stage, { alert_recipients: next });
                                }}
                              >
                                <Text style={[styles.optText, active && { color: "#fff" }]}>{r}</Text>
                              </TouchableOpacity>
                            );
                          })}
                        </View>
                      </>
                    )}

                    {/* Customer message */}
                    {tplType && (
                      <>
                        <View style={styles.dividerSm} />
                        <View style={styles.kvRow}>
                          <Text style={styles.kvLabel}>💬 Customer Message</Text>
                          <Switch
                            value={!!v.customer_msg_enabled}
                            onValueChange={(b) => setStageVal(stage, { customer_msg_enabled: b })}
                            trackColor={{ false: "#D1D5DB", true: "#10B981" }}
                            thumbColor="#fff"
                          />
                        </View>
                        {v.customer_msg_enabled && (
                          <>
                            <View style={[styles.kvRow, { marginTop: 8 }]}>
                              <Text style={styles.kvLabel}>⚡ Auto-trigger (background)</Text>
                              <Switch
                                value={!!v.auto_trigger}
                                onValueChange={(b) => setStageVal(stage, { auto_trigger: b })}
                                trackColor={{ false: "#D1D5DB", true: "#1F4FBF" }}
                                thumbColor="#fff"
                              />
                            </View>
                            <TouchableOpacity
                              style={styles.tplLink}
                              onPress={() => router.push("/settings/whatsapp-templates")}
                            >
                              <PhIcon name="open-outline" size={14} color="#4338CA" />
                              <Text style={styles.tplLinkText}>
                                Edit template variants ({tplType}) →
                              </Text>
                            </TouchableOpacity>
                          </>
                        )}
                      </>
                    )}

                    {/* Cooldown */}
                    <View style={styles.dividerSm} />
                    <Text style={styles.fieldLabel}>⏱ Cooldown — minimum gap between alerts</Text>
                    <View style={styles.optsRow}>
                      {[6, 12, 24, 48, 72].map((h) => {
                        const active = v.cooldown_hours === h;
                        return (
                          <TouchableOpacity
                            key={h}
                            style={[styles.opt, active && { backgroundColor: "#9333EA", borderColor: "#9333EA" }]}
                            onPress={() => setStageVal(stage, { cooldown_hours: h })}
                          >
                            <Text style={[styles.optText, active && { color: "#fff" }]}>{h}h</Text>
                          </TouchableOpacity>
                        );
                      })}
                    </View>

                    {/* Escalation summary (read-only in v1) */}
                    {v.escalation && v.escalation.length > 0 && (
                      <>
                        <View style={styles.dividerSm} />
                        <Text style={styles.fieldLabel}>📈 Escalation</Text>
                        {v.escalation.map((step: any, idx: number) => (
                          <Text key={idx} style={styles.escLine}>
                            • Day {Number(v.sla_days) + Number(step.day_after_sla || 0)} →
                            {" "}{(step.recipients || []).join(" + ") || "(no recipients)"}
                            {" "}({step.priority})
                          </Text>
                        ))}
                      </>
                    )}
                  </View>
                )}
              </View>
            );
          })}

          {/* Engine Settings (Phase G3) */}
          <View style={styles.engineBlock}>
            <View style={styles.engineHeader}>
              <Text style={styles.recipTitle}>⚙️ SLA Engine Settings</Text>
              <Switch
                value={globalEnabled}
                onValueChange={setGlobalEnabled}
                trackColor={{ false: "#D1D5DB", true: "#10B981" }}
                thumbColor="#fff"
              />
            </View>
            <Text style={styles.recipHint}>
              Master switch for the breach scanner. Turn off to silence all
              SLA alerts without touching individual stage configs.
            </Text>

            <Text style={styles.fieldLabel}>⏱️ Scan interval — how often the scanner runs</Text>
            <View style={styles.slaRow}>
              {[15, 30, 60, 120, 240].map((n) => {
                const active = scanInterval === n;
                return (
                  <TouchableOpacity
                    key={n}
                    style={[styles.slaChip, active && { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" }]}
                    onPress={() => setScanInterval(n)}
                  >
                    <Text style={[styles.slaChipText, active && { color: "#fff" }]}>
                      {n < 60 ? `${n}m` : `${n / 60}h`}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <Text style={styles.fieldLabel}>🛌 Default cooldown — gap before re-alerting on the same shipment</Text>
            <View style={styles.slaRow}>
              {[6, 12, 24, 48, 72, 168].map((n) => {
                const active = defaultCooldown === n;
                return (
                  <TouchableOpacity
                    key={n}
                    style={[styles.slaChip, active && { backgroundColor: "#B45309", borderColor: "#B45309" }]}
                    onPress={() => setDefaultCooldown(n)}
                  >
                    <Text style={[styles.slaChipText, active && { color: "#fff" }]}>
                      {n < 24 ? `${n}h` : `${n / 24}d`}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            <Text style={styles.fieldLabel}>📍 Where to show alerts (display channels)</Text>
            <View style={styles.kvRow}>
              <Text style={styles.kvLabel}>📋 Alerts list page</Text>
              <Switch
                value={chList}
                onValueChange={setChList}
                trackColor={{ false: "#D1D5DB", true: "#1F4FBF" }}
                thumbColor="#fff"
              />
            </View>
            <View style={styles.kvRow}>
              <Text style={styles.kvLabel}>🚨 Dashboard banner</Text>
              <Switch
                value={chBanner}
                onValueChange={setChBanner}
                trackColor={{ false: "#D1D5DB", true: "#DC2626" }}
                thumbColor="#fff"
              />
            </View>
            <View style={styles.kvRow}>
              <Text style={styles.kvLabel}>🔔 Push notification</Text>
              <Switch
                value={chPush}
                onValueChange={setChPush}
                trackColor={{ false: "#D1D5DB", true: "#10B981" }}
                thumbColor="#fff"
              />
            </View>
            <Text style={[styles.recipHint, { marginTop: 4 }]}>
              Push requires Expo notifications to be wired (Phase D).
              List + banner work today.
            </Text>

            <View style={styles.dividerSm} />
            <View style={styles.runRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.fieldLabel}>🔄 Last scan</Text>
                <Text style={styles.runMeta}>
                  {lastRun?.ran_at
                    ? `${new Date(lastRun.ran_at).toLocaleString()} · ${lastRun.alerts_raised || 0} new`
                    : "Not run yet"}
                </Text>
              </View>
              <TouchableOpacity
                style={[styles.runBtn, running && { opacity: 0.6 }]}
                onPress={runScanNow}
                disabled={running}
              >
                {running ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <PhIcon name="play" size={14} color="#fff" />
                    <Text style={styles.runBtnText}>Run scan now</Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
            <TouchableOpacity
              style={styles.viewAlertsBtn}
              onPress={() => router.push("/admin/sla-alerts" as any)}
            >
              <PhIcon name="alert-circle" size={14} color="#DC2626" />
              <Text style={styles.viewAlertsText}>View open SLA alerts →</Text>
            </TouchableOpacity>
          </View>

          {/* Recipient master list (admin global) */}
          <View style={styles.recipBlock}>
            <Text style={styles.recipTitle}>📞 Alert Recipients (global)</Text>
            <Text style={styles.recipHint}>
              These numbers / accounts receive every internal alert chosen above.
              Phone numbers can be plain 10-digit; we'll add the +91 prefix.
            </Text>
            <Text style={styles.fieldLabel}>👑 Admin number</Text>
            <TextInput
              value={adminNumber}
              onChangeText={setAdminNumber}
              placeholder="9876543210"
              placeholderTextColor="#9CA3AF"
              style={styles.bigInput}
              keyboardType="phone-pad"
              maxLength={15}
            />
            <Text style={styles.fieldLabel}>👥 Team numbers (comma or newline separated)</Text>
            <TextInput
              value={teamNumbersText}
              onChangeText={setTeamNumbersText}
              placeholder="9111111111, 9222222222"
              placeholderTextColor="#9CA3AF"
              style={[styles.bigInput, { minHeight: 70 }]}
              multiline
            />
          </View>

          <View style={{ height: 24 }} />
        </ScrollView>

        {/* Sticky save bar */}
        <View style={[styles.saveBar, !dirty && { opacity: 0.5 }]}>
          <TouchableOpacity
            style={styles.saveBtn}
            onPress={save}
            disabled={!dirty || saving}
          >
            {saving
              ? <ActivityIndicator color="#fff" />
              : <>
                  <PhIcon name="save-outline" size={15} color="#fff" />
                  <Text style={styles.saveBtnText}>
                    {dirty ? "Save changes" : "All saved"}
                  </Text>
                </>}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const priorityColor = (p: string) =>
  p === "high" ? "#DC2626" : p === "medium" ? "#F59E0B" : "#6B7280";

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F7F7F9" },

  headerCard: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  headerTitle: { fontSize: 16, fontWeight: "800", color: "#111827" },
  headerSub:   { fontSize: 12, color: "#6B7280", marginTop: 4, lineHeight: 17 },

  tableHeader: {
    flexDirection: "row", paddingHorizontal: 12, paddingVertical: 10,
    marginTop: 14,
  },
  thStage: { fontSize: 11, fontWeight: "800", color: "#6B7280", letterSpacing: 0.4, textTransform: "uppercase" },
  thNum:   { width: 44, fontSize: 11, fontWeight: "800", color: "#6B7280", textAlign: "center", letterSpacing: 0.4, textTransform: "uppercase" },
  thEdit:  { width: 18 },

  row: {
    backgroundColor: "#fff", borderRadius: 12, marginBottom: 8,
    borderWidth: 1, borderColor: "#E5E7EB", overflow: "hidden",
  },
  rowDirty: { borderColor: "#6B5BFF", borderWidth: 1.5 },
  rowMain: {
    flexDirection: "row", alignItems: "center",
    padding: 12, gap: 4,
  },
  stageCol: { flex: 1.6, flexDirection: "row", gap: 8, alignItems: "center" },
  stageIcon: { fontSize: 18 },
  stageName: { fontSize: 13.5, fontWeight: "800" },
  stageSub:  { fontSize: 10, color: "#9CA3AF", marginTop: 1 },
  dirtyTag:  { color: "#6B5BFF", fontSize: 10, fontWeight: "700" },
  tdNum:     { width: 44, fontSize: 11.5, fontWeight: "800", color: "#374151", textAlign: "center" },

  drawer: {
    paddingHorizontal: 14, paddingBottom: 14, paddingTop: 4,
    backgroundColor: "#FAFAFB", borderTopWidth: 1, borderTopColor: "#F3F4F6",
  },
  dividerSm: { height: 1, backgroundColor: "#E5E7EB", marginVertical: 10 },
  fieldLabel: { fontSize: 12, fontWeight: "800", color: "#111827", marginTop: 8, marginBottom: 6 },
  subLabel:   { fontSize: 11, fontWeight: "700", color: "#6B7280", marginTop: 8, marginBottom: 4 },

  slaRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, alignItems: "center" },
  slaChip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  slaChipText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  slaInput: {
    width: 50, height: 30, borderWidth: 1, borderColor: "#D1D5DB",
    borderRadius: 8, paddingHorizontal: 8, fontSize: 12, fontWeight: "700",
    color: "#111827", textAlign: "center", backgroundColor: "#fff",
  },

  kvRow: { flexDirection: "row", alignItems: "center" },
  kvLabel: { flex: 1, fontSize: 13, fontWeight: "700", color: "#111827" },

  optsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  opt: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  optText: { fontSize: 11, fontWeight: "700", color: "#374151", textTransform: "capitalize" },

  tplLink: {
    flexDirection: "row", alignItems: "center", gap: 4,
    marginTop: 8, paddingVertical: 8, paddingHorizontal: 10,
    backgroundColor: "#EEF2FF", borderRadius: 8,
    borderWidth: 1, borderColor: "#C7D2FE",
  },
  tplLinkText: { fontSize: 12, fontWeight: "700", color: "#4338CA" },

  escLine: { fontSize: 11.5, color: "#374151", marginTop: 2, lineHeight: 16 },

  recipBlock: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginTop: 14,
  },
  recipTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  recipHint:  { fontSize: 11, color: "#6B7280", marginTop: 4, lineHeight: 15, marginBottom: 6 },
  bigInput: {
    backgroundColor: "#F9FAFB", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, padding: 12, fontSize: 14, color: "#111827",
  },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    paddingHorizontal: 16, paddingVertical: 12,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 14, backgroundColor: "#10B981", borderRadius: 12,
  },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },

  engineBlock: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginTop: 14,
  },
  engineHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
  },
  runRow: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 4 },
  runMeta: { fontSize: 11.5, color: "#6B7280", marginTop: 2 },
  runBtn: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingVertical: 9, paddingHorizontal: 14,
    backgroundColor: "#1F4FBF", borderRadius: 999,
  },
  runBtnText: { color: "#fff", fontWeight: "800", fontSize: 12 },
  viewAlertsBtn: {
    flexDirection: "row", alignItems: "center", gap: 5,
    marginTop: 12, paddingVertical: 10, paddingHorizontal: 12,
    backgroundColor: "#FEF2F2", borderRadius: 8,
    borderWidth: 1, borderColor: "#FECACA",
    justifyContent: "center",
  },
  viewAlertsText: { color: "#DC2626", fontWeight: "800", fontSize: 12 },
});
