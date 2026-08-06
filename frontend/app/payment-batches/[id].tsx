// ==============================================================
// Phase F9 — Payment Batch drill-down screen.
// --------------------------------------------------------------
// Header  : batch meta (ref#, mode, date, totals) + Delete button.
// Body    : list of shipments belonging to the batch, sourced via
//           /shipments?payment_batch_id=<id>. Each row has a
//           swipe-style Remove button that unlinks the shipment
//           from this batch (does NOT delete the shipment).
// Actions : Export CSV (Sharing.shareAsync of a client-generated
//           CSV) and a footer "Jump to filtered Shipments" link.
// ==============================================================
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Alert, FlatList, RefreshControl, StyleSheet,
  Text, TouchableOpacity, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import * as Sharing from "expo-sharing";
// expo-file-system v19 moved the sync `cacheDirectory` + `EncodingType`
// API into the /legacy submodule. New SAF-based API is Promise-based;
// for a one-shot CSV export the legacy sync API is simpler + matches
// the pattern used elsewhere in the app (shipments.tsx packing slip).
import * as FileSystem from "expo-file-system/legacy";

import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";

type Batch = Awaited<ReturnType<typeof Api.getPaymentBatch>>;
type Shipment = {
  id: string;
  tracking_id?: string;
  customer_name?: string;
  customer_phone?: string;
  cod_collected_amount?: number;
  cod_amount?: number;
  total_amount?: number;
  status?: string;
  city?: string;
};

const MODE_LABEL: Record<string, string> = {
  cheque: "Cheque", neft: "NEFT", bank_transfer: "Bank Transfer",
  upi: "UPI", other: "Other",
};

