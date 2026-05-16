/**
 * Phase-21 — Support Center → Create Request → STEP 2 / 3 / 4
 *
 * Three-phase wizard inside a single screen:
 *   step "form"    → Issue Details form (order id, problem, etc.)
 *   step "review"  → Review & Submit summary
 *   step "done"    → "Request Submitted!" success with SHP-XXXX
 *
 * Category arrives as a URL param `cat` (one of the keys in
 * CATEGORIES). The success screen exposes "View My Requests" and
 * "Back to Home" CTAs.
 *
 * 2026-05-16 — Screenshot + screen-recording uploads enabled.
 *   • Screenshot → `expo-image-picker` (camera or gallery), 5 MB cap,
 *     PNG/JPG only, base64-encoded for the existing
 *     `screenshot_b64` payload field.
 *   • Screen recording → `expo-document-picker` (MP4), 20 MB cap,
 *     read via `expo-file-system` into base64 for the
 *     `recording_b64` payload field.
 */
import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
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
import * as ImagePicker from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system";

import PhIcon from "../../../components/PhIcon";
import { colors } from "../../../lib/theme";
import { Api } from "../../../lib/api";
import { CATEGORIES, SupportCategoryKey } from "../create";

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;   // 5 MB
const MAX_VIDEO_BYTES = 20 * 1024 * 1024;  // 20 MB

const fmtSize = (bytes: number): string => {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
};

const WHEN_OPTIONS = [
  { k: "today",      label: "Today" },
  { k: "yesterday",  label: "Yesterday" },
  { k: "this_week",  label: "This week" },
  { k: "older",      label: "Earlier" },
];

/**
 * Per-category field visibility. Each category only shows the
 * inputs that make sense for that workflow — keeping the form
 * focused and reducing noise. This config is the single source
 * of truth used by both the form view and the review summary.
 */
type FieldConfig = {
  courierName: boolean;
  orderId:     boolean;
  whenStarted: boolean;
  screenshot:  boolean;
  recording:   boolean;
};

