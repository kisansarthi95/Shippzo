import React, { useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";
import GoogleSignInButton from "../../components/GoogleSignInButton";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";

export default function LoginScreen() {
  const { signIn, signInTeam } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  // Phase B+C — toggle between owner login (default) and team-member
  // login. Both paths post to different endpoints; the rest of the
  // form stays identical so we don't bloat the UI.
  const [mode, setMode] = useState<"owner" | "team">("owner");

  const submit = async () => {
    const e = email.trim().toLowerCase();
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
            <BrandHeaderAnimator variant="login" />
          </View>

          <View style={styles.card}>
            {/* Phase B+C — owner / team-member mode toggle */}
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

            <Text style={styles.label}>Email</Text>
            <TextInput
              testID="login-email"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

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

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <GoogleSignInButton label="Continue with Google" />

            <View style={styles.footerRow}>
              <Text style={styles.footerText}>New here?</Text>
              <TouchableOpacity onPress={() => router.push("/(auth)/signup")}>
                <Text style={styles.footerLink}>Create an account</Text>
              </TouchableOpacity>
            </View>
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
  divider: {
    flexDirection: "row", alignItems: "center",
    gap: 10, marginTop: 18, marginBottom: 14,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: { fontSize: 11, fontWeight: "800", color: "#94A3B8", letterSpacing: 2 },
  hint: { marginTop: 16, textAlign: "center", fontSize: 11.5, color: colors.textMuted, paddingHorizontal: 12 },

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
