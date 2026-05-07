/**
 * Refund Policy + Terms of Service screen (Razorpay merchant policy).
 *
 * Razorpay's merchant onboarding requires a publicly accessible Refund
 * & Cancellation Policy as well as Terms of Service. This screen
 * surfaces both — toggled via `?tab=terms` query param.
 *
 * NOTE TO MAIN AGENT: The text below is plain-English boilerplate
 * tuned for Indian B2B SaaS. Replace the merchant name / contact
 * email with the real entity before production.
 */
import React, { useMemo } from "react";
import PhIcon from "../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { colors } from "../lib/theme";

type Tab = "refund" | "terms" | "privacy";

export default function RefundPolicyScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ tab?: string }>();
  const initial: Tab =
    params.tab === "terms" ? "terms"
    : params.tab === "privacy" ? "privacy"
    : "refund";
  const [tab, setTab] = React.useState<Tab>(initial);

  const headerRight = useMemo(
    () => () => (
      <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
        <PhIcon name="close" size={22} color={colors.text} />
      </TouchableOpacity>
    ),
    [router],
  );

  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: "Policies",
          headerRight,
          headerBackVisible: false,
          headerStyle: { backgroundColor: colors.background },
        }}
      />
      <View style={styles.tabs}>
        <TouchableOpacity
          onPress={() => setTab("refund")}
          style={[styles.tab, tab === "refund" && styles.tabActive]}
        >
          <Text style={[styles.tabTxt, tab === "refund" && styles.tabTxtActive]}>
            Refund Policy
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => setTab("terms")}
          style={[styles.tab, tab === "terms" && styles.tabActive]}
        >
          <Text style={[styles.tabTxt, tab === "terms" && styles.tabTxtActive]}>
            Terms of Service
          </Text>
        </TouchableOpacity>
        <TouchableOpacity
          onPress={() => setTab("privacy")}
          style={[styles.tab, tab === "privacy" && styles.tabActive]}
        >
          <Text style={[styles.tabTxt, tab === "privacy" && styles.tabTxtActive]}>
            Privacy
          </Text>
        </TouchableOpacity>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {tab === "refund"  ? <RefundContent /> :
         tab === "terms"   ? <TermsContent />  :
                             <PrivacyContent />}
      </ScrollView>
    </SafeAreaView>
  );
}

function RefundContent() {
  return (
    <>
      <Text style={styles.h1}>Refund & Cancellation Policy</Text>
      <Text style={styles.muted}>Effective May 2026</Text>

      <Text style={styles.h2}>1. Subscription Refunds</Text>
      <Bullet>You may cancel an active subscription at any time from the Subscription screen.</Bullet>
      <Bullet>If you cancel within <Text style={styles.b}>7 days</Text> of starting a new paid plan, email <Text style={styles.b}>shippzo.support@gmail.com</Text> for a full refund.</Bullet>
      <Bullet>After 7 days, the remaining unused days of the current billing period are non-refundable; the plan stays active until the period ends and does not auto-renew.</Bullet>

      <Text style={styles.h2}>2. Wallet / Generation Credits</Text>
      <Bullet>Wallet credits and generation rates already consumed are non-refundable.</Bullet>
      <Bullet>Unused wallet balance from a recent top-up can be refunded within <Text style={styles.b}>14 days</Text> of the top-up date if you have not yet used credits from that top-up.</Bullet>

      <Text style={styles.h2}>3. Failed or Duplicate Charges</Text>
      <Text style={styles.p}>
        If you are double-charged or the app fails to credit your wallet, contact us within
        7 days with the payment reference. We will reconcile and refund within 7 working days.
      </Text>

      <Text style={styles.h2}>4. How to Request a Refund</Text>
      <Bullet>Email <Text style={styles.b}>shippzo.support@gmail.com</Text> from the address linked to your account.</Bullet>
      <Bullet>Include order or payment ID and a brief reason.</Bullet>
      <Bullet>Refund credits return to the original payment method within 5–10 business days after approval.</Bullet>

      <Text style={styles.h2}>5. Contact</Text>
      <Text style={styles.p}>
        For any refund or billing question, write to{" "}
        <Text style={styles.b}>shippzo.support@gmail.com</Text>. We reply within one business day.
      </Text>
    </>
  );
}

