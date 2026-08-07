/**
 * /shipment-import-history — Phase F6.0.
 *
 * Lists all past shipment-import batches for the current user with
 * quick-glance counts (updated / mismatch / unmatched). Tap a row to
 * drill into per-row details + download mismatches CSV.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet, RefreshControl,
  ActivityIndicator, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";
import PhIcon from "../components/PhIcon";

type Batch = Awaited<ReturnType<typeof Api.listShipmentImportBatches>>["batches"][number];

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

export default function ShipmentImportHistoryScreen() {
  const router = useRouter();
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [batches, setBatches]       = useState<Batch[]>([]);
  // Phase F11.B — Batch History status filter. Local (client-side)
  // filter over the loaded batches array. The chip semantics:
  //   • all              — no filter
  //   • clean            — updated>0 AND mismatch=0 AND unmatched=0 AND errors=0
  //   • has_mismatch     — matched_mismatch > 0
  //   • has_unmatched    — unmatched > 0
  //   • has_errors       — errors > 0
  //   • has_junk         — junk_skipped > 0
  type StatusKey = "all" | "clean" | "has_mismatch" | "has_unmatched" | "has_errors" | "has_junk";
  const [statusFilter, setStatusFilter] = useState<StatusKey>("all");
  type TypeKey = "all" | "booking" | "delivery" | "cod_payment";
  const [typeFilter, setTypeFilter] = useState<TypeKey>("all");

  const load = useCallback(async () => {
    try {
      const r = await Api.listShipmentImportBatches(100);
      setBatches(r.batches || []);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  const onRefresh = () => { setRefreshing(true); load(); };

  // Apply client-side status + type filters. Order matters — type
  // filter runs first to keep the empty-state message sensible.
  const filteredBatches = batches.filter((b) => {
    if (typeFilter !== "all" && b.import_type !== typeFilter) return false;
    if (statusFilter === "all") return true;
    const mm = Number(b.matched_mismatch || 0);
    const um = Number(b.unmatched || 0);
    const er = Number((b as any).errors || 0);
    const jk = Number((b as any).junk_skipped || 0);
    const up = Number(b.matched_updated || 0);
    switch (statusFilter) {
      case "clean":         return up > 0 && mm === 0 && um === 0 && er === 0;
      case "has_mismatch":  return mm > 0;
      case "has_unmatched": return um > 0;
      case "has_errors":    return er > 0;
      case "has_junk":      return jk > 0;
    }
    return true;
  });

  const STATUS_CHIPS: { key: StatusKey; label: string; tint: string }[] = [
    { key: "all",           label: "All",              tint: "#0F172A" },
    { key: "clean",         label: "Clean",            tint: "#10B981" },
    { key: "has_mismatch",  label: "Has Mismatch",     tint: "#B45309" },
    { key: "has_unmatched", label: "Has Unmatched",    tint: "#DC2626" },
    { key: "has_errors",    label: "Has Errors",       tint: "#DC2626" },
    { key: "has_junk",      label: "Has Junk",         tint: "#94A3B8" },
  ];
  const TYPE_CHIPS: { key: TypeKey; label: string; tint: string }[] = [
    { key: "all",         label: "All Types",   tint: "#0F172A" },
    { key: "booking",     label: "Booking",     tint: "#2563EB" },
    { key: "delivery",    label: "Delivery",    tint: "#10B981" },
    { key: "cod_payment", label: "COD Payment", tint: "#F59E0B" },
  ];

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>Import History</Text>
        <TouchableOpacity onPress={() => router.push("/shipment-import" as any)} hitSlop={8}>
          <PhIcon name="add" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 12, paddingBottom: 40 }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        >
          {/* Phase F11.B — Batch History status + type filter chips.
              Two horizontal rows sitting above the batch list. Purely
              client-side filter over the already-loaded batches. */}
          {batches.length > 0 && (
            <>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.chipRow}
              >
                {TYPE_CHIPS.map((c) => {
                  const active = typeFilter === c.key;
                  return (
                    <TouchableOpacity
                      key={c.key}
                      onPress={() => setTypeFilter(c.key)}
                      style={[
                        styles.filterChip,
                        active && { backgroundColor: c.tint, borderColor: c.tint },
                      ]}
                    >
                      <Text style={[
                        styles.filterChipTxt,
                        { color: active ? "#fff" : c.tint },
                      ]}>
                        {c.label}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </ScrollView>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.chipRow}
              >
                {STATUS_CHIPS.map((c) => {
                  const active = statusFilter === c.key;
                  return (
                    <TouchableOpacity
                      key={c.key}
                      onPress={() => setStatusFilter(c.key)}
                      style={[
                        styles.filterChip,
                        active && { backgroundColor: c.tint, borderColor: c.tint },
                      ]}
                    >
                      <Text style={[
                        styles.filterChipTxt,
                        { color: active ? "#fff" : c.tint },
                      ]}>
                        {c.label}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </ScrollView>
            </>
          )}

          {batches.length === 0 ? (
            <View style={styles.empty}>
              <PhIcon name="cloud-upload-outline" size={44} color="#CBD5E1" />
              <Text style={styles.emptyTitle}>No imports yet</Text>
              <Text style={styles.emptySub}>
                Upload a CSV/Excel from the Shipments tab to bulk-update tracking, delivery,
                or COD payment records.
              </Text>
              <TouchableOpacity
                style={styles.emptyBtn}
                onPress={() => router.push("/shipment-import" as any)}
              >
                <Text style={styles.emptyBtnTxt}>Start Import</Text>
              </TouchableOpacity>
            </View>
          ) : filteredBatches.length === 0 ? (
            <View style={styles.empty}>
              <PhIcon name="funnel-outline" size={44} color="#CBD5E1" />
              <Text style={styles.emptyTitle}>No batches match your filters</Text>
              <Text style={styles.emptySub}>Tap the &quot;All&quot; chips above to reset.</Text>
            </View>
          ) : filteredBatches.map((b) => {
            const meta = TYPE_META[b.import_type] || {
              label: b.import_type, tint: "#64748B", icon: "cube-outline",
            };
            const askDelete = () => {
              Alert.alert(
                "Delete this batch record?",
                `Are you sure you want to delete "${b.filename || "(no filename)"}"?\n\n` +
                "This removes only the import history entry. Shipments that were " +
                "already updated by this import will keep their new values.",
                [
                  { text: "Cancel", style: "cancel" },
                  {
                    text: "Delete",
                    style: "destructive",
                    onPress: async () => {
                      try {
                        await Api.deleteShipmentImportBatch(b.id);
                        setBatches((prev) => prev.filter((x) => x.id !== b.id));
                      } catch (e: any) {
                        Alert.alert(
                          "Delete failed",
                          e?.response?.data?.detail || e?.message || "Try again.",
                        );
                      }
                    },
                  },
                ],
              );
            };
            return (
              <TouchableOpacity
                key={b.id}
                style={styles.card}
                onPress={() => router.push(`/shipment-import-batch?id=${b.id}` as any)}
              >
                <View style={styles.cardTop}>
                  <View style={[styles.typePill, { backgroundColor: meta.tint + "22" }]}>
                    <PhIcon name={meta.icon} size={11} color={meta.tint} />
                    <Text style={[styles.typePillTxt, { color: meta.tint }]}>
                      {meta.label}
                    </Text>
                  </View>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                    <Text style={styles.whenTxt}>{fmtWhen(b.created_at)}</Text>
                    <TouchableOpacity
                      onPress={askDelete}
                      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                      style={styles.trashBtn}
                    >
                      <PhIcon name="trash-outline" size={16} color="#DC2626" />
                    </TouchableOpacity>
                  </View>
                </View>
                <Text style={styles.fileTxt} numberOfLines={1}>
                  📄 {b.filename || "(no filename)"} · {b.format.toUpperCase()}
                </Text>
                <View style={styles.statsRow}>
                  <Stat label="Total"    n={b.total_rows}       tint="#0F172A" />
                  <Stat label="Updated"  n={b.matched_updated}  tint="#10B981" />
                  <Stat label="Mismatch" n={b.matched_mismatch} tint="#B45309" />
                  <Stat label="Unmatch"  n={b.unmatched}        tint="#DC2626" />
                </View>
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function Stat({ label, n, tint }: { label: string; n: number; tint: string }) {
  return (
    <View style={{ flex: 1, alignItems: "center" }}>
      <Text style={[styles.statN, { color: tint }]}>{n}</Text>
      <Text style={styles.statL}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: "#F4F5F7" },
  header:  {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingVertical: 12,
  },
  title:   { fontSize: 17, fontWeight: "700", color: "#0F172A" },
  card:    {
    backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  cardTop: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 6 },
  typePill:{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10 },
  typePillTxt: { fontSize: 10, fontWeight: "800", letterSpacing: 0.4 },
  whenTxt: { fontSize: 11, color: "#94A3B8" },
  fileTxt: { fontSize: 13, color: "#0F172A", marginBottom: 8 },
  statsRow:{ flexDirection: "row", gap: 6, backgroundColor: "#F8FAFC", padding: 10, borderRadius: 8 },
  statN:   { fontSize: 16, fontWeight: "800" },
  statL:   { fontSize: 10, color: "#64748B", marginTop: 2 },
  empty:   { alignItems: "center", padding: 32, gap: 8 },
  emptyTitle: { fontSize: 15, fontWeight: "700", color: "#334155", marginTop: 6 },
  emptySub:{ fontSize: 12, color: "#64748B", textAlign: "center", lineHeight: 17 },
  emptyBtn:{ marginTop: 12, backgroundColor: colors.primary, paddingHorizontal: 20, paddingVertical: 10, borderRadius: 10 },
  emptyBtnTxt: { color: "#fff", fontWeight: "700", fontSize: 13 },
  trashBtn: {
    padding: 4, borderRadius: 6, backgroundColor: "#FEE2E2",
  },
  // Phase F11.B — Filter chip row (horizontal scroll).
  chipRow: {
    flexDirection: "row",
    gap: 8,
    paddingVertical: 4,
    paddingHorizontal: 2,
  },
  filterChip: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: "#CBD5E1",
    backgroundColor: "#fff",
  },
  filterChipTxt: {
    fontSize: 12,
    fontWeight: "700",
  },
});
