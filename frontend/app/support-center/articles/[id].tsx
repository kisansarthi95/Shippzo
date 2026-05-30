/**
 * Phase-29 — In-app Article Reader.
 *
 * Lives at /support-center/articles/[id]. Replaces the previous
 * Linking.openURL("https://shippzo.com/help") redirect so every
 * article tap stays inside the app.
 *
 * The article body is plain text with line breaks (no Markdown
 * renderer dependency yet) — we render every blank-line separated
 * chunk as its own paragraph and detect leading "1." / "•" / "-"
 * markers to format ordered + bullet lists.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import * as Clipboard from "expo-clipboard";

import PhIcon from "../../../components/PhIcon";
import { colors } from "../../../lib/theme";
import { Api } from "../../../lib/api";

type Article = {
  id:         string;
  title:      string;
  summary:    string;
  body:       string;
  icon:       string;
  category:   string;
  updated_at?: string;
};

// ── Lightweight paragraph + list renderer ──────────────────────────
// We deliberately avoid pulling in a Markdown library — the body
// strings shipped from the seed (or admin CMS) only need:
//   • blank-line separated paragraphs
//   • lines that start with "1.", "2.", "-", "•" → list items
// Anything fancier (bold/italic/images) can be added later by
// dropping in `react-native-markdown-display` without changing the
// reader's public surface.
function renderBody(body: string) {
  const paragraphs = body.split(/\n\s*\n/);
  return paragraphs.map((para, pi) => {
    const lines = para.split("\n").map((l) => l.trimEnd());
    const isList = lines.every((l) =>
      /^\s*(?:[•\-\u2022]|\d+\.)\s+/.test(l),
    );
    if (isList) {
      return (
        <View key={pi} style={styles.list}>
          {lines.map((line, li) => {
            const m = line.match(/^\s*(?:([•\-\u2022])|(\d+\.))\s+(.*)$/);
            const marker = m?.[1] || m?.[2] || "•";
            const text   = m?.[3] || line;
            return (
              <View key={li} style={styles.listRow}>
                <Text style={styles.listMarker}>{marker}</Text>
                <Text style={styles.listText}>{text}</Text>
              </View>
            );
          })}
        </View>
      );
    }
    return (
      <Text key={pi} style={styles.paragraph}>
        {para.replace(/\n/g, " ")}
      </Text>
    );
  });
}

export default function ArticleReaderScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const articleId = Array.isArray(id) ? id[0] : id;

  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState("");

  const load = useCallback(async () => {
    if (!articleId) {
      setError("Missing article id");
      setLoading(false);
      return;
    }
    try {
      setError("");
      const r = await Api.getArticle(articleId);
      setArticle(r.item);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Article not found";
      setError(detail);
    } finally {
      setLoading(false);
    }
  }, [articleId]);

  useEffect(() => { load(); }, [load]);

  const copyBody = async () => {
    if (!article?.body) return;
    try {
      await Clipboard.setStringAsync(
        `${article.title}\n\n${article.body}`,
      );
      Alert.alert("Copied ✓", "Article text copied to clipboard.");
    } catch {
      Alert.alert("Copy failed", "Could not access clipboard.");
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />

      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn} testID="article-back">
          <PhIcon name="arrow-back" size={22} color="#0F172A" />
        </TouchableOpacity>
        <Text style={styles.headerTitle} numberOfLines={1}>Article</Text>
        <TouchableOpacity onPress={copyBody} style={styles.backBtn} testID="article-copy">
          <PhIcon name="copy-outline" size={20} color="#1E40AF" />
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : error ? (
        <View style={styles.center}>
          <PhIcon name="alert-circle-outline" size={36} color="#DC2626" />
          <Text style={styles.errorTxt}>{error}</Text>
          <Pressable
            style={styles.retryBtn}
            onPress={() => { setLoading(true); load(); }}
          >
            <Text style={styles.retryTxt}>Try again</Text>
          </Pressable>
        </View>
      ) : article ? (
        <ScrollView contentContainerStyle={{ padding: 18, paddingBottom: 48 }}>
          <View style={styles.iconWrap}>
            <PhIcon
              name={(article.icon as any) || "document-text-outline"}
              size={28}
              color="#1E40AF"
            />
          </View>
          <Text style={styles.category}>{article.category}</Text>
          <Text style={styles.title} testID="article-title">{article.title}</Text>
          {!!article.summary && (
            <Text style={styles.summary}>{article.summary}</Text>
          )}
          <View style={styles.divider} />
          <View testID="article-body">
            {renderBody(article.body || "")}
          </View>
        </ScrollView>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#fff" },

  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 8,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
  },
  backBtn: { padding: 6 },
  headerTitle: {
    flex: 1, fontSize: 16, fontWeight: "700",
    color: "#0F172A", textAlign: "center",
  },

  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    gap: 10,
  },
  errorTxt: { color: "#475569", textAlign: "center", fontSize: 14 },
  retryBtn: {
    marginTop: 8,
    paddingHorizontal: 16, paddingVertical: 8,
    backgroundColor: colors.primary,
    borderRadius: 8,
  },
  retryTxt: { color: "#fff", fontWeight: "700" },

  iconWrap: {
    width: 56, height: 56, borderRadius: 14,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#DBEAFE",
    marginBottom: 12,
  },
  category: {
    fontSize: 12, fontWeight: "700",
    color: "#1E40AF",
    textTransform: "uppercase",
    letterSpacing: 0.4,
  },
  title: {
    fontSize: 22, fontWeight: "800",
    color: "#0F172A", marginTop: 4, lineHeight: 28,
  },
  summary: {
    fontSize: 14, color: "#475569",
    marginTop: 8, lineHeight: 20,
  },
  divider: {
    height: 1, backgroundColor: "#E5E7EB",
    marginVertical: 18,
  },
  paragraph: {
    fontSize: 15, color: "#1F2937",
    lineHeight: 23,
    marginBottom: 14,
  },
  list: { marginBottom: 14 },
  listRow: { flexDirection: "row", gap: 8, marginBottom: 6 },
  listMarker: {
    width: 22, color: "#1E40AF",
    fontWeight: "700", fontSize: 15,
  },
  listText: {
    flex: 1, fontSize: 15,
    color: "#1F2937", lineHeight: 23,
  },
});
