/**
 * /app/(auth)/complete-profile.tsx — Phase G2 post-Google-signup gate.
 *
 * Google OAuth gives us only `email` + `name` — NONE of the data the
 * dashboard depends on (Business name, Mobile number, Business
 * category). Instead of letting the user wander into a broken
 * dashboard, the auth gate (app/_layout.tsx) bounces them here when
 * `/auth/context.needs_profile_completion` is true. Hardware back is
 * blocked; the only exit is a successful POST.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator,
  Alert, Modal, FlatList, BackHandler,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";

type Category = { slug: string; label: string; icon: string };

export default function CompleteProfileScreen() {
  const router = useRouter();
  const { user, refresh, signOut } = useAuth();

  const [shopName, setShopName] = useState("");
  const [phone, setPhone] = useState("");
  const [category, setCategory] = useState("");
  const [policyAccepted, setPolicyAccepted] = useState(false);
  const [busy, setBusy] = useState(false);

  const [categories, setCategories] = useState<Category[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");

  // Mandatory step — block hardware back so users can't slip into a
  // half-configured dashboard. They can still sign out via the bail
  // link below if they truly want to abandon the flow.
  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => true);
    return () => sub.remove();
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await Api.listBusinessCategories();
        if (alive) setCategories(data.categories || []);
      } catch {
        /* non-fatal */
      }
    })();
    return () => { alive = false; };
  }, []);

  const selected = useMemo(
    () => categories.find((c) => c.slug === category),
    [categories, category],
  );
  const filtered = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    if (!q) return categories;
    return categories.filter((c) => c.label.toLowerCase().includes(q));
  }, [categories, pickerSearch]);

  const canSubmit =
    !!shopName.trim() &&
    phone.replace(/\D/g, "").length >= 10 &&
    !!category &&
    policyAccepted;

  const submit = async () => {
    if (!shopName.trim()) {
      Alert.alert("Business Name required", "Please enter your business name.");
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length < 10) {
      Alert.alert(
        "Mobile number required",
        "Please enter your 10-digit mobile number.",
      );
      return;
    }
    if (!category) {
      Alert.alert("Pick a category", "Please tell us what you sell.");
      return;
    }
    if (!policyAccepted) {
      Alert.alert(
        "Please accept Terms & Privacy",
        "Tick the checkbox to continue.",
      );
      return;
    }
    setBusy(true);
    try {
      await Api.completeProfile({
        shop_name: shopName.trim(),
        phone: phoneDigits,
        primary_business_category: category,
      });
      // Refresh /auth/me so the cached user object picks up the new
      // shop_name + phone, then jump into the dashboard. The auth gate
      // re-runs on the next mount and stops bouncing us back here
      // because needs_profile_completion is now false.
      await refresh();
      router.replace("/(tabs)" as any);
    } catch (e: any) {
      Alert.alert(
        "Couldn't save",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.brandWrap}>
            <BrandHeaderAnimator variant="login" />
            <Text style={styles.tagline}>
              Welcome{user?.name ? `, ${user.name.split(" ")[0]}` : ""}! Just a few more details.
            </Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>
              Business Name <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="complete-business-name"
              value={shopName}
              onChangeText={setShopName}
              placeholder="Your business name"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>
              Mobile Number <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="complete-phone"
              value={phone}
              onChangeText={(t) => setPhone(t.replace(/[^\d+]/g, ""))}
              placeholder="10-digit mobile number"
              keyboardType="phone-pad"
              maxLength={15}
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />
            <Text style={styles.helperNote}>
              We'll use this for password recovery and urgent support.
            </Text>

            <Text style={[styles.label, { marginTop: 12 }]}>
              What do you sell? <Text style={styles.required}>*</Text>
            </Text>
            <TouchableOpacity
              testID="complete-business-category"
              style={styles.dropdown}
              onPress={() => { setPickerSearch(""); setPickerOpen(true); }}
              activeOpacity={0.8}
            >
              {selected ? (
                <View style={styles.dropdownValueRow}>
                  <Text style={styles.dropdownIcon}>{selected.icon}</Text>
                  <Text style={styles.dropdownText} numberOfLines={1}>
                    {selected.label}
                  </Text>
                </View>
              ) : (
                <Text style={styles.dropdownPlaceholder}>
                  Select your business category
                </Text>
              )}
              <PhIcon name="chevron-down" size={18} color="#64748B" />
            </TouchableOpacity>

            <TouchableOpacity
              testID="complete-policy-checkbox"
              onPress={() => setPolicyAccepted((v) => !v)}
              style={styles.policyRow}
              activeOpacity={0.8}
            >
              <View style={[styles.checkbox, policyAccepted && styles.checkboxOn]}>
                {policyAccepted ? (
                  <PhIcon name="checkmark" size={14} color="#fff" />
                ) : null}
              </View>
              <Text style={styles.policyText}>
                I've read and agree to the{" "}
                <Text
                  style={styles.policyLink}
                  onPress={() => router.push("/refund-policy?tab=terms" as any)}
                >
                  Terms of Service
                </Text>
                {"  &  "}
                <Text
                  style={styles.policyLink}
                  onPress={() => router.push("/refund-policy?tab=privacy" as any)}
                >
                  Privacy Policy
                </Text>
                .
              </Text>
            </TouchableOpacity>

            <TouchableOpacity
              testID="complete-submit"
              disabled={busy || !canSubmit}
              onPress={submit}
              style={[styles.primaryBtn, (busy || !canSubmit) && { opacity: 0.5 }]}
            >
              {busy
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.primaryBtnText}>Continue</Text>}
            </TouchableOpacity>

            <TouchableOpacity
              onPress={async () => {
                await signOut();
                router.replace("/(auth)/welcome" as any);
              }}
              style={{ marginTop: 14, alignSelf: "center" }}
            >
              <Text style={styles.bailLink}>Sign out instead</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      <Modal
        visible={pickerOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPickerOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>What do you sell?</Text>
              <TouchableOpacity
                onPress={() => setPickerOpen(false)}
                style={styles.modalCloseBtn}
                hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
              >
                <PhIcon name="close" size={20} color="#475569" />
              </TouchableOpacity>
            </View>
            <View style={styles.modalSearchWrap}>
              <PhIcon name="search" size={16} color="#94A3B8" />
              <TextInput
                value={pickerSearch}
                onChangeText={setPickerSearch}
                placeholder="Search categories"
                placeholderTextColor="#94A3B8"
                style={styles.modalSearch}
                autoCorrect={false}
              />
            </View>
            <FlatList
              data={filtered}
              keyExtractor={(item) => item.slug}
              keyboardShouldPersistTaps="handled"
              ItemSeparatorComponent={() => <View style={styles.modalSep} />}
              ListEmptyComponent={
                <Text style={styles.modalEmpty}>No matches.</Text>
              }
              renderItem={({ item }) => {
                const isSel = item.slug === category;
                return (
                  <TouchableOpacity
                    testID={`category-option-${item.slug}`}
                    style={[styles.modalRow, isSel && styles.modalRowSelected]}
                    onPress={() => { setCategory(item.slug); setPickerOpen(false); }}
                    activeOpacity={0.7}
                  >
                    <Text style={styles.modalRowIcon}>{item.icon}</Text>
                    <Text style={styles.modalRowLabel}>{item.label}</Text>
                    {isSel ? (
                      <PhIcon name="checkmark" size={18} color={colors.primary} />
                    ) : null}
                  </TouchableOpacity>
                );
              }}
            />
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  scroll: { flexGrow: 1, paddingHorizontal: 20, paddingVertical: 24 },
  brandWrap: { alignItems: "center", marginBottom: 18 },
  tagline: {
    marginTop: 12,
    fontSize: 14,
    color: colors.textMuted,
    textAlign: "center",
    fontWeight: "600",
    paddingHorizontal: 12,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
  required: { color: "#DC2626", fontWeight: "900" },
  helperNote: { fontSize: 11, color: "#64748B", marginTop: 4, lineHeight: 16 },
  input: {
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 46,
    fontSize: 15,
    color: colors.text,
  },
  dropdown: {
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 46,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  dropdownValueRow: { flex: 1, flexDirection: "row", alignItems: "center", gap: 8 },
  dropdownIcon: { fontSize: 18 },
  dropdownText: { flex: 1, fontSize: 15, color: colors.text, fontWeight: "600" },
  dropdownPlaceholder: { flex: 1, fontSize: 15, color: "#94A3B8" },
  primaryBtn: {
    marginTop: 18, height: 50, borderRadius: 12,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  bailLink: { color: colors.textMuted, fontSize: 12, fontWeight: "700", textDecorationLine: "underline" },
  policyRow: {
    flexDirection: "row", alignItems: "flex-start", gap: 10,
    marginTop: 14, paddingHorizontal: 2,
  },
  checkbox: {
    width: 22, height: 22, borderRadius: 6,
    borderWidth: 2, borderColor: "#94A3B8",
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#fff",
  },
  checkboxOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  policyText: { flex: 1, fontSize: 12, color: "#475569", lineHeight: 17 },
  policyLink: { color: colors.primary, fontWeight: "800", textDecorationLine: "underline" },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(15,23,42,0.45)", justifyContent: "flex-end" },
  modalSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 14, paddingHorizontal: 16, paddingBottom: 22,
    maxHeight: "85%",
    minHeight: "55%",
  },
  modalHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", marginBottom: 12,
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  modalCloseBtn: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: "#F1F5F9",
    alignItems: "center", justifyContent: "center",
  },
  modalSearchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 10, paddingHorizontal: 12, height: 42, marginBottom: 10,
  },
  modalSearch: { flex: 1, fontSize: 14, color: colors.text, paddingVertical: 0 },
  modalSep: { height: 1, backgroundColor: "#F1F5F9" },
  modalRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 14, paddingHorizontal: 6,
  },
  modalRowSelected: { backgroundColor: "#FFF7ED", borderRadius: 10 },
  modalRowIcon: { fontSize: 22 },
  modalRowLabel: { flex: 1, fontSize: 15, color: colors.text, fontWeight: "600" },
  modalEmpty: { textAlign: "center", color: "#94A3B8", paddingVertical: 24, fontSize: 13 },
});
