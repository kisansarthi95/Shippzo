/**
 * Cancel Subscription screen.
 *
 * Currently we use Razorpay Orders (one-time charges, NOT recurring
 * subscriptions). Cancelling here flips an `auto_renew=false` flag
 * on the user record, which is forward-compatible with future
 * Razorpay Subscriptions integration. The plan stays active until
 * plan_expires_at.
 */
import React, { useEffect, useState } from "react";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert, Platform,
  ActivityIndicator,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { Api, UsageSummary } from "../lib/api";
import { colors } from "../lib/theme";
import { useAuth } from "../lib/auth";

export default function CancelSubscriptionScreen() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Api.myUsage()
      .then((u) => setUsage(u))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const onPaid = usage?.period === "month";
  const planName = usage?.plan_name || "your plan";
  const expiresAt = usage?.plan_expires_at
    ? new Date(usage.plan_expires_at).toLocaleDateString("en-IN", {
        year: "numeric", month: "short", day: "numeric",
      })
    : "—";

  const doCancel = async () => {
    const proceed = async () => {
      try {
        setBusy(true);
        const r = await Api.cancelSubscription();
        await refresh().catch(() => {});
        Alert.alert("Cancellation requested", r.message);
        router.back();
      } catch (e: any) {
        Alert.alert(
          "Couldn't cancel",
          e?.response?.data?.detail || e?.message || "Please try again",
        );
      } finally {
        setBusy(false);
      }
    };
    const msg =
      `Are you sure you want to cancel auto-renewal of ${planName}? ` +
      `Your plan will stay active until ${expiresAt}, after which you'll ` +
      `be moved to the free trial.`;
    if (Platform.OS === "web") {
      if (typeof window !== "undefined" && window.confirm && window.confirm(msg)) {
        proceed();
      }
      return;
    }
    Alert.alert("Cancel subscription?", msg, [
      { text: "Keep plan", style: "cancel" },
      { text: "Yes, cancel", style: "destructive", onPress: proceed },
    ]);
  };

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Cancel Subscription",
          headerStyle: { backgroundColor: colors.background },
          headerRight: () => (
            <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          ),
          headerBackVisible: false,
        }}
      />
      <ScrollView contentContainerStyle={styles.content}>
        {loading ? (
          <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
        ) : !onPaid ? (
          <View style={styles.box}>
            <Ionicons name="information-circle" size={36} color="#475569" />
            <Text style={styles.h1}>You're on the free trial</Text>
            <Text style={styles.p}>
              There's nothing to cancel — you haven't been charged yet.
              When you upgrade to a paid plan, you'll be able to cancel
              auto-renewal from this screen.
            </Text>
            <TouchableOpacity
              onPress={() => router.replace("/plans")}
              style={styles.cta}
            >
              <Text style={styles.ctaTxt}>See Plans</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <>
            <View style={styles.summary}>
              <Text style={styles.label}>Current Plan</Text>
              <Text style={styles.value}>{planName}</Text>
              <Text style={[styles.label, { marginTop: 10 }]}>Active until</Text>
              <Text style={styles.value}>{expiresAt}</Text>
              {usage?.plan_billing_cycle ? (
                <>
                  <Text style={[styles.label, { marginTop: 10 }]}>Billing</Text>
                  <Text style={styles.value}>
                    {usage.plan_billing_cycle === "yearly" ? "Yearly" : "Monthly"}
                  </Text>
                </>
              ) : null}
            </View>

            <View style={styles.notice}>
              <Ionicons name="warning-outline" size={18} color="#B45309" />
              <Text style={styles.noticeTxt}>
                You won't be charged again. Your access continues until{" "}
                <Text style={{ fontWeight: "800" }}>{expiresAt}</Text>, then
                you'll move to the free trial.
              </Text>
            </View>

            <Text style={styles.h2}>What happens next?</Text>
            <Bullet>You can keep using {planName} until {expiresAt}.</Bullet>
            <Bullet>No further charges will be made.</Bullet>
            <Bullet>Your shipments, customer data, and settings stay intact.</Bullet>
            <Bullet>You can re-subscribe any time from Plans.</Bullet>

            <TouchableOpacity
              onPress={doCancel}
              disabled={busy}
              activeOpacity={0.85}
              style={[styles.cancelBtn, busy && { opacity: 0.6 }]}
            >
              {busy ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="close-circle" size={18} color="#fff" />
                  <Text style={styles.cancelTxt}>Cancel Auto-renewal</Text>
                </>
              )}
            </TouchableOpacity>

            <TouchableOpacity
              onPress={() => router.back()}
              style={styles.keepBtn}
            >
              <Text style={styles.keepTxt}>Keep my subscription</Text>
            </TouchableOpacity>

            <Text style={styles.foot}>
              Looking for a refund instead?{" "}
              <Text
                style={styles.link}
                onPress={() => router.replace("/refund-policy" as any)}
              >
                See Refund Policy
              </Text>
            </Text>
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.bulletRow}>
      <Ionicons name="checkmark-circle" size={16} color="#047857" />
      <Text style={styles.bulletTxt}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 60 },
  box: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 20,
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  summary: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 16,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    marginBottom: 14,
  },
  label: { color: "#94A3B8", fontSize: 11, fontWeight: "800", letterSpacing: 0.5 },
  value: { color: "#0F172A", fontSize: 16, fontWeight: "800", marginTop: 2 },
  notice: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    backgroundColor: "#FEF3C7",
    borderColor: "#FCD34D",
    borderWidth: 1,
    padding: 12,
    borderRadius: 10,
    marginBottom: 18,
  },
  noticeTxt: { flex: 1, color: "#92400E", fontSize: 13, lineHeight: 18 },
  h1: { fontSize: 18, fontWeight: "900", color: "#0F172A", marginTop: 6 },
  h2: { fontSize: 14, fontWeight: "800", color: "#1F2937", marginTop: 6, marginBottom: 8 },
  p: { color: "#475569", fontSize: 13.5, lineHeight: 20, textAlign: "center" },
  bulletRow: {
    flexDirection: "row",
    gap: 10,
    alignItems: "center",
    marginVertical: 4,
  },
  bulletTxt: { flex: 1, color: "#334155", fontSize: 13.5 },
  cancelBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    backgroundColor: "#DC2626",
    paddingVertical: 14,
    borderRadius: 12,
    marginTop: 22,
  },
  cancelTxt: { color: "#fff", fontSize: 14, fontWeight: "800", letterSpacing: 0.4 },
  keepBtn: {
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: "center",
    marginTop: 8,
  },
  keepTxt: { color: "#475569", fontSize: 13, fontWeight: "700" },
  foot: { textAlign: "center", color: "#94A3B8", fontSize: 12, marginTop: 16 },
  link: { color: colors.primary, fontWeight: "800", textDecorationLine: "underline" },
  cta: {
    marginTop: 12,
    backgroundColor: colors.primary,
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 10,
  },
  ctaTxt: { color: "#fff", fontWeight: "800" },
});
