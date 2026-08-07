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
          ) : batches.map((b) => {
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
});
