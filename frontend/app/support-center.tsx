/**
 * Phase-21 — Support Center
 *
 * Reachable from Settings → About & Help → Support Center.
 *
 * The visual hierarchy matches the approved design 1:1:
 *   1. Orange "How can we help you today?" banner with the headset
 *      illustration on the right.
 *   2. Search bar inside the banner — type to filter the article list
 *      below client-side. (Server-side article search can plug in
 *      later via `searchArticles()` without changing this screen.)
 *   3. 2x2 grid of large action cards:
 *        • Video Tutorials   • FAQs
 *        • Create Request    • My Requests
 *   4. "Popular Articles" list with a "View All" link.
 *   5. Sticky-feeling "Still need help?" CTA at the bottom that opens
 *      the same Create Request flow used in card #3.
 *
 * Styling notes — strict adherence to the existing app theme:
 *   • Orange accent reuses `colors.primary` (do NOT hardcode #FF8A3D).
 *   • Card backgrounds use `colors.surface`; outer screen is the soft
 *     `colors.bgSoft` already used by Settings.
 *   • Border radius / shadow / padding match `aboutLinkRow`-style
 *     cards in settings.tsx so the screen feels like a natural
 *     extension of the existing flow.
 *
 * The four card actions and the article list rows currently stub out
 * to lightweight Alert.alert() / mailto fallbacks so the visual
 * structure is fully usable today; each action has a clear hand-off
 * point (TODO comment) for when the real Tickets/Knowledge-Base
 * backend lands.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Linking,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { useRouter, Stack } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Constants from "expo-constants";

import PhIcon from "../components/PhIcon";
import SearchBar from "../components/SearchBar";
import { colors } from "../lib/theme";
import { useFeatureFlag } from "../lib/feature_flags";
import { Api } from "../lib/api";

// App branding inline constants — mirror the lookup used in
// settings.tsx so the support-center stays consistent if/when the
// app is rebranded via app.json `expoConfig.extra`.
const APP_EXTRA: any   = (Constants.expoConfig?.extra ?? {}) as any;
const APP_NAME: string = Constants.expoConfig?.name || "Shippzo";
const SUPPORT_EMAIL: string =
  APP_EXTRA.supportEmail || "shippzo.support@gmail.com";

// ───── Article type — mirrors the public /api/articles list shape ───
// `body` is intentionally NOT included here; the list endpoint omits
// it to keep the payload small. The detail screen (/support-center/
// articles/[id]) fetches the full body when an article is opened.
type ArticleRow = {
  id:         string;
  title:      string;
  summary:    string;
  icon:       string;
  category:   string;
  sort_order: number;
  is_visible: boolean;
};

export default function SupportCenter() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [query, setQuery]       = useState<string>("");
  const [articles, setArticles] = useState<ArticleRow[]>([]);
  const [articlesLoading, setArticlesLoading] = useState<boolean>(true);
  const [articlesError, setArticlesError]     = useState<string>("");

  // ── Fetch the article list once when the screen mounts. The
  // backend keeps `body` out of this list response so the payload
  // stays small even with dozens of articles. The detail screen
  // fetches the full body on tap.
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const r = await Api.listArticles();
        if (!mounted) return;
        setArticles(r.items || []);
      } catch (e: any) {
        if (!mounted) return;
        setArticlesError(
          e?.response?.data?.detail || e?.message || "Could not load articles",
        );
      } finally {
        if (mounted) setArticlesLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, []);

  // Lightweight client-side filter for the Popular Articles list.
  // Server-side full-text search can plug in later via
  // `Api.searchArticles(query)` without changing this screen.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return articles;
    return articles.filter((a) =>
      a.title.toLowerCase().includes(q)
        || (a.summary || "").toLowerCase().includes(q)
        || (a.category || "").toLowerCase().includes(q),
    );
  }, [articles, query]);

  // ── In-app article navigation. Replaces the previous
  // `Linking.openURL("https://shippzo.com/help")` redirects so every
  // article tap stays inside the app. The reader lives at
  // /support-center/articles/[id] and the index ("View All") at
  // /support-center/articles.
  const openArticle = useCallback(
    (id: string) => router.push(`/support-center/articles/${id}` as any),
    [router],
  );
  const openArticleList = useCallback(
    () => router.push("/support-center/articles" as any),
    [router],
  );

  // Centralised "fall back to email" helper kept for completeness —
  // not used directly anymore (Create Request + My Requests now route
  // to their own screens) but ready when the Video Tutorials and FAQs
  // surfaces need an "Email us instead" fallback.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _openSupportEmail = (subject: string) => {
    Linking.openURL(
      `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
        `${APP_NAME} — ${subject}`,
      )}`,
    ).catch(() => {});
  };

  // Card press handlers. Phase-21 — Video Tutorials, Create Request,
  // and My Requests all route to dedicated screens. FAQs now opens
  // the in-app /support-center/faq accordion (Phase-26, 2026-05-30) —
  // previously this incorrectly opened the legal Privacy/Refund
  // policy page, which had nothing to do with FAQs.
  const onVideoTutorials = () => router.push("/support-center/tutorials" as any);
  const onFAQs           = () => router.push("/support-center/faq" as any);
  const onCreateRequest  = () => router.push("/support-center/create" as any);
  const onMyRequests     = () => router.push("/support-center/my-tickets" as any);

  // 2026-05-25 — Plan-gated Video Tutorials card. Defaults ON for
  // every plan; admin can untick per-plan in the admin Plan
  // Features panel. The 2×2 grid below renders the tutorial card
  // only when this flag is ON — the other 3 cards stay visible.
  const flagVideoTutorials = useFeatureFlag("video_tutorials");

  return (
    <View style={[styles.root, { paddingBottom: insets.bottom + 16 }]}>
      <Stack.Screen
        options={{
          title: "Support Center",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ paddingBottom: 24 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* ─── 1. Orange help banner ───────────────────────────── */}
        <View style={styles.banner}>
          <View style={{ flex: 1, paddingRight: 12 }}>
            <Text style={styles.bannerTitle}>How can we help you today?</Text>
            <Text style={styles.bannerSub}>
              Search for help or raise a support request
            </Text>
          </View>
          <View style={styles.bannerHeadsetWrap}>
            <PhIcon name="headset" size={42} color="rgba(255,255,255,0.95)" />
          </View>
        </View>

        {/* ─── 2. Search bar (overlaps the banner footer) ─────── */}
        <View style={styles.searchWrap}>
          <SearchBar
            testID="support-search"
            value={query}
            onChangeText={setQuery}
            placeholder="Search for articles, topics..."
            containerStyle={{ marginTop: 0, marginHorizontal: 0 }}
          />
        </View>

        {/* ─── 3. 2×2 action card grid ────────────────────────── */}
        <View style={styles.grid}>
          {flagVideoTutorials ? (
            <BigCard
              testID="sc-video"
              icon="play"
              iconBg="#EDE9FE"
              iconColor="#7C3AED"
              title="Video Tutorials"
              sub={`Watch tutorials and learn\nhow to use ${APP_NAME}`}
              onPress={onVideoTutorials}
            />
          ) : null}
          <BigCard
            testID="sc-faqs"
            icon="question"
            iconBg="#DBEAFE"
            iconColor="#2563EB"
            title="FAQs"
            sub={"Find answers to\ncommon questions"}
            onPress={onFAQs}
          />
          <BigCard
            testID="sc-create"
            icon="document-text-outline"
            iconBg="#FFEDD5"
            iconColor={colors.primary}
            title="Create Request"
            sub={"Raise a support request\nfor any issue"}
            onPress={onCreateRequest}
          />
          <BigCard
            testID="sc-my"
            icon="clipboard"
            iconBg="#DCFCE7"
            iconColor="#16A34A"
            title="My Requests"
            sub={"Track and view your\nsupport requests"}
            onPress={onMyRequests}
          />
        </View>

        {/* ─── 4. Popular Articles list ───────────────────────── */}
        <View style={styles.articlesHeader}>
          <Text style={styles.articlesTitle}>Popular Articles</Text>
          <TouchableOpacity
            testID="sc-articles-view-all"
            onPress={openArticleList}
          >
            <Text style={styles.articlesViewAll}>View All</Text>
          </TouchableOpacity>
        </View>

        <View style={styles.articlesCard}>
          {articlesLoading ? (
            <View style={styles.emptyArticles}>
              <Text style={styles.emptyArticlesTxt}>Loading articles…</Text>
            </View>
          ) : articlesError ? (
            <View style={styles.emptyArticles}>
              <Text style={styles.emptyArticlesTxt}>{articlesError}</Text>
            </View>
          ) : filtered.length === 0 ? (
            <View style={styles.emptyArticles}>
              <Text style={styles.emptyArticlesTxt}>
                {query.trim()
                  ? `No articles match "${query}". Try a different search or tap Create Request below.`
                  : "No articles published yet. An admin can add them from /admin/articles."}
              </Text>
            </View>
          ) : (
            filtered.map((a, i) => (
              <Pressable
                key={a.id}
                testID={`sc-article-${a.id}`}
                onPress={() => openArticle(a.id)}
                style={({ pressed }) => [
                  styles.articleRow,
                  i < filtered.length - 1 && styles.articleRowDivider,
                  pressed && { opacity: 0.65 },
                ]}
              >
                <PhIcon
                  name={(a.icon as any) || "document-text-outline"}
                  size={18}
                  color="#64748B"
                />
                <Text style={styles.articleTxt} numberOfLines={1}>
                  {a.title}
                </Text>
                <PhIcon name="chevron-forward" size={16} color="#CBD5E1" />
              </Pressable>
            ))
          )}
        </View>

        {/* ─── 5. Bottom "Still need help?" CTA ───────────────── */}
        <View style={styles.helpCta}>
          <View style={styles.helpCtaIcon}>
            <PhIcon name="headset" size={20} color={colors.primary} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.helpCtaTitle}>Still need help?</Text>
            <Text style={styles.helpCtaSub}>Our support team is here for you.</Text>
          </View>
          <TouchableOpacity
            testID="sc-cta-create"
            onPress={onCreateRequest}
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

