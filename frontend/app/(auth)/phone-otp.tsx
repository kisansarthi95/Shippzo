/**
 * Phone OTP Login / Signup screen
 * --------------------------------
 *
 * Single 2-step screen that powers BOTH login-via-OTP and
 * signup-via-OTP. Linked from /(auth)/login.tsx and /(auth)/signup.tsx.
 *
 * Step 1 — phone entry
 *   • User types a phone number (10-digit Indian format default).
 *   • POST /api/auth/otp/request → backend dispatches OTP via the
 *     active WhatsApp provider (FlowConnect today, swappable to
 *     WATI/Interakt/Meta later without any frontend change).
 *   • UI never sees the OTP itself — backend returns only delivery
 *     metadata + `expires_in` for the countdown.
 *
 * Step 2 — OTP entry
 *   • 6-digit input + resend countdown.
 *   • For signup (mode=signup) we collect name + business name on
 *     this step so the verify response can create the user record
 *     in one round-trip.
 *   • POST /api/auth/otp/verify → returns `{ mode, token, user }`.
 *     The auth context persists the session and AuthGate routes the
 *     user to the dashboard.
 *
 * Design contract
 *   • The screen is PROVIDER-AGNOSTIC. It only knows the WhatsApp
 *     channel exists — never imports FlowConnect/WATI/etc.
 *   • Existing email/password flows are untouched; this screen
 *     coexists alongside them as a parallel authentication path.
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useLocalSearchParams } from "expo-router";
import PhIcon from "../../components/PhIcon";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";
import { useAuth } from "../../lib/auth";
import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";

type Step = "phone" | "otp";

export default function PhoneOtpScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ mode?: string }>();
  const initialMode: "login" | "signup" =
    params.mode === "signup" ? "signup" : "login";

  const { signInWithOtp } = useAuth();

  const [mode, setMode] = useState<"login" | "signup">(initialMode);
  const [step, setStep] = useState<Step>("phone");

  // Step-1 state
  const [phone, setPhone] = useState("");
  const [name, setName] = useState("");
  const [shopName, setShopName] = useState("");
  const [requesting, setRequesting] = useState(false);

  // Step-2 state
  const [otp, setOtp] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [maskedPhone, setMaskedPhone] = useState("");
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [resending, setResending] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup timer on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // Reset the countdown to a fresh window.
  const startCountdown = useCallback((seconds: number) => {
    if (timerRef.current) clearInterval(timerRef.current);
    setSecondsLeft(seconds);
    timerRef.current = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
  }, []);

  // ─── Step 1 — request OTP ────────────────────────────────────────
  const phoneDigits = phone.replace(/\D/g, "");
  const phoneValid = phoneDigits.length >= 10;
  const signupExtraValid =
    mode === "login" || (!!name.trim() && !!shopName.trim());
  const canRequest = phoneValid && signupExtraValid && !requesting;

  const requestOtp = async (forResend = false) => {
    if (forResend) {
      setResending(true);
    } else {
      setRequesting(true);
    }
    try {
      // Send with country code if user didn't include one — backend
      // also normalises but doing it client-side gives nicer logs.
      const normalised = phoneDigits.startsWith("91")
        ? `+${phoneDigits}`
        : phoneDigits.length === 10
        ? `+91${phoneDigits}`
        : `+${phoneDigits}`;

      const res = await Api.requestPhoneOtp(normalised, mode);
      setMaskedPhone(res.phone || normalised);
      startCountdown(Math.min(res.expires_in || 60, 300));
      if (!forResend) {
        setOtp("");
        setStep("otp");
      } else {
        Alert.alert(
          "OTP resent",
          `We've sent a fresh OTP to your WhatsApp on ${res.phone || normalised}.`,
        );
      }
    } catch (err: any) {
      const status = err?.response?.status;
      const detail =
        err?.response?.data?.detail || err?.message || "Please try again";
      if (status === 429) {
        Alert.alert("Please wait", detail);
      } else if (status === 400) {
        Alert.alert("Invalid phone", detail);
      } else {
        Alert.alert("Could not send OTP", detail);
      }
    } finally {
      setRequesting(false);
      setResending(false);
    }
  };

  // ─── Step 2 — verify OTP ─────────────────────────────────────────
  const otpDigits = otp.replace(/\D/g, "");
  const canVerify = otpDigits.length >= 4 && !verifying;

  const verify = async () => {
    const normalised = phoneDigits.startsWith("91")
      ? `+${phoneDigits}`
      : phoneDigits.length === 10
      ? `+91${phoneDigits}`
      : `+${phoneDigits}`;
    setVerifying(true);
    try {
      const result = await signInWithOtp({
        phone: normalised,
        otp: otpDigits,
        event_type: mode,
        name: mode === "signup" ? name.trim() : undefined,
        shop_name: mode === "signup" ? shopName.trim() : undefined,
      });
      // AuthGate in the root layout will route the now-authenticated
      // user to the appropriate landing screen automatically.
      if (result.mode === "signup") {
        // Friendly heads-up on the first ever sign-in via OTP.
        setTimeout(() => {
          Alert.alert(
            "Welcome to Shippzo!",
            "Your account has been created. You can add an email + password later from Settings for backup access.",
          );
        }, 400);
      }
    } catch (err: any) {
      const detail =
        err?.response?.data?.detail || err?.message || "Wrong or expired OTP";
      Alert.alert("Verification failed", detail);
    } finally {
      setVerifying(false);
    }
  };

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.brand}>
            <BrandHeaderAnimator variant="login" />
          </View>

          <View style={styles.card}>
            {/* Mode toggle — login vs signup, only meaningful at step 1 */}
            {step === "phone" ? (
              <View style={styles.modeRow}>
                <TouchableOpacity
                  testID="phone-otp-mode-login"
                  style={[
                    styles.modeBtn,
                    mode === "login" && styles.modeBtnActive,
                  ]}
                  onPress={() => setMode("login")}
                  disabled={requesting}
                >
                  <PhIcon
                    name="log-in"
                    size={14}
                    color={mode === "login" ? "#fff" : "#1F4FBF"}
                  />
                  <Text
                    style={[
                      styles.modeBtnTxt,
                      mode === "login" && { color: "#fff" },
                    ]}
                  >
                    Log in
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="phone-otp-mode-signup"
                  style={[
                    styles.modeBtn,
                    mode === "signup" && styles.modeBtnActive,
                  ]}
                  onPress={() => setMode("signup")}
                  disabled={requesting}
                >
                  <PhIcon
                    name="rocket"
                    size={14}
                    color={mode === "signup" ? "#fff" : "#1F4FBF"}
                  />
                  <Text
                    style={[
                      styles.modeBtnTxt,
                      mode === "signup" && { color: "#fff" },
                    ]}
                  >
                    Sign up
                  </Text>
                </TouchableOpacity>
              </View>
            ) : null}

            <View style={styles.headerRow}>
              <View style={styles.whatsBadge}>
                <PhIcon name="chatbubble-ellipses" size={14} color="#16A34A" />
                <Text style={styles.whatsBadgeTxt}>WhatsApp OTP</Text>
              </View>
            </View>

            <Text style={styles.title}>
              {step === "phone"
                ? mode === "login"
                  ? "Log in with WhatsApp OTP"
                  : "Sign up with WhatsApp OTP"
                : "Enter the OTP"}
            </Text>
            <Text style={styles.subtitle}>
              {step === "phone"
                ? "We'll send a 6-digit code to your WhatsApp."
                : `Sent to ${maskedPhone} via WhatsApp.`}
            </Text>

            {step === "phone" ? (
              <>
                <Text style={[styles.label, { marginTop: 16 }]}>
                  Mobile number
                </Text>
                <View style={styles.phoneRow}>
                  <View style={styles.ccBox}>
                    <Text style={styles.ccText}>+91</Text>
                  </View>
                  <TextInput
                    testID="phone-otp-input"
                    value={phone}
                    onChangeText={(t) => setPhone(t.replace(/[^\d+]/g, ""))}
                    placeholder="10-digit mobile number"
                    keyboardType="phone-pad"
                    maxLength={15}
                    style={[styles.input, { flex: 1 }]}
                    placeholderTextColor="#94A3B8"
                  />
                </View>

                {mode === "signup" ? (
                  <>
                    <Text style={[styles.label, { marginTop: 12 }]}>
                      Your name
                    </Text>
                    <TextInput
                      testID="phone-otp-name"
                      value={name}
                      onChangeText={setName}
                      placeholder="Mahek"
                      style={styles.input}
                      placeholderTextColor="#94A3B8"
                    />

                    <Text style={[styles.label, { marginTop: 12 }]}>
                      Business name
                    </Text>
                    <TextInput
                      testID="phone-otp-shop"
                      value={shopName}
                      onChangeText={setShopName}
                      placeholder="Mahek Creations"
                      style={styles.input}
                      placeholderTextColor="#94A3B8"
                    />
                  </>
                ) : null}

                <TouchableOpacity
                  testID="phone-otp-send"
                  disabled={!canRequest}
                  onPress={() => requestOtp(false)}
                  style={[
                    styles.primaryBtn,
                    !canRequest && { opacity: 0.5 },
                  ]}
                >
                  {requesting ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.primaryBtnText}>Send OTP</Text>
                  )}
                </TouchableOpacity>

                <Text style={styles.note}>
                  By continuing, you agree to receive an OTP on WhatsApp from
                  Shippzo. Standard data rates may apply.
                </Text>
              </>
            ) : (
              <>
                <Text style={[styles.label, { marginTop: 16 }]}>OTP</Text>
                <TextInput
                  testID="phone-otp-code"
                  value={otp}
                  onChangeText={(t) => setOtp(t.replace(/\D/g, "").slice(0, 6))}
                  placeholder="6-digit code"
                  keyboardType="number-pad"
                  maxLength={6}
                  style={[styles.input, styles.otpInput]}
                  placeholderTextColor="#94A3B8"
                  autoFocus
                />

                <TouchableOpacity
                  testID="phone-otp-verify"
                  disabled={!canVerify}
                  onPress={verify}
                  style={[
                    styles.primaryBtn,
                    !canVerify && { opacity: 0.5 },
                  ]}
                >
                  {verifying ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.primaryBtnText}>
                      {mode === "signup"
                        ? "Verify & Create account"
                        : "Verify & Log in"}
                    </Text>
                  )}
                </TouchableOpacity>

                <View style={styles.resendRow}>
                  {secondsLeft > 0 ? (
                    <Text style={styles.resendTimer}>
                      Resend OTP in 0:{String(secondsLeft).padStart(2, "0")}
                    </Text>
                  ) : (
                    <TouchableOpacity
                      testID="phone-otp-resend"
                      onPress={() => requestOtp(true)}
                      disabled={resending}
                      style={styles.resendBtn}
                    >
                      {resending ? (
                        <ActivityIndicator size="small" color={colors.primary} />
                      ) : (
                        <>
                          <PhIcon
                            name="arrow-clockwise"
                            size={14}
                            color={colors.primary}
                          />
                          <Text style={styles.resendTxt}>Resend OTP</Text>
                        </>
                      )}
                    </TouchableOpacity>
                  )}
                </View>

                <TouchableOpacity
                  testID="phone-otp-edit-phone"
                  onPress={() => {
                    if (timerRef.current) clearInterval(timerRef.current);
                    setSecondsLeft(0);
                    setOtp("");
                    setStep("phone");
                  }}
                  style={styles.editPhoneBtn}
                >
                  <PhIcon name="edit" size={14} color="#475569" />
                  <Text style={styles.editPhoneTxt}>
                    Wrong number? Edit
                  </Text>
                </TouchableOpacity>
              </>
            )}

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <TouchableOpacity
              testID="phone-otp-back-to-email"
              style={styles.altBtn}
              onPress={() =>
                router.replace(
                  mode === "signup"
                    ? "/(auth)/signup"
                    : ("/(auth)/login" as any),
                )
              }
              activeOpacity={0.85}
            >
              <PhIcon name="mail" size={16} color={colors.primary} />
              <Text style={styles.altBtnText}>
                {mode === "signup"
                  ? "Sign up with email instead"
                  : "Log in with email instead"}
              </Text>
            </TouchableOpacity>
          </View>

          <Text style={styles.hint}>
            Your data is private. Each shop keeps its own shipments, couriers
            and settings.
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  scroll: { flexGrow: 1, justifyContent: "center", padding: 20 },
  brand: { alignItems: "center", marginBottom: 24 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-start",
    marginBottom: 4,
  },
  whatsBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "#DCFCE7",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
  },
  whatsBadgeTxt: {
    color: "#15803D",
    fontWeight: "800",
    fontSize: 11,
    letterSpacing: 0.4,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
    marginTop: 8,
  },
  subtitle: { fontSize: 13, color: colors.textMuted, marginTop: 4 },
  label: {
    fontSize: 12,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 6,
    letterSpacing: 0.4,
  },
  input: {
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 46,
    fontSize: 15,
    color: colors.text,
  },
  otpInput: {
    letterSpacing: 6,
    fontWeight: "800",
    textAlign: "center",
    fontSize: 18,
  },
  phoneRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  ccBox: {
    paddingHorizontal: 12,
    height: 46,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    backgroundColor: "#F8FAFC",
    justifyContent: "center",
    alignItems: "center",
  },
  ccText: { color: colors.text, fontWeight: "800", fontSize: 14 },
  primaryBtn: {
    marginTop: 18,
    height: 50,
    borderRadius: 12,
    backgroundColor: colors.primary,
    justifyContent: "center",
    alignItems: "center",
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  note: {
    marginTop: 12,
    fontSize: 11,
    color: colors.textMuted,
    lineHeight: 16,
    textAlign: "center",
  },
  resendRow: {
    marginTop: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  resendBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 14,
  },
  resendTxt: { color: colors.primary, fontWeight: "800", fontSize: 13 },
  resendTimer: { color: colors.textMuted, fontSize: 12, fontWeight: "700" },
  editPhoneBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "center",
    paddingVertical: 8,
    marginTop: 2,
  },
  editPhoneTxt: { color: "#475569", fontSize: 12, fontWeight: "700" },
  divider: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginTop: 18,
    marginBottom: 14,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: {
    fontSize: 11,
    fontWeight: "800",
    color: "#94A3B8",
    letterSpacing: 2,
  },
  altBtn: {
    height: 48,
    borderRadius: 12,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.primary,
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  altBtnText: { color: colors.primary, fontWeight: "800", fontSize: 14 },
  hint: {
    marginTop: 16,
    textAlign: "center",
    fontSize: 11.5,
    color: colors.textMuted,
    paddingHorizontal: 12,
  },
  // Mode toggle
  modeRow: {
    flexDirection: "row",
    backgroundColor: "#F3F4F6",
    borderRadius: 10,
    padding: 4,
    marginBottom: 16,
    gap: 4,
  },
  modeBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 9,
    borderRadius: 8,
  },
  modeBtnActive: { backgroundColor: "#1F4FBF" },
  modeBtnTxt: {
    fontSize: 12,
    fontWeight: "800",
    color: "#1F4FBF",
    letterSpacing: 0.3,
  },
});
