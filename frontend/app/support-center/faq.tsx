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
import React, { useMemo, useState } from "react";
import {
  LayoutAnimation,
  Linking,
  Platform,
  Pressable,
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

const APP_NAME: string = Constants.expoConfig?.name || "Shippzo";

// Enable LayoutAnimation on Android — required for the smooth
// accordion expand/collapse. Safe to call multiple times.
if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

// ─── FAQ content (curated for Shippzo) ──────────────────────────────
// 25 questions across 7 categories. Each answer is intentionally
// concise (2-4 sentences) — the goal is "answer in 5 seconds", not
// a knowledge-base novella. Long-form how-tos belong in the future
// Knowledge Base; the FAQ is for quick orientation.
type FAQItem = {
  id:       string;
  category: string;
  q:        string;
  a:        string;
};

const FAQS: FAQItem[] = [
  // ─── Getting started ──────────────────────────────────────────
  {
    id: "gs-signup",
    category: "Getting started",
    q: "How do I create my Shippzo account?",
    a: "Tap Sign up on the welcome screen and pick either email + password or WhatsApp OTP. WhatsApp OTP only needs your 10-digit mobile number — we'll send a 6-digit code on WhatsApp to verify it. Your shop name + business category is asked on the same screen so we can pre-tune the app for your business.",
  },
  {
    id: "gs-first-shipment",
    category: "Getting started",
    q: "How do I add my first shipment?",
    a: "From the Shipments tab, tap the Add button (top-right) and either fill the form manually, paste a customer message into Smart Paste, or import an Excel/CSV. Smart Paste auto-extracts name, phone, address, pincode, product, COD amount and more from messy text in 9 Indian languages.",
  },
  {
    id: "gs-import",
    category: "Getting started",
    q: "How do I import orders from Excel or Google Sheets?",
    a: "Tap Add → Import. Drag in a .xlsx/.csv file or paste a Google Sheets link. The first row should contain headers (name, phone, address, pincode, etc.) — Shippzo auto-detects the mapping for the standard column names and lets you fix any unmapped ones before committing the import.",
  },

  // ─── Shipping labels & couriers ───────────────────────────────
  {
    id: "lbl-generate",
    category: "Shipping labels & couriers",
    q: "How do I generate a shipping label?",
    a: "Open any shipment row and tap Generate label. Pick the courier from your configured list, confirm the tracking ID (auto-pulled if you added the courier's API key), and the PDF will be ready to download or print. Bulk-print up to 100 labels at once from the multi-select toolbar.",
  },
  {
    id: "lbl-courier-add",
    category: "Shipping labels & couriers",
    q: "How do I add a new courier partner?",
    a: "Settings → Couriers → Add Courier. Pick from the built-in list (Delhivery, DTDC, India Post, etc.) or add a custom one. For tracking-ID auto-fetch, paste the courier's API key in the same screen. The same courier can be reused across all shipments.",
  },
  {
    id: "lbl-tracking",
    category: "Shipping labels & couriers",
    q: "Can Shippzo auto-fetch tracking IDs from courier APIs?",
    a: "Yes — for couriers that publish a label-generation API (Delhivery, Shiprocket, Bluedart, etc.). Add your API key under Settings → Couriers and the tracking ID is auto-populated when you tap Generate Label. For couriers without an API, paste the tracking ID manually.",
  },
  {
    id: "lbl-bulk",
    category: "Shipping labels & couriers",
    q: "How do I print labels in bulk?",
    a: "Switch the Shipments tab into Multi-Select mode (top-right toggle), pick the rows you want, then tap Bulk Download from the toolbar. The labels are stitched into a single multi-page PDF in the order you selected.",
  },

  // ─── Wallet, plans & payments ─────────────────────────────────
  {
    id: "pay-recharge",
    category: "Wallet, plans & payments",
    q: "How do I recharge my wallet?",
    a: "Profile → Wallet → Recharge. Pick an amount (or enter a custom amount) and pay via UPI / cards / netbanking through Razorpay. The credit reflects in your wallet within a few seconds. Your transaction history shows every recharge and deduction.",
  },
  {
    id: "pay-plan",
    category: "Wallet, plans & payments",
    q: "What's the difference between the Free, Starter, and Pro plans?",
    a: "Free is for 1 user with up to 25 shipments per month and basic features. Starter unlocks WhatsApp templates, bulk import, and 500 shipments. Pro adds Smart Paste AI, multi-user accounts, advanced exports, and unlimited shipments. See Profile → Plans for the full feature matrix.",
  },
  {
    id: "pay-deduction",
    category: "Wallet, plans & payments",
    q: "When does Shippzo deduct credits from my wallet?",
    a: "Credits are only deducted when you actually generate a shipping label or send a WhatsApp message — never for adding a shipment, importing data, or viewing reports. Every deduction is logged in Profile → Wallet → History so you can verify it line by line.",
  },
  {
    id: "pay-refund",
    category: "Wallet, plans & payments",
    q: "Can I get a refund for unused wallet credit?",
    a: "Wallet credits are non-refundable once recharged, but they never expire and can be used for any chargeable action in the app. For special cases please contact support via Support Center → Create Request.",
  },

  // ─── WhatsApp & messaging ─────────────────────────────────────
  {
    id: "wa-connect",
    category: "WhatsApp & messaging",
    q: "How do I connect WhatsApp to send order updates?",
    a: "Settings → WhatsApp Message Templates. WhatsApp messaging works out-of-the-box via our hosted provider (no separate WhatsApp Business API key needed). Pick your default language (Gujarati / Hindi / English) and customize the 4 standard templates: Booked, Dispatched, Delivered, Thanks.",
  },
  {
    id: "wa-otp",
    category: "WhatsApp & messaging",
    q: "Why am I getting login OTPs on WhatsApp?",
    a: "WhatsApp is one of the supported login channels — you can opt in by signing in with your mobile number on the Login screen. OTPs expire in 10 minutes; up to 5 OTP resends are allowed per 30-minute window before a temporary lockout kicks in.",
  },
  {
    id: "wa-pack",
    category: "WhatsApp & messaging",
    q: "How do I send a packing list to my staff via WhatsApp?",
    a: "Shipments tab → Multi-Select → pick rows → tap the green WhatsApp icon. Choose Gujarati / Hindi / English in the language popup; the packing summary opens with Copy / WhatsApp / Share actions. Default language is set in Settings → Packing Language.",
  },

  // ─── Smart Paste & AI ─────────────────────────────────────────
  {
    id: "ai-paste",
    category: "Smart Paste & AI",
    q: "What is Smart Paste and how does it work?",
    a: "Smart Paste turns a raw customer message (WhatsApp text, email body, screenshot OCR) into a fully-filled shipment form. It extracts name, phone, address, pincode, product, COD amount, even tokens — across 9 Indian scripts (Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam, Punjabi) plus English. Paste, review the highlighted fields, then save.",
  },
  {
    id: "ai-langs",
    category: "Smart Paste & AI",
    q: "Which Indian languages does Smart Paste understand?",
    a: "Hindi, Gujarati, Marathi, Bengali, Tamil, Telugu, Kannada, Malayalam and Punjabi — plus mixed Hinglish/Gujlish text where addresses and city names are typed in Roman script. Currency and quantity keywords are recognised in every supported script.",
  },
  {
    id: "ai-pincode",
    category: "Smart Paste & AI",
    q: "Why does the city change after I paste an address?",
    a: "Whenever a 6-digit pincode is detected, Shippzo cross-checks it with the official India Post database and overrides the city to the canonical district name. This avoids spelling typos (e.g. \"Ahemedabad\" → \"Ahmedabad\") that would otherwise reject the label at the courier's end.",
  },

  // ─── Reports & data ───────────────────────────────────────────
  {
    id: "rep-csv",
    category: "Reports & data",
    q: "How do I export my shipments to Excel/CSV?",
    a: "Shipments tab → tap the Bulk Download icon (top-right). The CSV honours any filters currently applied (status, date range, courier, etc.) and includes 44 columns including customer details, tracking, dispatch dates, COD amounts and payment status. Multi-select first if you only want specific rows.",
  },
  {
    id: "rep-sheet",
    category: "Reports & data",
    q: "How do I connect a Google Sheet for live sync?",
    a: "Settings → Sheets → Connect. Share your sheet with the service-account email shown on screen and paste the sheet URL. Shipments are mirrored to your sheet in near-real-time — status changes flow back so your team can view dispatches without opening the app.",
  },
  {
    id: "rep-privacy",
    category: "Reports & data",
    q: "Can other Shippzo users see my shipments?",
    a: "No. Every shop has a fully isolated dataset — your shipments, customers, couriers, wallet, settings and reports are scoped to your account. Even Shippzo admin staff need an explicit support-ticket trail before they can view your data.",
  },

  // ─── Account & troubleshooting ────────────────────────────────
  {
    id: "ac-team",
    category: "Account & troubleshooting",
    q: "How do I invite team members?",
    a: "Available on Starter and Pro plans. Settings → Team → Invite. Send an email/phone invite; the invitee logs in via Email + Password or WhatsApp OTP. Roles are configurable: Owner (full access), Staff (no billing), or Read-only.",
  },
  {
    id: "ac-pwd",
    category: "Account & troubleshooting",
    q: "I forgot my password — what now?",
    a: "Tap \"Forgot password?\" on the Login screen. We'll email a reset link valid for 30 minutes. Alternatively, log in with your registered mobile number via WhatsApp OTP and set a new password from Settings → Account.",
  },
  {
    id: "ac-logout",
    category: "Account & troubleshooting",
    q: "How do I log out of all devices?",
    a: "Settings → Account → Log out everywhere. This invalidates every JWT we've issued for your account; the next login on any device starts a fresh session. Useful if you suspect a lost phone or shared device.",
  },
  {
    id: "ac-delete",
    category: "Account & troubleshooting",
    q: "Can I delete my account and data?",
    a: "Yes — Support Center → Create Request and pick \"Account deletion\". We confirm the request with you, export any data you want, then erase your shop's records within 7 working days as required by our privacy policy.",
  },
  {
    id: "ac-bug",
    category: "Account & troubleshooting",
    q: "Something looks broken — how do I report a bug?",
    a: "Support Center → Create Request → pick the \"Bug / Issue\" category. Attach a screenshot or a screen recording (the form supports both) — that gets us 90% of the way to a fix on the first reply. Most reports are triaged within 24 hours.",
  },
];

// All unique categories in declaration order — used for the colour
// chip on each card. Keeping the array small and hand-tuned lets us
// pick a coherent palette below.
const CATEGORIES = Array.from(
  FAQS.reduce((set, f) => set.add(f.category), new Set<string>()),
);

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
  const [query, setQuery]   = useState<string>("");
  const [openId, setOpenId] = useState<string | null>(null);

  // Filter by question OR answer text — gives the user a single
  // search box that surfaces relevant rows regardless of which side
  // matched (e.g. searching "Razorpay" only matches answers).
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return FAQS;
    return FAQS.filter(
      (f) =>
        f.q.toLowerCase().includes(q) ||
        f.a.toLowerCase().includes(q) ||
        f.category.toLowerCase().includes(q),
    );
  }, [query]);

  const toggle = (id: string) => {
    LayoutAnimation.configureNext(
      LayoutAnimation.create(180, "easeInEaseOut", "opacity"),
    );
    setOpenId((prev) => (prev === id ? null : id));
  };

  // Index → colour helper. New categories beyond the palette length
  // fall back to a neutral slate so we never blow up at runtime.
  const chipFor = (cat: string) => {
    const i = CATEGORIES.indexOf(cat);
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
            {filtered.length} of {FAQS.length} questions
          </Text>
        </View>

        {/* ── Q&A accordion list ───────────────────────────────── */}
        <View style={styles.list}>
          {filtered.length === 0 ? (
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