// ─── Reusable big-card component (2x2 grid item) ─────────────────────
function BigCard({
  testID, icon, iconBg, iconColor, title, sub, onPress,
}: {
  testID?: string;
  icon: string;
  iconBg: string;
  iconColor: string;
  title: string;
  sub: string;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      activeOpacity={0.85}
      style={styles.bigCard}
    >
      <View style={[styles.bigCardIcon, { backgroundColor: iconBg }]}>
        <PhIcon name={icon} size={22} color={iconColor} />
      </View>
      <Text style={styles.bigCardTitle}>{title}</Text>
      <Text style={styles.bigCardSub}>{sub}</Text>
    </TouchableOpacity>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────
const RADIUS = 16;

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#F4F5F7",
  },

  // 1. Banner
  banner: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.primary,
    borderRadius: RADIUS,
    marginHorizontal: 16,
    marginTop: 12,
    paddingHorizontal: 18,
    paddingTop: 18,
    paddingBottom: 44, // leaves room for search bar overlap below
  },
  bannerTitle: {
    color: "#fff",
    fontSize: 20,
    fontWeight: "800",
    lineHeight: 26,
  },
  bannerSub: {
    color: "rgba(255,255,255,0.88)",
    fontSize: 13,
    marginTop: 6,
  },
  bannerHeadsetWrap: {
    width: 70,
    height: 70,
    borderRadius: 35,
    backgroundColor: "rgba(255,255,255,0.12)",
    alignItems: "center",
    justifyContent: "center",
  },

  // 2. Search bar — overlaps the banner so it visually anchors the
  // top of the page (mirrors the reference design).
  searchWrap: {
    marginTop: -28,
    paddingHorizontal: 26,
  },
  searchInner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#fff",
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: Platform.OS === "ios" ? 12 : 8,
    boxShadow: "0px 2px 6px rgba(0,0,0,0.07)",
    elevation: 2,
  },
  searchInput: {
    flex: 1,
    fontSize: 14,
    color: "#0F172A",
    paddingVertical: 0,
  },

  // 3. 2×2 grid
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    paddingHorizontal: 12,
    marginTop: 18,
    gap: 12,
  },
  bigCard: {
    width: "47%",
    flexGrow: 1,
    backgroundColor: "#fff",
    borderRadius: RADIUS,
    paddingVertical: 22,
    paddingHorizontal: 16,
    alignItems: "center",
    boxShadow: "0px 1px 4px rgba(0,0,0,0.05)",
    elevation: 1,
  },
  bigCardIcon: {
    width: 52,
    height: 52,
    borderRadius: 26,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 10,
  },
  bigCardTitle: {
    color: "#0F172A",
    fontSize: 14.5,
    fontWeight: "800",
  },
  bigCardSub: {
    color: "#64748B",
    fontSize: 12,
    textAlign: "center",
    marginTop: 4,
    lineHeight: 16,
  },

  // 4. Popular Articles
  articlesHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginHorizontal: 16,
    marginTop: 22,
    marginBottom: 10,
  },
  articlesTitle: {
    color: "#0F172A",
    fontSize: 15,
    fontWeight: "800",
  },
  articlesViewAll: {
    color: colors.primary,
    fontSize: 13,
    fontWeight: "700",
  },
  articlesCard: {
    backgroundColor: "#fff",
    borderRadius: RADIUS,
    marginHorizontal: 16,
    paddingHorizontal: 14,
    boxShadow: "0px 1px 4px rgba(0,0,0,0.05)",
    elevation: 1,
  },
  articleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
  },
  articleRowDivider: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#E5E7EB",
  },
  articleTxt: {
    flex: 1,
    color: "#0F172A",
    fontSize: 14,
    fontWeight: "500",
  },
  emptyArticles: { padding: 16 },
  emptyArticlesTxt: { color: "#64748B", fontSize: 13, lineHeight: 18 },

  // 5. Bottom CTA
  helpCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "#FFF7ED",
    borderRadius: RADIUS,
    marginHorizontal: 16,
    marginTop: 18,
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
  helpCtaTitle: {
    color: "#0F172A",
    fontSize: 14,
    fontWeight: "800",
  },
  helpCtaSub: {
    color: "#64748B",
    fontSize: 12,
    marginTop: 2,
  },
  helpCtaBtn: {
    backgroundColor: colors.primary,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: 10,
  },
  helpCtaBtnTxt: {
    color: "#fff",
    fontSize: 12.5,
    fontWeight: "800",
  },
});
