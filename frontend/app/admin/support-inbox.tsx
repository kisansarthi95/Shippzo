/**
 * Phase-22 (2026-05-16) — Super-Admin Support Inbox.
 *
 * Single-screen list of every support ticket across all users.
 * Only visible when the signed-in account has `is_admin: true`
 * (currently: admin@test.com). Reuses the same ticket-detail
 * screen as the user-side because the backend already lets an
 * admin reply / change status on the same endpoint.
 *
 * Polling: refreshes the list every 10s while the screen is
 * focused so new tickets appear "in seconds" without needing
 * a manual pull-to-refresh — matches the user's expectation that
 * the owner can respond immediately.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import PhIcon from "../../components/PhIcon";
import SearchBar from "../../components/SearchBar";
import FilterChipRow from "../../components/FilterChipRow";
import { colors } from "../../lib/theme";
import { Api, SupportTicket } from "../../lib/api";
import { useAuth } from "../../lib/auth";

const TABS = [
  { k: "all",         label: "All" },
  { k: "open",        label: "Open" },
  { k: "in_progress", label: "In Progress" },
  { k: "resolved",    label: "Resolved" },
  { k: "closed",      label: "Closed" },
];

const STATUS_BG: Record<string, string> = {
  open: "#FFEDD5", in_progress: "#DBEAFE", resolved: "#DCFCE7", closed: "#E2E8F0",
};
const STATUS_FG: Record<string, string> = {
  open: "#9A3412", in_progress: "#1D4ED8", resolved: "#15803D", closed: "#475569",
};
const STATUS_LBL: Record<string, string> = {
  open: "Open", in_progress: "In Progress", resolved: "Resolved", closed: "Closed",
};

function relTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const diff = Date.now() - d.getTime();
  if (diff < 60_000)         return "just now";
  if (diff < 3_600_000)      return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000)     return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return d.toLocaleDateString();
}

export default function AdminSupportInbox() {
  const router  = useRouter();
  const insets  = useSafeAreaInsets();
  const { user } = useAuth();

  const [tab, setTab]               = useState<string>("all");
  const [items, setItems]           = useState<SupportTicket[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery]           = useState("");

  // Active polling timer reference — cleared on blur / unmount so the
  // network is silent when the screen isn't visible.
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (silent = false) => {
    try {
      if (!silent) setRefreshing(true);
      const res = await Api.adminListSupportTickets(
        tab === "all" ? undefined : tab,
        200,
      );
      setItems(res.items || []);
    } catch (e) {
      if (!silent) setItems((prev) => prev || []);
    } finally {
      if (!silent) setRefreshing(false);
    }
  }, [tab]);

  // Initial fetch + every-tab-change fetch.
  useEffect(() => { load(false); }, [load]);

  // Poll every 10s while focused so new tickets surface quickly.
  useFocusEffect(
    useCallback(() => {
      pollRef.current = setInterval(() => { load(true); }, 10_000);
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
      };
    }, [load]),
  );

  // Guard: redirect non-admins away (defence-in-depth — the entry
  // in Settings is already gated).
  useEffect(() => {
    if (user && !user.is_admin) {
      router.replace("/(tabs)" as any);
    }
  }, [user, router]);

  const filtered = (items || []).filter((t) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      (t.ticket_number || "").toLowerCase().includes(q) ||
      (t.title || "").toLowerCase().includes(q) ||
      (t.user_email || "").toLowerCase().includes(q)
    );
  });

  const openCount       = (items || []).filter((t) => t.status === "open").length;
  const inProgressCount = (items || []).filter((t) => t.status === "in_progress").length;

  const renderRow = ({ item }: { item: SupportTicket }) => {
    const st = item.status as keyof typeof STATUS_BG;
    return (
      <TouchableOpacity
        activeOpacity={0.85}
        style={styles.card}
        onPress={() => router.push(`/support-center/ticket/${item.id}` as any)}
      >
        <View style={styles.rowTop}>
          <Text style={styles.ticketNo}>{item.ticket_number || `SHP-${item.id.slice(0, 4)}`}</Text>
          <View style={[styles.statusPill, { backgroundColor: STATUS_BG[st] || "#E2E8F0" }]}>
            <Text style={[styles.statusPillTxt, { color: STATUS_FG[st] || "#475569" }]}>
              {STATUS_LBL[st] || st}
            </Text>
          </View>
        </View>
        <Text style={styles.title} numberOfLines={1}>{item.title}</Text>
        <Text style={styles.meta} numberOfLines={1}>
          {(item.user_email || "Unknown user") + " · " + relTime(item.updated_at)}
        </Text>
        {item.last_message_preview ? (
          <Text style={styles.preview} numberOfLines={2}>
            {item.last_message_preview}
          </Text>
        ) : null}
      </TouchableOpacity>
    );
  };

  if (items === null) {
    return (
      <View style={[styles.root, { justifyContent: "center", alignItems: "center" }]}>
        <Stack.Screen options={{ title: "Support Inbox", headerShadowVisible: false }} />
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Stack.Screen options={{ title: "Support Inbox", headerShadowVisible: false }} />

      {/* Header stats strip */}
      <View style={styles.statsRow}>
        <View style={styles.statPill}>
          <Text style={styles.statN}>{openCount}</Text>
          <Text style={styles.statLbl}>Open</Text>
        </View>
        <View style={styles.statPill}>
          <Text style={styles.statN}>{inProgressCount}</Text>
          <Text style={styles.statLbl}>In Progress</Text>
        </View>
        <View style={styles.statPill}>
          <Text style={styles.statN}>{items.length}</Text>
          <Text style={styles.statLbl}>Total</Text>
        </View>
      </View>

      {/* Search — Phase-32: one-tap clear also resets the status
          tab back to "all" for a true default-state reset. */}
      <SearchBar
        value={query}
        onChangeText={setQuery}
        onClear={() => setTab("all")}
        placeholder="Search ticket #, title, or user…"
        testID="admin-support-search"
      />

      {/* Status filter tabs — Phase F3.9 canonical FilterChipRow. */}
      <FilterChipRow
        testIDPrefix="support-tab"
        selected={tab}
        onSelect={setTab}
        items={TABS.map((t) => ({ key: t.k, label: t.label }))}
        style={styles.chipRowWrap}
      />

      <FlatList
        data={filtered}
        keyExtractor={(t) => t.id}
        renderItem={renderRow}
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load(false)}
            tintColor={colors.primary}
          />
        }
        ListEmptyComponent={() => (
          <View style={styles.empty}>
            <PhIcon name="clipboard" size={36} color="#94A3B8" />
            <Text style={styles.emptyTitle}>
              No {tab === "all" ? "" : `${TABS.find((t) => t.k === tab)?.label.toLowerCase()} `}requests
            </Text>
            <Text style={styles.emptySub}>
              Pull down to refresh. Inbox auto-refreshes every 10 seconds.
            </Text>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  statsRow: {
    flexDirection: "row", paddingHorizontal: 16,
    paddingTop: 12, paddingBottom: 4, gap: 10,
  },
  statPill: {
    flex: 1, backgroundColor: "#fff", borderRadius: 12,
    paddingVertical: 10, paddingHorizontal: 10, alignItems: "center",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  statN:   { fontSize: 18, fontWeight: "900", color: "#0F172A" },
  statLbl: { fontSize: 11, fontWeight: "700", color: "#64748B", marginTop: 2 },

  searchWrap: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 12, marginHorizontal: 16, marginTop: 10,
  },
  searchInput: {
    flex: 1, fontSize: 13.5, color: "#0F172A",
    paddingVertical: 10, paddingLeft: 8,
  },

  // Phase F5.1 — Filter-Chip Consolidation. Legacy `tabsRow/tab/...`
  // styles retired in favor of the canonical <FilterChipRow>. Only
  // `chipRowWrap` here provides the row's outer horizontal padding.
  chipRowWrap: {
    paddingHorizontal: 16, paddingTop: 10, paddingBottom: 6,
  },

  card: {
    backgroundColor: "#fff", borderRadius: 14,
    padding: 14, marginBottom: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  rowTop: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    marginBottom: 6,
  },
  ticketNo: { fontSize: 12, fontWeight: "800", color: colors.primary, letterSpacing: 0.4 },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  statusPillTxt: { fontSize: 11, fontWeight: "800" },
  title:   { fontSize: 14.5, fontWeight: "800", color: "#0F172A" },
  meta:    { fontSize: 11.5, color: "#94A3B8", marginTop: 2 },
  preview: { fontSize: 12.5, color: "#475569", marginTop: 8, lineHeight: 18 },

  empty: { alignItems: "center", paddingVertical: 60, paddingHorizontal: 20 },
  emptyTitle: { fontSize: 15, fontWeight: "800", color: "#475569", marginTop: 12 },
  emptySub:   { fontSize: 12.5, color: "#94A3B8", marginTop: 6, textAlign: "center" },
});
