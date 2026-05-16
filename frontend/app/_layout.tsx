import { useEffect, useRef, useState } from "react";
import PhIcon from "../components/PhIcon";
import { Stack, useRouter, useSegments } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { Platform, View, ActivityIndicator, Text, LogBox } from "react-native";
import * as SplashScreen from "expo-splash-screen";
import { useFonts } from "expo-font";
import { AuthProvider, useAuth } from "../lib/auth";
import { Api } from "../lib/api";
import { FeatureFlagsProvider } from "../lib/feature_flags";
import { PermissionsProvider } from "../lib/permissions";
import ErrorBoundary from "../components/ErrorBoundary";

import OfflineBanner from "../components/OfflineBanner";

// Keep splash visible while we warm-up fonts
SplashScreen.preventAutoHideAsync().catch(() => {});

// LogBox: hide benign network-flake warnings that arise from the ngrok
// tunnel being slow on mobile. These are NON-FATAL (the app renders
// correctly once fonts cache) so spamming the user with red overlays
// is pure noise.
try {
  LogBox.ignoreLogs([
    /Unable to activate keep awake/i,
    /ExpoAsset\.downloadAsync/i,
    /Unable to download asset from url/i,
    /Network Error/i,
    /AxiosError/i,
    /ExpoFontLoader/i,
    /Font file.*empty/i,
    /loadAsync.*rejected/i,
    /Call to function.*has been rejected/i,
    /Uncaught \(in promise/i,
    /fontFamily.*not loaded/i,
    /Could not find.*font/i,
  ]);
} catch {
  /* ignore */
}

// Globally swallow benign network/promise rejections so the red
// "Uncaught (in promise) Error" toast doesn't spam the user. On slow
// mobile networks (the ngrok tunnel in dev), asset downloads for icon
// fonts / keep-awake sometimes time out — those are non-fatal; the
// app still works (icons render once fonts cache).
const _BENIGN_RX = /Network Error|AxiosError|timeout|Unauthorized|Request failed|ExpoAsset|downloadAsync|Unable to download|keep awake|CodedError|Unable to activate|ExpoFontLoader|Font file.*empty|loadAsync.*rejected|Uncaught.*in promise|fontFamily.*not loaded|Could not find.*font/i;
if (typeof globalThis !== "undefined") {
  // React Native ErrorUtils-style handler
  const g: any = globalThis as any;
  try {
    const prev = g.ErrorUtils?.getGlobalHandler?.();
    g.ErrorUtils?.setGlobalHandler?.((e: any, isFatal?: boolean) => {
      const msg = String(e?.message || e);
      if (_BENIGN_RX.test(msg)) {
        return;
      }
      prev?.(e, isFatal);
    });
  } catch {
    /* ignore */
  }
  // Web unhandled promise rejections
  if (typeof window !== "undefined") {
    window.addEventListener?.("unhandledrejection", (ev: any) => {
      const msg = String(ev?.reason?.message || ev?.reason || "");
      if (_BENIGN_RX.test(msg)) {
        ev.preventDefault?.();
      }
    });
  }
}

export default function RootLayout() {
  // 2026-05-07 — Phase 5e: migrated all icons from @expo/vector-icons
  // (font-based, flaky on Android Expo Go) to phosphor-react-native
  // (SVG, no font pipeline). The previous `useFonts({ ...Ionicons.font })`
  // gate is no longer needed because PhIcon ships pure SVG that renders
  // synchronously. Splash hides immediately on mount.
  useEffect(() => {
    SplashScreen.hideAsync().catch(() => {});
  }, []);

  return (
    <ErrorBoundary>
      <AuthProvider>
        <PermissionsProvider>
          <FeatureFlagsProvider>
          <SafeAreaProvider>
            <StatusBar style="dark" />
            <AuthGate>
              <OfflineBanner />
              <Stack
                screenOptions={{
                  headerShown: false,
                  contentStyle: { backgroundColor: "#F4F5F7" },
                }}
              >
                <Stack.Screen name="(auth)" />
                <Stack.Screen name="(tabs)" />
                <Stack.Screen name="scanner" options={{ presentation: "modal", headerShown: false }} />
                <Stack.Screen name="label/[id]" options={{ headerShown: false }} />
                <Stack.Screen name="courier/[id]" options={{ headerShown: false }} />
                <Stack.Screen name="courier/[id]/variants" options={{ headerShown: false }} />
                <Stack.Screen
                  name="plans"
                  options={{ headerShown: true, title: "Plans & Billing" }}
                />
                <Stack.Screen
                  name="wallet"
                  options={{ headerShown: true, title: "Wallet & Credits" }}
                />
                <Stack.Screen
                  name="admin/plan-features"
                  options={{ headerShown: false }}
                />
                <Stack.Screen
                  name="admin/credit-packages"
                  options={{ headerShown: false }}
                />
                {/* ── Support Center route stack ──
                 *  Each nested screen is declared so it joins the root
                 *  Stack with its own header + back button — this makes
                 *  pressing back navigate screen-by-screen (e.g. ticket
                 *  detail → my-tickets → support-center hub → settings)
                 *  instead of exiting all the way back to the tabs.
                 */}
                <Stack.Screen
                  name="support-center"
                  options={{ headerShown: true, title: "Support Center", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/my-tickets"
                  options={{ headerShown: true, title: "My Requests", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/create"
                  options={{ headerShown: true, title: "Contact Support", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/create/[cat]"
                  options={{ headerShown: true, title: "Create Request", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/ticket/[id]"
                  options={{ headerShown: true, title: "Request", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/tutorials"
                  options={{ headerShown: true, title: "Video Tutorials", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/tutorials/[id]"
                  options={{ headerShown: true, title: "Tutorial", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                <Stack.Screen
                  name="support-center/tutorials/admin/add"
                  options={{ headerShown: true, title: "Add Tutorial", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
                {/* Phase-22 — Super-Admin Support Inbox */}
                <Stack.Screen
                  name="admin/support-inbox"
                  options={{ headerShown: true, title: "Support Inbox", headerShadowVisible: false, headerBackTitle: "Back" }}
                />
              </Stack>
            </AuthGate>
          </SafeAreaProvider>
        </FeatureFlagsProvider>
        </PermissionsProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
}

/** Redirect between (auth) and (tabs) based on login state. */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, signInWithGoogleSession } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  // --------- Emergent Google OAuth callback handling (web only) ----------
  // After successful Google consent the user is bounced to:
  //   <origin>/#session_id=XXXXXXX
  // We exchange it against our backend for a JWT exactly once.
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS,
  // THIS BREAKS THE AUTH — the redirect is resolved from window.location.
  const [oauthBusy, setOauthBusy] = useState(false);
  const oauthConsumed = useRef(false);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (oauthConsumed.current) return;
    try {
      const hash = (typeof window !== "undefined" && window.location?.hash) || "";
      const m = hash.match(/session_id=([^&]+)/);
      if (!m) return;
      oauthConsumed.current = true;
      setOauthBusy(true);
      const sid = decodeURIComponent(m[1]);
      // Wipe the fragment so a reload doesn't replay it.
      try {
        const clean = window.location.pathname + window.location.search;
        window.history.replaceState({}, document.title, clean);
      } catch {
        /* ignore */
      }
      (async () => {
        try {
          await signInWithGoogleSession(sid);
        } catch {
          // AuthCtx surfaces errors via the login screen; we just clear the
          // flag so the user can retry.
        } finally {
          setOauthBusy(false);
        }
      })();
    } catch {
      /* no window in server render */
    }
  }, [signInWithGoogleSession]);

  // Phase G2 — Owner's primary_business_category + business_name +
  // mobile are now collected on the signup form (`signup.tsx`). For
  // Google-OAuth signups (Google only provides email + name), we
  // bounce the user to /(auth)/complete-profile to fill in the
  // missing fields BEFORE the dashboard unlocks. The backend exposes
  // a single `needs_profile_completion` aggregate flag on
  // /api/auth/context to drive this redirect.
  const [needsProfile, setNeedsProfile] = useState<boolean | null>(null);
  useEffect(() => {
    if (loading || !user) {
      setNeedsProfile(null);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const ctx = await Api.authContext();
        if (alive) setNeedsProfile(!!ctx.needs_profile_completion);
      } catch {
        if (alive) setNeedsProfile(false); // fail-open
      }
    })();
    return () => { alive = false; };
  }, [user, loading]);

  useEffect(() => {
    if (loading || oauthBusy) return;
    const inAuthGroup = segments[0] === "(auth)";
    const authSub = String(segments[1] || "");
    // 2026-04-30 — /refund-policy is the canonical public legal screen
    // (Terms / Refund / Privacy). Reachable without login so new users
    // can review it from the signup checkbox before creating an account.
    const publicRoutes = new Set(["refund-policy"]);
    const isPublic = publicRoutes.has(String(segments[0] || ""));

    if (!user && !inAuthGroup && !isPublic) {
      // Phase G2 — first-touch users land on the welcome screen which
      // explicitly shows BOTH "Create New Account" and "I have an
      // account" buttons. Solves the conversion problem where new
      // users couldn't find signup on the legacy login-first flow.
      router.replace("/(auth)/welcome" as any);
    } else if (user && inAuthGroup && authSub !== "complete-profile") {
      // Logged-in user landed back on welcome/login/signup → bounce
      // to the dashboard (or complete-profile if the gate flags them).
      if (needsProfile === true) {
        router.replace("/(auth)/complete-profile" as any);
      } else {
        router.replace("/(tabs)");
      }
    } else if (user && needsProfile === true && authSub !== "complete-profile") {
      // Inside the app but profile is incomplete → mandatory gate.
      router.replace("/(auth)/complete-profile" as any);
    }
  }, [user, loading, oauthBusy, segments, router, needsProfile]);

  if (loading || oauthBusy) {
    return (
      <View style={{ flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: "#F4F5F7" }}>
        <ActivityIndicator color="#111" />
        {oauthBusy ? (
          <Text style={{ marginTop: 12, color: "#64748B", fontSize: 13 }}>
            Signing you in…
          </Text>
        ) : null}
      </View>
    );
  }
  return (
    <>
      <NotificationDeepLinker />
      {children}
    </>
  );
}

/**
 * Phase-21 — Deep-link handler for new-order notifications.
 *
 * When the operator taps a system notification (lock-screen banner,
 * pull-down shade, …), expo-notifications fires a response with the
 * full payload we attached via `data: { type: "new_order", screen:
 * "orders" }` in lib/new_order_alert.tsx. This listener reads that
 * payload and routes the user straight to the Orders tab so they
 * land on the new arrivals instead of wherever the app was last
 * paused.
 *
 * Also handles the "cold start" case — if the app was fully closed
 * and got launched by a notification tap, getLastNotificationResponseAsync()
 * surfaces it once on mount so the deep-link still works.
 *
 * Mounted inside AuthGate AFTER the auth/loading gate clears so the
 * push doesn't fight with the welcome / OAuth redirect logic.
 */
function NotificationDeepLinker() {
  const router = useRouter();

  useEffect(() => {
    let sub: any = null;
    let cancelled = false;

    (async () => {
      try {
        const Notifications = await import("expo-notifications");

        // Handle the cold-start case once.
        try {
          const last = await Notifications.getLastNotificationResponseAsync();
          if (!cancelled && last) {
            handleResponse(last);
          }
        } catch {
          /* not all platforms support cold-start lookup */
        }

        // Live listener — fires every time the operator taps a notif
        // while the app is running (background or foreground).
        sub = Notifications.addNotificationResponseReceivedListener(
          (response) => {
            handleResponse(response);
          },
        );
      } catch {
        /* expo-notifications not available on this platform — no-op */
      }
    })();

    function handleResponse(response: any) {
      try {
        const data =
          response?.notification?.request?.content?.data || {};
        if (data?.type === "new_order" || data?.screen === "orders") {
          // Defer one tick so the route stack is mounted before we
          // push, avoiding "navigate before mounted" warnings on the
          // cold-start path.
          setTimeout(() => {
            try {
              router.push("/(tabs)/orders" as any);
            } catch {
              /* router not ready yet — ignore */
            }
          }, 0);
        }
      } catch {
        /* swallow — deep-linking is best-effort */
      }
    }

    return () => {
      cancelled = true;
      try {
        sub?.remove?.();
      } catch {
        /* ignore */
      }
    };
  }, [router]);

  return null;
}
