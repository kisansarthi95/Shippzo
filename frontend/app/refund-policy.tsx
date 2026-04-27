/**
 * Refund Policy + Terms of Service screen (Razorpay merchant policy).
 *
 * Razorpay's merchant onboarding requires a publicly accessible Refund
 * & Cancellation Policy as well as Terms of Service. This screen
 * surfaces both — toggled via `?tab=terms` query param.
 *
 * NOTE TO MAIN AGENT: The text below is plain-English boilerplate
 * tuned for Indian B2B SaaS. Replace the merchant name / contact
 * email with the real entity before production.
 */
import React, { useMemo } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../lib/theme";

type Tab = "refund" | "terms";

export default function RefundPolicyScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ tab?: string }>();
  const initial: Tab = params.tab === "terms" ? "terms" : "refund";
  const [tab, setTab] = React.useState<Tab>(initial);

  const headerRight = useMemo(
    () => () => (
      <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
        <Ionicons name="close" size={22} color={colors.text} />
      </TouchableOpacity>
    ),
    [router],
  );

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Policies",
          headerRight,
          headerBackVisible: false,
          headerStyle: { backgroundColor: colors.background },
        }}
      />
      <View style={styles.tabs}>
        <TouchableOpacity
          onPress={() => setTab("refund")}
          style={[styles.tab, tab === "refund" && styles.tabActive]}
        >
          <Text style={[styles.tabTxt, tab === "refund" && styles.tabTxtActive]}>
            Refund Policy
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => setTab("terms")}
          style={[styles.tab, tab === "terms" && styles.tabActive]}
        >
          <Text style={[styles.tabTxt, tab === "terms" && styles.tabTxtActive]}>
            Terms of Service
          </Text>
        </TouchableOpacity>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {tab === "refund" ? <RefundContent /> : <TermsContent />}
      </ScrollView>
    </SafeAreaView>
  );
}

function RefundContent() {
  return (
    <>
      <Text style={styles.h1}>Refund & Cancellation Policy</Text>
      <Text style={styles.muted}>Last updated: June 2025</Text>

      <Text style={styles.h2}>1. Subscription Plans</Text>
      <Text style={styles.p}>
        Courier Manager offers monthly and yearly subscription plans
        (Silver, Gold, Platinum). Once a subscription has been activated,
        it remains in force until the end of its billing cycle.
      </Text>

      <Text style={styles.h2}>2. 7-Day No-Questions-Asked Refund</Text>
      <Text style={styles.p}>
        If you are not satisfied with your subscription, you may request
        a full refund within <Text style={styles.b}>7 days</Text> of the
        original payment date. To qualify:
      </Text>
      <Bullet>You must have made fewer than 25 label prints during the 7-day window.</Bullet>
      <Bullet>The request must be sent to support@your-brand.app from the registered email.</Bullet>
      <Bullet>Refunds are processed back to the original payment method within 5–7 business days.</Bullet>

      <Text style={styles.h2}>3. After 7 Days</Text>
      <Text style={styles.p}>
        Refunds are not available after the 7-day window has elapsed.
        However, you can <Text style={styles.b}>cancel auto-renewal</Text>{" "}
        at any time from Plans → Cancel Subscription. Your plan will
        remain active until the current billing cycle ends, after which
        you will be moved to the free tier with no further charges.
      </Text>

      <Text style={styles.h2}>4. Wallet Credit Top-ups</Text>
      <Text style={styles.p}>
        Credits purchased via wallet top-ups are <Text style={styles.b}>non-refundable</Text>{" "}
        once consumed (i.e. used to print labels or run AI parses).
        Unused credits can be refunded pro-rata within 7 days of
        purchase, subject to email request and identity verification.
      </Text>

      <Text style={styles.h2}>5. Failed Payments / Duplicate Charges</Text>
      <Text style={styles.p}>
        If a payment is debited but your plan / wallet is not credited
        within 1 hour, please email{" "}
        <Text style={styles.b}>support@your-brand.app</Text> with the
        Razorpay reference ID. Duplicate charges are auto-detected and
        refunded within 24 hours.
      </Text>

      <Text style={styles.h2}>6. Contact</Text>
      <Text style={styles.p}>
        For any refund or billing question, write to{" "}
        <Text style={styles.b}>support@your-brand.app</Text>. We reply
        within one business day.
      </Text>
    </>
  );
}

function TermsContent() {
  return (
    <>
      <Text style={styles.h1}>Terms of Service</Text>
      <Text style={styles.muted}>Last updated: June 2025</Text>

      <Text style={styles.h2}>1. Acceptance</Text>
      <Text style={styles.p}>
        By creating an account on Courier Manager, you agree to these
        Terms of Service and our Refund Policy. If you do not agree,
        please do not use the service.
      </Text>

      <Text style={styles.h2}>2. Account & Data</Text>
      <Text style={styles.p}>
        You are responsible for safeguarding your login credentials.
        Each shop's data (shipments, customer addresses, settings) is
        kept logically isolated and accessible only to the account
        owner.
      </Text>

      <Text style={styles.h2}>3. Acceptable Use</Text>
      <Text style={styles.p}>
        You agree not to use the service to print labels for illegal,
        prohibited, or restricted goods. We reserve the right to
        suspend accounts engaged in such activity.
      </Text>

      <Text style={styles.h2}>4. Service Availability</Text>
      <Text style={styles.p}>
        We aim for 99% uptime but do not guarantee uninterrupted
        service. Planned maintenance is announced in advance. Pro-rata
        credits are issued for outages exceeding 4 hours.
      </Text>

      <Text style={styles.h2}>5. Pricing & Billing</Text>
      <Text style={styles.p}>
        All prices are in INR and inclusive of applicable taxes. Plan
        prices may change with 30 days' notice; existing subscriptions
        run out at the contracted rate.
      </Text>

      <Text style={styles.h2}>6. Limitation of Liability</Text>
      <Text style={styles.p}>
        Our maximum aggregate liability for any claim is limited to
        the amount paid by you in the previous 90 days.
      </Text>

      <Text style={styles.h2}>7. Governing Law</Text>
      <Text style={styles.p}>
        These terms are governed by the laws of India. Disputes are
        subject to the exclusive jurisdiction of courts in Ahmedabad,
        Gujarat.
      </Text>
    </>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.bulletRow}>
      <View style={styles.bulletDot} />
      <Text style={styles.bulletTxt}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  tabs: {
    flexDirection: "row",
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: "#F1F5F9",
    alignItems: "center",
  },
  tabActive: { backgroundColor: "#0F172A" },
  tabTxt: { fontSize: 12, fontWeight: "800", color: "#475569", letterSpacing: 0.4 },
  tabTxtActive: { color: "#fff" },
  content: { padding: 20, paddingBottom: 60 },
  h1: { fontSize: 22, fontWeight: "900", color: "#0F172A", marginBottom: 4 },
  h2: { fontSize: 15, fontWeight: "800", color: "#1F2937", marginTop: 18, marginBottom: 6 },
  muted: { color: "#94A3B8", fontSize: 12, marginBottom: 8 },
  p: { color: "#334155", fontSize: 13.5, lineHeight: 21, marginBottom: 4 },
  b: { fontWeight: "800", color: "#0F172A" },
  bulletRow: {
    flexDirection: "row",
    gap: 10,
    paddingLeft: 4,
    marginVertical: 3,
  },
  bulletDot: {
    width: 6, height: 6, borderRadius: 3,
    backgroundColor: "#475569", marginTop: 8,
  },
  bulletTxt: { flex: 1, color: "#334155", fontSize: 13.5, lineHeight: 21 },
});
