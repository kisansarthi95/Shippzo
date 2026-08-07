/**
 * /article-mismatches — Phase F11.A.
 *
 * Aggregated view of tracking ids that appeared in past bulk uploads
 * (Booking / Delivery / COD Payment) but did NOT match any shipment
 * in the merchant's DB. Junk cells (Total / RS / N-A / etc) are
 * dropped server-side at import time so this list stays actionable —
 * every row here is a real backlog item the merchant needs to book
 * (or investigate why the courier is remitting it).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet,
  RefreshControl, ActivityIndicator, Alert,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";
import PhIcon from "../components/PhIcon";

type Item = Awaited<ReturnType<typeof Api.articleMismatches>>["items"][number];
type TypeKey = "all" | "booking" | "delivery" | "cod_payment";

const TYPE_META: Record<string, { label: string; tint: string; icon: any }> = {
  booking:     { label: "Booking",     tint: "#2563EB", icon: "cube-outline" },
  delivery:    { label: "Delivery",    tint: "#10B981", icon: "checkmark-done-outline" },
  cod_payment: { label: "COD Payment", tint: "#F59E0B", icon: "cash-outline" },
};

function fmtWhen(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function ArticleMismatchesScreen() {
  const router = useRouter();
  const [items, setItems]     = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter]   = useState<TypeKey>("all");

  const load = useCallback(async () => {
    try {
      const r = await Api.articleMismatches(
        filter === "all" ? undefined : filter,
      );
      setItems(r.items || []);
    } catch (e: any) {
      Alert.alert(
        "Couldn't load",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [filter]);

  useEffect(() => { load(); }, [load]);

  const totals = useMemo(() => {
    let booking = 0, delivery = 0, cod_payment = 0;
    for (const it of items) {
      if (it.last_import_type === "booking")     booking++;
      if (it.last_import_type === "delivery")    delivery++;
      if (it.last_import_type === "cod_payment") cod_payment++;
    }
    return { booking, delivery, cod_payment, total: items.length };
  }, [items]);

  const copyAll = async () => {
    const text = items.map((i) => i.tracking_id).join("\n");
    await Clipboard.setStringAsync(text);
    Alert.alert("Copied", `${items.length} tracking numbers copied to clipboard.`);
  };

  const copyOne = async (t: string) => {
    await Clipboard.setStringAsync(t);
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>Article Number Mismatch</Text>
        <TouchableOpacity
          onPress={copyAll}
          hitSlop={8}
          disabled={!items.length}
          style={{ opacity: items.length ? 1 : 0.4 }}
        >
          <PhIcon name="copy-outline" size={20} color={colors.text} />
        </TouchableOpacity>
      </View>

      <Text style={styles.subtitle}>
        Tracking numbers found in your uploads that don&apos;t exist in your
        shipment list. Book them, or ask the courier why they were remitted.
      </Text>

      {/* Type filter chips */}
      <View style={styles.chipRow}>
        {(["all", "booking", "delivery", "cod_payment"] as TypeKey[]).map((k) => {
          const active = filter === k;
          const meta = k === "all"
            ? { label: "All", tint: "#0F172A" }
            : { label: TYPE_META[k].label, tint: TYPE_META[k].tint };
          return (
            <TouchableOpacity
              key={k}
              onPress={() => { setFilter(k); setLoading(true); }}
              style={[
                styles.chip,
                active && { backgroundColor: meta.tint, borderColor: meta.tint },
              ]}
            >
              <Text style={[
                styles.chipTxt,
                { color: active ? "#fff" : meta.tint },
              ]}>
                {meta.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {/* Summary card */}
      <View style={styles.summary}>
        <SummaryStat label="Total"        n={totals.total}       tint="#0F172A" />
        <SummaryStat label="Booking"      n={totals.booking}     tint="#2563EB" />
        <SummaryStat label="Delivery"     n={totals.delivery}    tint="#10B981" />
        <SummaryStat label="COD Payment"  n={totals.cod_payment} tint="#F59E0B" />
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 12, paddingBottom: 32 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
          />
        }
      >
        {loading ? (
          <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
        ) : items.length === 0 ? (
          <View style={styles.emptyBox}>
            <PhIcon name="checkmark-circle-outline" size={40} color="#10B981" />
            <Text style={styles.emptyTxt}>No article mismatches</Text>
            <Text style={styles.emptySub}>
              Every tracking number in your recent uploads matched a shipment
              in your DB. Clean data, well done!
            </Text>
          </View>
        ) : items.map((it) => {
          const meta = TYPE_META[it.last_import_type] || {
            label: it.last_import_type, tint: "#64748B", icon: "cube-outline",
          };
          return (
            <View key={it.tracking_id} style={styles.card}>
              <View style={styles.cardTop}>
                <View style={[styles.typePill, { backgroundColor: meta.tint + "22" }]}>
                  <PhIcon name={meta.icon} size={11} color={meta.tint} />
                  <Text style={[styles.typePillTxt, { color: meta.tint }]}>
                    {meta.label}
                  </Text>
                </View>
                <Text style={styles.whenTxt}>{fmtWhen(it.last_seen)}</Text>
              </View>
              <TouchableOpacity
                onPress={() => copyOne(it.tracking_id)}
                onLongPress={() => copyOne(it.tracking_id)}
                style={styles.trackRow}
              >
                <Text style={styles.trackTxt} selectable>
                  {it.tracking_id}
                </Text>
                <PhIcon name="copy-outline" size={14} color="#64748B" />
              </TouchableOpacity>
              <View style={styles.metaRow}>
                <Text style={styles.metaTxt}>
                  Seen in <Text style={styles.metaStrong}>{it.occurrence}</Text> row
                  {it.occurrence === 1 ? "" : "s"} across{" "}
                  <Text style={styles.metaStrong}>{it.batches.length}</Text>{" "}
                  batch{it.batches.length === 1 ? "" : "es"}
                </Text>
              </View>
              {it.batches.slice(0, 3).map((b) => (
                <TouchableOpacity
                  key={b.id}
                  onPress={() => router.push(`/shipment-import-batch?id=${b.id}` as any)}
                  style={styles.batchRow}
                >
                  <PhIcon name="folder-outline" size={12} color="#64748B" />
                  <Text style={styles.batchTxt} numberOfLines={1}>
                    {b.filename || "(no filename)"}
                  </Text>
                  <Text style={styles.batchWhen}>{fmtWhen(b.created_at)}</Text>
                </TouchableOpacity>
              ))}
              {it.batches.length > 3 && (
                <Text style={styles.batchMore}>+{it.batches.length - 3} more batch{it.batches.length - 3 === 1 ? "" : "es"}</Text>
              )}
            </View>
          );
        })}
      </ScrollView>
    </SafeAreaView>
  );
}

function SummaryStat({ label, n, tint }: { label: string; n: number; tint: string }) {
  return (
    <View style={styles.statCell}>
      <Text style={[styles.statN, { color: tint }]}>{n}</Text>
      <Text style={styles.statLbl}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    height: 52, paddingHorizontal: 14,
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  title: { flex: 1, textAlign: "center", fontSize: 16, fontWeight: "800", color: colors.text },
  subtitle: {
    fontSize: 12, color: "#64748B", paddingHorizontal: 14,
    paddingTop: 10, paddingBottom: 6, lineHeight: 17,
  },
  chipRow: {
    flexDirection: "row", flexWrap: "wrap", gap: 8,
    paddingHorizontal: 12, paddingBottom: 10,
  },
  chip: {
    paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 20, borderWidth: 1, borderColor: "#CBD5E1",
    backgroundColor: "#fff",
  },
  chipTxt: { fontSize: 12, fontWeight: "700" },
  summary: {
    marginHorizontal: 12, marginBottom: 8, padding: 12,
    borderRadius: 10, backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    flexDirection: "row", justifyContent: "space-around",
  },
  statCell: { alignItems: "center" },
  statN: { fontSize: 20, fontWeight: "800" },
  statLbl: { fontSize: 11, color: "#64748B", marginTop: 2 },
  card: {
    padding: 12, borderRadius: 12, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 10,
  },
  cardTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  typePill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  typePillTxt: { fontSize: 10.5, fontWeight: "800" },
  whenTxt: { fontSize: 11, color: "#94A3B8" },
  trackRow: {
    marginTop: 8, flexDirection: "row", alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8,
    backgroundColor: "#FEF3C7",
  },
  trackTxt: { fontSize: 14, fontWeight: "800", color: "#78350F", flex: 1 },
  metaRow: { marginTop: 8 },
  metaTxt: { fontSize: 11.5, color: "#64748B" },
  metaStrong: { fontWeight: "800", color: "#0F172A" },
  batchRow: {
    marginTop: 6, flexDirection: "row", alignItems: "center", gap: 6,
  },
  batchTxt: { fontSize: 11, color: "#334155", flex: 1 },
  batchWhen: { fontSize: 10, color: "#94A3B8" },
  batchMore: { fontSize: 10, color: "#94A3B8", marginTop: 4, fontStyle: "italic" },
  center: { padding: 40, alignItems: "center" },
  emptyBox: {
    marginTop: 40, alignItems: "center", padding: 24,
    backgroundColor: "#F0FDF4", borderRadius: 12,
    borderWidth: 1, borderColor: "#BBF7D0",
  },
  emptyTxt: {
    fontSize: 15, fontWeight: "800", color: "#166534",
    marginTop: 10,
  },
  emptySub: {
    fontSize: 12, color: "#065F46", textAlign: "center",
    marginTop: 6, lineHeight: 17,
  },
});
