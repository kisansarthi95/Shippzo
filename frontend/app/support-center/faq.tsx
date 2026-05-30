/**
 * Support Center → FAQ screen
 * ---------------------------
 *
 * An in-app, self-contained FAQ list for Shippzo. Replaces the
 * previous (incorrect) link to /refund-policy, which was a
 * legal/privacy page that had nothing to do with FAQs.
 *
 * Design contract
 *   • All questions + answers are CURATED to this app — shipping
 *     labels, couriers, wallet, plans, WhatsApp templates, Smart
 *     Paste, Razorpay, Google Sheets, multi-tenant data isolation,
 *     etc. No generic copy.
 *   • Accordion behaviour: tapping a row toggles open/closed; only
 *     one row open at a time keeps the list scannable. LayoutAnimation
 *     gives a soft expand/collapse without pulling in Reanimated.
 *   • Client-side search filters by question OR answer text so the
 *     user can find an answer without scrolling 25 rows. Empty-state
 *     copy points the user at the Create Request flow.
 *   • Categories grouped + colour-coded so the answer set feels
 *     curated, not random.
 *
 * Future-proof notes
 *   • Q/A list is a const FAQS array — easy to swap for a backend
 *     feed later without touching the rendering code.
 *   • Each entry has a stable `id` so screenshots / tests can target
 *     rows reliably.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  LayoutAnimation,
  Linking,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  UIManager,
  View,
} from "react-native";
import { Stack, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Constants from "expo-constants";

import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";

const APP_NAME: string = Constants.expoConfig?.name || "Shippzo";

// Enable LayoutAnimation on Android — required for the smooth
// accordion expand/collapse. Safe to call multiple times.
if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

// ─── FAQ entry shape ────────────────────────────────────────────────
// Items now come from the /api/faq backend (admin-managed). The
// shape matches the FAQCreate Pydantic schema. We render them
// dynamically with categories grouped + colour-coded.
type FAQItem = {
  id:          string;
  category:    string;
  q:           string;
  a:           string;
  is_visible?: boolean;
  sort_order?: number;
};


// Subtle, on-brand pastel chips. Index lookup keeps the colour bound
// to the category position so the same chip always renders the same
// shade across renders / search filters.
const CATEGORY_COLORS: Array<{ bg: string; fg: string }> = [
  { bg: "#FFEDD5", fg: "#9A3412" },  // Getting started — orange
  { bg: "#DBEAFE", fg: "#1D4ED8" },  // Labels & couriers — blue
  { bg: "#DCFCE7", fg: "#15803D" },  // Wallet & payments — green
  { bg: "#F0FDF4", fg: "#166534" },  // WhatsApp — green-tint
  { bg: "#EDE9FE", fg: "#5B21B6" },  // Smart Paste — purple
  { bg: "#FEF3C7", fg: "#92400E" },  // Reports — amber
  { bg: "#FEE2E2", fg: "#991B1B" },  // Account / troubleshooting — red
];

export default function FAQScreen() {
  const router  = useRouter();
  const insets  = useSafeAreaInsets();
  const [query, setQuery]     = useState<string>("");
  const [openId, setOpenId]   = useState<string | null>(null);
  // Phase-27 — items now come from /api/faq (admin-managed). The
  // backend filter pre-strips hidden rows, so the client only ever
  // sees what the admin has flagged visible.
  const [items, setItems]     = useState<FAQItem[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError]     = useState<string>("");
  const [refreshing, setRefreshing] = useState<boolean>(false);

  // Derive the unique category order from whatever the server sent
  // (instead of a hard-coded const list). This lets admins add brand
  // new categories on the fly without a frontend deploy.
  const categories = useMemo<string[]>(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const it of items) {
      if (!seen.has(it.category)) { seen.add(it.category); out.push(it.category); }
    }
    return out;
  }, [items]);

  const load = useCallback(async () => {
    try {
      setError("");
      const r = await Api.faqList();
      setItems(r.items || []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Could not load FAQs");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true);
    load();
  }, [load]);

  // Filter by question OR answer text — gives the user a single
  // search box that surfaces relevant rows regardless of which side
  // matched (e.g. searching "Razorpay" only matches answers).
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (f) =>
        f.q.toLowerCase().includes(q) ||
        f.a.toLowerCase().includes(q) ||
        f.category.toLowerCase().includes(q),
    );
  }, [items, query]);

  const toggle = (id: string) => {
    LayoutAnimation.configureNext(
      LayoutAnimation.create(180, "easeInEaseOut", "opacity"),
    );
    setOpenId((prev) => (prev === id ? null : id));
  };

  // Index → colour helper. New categories beyond the palette length
  // fall back to a neutral slate so we never blow up at runtime.
  const chipFor = (cat: string) => {
    const i = categories.indexOf(cat);
    return CATEGORY_COLORS[i % CATEGORY_COLORS.length] || { bg: "#E2E8F0", fg: "#475569" };
  };

  return (
    <View style={[styles.root, { paddingBottom: insets.bottom + 16 }]}>
      <Stack.Screen
        options={{
          title: "FAQ",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 32 }}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            tintColor={colors.primary}
          />
        }
      >
        {/* ── Heading + search ─────────────────────────────────── */}
        <View style={styles.hero}>
          <View style={styles.heroIcon}>
            <PhIcon name="question" size={26} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.heroTitle}>Frequently Asked Questions</Text>
            <Text style={styles.heroSub}>
              Answers to common questions about {APP_NAME}. Tap any row to
              expand it.
            </Text>
          </View>
        </View>

        <View style={styles.searchWrap}>
          <View style={styles.searchInner}>
            <PhIcon name="search" size={16} color="#94A3B8" />
            <TextInput
              testID="faq-search"
              value={query}
              onChangeText={setQuery}
              placeholder="Search FAQs..."
              placeholderTextColor="#9CA3AF"
              style={styles.searchInput}
              returnKeyType="search"
            />
            {query.length > 0 ? (
              <TouchableOpacity
                testID="faq-clear-search"
                onPress={() => setQuery("")}
                hitSlop={8}
              >
                <PhIcon name="close-circle" size={16} color="#94A3B8" />
              </TouchableOpacity>
            ) : null}
          </View>
          <Text style={styles.searchCount}>
            {filtered.length} of {items.length} questions
          </Text>
        </View>

        {/* ── Q&A accordion list ───────────────────────────────── */}
        <View style={styles.list}>
          {loading ? (
            <View style={styles.empty} testID="faq-loading">
              <ActivityIndicator color={colors.primary} />
              <Text style={[styles.emptyTitle, { fontSize: 13 }]}>Loading FAQs…</Text>
            </View>
          ) : error ? (
            <View style={styles.empty} testID="faq-error">
              <PhIcon name="warning" size={28} color="#DC2626" />
              <Text style={styles.emptyTitle}>Could not load FAQs</Text>
              <Text style={styles.emptySub}>{error}</Text>
              <TouchableOpacity onPress={load} style={styles.emptyBtn}>
                <Text style={styles.emptyBtnTxt}>Retry</Text>
              </TouchableOpacity>
            </View>
          ) : filtered.length === 0 ? (
            <View style={styles.empty}>
              <PhIcon name="search" size={28} color="#CBD5E1" />
              <Text style={styles.emptyTitle}>
                No FAQs match "{query.trim()}"
              </Text>
              <Text style={styles.emptySub}>
                Can't find what you're looking for? Raise a support request
                and we'll get back to you.
              </Text>
              <TouchableOpacity
                testID="faq-empty-create"
                onPress={() => router.push("/support-center/create" as any)}
                style={styles.emptyBtn}
                activeOpacity={0.85}
              >
                <Text style={styles.emptyBtnTxt}>Create Request</Text>
              </TouchableOpacity>
            </View>
          ) : (
            filtered.map((f) => {
              const open = openId === f.id;
              const chip = chipFor(f.category);
              return (
                <Pressable
                  key={f.id}
                  testID={`faq-row-${f.id}`}
                  onPress={() => toggle(f.id)}
                  style={({ pressed }) => [
                    styles.row,
                    open && styles.rowOpen,
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  <View style={styles.rowHeader}>
                    <View style={{ flex: 1 }}>
                      <View style={[styles.catChip, { backgroundColor: chip.bg }]}>
                        <Text style={[styles.catChipTxt, { color: chip.fg }]}>
                          {f.category}
                        </Text>
                      </View>
                      <Text style={styles.rowQ}>{f.q}</Text>
                    </View>
                    <View style={[styles.chevWrap, open && styles.chevWrapOpen]}>
                      <PhIcon
                        name={open ? "chevron-up" : "chevron-down"}
                        size={16}
                        color={open ? colors.primary : "#64748B"}
                      />
                    </View>
                  </View>
                  {open ? (
                    <View style={styles.answerWrap}>
                      <Text style={styles.answerTxt} selectable>
                        {f.a}
                      </Text>
                    </View>
                  ) : null}
                </Pressable>
              );
            })
          )}
        </View>

        {/* ── Bottom CTA ──────────────────────────────────────── */}
        <View style={styles.helpCta}>
          <View style={styles.helpCtaIcon}>
            <PhIcon name="headset" size={20} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.helpCtaTitle}>Couldn't find an answer?</Text>
            <Text style={styles.helpCtaSub}>
              Our support team is here for you.
            </Text>
          </View>
          <TouchableOpacity
            testID="faq-cta-create"
            onPress={() => router.push("/support-center/create" as any)}
            activeOpacity={0.85}
            style={styles.helpCtaBtn}
          >
            <Text style={styles.helpCtaBtnTxt}>Create Request</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </View>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────
const RADIUS = 16;

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  // Hero
  hero: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    backgroundColor: "#fff",
    marginHorizontal: 16,
    marginTop: 12,
    padding: 16,
    borderRadius: RADIUS,
    boxShadow: "0px 1px 4px rgba(0,0,0,0.05)",
    elevation: 1,
  },
  heroIcon: {
    width: 48,
    height: 48,
    borderRadius: 24,
    backgroundColor: "#FFEDD5",
    alignItems: "center",
    justifyContent: "center",
  },
  heroTitle: { fontSize: 15.5, fontWeight: "800", color: "#0F172A" },
  heroSub: { fontSize: 12.5, color: "#64748B", marginTop: 4, lineHeight: 17 },

  // Search
  searchWrap: { marginTop: 14, paddingHorizontal: 16 },
  searchInner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#fff",
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "ios" ? 12 : 8,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.05)",
    elevation: 1,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    color: "#0F172A",
    paddingVertical: 0,
  },
  searchCount: {
    fontSize: 11,
    color: "#94A3B8",
    marginTop: 6,
    marginLeft: 4,
    fontWeight: "700",
  },

  // List
  list: { marginTop: 12, paddingHorizontal: 16, gap: 10 },
  row: {
    backgroundColor: "#fff",
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: "transparent",
    boxShadow: "0px 1px 3px rgba(0,0,0,0.05)",
    elevation: 1,
  },
  rowOpen: {
    borderColor: colors.primary,
  },
  rowHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  catChip: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  catChipTxt: {
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.3,
  },
  rowQ: {
    fontSize: 14,
    fontWeight: "800",
    color: "#0F172A",
    marginTop: 6,
    lineHeight: 19,
  },
  chevWrap: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "#F1F5F9",
    alignItems: "center",
    justifyContent: "center",
  },
  chevWrapOpen: {
    backgroundColor: "#FFEDD5",
  },
  answerWrap: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#E5E7EB",
  },
  answerTxt: {
    fontSize: 13.5,
    color: "#334155",
    lineHeight: 20,
  },

  // Empty
  empty: { alignItems: "center", padding: 32 },
  emptyTitle: { fontSize: 15, fontWeight: "800", color: "#0F172A", marginTop: 14 },
  emptySub: {
    fontSize: 13,
    color: "#64748B",
    textAlign: "center",
    marginTop: 6,
    lineHeight: 18,
  },
  emptyBtn: {
    marginTop: 18,
    backgroundColor: colors.primary,
    paddingHorizontal: 22,
    paddingVertical: 12,
    borderRadius: 12,
  },
  emptyBtnTxt: { color: "#fff", fontSize: 13.5, fontWeight: "800" },

  // Bottom CTA — mirrors support-center.tsx for visual continuity.
  helpCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "#FFF7ED",
    borderRadius: RADIUS,
    marginHorizontal: 16,
    marginTop: 22,
    paddingHorizontal: 14,
    paddingVertical: 14,
  },
  helpCtaIcon: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: "#FFEDD5",
    alignItems: "center",
    justifyContent: "center",
  },
  helpCtaTitle: { color: "#0F172A", fontSize: 14, fontWeight: "800" },
  helpCtaSub: { color: "#64748B", fontSize: 12, marginTop: 2 },
  helpCtaBtn: {
    backgroundColor: colors.primary,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 10,
  },
  helpCtaBtnTxt: { color: "#fff", fontSize: 12.5, fontWeight: "800" },
});
