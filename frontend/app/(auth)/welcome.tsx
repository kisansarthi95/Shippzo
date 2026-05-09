/**
 * /app/(auth)/welcome.tsx — Phase G2 first-touch landing screen.
 *
 * Shippzo is brand-new, so the typical visitor on the auth stack is
 * a *fresh* signup — not a returning user. The legacy login-first
 * pattern ("big Login button + tiny 'Create account' link below") was
 * confusing those users into the wrong screen. This Welcome screen
 * fixes that by showing both choices with equal prominence:
 *
 *   • Brand header (logo + tagline) at the top
 *   • Primary  CTA  → Create New Account (orange)
 *   • Secondary CTA → I already have an account (outlined)
 *   • Tertiary CTA  → Continue with Google (one-tap path)
 *
 * Routing:  app/_layout.tsx redirects unauthenticated users that land
 * on "/" or any auth screen to /(auth)/welcome. From here the user
 * picks a path and the rest of the auth stack handles the form work.
 */
import React from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { colors } from "../../lib/theme";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";
import GoogleSignInButton from "../../components/GoogleSignInButton";
import PhIcon from "../../components/PhIcon";

export default function WelcomeScreen() {
  const router = useRouter();
  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.brandWrap}>
          <BrandHeaderAnimator variant="login" />
          <Text style={styles.tagline}>
            Generate Shipping Labels in Just 30 Seconds
          </Text>
        </View>

        <View style={styles.actions}>
          <TouchableOpacity
            testID="welcome-signup"
            style={styles.primaryBtn}
            onPress={() => router.push("/(auth)/signup")}
            activeOpacity={0.85}
          >
            <PhIcon name="rocket" size={18} color="#fff" />
            <Text style={styles.primaryBtnText}>Create New Account</Text>
          </TouchableOpacity>

          <TouchableOpacity
            testID="welcome-login"
            style={styles.secondaryBtn}
            onPress={() => router.push("/(auth)/login")}
            activeOpacity={0.85}
          >
            <PhIcon name="person" size={18} color={colors.primary} />
            <Text style={styles.secondaryBtnText}>I already have an account</Text>
          </TouchableOpacity>

          <View style={styles.divider}>
            <View style={styles.dividerLine} />
            <Text style={styles.dividerText}>OR</Text>
            <View style={styles.dividerLine} />
          </View>

          <GoogleSignInButton label="Continue with Google" />
        </View>

        <Text style={styles.footerNote}>
          By continuing you agree to our Terms & Privacy.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  scroll: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: 22,
    paddingVertical: 30,
  },
  brandWrap: {
    alignItems: "center",
    marginBottom: 30,
  },
  tagline: {
    marginTop: 14,
    fontSize: 14,
    color: colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
    fontWeight: "600",
    paddingHorizontal: 12,
  },
  actions: { gap: 12 },
  primaryBtn: {
    height: 54,
    borderRadius: 14,
    backgroundColor: colors.primary,
    flexDirection: "row",
    gap: 10,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.25,
    shadowRadius: 8,
    elevation: 4,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  secondaryBtn: {
    height: 54,
    borderRadius: 14,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.primary,
    flexDirection: "row",
    gap: 10,
    alignItems: "center",
    justifyContent: "center",
  },
  secondaryBtnText: { color: colors.primary, fontWeight: "800", fontSize: 15 },
  divider: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginVertical: 8,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: { fontSize: 11, fontWeight: "700", color: "#94A3B8", letterSpacing: 1 },
  footerNote: {
    marginTop: 22,
    textAlign: "center",
    fontSize: 11,
    color: "#94A3B8",
    lineHeight: 16,
  },
});