export default function BatchDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const [batch, setBatch]           = useState<Batch | null>(null);
  const [shipments, setShipments]   = useState<Shipment[]>([]);
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [exporting, setExporting]   = useState(false);
  const [removing, setRemoving]     = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const [b, sr] = await Promise.all([
        Api.getPaymentBatch(id),
        Api.listShipments({ payment_batch_id: id, limit: 500 } as any).catch(() => []),
      ]);
      setBatch(b);
      setShipments(Array.isArray(sr) ? sr : (sr as any)?.shipments || []);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const amountFor = (s: Shipment) =>
    Number(s.cod_collected_amount ?? s.cod_amount ?? s.total_amount ?? 0);

  const csvContent = useMemo(() => {
    const header = [
      "Tracking ID", "Order Status", "Customer Name", "Phone", "City",
      "COD Amount (INR)",
    ].join(",");
    const rows = shipments.map((s) => [
      s.tracking_id || s.id,
      s.status || "",
      (s.customer_name || "").replace(/[",\n\r]/g, " "),
      s.customer_phone || "",
      (s.city || "").replace(/[",\n\r]/g, " "),
      amountFor(s).toFixed(2),
    ].join(","));
    return [header, ...rows].join("\n");
  }, [shipments]);

  const doExport = async () => {
    if (!batch) return;
    setExporting(true);
    try {
      const filename = `PaymentBatch_${(batch.reference_number || batch.id).replace(/\W+/g, "_")}.csv`;
      const uri = `${FileSystem.cacheDirectory}${filename}`;
      await FileSystem.writeAsStringAsync(uri, csvContent, {
        encoding: FileSystem.EncodingType.UTF8,
      });
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: "text/csv",
          UTI: "public.comma-separated-values-text",
          dialogTitle: `Export ${batch.name}`,
        });
      } else {
        Alert.alert("Saved", `CSV written to ${uri}`);
      }
    } catch (e: any) {
      Alert.alert("Export failed", e?.message || "Try again");
    } finally {
      setExporting(false);
    }
  };

  const confirmRemove = (s: Shipment) => {
    Alert.alert(
      "Remove from batch?",
      `${s.tracking_id || s.id} will be unlinked from this batch. The shipment itself is not deleted.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove", style: "destructive",
          onPress: async () => {
            setRemoving(s.id);
            try {
              const r = await Api.removeShipmentsFromBatch(id, [s.id]);
              setShipments((prev) => prev.filter((x) => x.id !== s.id));
              setBatch((prev) => prev ? {
                ...prev,
                total_articles: r.total_articles,
                total_amount:   r.total_amount,
              } : prev);
            } catch (e: any) {
              Alert.alert(
                "Remove failed",
                e?.response?.data?.detail || e?.message || "Try again",
              );
            } finally {
              setRemoving(null);
            }
          },
        },
      ],
    );
  };

  const confirmDelete = () => {
    if (!batch) return;
    Alert.alert(
      "Delete this batch?",
      `“${batch.name}” will be removed. The ${batch.total_articles} linked shipments will be unlinked (kept intact).`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.deletePaymentBatch(id);
              router.back();
            } catch (e: any) {
              Alert.alert(
                "Delete failed",
                e?.response?.data?.detail || e?.message || "Try again",
              );
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.root} edges={["top"]}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      </SafeAreaView>
    );
  }
  if (!batch) {
    return (
      <SafeAreaView style={styles.root} edges={["top"]}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.center}><Text>Batch not found</Text></View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={{ padding: 4 }}>
          <Ionicons name="chevron-back" size={26} color={colors.primary} />
        </TouchableOpacity>
        <Text style={styles.hTitle} numberOfLines={1}>{batch.name}</Text>
        <TouchableOpacity onPress={confirmDelete} style={{ padding: 4 }}>
          <Ionicons name="trash-outline" size={22} color="#B91C1C" />
        </TouchableOpacity>
      </View>

      {/* Meta card */}
      <View style={styles.metaCard}>
        <View style={styles.metaRow}>
          <Text style={styles.metaK}>Mode</Text>
          <Text style={styles.metaV}>
            {MODE_LABEL[batch.payment_mode] || batch.payment_mode}
          </Text>
        </View>
        <View style={styles.metaRow}>
          <Text style={styles.metaK}>Reference #</Text>
          <Text style={styles.metaV}>{batch.reference_number}</Text>
        </View>
        <View style={styles.metaRow}>
          <Text style={styles.metaK}>Payment Date</Text>
          <Text style={styles.metaV}>{(batch.payment_date || "").slice(0, 10)}</Text>
        </View>
        {batch.bank_name ? (
          <View style={styles.metaRow}>
            <Text style={styles.metaK}>Bank</Text>
            <Text style={styles.metaV}>{batch.bank_name}</Text>
          </View>
        ) : null}
        <View style={[styles.metaRow, { marginTop: 6 }]}>
          <View style={styles.totalPill}>
            <Text style={styles.totalPillTxt}>
              📦 {batch.total_articles} article{batch.total_articles === 1 ? "" : "s"}
            </Text>
          </View>
          <View style={[styles.totalPill, { backgroundColor: "#DCFCE7" }]}>
            <Text style={[styles.totalPillTxt, { color: "#166534" }]}>
              ₹{Number(batch.total_amount || 0).toLocaleString("en-IN")}
            </Text>
          </View>
        </View>
        <TouchableOpacity
          style={styles.exportBtn}
          onPress={doExport}
          disabled={exporting || shipments.length === 0}
        >
          {exporting ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <>
              <Ionicons name="download-outline" size={16} color="#fff" />
              <Text style={styles.exportTxt}>Export CSV</Text>
            </>
          )}
        </TouchableOpacity>
        {/* Phase F9.1 — Explicit labeled "Delete Batch" button in
            addition to the trash icon in the header. Merchants who
            accidentally create a batch (e.g. wrong reference number)
            want an obvious, one-tap route to remove the whole thing
            and keep the database clean. Unlinks the shipments —
            never deletes them. */}
        <TouchableOpacity
          style={styles.deleteBtn}
          onPress={confirmDelete}
        >
          <Ionicons name="trash-outline" size={16} color="#B91C1C" />
          <Text style={styles.deleteBtnTxt}>Delete Batch</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.listHead}>
        <Text style={styles.listHeadTxt}>
          Shipments ({shipments.length})
        </Text>
        <TouchableOpacity
          onPress={() => router.push({
            pathname: "/(tabs)/shipments" as any,
            params: { payment_batch_id: id, payment_batch_label: batch.name },
          })}
        >
          <Text style={styles.linkTxt}>View in Shipments →</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        data={shipments}
        keyExtractor={(i) => i.id}
        contentContainerStyle={{ padding: 12, gap: 8, paddingBottom: 24 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={colors.primary}
          />
        }
        renderItem={({ item }) => (
          <View style={styles.shipCard}>
            <View style={{ flex: 1 }}>
              <Text style={styles.shipTitle} numberOfLines={1}>
                {item.tracking_id || item.id.slice(0, 8)}
              </Text>
              <Text style={styles.shipSub} numberOfLines={1}>
                {item.customer_name || "—"} · {item.status || "—"}
              </Text>
              <Text style={styles.shipAmount}>
                ₹{amountFor(item).toLocaleString("en-IN")}
              </Text>
            </View>
            <TouchableOpacity
              style={styles.removeBtn}
              onPress={() => confirmRemove(item)}
              disabled={removing === item.id}
            >
              {removing === item.id
                ? <ActivityIndicator size="small" color="#B91C1C" />
                : <Ionicons name="close-circle-outline" size={22} color="#B91C1C" />}
            </TouchableOpacity>
          </View>
        )}
        ListEmptyComponent={
          <View style={styles.emptyState}>
            <Ionicons name="cube-outline" size={48} color="#CBD5E1" />
            <Text style={styles.emptyTxt}>No shipments in this batch</Text>
            <Text style={styles.emptySub}>
              Ship-to-batch assignments can be added by re-running a
              COD-Payment import with this batch&apos;s reference number.
            </Text>
          </View>
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F8FAFC" },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 8, paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: 1, borderBottomColor: "#E2E8F0",
  },
  hTitle: { flex: 1, textAlign: "center", fontSize: 15.5, fontWeight: "800", color: "#0F172A" },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  metaCard: {
    backgroundColor: "#fff", margin: 12, padding: 14, borderRadius: 12,
    borderWidth: 1, borderColor: "#E2E8F0", gap: 6,
  },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  metaK: { fontSize: 12, color: "#64748B", width: 100 },
  metaV: { flex: 1, fontSize: 13, fontWeight: "700", color: "#0F172A" },
  totalPill: {
    paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 12, backgroundColor: "#EFF6FF",
  },
  totalPillTxt: { fontSize: 12, fontWeight: "800", color: "#1E40AF" },
  exportBtn: {
    marginTop: 10, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 6, backgroundColor: colors.primary, paddingVertical: 10, borderRadius: 8,
  },
  exportTxt: { fontSize: 13, fontWeight: "800", color: "#fff" },
  // Phase F9.1 — explicit Delete Batch pill (paired with the header
  // trash icon). Red border + red label to unambiguously communicate
  // the destructive nature of the action.
  deleteBtn: {
    marginTop: 8,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    backgroundColor: "#FEF2F2",
    borderWidth: 1,
    borderColor: "#FECACA",
    paddingVertical: 10,
    borderRadius: 8,
  },
  deleteBtnTxt: { fontSize: 13, fontWeight: "800", color: "#B91C1C" },
  listHead: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingHorizontal: 14, paddingBottom: 6,
  },
  listHeadTxt: { fontSize: 13, fontWeight: "700", color: "#334155" },
  linkTxt:     { fontSize: 12.5, fontWeight: "700", color: colors.primary },
  shipCard: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#fff", padding: 12, borderRadius: 10,
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  shipTitle:  { fontSize: 13.5, fontWeight: "800", color: "#0F172A" },
  shipSub:    { fontSize: 11.5, color: "#64748B", marginTop: 2 },
  shipAmount: { fontSize: 12, fontWeight: "700", color: "#166534", marginTop: 3 },
  removeBtn:  { padding: 6 },
  emptyState: { alignItems: "center", padding: 24, gap: 6, marginTop: 24 },
  emptyTxt:   { fontSize: 14, fontWeight: "700", color: "#475569" },
  emptySub:   { fontSize: 12, color: "#94A3B8", textAlign: "center", lineHeight: 17 },
});
