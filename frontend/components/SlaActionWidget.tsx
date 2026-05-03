/**
 * SLA Action Required widget — Phase G5
 * --------------------------------------
 * Dashboard widget that surfaces every SLA breach impacting the
 * CURRENT user. Reads /api/me/sla/alerts which already honours the
 * admin-side `display_channels.banner` toggle: when the admin has
 * disabled the banner channel the widget self-hides.
 *
 * Renders top 3 alerts as inline cards with stage colour, customer
 * name, days overdue, and tap-to-open the shipment label. A "View
 * all (N)" footer link routes admins to the full /admin/sla-alerts
 * console; non-admins tap into their Shipments tab filtered to the
 * relevant stage.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { router } from "expo-router";
import { Api } from "../lib/api";

const STAGE_COLOR: Record<string, string> = {
  "Pending":       "#9333EA",
  "Processing":    "#0EA5E9",
  "Ready to Ship": "#10B981",
  "Shipped":       "#1F4FBF",
  "Delivered":     "#059669",
  "Feedback":      "#B45309",
};
const PRIORITY_COLOR = (p?: string) =>
  p === "high" ? "#DC2626" : p === "medium" ? "#F59E0B" : "#6B7280";

type Alert = {
  id: string;
  stage: string;
  shipment_id: string;
  priority?: string;
  level?: number;
  days_overdue: number;
  sla_days: number;
  raised_at: string;
  shipment?: {
    customer_name?: string;
    order_id?: string;
    tracking_id?: string;
  };
};

type Props = {
  isAdmin?: boolean;
};

export default function SlaActionWidget({ isAdmin = false }: Props) {
  const [loading, setLoading] = useState(true);
  const [muted, setMuted]     = useState(false);
  const [alerts, setAlerts]   = useState<Alert[]>([]);

  const load = useCallback(async () => {
    try {
      const res = await Api.meSlaAlerts({ dismissed: false, limit: 50 });
      setMuted(!!res.muted);
      setAlerts(res.alerts || []);
    } catch {
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Self-hide when banner channel is muted by admin or no breaches.
  if (muted) return null;
  if (!loading && alerts.length === 0) return null;

  // Per-stage counts for header summary.
  const counts: Record<string, number> = {};
  for (const a of alerts) counts[a.stage] = (counts[a.stage] || 0) + 1;

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Ionicons name="alert-circle" size={18} color="#DC2626" />
          <Text style={styles.headerTitle}>Action Required</Text>
        </View>
        <View style={styles.countPill}>
          <Text style={styles.countPillText}>{alerts.length}</Text>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator color="#DC2626" style={{ marginVertical: 14 }} />
      ) : (
        <>
          <Text style={styles.subtitle}>
            {alerts.length} parcel{alerts.length === 1 ? "" : "s"} past SLA — tap to review
          </Text>

          {/* Per-stage chip row */}
          <View style={styles.chipRow}>
            {Object.entries(counts).map(([stage, n]) => {
              const c = STAGE_COLOR[stage] || "#374151";
              return (
                <View key={stage} style={[styles.chip, { backgroundColor: c + "18", borderColor: c + "40" }]}>
                  <Text style={[styles.chipText, { color: c }]}>{stage} · {n}</Text>
                </View>
              );
            })}
          </View>

          {/* Top 3 alerts as compact rows */}
          {alerts.slice(0, 3).map((a) => {
            const c = STAGE_COLOR[a.stage] || "#374151";
            return (
              <TouchableOpacity
                key={a.id}
                style={styles.row}
                onPress={() => router.push(`/label/${a.shipment_id}` as any)}
                activeOpacity={0.7}
              >
                <View style={[styles.stageDot, { backgroundColor: c }]} />
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {a.shipment?.customer_name || "(unnamed)"}
                    <Text style={[styles.rowPri, { color: PRIORITY_COLOR(a.priority) }]}>
                      {"  · "}{a.priority?.toUpperCase()}
                      {a.level && a.level > 1 ? ` · L${a.level}` : ""}
                    </Text>
                  </Text>
                  <Text style={styles.rowMeta} numberOfLines={1}>
                    📦 {a.shipment?.order_id || a.shipment?.tracking_id || a.shipment_id?.slice(0, 8)}
                    {"  ·  "}⏱ {a.days_overdue}d past {a.sla_days}d SLA
                    {"  ·  "}{a.stage}
                  </Text>
                </View>
                <Ionicons name="chevron-forward" size={16} color="#9CA3AF" />
              </TouchableOpacity>
            );
          })}

          {/* Footer "View all" — admins go to console, regular users land
              on shipments tab (filtered by the most-common stage). */}
          <TouchableOpacity
            style={styles.viewAllBtn}
            onPress={() => {
              if (isAdmin) {
                router.push("/admin/sla-alerts" as any);
              } else {
                // Pick the stage with the most breaches so the
                // shipments tab opens to the relevant filter.
                const top = Object.entries(counts)
                  .sort(([, a], [, b]) => b - a)[0]?.[0] || "Pending";
                router.push({
                  pathname: "/(tabs)/shipments",
                  params: { status: top },
                } as any);
              }
            }}
          >
            <Text style={styles.viewAllText}>
              View all {alerts.length} alert{alerts.length === 1 ? "" : "s"}
            </Text>
            <Ionicons name="arrow-forward" size={14} color="#DC2626" />
          </TouchableOpacity>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FEF2F2",
    borderRadius: 12,
    padding: 12,
    marginHorizontal: 16,
    marginTop: 14,
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
  },
  headerLeft: { flexDirection: "row", alignItems: "center", gap: 6 },
  headerTitle: { fontSize: 14, fontWeight: "800", color: "#7F1D1D" },
  countPill: {
    backgroundColor: "#DC2626",
    paddingHorizontal: 8, paddingVertical: 2,
    borderRadius: 999, minWidth: 26, alignItems: "center",
  },
  countPillText: { color: "#fff", fontSize: 11, fontWeight: "800" },
  subtitle:  { fontSize: 11.5, color: "#991B1B", marginTop: 4 },
  chipRow:   { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 10 },
  chip: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999,
    borderWidth: 1,
  },
  chipText: { fontSize: 10.5, fontWeight: "800", letterSpacing: 0.3 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 8, paddingHorizontal: 6, marginTop: 8,
    backgroundColor: "#fff", borderRadius: 8,
    borderWidth: 1, borderColor: "#FCA5A5",
  },
  stageDot: { width: 8, height: 8, borderRadius: 4 },
  rowName:  { fontSize: 13, fontWeight: "800", color: "#111827" },
  rowPri:   { fontSize: 10.5, fontWeight: "700" },
  rowMeta:  { fontSize: 10.5, color: "#6B7280", marginTop: 2 },
  viewAllBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5,
    paddingVertical: 9, marginTop: 10, borderRadius: 8,
    borderWidth: 1, borderColor: "#FCA5A5",
    backgroundColor: "#fff",
  },
  viewAllText: { color: "#DC2626", fontSize: 12, fontWeight: "800" },
});
