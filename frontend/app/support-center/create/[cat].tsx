/**
 * Phase-21 — Support Center → Create Request → STEP 2 / 3 / 4
 *
 * Three-phase wizard inside a single screen:
 *   step “form”    → Issue Details form (order id, problem, etc.)
 *   step “review”  → Review & Submit summary
 *   step “done”    → "Request Submitted!" success with SHP-XXXX
 *
 * Category arrives as a URL param `cat` (one of the keys in
 * CATEGORIES). The success screen exposes "View My Requests" and
 * "Back to Home" CTAs.
 *
 * Phase-21 scope note: screenshot + screen-recording uploads are
 * STUBBED for now (placeholder cards) — enabling them requires the
 * image-picker / file-picker plumbing which can land in a follow-up.
 * Backend already accepts the `screenshot_b64` / `recording_b64`
 * fields so flipping the stubs on is a frontend-only change.
 */
import React, { useMemo, useState } from "react";
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import Constants from "expo-constants";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import PhIcon from "../../../components/PhIcon";
import { colors } from "../../../lib/theme";
import { Api } from "../../../lib/api";
import { CATEGORIES, SupportCategoryKey } from "../create";

const WHEN_OPTIONS = [
  { k: "today",      label: "Today" },
  { k: "yesterday",  label: "Yesterday" },
  { k: "this_week",  label: "This week" },
  { k: "older",      label: "Earlier" },
];

/**
 * Categories where the "Courier Name" field is contextually
 * relevant. For unrelated categories (login, wallet, WhatsApp,
 * app bug, feature request, etc.) the field is hidden to keep
 * the form short and on-topic.
 */
const COURIER_RELEVANT_CATEGORIES: SupportCategoryKey[] = [
  "label_print",
  "order_input",
];

