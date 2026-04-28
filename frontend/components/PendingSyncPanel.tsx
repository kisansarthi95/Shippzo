/**
 * Per-item Pending Sync panel for the Settings screen.
 *
 * Shows every queued offline operation (create / update / delete /
 * status) so the user can:
 *   - See exactly what's waiting to sync
 *   - Manually retry the entire queue
 *   - Discard a single failed item
 *   - Clear all permanently-errored items in one tap
 *
 * The panel auto-refreshes whenever the queue mutates (via the
 * SyncQueue subscribe hook).
 */
import React, { useEffect, useState, useCallback } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Alert } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { SyncQueue, QueueItem } from "../lib/syncQueue";
import { colors } from "../lib/theme";

const TYPE_META: Record<string, { icon: string; label: string; tint: string; bg: string }> = {
  shipment_create: { icon: "add-circle-outline", label: "Create",       tint: "#1E40AF", bg: "#DBEAFE" },
  shipment_update: { icon: "create-outline",     label: "Update",       tint: "#92400E", bg: "#FEF3C7" },
  shipment_delete: { icon: "trash-outline",      label: "Delete",       tint: "#7F1D1D", bg: "#FEE2E2" },
  shipment_status: { icon: "checkmark-circle",   label: "Status",       tint: "#065F46", bg: "#D1FAE5" },
};

export default function PendingSyncPanel() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const all = await SyncQueue.getAll();
      setItems(all);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const unsub = SyncQueue.subscribe(refresh);
    return () => unsub();
  }, [refresh]);

  const onRetryAll = async () => {
    setBusy(true);
    try {
      const synced = await SyncQueue.flush();
      if (synced > 0) {
        Alert.alert("Synced", `${synced} item${synced === 1 ? "" : "s"} successfully synced.`);
      } else {
        Alert.alert("No progress", "Couldn't sync — check your connection and retry.");
      }
    } finally {
      setBusy(false);
    }
  };

  const onClearErrored = async () => {
    Alert.alert(
      "Discard failed items?",
      "Items that errored permanently (e.g. duplicate tracking ID) will be removed from the queue. This can't be undone.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Discard",
          style: "destructive",
          onPress: async () => {
            await SyncQueue.clearErrored();
          },
        },
      ],
    );
  };

  const onRemoveOne = async (item: QueueItem) => {
    Alert.alert(
      "Remove this item?",
      `${TYPE_META[item.type]?.label || item.type}: ${item.label || item.id}`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: async () => {
            await SyncQueue.remove(item.id);
          },
        },
      ],
    );
  };

  if (items.length === 0) {
    return (
      <View style={styles.emptyBox} testID="pending-sync-empty">
        <Ionicons name="cloud-done-outline" size={16} color="#16A34A" />
        <Text style={styles.emptyTxt}>All synced — no pending items</Text>
      </View>
    );
  }

  const erroredCount = items.filter((i) => i.permanent_error).length;
  const pendingCount = items.length - erroredCount;

  return (
    <View testID="pending-sync-panel">
      <View style={styles.summaryBar}>
        <Text style={styles.summaryTxt}>
          {pendingCount > 0 ? `${pendingCount} pending` : ""}
          {pendingCount > 0 && erroredCount > 0 ? " · " : ""}
          {erroredCount > 0 ? `${erroredCount} errored` : ""}
        </Text>
        <View style={{ flexDirection: "row", gap: 8 }}>
          <TouchableOpacity
            testID="retry-all-btn"
            onPress={onRetryAll}
            disabled={busy || pendingCount === 0}
            style={[styles.btn, (busy || pendingCount === 0) && styles.btnDisabled]}
          >
            <Ionicons name="refresh" size={13} color="#fff" />
            <Text style={styles.btnTxt}>{busy ? "Syncing…" : "Retry all"}</Text>
          </TouchableOpacity>
          {erroredCount > 0 && (
            <TouchableOpacity
              testID="clear-errored-btn"
              onPress={onClearErrored}
              style={[styles.btn, styles.btnGhost]}
            >
              <Ionicons name="trash-outline" size={13} color="#DC2626" />
              <Text style={[styles.btnTxt, { color: "#DC2626" }]}>Clear errors</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>

      {items.map((item) => {
        const meta = TYPE_META[item.type] || TYPE_META.shipment_create;
        const isErr = !!item.permanent_error;
        return (
          <View
            key={item.id}
            style={[styles.row, isErr && styles.rowErrored]}
            testID={`pending-item-${item.id}`}
          >
            <View style={[styles.typePill, { backgroundColor: meta.bg }]}>
              <Ionicons name={meta.icon as any} size={11} color={meta.tint} />
              <Text style={[styles.typePillTxt, { color: meta.tint }]}>{meta.label}</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.label} numberOfLines={1}>
                {item.label || item.id}
              </Text>
              <Text style={styles.subLabel} numberOfLines={2}>
                {isErr ? `❌ ${item.last_error || "Permanent error"}` : (
                  item.tries > 0
                    ? `Retried ${item.tries}× — ${item.last_error || "still trying"}`
                    : `Queued ${friendlyAge(item.created_at)}`
                )}
              </Text>
            </View>
            <TouchableOpacity
              onPress={() => onRemoveOne(item)}
              style={styles.removeBtn}
              testID={`pending-remove-${item.id}`}
            >
              <Ionicons name="close-circle" size={18} color="#94A3B8" />
            </TouchableOpacity>
          </View>
        );
      })}
    </View>
  );
}

function friendlyAge(iso: string): string {
  try {
    const ms = Date.now() - new Date(iso).getTime();
    if (ms < 60_000) return "just now";
    const m = Math.floor(ms / 60_000);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  } catch { return "earlier"; }
}

const styles = StyleSheet.create({
  emptyBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 14, paddingVertical: 12,
    borderRadius: 10, backgroundColor: "#F0FDF4",
    borderWidth: 1, borderColor: "#BBF7D0",
  },
  emptyTxt: { fontSize: 12.5, color: "#166534", fontWeight: "700" },
  summaryBar: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    marginBottom: 10, gap: 8, flexWrap: "wrap",
  },
  summaryTxt: { fontSize: 12, color: colors.textMuted, fontWeight: "700" },
  btn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6,
    backgroundColor: colors.primary,
  },
  btnGhost: {
    backgroundColor: "#FEE2E2", borderWidth: 1, borderColor: "#FCA5A5",
  },
  btnDisabled: { opacity: 0.4 },
  btnTxt: { color: "#fff", fontSize: 11.5, fontWeight: "800" },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingHorizontal: 12, paddingVertical: 10,
    backgroundColor: "#fff", borderRadius: 8,
    marginBottom: 8,
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  rowErrored: {
    backgroundColor: "#FEF2F2", borderColor: "#FCA5A5",
  },
  typePill: {
    flexDirection: "row", alignItems: "center", gap: 3,
    paddingHorizontal: 7, paddingVertical: 3, borderRadius: 4,
  },
  typePillTxt: { fontSize: 9.5, fontWeight: "800", letterSpacing: 0.3 },
  label: { fontSize: 12.5, fontWeight: "700", color: colors.text },
  subLabel: { fontSize: 11, color: colors.textMuted, marginTop: 2 },
  removeBtn: { padding: 4 },
});
