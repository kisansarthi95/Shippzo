/**
 * Phase-21 — Video Tutorials list screen.
 *
 * UI hierarchy (matches the approved design):
 *   1. Header: back button, "Video Tutorials" title, subtitle,
 *      optional admin add button on the right.
 *   2. Horizontal scrollable category chips (All + dynamic from DB).
 *   3. Cards list: each card has a thumbnail with play overlay +
 *      duration badge on the left, title + description + arrow on
 *      the right.
 *   4. Bottom "Still need help?" CTA card.
 *
 * Admin-only actions (visible when user.is_admin === true):
 *   • "+" button in the header opens the Add Tutorial screen.
 *   • Long-press on a tutorial card opens a Delete confirmation.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
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
import { Api, VideoTutorial, VideoTutorialCategory } from "../../lib/api";
import { useAuth } from "../../lib/auth";

const SUPPORT_EMAIL = (Constants.expoConfig?.extra as any)?.supportEmail || "shippzo.support@gmail.com";
const APP_NAME = Constants.expoConfig?.name || "Shippzo";

export default function VideoTutorialsList() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const isAdmin = !!(user as any)?.is_admin;

  const [cats, setCats]       = useState<VideoTutorialCategory[] | null>(null);
  const [tutorials, setTuts]  = useState<VideoTutorial[] | null>(null);
  const [activeCat, setActive] = useState<string>("All");
  const [refreshing, setR]    = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([
        Api.listVideoTutorialCategories(),
        Api.listVideoTutorials(activeCat),
      ]);
      setCats(c.items || []);
      setTuts(t.items || []);
    } catch {
      setCats([]);
      setTuts([]);
    }
  }, [activeCat]);

  // Reload when active category changes
  useEffect(() => { load(); }, [load]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const onRefresh = async () => { setR(true); await load(); setR(false); };

  const onTutorialPress = (t: VideoTutorial) => router.push(`/support-center/tutorials/${t.id}` as any);

  const onTutorialLongPress = (t: VideoTutorial) => {
    if (!isAdmin) return;
    Alert.alert(
      "Delete tutorial?",
      `"${t.title}" will be permanently removed.`,
      [
        { text: "Cancel", style: "cancel" },
        { text: "Delete", style: "destructive", onPress: async () => {
          try {
            await Api.adminDeleteVideoTutorial(t.id);
            load();
          } catch (e: any) {
            Alert.alert("Failed", e?.response?.data?.detail || "Try again.");
          }
        }},
      ],
    );
  };

  const chips = useMemo(() => {
    return [
      { id: "all", name: "All", icon: "document-text-outline" },
      ...((cats || []).map((c) => ({ id: c.id, name: c.name, icon: c.icon }))),
    ];
  }, [cats]);

  if (tutorials === null || cats === null) {
    return (
      <View style={[styles.root, { justifyContent: "center", alignItems: "center" }]}>
        <Stack.Screen options={{ title: "Video Tutorials", headerShadowVisible: false }} />
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{
          title: "Video Tutorials",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
          headerRight: () => isAdmin ? (
            <TouchableOpacity
              onPress={() => router.push("/support-center/tutorials/admin/add" as any)}
              hitSlop={10}
              style={{ paddingHorizontal: 6 }}
            >
              <PhIcon name="add" size={24} color={colors.primary} />
            </TouchableOpacity>
          ) : null,
        }}
      />
      <ScrollView
        contentContainerStyle={{ paddingBottom: insets.bottom + 24 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
        }
      >
        <View style={styles.headTextWrap}>
          <Text style={styles.headSubtitle}>
            Watch step-by-step videos to learn and use {APP_NAME}.
          </Text>
        </View>

        {/* Category chips */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chipsRow}
        >
          {chips.map((c) => {
            const active = c.name === activeCat;
            return (
              <TouchableOpacity
                key={c.id}
                testID={`vt-chip-${c.name}`}
                onPress={() => setActive(c.name)}
                style={[styles.chip, active && styles.chipActive]}
                activeOpacity={0.85}
              >
                <Text style={[styles.chipTxt, active && styles.chipTxtActive]}>{c.name}</Text>
              </TouchableOpacity>
            );
          })}
        </ScrollView>

        {/* Tutorial cards */}
        <View style={{ paddingHorizontal: 16, marginTop: 12 }}>
          {tutorials.length === 0 ? (
            <View style={styles.empty}>
              <PhIcon name="play" size={32} color="#94A3B8" />
              <Text style={styles.emptyTxt}>
                No tutorials in this category yet.{isAdmin ? "\nTap the + button to add one." : ""}
              </Text>
            </View>
          ) : (
            tutorials.map((t) => (
              <TouchableOpacity
                key={t.id}
                testID={`vt-${t.id}`}
                activeOpacity={0.85}
                onPress={() => onTutorialPress(t)}
                onLongPress={() => onTutorialLongPress(t)}
                style={styles.card}
              >
                <View style={styles.thumbWrap}>
                  <Image source={{ uri: t.thumbnail_url }} style={styles.thumb} />
                  <View style={styles.playOverlay}>
                    <View style={styles.playCircle}>
                      <PhIcon name="play" size={20} color="#fff" />
                    </View>
                  </View>
                  {t.duration ? (
                    <View style={styles.durBadge}>
                      <Text style={styles.durBadgeTxt}>{t.duration}</Text>
                    </View>
                  ) : null}
                </View>
                <View style={{ flex: 1, paddingHorizontal: 12 }}>
                  <Text style={styles.cardTitle} numberOfLines={2}>{t.title}</Text>
                  {t.short_description ? (
                    <Text style={styles.cardSub} numberOfLines={2}>{t.short_description}</Text>
                  ) : null}
                </View>
                <PhIcon name="chevron-forward" size={16} color="#CBD5E1" />
              </TouchableOpacity>
            ))
          )}
        </View>

        {/* Still need help */}
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
              <Text style={styles.helpCardBtnTxt}>Create Support Request</Text>
            </TouchableOpacity>
          </View>
          <View style={styles.helpCardIcon}>
            <PhIcon name="headset" size={32} color={colors.primary} />
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  headTextWrap: { paddingHorizontal: 16, paddingTop: 10 },
  headSubtitle: { fontSize: 13.5, color: "#64748B", lineHeight: 18 },

  chipsRow: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 4, gap: 8 },
  chip: {
    paddingHorizontal: 16, paddingVertical: 8,
    borderRadius: 999, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipTxt: { fontSize: 13, fontWeight: "700", color: "#475569" },
  chipTxtActive: { color: "#fff" },

  card: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#fff", borderRadius: 14,
    padding: 10, marginBottom: 10,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.05)", elevation: 1,
  },
  thumbWrap: { width: 110, height: 70, borderRadius: 10, overflow: "hidden", backgroundColor: "#000" },
  thumb:     { width: "100%", height: "100%" },
  playOverlay: {
    position: "absolute", top: 0, bottom: 0, left: 0, right: 0,
    alignItems: "center", justifyContent: "center",
  },
  playCircle: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: "rgba(0,0,0,0.55)",
    alignItems: "center", justifyContent: "center",
  },
  durBadge: {
    position: "absolute", right: 6, bottom: 6,
    backgroundColor: "rgba(0,0,0,0.75)",
    paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  durBadgeTxt: { color: "#fff", fontSize: 10, fontWeight: "800" },
  cardTitle: { fontSize: 13.5, fontWeight: "800", color: "#0F172A" },
  cardSub:   { fontSize: 11.5, color: "#64748B", marginTop: 4, lineHeight: 15 },

  empty: { alignItems: "center", paddingVertical: 40 },
  emptyTxt: { color: "#64748B", fontSize: 13, marginTop: 12, textAlign: "center", lineHeight: 18 },

  helpCard: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#FFF7ED", borderRadius: 14,
    paddingHorizontal: 14, paddingVertical: 14,
    marginHorizontal: 16, marginTop: 18,
  },
  helpCardTitle: { fontSize: 13.5, fontWeight: "800", color: "#0F172A" },
  helpCardSub: { fontSize: 12, color: "#64748B", marginTop: 4 },
  helpCardBtn: {
    alignSelf: "flex-start", marginTop: 10,
    backgroundColor: colors.primary, borderRadius: 10,
    paddingHorizontal: 14, paddingVertical: 8,
  },
  helpCardBtnTxt: { color: "#fff", fontSize: 12.5, fontWeight: "800" },
  helpCardIcon: {
    width: 56, height: 56, borderRadius: 28,
    backgroundColor: "#FFEDD5",
    alignItems: "center", justifyContent: "center",
    marginLeft: 12,
  },
});
