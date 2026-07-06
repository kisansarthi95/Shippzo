/* eslint-disable react/no-unescaped-entities */
/**
 * Auto SMS Sync configuration section — lives inside the Courier Edit
 * screen (`/app/courier/[id].tsx`). Renders four groups:
 *
 *   1. Enable toggle
 *   2. Sender ID patterns (chips + add)
 *   3. Tracking-number regex + live preview
 *   4. Status Rules table (SMS keyword → Internal Stage)
 *   5. Test Parse box (paste sample SMS → live result)
 *   6. Recent sync events (last 20 for this courier)
 *
 * Owns its own local state. Parent (courier edit screen) receives the
 * final values via `onChange` on every keystroke and includes them in
 * the save payload. Events + status choices are loaded on mount.
 *
 * Phase F5.0 — 2026-06-27. Replaces the standalone `/courier-sync`
 * screen; every courier now carries its own SMS scanning config.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity, ActivityIndicator,
  Alert, Platform,
} from "react-native";
import PhIcon from "../PhIcon";
import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";

export type SyncStatusRule = {
  keyword: string;
  canonical_status: string;
  shipment_status: string;
  whitelisted: boolean;
};

export type CourierSyncConfig = {
  auto_sync_enabled: boolean;
  auto_sync_sender_patterns: string[];
  auto_sync_tracking_regex: string;
  auto_sync_status_rules: SyncStatusRule[];
  auto_sync_case_sensitive: boolean;
};

type CanonicalChoice = {
  label: string;
  canonical: string;
  shipment: string;
  whitelisted: boolean;
};

type Props = {
  courierId: string;            // pass "" when courier not yet saved
  courierName: string;
  value: CourierSyncConfig;
  onChange: (patch: Partial<CourierSyncConfig>) => void;
};

const SAMPLE_SMS =
  "Item: EG350860840IN is out for delivery. Delivery will be attempted by - " +
  "Rajeshkumar Mohanlal Chauhan (BEAT_01) - on 2026-06-25 - IndiaPost";

export default function CourierAutoSyncSection({
  courierId,
  courierName,
  value,
  onChange,
}: Props) {
  // ── local UI state ───────────────────────────────────────────────
  const [choices, setChoices] = useState<CanonicalChoice[]>([]);
  const [newSender, setNewSender] = useState("");
  const [showRules, setShowRules] = useState(false);
  const [testSender, setTestSender] = useState("VA-INPOST-G");
  const [testText, setTestText] = useState(SAMPLE_SMS);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [choicePickerOpen, setChoicePickerOpen] = useState<number | null>(null);

  // ── boot: load status choices + recent events for this courier ───
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const chs = await Api.getCourierSyncStatusChoices();
        if (alive) setChoices(chs || []);
      } catch {
        // Non-fatal — dropdown just won't render pretty labels.
        if (alive) setChoices([]);
      }
    })();
    return () => { alive = false; };
  }, []);

  const loadEvents = useCallback(async () => {
    if (!courierId) return;   // new courier, no events yet
    setEventsLoading(true);
    try {
      const r = await Api.listCourierSyncEvents(courierId, 20);
      setEvents(r.events || []);
    } catch {
      setEvents([]);
    } finally {
      setEventsLoading(false);
    }
  }, [courierId]);

  useEffect(() => { void loadEvents(); }, [loadEvents]);

  // ── local mutators ───────────────────────────────────────────────
  const addSender = useCallback(() => {
    const s = newSender.trim().toUpperCase();
    if (!s) return;
    const cur = value.auto_sync_sender_patterns || [];
    if (cur.some((x) => x.toUpperCase() === s)) {
      Alert.alert("Duplicate", "This sender pattern is already in the list.");
      return;
    }
    onChange({ auto_sync_sender_patterns: [...cur, s] });
    setNewSender("");
  }, [newSender, value.auto_sync_sender_patterns, onChange]);

  const removeSender = useCallback((idx: number) => {
    const cur = value.auto_sync_sender_patterns || [];
    onChange({
      auto_sync_sender_patterns: cur.filter((_, i) => i !== idx),
    });
  }, [value.auto_sync_sender_patterns, onChange]);

  const setRuleField = useCallback((
    idx: number, field: keyof SyncStatusRule, v: any,
  ) => {
    const cur = value.auto_sync_status_rules || [];
    const next = cur.map((r, i) => (i === idx ? { ...r, [field]: v } : r));
    onChange({ auto_sync_status_rules: next });
  }, [value.auto_sync_status_rules, onChange]);

  const addRule = useCallback(() => {
    const cur = value.auto_sync_status_rules || [];
    onChange({
      auto_sync_status_rules: [
        ...cur,
        {
          keyword:          "",
          canonical_status: "In Transit",
          shipment_status:  "",
          whitelisted:      false,
        },
      ],
    });
    setShowRules(true);
  }, [value.auto_sync_status_rules, onChange]);

  const removeRule = useCallback((idx: number) => {
    const cur = value.auto_sync_status_rules || [];
    onChange({
      auto_sync_status_rules: cur.filter((_, i) => i !== idx),
    });
  }, [value.auto_sync_status_rules, onChange]);

  const applyChoice = useCallback((idx: number, choice: CanonicalChoice) => {
    setRuleField(idx, "canonical_status", choice.canonical);
    // Also seed shipment_status + whitelist from the choice so
    // operators don't have to hand-fill those.
    const cur = value.auto_sync_status_rules || [];
    const next = cur.map((r, i) => (i === idx ? {
      ...r,
      canonical_status: choice.canonical,
      shipment_status:  choice.shipment,
      whitelisted:      choice.whitelisted,
    } : r));
    onChange({ auto_sync_status_rules: next });
    setChoicePickerOpen(null);
  }, [value.auto_sync_status_rules, onChange, setRuleField]);

  // ── test parse ───────────────────────────────────────────────────
  const runTest = useCallback(async () => {
    if (!courierId) {
      Alert.alert("Save first", "Save the courier once, then come back to run the Test Parse.");
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await Api.courierSyncTestParse(courierId, {
        sender: testSender,
        text:   testText,
      });
      setTestResult(r);
    } catch (e: any) {
      setTestResult({ matched: false, reason: e?.message || "test_parse_failed" });
    } finally {
      setTesting(false);
    }
  }, [courierId, testSender, testText]);

  // ── tracking regex preview ───────────────────────────────────────
  const trackingPreview = useMemo(() => {
    const re = (value.auto_sync_tracking_regex || "").trim();
    if (!re) return { ok: null, sample: "" };
    try {
      const rx = new RegExp(re, "i");
      const m = rx.exec(testText);
      return { ok: !!m, sample: m ? m[0] : "" };
    } catch {
      return { ok: false, sample: "invalid regex" };
    }
  }, [value.auto_sync_tracking_regex, testText]);

  const senders = value.auto_sync_sender_patterns || [];
  const rules   = value.auto_sync_status_rules || [];
  const canonicalLabelFor = (c: string) =>
    choices.find((x) => x.canonical === c)?.label || c;

  // ── render ───────────────────────────────────────────────────────
  return (
    <View style={styles.wrap}>
      {/* Enable toggle */}
      <TouchableOpacity
        activeOpacity={0.85}
        onPress={() => onChange({ auto_sync_enabled: !value.auto_sync_enabled })}
        style={styles.enableRow}
        testID="auto-sync-toggle"
      >
        <View style={{ flex: 1, paddingRight: 12 }}>
          <Text style={styles.enableTitle}>Enable Auto SMS Sync</Text>
          <Text style={styles.enableSub}>
            When ON, incoming SMS from this courier's DLT sender IDs will
            auto-update the shipment status. Requires the Android device
            with the Shippzo app to have SMS Notification access granted.
          </Text>
        </View>
        <View
          style={[
            styles.toggleTrack,
            value.auto_sync_enabled && { backgroundColor: colors.primary },
          ]}
        >
          <View
            style={[
              styles.toggleThumb,
              value.auto_sync_enabled && { transform: [{ translateX: 18 }] },
            ]}
          />
        </View>
      </TouchableOpacity>

      {/* Sender patterns */}
      <View style={styles.subSection}>
        <Text style={styles.subTitle}>Sender ID Patterns</Text>
        <Text style={styles.hint}>
          Add the substring or regex that the courier's DLT SMS sender
          contains — e.g. <Text style={styles.mono}>INPOST</Text> for
          India Post (matches VA-INPOST-G, VK-INPOST-G, JD-INPOST-G,
          etc.), or <Text style={styles.mono}>NANDAN</Text> for Nandan
          Courier.
        </Text>
        <View style={styles.chipsRow}>
          {senders.map((s, i) => (
            <View key={`${s}-${i}`} style={styles.chip} testID={`sender-chip-${i}`}>
              <Text style={styles.chipText}>{s}</Text>
              <TouchableOpacity
                onPress={() => removeSender(i)}
                style={styles.chipRemove}
                hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
                testID={`sender-chip-remove-${i}`}
              >
                <PhIcon name="close" size={13} color="#fff" />
              </TouchableOpacity>
            </View>
          ))}
          {senders.length === 0 && (
            <Text style={styles.emptyHint}>No sender patterns yet — add one below.</Text>
          )}
        </View>
        <View style={styles.rowGap}>
          <TextInput
            value={newSender}
            onChangeText={setNewSender}
            placeholder="e.g. INPOST or NANDAN"
            placeholderTextColor="#9CA3AF"
            style={[styles.input, { flex: 1 }]}
            autoCapitalize="characters"
            onSubmitEditing={addSender}
            testID="sender-add-input"
          />
          <TouchableOpacity
            style={styles.addBtn}
            onPress={addSender}
            testID="sender-add-btn"
          >
            <PhIcon name="add" size={16} color="#fff" />
            <Text style={styles.addBtnText}>Add</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Tracking regex */}
      <View style={styles.subSection}>
        <Text style={styles.subTitle}>Tracking Number Regex</Text>
        <Text style={styles.hint}>
          Regex that matches this courier's AWB. Example: {" "}
          <Text style={styles.mono}>[A-Z]{"{2}"}\d{"{9}"}IN</Text> for
          India Post EMS/Speed Post (13 chars, "IN" suffix).
        </Text>
        <TextInput
          value={value.auto_sync_tracking_regex || ""}
          onChangeText={(t) => onChange({ auto_sync_tracking_regex: t })}
          placeholder="e.g. [A-Z]{2}\d{9}IN"
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
          style={[styles.input, styles.mono]}
          testID="tracking-regex-input"
        />
        {trackingPreview.ok === true && (
          <Text style={[styles.previewOk]} testID="regex-preview-ok">
            ✓ Matches sample: <Text style={styles.mono}>{trackingPreview.sample}</Text>
          </Text>
        )}
        {trackingPreview.ok === false && (
          <Text style={[styles.previewBad]} testID="regex-preview-bad">
            ✗ No match in sample text — try a different pattern.
          </Text>
        )}
      </View>

      {/* Status Rules */}
      <View style={styles.subSection}>
        <View style={styles.rowGap}>
          <Text style={[styles.subTitle, { flex: 1 }]}>
            Status Keyword → Internal Stage
          </Text>
          <TouchableOpacity
            onPress={() => setShowRules((v) => !v)}
            style={styles.toggleBtn}
            testID="rules-toggle-visibility"
          >
            <PhIcon
              name={showRules ? "chevron-up" : "chevron-down"}
              size={14}
              color={colors.primary}
            />
            <Text style={styles.toggleBtnText}>
              {showRules ? "Hide" : `Show (${rules.length})`}
            </Text>
          </TouchableOpacity>
        </View>
        <Text style={styles.hint}>
          Map the courier's SMS phrases (e.g. "out for delivery", "delivered")
          to our internal shipment stages. Order matters — negative phrasings
          like "could not be delivered" should come BEFORE the bare
          "delivered" rule.
        </Text>

        {showRules && rules.map((rule, idx) => (
          <View key={idx} style={styles.ruleCard} testID={`rule-${idx}`}>
            <View style={styles.ruleHeader}>
              <Text style={styles.ruleNum}>Rule {idx + 1}</Text>
              <TouchableOpacity
                onPress={() => removeRule(idx)}
                style={styles.ruleDeleteBtn}
                testID={`rule-remove-${idx}`}
              >
                <PhIcon name="trash-outline" size={14} color="#DC2626" />
              </TouchableOpacity>
            </View>
            <Text style={styles.ruleLabel}>SMS Keyword (substring or regex)</Text>
            <TextInput
              value={rule.keyword}
              onChangeText={(t) => setRuleField(idx, "keyword", t)}
              placeholder='e.g. "out for delivery" or "delivered"'
              placeholderTextColor="#9CA3AF"
              autoCapitalize="none"
              autoCorrect={false}
              style={[styles.input, styles.mono, { marginTop: 4 }]}
              testID={`rule-keyword-${idx}`}
            />
            <Text style={[styles.ruleLabel, { marginTop: 10 }]}>Internal Stage</Text>
            <TouchableOpacity
              onPress={() => setChoicePickerOpen(choicePickerOpen === idx ? null : idx)}
              style={styles.pickerBtn}
              testID={`rule-stage-picker-${idx}`}
            >
              <Text style={styles.pickerBtnText}>
                {canonicalLabelFor(rule.canonical_status) || "Choose a stage…"}
              </Text>
              <PhIcon
                name={choicePickerOpen === idx ? "chevron-up" : "chevron-down"}
                size={14}
                color={colors.text}
              />
            </TouchableOpacity>
            {choicePickerOpen === idx && (
              <View style={styles.pickerDropdown}>
                {choices.map((c) => (
                  <TouchableOpacity
                    key={c.canonical}
                    onPress={() => applyChoice(idx, c)}
                    style={[
                      styles.pickerItem,
                      rule.canonical_status === c.canonical && styles.pickerItemActive,
                    ]}
                    testID={`rule-stage-choice-${idx}-${c.canonical}`}
                  >
                    <Text style={styles.pickerItemText}>{c.label}</Text>
                    {c.whitelisted && (
                      <Text style={styles.pickerItemBadge}>updates status</Text>
                    )}
                  </TouchableOpacity>
                ))}
              </View>
            )}
          </View>
        ))}

        <TouchableOpacity
          style={styles.addRuleBtn}
          onPress={addRule}
          testID="add-rule-btn"
        >
          <PhIcon name="add-circle-outline" size={16} color={colors.primary} />
          <Text style={styles.addRuleBtnText}>Add Status Rule</Text>
        </TouchableOpacity>
      </View>

      {/* Test Parse */}
      <View style={styles.subSection}>
        <Text style={styles.subTitle}>Test Parse</Text>
        <Text style={styles.hint}>
          Paste a real SMS from this courier to verify the config catches
          it. Save the courier first — the test uses your saved rules.
        </Text>
        <TextInput
          value={testSender}
          onChangeText={setTestSender}
          placeholder="Sender ID (e.g. VA-INPOST-G)"
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
          style={styles.input}
          testID="test-sender-input"
        />
        <TextInput
          value={testText}
          onChangeText={setTestText}
          placeholder="SMS body"
          placeholderTextColor="#9CA3AF"
          multiline
          style={[styles.input, { height: 90, textAlignVertical: "top", paddingTop: 10 }]}
          testID="test-text-input"
        />
        <TouchableOpacity
          onPress={runTest}
          style={styles.testBtn}
          disabled={testing || !courierId}
          testID="test-parse-btn"
        >
          {testing
            ? <ActivityIndicator color="#fff" size="small" />
            : (
              <>
                <PhIcon name="flash" size={14} color="#fff" />
                <Text style={styles.testBtnText}>
                  {courierId ? "Run Test Parse" : "Save courier first"}
                </Text>
              </>
            )}
        </TouchableOpacity>

        {testResult && (
          <View
            style={[
              styles.testResult,
              testResult.matched ? styles.testResultOk : styles.testResultBad,
            ]}
            testID="test-parse-result"
          >
            <Text style={styles.testResultTitle}>
              {testResult.matched ? "✓ Matched" : "✗ Not matched"}
            </Text>
            {testResult.matched ? (
              <>
                <Text style={styles.testResultLine}>
                  <Text style={styles.testResultKey}>AWB:</Text>{" "}
                  <Text style={styles.mono}>{testResult.tracking_id}</Text>
                </Text>
                <Text style={styles.testResultLine}>
                  <Text style={styles.testResultKey}>Canonical:</Text>{" "}
                  {testResult.canonical_status}
                </Text>
                <Text style={styles.testResultLine}>
                  <Text style={styles.testResultKey}>Will set status:</Text>{" "}
                  {testResult.shipment_status || "— (audit-only)"}
                </Text>
                <Text style={styles.testResultLine}>
                  <Text style={styles.testResultKey}>Matched phrase:</Text>{" "}
                  <Text style={styles.mono}>"{testResult.matched_phrase}"</Text>
                </Text>
              </>
            ) : (
              <Text style={styles.testResultLine}>
                Reason: <Text style={styles.mono}>{testResult.reason}</Text>
              </Text>
            )}
          </View>
        )}
      </View>

      {/* Recent Events */}
      <View style={styles.subSection}>
        <View style={styles.rowGap}>
          <Text style={[styles.subTitle, { flex: 1 }]}>Recent Sync Events</Text>
          <TouchableOpacity
            onPress={loadEvents}
            style={styles.toggleBtn}
            testID="events-refresh-btn"
          >
            <PhIcon name="refresh" size={14} color={colors.primary} />
            <Text style={styles.toggleBtnText}>Refresh</Text>
          </TouchableOpacity>
        </View>
        {eventsLoading && (
          <ActivityIndicator color={colors.primary} style={{ marginTop: 12 }} />
        )}
        {!eventsLoading && events.length === 0 && (
          <Text style={styles.emptyHint}>
            No sync events yet. When the Android app forwards an SMS,
            it will show up here.
          </Text>
        )}
        {!eventsLoading && events.map((ev, i) => (
          <View key={ev.id || i} style={styles.eventCard} testID={`event-${i}`}>
            <View style={styles.rowGap}>
              <Text style={[
                styles.eventStatus,
                ev.matched ? styles.eventStatusOk : styles.eventStatusBad,
              ]}>
                {ev.matched ? (ev.action || "matched") : "ignored"}
              </Text>
              <Text style={styles.eventTs}>
                {(ev.received_at || "").slice(0, 19).replace("T", " ")}
              </Text>
            </View>
            {ev.tracking_id ? (
              <Text style={styles.eventTracking}>
                {ev.tracking_id}{" "}
                {ev.canonical_status && (
                  <Text style={styles.eventCanonical}>→ {ev.canonical_status}</Text>
                )}
              </Text>
            ) : null}
            <Text style={styles.eventSender} numberOfLines={1}>
              from {ev.sender || "(no sender)"}
            </Text>
            {ev.raw_text ? (
              <Text style={styles.eventBody} numberOfLines={2}>
                {ev.raw_text}
              </Text>
            ) : null}
            {!ev.matched && ev.reason ? (
              <Text style={styles.eventReason}>reason: {ev.reason}</Text>
            ) : null}
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap:            { marginTop: 4 },
  subSection:      { marginTop: 16 },
  subTitle:        { fontSize: 13, fontWeight: "700", color: colors.text, marginBottom: 6 },
  hint:            { fontSize: 11, color: "#64748B", lineHeight: 16, marginBottom: 6 },
  mono:            { fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) || "monospace" },
  input:           { backgroundColor: "#F8FAFC", borderColor: "#E5E7EB", borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, fontSize: 13, color: colors.text },
  rowGap:          { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
  addBtn:          { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: colors.primary, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8 },
  addBtnText:      { color: "#fff", fontSize: 12, fontWeight: "700" },

  enableRow:       { flexDirection: "row", alignItems: "center", backgroundColor: "#F0F9FF", borderColor: "#BAE6FD", borderWidth: 1, borderRadius: 10, padding: 12, marginTop: 4 },
  enableTitle:     { fontSize: 13, fontWeight: "700", color: "#075985" },
  enableSub:       { fontSize: 11, color: "#0369A1", marginTop: 2, lineHeight: 15 },
  toggleTrack:     { width: 44, height: 26, backgroundColor: "#D1D5DB", borderRadius: 13, justifyContent: "center", padding: 3 },
  toggleThumb:     { width: 20, height: 20, backgroundColor: "#fff", borderRadius: 10, shadowColor: "#000", shadowOpacity: 0.2, shadowOffset: { width: 0, height: 1 }, shadowRadius: 2, elevation: 2 },

  chipsRow:        { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 4 },
  chip:            { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: "#4338CA", paddingLeft: 10, paddingRight: 4, paddingVertical: 4, borderRadius: 12 },
  chipText:        { color: "#fff", fontSize: 11, fontWeight: "700" },
  chipRemove:      { backgroundColor: "rgba(0,0,0,0.25)", borderRadius: 8, padding: 2, marginLeft: 2 },
  emptyHint:       { fontSize: 11, color: "#94A3B8", fontStyle: "italic", marginVertical: 6 },

  previewOk:       { color: "#059669", fontSize: 11, marginTop: 4 },
  previewBad:      { color: "#DC2626", fontSize: 11, marginTop: 4 },

  toggleBtn:       { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4 },
  toggleBtnText:   { color: colors.primary, fontSize: 12, fontWeight: "700" },

  ruleCard:        { backgroundColor: "#F8FAFC", borderColor: "#E5E7EB", borderWidth: 1, borderRadius: 10, padding: 10, marginTop: 6 },
  ruleHeader:      { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 4 },
  ruleNum:         { fontSize: 11, fontWeight: "700", color: "#64748B" },
  ruleDeleteBtn:   { padding: 6, borderRadius: 6, backgroundColor: "#FEF2F2" },
  ruleLabel:       { fontSize: 11, color: "#64748B", marginTop: 6 },
  pickerBtn:       { flexDirection: "row", alignItems: "center", backgroundColor: "#fff", borderColor: "#E5E7EB", borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, marginTop: 4 },
  pickerBtnText:   { flex: 1, fontSize: 13, color: colors.text },
  pickerDropdown:  { backgroundColor: "#fff", borderColor: "#E5E7EB", borderWidth: 1, borderRadius: 8, marginTop: 4, overflow: "hidden" },
  pickerItem:      { flexDirection: "row", alignItems: "center", paddingHorizontal: 12, paddingVertical: 10, borderBottomColor: "#F1F5F9", borderBottomWidth: 1, gap: 8 },
  pickerItemActive:{ backgroundColor: "#EEF2FF" },
  pickerItemText:  { flex: 1, fontSize: 13, color: colors.text },
  pickerItemBadge: { fontSize: 10, color: "#059669", backgroundColor: "#ECFDF5", paddingHorizontal: 6, paddingVertical: 2, borderRadius: 6 },
  addRuleBtn:      { flexDirection: "row", alignItems: "center", gap: 6, justifyContent: "center", backgroundColor: "#EEF2FF", borderColor: "#C7D2FE", borderWidth: 1, borderRadius: 8, paddingVertical: 10, marginTop: 8 },
  addRuleBtnText:  { color: colors.primary, fontSize: 12, fontWeight: "700" },

  testBtn:         { flexDirection: "row", alignItems: "center", gap: 6, justifyContent: "center", backgroundColor: colors.primary, borderRadius: 8, paddingVertical: 12, marginTop: 8 },
  testBtnText:     { color: "#fff", fontSize: 13, fontWeight: "700" },
  testResult:      { borderRadius: 8, padding: 10, marginTop: 8, borderWidth: 1 },
  testResultOk:    { backgroundColor: "#ECFDF5", borderColor: "#86EFAC" },
  testResultBad:   { backgroundColor: "#FEF2F2", borderColor: "#FCA5A5" },
  testResultTitle: { fontSize: 13, fontWeight: "700", color: colors.text, marginBottom: 4 },
  testResultLine:  { fontSize: 11, color: colors.text, marginTop: 2 },
  testResultKey:   { fontWeight: "700", color: "#0F172A" },

  eventCard:       { backgroundColor: "#F8FAFC", borderColor: "#E5E7EB", borderWidth: 1, borderRadius: 8, padding: 10, marginTop: 6 },
  eventStatus:     { flex: 1, fontSize: 11, fontWeight: "700", textTransform: "uppercase" },
  eventStatusOk:   { color: "#065F46" },
  eventStatusBad:  { color: "#7C2D12" },
  eventTs:         { fontSize: 10, color: "#64748B" },
  eventTracking:   { fontSize: 12, fontWeight: "700", color: colors.text, marginTop: 2 },
  eventCanonical:  { color: colors.primary },
  eventSender:     { fontSize: 10, color: "#64748B", marginTop: 1 },
  eventBody:       { fontSize: 11, color: colors.text, marginTop: 4, lineHeight: 15 },
  eventReason:     { fontSize: 10, color: "#DC2626", marginTop: 2, fontStyle: "italic" },
});
