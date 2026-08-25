/**
 * Audience Hub — Phase F12.
 *
 * A tab-level view of unique customers derived from actual shipments.
 * Provides four filter chips (All / New / Returning / Imported) with
 * live counts, a search bar, and premium customer cards showing
 * name, phone|email, city, and per-customer stats
 * (TOTAL ORDERS | TOTAL SALES).
 *
 * A floating "+" action button opens the manual-entry flow (the
 * legacy /(tabs)/add screen) — this replaces the central "+" tab
 * button that used to live in the bottom bar.
 *
 * Data source: `/api/me/audience` and `/api/me/audience/stats`.
 * Both endpoints group over `shipments` collection by phone (or name
 * fallback) and return one row per unique customer.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  RefreshControl,
  Alert,
  FlatList,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter, useFocusEffect } from "expo-router";
import PhIcon from "../../components/PhIcon";
import SearchBar from "../../components/SearchBar";
import FilterChipRow from "../../components/FilterChipRow";
import {
  Api,
  AudienceCustomer,
  AudienceStats,
} from "../../lib/api";
import { colors } from "../../lib/theme";
import { screenCache } from "../../lib/screenCache";
import { SkeletonList, SkeletonStatsStrip } from "../../components/Skeleton";
import { usePermissions } from "../../lib/permissions";

type Segment = "all" | "new" | "returning" | "imported" | "vip";

const CACHE_KEY_LIST = "audience:list";
const CACHE_KEY_STATS = "audience:stats";

function formatINR(n: number): string {
  const v = Number.isFinite(n) ? n : 0;
  // Two-decimal INR (e.g., ₹8,813.63) — matches the spec sample.
  const rounded = Math.round(v * 100) / 100;
  return "₹" + rounded.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export default function AudienceScreen() {
  const router = useRouter();
  const { hasPerm } = usePermissions();

  // Manual-entry gating: use the same permission triad as the old
  // central "+" tab — if the team member has any of these, they can
  // create shipments manually.
  const canAdd =
    hasPerm("shipments_create") ||
    hasPerm("smart_paste_ai") ||
    hasPerm("file_import_csv");

  const cachedList = screenCache.get<AudienceCustomer[]>(CACHE_KEY_LIST);
  const cachedStats = screenCache.get<AudienceStats>(CACHE_KEY_STATS);

  const [loading, setLoading] = useState(!cachedList);
  const [refreshing, setRefreshing] = useState(false);
  const [customers, setCustomers] = useState<AudienceCustomer[]>(cachedList || []);
  const [stats, setStats] = useState<AudienceStats | null>(cachedStats);
  const [segment, setSegment] = useState<Segment>("all");
  const [search, setSearch] = useState("");

  const load = useCallback(
    async (opts?: { silent?: boolean }) => {
      try {
        const params: any = { segment, limit: 200 };
        if (search.trim()) params.q = search.trim();
        const [list, s] = await Promise.all([
          Api.listAudience(params),
          Api.audienceStats(),
        ]);
        setCustomers(list.customers);
        setStats(s);
        if (segment === "all" && !search.trim()) {
          screenCache.set(CACHE_KEY_LIST, list.customers);
          screenCache.set(CACHE_KEY_STATS, s);
        }
      } catch (e: any) {
        if (!opts?.silent) {
          Alert.alert(
            "Couldn't load audience",
            e?.response?.data?.detail || e?.message || "Try again.",
          );
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [segment, search],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Refresh whenever the tab gains focus so a newly added shipment
  // shows up without a manual pull-to-refresh.
  useFocusEffect(
    useCallback(() => {
      load({ silent: true });
    }, [load]),
  );

  const onRefresh = () => {
    setRefreshing(true);
    load();
  };

  const openProfile = (c: AudienceCustomer) => {
    if (!c.key) return;
    router.push({
      pathname: "/audience/[key]",
      params: { key: c.key },
    });
  };

  const segments = useMemo(() => {
    const s = stats || { all: 0, new: 0, returning: 0, imported: 0, vip: 0 };
    return [
      { key: "all",       label: "All",       count: s.all },
      { key: "vip",       label: "👑 VIP",    count: s.vip ?? 0 },
      { key: "new",       label: "New",       count: s.new },
      { key: "returning", label: "Returning", count: s.returning },
      { key: "imported",  label: "Imported",  count: s.imported },
    ];
  }, [stats]);

  const renderCard = ({ item }: { item: AudienceCustomer }) => {
    const line2Parts: string[] = [];
    if (item.customer_phone) line2Parts.push(item.customer_phone);
    if (item.customer_email) line2Parts.push(item.customer_email);
    const rank = item.rank || 0;
    const isVipCard = segment === "vip" && rank > 0;
    const medal =
      rank === 1 ? "🥇" :
      rank === 2 ? "🥈" :
      rank === 3 ? "🥉" : "";
    return (
      <TouchableOpacity
        style={[
          styles.card,
          isVipCard && rank <= 3 && styles.cardTop3,
        ]}
        activeOpacity={0.75}
        onPress={() => openProfile(item)}
        testID={`audience-card-${item.key}`}
      >
        {isVipCard ? (
          <View style={styles.rankBadge}>
            {medal ? (
              <Text style={styles.rankMedal}>{medal}</Text>
            ) : (
              <Text style={styles.rankNumberOnly}>#{rank}</Text>
            )}
            {medal ? (
              <Text style={styles.rankNumber}>#{rank}</Text>
            ) : null}
          </View>
        ) : null}
        <Text style={styles.cardName} numberOfLines={1}>
          {item.customer_name || "Unknown customer"}
        </Text>
        {line2Parts.length ? (
          <Text style={styles.cardSub} numberOfLines={1}>
            {line2Parts.join("  |  ")}
          </Text>
        ) : null}
        {item.city ? (
          <View style={styles.locRow}>
            <PhIcon name="location" size={13} color="#64748B" />
            <Text style={styles.locTxt} numberOfLines={1}>
              {[item.city, item.state].filter(Boolean).join(", ")}
            </Text>
          </View>
        ) : null}
        {item.is_imported ? (
          <View style={styles.importedBadge}>
            <Text style={styles.importedBadgeTxt}>IMPORTED</Text>
          </View>
        ) : null}

        <View style={styles.statsRow}>
          <View style={styles.statCol}>
            <Text style={styles.statLbl}>TOTAL ORDERS</Text>
            <Text style={styles.statValue}>{item.orders_count}</Text>
          </View>
          <View style={styles.statColRight}>
            <Text style={styles.statLbl}>TOTAL SALES</Text>
            <Text
              style={[
                styles.statValue,
                isVipCard && styles.statValueVip,
              ]}
            >
              {formatINR(item.total_sales)}
            </Text>
          </View>
        </View>
      </TouchableOpacity>
    );
  };

  const emptySegmentLabel = useMemo(() => {
    switch (segment) {
      case "new":       return "new";
      case "returning": return "returning";
      case "imported":  return "imported";
      case "vip":       return "VIP";
      default:          return "";
    }
  }, [segment]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Audience</Text>
        <Text style={styles.headerSub}>
          Every customer who&apos;s ordered from you
        </Text>
      </View>

      {/* Filter chips */}
      <FilterChipRow
        testIDPrefix="audience-segment"
        selected={segment}
        onSelect={(k) => setSegment((k as Segment) || "all")}
        items={segments.map((s) => ({
          key: s.key,
          label: s.label,
          count: s.count,
          testID: `audience-segment-${s.key}`,
        }))}
        style={{ paddingHorizontal: 12, marginBottom: 6 }}
      />

      {/* Search */}
      <View style={{ paddingHorizontal: 12, marginTop: 6 }}>
        <SearchBar
          testID="audience-search"
          value={search}
          onChangeText={setSearch}
          onClear={() => setSearch("")}
          placeholder="Search by name, phone, email"
          onSubmitEditing={() => load()}
          containerStyle={{ marginHorizontal: 0 }}
        />
      </View>

      {loading ? (
        <View style={{ paddingHorizontal: 12, paddingTop: 8 }}>
          <SkeletonStatsStrip boxes={2} />
          <SkeletonList rows={6} height={110} />
        </View>
      ) : (
        <FlatList
          testID="audience-list"
          data={customers}
          keyExtractor={(item) => item.key}
          renderItem={renderCard}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Text style={styles.emptyEmoji}>👥</Text>
              <Text style={styles.emptyTitle}>
                {search.trim()
                  ? "No customers match your search"
                  : emptySegmentLabel
                  ? `No ${emptySegmentLabel} customers yet`
                  : "No customers yet"}
              </Text>
              <Text style={styles.emptySub}>
                {search.trim()
                  ? "Try a shorter search or clear it to see everyone."
                  : "Once you create your first shipment, the customer appears here automatically."}
              </Text>
              {!search.trim() && canAdd ? (
                <TouchableOpacity
                  style={styles.emptyCta}
                  onPress={() => router.push("/add")}
                  activeOpacity={0.85}
                  testID="audience-empty-cta"
                >
                  <PhIcon name="add" size={16} color="#fff" />
                  <Text style={styles.emptyCtaTxt}>Add first order</Text>
                </TouchableOpacity>
              ) : null}
            </View>
          }
        />
      )}

      {/* FAB — floating '+' for manual entry.
          Only shown to users who can create shipments. */}
      {canAdd ? (
        <TouchableOpacity
          testID="audience-fab-add"
          style={styles.fab}
          onPress={() => router.push("/add")}
          activeOpacity={0.85}
        >
          <PhIcon name="add" size={30} color="#fff" />
        </TouchableOpacity>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:   { flex: 1, backgroundColor: colors.background },
  header: {
    paddingHorizontal: 16,
    paddingTop: 4,
    paddingBottom: 10,
  },
  headerTitle: {
    fontSize: 24,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: -0.3,
  },
  headerSub: {
    fontSize: 12,
    color: colors.textMuted,
    marginTop: 2,
  },
  listContent: {
    paddingHorizontal: 12,
    paddingTop: 8,
    paddingBottom: 96,
  },

  // ── Customer card ────────────────────────────────────────────────
  card: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    padding: 14,
    marginBottom: 10,
    gap: 4,
    ...Platform.select({
      ios: {
        shadowColor: "#000",
        shadowOpacity: 0.04,
        shadowRadius: 4,
        shadowOffset: { width: 0, height: 1 },
      },
      android: { elevation: 1 },
    }),
  },
  cardTop3: {
    borderColor: "#F59E0B",
    borderWidth: 1.5,
    backgroundColor: "#FFFBEB",
  },
  rankBadge: {
    position: "absolute",
    top: 10,
    right: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    zIndex: 1,
  },
  rankMedal: { fontSize: 14 },
  rankNumber: {
    fontSize: 11,
    fontWeight: "800",
    color: "#92400E",
    letterSpacing: 0.3,
  },
  rankNumberOnly: {
    fontSize: 11,
    fontWeight: "800",
    color: "#92400E",
    letterSpacing: 0.3,
  },
  statValueVip: {
    color: "#B45309",
  },
  cardName: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.text,
  },
  cardSub: {
    fontSize: 13,
    color: "#475569",
    fontWeight: "500",
  },
  locRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 2,
  },
  locTxt: {
    fontSize: 12,
    color: "#64748B",
    fontWeight: "500",
  },
  importedBadge: {
    alignSelf: "flex-start",
    backgroundColor: "#E0E7FF",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 6,
    marginTop: 4,
  },
  importedBadgeTxt: {
    fontSize: 10,
    fontWeight: "800",
    color: "#3730A3",
    letterSpacing: 0.4,
  },
  statsRow: {
    flexDirection: "row",
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#F1F5F9",
  },
  statCol: { flex: 1 },
  statColRight: { flex: 1, alignItems: "flex-end" },
  statLbl: {
    fontSize: 10,
    fontWeight: "700",
    color: "#94A3B8",
    letterSpacing: 0.6,
    marginBottom: 2,
  },
  statValue: {
    fontSize: 15,
    fontWeight: "800",
    color: colors.text,
  },

  // ── Empty state ──────────────────────────────────────────────────
  emptyCard: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
    marginTop: 16,
  },
  emptyEmoji: { fontSize: 42, marginBottom: 6 },
  emptyTitle: {
    fontSize: 16,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 6,
    textAlign: "center",
  },
  emptySub: {
    fontSize: 13,
    color: colors.textMuted,
    textAlign: "center",
    lineHeight: 19,
  },
  emptyCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 10,
    marginTop: 14,
  },
  emptyCtaTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  // ── FAB ───────────────────────────────────────────────────────────
  fab: {
    position: "absolute",
    right: 20,
    bottom: 24,
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: colors.primary,
    alignItems: "center",
    justifyContent: "center",
    ...Platform.select({
      ios: {
        shadowColor: colors.primary,
        shadowOpacity: 0.4,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 4 },
      },
      android: { elevation: 8 },
    }),
  },
});
