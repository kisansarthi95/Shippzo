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

  // Phase G — onboarding category gate. After login the gate fetches
  // /api/auth/context once. If `needs_onboarding_category` is true we
  // bounce the user to the mandatory "What do you sell?" picker. The
  // flag is fully owner-only on the backend (team members are exempt
  // and inherit the parent's category), so this single check is safe.
  const [needsOnboarding, setNeedsOnboarding] = useState<boolean | null>(null);
  useEffect(() => {
    if (loading || !user) {
      setNeedsOnboarding(null);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const ctx = await Api.authContext();
        if (alive) setNeedsOnboarding(!!ctx.needs_onboarding_category);
      } catch {
        if (alive) setNeedsOnboarding(false);   // fail-open
      }
    })();
    return () => { alive = false; };
  }, [user, loading]);

  useEffect(() => {
    if (loading || oauthBusy) return;
    const inAuthGroup = segments[0] === "(auth)";
    const inOnboarding = segments[0] === "onboarding";
    // 2026-04-30 — /refund-policy is the canonical public legal screen
    // (Terms / Refund / Privacy). Reachable without login so new users
    // can review it from the signup checkbox before creating an account.
    const publicRoutes = new Set(["refund-policy"]);
    const isPublic = publicRoutes.has(String(segments[0] || ""));
    if (!user && !inAuthGroup && !isPublic) {
      router.replace("/(auth)/login");
    } else if (user && inAuthGroup) {
      router.replace("/(tabs)");
    } else if (user && needsOnboarding === true && !inOnboarding) {
      // Owner missing primary_business_category → mandatory picker.
      router.replace("/onboarding/business-category");
    }
  }, [user, loading, oauthBusy, segments, router, needsOnboarding]);

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
  return <>{children}</>;
}
