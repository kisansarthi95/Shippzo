/**
 * /shipment-import-batch?id=… — Per-batch drill-down.
 *
 * Shows summary + per-row status list. Rows with mismatches expand to
 * reveal existing vs imported values. "Download Mismatch Report" button
 * fetches the CSV via authenticated axios and shares/downloads it.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet, RefreshControl,
  ActivityIndicator, Alert, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { api as axiosApi, Api } from "../lib/api";
import { colors } from "../lib/theme";
import PhIcon from "../components/PhIcon";

type Batch = Awaited<ReturnType<typeof Api.getShipmentImportBatch>>;
type Row = Batch["rows"][number];

const STATUS_META: Record<
  Row["status"],
  { label: string; tint: string; icon: any }
> = {
  matched_updated:   { label: "Updated",    tint: "#10B981", icon: "checkmark-circle" },
  matched_mismatch:  { label: "Mismatch",   tint: "#B45309", icon: "alert-circle" },
  matched_no_change: { label: "No Change",  tint: "#64748B", icon: "remove-circle" },
  unmatched:         { label: "Unmatched",  tint: "#DC2626", icon: "close-circle" },
  error:             { label: "Error",      tint: "#DC2626", icon: "warning" },
};

const TYPE_LABEL: Record<string, string> = {
  booking: "Booking Update",
  delivery: "Delivery Update",
  cod_payment: "COD Payment",
};

const FILTERS: (Row["status"] | "all")[] = [
  "all", "matched_updated", "matched_mismatch", "unmatched", "matched_no_change", "error",
];

export default function ShipmentImportBatchScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id?: string }>();

  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [batch, setBatch]           = useState<Batch | null>(null);
  const [filter, setFilter]         = useState<(Row["status"] | "all")>("all");
  const [downloading, setDownloading] = useState(false);
  const [expandedRow, setExpandedRow] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!id) { setLoading(false); return; }
    try {
      const b = await Api.getShipmentImportBatch(String(id));
      setBatch(b);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Not found.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);
  const onRefresh = () => { setRefreshing(true); load(); };

  const filteredRows = useMemo(() => {
    if (!batch) return [];
    if (filter === "all") return batch.rows;
    return batch.rows.filter((r) => r.status === filter);
  }, [batch, filter]);

  const downloadMismatches = async () => {
    if (!batch) return;
    setDownloading(true);
    try {
      // Use authenticated axios instance to fetch CSV blob.
      const res = await axiosApi.get(
        Api.shipmentImportMismatchesUrl(batch.id),
        { responseType: Platform.OS === "web" ? "blob" : "text" },
      );
      const filename = `shipment_import_mismatches_${batch.id.slice(0, 8)}.csv`;

      if (Platform.OS === "web") {
        const blob: Blob = res.data as any;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = filename;
        document.body.appendChild(a); a.click();
        a.remove(); URL.revokeObjectURL(url);
      } else {
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const FileSystem = require("expo-file-system/legacy");
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const Sharing = require("expo-sharing");
        const path = `${FileSystem.cacheDirectory || ""}${filename}`;
        await FileSystem.writeAsStringAsync(path, res.data as any, {
          encoding: FileSystem.EncodingType.UTF8,
        });
        if (await Sharing.isAvailableAsync()) {
          await Sharing.shareAsync(path, {
            mimeType: "text/csv",
            dialogTitle: "Shipment Import Mismatches",
            UTI: "public.comma-separated-values-text",
          });
        } else {
          Alert.alert("Saved", `Report saved to ${path}`);
        }
      }
    } catch (e: any) {
      Alert.alert(
        "Download failed",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setDownloading(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <ActivityIndicator style={{ marginTop: 40 }} color={colors.primary} />
      </SafeAreaView>
    );
  }
  if (!batch) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
            <PhIcon name="arrow-back" size={22} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.title}>Batch not found</Text>
          <View style={{ width: 22 }} />
        </View>
      </SafeAreaView>
    );
  }

  const mismatchOrUnmatched =
    (batch.matched_mismatch || 0) + (batch.unmatched || 0) + (batch.errors || 0);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>
          {TYPE_LABEL[batch.import_type] || batch.import_type}
        </Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView
        contentContainerStyle={{ padding: 12, paddingBottom: 60 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        <View style={styles.summaryCard}>
          <Text style={styles.summaryFile} numberOfLines={1}>
            📄 {batch.filename || "(no filename)"} · {batch.format.toUpperCase()}
          </Text>
          <Text style={styles.summaryWhen}>
            {new Date(batch.created_at).toLocaleString()}
          </Text>
          <View style={styles.statsGrid}>
            <StatBox label="Total"    n={batch.total_rows}         tint="#0F172A" />
            <StatBox label="Updated"  n={batch.matched_updated}    tint="#10B981" />
            <StatBox label="Mismatch" n={batch.matched_mismatch}   tint="#B45309" />
            <StatBox label="Unmatched" n={batch.unmatched}          tint="#DC2626" />
            <StatBox label="No Change" n={batch.matched_no_change} tint="#64748B" />
            <StatBox label="Errors"   n={batch.errors}             tint="#DC2626" />
          </View>
          {mismatchOrUnmatched > 0 ? (
            <TouchableOpacity
              style={styles.dlBtn}
              onPress={downloadMismatches}
              disabled={downloading}
            >
              {downloading ? (
                <ActivityIndicator color="#fff" size="small" />
              ) : (
                <>
                  <PhIcon name="download-outline" size={16} color="#fff" />
                  <Text style={styles.dlBtnTxt}>
                    Download Mismatch Report ({mismatchOrUnmatched})
                  </Text>
                </>
              )}
            </TouchableOpacity>
          ) : null}
        </View>

        {/* Filter chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingVertical: 8, gap: 6 }}
        >
          {FILTERS.map((f) => {
            const active = filter === f;
            const label = f === "all" ? `All (${batch.rows.length})` :
              `${STATUS_META[f]?.label || f} (${
                batch.rows.filter((r) => r.status === f).length
              })`;
            return (
              <TouchableOpacity
                key={f}
                style={[styles.chip, active && styles.chipActive]}
                onPress={() => setFilter(f)}
              >
                <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>
                  {label}
                </Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        {filteredRows.map((r) => {
          const meta = STATUS_META[r.status];
          const hasMismatch = (r.mismatches || []).length > 0;
          const isOpen = expandedRow === r.row_index;
          const canExpand = hasMismatch || Object.keys(r.applied || {}).length > 0;
          return (
            <TouchableOpacity
              key={r.row_index}
              style={styles.rowCard}
              onPress={() => canExpand && setExpandedRow(isOpen ? null : r.row_index)}
              activeOpacity={canExpand ? 0.6 : 1}
            >
              <View style={styles.rowTop}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.rowTrack} numberOfLines={1}>
                    {r.tracking_id || "(no tracking)"}
                  </Text>
                  <Text style={styles.rowIdx}>Row {r.row_index}</Text>
                </View>
                <View style={[styles.statusChip, { backgroundColor: meta.tint + "22" }]}>
                  <PhIcon name={meta.icon} size={11} color={meta.tint} />
                  <Text style={[styles.statusChipTxt, { color: meta.tint }]}>
                    {meta.label}
                  </Text>
                </View>
              </View>

              {isOpen ? (
                <View style={styles.rowExpand}>
                  {hasMismatch ? (
                    <View style={{ marginBottom: 8 }}>
                      <Text style={styles.expandLabel}>⚠️ Mismatches (existing preserved)</Text>
                      {(r.mismatches || []).map((mm, i) => (
                        <View key={i} style={styles.mmRow}>
                          <Text style={styles.mmField}>{mm.field}</Text>
                          <View style={styles.mmValues}>
                            <View style={styles.mmVal}>
                              <Text style={styles.mmValLbl}>Existing (kept)</Text>
                              <Text style={styles.mmValTxt}>{String(mm.existing ?? "")}</Text>
                            </View>
                            <View style={styles.mmVal}>
                              <Text style={styles.mmValLbl}>Imported</Text>
                              <Text style={[styles.mmValTxt, { color: "#B45309" }]}>
                                {String(mm.imported ?? "")}
                              </Text>
                            </View>
                          </View>
                        </View>
                      ))}
                    </View>
                  ) : null}
                  {Object.keys(r.applied || {}).length > 0 ? (
                    <View>
                      <Text style={styles.expandLabel}>✅ Applied Updates</Text>
                      {Object.entries(r.applied).map(([k, v]) => (
                        <View key={k} style={styles.appliedRow}>
                          <Text style={styles.appliedKey}>{k}</Text>
                          <Text style={styles.appliedVal} numberOfLines={2}>
                            {typeof v === "object" ? JSON.stringify(v) : String(v ?? "")}
                          </Text>
                        </View>
                      ))}
                    </View>
                  ) : null}
                  {r.error ? (
                    <Text style={{ color: "#DC2626", fontSize: 12, marginTop: 6 }}>
                      Error: {r.error}
                    </Text>
                  ) : null}
                </View>
              ) : (
                canExpand ? (
                  <Text style={styles.rowHint}>
                    Tap to expand · {hasMismatch ? `${r.mismatches.length} mismatch(es)` : `${Object.keys(r.applied || {}).length} update(s)`}
                  </Text>
                ) : null
              )}
            </TouchableOpacity>
          );
        })}
      </ScrollView>
    </SafeAreaView>
  );
}

function StatBox({ label, n, tint }: { label: string; n: number; tint: string }) {
  return (
    <View style={styles.statBox}>
      <Text style={[styles.statBoxN, { color: tint }]}>{n}</Text>
      <Text style={styles.statBoxL}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: "#F4F5F7" },
  header:  {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingVertical: 12, gap: 12,
  },
  title:   { fontSize: 16, fontWeight: "700", color: "#0F172A", flex: 1, textAlign: "center" },

  summaryCard: {
    backgroundColor: "#fff", borderRadius: 14, padding: 14, borderWidth: 1, borderColor: "#E5E7EB",
  },
  summaryFile: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  summaryWhen: { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  statsGrid: { flexDirection: "row", flexWrap: "wrap", marginTop: 10, gap: 6 },
  statBox:  {
    width: "31%", padding: 10, backgroundColor: "#F8FAFC", borderRadius: 8, alignItems: "center",
  },
  statBoxN: { fontSize: 18, fontWeight: "800" },
  statBoxL: { fontSize: 10, color: "#64748B", marginTop: 2 },
  dlBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    marginTop: 12, backgroundColor: "#B45309", padding: 12, borderRadius: 8,
  },
  dlBtnTxt: { color: "#fff", fontWeight: "700", fontSize: 13 },

  chip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 16,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipTxt: { fontSize: 12, color: "#334155", fontWeight: "600" },
  chipTxtActive: { color: "#fff" },

  rowCard: {
    backgroundColor: "#fff", borderRadius: 10, padding: 12, marginTop: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  rowTop:   { flexDirection: "row", alignItems: "center", gap: 8 },
  rowTrack: { fontSize: 13, fontWeight: "700", color: "#0F172A" },
  rowIdx:   { fontSize: 10, color: "#94A3B8", marginTop: 1 },
  statusChip:{
    flexDirection: "row", alignItems: "center", gap: 3,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10,
  },
  statusChipTxt: { fontSize: 10, fontWeight: "800", letterSpacing: 0.3 },
  rowHint:  { fontSize: 10, color: "#94A3B8", marginTop: 6, fontStyle: "italic" },

  rowExpand: {
    marginTop: 10, paddingTop: 10, borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  expandLabel: { fontSize: 11, fontWeight: "800", color: "#334155", marginBottom: 6 },
  mmRow: {
    marginBottom: 8, padding: 8, backgroundColor: "#FFFBEB",
    borderLeftWidth: 3, borderLeftColor: "#F59E0B", borderRadius: 6,
  },
  mmField:   { fontSize: 12, fontWeight: "700", color: "#78350F", marginBottom: 4 },
  mmValues:  { flexDirection: "row", gap: 8 },
  mmVal:     { flex: 1 },
  mmValLbl:  { fontSize: 9, color: "#78350F", textTransform: "uppercase", fontWeight: "700" },
  mmValTxt:  { fontSize: 12, color: "#0F172A", marginTop: 2 },

  appliedRow: {
    flexDirection: "row", justifyContent: "space-between",
    paddingVertical: 4, borderBottomWidth: 1, borderBottomColor: "#F1F5F9",
  },
  appliedKey: { fontSize: 11, color: "#64748B", flex: 1 },
  appliedVal: { fontSize: 12, color: "#0F172A", flex: 2, textAlign: "right" },
});