export default function CreateTicketForm() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { cat } = useLocalSearchParams<{ cat: string }>();
  const category = useMemo(
    () => CATEGORIES.find((c) => c.k === (cat as SupportCategoryKey)) || CATEGORIES[CATEGORIES.length - 1],
    [cat],
  );

  const [step, setStep] = useState<"form" | "review" | "done">("form");
  const [courierName, setCourierName] = useState("");
  const [orderId, setOrderId]         = useState("");
  const [problem, setProblem]         = useState("");
  const [when, setWhen]               = useState<string>("today");
  const [saving, setSaving]           = useState(false);
  const [createdTicketNumber, setCreatedTicketNumber] = useState<string>("");
  const [createdTicketId, setCreatedTicketId]         = useState<string>("");

  /** Courier Name input is only relevant for label/courier and
   *  order-input issues. For other categories (login, wallet,
   *  WhatsApp, app bug, feature requests, etc.) we hide the
   *  field so the form stays focused. */
  const showCourierField = COURIER_RELEVANT_CATEGORIES.includes(category.k);

  const deviceInfo = useMemo(() => ({
    app_version: Constants.expoConfig?.version || "",
    platform:    Platform.OS,
    os_version:  String(Platform.Version || ""),
  }), []);

  const onSubmit = async () => {
    if (problem.trim().length < 5) {
      Alert.alert("Description required", "Please describe the issue in a bit more detail.");
      return;
    }
    setSaving(true);
    try {
      const ticket = await Api.supportCreateTicket({
        title:         category.title,
        description:   problem.trim(),
        category:      category.k,
        courier_name:  showCourierField ? courierName.trim() : "",
        order_id:      orderId.trim(),
        issue_started: when,
        device_info:   deviceInfo,
      });
      setCreatedTicketNumber(ticket.ticket_number || `SHP-${ticket.id.slice(0, 4)}`);
      setCreatedTicketId(ticket.id);
      setStep("done");
    } catch (e: any) {
      Alert.alert(
        "Could not submit",
        e?.response?.data?.detail || e?.message || "Please try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  // ────── Step DONE — Success ────────────────────────────────────
  if (step === "done") {
    return (
      <View style={[styles.root, { paddingTop: insets.top + 8 }]}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.successWrap}>
          <View style={styles.successCircle}>
            <PhIcon name="checkmark" size={48} color="#fff" />
          </View>
          <Text style={styles.successTitle}>Request Submitted!</Text>
          <Text style={styles.successSub}>
            Your support request has been submitted successfully.
          </Text>

          <View style={styles.idCard}>
            <Text style={styles.idCardLbl}>Your Request ID</Text>
            <Text style={styles.idCardVal}>{createdTicketNumber}</Text>
          </View>

          <Text style={styles.successFootnote}>
            We will get back to you via email as soon as possible.
          </Text>
        </View>

        <View style={[styles.barCol, { paddingBottom: insets.bottom + 14 }]}>
          <TouchableOpacity
            style={styles.submitBtn}
            activeOpacity={0.85}
            onPress={() => router.replace("/support-center/my-tickets" as any)}
          >
            <Text style={styles.submitTxt}>View My Requests</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.backHomeBtn}
            activeOpacity={0.85}
            onPress={() => router.replace("/(tabs)" as any)}
          >
            <Text style={styles.backHomeTxt}>Back to Home</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // ────── Step REVIEW — summary card ─────────────────────────────
  if (step === "review") {
    return (
      <View style={styles.root}>
        <Stack.Screen
          options={{ title: "Review & Submit", headerTitleStyle: { fontWeight: "800" }, headerShadowVisible: false }}
        />
        <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 96 }}>
          <View style={styles.summaryCard}>
            <Text style={styles.summarySection}>Issue Summary</Text>
            <SummaryRow lbl="Category" val={category.title} />
            {courierName.trim() ? <SummaryRow lbl="Courier Name" val={courierName} /> : null}
            {orderId.trim()     ? <SummaryRow lbl="Order ID" val={orderId} /> : null}
            <SummaryRow lbl="Description" val={problem} multiline />
            <SummaryRow lbl="Issue Start Time" val={WHEN_OPTIONS.find((w) => w.k === when)?.label || when} />
          </View>
          <View style={[styles.summaryCard, { marginTop: 12 }]}>
            <Text style={styles.summarySection}>Technical Information</Text>
            <SummaryRow lbl="App Version" val={deviceInfo.app_version || "—"} />
            <SummaryRow lbl="Platform"    val={deviceInfo.platform || "—"} />
            <SummaryRow lbl="OS Version"  val={deviceInfo.os_version || "—"} />
          </View>
        </ScrollView>
        <View style={[styles.bar, { paddingBottom: insets.bottom + 10 }]}>
          <TouchableOpacity
            style={[styles.submitBtn, saving && { opacity: 0.6 }]}
            activeOpacity={0.85}
            disabled={saving}
            onPress={onSubmit}
            testID="ticket-submit"
          >
            <Text style={styles.submitTxt}>{saving ? "Submitting…" : "Submit Request"}</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  // ────── Step FORM — Issue Details ──────────────────────────────
  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1, backgroundColor: "#F4F5F7" }}
    >
      <Stack.Screen
        options={{ title: category.title, headerTitleStyle: { fontWeight: "800" }, headerShadowVisible: false }}
      />
      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 96 }}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={styles.sectionLbl}>Please provide details about the issue</Text>

        {showCourierField && (
          <>
            <Text style={styles.fieldLbl}>Courier Name</Text>
            <View style={styles.inputWrap}>
              <TextInput
                value={courierName}
                onChangeText={setCourierName}
                placeholder="Optional… (e.g. Delhivery)"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
                maxLength={80}
              />
            </View>
          </>
        )}

        <Text style={styles.fieldLbl}>Order ID (If Applicable)</Text>
        <View style={styles.inputWrap}>
          <TextInput
            value={orderId}
            onChangeText={setOrderId}
            placeholder="Enter order ID"
            placeholderTextColor="#9CA3AF"
            style={styles.input}
            maxLength={80}
          />
        </View>

        <Text style={styles.fieldLbl}>Describe the problem</Text>
        <View style={[styles.inputWrap, { minHeight: 130, alignItems: "stretch" }]}>
          <TextInput
            value={problem}
            onChangeText={setProblem}
            placeholder="Please describe the issue in detail…"
            placeholderTextColor="#9CA3AF"
            style={[styles.input, { minHeight: 110, textAlignVertical: "top" }]}
            multiline
            maxLength={5000}
          />
        </View>
        <Text style={styles.charCount}>{problem.length} / 5000</Text>

        <Text style={styles.fieldLbl}>When did this issue start?</Text>
        <View style={styles.chips}>
          {WHEN_OPTIONS.map((w) => {
            const active = w.k === when;
            return (
              <TouchableOpacity
                key={w.k}
                onPress={() => setWhen(w.k)}
                style={[styles.whenChip, active && { backgroundColor: colors.primary, borderColor: colors.primary }]}
                activeOpacity={0.85}
              >
                <Text style={[styles.whenChipTxt, active && { color: "#fff" }]}>{w.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* Phase-21 — attachment slots stubbed for now. */}
        <View style={styles.uploadCard}>
          <PhIcon name="cloud-upload-outline" size={22} color={colors.primary} />
          <Text style={styles.uploadTitle}>Upload Screenshot (Optional)</Text>
          <Text style={styles.uploadSub}>Coming soon — PNG/JPG up to 5MB.</Text>
        </View>
        <View style={[styles.uploadCard, { marginTop: 10 }]}>
          <PhIcon name="play" size={22} color={colors.primary} />
          <Text style={styles.uploadTitle}>Upload Screen Recording (Optional)</Text>
          <Text style={styles.uploadSub}>Coming soon — MP4 up to 20MB.</Text>
        </View>
      </ScrollView>

      <View style={[styles.bar, { paddingBottom: insets.bottom + 10 }]}>
        <TouchableOpacity
          style={styles.submitBtn}
          activeOpacity={0.85}
          onPress={() => {
            if (problem.trim().length < 5) {
              Alert.alert("Description required", "Please describe the issue in a bit more detail.");
              return;
            }
            setStep("review");
          }}
          testID="ticket-next"
        >
          <Text style={styles.submitTxt}>Next: Review</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

function SummaryRow({ lbl, val, multiline }: { lbl: string; val: string; multiline?: boolean }) {
  return (
    <View style={{ marginTop: 10 }}>
      <Text style={styles.summaryRowLbl}>{lbl}</Text>
      <Text style={[styles.summaryRowVal, multiline && { lineHeight: 19 }]}>{val || "—"}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },
  sectionLbl: { fontSize: 14, color: "#475569", marginBottom: 14, lineHeight: 19 },

  fieldLbl: { fontSize: 12.5, fontWeight: "800", color: "#0F172A", marginTop: 14, marginBottom: 6 },
  inputWrap: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 12, paddingVertical: Platform.OS === "ios" ? 12 : 6,
  },
  input: { fontSize: 14, color: "#0F172A", paddingVertical: 6 },
  charCount: { fontSize: 11, color: "#94A3B8", textAlign: "right", marginTop: 4 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  whenChip: {
    paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 999, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  whenChipTxt: { fontSize: 12.5, fontWeight: "700", color: "#334155" },

  uploadCard: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB", borderStyle: "dashed",
    paddingVertical: 18, paddingHorizontal: 14, marginTop: 14,
    alignItems: "center",
  },
  uploadTitle: { fontSize: 13, fontWeight: "800", color: "#0F172A", marginTop: 8 },
  uploadSub: { fontSize: 11, color: "#94A3B8", marginTop: 2 },

  bar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 16, paddingTop: 10,
  },
  barCol: {
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 16, paddingTop: 10,
  },
  submitBtn: {
    backgroundColor: colors.primary, borderRadius: 12,
    paddingVertical: 14, alignItems: "center",
  },
  submitTxt: { color: "#fff", fontSize: 15, fontWeight: "800" },
  backHomeBtn: { alignItems: "center", paddingVertical: 14 },
  backHomeTxt: { color: colors.primary, fontSize: 14, fontWeight: "800" },

  // Review screen
  summaryCard: {
    backgroundColor: "#fff", borderRadius: 14,
    padding: 14,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.04)", elevation: 1,
  },
  summarySection: { fontSize: 13.5, fontWeight: "800", color: colors.primary, marginBottom: 4 },
  summaryRowLbl: { fontSize: 11.5, color: "#94A3B8", fontWeight: "700" },
  summaryRowVal: { fontSize: 14, fontWeight: "600", color: "#0F172A", marginTop: 2 },

  // Success screen
  successWrap: { flex: 1, alignItems: "center", paddingTop: 60, paddingHorizontal: 24 },
  successCircle: {
    width: 96, height: 96, borderRadius: 48,
    backgroundColor: "#16A34A",
    alignItems: "center", justifyContent: "center",
    boxShadow: "0px 6px 16px rgba(22,163,74,0.35)", elevation: 6,
  },
  successTitle: { fontSize: 22, fontWeight: "800", color: "#0F172A", marginTop: 22 },
  successSub: { fontSize: 13.5, color: "#64748B", marginTop: 8, textAlign: "center", lineHeight: 19 },
  idCard: {
    width: "100%", marginTop: 28,
    backgroundColor: "#fff", borderRadius: 14,
    paddingVertical: 18, paddingHorizontal: 18,
    alignItems: "center",
    boxShadow: "0px 1px 4px rgba(0,0,0,0.05)", elevation: 1,
  },
  idCardLbl: { fontSize: 12, color: "#64748B", fontWeight: "700" },
  idCardVal: { fontSize: 24, fontWeight: "900", color: "#0F172A", marginTop: 6, letterSpacing: 1 },
  successFootnote: { fontSize: 12.5, color: "#94A3B8", marginTop: 18, textAlign: "center" },
});
