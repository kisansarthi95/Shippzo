/**
 * Privacy Policy — public page reachable at /privacy.
 * Linked from signup checkbox.
 */
import React from "react";
import { View, Text, ScrollView, StyleSheet, TouchableOpacity } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { colors } from "../lib/theme";

export default function PrivacyPage() {
  const router = useRouter();
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={styles.hBtn}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.hTitle}>Privacy Policy</Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: 18, paddingBottom: 48 }}>
        <Text style={styles.updated}>Last updated: 30 April 2026</Text>

        <H>1. What we collect</H>
        <P>
          When you use the app we collect: your account details (name, email, phone, shop
          name, password hash), the data you enter about shipments (customer names, phones,
          addresses, items, amounts), usage telemetry (feature clicks, AI token counts) and
          payment records (Razorpay order IDs — never full card details).
        </P>

        <H>2. Why we collect it</H>
        <P>
          To operate the core features — storing your shipments, generating labels, running
          Smart Paste AI, syncing with your Google Sheet, sending plan renewal emails, and
          enforcing plan limits (shipments/month, AI credits).
        </P>

        <H>3. How we share it</H>
        <P>
          We never sell your data. We only share what's strictly needed with:
          {"\n"}  • Razorpay — to process your payments.
          {"\n"}  • Google Sheets API — to sync rows you've explicitly linked.
          {"\n"}  • WhatsApp — only when you actively tap a "Share" / "Send" button.
          {"\n"}  • Our AI provider (Google Gemini) — your Smart Paste text input is processed
              to extract order fields. We do not retain it beyond the parsing step.
        </P>

        <H>4. Data retention</H>
        <P>
          Your account data stays until you delete it. You can erase every shipment you created
          from the app's Shipments screen. For full account deletion, email
          support@example.com and we'll remove your records within 30 days (we keep the
          minimum necessary for tax / audit compliance).
        </P>

        <H>5. Your rights</H>
        <P>
          You can access, export (CSV on the Shipments screen), correct, or delete your data
          at any time. You can also opt out of non-critical emails from your notification
          preferences.
        </P>

        <H>6. Security</H>
        <P>
          Passwords are hashed with bcrypt. Transport is TLS-encrypted. We apply principle of
          least access internally and rotate service credentials regularly.
        </P>

        <H>7. Cookies & analytics</H>
        <P>
          The web preview uses session cookies to keep you logged in. Mobile builds use
          on-device secure storage. We don't use any third-party tracking cookies for ads.
        </P>

        <H>8. Children</H>
        <P>
          The service is for businesses and is not directed at users under 16.
        </P>

        <H>9. Changes</H>
        <P>
          We'll notify you in-app and by email for any material change. Continued use after
          changes take effect means you accept the updated policy.
        </P>

        <H>10. Contact</H>
        <P>
          Questions about your privacy? Email support@example.com.
        </P>
      </ScrollView>
    </SafeAreaView>
  );
}

function H({ children }: { children: React.ReactNode }) {
  return <Text style={styles.h}>{children}</Text>;
}
function P({ children }: { children: React.ReactNode }) {
  return <Text style={styles.p}>{children}</Text>;
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: "#fff" },
  header: { flexDirection: "row", alignItems: "center", padding: 12, gap: 8, borderBottomWidth: 1, borderBottomColor: "#E5E7EB" },
  hBtn:   { padding: 8 },
  hTitle: { fontSize: 18, fontWeight: "800", color: colors.text },
  updated:{ color: "#64748B", fontSize: 12, marginBottom: 14, fontStyle: "italic" },
  h:      { fontSize: 15, fontWeight: "800", color: colors.text, marginTop: 18, marginBottom: 6 },
  p:      { fontSize: 14, color: "#334155", lineHeight: 22, marginBottom: 8 },
});
