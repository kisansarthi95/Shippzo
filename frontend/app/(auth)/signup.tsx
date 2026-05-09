import React, { useState, useEffect, useMemo } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  KeyboardAvoidingView, Platform, ScrollView, ActivityIndicator, Alert,
  Modal, FlatList,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";
import GoogleSignInButton from "../../components/GoogleSignInButton";
import { Api } from "../../lib/api";

type BusinessCategory = { slug: string; label: string; icon: string };

export default function SignupScreen() {
  const { signUp } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [shop, setShop] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  // Phase G — primary business category. Required on the form. The list
  // is fetched live from /api/auth/business-categories so adding a new
  // category server-side doesn't need a frontend deploy.
  const [categories, setCategories] = useState<BusinessCategory[]>([]);
  const [businessCategory, setBusinessCategory] = useState<string>("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerSearch, setPickerSearch] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await Api.listBusinessCategories();
        if (alive) setCategories(data.categories || []);
      } catch {
        // Non-fatal — user can still sign up, server just won't validate
        // an empty category and the post-login gate (if any) catches it.
      }
    })();
    return () => { alive = false; };
  }, []);

  const selectedCategory = useMemo(
    () => categories.find((c) => c.slug === businessCategory),
    [categories, businessCategory],
  );

  const filteredCategories = useMemo(() => {
    const q = pickerSearch.trim().toLowerCase();
    if (!q) return categories;
    return categories.filter((c) => c.label.toLowerCase().includes(q));
  }, [categories, pickerSearch]);
  // 2026-04-30 — Privacy / Terms acceptance. New users MUST check this
  // before the "Create account" button is enabled. Stored only locally
  // for this flow; backend doesn't need to persist it because signing
  // up is itself the record of consent.
  const [policyAccepted, setPolicyAccepted] = useState(false);

  const submit = async () => {
    const e = email.trim().toLowerCase();
    if (!e || !password || !name.trim()) {
      Alert.alert("Missing fields", "Email, password and name are required");
      return;
    }
    const phoneDigits = phone.replace(/\D/g, "");
    if (phoneDigits.length < 10) {
      Alert.alert(
        "Mobile number required",
        "Please enter your 10-digit mobile number. We'll use it if you ever need to reset your password.",
      );
      return;
    }
    if (password.length < 6) {
      Alert.alert("Password too short", "Use at least 6 characters");
      return;
    }
    if (!businessCategory) {
      Alert.alert(
        "Pick a category",
        "Please tell us what you sell so we can tailor the app for your business.",
      );
      return;
    }
    if (!policyAccepted) {
      Alert.alert(
        "Please accept Terms & Privacy",
        "You need to tick the checkbox confirming you've read and agree to our Terms of Service and Privacy Policy before creating an account.",
      );
      return;
    }
    setBusy(true);
    try {
      const res = await signUp(
        e, password, name.trim(), shop.trim(), phoneDigits, businessCategory,
      );
      if (res?.trial_denied) {
        // Phase-2b: friendly notice — signup succeeded, free trial wasn't
        // granted because this device already used one. We don't reveal
        // *who* the prior account belongs to.
        Alert.alert(
          "Welcome aboard!",
          "We noticed this device has already used a free trial before, so the trial wasn't started this time. You can subscribe to a paid plan from the Plans screen anytime to continue.",
        );
      }
    } catch (err: any) {
      Alert.alert(
        "Signup failed",
        err?.response?.data?.detail || err?.message || "Please try again"
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
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <PhIcon name="cube-outline" size={36} color={colors.primary} />
            <Text style={styles.brandTitle}>Create account</Text>
            <Text style={styles.brandSub}>Start shipping in minutes — 15 demo orders included</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>Your name</Text>
            <TextInput
              testID="signup-name"
              value={name}
              onChangeText={setName}
              placeholder="Mahek"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>Shop name (optional)</Text>
            <TextInput
              testID="signup-shop"
              value={shop}
              onChangeText={setShop}
              placeholder="Mahek Creations"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>Email</Text>
            <TextInput
              testID="signup-email"
              value={email}
              onChangeText={setEmail}
              placeholder="you@example.com"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>
              Mobile Number <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="signup-phone"
              value={phone}
              onChangeText={(t) => setPhone(t.replace(/[^\d+]/g, ""))}
              placeholder="10-digit mobile number"
              keyboardType="phone-pad"
              maxLength={15}
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />
            <Text style={styles.helperNote}>
              We'll use this to help you reset your password if you forget it, and for urgent support.
            </Text>

            <Text style={[styles.label, { marginTop: 12 }]}>
              What do you sell? <Text style={styles.required}>*</Text>
            </Text>
            <TouchableOpacity
              testID="signup-business-category"
              style={styles.dropdown}
              onPress={() => { setPickerSearch(""); setPickerOpen(true); }}
              activeOpacity={0.8}
            >
              {selectedCategory ? (
                <View style={styles.dropdownValueRow}>
                  <Text style={styles.dropdownIcon}>{selectedCategory.icon}</Text>
                  <Text style={styles.dropdownText} numberOfLines={1}>
                    {selectedCategory.label}
                  </Text>
                </View>
              ) : (
                <Text style={styles.dropdownPlaceholder}>
                  Select your business category
                </Text>
              )}
              <PhIcon name="chevron-down" size={18} color="#64748B" />
            </TouchableOpacity>

            <Text style={[styles.label, { marginTop: 12 }]}>Password (min 6)</Text>
            <View style={styles.pwRow}>
              <TextInput
                testID="signup-password"
                value={password}
                onChangeText={setPassword}
                placeholder="At least 6 characters"
                secureTextEntry={!showPwd}
                style={[styles.input, { flex: 1 }]}
                placeholderTextColor="#94A3B8"
              />
              <TouchableOpacity style={styles.eyeBtn} onPress={() => setShowPwd((v) => !v)}>
                <PhIcon name={showPwd ? "eye-off" : "eye"} size={20} color="#64748B" />
              </TouchableOpacity>
            </View>

            <TouchableOpacity
              testID="signup-submit"
              disabled={busy || !policyAccepted || !businessCategory}
              onPress={submit}
              style={[
                styles.primaryBtn,
                (busy || !policyAccepted || !businessCategory) && { opacity: 0.5 },
              ]}
            >
              {busy
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.primaryBtnText}>Create account</Text>}
            </TouchableOpacity>

            {/* 2026-04-30 — Terms & Privacy acceptance. Required before
                signing up. Links open the hosted policy pages in the
                external browser so the user can review them before
                ticking the box. Using router.push would deep-link back
                inside the auth stack which is noisy on a signup flow. */}
            <TouchableOpacity
              testID="signup-policy-checkbox"
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

            <View style={styles.divider}>
              <View style={styles.dividerLine} />
              <Text style={styles.dividerText}>OR</Text>
              <View style={styles.dividerLine} />
            </View>

            <GoogleSignInButton label="Sign up with Google" />

            <View style={styles.footerRow}>
              <Text style={styles.footerText}>Already have an account?</Text>
              <TouchableOpacity onPress={() => router.push("/(auth)/login")}>
                <Text style={styles.footerLink}>Log in</Text>
              </TouchableOpacity>
            </View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      {/* Phase G — Business category picker modal. Modal pattern keeps
          the signup form short while still surfacing all 16 categories
          (with search) when the user taps the dropdown. */}
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
              data={filteredCategories}
              keyExtractor={(item) => item.slug}
              keyboardShouldPersistTaps="handled"
              ItemSeparatorComponent={() => <View style={styles.modalSep} />}
              ListEmptyComponent={
                <Text style={styles.modalEmpty}>No matches.</Text>
              }
              renderItem={({ item }) => {
                const isSelected = item.slug === businessCategory;
                return (
                  <TouchableOpacity
                    testID={`category-option-${item.slug}`}
                    style={[
                      styles.modalRow,
                      isSelected && styles.modalRowSelected,
                    ]}
                    onPress={() => {
                      setBusinessCategory(item.slug);
                      setPickerOpen(false);
                    }}
                    activeOpacity={0.7}
                  >
                    <Text style={styles.modalRowIcon}>{item.icon}</Text>
                    <Text style={styles.modalRowLabel}>{item.label}</Text>
                    {isSelected ? (
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
  scroll: { flexGrow: 1, justifyContent: "center", padding: 20 },
  brand: { alignItems: "center", marginBottom: 24 },
  brandTitle: { fontSize: 22, fontWeight: "800", color: colors.text, marginTop: 8 },
  brandSub: { fontSize: 13, color: colors.textMuted, marginTop: 2, textAlign: "center" },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 18,
    borderWidth: 2,
    borderColor: "#E5E7EB",
  },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
  required: { color: "#DC2626", fontWeight: "900" },
  helperNote: { fontSize: 11, color: "#64748B", marginTop: 4, marginBottom: 2, lineHeight: 16 },
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
  pwRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  eyeBtn: { width: 44, height: 46, justifyContent: "center", alignItems: "center" },
  primaryBtn: {
    marginTop: 18, height: 50, borderRadius: 12,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  primaryBtnText: { color: "#fff", fontWeight: "800", fontSize: 15 },
  divider: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginVertical: 16,
  },
  dividerLine: { flex: 1, height: 1, backgroundColor: "#E5E7EB" },
  dividerText: { fontSize: 11, fontWeight: "700", color: "#94A3B8", letterSpacing: 1 },
  footerRow: {
    flexDirection: "row", gap: 6,
    justifyContent: "center", alignItems: "center", marginTop: 18,
  },
  footerText: { color: colors.textMuted, fontSize: 13 },
  footerLink: { color: colors.primary, fontWeight: "800", fontSize: 13 },
  // Policy acceptance row
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
  // Phase G — Business category dropdown
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
  dropdownValueRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  dropdownIcon: { fontSize: 18 },
  dropdownText: { flex: 1, fontSize: 15, color: colors.text, fontWeight: "600" },
  dropdownPlaceholder: { flex: 1, fontSize: 15, color: "#94A3B8" },
  // Modal picker
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.45)",
    justifyContent: "flex-end",
  },
  modalSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 14,
    paddingHorizontal: 16,
    paddingBottom: 22,
    maxHeight: "85%",
    minHeight: "55%",
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  modalCloseBtn: {
    width: 32, height: 32, borderRadius: 16,
    backgroundColor: "#F1F5F9",
    alignItems: "center", justifyContent: "center",
  },
  modalSearchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#F8FAFC",
    borderWidth: 1,
    borderColor: "#E2E8F0",
    borderRadius: 10,
    paddingHorizontal: 12,
    height: 42,
    marginBottom: 10,
  },
  modalSearch: {
    flex: 1,
    fontSize: 14,
    color: colors.text,
    paddingVertical: 0,
  },
  modalSep: { height: 1, backgroundColor: "#F1F5F9" },
  modalRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 14,
    paddingHorizontal: 6,
  },
  modalRowSelected: { backgroundColor: "#FFF7ED", borderRadius: 10 },
  modalRowIcon: { fontSize: 22 },
  modalRowLabel: { flex: 1, fontSize: 15, color: colors.text, fontWeight: "600" },
  modalEmpty: {
    textAlign: "center",
    color: "#94A3B8",
    paddingVertical: 24,
    fontSize: 13,
  },
});
