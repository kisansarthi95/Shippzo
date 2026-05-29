import React, { useState, useEffect, useMemo, useRef } from "react";
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
import BrandHeaderAnimator from "../../components/BrandHeaderAnimator";
import { Api, api } from "../../lib/api";

type BusinessCategory = { slug: string; label: string; icon: string };

export default function SignupScreen() {
  const { signInWithOtp } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Phase G2 — Re-enter password to catch typos. Both fields render
  // the same eye-toggle pattern but with independent visibility state.
  const [confirmPassword, setConfirmPassword] = useState("");
  const [name, setName] = useState("");
  const [shop, setShop] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [showConfirmPwd, setShowConfirmPwd] = useState(false);
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

  // ─── Phase-OTP — inline WhatsApp phone verification ──────────────
  // After the user submits the registration form, we (1) create the
  // full account via /auth/signup directly (so email + password +
  // category are all persisted as the form intends) but WITHOUT
  // persisting the session locally, then (2) immediately fire an OTP
  // to the supplied mobile number. The user proves they own that
  // number by entering the 6-digit code in the inline OTP field that
  // appears below — only after a successful /auth/otp/verify (which
  // finds the freshly-created user by phone and mints a JWT) is the
  // session actually persisted by signInWithOtp().
  //
  // If the OTP step fails (wrong code / expired) the user can resend
  // and retry — the underlying account already exists in the DB and
  // can be reached via the email/password path or a fresh OTP at any
  // time.
  const [otpSent, setOtpSent] = useState(false);
  const [otp, setOtp] = useState("");
  const [verifyingOtp, setVerifyingOtp] = useState(false);
  const [maskedPhone, setMaskedPhone] = useState("");
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [resending, setResending] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const startCountdown = (seconds: number) => {
    if (timerRef.current) clearInterval(timerRef.current);
    setSecondsLeft(seconds);
    timerRef.current = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          if (timerRef.current) clearInterval(timerRef.current);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
  };

  const normalisePhone = (digits: string) =>
    digits.length === 10 ? `+91${digits}` : digits.startsWith("91") ? `+${digits}` : `+${digits}`;

  // Phase G2 — every field is mandatory; the button stays disabled
  // until the entire form is well-formed AND the policy box is ticked.
  const phoneDigitsLen = phone.replace(/\D/g, "").length;
  const formValid =
    !!name.trim() &&
    !!shop.trim() &&
    !!email.trim() &&
    phoneDigitsLen >= 10 &&
    !!businessCategory &&
    password.length >= 6 &&
    confirmPassword.length >= 6 &&
    password === confirmPassword &&
    policyAccepted;

  const submit = async () => {
    const e = email.trim().toLowerCase();
    if (!name.trim()) {
      Alert.alert("Name required", "Please enter your name.");
      return;
    }
    if (!shop.trim()) {
      Alert.alert("Business Name required", "Please enter your business name.");
      return;
    }
    if (!e) {
      Alert.alert("Email required", "Please enter your email address.");
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
    if (!businessCategory) {
      Alert.alert(
        "Pick a category",
        "Please tell us what you sell so we can tailor the app for your business.",
      );
      return;
    }
    if (password.length < 6) {
      Alert.alert("Password too short", "Use at least 6 characters");
      return;
    }
    if (password !== confirmPassword) {
      Alert.alert(
        "Passwords don't match",
        "Please re-enter the same password in both fields.",
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
      // ── Step 1: create the account via the existing /auth/signup
      // endpoint, but DO NOT use the auth-context signUp() helper —
      // that one auto-persists the session and AuthGate would redirect
      // away before the user could enter the OTP. We use a raw
      // api.post() so the JWT is returned but stays in-memory, gated
      // behind the OTP verification below.
      let device_fingerprint = "";
      try {
        const { safeGetDeviceFingerprint } = await import("../../lib/deviceFingerprint");
        device_fingerprint = await safeGetDeviceFingerprint();
      } catch { /* ignore */ }

      await api.post("/auth/signup", {
        email: e,
        password,
        name: name.trim(),
        shop_name: shop.trim(),
        phone: phoneDigits,
        device_fingerprint,
        primary_business_category: businessCategory || "",
      });

      // ── Step 2: dispatch the WhatsApp OTP. The account now exists
      // in the DB but the client is NOT signed in yet — the user
      // must prove phone ownership before the session is minted.
      const normalised = normalisePhone(phoneDigits);
      const otpRes = await Api.requestPhoneOtp(normalised, "signup");
      setMaskedPhone(otpRes.phone || normalised);
      startCountdown(otpRes.resend_cooldown || 60);
      setOtp("");
      setOtpSent(true);
    } catch (err: any) {
      Alert.alert(
        "Signup failed",
        err?.response?.data?.detail || err?.message || "Please try again",
      );
    } finally {
      setBusy(false);
    }
  };

  // ── Step 3: verify the OTP. signInWithOtp calls /auth/otp/verify
  // which (via its last-10-digit fallback regex) locates the user we
  // just created above, returns a JWT, and persists the session.
  // AuthGate then handles the redirect to the dashboard automatically.
  const verifyAndComplete = async () => {
    setVerifyingOtp(true);
    try {
      const phoneDigits = phone.replace(/\D/g, "");
      const normalised = normalisePhone(phoneDigits);
      await signInWithOtp({
        phone: normalised,
        otp: otp.replace(/\D/g, ""),
        event_type: "signup",
      });
      // AuthGate handles redirect to /(tabs).
    } catch (err: any) {
      Alert.alert(
        "Verification failed",
        err?.response?.data?.detail || err?.message || "Wrong or expired OTP",
      );
    } finally {
      setVerifyingOtp(false);
    }
  };

  const resendOtp = async () => {
    setResending(true);
    try {
      const phoneDigits = phone.replace(/\D/g, "");
      const normalised = normalisePhone(phoneDigits);
      const otpRes = await Api.requestPhoneOtp(normalised, "signup");
      setMaskedPhone(otpRes.phone || normalised);
      startCountdown(otpRes.resend_cooldown || 60);
      Alert.alert("OTP resent", `Sent a fresh OTP to ${otpRes.phone || normalised}.`);
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err?.message || "Please try again";
      if (status === 429) Alert.alert("Please wait", detail);
      else Alert.alert("Could not resend OTP", detail);
    } finally {
      setResending(false);
    }
  };

  const otpDigits = otp.replace(/\D/g, "");
  const canVerifyOtp = otpDigits.length >= 4 && !verifyingOtp;

  return (
    <SafeAreaView style={styles.safe}>
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          {/* Phase G2 — Replaced the placeholder cube icon with the real
              Shippzo brand header so the signup screen matches the
              welcome / login visual identity. */}
          <View style={styles.brand}>
            <BrandHeaderAnimator variant="login" />
            <Text style={styles.brandSub}>
              Generate Shipping Labels in Just 30 Seconds
            </Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>
              Your name <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="signup-name"
              value={name}
              onChangeText={setName}
              placeholder="Mahek"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>
              Business Name <Text style={styles.required}>*</Text>
            </Text>
            <TextInput
              testID="signup-shop"
              value={shop}
              onChangeText={setShop}
              placeholder="Mahek Creations"
              style={styles.input}
              placeholderTextColor="#94A3B8"
            />

            <Text style={[styles.label, { marginTop: 12 }]}>
              Email <Text style={styles.required}>*</Text>
            </Text>
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

            <Text style={[styles.label, { marginTop: 12 }]}>
              Password (min 6) <Text style={styles.required}>*</Text>
            </Text>
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

            <Text style={[styles.label, { marginTop: 12 }]}>
              Confirm Password <Text style={styles.required}>*</Text>
            </Text>
            <View style={styles.pwRow}>
              <TextInput
                testID="signup-confirm-password"
                value={confirmPassword}
                onChangeText={setConfirmPassword}
                placeholder="Re-enter your password"
                secureTextEntry={!showConfirmPwd}
                style={[styles.input, { flex: 1 }]}
                placeholderTextColor="#94A3B8"
              />
              <TouchableOpacity style={styles.eyeBtn} onPress={() => setShowConfirmPwd((v) => !v)}>
                <PhIcon name={showConfirmPwd ? "eye-off" : "eye"} size={20} color="#64748B" />
              </TouchableOpacity>
            </View>
            {/* Inline mismatch hint — only when the user has typed
                something into BOTH fields and they differ. Avoids
                shouting at the user mid-typing. */}
            {confirmPassword.length > 0 && password !== confirmPassword ? (
              <Text style={styles.errorNote}>Passwords don't match.</Text>
            ) : null}

            <TouchableOpacity
              testID="signup-submit"
              disabled={busy || !formValid || otpSent}
              onPress={submit}
              style={[
                styles.primaryBtn,
                (busy || !formValid || otpSent) && { opacity: 0.5 },
              ]}
            >
              {busy
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.primaryBtnText}>
                    {otpSent ? "Account created — verify OTP below" : "Create account"}
                  </Text>}
            </TouchableOpacity>

            {/* Phase-OTP — inline OTP verification step. Only renders
                after the form has been submitted AND the backend has
                dispatched an OTP to the user's WhatsApp. Locking it
                inside the same card keeps the user oriented — no
                surprise navigation. */}
            {otpSent ? (
              <View style={styles.otpBox}>
                <View style={styles.otpHeader}>
                  <PhIcon name="chatbubble-ellipses" size={16} color="#15803D" />
                  <Text style={styles.otpHeaderTxt}>
                    OTP sent on WhatsApp to {maskedPhone}
                  </Text>
                </View>
                <TextInput
                  testID="signup-otp"
                  value={otp}
                  onChangeText={(t) => setOtp(t.replace(/\D/g, "").slice(0, 6))}
                  placeholder="6-digit code"
                  keyboardType="number-pad"
                  maxLength={6}
                  style={[styles.input, styles.otpInput]}
                  placeholderTextColor="#94A3B8"
                  autoFocus
                />
                <TouchableOpacity
                  testID="signup-verify-otp"
                  disabled={!canVerifyOtp}
                  onPress={verifyAndComplete}
                  style={[
                    styles.primaryBtn,
                    !canVerifyOtp && { opacity: 0.5 },
                    { marginTop: 12 },
                  ]}
                >
                  {verifyingOtp
                    ? <ActivityIndicator color="#fff" />
                    : <Text style={styles.primaryBtnText}>Verify &amp; Complete signup</Text>}
                </TouchableOpacity>

                <View style={styles.resendRow}>
                  {secondsLeft > 0 ? (
                    <Text style={styles.resendTimer}>
                      Resend OTP in 0:{String(secondsLeft).padStart(2, "0")}
                    </Text>
                  ) : (
                    <TouchableOpacity
                      testID="signup-resend-otp"
                      onPress={resendOtp}
                      disabled={resending}
                      style={styles.resendBtn}
                    >
                      {resending ? (
                        <ActivityIndicator size="small" color={colors.primary} />
                      ) : (
                        <>
                          <PhIcon name="arrow-clockwise" size={14} color={colors.primary} />
                          <Text style={styles.resendTxt}>Resend OTP</Text>
                        </>
                      )}
                    </TouchableOpacity>
                  )}
                </View>
              </View>
            ) : null}

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
  errorNote: {
    fontSize: 12,
    color: "#DC2626",
    marginTop: 6,
    fontWeight: "700",
  },
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

  // Phase-OTP — inline phone-verification panel that appears below
  // the "Create account" button after a successful form submit.
  otpBox: {
    marginTop: 14,
    backgroundColor: "#F0FDF4",
    borderWidth: 2,
    borderColor: "#16A34A",
    borderRadius: 12,
    padding: 14,
  },
  otpHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 10,
  },
  otpHeaderTxt: {
    flex: 1,
    color: "#15803D",
    fontWeight: "700",
    fontSize: 12.5,
  },
  otpInput: {
    letterSpacing: 6,
    fontWeight: "800",
    textAlign: "center",
    fontSize: 18,
  },
  resendRow: {
    marginTop: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  resendBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: 6,
    paddingHorizontal: 14,
  },
  resendTxt: { color: colors.primary, fontWeight: "800", fontSize: 13 },
  resendTimer: { color: "#475569", fontSize: 12, fontWeight: "700" },
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
