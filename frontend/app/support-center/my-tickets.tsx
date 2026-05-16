/**
 * Phase-21 — My Support Requests screen.
 *
 * Lists the current user's tickets newest-first. Each row shows:
 *   • Status pill (open / in_progress / resolved / closed) +
 *     priority dot when priority != medium.
 *   • Title (truncated to one line).
 *   • Category chip + last-message preview.
 *   • Relative timestamp on the right.
 *
 * Tap → ticket detail. Empty state has a primary CTA to Create.
 */
import React, { useCallback, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api, SupportTicket } from "../../lib/api";

const STATUS_STYLE: Record<
  SupportTicket["status"],
  { bg: string; fg: string; label: string }
> = {
  open:         { bg: "#FFEDD5", fg: "#9A3412", label: "Open" },
  in_progress:  { bg: "#DBEAFE", fg: "#1D4ED8", label: "In progress" },
  resolved:     { bg: "#DCFCE7", fg: "#15803D", label: "Resolved" },
  closed:       { bg: "#E2E8F0", fg: "#475569", label: "Closed" },
};

function relTime(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "";
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60_000);
  if (m < 1)        return "just now";
  if (m < 60)       return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)       return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7)        return `${d}d ago`;
  return new Date(t).toLocaleDateString();
}

export default function MyTickets() {
  const router  = useRouter();
  const insets  = useSafeAreaInsets();
  const [items, setItems]       = useState<SupportTicket[] | null>(null);
  const [refreshing, setR]      = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await Api.supportListMyTickets();
      setItems(r.items || []);
    } catch {
      setItems([]);
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = async () => {
    setR(true);
    await load();
    setR(false);
  };

  // Loading state — initial pull only (re-fetches on focus use list as-is).
  if (items === null) {
    return (
      <View style={[styles.root, { justifyContent: "center", alignItems: "center" }]}>
        <Stack.Screen options={{ title: "My Requests", headerShadowVisible: false }} />
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  // Empty state
  if (items.length === 0) {
    return (
      <View style={styles.root}>
        <Stack.Screen options={{ title: "My Requests", headerShadowVisible: false }} />
        <View style={styles.emptyWrap}>
          <View style={styles.emptyIcon}>
            <PhIcon name="clipboard" size={36} color="#94A3B8" />
          </View>
          <Text style={styles.emptyTitle}>No support requests yet</Text>
          <Text style={styles.emptySub}>
            Raise a new request and our team will get back to you as soon as possible.
          </Text>
          <TouchableOpacity
            onPress={() => router.push("/support-center/create" as any)}
            activeOpacity={0.85}
            style={styles.emptyCta}
            testID="my-tickets-empty-cta"
          >
            <Text style={styles.emptyCtaTxt}>Create Request</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{
          title: "My Requests",
          headerShadowVisible: false,
          headerRight: () => (
            <TouchableOpacity
              onPress={() => router.push("/support-center/create" as any)}
              hitSlop={10}
              testID="my-tickets-create-header"
              style={{ paddingHorizontal: 6 }}
            >
              <PhIcon name="add" size={24} color={colors.primary} />
            </TouchableOpacity>
          ),
        }}
      />
      <FlatList
        data={items}
        keyExtractor={(t) => t.id}
        contentContainerStyle={{ padding: 12, paddingBottom: insets.bottom + 16 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
        }
        renderItem={({ item }) => {
          const s = STATUS_STYLE[item.status] || STATUS_STYLE.open;
          return (
            <TouchableOpacity
              testID={`ticket-${item.id}`}
              activeOpacity={0.85}
              onPress={() => router.push(`/support-center/ticket/${item.id}` as any)}
              style={styles.card}
            >
              <View style={styles.cardHead}>
                <View style={[styles.statusPill, { backgroundColor: s.bg }]}>
                  <Text style={[styles.statusPillTxt, { color: s.fg }]}>
                    {s.label}
                  </Text>
                </View>
                {item.priority === "high" ? (
                  <View style={styles.priorityDot} />
                ) : null}
                <Text style={styles.cardTime}>{relTime(item.updated_at)}</Text>
              </View>
              <Text style={styles.cardTitle} numberOfLines={1}>
                {item.title}
              </Text>
              {item.last_message_preview ? (
                <Text style={styles.cardPreview} numberOfLines={1}>
                  {item.last_reply_by === "admin" ? "💬 " : ""}
                  {item.last_message_preview}
                </Text>
              ) : null}
              <View style={styles.cardFoot}>
                <Text style={styles.cardMeta}>
                  #{item.id.slice(0, 8)} · {item.category}
                  {item.message_count ? `  ·  ${item.message_count} msg` : ""}
                </Text>
                <PhIcon name="chevron-forward" size={14} color="#CBD5E1" />
              </View>
            </TouchableOpacity>
          );
        }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  card: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 14,
    marginBottom: 10,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.04)",
    elevation: 1,
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  statusPillTxt: { fontSize: 10.5, fontWeight: "900", letterSpacing: 0.3 },
  priorityDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: "#DC2626" },
  cardTime: { marginLeft: "auto", fontSize: 11, color: "#94A3B8" },
  cardTitle: { fontSize: 15, fontWeight: "800", color: "#0F172A", marginTop: 8 },
  cardPreview: { fontSize: 13, color: "#475569", marginTop: 4 },
  cardFoot: { flexDirection: "row", alignItems: "center", marginTop: 10 },
  cardMeta: { fontSize: 11.5, color: "#94A3B8", flex: 1 },

  emptyWrap: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  emptyIcon: {
    width: 84, height: 84, borderRadius: 42,
    backgroundColor: "#E2E8F0",
    alignItems: "center", justifyContent: "center",
    marginBottom: 16,
  },
  emptyTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  emptySub: { fontSize: 13.5, color: "#64748B", textAlign: "center", marginTop: 8, lineHeight: 19 },
  emptyCta: {
    marginTop: 18,
    backgroundColor: colors.primary,
    paddingHorizontal: 22, paddingVertical: 12, borderRadius: 12,
  },
  emptyCtaTxt: { color: "#fff", fontSize: 14, fontWeight: "800" },
});
