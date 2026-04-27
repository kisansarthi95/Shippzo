/**
 * Razorpay Checkout screen (Phase-4c / 4d).
 *
 * Supports TWO modes via query params:
 *   • mode=wallet  (default) — query: amount=<INR>
 *       Tops up the user's credit wallet by the given INR amount.
 *   • mode=plan — query: plan=<plan_key>&cycle=<monthly|yearly>
 *       Buys a plan subscription. plan_key ∈ silver | gold | platinum.
 *
 * Flow:
 *   1. Compute mode from params.
 *   2. Hit the appropriate /create-order endpoint.
 *   3. Render a WebView pointing at an inline HTML page that loads
 *      Razorpay's Checkout JS and opens it with the order details.
 *   4. WebView posts the result back to RN via
 *      `window.ReactNativeWebView.postMessage(JSON.stringify({...}))`.
 *   5. On success → call the matching /verify, show a success alert,
 *      route back. On failure / cancel → friendly alert + back.
 *
 * Why WebView instead of `react-native-razorpay`? The SDK requires a
 * native module which Expo Go doesn't ship. WebView works in Go and
 * in production builds without any native config.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, ActivityIndicator, StyleSheet, Alert, TouchableOpacity, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";
import { Api, PlanKey } from "../lib/api";
import { colors } from "../lib/theme";
import { useAuth } from "../lib/auth";

type CheckoutMode = "wallet" | "plan";

type WalletOrder = Awaited<ReturnType<typeof Api.rzpCreateOrder>>;
type PlanOrder   = Awaited<ReturnType<typeof Api.rzpCreatePlanOrder>>;

type AnyOrder =
  | (WalletOrder & { mode: "wallet" })
  | (PlanOrder   & { mode: "plan" });

export default function CheckoutScreen() {
  const params = useLocalSearchParams<{
    amount?: string;
    mode?: string;
    plan?: string;
    cycle?: string;
  }>();
  const router = useRouter();
  const { refresh } = useAuth();

  const mode: CheckoutMode = (params.mode === "plan" ? "plan" : "wallet");
  const amount = useMemo(
    () => Math.max(0, Number(params.amount || 0)),
    [params.amount],
  );
  const planKey = (params.plan || "") as PlanKey;
  const cycle = (params.cycle === "yearly" ? "yearly" : "monthly") as "monthly" | "yearly";

  const [order, setOrder] = useState<AnyOrder | null>(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handledRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (mode === "wallet") {
          if (!amount || amount < 10) throw new Error("Amount must be at least ₹10");
          const r = await Api.rzpCreateOrder(amount);
          if (cancelled) return;
          setOrder({ ...r, mode: "wallet" });
        } else {
          if (!planKey || !["silver", "gold", "platinum"].includes(planKey)) {
            throw new Error("Invalid plan selected");
          }
          const r = await Api.rzpCreatePlanOrder(planKey, cycle);
          if (cancelled) return;
          setOrder({ ...r, mode: "plan" });
        }
      } catch (e: any) {
        setError(e?.response?.data?.detail || e?.message || "Could not start payment");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [mode, amount, planKey, cycle]);

  const html = useMemo(() => {
    if (!order) return "";
    const description =
      order.mode === "wallet"
        ? `${order.credits_to_grant} credits top-up`
        : `${order.plan_name} ${order.billing_cycle === "yearly" ? "Yearly" : "Monthly"} subscription`;
    const payload = {
      key: order.key_id,
      order_id: order.order_id,
      amount: order.amount_paise,
      currency: order.currency,
      name: "Courier Manager",
      description,
      prefill: {
        name: order.user_name || "",
        email: order.user_email || "",
      },
      theme: { color: "#FF5A00" },
    };
    return `<!doctype html>
<html><head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pay</title>
  <style>
    html,body{margin:0;padding:0;background:#0F172A;color:#fff;
              font-family:-apple-system,sans-serif;}
    .wrap{min-height:100vh;display:flex;align-items:center;
          justify-content:center;flex-direction:column;gap:14px;
          padding:20px;text-align:center;}
    .pill{background:#FF5A00;padding:12px 24px;border-radius:999px;
          color:#fff;font-weight:800;font-size:16px;border:none;}
    .small{color:#94A3B8;font-size:13px;max-width:280px;line-height:1.4;}
  </style>
</head><body>
  <div class="wrap">
    <div style="font-size:46px">💳</div>
    <div style="font-weight:800;font-size:18px">Razorpay Checkout</div>
    <div class="small">If the payment popup doesn't appear automatically, tap the button below.</div>
    <button class="pill" onclick="openCheckout()">Open payment</button>
  </div>
  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <script>
    var data = ${JSON.stringify(payload)};
    function send(o){
      try { window.ReactNativeWebView.postMessage(JSON.stringify(o)); }
      catch(e) { console.log("post fail", e); }
    }
    function openCheckout(){
      var opts = Object.assign({}, data, {
        handler: function(resp){
          send({ type: "success",
                 razorpay_order_id: resp.razorpay_order_id,
                 razorpay_payment_id: resp.razorpay_payment_id,
                 razorpay_signature: resp.razorpay_signature });
        },
        modal: { ondismiss: function(){ send({ type: "dismissed" }); } }
      });
      var rzp = new Razorpay(opts);
      rzp.on("payment.failed", function(resp){
        send({ type: "failure", error: resp.error });
      });
      rzp.open();
    }
    // Auto-open after a short delay so the user sees the loading screen first.
    setTimeout(openCheckout, 350);
  </script>
</body></html>`;
  }, [order]);

  const onMessage = async (evt: any) => {
    if (handledRef.current) return;
    let msg: any;
    try { msg = JSON.parse(evt.nativeEvent.data); } catch { return; }
    if (!msg?.type) return;

    if (msg.type === "dismissed") {
      handledRef.current = true;
      Alert.alert(
        "Payment cancelled",
        order?.mode === "plan"
          ? "You can try the upgrade again anytime from the Plans screen."
          : "You can try again anytime from your wallet.",
      );
      router.back();
      return;
    }
    if (msg.type === "failure") {
      handledRef.current = true;
      const desc = msg.error?.description || "Payment failed";
      Alert.alert("Payment failed", desc);
      router.back();
      return;
    }
    if (msg.type === "success" && msg.razorpay_payment_id) {
      handledRef.current = true;
      try {
        setVerifying(true);
        if (order?.mode === "plan") {
          const v = await Api.rzpVerifyPlan(
            msg.razorpay_order_id,
            msg.razorpay_payment_id,
            msg.razorpay_signature,
          );
          await refresh().catch(() => {});
          const expiry = v.plan_expires_at
            ? new Date(v.plan_expires_at).toLocaleDateString("en-IN", {
                year: "numeric", month: "short", day: "numeric",
              })
            : "—";
          const planLabel = String(v.plan).charAt(0).toUpperCase() + String(v.plan).slice(1);
          const cycleLabel = v.billing_cycle === "yearly" ? "Yearly" : "Monthly";
          Alert.alert(
            "Subscription active 🎉",
            `Welcome to ${planLabel} (${cycleLabel}). Valid until ${expiry}.`,
          );
          router.back();
        } else {
          const v = await Api.rzpVerify(
            msg.razorpay_order_id,
            msg.razorpay_payment_id,
            msg.razorpay_signature,
          );
          Alert.alert(
            "Payment successful 🎉",
            `${v.credits_added} credits added. New balance: ${v.balance.toFixed(2)} cr.`,
          );
          router.back();
        }
      } catch (e: any) {
        Alert.alert(
          "Verification failed",
          e?.response?.data?.detail || e?.message ||
            "Your payment was captured but we couldn't verify it. We'll retry via webhook — please refresh the app shortly.",
        );
        router.back();
      } finally {
        setVerifying(false);
      }
    }
  };

  // Loading state
  if (loading || verifying || !order) {
    return (
      <SafeAreaView edges={["top"]} style={styles.safe}>
        <Stack.Screen options={{ title: "Payment", headerStyle: { backgroundColor: colors.background } }} />
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.loadingTxt}>
            {verifying ? "Verifying payment…" : loading ? "Preparing checkout…" : ""}
          </Text>
        </View>
      </SafeAreaView>
    );
  }

  // Error state
  if (error) {
    return (
      <SafeAreaView edges={["top"]} style={styles.safe}>
        <Stack.Screen options={{ title: "Payment", headerStyle: { backgroundColor: colors.background } }} />
        <View style={styles.center}>
          <Ionicons name="alert-circle" size={48} color="#DC2626" />
          <Text style={styles.errTxt}>{error}</Text>
          <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
            <Text style={styles.backBtnTxt}>Go back</Text>
          </TouchableOpacity>
        </View>
      </SafeAreaView>
    );
  }

  // Header title reflects mode
  const headerTitle =
    order.mode === "plan"
      ? `Pay for ${order.plan_name}`
      : "Razorpay Payment";

  // WebView happy path
  return (
    <SafeAreaView edges={["top"]} style={styles.safe}>
      <Stack.Screen
        options={{
          title: headerTitle,
          headerStyle: { backgroundColor: colors.background },
          headerRight: () => (
            <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          ),
          headerBackVisible: false,
        }}
      />
      <WebView
        originWhitelist={["*"]}
        source={{
          html,
          baseUrl: Platform.OS === "android"
            ? "https://checkout.razorpay.com"
            : undefined,
        }}
        onMessage={onMessage}
        javaScriptEnabled
        domStorageEnabled
        thirdPartyCookiesEnabled
        sharedCookiesEnabled
        startInLoadingState
        renderLoading={() => (
          <View style={styles.center}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.loadingTxt}>Loading Razorpay…</Text>
          </View>
        )}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  loadingTxt: { color: colors.textMuted, fontSize: 13, fontWeight: "600" },
  errTxt: { color: colors.text, fontSize: 14, textAlign: "center", fontWeight: "600" },
  backBtn: {
    backgroundColor: colors.primary, paddingHorizontal: 18, paddingVertical: 10,
    borderRadius: 10, marginTop: 12,
  },
  backBtnTxt: { color: "#fff", fontWeight: "800" },
});
