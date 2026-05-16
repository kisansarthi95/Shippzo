/**
 * Phase-21 — Support Center → Create Request → STEP 1
 * "How can we help you?" Issue Category Selection screen.
 *
 * Matches the approved design 1:1 — a list of full-width cards,
 * each with a coloured icon tile, a category title, and a small
 * helper description. Tapping a card pushes the Issue Details form
 * at /support-center/create/[cat] with that category pre-selected.
 *
 * Category copy is sourced from `CATEGORIES` so the same list can
 * power downstream screens (My Requests filters, Review summary,
 * etc.) without copy-pasting strings.
 */
import React from "react";
import {
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import PhIcon from "../../components/PhIcon";

export type SupportCategoryKey =
  | "account_login"
  | "plan_wallet"
  | "label_print"
  | "order_input"
  | "whatsapp"
  | "app_bug"
  | "feature_request"
  | "other";

export const CATEGORIES: Array<{
  k: SupportCategoryKey;
  title: string;
  desc: string;
  icon: string;
  bg: string;
  fg: string;
}> = [
  {
    k: "account_login",
    title: "Account Login & Forgot Account",
    desc: "Login, OTP, account access, password reset, mobile verification, etc.",
    icon: "lock-closed", bg: "#DBEAFE", fg: "#2563EB",
  },
  {
    k: "plan_wallet",
    title: "Plan & Wallet Issue",
    desc: "Plan activation, subscription renewal, wallet recharge, balance update, payment verification.",
    icon: "wallet-outline", bg: "#FFEDD5", fg: "#F97316",
  },
  {
    k: "label_print",
    title: "Label Generate & Print Related Issue",
    desc: "Label generation, PDF issue, print issue, printer setup, download issue, courier label format.",
    icon: "document-text-outline", bg: "#EDE9FE", fg: "#7C3AED",
  },
  {
    k: "order_input",
    title: "Order Input Related Issue",
    desc: "Smart Fill, Google Sheets, Excel File, CSV File, Webhook, manual order input, bulk import.",
    icon: "cloud-upload-outline", bg: "#CFFAFE", fg: "#0891B2",
  },
  {
    k: "whatsapp",
    title: "WhatsApp Integration Problem",
    desc: "WhatsApp login, connection, session issue, automation issue, WhatsApp sync, etc.",
    icon: "logo-whatsapp", bg: "#DCFCE7", fg: "#16A34A",
  },
  {
    k: "app_bug",
    title: "App Crash / Bug",
    desc: "App crash, loading issue, screen freeze, slow performance, unexpected errors, etc.",
    icon: "bug", bg: "#FEE2E2", fg: "#DC2626",
  },
  {
    k: "feature_request",
    title: "Required Feature",
    desc: "Request a new feature, automation idea, improvement suggestion, workflow enhancement.",
    icon: "sparkles", bg: "#FEF3C7", fg: "#D97706",
  },
  {
    k: "other",
    title: "Other Issue",
    desc: "If your issue does not match the above categories, select this option.",
    icon: "ellipsis-horizontal", bg: "#E2E8F0", fg: "#475569",
  },
];

export default function CreateTicketCategoryPicker() {
  const router = useRouter();
  const insets = useSafeAreaInsets();

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{
          title: "Contact Support",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24 }}
        showsVerticalScrollIndicator={false}
      >
        <Text style={styles.heading}>How can we help you?</Text>
        <Text style={styles.sub}>Select the issue category</Text>

        <View style={{ height: 12 }} />

        {CATEGORIES.map((c) => (
          <TouchableOpacity
            key={c.k}
            testID={`cat-card-${c.k}`}
            activeOpacity={0.85}
            onPress={() => router.push(`/support-center/create/${c.k}` as any)}
            style={styles.card}
          >
            <View style={[styles.iconTile, { backgroundColor: c.bg }]}>
              <PhIcon name={c.icon} size={20} color={c.fg} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.cardTitle} numberOfLines={2}>
                {c.title}
              </Text>
              <Text style={styles.cardSub} numberOfLines={2}>
                {c.desc}
              </Text>
            </View>
            <PhIcon name="chevron-forward" size={18} color="#CBD5E1" />
          </TouchableOpacity>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },
  heading: { fontSize: 22, fontWeight: "800", color: "#0F172A" },
  sub: { fontSize: 13.5, color: "#64748B", marginTop: 4 },
  card: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: "#fff", borderRadius: 14,
    paddingHorizontal: 14, paddingVertical: 14,
    marginBottom: 10,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.05)", elevation: 1,
  },
  iconTile: {
    width: 40, height: 40, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  cardTitle: {
    fontSize: 14, fontWeight: "800", color: "#0F172A",
  },
  cardSub: {
    fontSize: 11.5, color: "#64748B", marginTop: 3, lineHeight: 15,
  },
});