const FIELD_CONFIG: Record<SupportCategoryKey, FieldConfig> = {
  account_login:   { courierName: false, orderId: false, whenStarted: true,  screenshot: true, recording: true  },
  plan_wallet:     { courierName: false, orderId: false, whenStarted: true,  screenshot: true, recording: true  },
  label_print:     { courierName: true,  orderId: true,  whenStarted: true,  screenshot: true, recording: true  },
  order_input:     { courierName: true,  orderId: true,  whenStarted: true,  screenshot: true, recording: true  },
  whatsapp:        { courierName: false, orderId: false, whenStarted: true,  screenshot: true, recording: true  },
  app_bug:         { courierName: false, orderId: false, whenStarted: true,  screenshot: true, recording: true  },
  feature_request: { courierName: false, orderId: false, whenStarted: false, screenshot: true, recording: false },
  other:           { courierName: false, orderId: false, whenStarted: true,  screenshot: true, recording: true  },
};

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

  // Attachment state — populated by the image / document pickers
  // below. The `*B64` strings are full data-URIs (so the receiving
  // side can render them directly); `*Name` / `*Size` drive the
  // preview chip UI. `*Busy` shows a spinner inside the upload card
  // while the picker is fetching / encoding the file.
  const [screenshotB64, setScreenshotB64]     = useState<string>("");
  const [screenshotName, setScreenshotName]   = useState<string>("");
  const [screenshotSize, setScreenshotSize]   = useState<number>(0);
  const [screenshotBusy, setScreenshotBusy]   = useState<boolean>(false);

  const [recordingB64, setRecordingB64]       = useState<string>("");
  const [recordingName, setRecordingName]     = useState<string>("");
  const [recordingSize, setRecordingSize]     = useState<number>(0);
  const [recordingBusy, setRecordingBusy]     = useState<boolean>(false);

  /** Per-category field visibility — drives which inputs are
   *  rendered (and which values are submitted) so each category
   *  only collects the data points that actually matter for it. */
  const fields = FIELD_CONFIG[category.k] || FIELD_CONFIG.other;

  const deviceInfo = useMemo(() => ({
    app_version: Constants.expoConfig?.version || "",
    platform:    Platform.OS,
    os_version:  String(Platform.Version || ""),
  }), []);

  // ────── Screenshot picker ───────────────────────────────────────
  // Shows a small action-sheet so the user can grab a fresh photo
  // (e.g. of the printer / error dialog) or pick one already saved
  // in the gallery. Both paths go through expo-image-picker and
  // come back base64-encoded so we can stick the result straight
  // into the existing `screenshot_b64` payload field.
  const pickScreenshot = () => {
    Alert.alert(
      "Add Screenshot",
      "Choose a source",
      [
        { text: "Take Photo",       onPress: () => doPickImage("camera") },
        { text: "Pick from Gallery", onPress: () => doPickImage("gallery") },
        { text: "Cancel", style: "cancel" },
      ],
    );
  };

  const doPickImage = async (src: "camera" | "gallery") => {
    try {
      if (src === "camera") {
        const perm = await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) {
          Alert.alert("Permission needed", "Please allow camera access from Settings to attach a photo.");
          return;
        }
      } else {
        const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) {
          Alert.alert("Permission needed", "Please allow photo library access from Settings to attach a screenshot.");
          return;
        }
      }
      setScreenshotBusy(true);
      const opts: ImagePicker.ImagePickerOptions = {
        // expo-image-picker v17 uses the string array form. Cast to
        // any to keep TS happy across minor version drift.
        mediaTypes: ["images"] as any,
        base64: true,
        quality: 0.7,
        allowsEditing: false,
      };
      const res = src === "camera"
        ? await ImagePicker.launchCameraAsync(opts)
        : await ImagePicker.launchImageLibraryAsync(opts);
      if (res.canceled || !res.assets?.[0]) return;
      const asset = res.assets[0];
      // Prefer the OS-reported size; fall back to estimating from
      // the base64 string length (≈ bytes * 4/3).
      const size = (asset as any).fileSize ?? Math.floor(((asset.base64 || "").length * 3) / 4);
      if (size > MAX_IMAGE_BYTES) {
        Alert.alert(
          "Image too large",
          `Please pick a PNG/JPG under 5 MB. Selected size: ${fmtSize(size)}.`,
        );
        return;
      }
      const ext  = (asset.uri.split(".").pop() || "jpg").toLowerCase();
      const mime = ext === "png" ? "image/png" : "image/jpeg";
      setScreenshotB64(`data:${mime};base64,${asset.base64 || ""}`);
      setScreenshotName((asset as any).fileName || `screenshot.${ext}`);
      setScreenshotSize(size);
    } catch (e: any) {
      Alert.alert("Couldn't attach image", e?.message || "Please try again.");
    } finally {
      setScreenshotBusy(false);
    }
  };

  // ────── Screen-recording picker ─────────────────────────────────
  // Uses expo-document-picker (the system file-picker) so the user
  // can attach an MP4 saved via the OS screen recorder. We then
  // read it into base64 via expo-file-system to push through the
  // existing `recording_b64` field.
  const pickRecording = async () => {
    try {
      setRecordingBusy(true);
      const res = await DocumentPicker.getDocumentAsync({
        type: ["video/mp4", "video/*"],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (res.canceled || !res.assets?.[0]) return;
      const asset = res.assets[0];
      const reportedSize = (asset as any).size ?? 0;
      if (reportedSize && reportedSize > MAX_VIDEO_BYTES) {
        Alert.alert(
          "Video too large",
          `Please pick an MP4 under 20 MB. Selected size: ${fmtSize(reportedSize)}.`,
        );
        return;
      }
      const b64 = await FileSystem.readAsStringAsync(asset.uri, {
        encoding: "base64" as any,
      });
      const actualSize = Math.floor((b64.length * 3) / 4);
      if (actualSize > MAX_VIDEO_BYTES) {
        Alert.alert(
          "Video too large",
          `Please pick an MP4 under 20 MB. Selected size: ${fmtSize(actualSize)}.`,
        );
        return;
      }
      setRecordingB64(`data:video/mp4;base64,${b64}`);
      setRecordingName(asset.name || "recording.mp4");
      setRecordingSize(reportedSize || actualSize);
    } catch (e: any) {
      Alert.alert("Couldn't attach video", e?.message || "Please try again.");
    } finally {
      setRecordingBusy(false);
    }
  };

  const removeScreenshot = () => { setScreenshotB64(""); setScreenshotName(""); setScreenshotSize(0); };
  const removeRecording  = () => { setRecordingB64("");  setRecordingName("");  setRecordingSize(0);  };

  const onSubmit = async () => {
    if (problem.trim().length < 5) {
      Alert.alert("Description required", "Please describe the issue in a bit more detail.");
      return;
    }
    setSaving(true);
    try {
      const ticket = await Api.supportCreateTicket({
        title:          category.title,
        description:    problem.trim(),
        category:       category.k,
        courier_name:   fields.courierName ? courierName.trim() : "",
        order_id:       fields.orderId     ? orderId.trim()     : "",
        issue_started:  fields.whenStarted ? when               : "",
        screenshot_b64: fields.screenshot  ? screenshotB64      : "",
        recording_b64:  fields.recording   ? recordingB64       : "",
        device_info:    deviceInfo,
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
            {fields.courierName && courierName.trim() ? <SummaryRow lbl="Courier Name" val={courierName} /> : null}
            {fields.orderId     && orderId.trim()     ? <SummaryRow lbl="Order ID"     val={orderId} />     : null}
            <SummaryRow lbl="Description" val={problem} multiline />
            {fields.whenStarted ? (
              <SummaryRow
                lbl="Issue Start Time"
                val={WHEN_OPTIONS.find((w) => w.k === when)?.label || when}
              />
            ) : null}
            {fields.screenshot && screenshotB64 ? (
              <SummaryRow
                lbl="Screenshot"
                val={`${screenshotName || "image"} (${fmtSize(screenshotSize)})`}
              />
            ) : null}
            {fields.recording && recordingB64 ? (
              <SummaryRow
                lbl="Screen Recording"
                val={`${recordingName || "video.mp4"} (${fmtSize(recordingSize)})`}
              />
            ) : null}
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

        {fields.courierName && (
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

        {fields.orderId && (
          <>
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
          </>
        )}

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

        {fields.whenStarted && (
          <>
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
          </>
        )}

        {/* Screenshot attachment — PNG/JPG, max 5 MB. */}
        {fields.screenshot && (
          screenshotB64 ? (
            <View style={styles.attachedCard}>
              <Image
                source={{ uri: screenshotB64 }}
                style={styles.attachedThumb}
                resizeMode="cover"
              />
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Text style={styles.attachedName} numberOfLines={1}>
                  {screenshotName || "Screenshot attached"}
                </Text>
                <Text style={styles.attachedMeta}>
                  {fmtSize(screenshotSize)} · Tap × to remove
                </Text>
              </View>
              <TouchableOpacity
                onPress={removeScreenshot}
                hitSlop={10}
                style={styles.removeBtn}
                testID="remove-screenshot"
              >
                <PhIcon name="close" size={16} color="#64748B" />
              </TouchableOpacity>
            </View>
          ) : (
            <TouchableOpacity
              style={styles.uploadCard}
              activeOpacity={0.85}
              onPress={pickScreenshot}
              disabled={screenshotBusy}
              testID="pick-screenshot"
            >
              {screenshotBusy ? (
                <ActivityIndicator color={colors.primary} />
              ) : (
                <PhIcon name="cloud-upload-outline" size={22} color={colors.primary} />
              )}
              <Text style={styles.uploadTitle}>Upload Screenshot (Optional)</Text>
              <Text style={styles.uploadSub}>
                {screenshotBusy ? "Processing…" : "Tap to choose — PNG/JPG up to 5 MB"}
              </Text>
            </TouchableOpacity>
          )
        )}

        {/* Screen-recording attachment — MP4, max 20 MB. */}
        {fields.recording && (
          recordingB64 ? (
            <View style={[styles.attachedCard, { marginTop: 10 }]}>
              <View style={[styles.attachedThumb, { backgroundColor: "#EEF2FF", alignItems: "center", justifyContent: "center" }]}>
                <PhIcon name="play" size={26} color={colors.primary} />
              </View>
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Text style={styles.attachedName} numberOfLines={1}>
                  {recordingName || "Recording attached"}
                </Text>
                <Text style={styles.attachedMeta}>
                  {fmtSize(recordingSize)} · Tap × to remove
                </Text>
              </View>
              <TouchableOpacity
                onPress={removeRecording}
                hitSlop={10}
                style={styles.removeBtn}
                testID="remove-recording"
              >
                <PhIcon name="close" size={16} color="#64748B" />
              </TouchableOpacity>
            </View>
          ) : (
            <TouchableOpacity
              style={[styles.uploadCard, { marginTop: 10 }]}
              activeOpacity={0.85}
              onPress={pickRecording}
              disabled={recordingBusy}
              testID="pick-recording"
            >
              {recordingBusy ? (
                <ActivityIndicator color={colors.primary} />
              ) : (
                <PhIcon name="play" size={22} color={colors.primary} />
              )}
              <Text style={styles.uploadTitle}>Upload Screen Recording (Optional)</Text>
              <Text style={styles.uploadSub}>
                {recordingBusy ? "Encoding…" : "Tap to choose an MP4 up to 20 MB"}
              </Text>
            </TouchableOpacity>
          )
        )}
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

  // Selected-attachment chip — shown once the user has picked a file.
  attachedCard: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingVertical: 10, paddingHorizontal: 10, marginTop: 14,
    flexDirection: "row", alignItems: "center",
  },
  attachedThumb: {
    width: 48, height: 48, borderRadius: 8,
    backgroundColor: "#F1F5F9",
  },
  attachedName: { fontSize: 13, fontWeight: "700", color: "#0F172A" },
  attachedMeta: { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  removeBtn: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: "#F1F5F9",
    alignItems: "center", justifyContent: "center",
    marginLeft: 8,
  },

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
