/**
 * Reports Hub — Phase 2.5
 * --------------------------
 * Single landing page that lists every report the user can run.
 * Each tile navigates to its own dedicated screen so the period/
 * filter state stays isolated per-report.
 *
 * The Hub itself has no data calls — it's a static menu with light
 * coloured tiles and a short description per report. Plan-gating is
 * handled inside the individual report screens (or by hiding tiles
 * behind feature flags later if needed).
 */
import React from "react";
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Stack, router } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { usePermissions } from "../../lib/permissions";

type Report = {
  key: string;
  title: string;
  desc: string;
  icon: keyof typeof Ionicons.glyphMap;
  color: string;
  bg: string;
  path: string;
  // Phase B+C — permission key required when running as a team member.
  // Owners always see everything; team members see only tiles whose
  // permission is in their granted list.
  permission: string;
};

const REPORTS: Report[] = [
  {
    key:   "courier-billing",
    title: "Courier Billing",
    desc:  "Per-courier shipment tally + charges. Ready-to-send Excel bills.",
    icon:  "receipt",
    color: "#1F4FBF",
    bg:    "#EFF6FF",
    path:  "/reports/courier-billing",
    permission: "reports_courier_billing",
  },
  {
    key:   "return-analysis",
    title: "Return Analysis",
    desc:  "Returns by courier / reason / customer. Spot recurring issues.",
    icon:  "refresh-circle",
    color: "#DC2626",
    bg:    "#FEF2F2",
    path:  "/reports/return-analysis",
    permission: "reports_return_analysis",
  },
  {
    key:   "weight-wise",
    title: "Weight-wise Breakup",
    desc:  "Shipments + revenue grouped by 0–500g, 1kg, 2kg, 5kg+ buckets.",
    icon:  "barbell",
    color: "#0EA5E9",
    bg:    "#F0F9FF",
    path:  "/reports/weight-wise",
    permission: "reports_weight_wise",
  },
  {
    key:   "partner-comparison",
    title: "Partner Comparison",
    desc:  "Side-by-side delivery, return & cost metrics across couriers.",
    icon:  "git-compare",
    color: "#7C3AED",
    bg:    "#F5F3FF",
    path:  "/reports/partner-comparison",
    permission: "reports_partner_comparison",
  },
  {
    key:   "reconciliation",
    title: "COD Reconciliation",
    desc:  "Expected vs received COD. Track pending settlements per courier.",
    icon:  "cash",
    color: "#059669",
    bg:    "#ECFDF5",
    path:  "/reports/reconciliation",
    permission: "reports_reconciliation",
  },
];

export default function ReportsHubScreen() {
  const { hasPerm, isTeamMember } = usePermissions();
  const visible = REPORTS.filter((r) => hasPerm(r.permission));
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "Reports" }} />

      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Reports</Text>
          <Text style={styles.subtitle}>Analytics & downloadable Excel files</Text>
        </View>
      </View>

      <ScrollView contentContainerStyle={styles.scroll}>
        {isTeamMember && visible.length < REPORTS.length && (
          <View style={styles.permsBanner}>
            <Ionicons name="information-circle" size={16} color="#1F4FBF" />
            <Text style={styles.permsBannerTxt}>
              You're seeing {visible.length} of {REPORTS.length} reports based on
              your role permissions.
            </Text>
          </View>
        )}

        {visible.length === 0 ? (
          <View style={[styles.tile, { backgroundColor: "#FEF3C7", justifyContent: "center" }]}>
            <Ionicons name="lock-closed" size={22} color="#92400E" />
            <View style={{ flex: 1, marginLeft: 8 }}>
              <Text style={[styles.tileTitle, { color: "#92400E" }]}>No reports available</Text>
              <Text style={styles.tileDesc}>
                Your role doesn't have access to any reports. Ask the shop owner
                to grant report permissions.
              </Text>
            </View>
          </View>
        ) : visible.map((r) => (
          <TouchableOpacity
            key={r.key}
            style={[styles.tile, { backgroundColor: r.bg }]}
            onPress={() => router.push(r.path as any)}
            activeOpacity={0.7}
          >
            <View style={[styles.iconBox, { backgroundColor: r.color }]}>
              <Ionicons name={r.icon} size={22} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={[styles.tileTitle, { color: r.color }]}>{r.title}</Text>
              <Text style={styles.tileDesc} numberOfLines={2}>{r.desc}</Text>
            </View>
            <Ionicons name="chevron-forward" size={20} color={r.color} />
          </TouchableOpacity>
        ))}

        <View style={styles.footerNote}>
          <Ionicons name="information-circle-outline" size={16} color="#6B7280" />
          <Text style={styles.footerNoteTxt}>
            All reports support custom date ranges (up to 3 months) and one-tap
            Excel download for sharing with couriers, accountants, or your team.
          </Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F9FAFB" },
  header: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 12, backgroundColor: "#fff",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  backBtn: { padding: 4 },
  title: { fontSize: 18, fontWeight: "800", color: "#111827" },
  subtitle: { fontSize: 12, color: "#6B7280", marginTop: 2 },
  scroll: { padding: 12, paddingBottom: 60 },

  tile: {
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 14, borderRadius: 14, marginBottom: 10,
  },
  iconBox: {
    width: 44, height: 44, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  tileTitle: { fontSize: 15, fontWeight: "800" },
  tileDesc: { fontSize: 12, color: "#374151", marginTop: 2, lineHeight: 16 },

  permsBanner: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#EFF6FF", borderWidth: 1, borderColor: "#BFDBFE",
    padding: 10, borderRadius: 10, marginBottom: 10,
  },
  permsBannerTxt: { flex: 1, fontSize: 11.5, color: "#1E40AF", lineHeight: 16 },
  footerNote: {
    flexDirection: "row", gap: 8, padding: 12,
    backgroundColor: "#fff", borderRadius: 10, marginTop: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  footerNoteTxt: { flex: 1, fontSize: 11.5, color: "#6B7280", lineHeight: 17 },
});
