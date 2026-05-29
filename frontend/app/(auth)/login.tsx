import React, { useState, useEffect, useRef, useMemo } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";
import GoogleSignInButton from "../../components/GoogleSignInButton";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";

/**
 * Login screen — supports THREE auth paths from a single "Email or
 * Mobile Number" field:
 *
 *   1. Owner / Admin email + password  (legacy)
 *   2. Team-member email + password    (legacy)
 *   3. Mobile-OTP login                (new — inline on the SAME
 *                                       screen, no extra route)
 *
 * Smart detection
 * ---------------
 * The single identifier field is the user's primary handle. We detect
 * what they typed by stripping non-digits:
 *   • If the result has 10+ digits AND the original has no "@" → treat
 *     as a mobile number → swap the password field for a Send OTP CTA.
 *   • Otherwise → email path → password field stays visible.
 *
 * OTP flow (mobile)
 * -----------------
 * Tapping "Send OTP" calls /api/auth/otp/request and transitions the
 * card into an inline OTP-entry sub-state (still the SAME login.tsx
 * screen — no navigation). A 6-digit input + countdown + resend appear
 * directly below the identifier field. Tapping "Verify & Log in"
 * calls signInWithOtp() which exchanges the code for a JWT via
 * /api/auth/otp/verify and the rest of the app sees a standard
 * authenticated session.
 *
 * The owner/team toggle is hidden the moment the identifier looks
 * like a phone number — team-member login is email-only by design.
 */