function TermsContent() {
  return (
    <>
      <Text style={styles.h1}>Terms of Service</Text>
      <Text style={styles.muted}>Effective May 2026</Text>

      <Text style={styles.h2}>1. Service</Text>
      <Text style={styles.p}>
        Shippzo is a courier-label management tool that lets businesses create shipping
        labels, assist in generating and managing customer communication workflows, sync
        with Google Sheets, and receive operational analytics.
      </Text>

      <Text style={styles.h2}>2. Accounts</Text>
      <Bullet>You must be at least 18 years old or a registered business.</Bullet>
      <Bullet>You are responsible for safeguarding your login credentials.</Bullet>
      <Bullet>One paid subscription covers a single business; sharing across organisations is prohibited.</Bullet>

      <Text style={styles.h2}>3. Acceptable Use</Text>
      <Bullet>Do not send spam, fraudulent, or harassing messages to customers.</Bullet>
      <Bullet>Do not use the platform to ship illegal, hazardous, or restricted goods.</Bullet>
      <Bullet>Do not reverse-engineer the API or attempt unauthorised access to other users' data.</Bullet>
      <Bullet>You are solely responsible for compliance with applicable laws, courier regulations, and messaging platform policies.</Bullet>

      <Text style={styles.h2}>4. Subscriptions & Wallet</Text>
      <Bullet>Plans are billed in advance; auto-renewal can be cancelled anytime from the Subscription screen.</Bullet>
      <Bullet>Wallet credits used for in-app generation features are non-refundable once consumed.</Bullet>
      <Bullet>Refunds for unused subscription days follow the Refund Policy.</Bullet>
      <Bullet>We reserve the right to modify pricing, features, or plan limits at any time with reasonable notice.</Bullet>

      <Text style={styles.h2}>5. Third-Party Integrations</Text>
      <Text style={styles.p}>
        Shippzo may rely on essential third-party service providers (such as payment
        processors, cloud services, and notification services) to operate certain features.
      </Text>
      <Text style={styles.p}>
        We are not responsible for service interruptions, delays, or policy changes caused
        by these external providers.
      </Text>

      <Text style={styles.h2}>6. Intellectual Property</Text>
      <Text style={styles.p}>
        All app content, branding, code, and generated templates remain the property of
        Shippzo.
      </Text>
      <Text style={styles.p}>
        Shipment data you create remains yours. You grant Shippzo a limited, non-exclusive
        license to process and store such data solely to provide the Service.
      </Text>

      <Text style={styles.h2}>7. Limitation of Liability</Text>
      <Text style={styles.p}>
        To the fullest extent permitted by law, Shippzo is not liable for indirect,
        incidental, or consequential damages arising from your use of the Service. Our
        total liability for any claim is limited to the fees you have paid in the past 3
        months.
      </Text>
      <Text style={styles.p}>
        We do not guarantee uninterrupted or error-free operation of the Service.
      </Text>

      <Text style={styles.h2}>8. Termination</Text>
      <Text style={styles.p}>
        You may delete your account anytime by contacting{" "}
        <Text style={styles.b}>shippzo.support@gmail.com</Text>. We may suspend or
        terminate accounts with or without prior notice in cases of policy violation,
        abuse, or risk to the platform.
      </Text>

      <Text style={styles.h2}>9. Governing Law</Text>
      <Text style={styles.p}>
        These Terms are governed by the laws of India, with exclusive jurisdiction in
        Gujarat courts.
      </Text>

      <Text style={styles.h2}>10. Updates</Text>
      <Text style={styles.p}>
        We may update these Terms; material changes will be announced inside the app.
        Continued use means acceptance.
      </Text>
    </>
  );
}

