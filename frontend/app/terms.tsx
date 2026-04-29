/**
 * Terms of Service — public page reachable at /terms.
 * Linked from signup checkbox. Kept plain and readable so users
 * actually scan it before ticking the box.
 */
import React from "react";
import { View, Text, ScrollView, StyleSheet, TouchableOpacity } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { colors } from "../lib/theme";

export default function TermsPage() {
  const router = useRouter();
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => (router.canGoBack() ? router.back() : router.replace("/"))} style={styles.hBtn}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.hTitle}>Terms of Service</Text>
      </View>
      <ScrollView contentContainerStyle={{ padding: 18, paddingBottom: 48 }}>
        <Text style={styles.updated}>Last updated: 30 April 2026</Text>

        <H>1. Acceptance</H>
        <P>
          By creating an account or using the app you agree to these Terms. If you don't agree,
          please stop using the service.
        </P>

        <H>2. Your account</H>
        <P>
          You're responsible for keeping your login credentials safe and for every action taken
          under your account. Notify us right away if you suspect unauthorized access.
        </P>

        <H>3. Plans & billing</H>
        <P>
          Paid plans (Silver, Gold, Platinum) are billed via Razorpay either monthly or yearly
          upfront. Yearly subscriptions include bonus months as advertised on the Plans screen
          at the time of purchase. Coupons and promotional discounts follow the rules shown on
          each coupon (validity window, plan/cycle eligibility, usage caps).
        </P>
        <P>
          Subscriptions auto-renew until cancelled. You can cancel any time from Settings →
          Cancel subscription. After cancellation, your current paid period remains active
          until its expiry date.
        </P>

        <H>4. Refunds</H>
        <P>
          Monthly plans are non-refundable. Yearly plans may be refunded on a pro-rata basis
          within 14 days of purchase, less any discounts, bonus months already consumed, and
          processing fees. Email support@example.com to request a refund.
        </P>

        <H>5. Acceptable use</H>
        <P>
          Don't use the app for illegal shipments, spam, reverse-engineering, or to abuse our
          Smart Paste AI / storage. We may suspend accounts that abuse the service.
        </P>

        <H>6. Your content</H>
        <P>
          Customer details, addresses and shipment data you store in the app remain yours. We
          only process them to provide the service. You can export or delete your data any time.
        </P>

        <H>7. Third-party services</H>
        <P>
          The app integrates with Razorpay (payments), Google Sheets (data sync) and WhatsApp
          (messaging). Those services have their own terms and privacy policies; using our
          integrations means you also agree to theirs.
        </P>

        <H>8. Service availability & limits</H>
        <P>
          We aim for high uptime but don't guarantee 100% availability. We may rate-limit
          Smart Paste AI / Google Sheets sync / print jobs to prevent abuse. Plan feature
          limits (shipments / month, AI credits etc.) are enforced as advertised.
        </P>

        <H>9. Liability</H>
        <P>
          Except as required by law, our total liability to you for any claim is limited to
          the amount you paid us in the 3 months preceding the claim. We aren't liable for
          indirect or consequential losses.
        </P>

        <H>10. Changes</H>
        <P>
          We may update these Terms. Material changes will be notified in the app and by the
          email on file at least 14 days before they take effect.
        </P>

        <H>11. Contact</H>
        <P>
          Questions? Email support@example.com. We typically reply within 1-2 business days.
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