export default function LoginScreen() {
  const { signIn, signInTeam, signInWithOtp } = useAuth();
  const router = useRouter();

  // Phase B+C — toggle between owner login (default) and team-member
  // login. Both paths post to different endpoints; the rest of the
  // form stays identical so we don't bloat the UI.
  const [mode, setMode] = useState<"owner" | "team">("owner");

  // Single identifier field — accepts email OR mobile number. The form
  // morphs based on what the user types.
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPwd, setShowPwd] = useState(false);

  // ─── Smart detection (memoised, recomputes every keystroke) ──────
  // A "phone" is any input whose digit-stripped form is at least
  // 10 chars AND has no "@" in the raw string. Everything else is
  // treated as email (or empty).
  const detection = useMemo(() => {
    const raw = identifier.trim();
    const digits = raw.replace(/\D/g, "");
    const hasAt = raw.includes("@");
    const isPhone = !hasAt && digits.length >= 10;
    return { isPhone, digits, raw };
  }, [identifier]);

  // ─── OTP sub-flow state (inline on the same screen) ──────────────
  // `otpSent` flips the bottom half of the card from "Send OTP" CTA
  // to "Enter OTP" + "Verify & Log in". `secondsLeft` drives the
  // resend countdown so users don't hammer the WhatsApp dispatcher.
  const [otpSent, setOtpSent] = useState(false);
  const [otp, setOtp] = useState("");
  const [sendingOtp, setSendingOtp] = useState(false);
  const [verifyingOtp, setVerifyingOtp] = useState(false);
  const [maskedPhone, setMaskedPhone] = useState("");
  const [secondsLeft, setSecondsLeft] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup any running countdown when the screen unmounts.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  // If the user edits the identifier back to non-phone form (or
  // clears the OTP step entirely), reset the OTP sub-flow so we
  // don't leave stale state hanging around behind the password UI.
  useEffect(() => {
    if (!detection.isPhone && otpSent) {
      setOtpSent(false);
      setOtp("");
      setSecondsLeft(0);
      if (timerRef.current) clearInterval(timerRef.current);
    }
  }, [detection.isPhone, otpSent]);

  const startCountdown = (seconds: number) => {
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
  };

  // Normalise a 10-digit Indian number to E.164. Bigger digit strings
  // are assumed to already include the country code.
  const normalisePhone = (digits: string) =>
    digits.length === 10 ? `+91${digits}` : digits.startsWith("91") ? `+${digits}` : `+${digits}`;

  // ─── Mobile OTP — Send OTP ───────────────────────────────────────
  const sendOtp = async (forResend = false) => {
    setSendingOtp(true);
    try {
      const phone = normalisePhone(detection.digits);
      const res = await Api.requestPhoneOtp(phone, "login");
      setMaskedPhone(res.phone || phone);
      startCountdown(Math.min(res.expires_in || 60, 300));
      if (!forResend) {
        setOtp("");
        setOtpSent(true);
      } else {
        Alert.alert("OTP resent", `Sent a fresh OTP to your WhatsApp on ${res.phone || phone}.`);
      }
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || "Please try again";
      if (status === 429) Alert.alert("Please wait", detail);
      else if (status === 400) Alert.alert("Invalid phone", detail);
      else Alert.alert("Could not send OTP", detail);
    } finally {
      setSendingOtp(false);
    }
  };

  // ─── Mobile OTP — Verify & Log in ────────────────────────────────
  const verifyOtp = async () => {
    setVerifyingOtp(true);
    try {
      const phone = normalisePhone(detection.digits);
      await signInWithOtp({
        phone,
        otp: otp.replace(/\D/g, ""),
        event_type: "login",
      });
      // AuthGate handles the redirect to the dashboard automatically.
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || "Wrong or expired OTP";
      Alert.alert("Verification failed", detail);
    } finally {
      setVerifyingOtp(false);
    }
  };

  // ─── Email + password (legacy paths) ─────────────────────────────
  const submit = async () => {
    const e = identifier.trim().toLowerCase();
    if (!e || !password) {
      Alert.alert("Missing fields", "Enter email and password");
      return;
    }
    setBusy(true);
    try {
      if (mode === "team") {
        await signInTeam(e, password);
      } else {
        await signIn(e, password);
      }
    } catch (err: any) {
      Alert.alert(
        "Login failed",
        err?.response?.data?.detail || err?.message || "Please try again",
      );
    } finally {
      setBusy(false);
    }
  };

  const canSendOtp = detection.isPhone && !sendingOtp;
  const otpDigits = otp.replace(/\D/g, "");
  const canVerifyOtp = otpDigits.length >= 4 && !verifyingOtp;

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <BrandHeaderAnimator variant="login" />
          </View>

          <View style={styles.card}>
            {/* Phase B+C — owner / team-member toggle. Hidden when the
                user is typing a phone number because team-member login
                is email-only by design. */}
            {!detection.isPhone ? (
              <View style={styles.modeRow}>
                <TouchableOpacity
                  style={[styles.modeBtn, mode === "owner" && styles.modeBtnActive]}
                  onPress={() => setMode("owner")}
                >
                  <PhIcon
                    name="storefront"
                    size={14}
                    color={mode === "owner" ? "#fff" : "#1F4FBF"}
                  />
                  <Text style={[styles.modeBtnTxt, mode === "owner" && { color: "#fff" }]}>
                    Owner / Admin
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.modeBtn, mode === "team" && styles.modeBtnActive]}
                  onPress={() => setMode("team")}
                >
                  <PhIcon
                    name="people"
                    size={14}
                    color={mode === "team" ? "#fff" : "#1F4FBF"}
                  />
                  <Text style={[styles.modeBtnTxt, mode === "team" && { color: "#fff" }]}>
                    Team Member
                  </Text>
                </TouchableOpacity>
              </View>
            ) : null}

            <Text style={styles.label}>Email or Mobile Number</Text>
            <TextInput
              testID="login-email"
              value={identifier}
              onChangeText={setIdentifier}
              placeholder="you@example.com  or  9876543210"
              autoCapitalize="none"
              autoCorrect={false}
              // Email keyboard by default; phone-pad once the input
              // looks like digits. Keeps numeric entry comfortable
              // on mobile while not blocking email typing.
              keyboardType={detection.isPhone ? "phone-pad" : "email-address"}
              style={styles.input}
              placeholderTextColor="#94A3B8"
              editable={!otpSent}
            />
            {detection.isPhone ? (
              <Text style={styles.detectionHint}>
                We&apos;ll send a 6-digit OTP to your WhatsApp.
              </Text>
            ) : null}

            {/* Email / password path */}
            {!detection.isPhone ? (
              <>
                <Text style={[styles.label, { marginTop: 12 }]}>Password</Text>
                <View style={styles.pwRow}>
                  <TextInput
                    testID="login-password"
                    value={password}
                    onChangeText={setPassword}
                    placeholder="••••••••"
                    secureTextEntry={!showPwd}
                    style={[styles.input, { flex: 1 }]}
                    placeholderTextColor="#94A3B8"
                  />
                  <TouchableOpacity style={styles.eyeBtn} onPress={() => setShowPwd((v) => !v)}>
                    <PhIcon name={showPwd ? "eye-off" : "eye"} size={20} color="#64748B" />
                  </TouchableOpacity>
                </View>

                <TouchableOpacity
                  testID="login-submit"
                  disabled={busy}
                  onPress={submit}
                  style={[styles.primaryBtn, busy && { opacity: 0.6 }]}
                >
                  {busy
                    ? <ActivityIndicator color="#fff" />
                    : <Text style={styles.primaryBtnText}>Log in</Text>}
                </TouchableOpacity>

                <TouchableOpacity
                  testID="login-forgot"
                  onPress={() => router.push("/(auth)/forgot-password" as any)}
                  style={styles.forgotBtn}
                >
                  <Text style={styles.forgotTxt}>Forgot password?</Text>
                </TouchableOpacity>
              </>
            ) : (
              // Mobile / OTP path
              <>
                {!otpSent ? (
                  <TouchableOpacity
                    testID="login-send-otp"
                    disabled={!canSendOtp}
                    onPress={() => sendOtp(false)}
                    style={[styles.primaryBtn, !canSendOtp && { opacity: 0.5 }]}
                  >
                    {sendingOtp
                      ? <ActivityIndicator color="#fff" />
                      : <Text style={styles.primaryBtnText}>Send OTP</Text>}
                  </TouchableOpacity>
                ) : (
                  <>
                    <Text style={[styles.label, { marginTop: 12 }]}>
                      Enter OTP {maskedPhone ? `(sent to ${maskedPhone})` : ""}
                    </Text>
                    <TextInput
                      testID="login-otp"
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
                      testID="login-verify-otp"
                      disabled={!canVerifyOtp}
                      onPress={verifyOtp}
                      style={[styles.primaryBtn, !canVerifyOtp && { opacity: 0.5 }]}
                    >
                      {verifyingOtp
                        ? <ActivityIndicator color="#fff" />
                        : <Text style={styles.primaryBtnText}>Verify &amp; Log in</Text>}
                    </TouchableOpacity>

                    <View style={styles.resendRow}>
                      {secondsLeft > 0 ? (
                        <Text style={styles.resendTimer}>
                          Resend OTP in 0:{String(secondsLeft).padStart(2, "0")}
                        </Text>
                      ) : (
                        <TouchableOpacity
                          testID="login-resend-otp"
                          onPress={() => sendOtp(true)}
                          disabled={sendingOtp}
                          style={styles.resendBtn}
                        >
                          {sendingOtp ? (
                            <ActivityIndicator size="small" color={colors.primary} />
                          ) : (
                            <>
                              <PhIcon name="arrow-clockwise" size={14} color={colors.primary} />
                              <Text style={styles.resendTxt}>Resend OTP</Text>
                            </>
                          )}
                        </TouchableOpacity>
                      )}
                    </View>

                    <TouchableOpacity
                      testID="login-edit-phone"
                      onPress={() => {
                        if (timerRef.current) clearInterval(timerRef.current);
                        setSecondsLeft(0);
                        setOtp("");
                        setOtpSent(false);
                      }}
                      style={styles.editPhoneBtn}
                    >
                      <PhIcon name="edit" size={14} color="#475569" />
                      <Text style={styles.editPhoneTxt}>Wrong number? Edit</Text>
                    </TouchableOpacity>
                  </>
                )}
              </>
            )}

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <GoogleSignInButton label="Continue with Google" />

            {/* Phase G2 — Make the "Create an account" path clearly
                tappable for first-touch users who default-landed on
                login. The outlined button has been promoted from a
                tiny inline link to a full-width secondary CTA. */}
            <TouchableOpacity
              testID="login-create-account"
              style={styles.signupBtn}
              onPress={() => router.push("/(auth)/signup")}
              activeOpacity={0.85}
            >
              <PhIcon name="rocket" size={18} color={colors.primary} />
              <Text style={styles.signupBtnText}>Create new account</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles.hint}>
            Your data is private. Each shop keeps its own shipments, couriers,
            settings and demo-free workspace.
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
  brandTitle: { fontSize: 22, fontWeight: "800", color: colors.text, marginTop: 8 },
  brandSub: { fontSize: 13, color: colors.textMuted, marginTop: 2 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
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
  detectionHint: {
    marginTop: 6,
    fontSize: 11.5,
    color: "#16A34A",
    fontWeight: "700",
  },
  pwRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  eyeBtn: { width: 44, height: 46, justifyContent: "center", alignItems: "center" },
  primaryBtn: {
    marginTop: 18, height: 50, borderRadius: 12,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  footerRow: {
    flexDirection: "row", gap: 6,
    justifyContent: "center", alignItems: "center", marginTop: 18,
  },
  footerText: { color: colors.textMuted, fontSize: 13 },
  footerLink: { color: colors.primary, fontWeight: "800", fontSize: 13 },
  forgotBtn: {
    alignItems: "center",
    paddingVertical: 12,
    marginTop: 4,
  },
  forgotTxt: { color: colors.primary, fontSize: 13, fontWeight: "700" },
  resendRow: { marginTop: 14, alignItems: "center", justifyContent: "center" },
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
    flexDirection: "row", alignItems: "center",
    gap: 10, marginTop: 18, marginBottom: 14,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: { fontSize: 11, fontWeight: "800", color: "#94A3B8", letterSpacing: 2 },
  hint: { marginTop: 16, textAlign: "center", fontSize: 11.5, color: colors.textMuted, paddingHorizontal: 12 },
  signupBtn: {
    marginTop: 12,
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
  signupBtnText: { color: colors.primary, fontWeight: "800", fontSize: 14 },

  // Phase B+C — login mode toggle (owner vs team-member).
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
  modeBtnTxt:    { fontSize: 12, fontWeight: "800", color: "#1F4FBF", letterSpacing: 0.3 },
});
