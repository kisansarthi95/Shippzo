/**
 * /app/onboarding/business-category.tsx — Phase G onboarding step.
 *
 * "What do you sell?" — a mandatory single-select category picker shown
 * to every brand-new account (and existing accounts that don't yet have
 * a `primary_business_category` set on them) right after auth gate.
 *
 * UX guarantees:
 *   • One screen, one decision — feels like setup, not a survey.
 *   • Categories pulled live from /api/auth/business-categories so a
 *     future enum addition doesn't require a frontend deploy.
 *   • Search bar filters by label substring (case-insensitive).
 *   • Single-select only; tapping the same card again is a no-op.
 *   • Sticky bottom Continue button stays disabled until a card is
 *     selected, then turns into the brand orange.
 *   • Hardware back is BLOCKED — onboarding is mandatory.
 *
 * After a successful POST, we navigate to /(tabs) and the auth gate's
 * `needs_onboarding_category` flag flips false on the next /context fetch.
 */
import { useRouter } from "expo-router";
import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Alert, BackHandler, ScrollView, StyleSheet, Text,
  TextInput, TouchableOpacity, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import PhIcon from "../../components/PhIcon";
import { Api } from "../../lib/api";


type Category = { slug: string; label: string; icon: string };


export default function OnboardingBusinessCategory() {
  const router = useRouter();
  const [categories, setCategories] = useState<Category[]>([]);
  const [selected, setSelected]     = useState<string>("");
  const [search, setSearch]         = useState("");
  const [loading, setLoading]       = useState(true);
  const [saving, setSaving]         = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await Api.listBusinessCategories();
        if (alive) setCategories(data.categories);
      } catch (e: any) {
        Alert.alert("Couldn't load", e?.message || "Try again.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  // Block hardware back — mandatory onboarding step.
  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => true);
    return () => sub.remove();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return categories;
    return categories.filter((c) => c.label.toLowerCase().includes(q));
  }, [categories, search]);

  const onContinue = async () => {
    if (!selected) return;
    setSaving(true);
    try {
      await Api.setBusinessCategory(selected);
      // Replace history so user can't navigate back to onboarding.
      router.replace("/(tabs)" as any);
    } catch (e: any) {
      Alert.alert("Couldn't save", e?.message || "Try again.");
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color="#FF6B00" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.h1}>What do you sell?</Text>
        <Text style={styles.h2}>
          Pick the category that best describes your products. Helps us tune
          analytics, defaults, and templates for your business.
        </Text>
      </View>

      {/* Search filter — tucked to the top so it stays in reach with one
          thumb on long phones. */}
      <View style={styles.searchWrap}>
        <PhIcon name="search" size={16} color="#94A3B8" />
        <TextInput
          value={search}
          onChangeText={setSearch}
          placeholder="Search categories"
          placeholderTextColor="#94A3B8"
          style={styles.searchInput}
        />
      </View>

      <ScrollView
        contentContainerStyle={styles.gridContent}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.grid}>
          {filtered.map((c) => {
            const active = selected === c.slug;
            return (
              <TouchableOpacity
                key={c.slug}
                style={[styles.cell, active && styles.cellActive]}
                onPress={() => setSelected(c.slug)}
                testID={`cat-${c.slug}`}
              >
                <Text style={styles.icon}>{c.icon}</Text>
                <Text
                  style={[styles.label, active && styles.labelActive]}
                  numberOfLines={2}
                >
                  {c.label}
                </Text>
                {active && (
                  <View style={styles.checkBadge}>
                    <PhIcon name="check" size={12} color="#fff" />
                  </View>
                )}
              </TouchableOpacity>
            );
          })}
        </View>
      </ScrollView>

      {/* Sticky Continue */}
      <View style={styles.footer}>
        <TouchableOpacity
          style={[styles.continueBtn, !selected && styles.continueBtnDisabled]}
          onPress={onContinue}
          disabled={!selected || saving}
          testID="continue-onboarding"
        >
          {saving ? <ActivityIndicator color="#fff" /> : (
            <>
              <Text style={styles.continueTxt}>Continue</Text>
              <PhIcon name="arrow-forward" size={16} color="#fff" />
            </>
          )}
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root:   { flex: 1, backgroundColor: "#FFFFFF" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#FFFFFF" },

  header: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 8 },
  h1:     { fontSize: 24, fontWeight: "800", color: "#0F172A", marginBottom: 6 },
  h2:     { fontSize: 13, color: "#64748B", lineHeight: 19 },

  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 10, marginHorizontal: 16, marginBottom: 8,
    borderRadius: 12, backgroundColor: "#F1F5F9",
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  searchInput: { flex: 1, fontSize: 14, color: "#0F172A" },

  gridContent: { paddingHorizontal: 12, paddingBottom: 24 },
  grid:        { flexDirection: "row", flexWrap: "wrap", justifyContent: "space-between" },

  // Two columns of equal cells; minHeight ensures uniform card size
  // even when label wraps to two lines.
  cell: {
    width: "48%", minHeight: 96,
    paddingHorizontal: 12, paddingVertical: 14, marginBottom: 10,
    borderWidth: 2, borderColor: "#E2E8F0",
    borderRadius: 14, backgroundColor: "#FFFFFF",
    alignItems: "center", justifyContent: "center",
  },
  cellActive: {
    borderColor: "#FF6B00",
    backgroundColor: "#FFF7EE",
  },
  icon:        { fontSize: 30, marginBottom: 6 },
  label:       { fontSize: 13, fontWeight: "600", color: "#0F172A", textAlign: "center" },
  labelActive: { color: "#9A3412" },
  checkBadge: {
    position: "absolute", top: 6, right: 6,
    width: 20, height: 20, borderRadius: 10,
    backgroundColor: "#FF6B00",
    alignItems: "center", justifyContent: "center",
  },

  footer: {
    paddingHorizontal: 16, paddingTop: 8, paddingBottom: 16,
    borderTopWidth: 1, borderTopColor: "#F1F5F9",
    backgroundColor: "#FFFFFF",
  },
  continueBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 14, borderRadius: 12,
    backgroundColor: "#FF6B00",
  },
  continueBtnDisabled: { backgroundColor: "#CBD5E1" },
  continueTxt:         { color: "#FFFFFF", fontWeight: "700", fontSize: 15 },
});
