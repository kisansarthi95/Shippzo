/**
 * Phase-29 — Support-Center Articles list ("View All" target).
 *
 * Lives at /support-center/articles. Renders every visible article
 * grouped by category and routes the tap to the in-app reader at
 * /support-center/articles/[id]. No external redirect — replaces the
 * previous https://shippzo.com/help link from support-center.tsx.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";

import PhIcon from "../../../components/PhIcon";
import { colors } from "../../../lib/theme";
import { Api } from "../../../lib/api";

type Row = {
  id:         string;
  title:      string;
  summary:    string;
  icon:       string;
  category:   string;
  sort_order: number;
  is_visible: boolean;
};

export default function ArticlesIndexScreen() {
  const router = useRouter();
  const [items, setItems]     = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]     = useState("");
  const [query, setQuery]     = useState("");

  const load = useCallback(async () => {
    try {
      setError("");
      const r = await Api.listArticles();
      setItems(r.items || []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Could not load");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      it.title.toLowerCase().includes(q)
        || (it.summary || "").toLowerCase().includes(q)
        || (it.category || "").toLowerCase().includes(q),
    );
  }, [items, query]);

  // Group by category for a calmer list ordering.
  const grouped = useMemo(() => {
    const map: Record<string, Row[]> = {};
    for (const it of filtered) {
      const c = it.category || "General";
      if (!map[c]) map[c] = [];
      map[c].push(it);
    }
    return Object.entries(map).sort(([a], [b]) => a.localeCompare(b));
  }, [filtered]);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />

      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn} testID="articles-back">
          <PhIcon name="arrow-back" size={22} color="#0F172A" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>All Articles</Text>
          <Text style={styles.sub}>{items.length} {items.length === 1 ? "article" : "articles"}</Text>
        </View>
      </View>

      <View style={styles.searchWrap}>
        <PhIcon name="search" size={16} color="#94A3B8" />
        <TextInput
          testID="articles-search"
          value={query}
          onChangeText={setQuery}
          placeholder="Search articles…"
          style={styles.searchInput}
          autoCapitalize="none"
        />
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 12, paddingBottom: 40 }}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => { setRefreshing(true); load(); }}
              tintColor={colors.primary}
            />
          }
        >
          {error ? (
            <Text style={styles.errorTxt}>{error}</Text>
          ) : grouped.length === 0 ? (
            <View style={styles.empty}>
              <PhIcon name="document-text-outline" size={32} color="#94A3B8" />
              <Text style={styles.emptyTxt}>
                {query.trim()
                  ? `No articles match "${query}".`
                  : "No articles published yet."}
              </Text>
            </View>
          ) : (
            grouped.map(([cat, rows]) => (
              <View key={cat} style={styles.group}>
                <Text style={styles.groupHead}>{cat}</Text>
                <View style={styles.card}>
                  {rows.map((a, i) => (
                    <Pressable
                      key={a.id}
                      testID={`article-row-${a.id}`}
                      onPress={() => router.push(`/support-center/articles/${a.id}` as any)}
                      style={({ pressed }) => [
                        styles.row,
                        i < rows.length - 1 && styles.rowDivider,
                        pressed && { opacity: 0.65 },
                      ]}
                    >
                      <View style={styles.rowIcon}>
                        <PhIcon
                          name={(a.icon as any) || "document-text-outline"}
                          size={20}
                          color="#1E40AF"
                        />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.rowTitle} numberOfLines={2}>
                          {a.title}
                        </Text>
                        {!!a.summary && (
                          <Text style={styles.rowSummary} numberOfLines={2}>
                            {a.summary}
                          </Text>
                        )}
                      </View>
                      <PhIcon name="chevron-forward" size={18} color="#CBD5E1" />
                    </Pressable>
                  ))}
                </View>
              </View>
            ))
          )}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F4F5F7" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    gap: 8,
  },
  backBtn: { padding: 6 },
  title: { fontSize: 18, fontWeight: "800", color: "#0F172A" },
  sub:   { fontSize: 12, color: "#64748B", marginTop: 2 },

  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginHorizontal: 12,
    marginTop: 12,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: "#fff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  searchInput: { flex: 1, color: "#0F172A", fontSize: 14, paddingVertical: 2 },

  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  errorTxt: { color: "#991B1B", textAlign: "center", marginTop: 16 },

  group: { marginTop: 16 },
  groupHead: {
    fontSize: 12,
    fontWeight: "700",
    color: "#475569",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    marginBottom: 6,
    paddingHorizontal: 4,
  },
  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingVertical: 14,
    gap: 12,
  },
  rowDivider: { borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  rowIcon: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#DBEAFE",
  },
  rowTitle:   { fontSize: 14, fontWeight: "700", color: "#0F172A" },
  rowSummary: { fontSize: 12, color: "#64748B", marginTop: 2 },

  empty: { alignItems: "center", justifyContent: "center", padding: 40, gap: 8 },
  emptyTxt: { color: "#64748B", textAlign: "center" },
});
