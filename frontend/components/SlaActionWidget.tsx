/**
 * SLA Action Required widget — Phase G5 (UI polish v2)
 * ----------------------------------------------------
 * Dashboard widget that surfaces every SLA breach impacting the
 * CURRENT user. Reads /api/me/sla/alerts which already honours the
 * admin-side `display_channels.banner` toggle: when the admin has
 * disabled the banner channel the widget self-hides.
 *
 * v2 layout (matches mock):
 *  • Stage chips on a single horizontal-scroll row (no wrap).
 *  • Top 3 alerts are stacked inside ONE white container with
 *    hairline dividers — uses the full width and adds a package
 *    box icon for visual balance instead of leaving white space.
 *  • Footer becomes a solid red "View all N alerts →" CTA.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator,
  ScrollView,
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

  // Per-stage counts for the chip row (sorted by stage workflow order).
  const STAGE_ORDER = ["Pending", "Processing", "Ready to Ship", "Shipped", "Delivered", "Feedback"];
  const counts: Record<string, number> = {};
  for (const a of alerts) counts[a.stage] = (counts[a.stage] || 0) + 1;
  const sortedStages = Object.entries(counts).sort(
    ([a], [b]) => STAGE_ORDER.indexOf(a) - STAGE_ORDER.indexOf(b),
  );

  const top3 = alerts.slice(0, 3);

  return (
    <View style={styles.card}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Ionicons name="alert-circle" size={20} color="#DC2626" />
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

          {/* Per-stage chips on a single horizontal-scroll row so they
              stay aligned and never wrap onto a second line. */}
          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipScroll}
          >
            {sortedStages.map(([stage, n]) => {
              const c = STAGE_COLOR[stage] || "#374151";
              return (
                <View
                  key={stage}
                  style={[styles.chip, { backgroundColor: c + "18", borderColor: c + "40" }]}
                >
                  <Text style={[styles.chipText, { color: c }]}>{stage} · {n}</Text>
                </View>
              );
            })}
          </ScrollView>

          {/* Single white container holding all 3 alert rows with
              hairline dividers — fills the available width. */}
          <View style={styles.rowsCard}>
            {top3.map((a, idx) => {
              const c = STAGE_COLOR[a.stage] || "#374151";
              const isLast = idx === top3.length - 1;
              return (
                <TouchableOpacity
                  key={a.id}
                  style={[styles.row, !isLast && styles.rowDivider]}
                  onPress={() => router.push(`/label/${a.shipment_id}` as any)}
                  activeOpacity={0.6}
                >
                  {/* Stage colour dot */}
                  <View style={[styles.stageDot, { backgroundColor: c }]} />

                  {/* Package box icon — fills the otherwise empty column
                      and gives the row vertical anchor. */}
                  <View style={styles.boxIconWrap}>
                    <Text style={styles.boxIcon}>📦</Text>
                  </View>

                  {/* Customer name + priority on first line, meta on second */}
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <View style={styles.rowTitleLine}>
                      <Text style={styles.rowName} numberOfLines={1}>
                        {a.shipment?.customer_name || "(unnamed)"}
                      </Text>
                      {!!a.priority && (
                        <Text style={[styles.rowPri, { color: PRIORITY_COLOR(a.priority) }]}>
                          {" · "}{a.priority.toUpperCase()}
                          {a.level && a.level > 1 ? ` · L${a.level}` : ""}
                        </Text>
                      )}
                    </View>
                    <Text style={styles.rowMeta} numberOfLines={1}>
                      {a.shipment?.order_id || a.shipment?.tracking_id || a.shipment_id?.slice(0, 8)}
                      {"  ·  ⏱ "}{a.days_overdue.toFixed(2)}d past {a.sla_days}d SLA
                    </Text>
                  </View>

                  <Ionicons name="chevron-forward" size={18} color="#9CA3AF" />
                </TouchableOpacity>
              );
            })}
          </View>

          {/* Solid red "View all N alerts →" CTA (matches mock). */}
          <TouchableOpacity
            style={styles.viewAllBtn}
            activeOpacity={0.85}
            onPress={() => {
              if (isAdmin) {
                router.push("/admin/sla-alerts" as any);
              } else {
                // Pick the stage with the most breaches so the shipments
                // tab opens to the relevant filter.
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
            <Ionicons name="arrow-forward" size={16} color="#fff" />
          </TouchableOpacity>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: "#FEF2F2",
    borderRadius: 14,
    padding: 14,
    marginHorizontal: 16,
    marginTop: 14,
    borderWidth: 1,
    borderColor: "#FECACA",
  },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
  },
  headerLeft: { flexDirection: "row", alignItems: "center", gap: 8 },
  headerTitle: { fontSize: 16, fontWeight: "800", color: "#7F1D1D" },
  countPill: {
    backgroundColor: "#DC2626",
    paddingHorizontal: 10, paddingVertical: 3,
    borderRadius: 999, minWidth: 32, alignItems: "center",
  },
  countPillText: { color: "#fff", fontSize: 12, fontWeight: "800" },
  subtitle: { fontSize: 12.5, color: "#991B1B", marginTop: 4 },

  chipScroll: {
    gap: 6,
    paddingVertical: 10,
    paddingRight: 10,
  },
  chip: {
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999,
    borderWidth: 1,
  },
  chipText: { fontSize: 11, fontWeight: "800", letterSpacing: 0.3 },

  // Single white container for all 3 rows (uses full available width).
  rowsCard: {
    backgroundColor: "#fff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#FCA5A5",
    overflow: "hidden",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 10,
    paddingHorizontal: 10,
  },
  rowDivider: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#FEE2E2",
  },
  stageDot: { width: 8, height: 8, borderRadius: 4 },
  boxIconWrap: {
    width: 30, height: 30, borderRadius: 6,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#F9FAFB",
  },
  boxIcon: { fontSize: 18 },

  rowTitleLine: { flexDirection: "row", alignItems: "center" },
  rowName: { fontSize: 14, fontWeight: "800", color: "#111827", flexShrink: 1 },
  rowPri:  { fontSize: 11, fontWeight: "800" },
  rowMeta: { fontSize: 11.5, color: "#6B7280", marginTop: 2 },

  // Solid red CTA — matches mock.
  viewAllBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 13, marginTop: 12, borderRadius: 10,
    backgroundColor: "#DC2626",
  },
  viewAllText: { color: "#fff", fontSize: 13.5, fontWeight: "800" },
});
