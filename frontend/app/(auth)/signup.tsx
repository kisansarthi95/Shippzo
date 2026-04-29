import React, { useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";
import GoogleSignInButton from "../../components/GoogleSignInButton";

export default function SignupScreen() {
  const { signUp } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [shop, setShop] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  // 2026-04-30 — Privacy / Terms acceptance. New users MUST check this
  // before the "Create account" button is enabled. Stored only locally
  // for this flow; backend doesn't need to persist it because signing
  // up is itself the record of consent.
  const [policyAccepted, setPolicyAccepted] = useState(false);

  const submit = async () => {
    const e = email.trim().toLowerCase();
    if (!e || !password || !name.trim()) {
      Alert.alert("Missing fields", "Email, password and name are required");
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length < 10) {
      Alert.alert(
        "Mobile number required",
        "Please enter your 10-digit mobile number. We'll use it if you ever need to reset your password.",
      );
      return;
    }
    if (password.length < 6) {
      Alert.alert("Password too short", "Use at least 6 characters");
      return;
    }
    if (!policyAccepted) {
      Alert.alert(
        "Please accept Terms & Privacy",
        "You need to tick the checkbox confirming you've read and agree to our Terms of Service and Privacy Policy before creating an account.",
      );
      return;
    }
    setBusy(true);
    try {
      const res = await signUp(e, password, name.trim(), shop.trim(), phoneDigits);
      if (res?.trial_denied) {
        // Phase-2b: friendly notice — signup succeeded, free trial wasn't
        // granted because this device already used one. We don't reveal
        // *who* the prior account belongs to.
        Alert.alert(
          "Welcome aboard!",
          "We noticed this device has already used a free trial before, so the trial wasn't started this time. You can subscribe to a paid plan from the Plans screen anytime to continue.",
        );
      }
    } catch (err: any) {
      Alert.alert(
        "Signup failed",
        err?.response?.data?.detail || err?.message || "Please try again"
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <Ionicons name="cube-outline" size={36} color={colors.primary} />
            <Text style={styles.brandTitle}>Create account</Text>
            <Text style={styles.brandSub}>Start shipping in minutes — 15 demo orders included</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>Your name</Text>
            <TextInput
              testID="signup-name"
              value={name}
              onChangeText={setName}
              placeholder="Mahek"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>Shop name (optional)</Text>
            <TextInput
              testID="signup-shop"
              value={shop}
              onChangeText={setShop}
              placeholder="Mahek Creations"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>Email</Text>
            <TextInput
              testID="signup-email"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>
              Mobile Number <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="signup-phone"
              value={phone}
              onChangeText={(t) => setPhone(t.replace(/[^\d+]/g, ""))}
              placeholder="10-digit mobile number"
              keyboardType="phone-pad"
              maxLength={15}
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />
            <Text style={styles.helperNote}>
              We'll use this to help you reset your password if you forget it, and for urgent support.
            </Text>

            <Text style={[styles.label, { marginTop: 12 }]}>Password (min 6)</Text>
            <View style={styles.pwRow}>
              <TextInput
                testID="signup-password"
                value={password}
                onChangeText={setPassword}
                placeholder="At least 6 characters"
                secureTextEntry={!showPwd}
                style={[styles.input, { flex: 1 }]}
                placeholderTextColor="#94A3B8"
              />
              <TouchableOpacity style={styles.eyeBtn} onPress={() => setShowPwd((v) => !v)}>
                <Ionicons name={showPwd ? "eye-off" : "eye"} size={20} color="#64748B" />
              </TouchableOpacity>
            </View>

            <TouchableOpacity
              testID="signup-submit"
              disabled={busy || !policyAccepted}
              onPress={submit}
              style={[styles.primaryBtn, (busy || !policyAccepted) && { opacity: 0.5 }]}
            >
              {busy
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.primaryBtnText}>Create account</Text>}
            </TouchableOpacity>

            {/* 2026-04-30 — Terms & Privacy acceptance. Required before
                signing up. Links open the hosted policy pages in the
                external browser so the user can review them before
                ticking the box. Using router.push would deep-link back
                inside the auth stack which is noisy on a signup flow. */}
            <TouchableOpacity
              testID="signup-policy-checkbox"
              onPress={() => setPolicyAccepted((v) => !v)}
              style={styles.policyRow}
              activeOpacity={0.8}
            >
              <View style={[styles.checkbox, policyAccepted && styles.checkboxOn]}>
                {policyAccepted ? (
                  <Ionicons name="checkmark" size={14} color="#fff" />
                ) : null}
              </View>
              <Text style={styles.policyText}>
                I've read and agree to the{" "}
                <Text
                  style={styles.policyLink}
                  onPress={() => router.push("/refund-policy?tab=terms" as any)}
                >
                  Terms of Service
                </Text>
                {"  &  "}
                <Text
                  style={styles.policyLink}
                  onPress={() => router.push("/refund-policy?tab=privacy" as any)}
                >
                  Privacy Policy
                </Text>
                .
              </Text>
            </TouchableOpacity>

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <GoogleSignInButton label="Sign up with Google" />

            <View style={styles.footerRow}>
              <Text style={styles.footerText}>Already have an account?</Text>
              <TouchableOpacity onPress={() => router.push("/(auth)/login")}>
                <Text style={styles.footerLink}>Log in</Text>
              </TouchableOpacity>
            </View>
          </View>
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
  brandSub: { fontSize: 13, color: colors.textMuted, marginTop: 2, textAlign: "center" },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
  required: { color: "#DC2626", fontWeight: "900" },
  helperNote: { fontSize: 11, color: "#64748B", marginTop: 4, marginBottom: 2, lineHeight: 16 },
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
  pwRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  eyeBtn: { width: 44, height: 46, justifyContent: "center", alignItems: "center" },
  primaryBtn: {
    marginTop: 18, height: 50, borderRadius: 12,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  divider: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginVertical: 16,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: { fontSize: 11, fontWeight: "700", color: "#94A3B8", letterSpacing: 1 },
  footerRow: {
    flexDirection: "row", gap: 6,
    justifyContent: "center", alignItems: "center", marginTop: 18,
  },
  footerText: { color: colors.textMuted, fontSize: 13 },
  footerLink: { color: colors.primary, fontWeight: "800", fontSize: 13 },
  // Policy acceptance row
  policyRow: {
    flexDirection: "row", alignItems: "flex-start", gap: 10,
    marginTop: 14, paddingHorizontal: 2,
  },
  checkbox: {
    width: 22, height: 22, borderRadius: 6,
    borderWidth: 2, borderColor: "#94A3B8",
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#fff",
  },
  checkboxOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  policyText: { flex: 1, fontSize: 12, color: "#475569", lineHeight: 17 },
  policyLink: { color: colors.primary, fontWeight: "800", textDecorationLine: "underline" },
});
