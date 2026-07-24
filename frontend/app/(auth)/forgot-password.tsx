/**
 * Forgot Password screen — Phase F8.1 OTP-verified flow.
 *
 * Step 1: user enters their registered email + registered mobile
 *         number → "Send OTP". The backend validates the pair and
 *         dispatches a "password_reset" OTP through the configured
 *         WhatsApp/webhook automation (payload includes
 *         `contact_email` = the registered email so the operator's
 *         automation can also deliver the code by email).
 * Step 2: user enters the OTP + a new password → reset + auto-return
 *         to login. Rate-limited to 3 failed attempts per email/hour.
 */
import React, { useEffect, useRef, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity, Alert,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { api } from "../../lib/api";
import { colors } from "../../lib/theme";

export default function ForgotPasswordScreen() {
  const router = useRouter();

  const [step, setStep]     = useState<1 | 2>(1);
  const [email, setEmail]   = useState("");
  const [phone, setPhone]   = useState("");
  const [otp, setOtp]       = useState("");
  const [pwd, setPwd]       = useState("");
  const [pwd2, setPwd2]     = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [busy, setBusy]     = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Resend cooldown ticker.
  useEffect(() => {
    if (cooldown <= 0) return;
    timerRef.current = setInterval(() => {
      setCooldown((c) => (c <= 1 ? 0 : c - 1));
    }, 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [cooldown > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const validateContact = () => {
    const e = email.trim().toLowerCase();
    const phoneDigits = phone.replace(/\D/g, "");
    if (!e || !phoneDigits) {
      Alert.alert("Missing fields", "Please enter both your email and registered mobile number.");
      return null;
    }
    if (phoneDigits.length < 10) {
      Alert.alert("Invalid phone", "Enter your 10-digit registered mobile number.");
      return null;
    }
    return { e, phoneDigits };
  };

  const sendOtp = async () => {
    const v = validateContact();
    if (!v) return;
    setBusy(true);
    try {
      const r = await api.post("/auth/forgot-password/request-otp", {
        email: v.e,
        phone: v.phoneDigits,
      });
      setStep(2);
      setCooldown(Number(r?.data?.resend_cooldown || 60));
      Alert.alert(
        "OTP sent ✅",
        "We've sent a one-time code to your registered contact (WhatsApp / email). Enter it below.",
      );
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || "Please try again";
      Alert.alert(status === 429 ? "Please wait" : "Could not send OTP", detail);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    const v = validateContact();
    if (!v) return;
    const otpDigits = otp.replace(/\D/g, "");
    if (otpDigits.length < 4) {
      Alert.alert("Missing OTP", "Enter the one-time code we sent to your registered contact.");
      return;
    }
    if (!pwd || pwd.length < 6) {
      Alert.alert("Password too short", "Use at least 6 characters for the new password.");
      return;
    }
    if (pwd !== pwd2) {
      Alert.alert("Passwords don't match", "Please re-enter the same new password in both fields.");
      return;
    }
    setBusy(true);
    try {
      await api.post("/auth/forgot-password", {
        email: v.e,
        phone: v.phoneDigits,
        otp: otpDigits,
        new_password: pwd,
      });
      Alert.alert(
        "Password reset ✅",
        "Your new password has been set. Please log in with the new password.",
        [{ text: "Go to Login", onPress: () => router.replace("/(auth)/login") }],
      );
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || "Please try again";
      if (status === 429) {
        Alert.alert("Too many attempts", detail);
      } else {
        Alert.alert("Reset failed", detail);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Reset Password",
          headerStyle: { backgroundColor: colors.background },
          headerRight: () => (
            <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          ),
          headerBackVisible: false,
        }}
      />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={{ padding: 20, paddingBottom: 40 }}>
          <View style={styles.hero}>
            <View style={styles.heroIcon}>
              <PhIcon name="key-outline" size={32} color={colors.primary} />
            </View>
            <Text style={styles.title}>Forgot your password?</Text>
            <Text style={styles.sub}>
              {step === 1
                ? "Enter the email and mobile number you used when signing up. We'll send a one-time code to your registered contact."
                : "Enter the one-time code we sent you, then pick a new password."}
            </Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              testID="forgot-email"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              editable={step === 1}
              placeholderTextColor="#94A3B8"
              style={[styles.input, step === 2 && styles.inputLocked]}
            />

            <Text style={[styles.label, { marginTop: 14 }]}>Registered Mobile Number</Text>
            <TextInput
              testID="forgot-phone"
              value={phone}
              onChangeText={(t) => setPhone(t.replace(/[^\d+]/g, ""))}
              placeholder="10-digit mobile number"
              keyboardType="phone-pad"
              maxLength={15}
              editable={step === 1}
              placeholderTextColor="#94A3B8"
              style={[styles.input, step === 2 && styles.inputLocked]}
            />

            {step === 1 ? (
              <TouchableOpacity
                testID="forgot-send-otp"
                onPress={sendOtp}
                disabled={busy}
                style={[styles.cta, busy && { opacity: 0.6 }]}
                activeOpacity={0.85}
              >
                {busy ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.ctaTxt}>Send OTP</Text>
                )}
              </TouchableOpacity>
            ) : (
              <>
                <Text style={[styles.label, { marginTop: 14 }]}>One-Time Code (OTP)</Text>
                <TextInput
                  testID="forgot-otp"
                  value={otp}
                  onChangeText={(t) => setOtp(t.replace(/\D/g, ""))}
                  placeholder="6-digit code"
                  keyboardType="number-pad"
                  maxLength={8}
                  placeholderTextColor="#94A3B8"
                  style={[styles.input, { letterSpacing: 4, fontWeight: "800" }]}
                />
                <View style={styles.resendRow}>
                  <TouchableOpacity
                    testID="forgot-resend-otp"
                    onPress={sendOtp}
                    disabled={busy || cooldown > 0}
                  >
                    <Text style={[styles.resendTxt, (busy || cooldown > 0) && { opacity: 0.5 }]}>
                      {cooldown > 0 ? `Resend OTP in ${cooldown}s` : "Resend OTP"}
                    </Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => { setStep(1); setOtp(""); }}>
                    <Text style={styles.resendTxt}>Change details</Text>
                  </TouchableOpacity>
                </View>

                <Text style={[styles.label, { marginTop: 14 }]}>New Password (min 6)</Text>
                <View style={styles.pwRow}>
                  <TextInput
                    testID="forgot-new-password"
                    value={pwd}
                    onChangeText={setPwd}
                    placeholder="Pick a new password"
                    secureTextEntry={!showPwd}
                    placeholderTextColor="#94A3B8"
                    style={[styles.input, { flex: 1 }]}
                  />
                  <TouchableOpacity style={styles.eyeBtn} onPress={() => setShowPwd((v) => !v)}>
                    <PhIcon name={showPwd ? "eye-off" : "eye"} size={20} color="#64748B" />
                  </TouchableOpacity>
                </View>

                <Text style={[styles.label, { marginTop: 14 }]}>Confirm New Password</Text>
                <TextInput
                  testID="forgot-new-password-2"
                  value={pwd2}
                  onChangeText={setPwd2}
                  placeholder="Re-type the new password"
                  secureTextEntry={!showPwd}
                  placeholderTextColor="#94A3B8"
                  style={styles.input}
                />

                <TouchableOpacity
                  testID="forgot-submit"
                  onPress={submit}
                  disabled={busy}
                  style={[styles.cta, busy && { opacity: 0.6 }]}
                  activeOpacity={0.85}
                >
                  {busy ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <Text style={styles.ctaTxt}>Reset Password</Text>
                  )}
                </TouchableOpacity>
              </>
            )}

            <View style={styles.noteBox}>
              <PhIcon name="information-circle-outline" size={15} color="#64748B" />
              <Text style={styles.noteTxt}>
                For security we allow max 3 failed attempts per hour.
                Can&apos;t remember your registered mobile? Contact support and
                an admin will reset your password for you.
              </Text>
            </View>
          </View>

          <TouchableOpacity
            onPress={() => router.replace("/(auth)/login")}
            style={styles.backLink}
          >
            <PhIcon name="arrow-back" size={14} color={colors.primary} />
            <Text style={styles.backLinkTxt}>Back to login</Text>
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  hero: { alignItems: "center", gap: 8, marginBottom: 18 },
  heroIcon: {
    width: 64, height: 64, borderRadius: 20,
    backgroundColor: `${colors.primary}15`,
    alignItems: "center", justifyContent: "center",
    marginBottom: 4,
  },
  title: { fontSize: 20, fontWeight: "900", color: colors.text },
  sub:   { fontSize: 13, color: "#64748B", textAlign: "center", lineHeight: 19, paddingHorizontal: 20 },
  card: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 18,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
  input: {
    backgroundColor: "#F8FAFC",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 11,
    fontSize: 14,
    color: "#0F172A",
  },
  inputLocked: { opacity: 0.6 },
  pwRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  eyeBtn: { padding: 8 },
  resendRow: {
    flexDirection: "row", justifyContent: "space-between",
    marginTop: 8,
  },
  resendTxt: { color: colors.primary, fontSize: 12.5, fontWeight: "700" },
  cta: {
    backgroundColor: colors.primary,
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
    marginTop: 18,
  },
  ctaTxt: { color: "#fff", fontSize: 14, fontWeight: "800", letterSpacing: 0.4 },
  noteBox: {
    flexDirection: "row", gap: 8,
    backgroundColor: "#F1F5F9", borderRadius: 8,
    padding: 10, marginTop: 14, alignItems: "flex-start",
  },
  noteTxt: { flex: 1, fontSize: 11.5, color: "#64748B", lineHeight: 17 },
  backLink: {
    marginTop: 16,
    flexDirection: "row", alignItems: "center",
    justifyContent: "center", gap: 6,
  },
  backLinkTxt: { color: colors.primary, fontSize: 13, fontWeight: "700" },
});
