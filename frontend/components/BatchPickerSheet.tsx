/**
 * BatchPickerSheet — Phase F6.3.
 *
 * Reusable single-select picker for both:
 *   • Import Batch  (from /api/shipments/import/batches)
 *   • Payment Batch (from /api/shipments/payment-batches)
 *
 * Opens as a bottom sheet with a search bar + scrollable list. Tap a
 * row to select and close — parent component receives {id,label,sub}.
 * "Clear selection" pill sits at the top.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, Pressable, ScrollView,
  TextInput, ActivityIndicator,
} from "react-native";
import PhIcon from "./PhIcon";
import { colors } from "../lib/theme";
import { Api } from "../lib/api";

export type PickedBatch = { id: string; label: string; sub?: string } | null;

type Props = {
  visible: boolean;
  onClose: () => void;
  kind: "import" | "payment";
  current: PickedBatch;
  onSelect: (b: PickedBatch) => void;
};

const TYPE_TINT: Record<string, string> = {
  booking: "#2563EB",
  delivery: "#10B981",
  cod_payment: "#F59E0B",
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

type ListItem = {
  id: string;
  title: string;      // primary label
  subtitle: string;   // date · type · counts
  extra?: string;     // extra tag line
  tint: string;
};

export default function BatchPickerSheet({
  visible, onClose, kind, current, onSelect,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [items, setItems]     = useState<ListItem[]>([]);
  const [q, setQ]             = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      if (kind === "import") {
        const r = await Api.listShipmentImportBatches(200);
        const out: ListItem[] = (r.batches || []).map((b) => ({
          id: b.id,
          title: b.filename || "(no filename)",
          subtitle: `${b.import_type.toUpperCase()} · ${fmtWhen(b.created_at)}`,
          extra: `${b.total_rows} rows · ${b.matched_updated} updated · ${b.matched_mismatch} mismatch · ${b.unmatched} unmatched`,
          tint: TYPE_TINT[b.import_type] || "#64748B",
        }));
        setItems(out);
      } else {
        const r = await Api.listPaymentBatches({ limit: 200 });
        const out: ListItem[] = (r.batches || []).map((b) => ({
          id: b.id,
          title: b.name || `Batch ${b.id.slice(0, 8)}`,
          subtitle: `${b.payment_mode.toUpperCase()} · ${b.payment_date} · ${b.reference_number}`,
          extra: `${b.total_articles} articles · ₹${b.total_amount.toLocaleString()}`,
          tint: "#F59E0B",
        }));
        setItems(out);
      }
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    if (visible) load();
  }, [visible, load]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter(
      (i) =>
        i.title.toLowerCase().includes(s) ||
        i.subtitle.toLowerCase().includes(s) ||
        (i.extra || "").toLowerCase().includes(s) ||
        i.id.toLowerCase().includes(s),
    );
  }, [items, q]);

  const title = kind === "import" ? "Pick Import Batch" : "Pick Payment Batch";

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{title}</Text>
            <TouchableOpacity onPress={onClose} hitSlop={8}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>

          <View style={styles.searchWrap}>
            <PhIcon name="search" size={14} color="#94A3B8" />
            <TextInput
              style={styles.searchInput}
              placeholder={
                kind === "import"
                  ? "Search filename / type…"
                  : "Search name / cheque no. / UTR…"
              }
              placeholderTextColor="#94A3B8"
              value={q}
              onChangeText={setQ}
              autoCorrect={false}
            />
          </View>

          {current ? (
            <TouchableOpacity
              style={styles.clearPill}
              onPress={() => { onSelect(null); onClose(); }}
            >
              <PhIcon name="close-circle" size={14} color="#DC2626" />
              <Text style={styles.clearPillTxt}>Clear selection</Text>
            </TouchableOpacity>
          ) : null}

          {loading ? (
            <ActivityIndicator style={{ marginTop: 30 }} color={colors.primary} />
          ) : filtered.length === 0 ? (
            <Text style={styles.empty}>
              {q.trim() ? "No batches match your search." :
                kind === "import"
                  ? "No import batches yet. Upload a Shipments CSV first."
                  : "No payment batches yet. Create one via a COD Payment import."}
            </Text>
          ) : (
            <ScrollView style={{ maxHeight: 460 }}>
              {filtered.map((b) => {
                const active = current?.id === b.id;
                return (
                  <TouchableOpacity
                    key={b.id}
                    style={[styles.row, active && styles.rowActive]}
                    onPress={() => {
                      onSelect({ id: b.id, label: b.title, sub: b.subtitle });
                      onClose();
                    }}
                  >
                    <View style={[styles.dot, { backgroundColor: b.tint }]} />
                    <View style={{ flex: 1 }}>
                      <Text style={styles.rowTitle} numberOfLines={1}>{b.title}</Text>
                      <Text style={styles.rowSub}   numberOfLines={1}>{b.subtitle}</Text>
                      {!!b.extra && (
                        <Text style={styles.rowExtra} numberOfLines={1}>{b.extra}</Text>
                      )}
                    </View>
                    {active ? (
                      <PhIcon name="checkmark-circle" size={20} color={colors.primary} />
                    ) : (
                      <PhIcon name="chevron-forward" size={16} color="#CBD5E1" />
                    )}
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root:     { flex: 1, justifyContent: "flex-end" },
  backdrop: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet:    {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingHorizontal: 16, paddingTop: 12, paddingBottom: 20,
    maxHeight: "88%",
  },
  header:   { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 12 },
  title:    { fontSize: 16, fontWeight: "800", color: colors.text },
  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F1F5F9", borderRadius: 10, paddingHorizontal: 12, marginBottom: 10,
  },
  searchInput: { flex: 1, height: 40, color: colors.text, fontSize: 14 },
  clearPill: {
    alignSelf: "flex-start",
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#FEF2F2", paddingHorizontal: 10, paddingVertical: 5, borderRadius: 12,
    marginBottom: 8,
  },
  clearPillTxt: { color: "#DC2626", fontWeight: "700", fontSize: 12 },
  empty:    { color: "#94A3B8", textAlign: "center", padding: 24, fontSize: 13 },
  row:      {
    flexDirection: "row", alignItems: "center", gap: 10,
    padding: 12, borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 10,
    marginBottom: 8, backgroundColor: "#fff",
  },
  rowActive:{ borderColor: colors.primary, backgroundColor: `${colors.primary}0D` },
  dot:      { width: 8, height: 8, borderRadius: 4 },
  rowTitle: { fontSize: 13, fontWeight: "700", color: colors.text },
  rowSub:   { fontSize: 11, color: "#64748B", marginTop: 2 },
  rowExtra: { fontSize: 10, color: "#94A3B8", marginTop: 2 },
});
