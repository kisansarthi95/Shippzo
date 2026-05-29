/**
 * Phase-21 — My Requests screen (redesigned to match the approved
 * UI reference). Strict adherence to the screenshot:
 *   • 4 horizontal filter tabs at the top — All / Open / In progress
 *     / Resolved — with the active one painted orange.
 *   • Cards show SHP-XXXX, category title, date · time, the first
 *     line of the description, and a colour-coded status badge.
 *   • Bottom "Still need help?" card with a Contact Support button.
 *
 * Only the current user's tickets are returned (server-side filter
 * via /api/support/tickets which scopes to current_user.id).
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Linking,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useFocusEffect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Constants from "expo-constants";

import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api, SupportTicket } from "../../lib/api";
import { CATEGORIES } from "./create";

const SUPPORT_EMAIL: string =
  (Constants.expoConfig?.extra as any)?.supportEmail || "shippzo.support@gmail.com";
const APP_NAME: string = Constants.expoConfig?.name || "Shippzo";

const TABS = [
  { k: "all",         label: "All" },
  { k: "open",        label: "Open" },
  { k: "in_progress", label: "In Progress" },
  { k: "resolved",    label: "Resolved" },
];

const STATUS_STYLE: Record<
  SupportTicket["status"],
  { bg: string; fg: string; label: string }
> = {
  open:         { bg: "#FFEDD5", fg: "#9A3412", label: "Open" },
  in_progress:  { bg: "#DBEAFE", fg: "#1D4ED8", label: "In Progress" },
  resolved:     { bg: "#DCFCE7", fg: "#15803D", label: "Resolved" },
  closed:       { bg: "#E2E8F0", fg: "#475569", label: "Closed" },
};

function fmtDateTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  // "20 May 2024 • 11:30 AM"
  const date = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: true });
  return `${date} • ${time}`;
}
function categoryLabel(k: string): string {
  return CATEGORIES.find((c) => c.k === k)?.title || (k || "Other Issue");
}

export default function MyTickets() {
  const router  = useRouter();
  const insets  = useSafeAreaInsets();
  const [tab, setTab]           = useState<string>("all");
  const [items, setItems]       = useState<SupportTicket[] | null>(null);
  const [refreshing, setR]      = useState(false);

  const load = useCallback(async () => {
    try {
      // 2026-05-25 — Server-side status filter.
      // Previously this screen fetched the newest tickets ONCE and
      // filtered them on-device, which meant any tab whose tickets
      // sat past the page boundary appeared empty (the per-tab
      // counts were misleading too). We now mirror the pattern in
      // admin/support-inbox.tsx — pass the selected tab's status to
      // /api/support/tickets and refetch on every tab change. The
      // backend accepts {open, in_progress, resolved, closed}; "all"
      // is sent as `undefined` so the server returns every ticket.
      const r = await Api.supportListMyTickets(tab === "all" ? undefined : tab);
      setItems(r.items || []);
    } catch {
      setItems([]);
    }
  }, [tab]);
  // Initial fetch + every-tab-change fetch.
  useEffect(() => { load(); }, [load]);
  // Re-fetch when the screen comes back into focus (e.g. after the
  // user navigated into a ticket detail and resolved it).
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = async () => { setR(true); await load(); setR(false); };

  // Server already returned the rows for the current status filter,
  // so `items` IS the visible list — no second-pass filter needed.
  const filtered = items || [];

  const renderItem = ({ item }: { item: SupportTicket }) => {
    const s = STATUS_STYLE[item.status] || STATUS_STYLE.open;
    const num = item.ticket_number || `SHP-${item.id.slice(0, 4).toUpperCase()}`;
    return (
      <TouchableOpacity
        testID={`ticket-${item.id}`}
        activeOpacity={0.85}
        onPress={() => router.push(`/support-center/ticket/${item.id}` as any)}
        style={styles.card}
      >
        <View style={styles.cardHead}>
          <Text style={styles.cardNum}>{num}</Text>
          <View style={[styles.statusPill, { backgroundColor: s.bg }]}>
            <Text style={[styles.statusPillTxt, { color: s.fg }]}>{s.label}</Text>
          </View>
        </View>
        <Text style={styles.cardCategory}>{categoryLabel(item.category)}</Text>
        <Text style={styles.cardDate}>{fmtDateTime(item.created_at)}</Text>
        {item.last_message_preview ? (
          <Text style={styles.cardDesc} numberOfLines={2}>
            {item.last_message_preview}
          </Text>
        ) : null}
      </TouchableOpacity>
    );
  };

  const HeaderTabs = () => (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.tabsRow}
    >
      {TABS.map((t) => {
        const active = t.k === tab;
        return (
          <TouchableOpacity
            key={t.k}
            testID={`tab-${t.k}`}
            onPress={() => setTab(t.k)}
            activeOpacity={0.85}
            style={[styles.tab, active && styles.tabActive]}
          >
            <Text style={[styles.tabTxt, active && styles.tabTxtActive]}>
              {t.label}
            </Text>
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );

  const StillNeedHelp = () => (
    <View style={styles.helpCard}>
      <View style={{ flex: 1 }}>
        <Text style={styles.helpCardTitle}>Still need help?</Text>
        <Text style={styles.helpCardSub}>Our support team is here for you.</Text>
        <TouchableOpacity
          activeOpacity={0.85}
          onPress={() =>
            Linking.openURL(`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(`${APP_NAME} support`)}`).catch(() => {})
          }
          style={styles.helpCardBtn}
        >
          <Text style={styles.helpCardBtnTxt}>Contact Support</Text>
        </TouchableOpacity>
      </View>
      <View style={styles.helpCardIcon}>
        <PhIcon name="headset" size={36} color={colors.primary} />
      </View>
    </View>
  );

  if (items === null) {
    return (
      <View style={[styles.root, { justifyContent: "center", alignItems: "center" }]}>
        <Stack.Screen options={{ title: "My Requests", headerShadowVisible: false }} />
        <ActivityIndicator color={colors.primary} />
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
              style={{ paddingHorizontal: 6 }}
            >
              <PhIcon name="add" size={24} color={colors.primary} />
            </TouchableOpacity>
          ),
        }}
      />
      <HeaderTabs />
      <FlatList
        data={filtered}
        keyExtractor={(t) => t.id}
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 24 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
        }
        ListEmptyComponent={() => (
          <View style={styles.empty}>
            <View style={styles.emptyIcon}>
              <PhIcon name="clipboard" size={36} color="#94A3B8" />
            </View>
            <Text style={styles.emptyTitle}>
              No {tab === "all" ? "" : `${TABS.find((t) => t.k === tab)?.label.toLowerCase()} `}requests
            </Text>
            <Text style={styles.emptySub}>
              Tap Create Request below to raise your first support ticket.
            </Text>
            <TouchableOpacity
              onPress={() => router.push("/support-center/create" as any)}
              style={styles.emptyCta}
              activeOpacity={0.85}
            >
              <Text style={styles.emptyCtaTxt}>Create Request</Text>
            </TouchableOpacity>
          </View>
        )}
        renderItem={renderItem}
        ListFooterComponent={items.length > 0 ? <StillNeedHelp /> : null}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  tabsRow: { paddingHorizontal: 16, paddingTop: 12, paddingBottom: 10, gap: 8 },
  tab: {
    paddingHorizontal: 18, paddingVertical: 9,
    borderRadius: 10, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
    minWidth: 72, alignItems: "center", justifyContent: "center",
  },
  tabActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  tabTxt: { fontSize: 13, fontWeight: "700", color: "#475569" },
  tabTxtActive: { color: "#fff" },

  card: {
    backgroundColor: "#fff", borderRadius: 14,
    padding: 14, marginBottom: 10,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.05)", elevation: 1,
  },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cardNum: { fontSize: 13.5, fontWeight: "800", color: "#0F172A" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  statusPillTxt: { fontSize: 10.5, fontWeight: "900", letterSpacing: 0.3 },
  cardCategory: { fontSize: 14, fontWeight: "800", color: "#0F172A", marginTop: 10 },
  cardDate: { fontSize: 12, color: "#64748B", marginTop: 4 },
  cardDesc: { fontSize: 13, color: "#475569", marginTop: 8, lineHeight: 18 },

  helpCard: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#FFF7ED", borderRadius: 14,
    paddingHorizontal: 14, paddingVertical: 14,
    marginTop: 6,
  },
  helpCardTitle: { fontSize: 13.5, fontWeight: "800", color: "#0F172A" },
  helpCardSub: { fontSize: 12, color: "#64748B", marginTop: 4 },
  helpCardBtn: {
    alignSelf: "flex-start", marginTop: 10,
    borderWidth: 1, borderColor: colors.primary,
    borderRadius: 10,
    paddingHorizontal: 14, paddingVertical: 8,
  },
  helpCardBtnTxt: { color: colors.primary, fontSize: 12.5, fontWeight: "800" },
  helpCardIcon: {
    width: 64, height: 64, borderRadius: 32,
    backgroundColor: "#FFEDD5",
    alignItems: "center", justifyContent: "center",
    marginLeft: 12,
  },

  empty: { alignItems: "center", paddingVertical: 50, paddingHorizontal: 16 },
  emptyIcon: {
    width: 84, height: 84, borderRadius: 42,
    backgroundColor: "#E2E8F0",
    alignItems: "center", justifyContent: "center",
    marginBottom: 16,
  },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: "#0F172A" },
  emptySub: { fontSize: 13, color: "#64748B", textAlign: "center", marginTop: 8, lineHeight: 18 },
  emptyCta: {
    marginTop: 18,
    backgroundColor: colors.primary,
    paddingHorizontal: 22, paddingVertical: 12, borderRadius: 12,
  },
  emptyCtaTxt: { color: "#fff", fontSize: 14, fontWeight: "800" },
});