function PrivacyContent() {
  return (
    <>
      <Text style={styles.h1}>Privacy Policy</Text>
      <Text style={styles.muted}>Effective May 2026</Text>

      <Text style={styles.p}>
        This Privacy Policy explains how Shippzo collects, uses, and protects your data
        when you use the Shippzo mobile application and related services.
      </Text>

      <Text style={styles.h2}>1. Information We Collect</Text>
      <Text style={styles.h3}>1.1 Account information</Text>
      <Bullet>Name, email, phone number provided at sign-up.</Bullet>
      <Bullet>Business name, sender address, GST number when added voluntarily for shipment labels.</Bullet>

      <Text style={styles.h3}>1.2 Shipment data you create</Text>
      <Bullet>Customer names, addresses, phone numbers, order amounts, courier choice, status updates.</Bullet>
      <Bullet>Photos / barcodes you scan with your camera.</Bullet>

      <Text style={styles.h3}>1.3 Device & usage data</Text>
      <Bullet>Device model, OS version, app version, crash reports.</Bullet>
      <Bullet>Push notification tokens issued by Apple / Google so we can send alerts you opt in to.</Bullet>
      <Bullet>Aggregate usage metrics for billing & analytics.</Bullet>

      <Text style={styles.h3}>1.4 How you provide shipment data</Text>
      <Text style={styles.p}>
        You may input shipment data manually, by pasting text (such as from messaging
        apps), uploading files, or connecting your Google Sheet. We do not access your
        messaging apps directly.
      </Text>

      <Text style={styles.h2}>2. How We Use Your Data</Text>
      <Bullet>To create courier labels, manage shipments, and sync with your own Google Sheet.</Bullet>
      <Bullet>To send transactional and operational notifications you have explicitly enabled.</Bullet>
      <Bullet>To support features that automatically extract or format address and shipment details from text or images you provide.</Bullet>
      <Bullet>To maintain billing, enforce subscription tier limits, and prevent abuse.</Bullet>
      <Bullet>To improve product reliability via aggregated, non-identifying analytics.</Bullet>

      <Text style={styles.h2}>3. Sharing & Third Parties</Text>
      <Text style={styles.p}>We do not sell your personal data.</Text>
      <Text style={styles.p}>
        We share data only with essential service providers required to operate the app:
      </Text>
      <Bullet>Google services — when you choose to connect your Google Sheet for data sync.</Bullet>
      <Bullet>Push notification services (Apple / Google) — to deliver notifications you opt in to.</Bullet>
      <Bullet>Payment providers — only when you make payments within the app (if applicable).</Bullet>
      <Text style={styles.p}>We share only the minimum data necessary to provide these features.</Text>

      <Text style={styles.h2}>4. Automated Processing</Text>
      <Text style={styles.p}>
        Some features use automated processing to extract and format address or shipment
        details from text or images you provide.
      </Text>
      <Text style={styles.p}>
        This processing may be performed on-device or through secure services. We do not
        use your data to train any models.
      </Text>

      <Text style={styles.h2}>5. Data Retention & Deletion</Text>
      <Bullet>Shipment records are retained for the lifetime of your account so you can audit history.</Bullet>
      <Bullet>You may delete individual shipments at any time from inside the app.</Bullet>
      <Bullet>You may request full account deletion by emailing <Text style={styles.b}>shippzo.support@gmail.com</Text>; we will permanently erase your data within 30 days, retaining only aggregate billing records as required by law.</Bullet>

      <Text style={styles.h2}>6. Permissions</Text>
      <Bullet><Text style={styles.b}>Camera</Text> — to scan addresses and barcodes; photos are used only as you direct inside the app.</Bullet>
      <Bullet><Text style={styles.b}>Photos</Text> — to attach images for label printing; shown as a system picker.</Bullet>
      <Bullet><Text style={styles.b}>Notifications</Text> — only after you tap "Allow"; you can revoke in OS settings or in-app Notifications screen.</Bullet>

      <Text style={styles.h2}>7. Children</Text>
      <Text style={styles.p}>
        Shippzo is intended for businesses and adults age 18+. We do not knowingly collect
        data from children.
      </Text>

      <Text style={styles.h2}>8. Security</Text>
      <Text style={styles.p}>
        All connections are encrypted in transit. Passwords are securely hashed. Access to
        systems handling your data is restricted to authorised personnel.
      </Text>

      <Text style={styles.h2}>9. Your Rights</Text>
      <Text style={styles.p}>
        You can access, correct, export, or delete your data anytime by emailing{" "}
        <Text style={styles.b}>shippzo.support@gmail.com</Text>. India PDP, EU GDPR, and
        California CCPA rights are honoured.
      </Text>

      <Text style={styles.h2}>10. Changes to This Policy</Text>
      <Text style={styles.p}>
        We will publish an updated effective date at the top of this page when we make
        material changes. Continued use after publication constitutes acceptance.
      </Text>
    </>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <View style={styles.bulletRow}>
      <View style={styles.bulletDot} />
      <Text style={styles.bulletTxt}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  tabs: {
    flexDirection: "row",
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 12,
    gap: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
  },
  tab: {
    flex: 1,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: "#F1F5F9",
    alignItems: "center",
  },
  tabActive: { backgroundColor: "#0F172A" },
  tabTxt: { fontSize: 12, fontWeight: "800", color: "#475569", letterSpacing: 0.4 },
  tabTxtActive: { color: "#fff" },
  content: { padding: 20, paddingBottom: 60 },
  h1: { fontSize: 22, fontWeight: "900", color: "#0F172A", marginBottom: 4 },
  h2: { fontSize: 15, fontWeight: "800", color: "#1F2937", marginTop: 18, marginBottom: 6 },
  h3: { fontSize: 13, fontWeight: "800", color: "#374151", marginTop: 10, marginBottom: 4 },
  muted: { color: "#94A3B8", fontSize: 12, marginBottom: 8 },
  p: { color: "#334155", fontSize: 13.5, lineHeight: 21, marginBottom: 4 },
  b: { fontWeight: "800", color: "#0F172A" },
  bulletRow: {
    flexDirection: "row",
    gap: 10,
    paddingLeft: 4,
    marginVertical: 3,
  },
  bulletDot: {
    width: 6, height: 6, borderRadius: 3,
    backgroundColor: "#475569", marginTop: 8,
  },
  bulletTxt: { flex: 1, color: "#334155", fontSize: 13.5, lineHeight: 21 },
});
