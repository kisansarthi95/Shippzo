import { useEffect, useRef, useState } from "react";
import { Stack, useRouter, useSegments } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { Platform, View, ActivityIndicator, Text } from "react-native";
import * as SplashScreen from "expo-splash-screen";
import { Ionicons } from "@expo/vector-icons";
import { AuthProvider, useAuth } from "../lib/auth";
import { FeatureFlagsProvider } from "../lib/feature_flags";

// Keep splash visible while we warm-up fonts
SplashScreen.preventAutoHideAsync().catch(() => {});

// Globally swallow benign network/promise rejections so the red
// "Uncaught (in promise) Error" toast doesn't spam the user.
if (typeof globalThis !== "undefined") {
  // React Native ErrorUtils-style handler
  const g: any = globalThis as any;
  try {
    const prev = g.ErrorUtils?.getGlobalHandler?.();
    g.ErrorUtils?.setGlobalHandler?.((e: any, isFatal?: boolean) => {
      const msg = String(e?.message || e);
      // Swallow network/axios errors silently
      if (/Network Error|AxiosError|timeout|Unauthorized|Request failed/i.test(msg)) {
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
      if (/Network Error|AxiosError|timeout|Unauthorized|Request failed/i.test(msg)) {
        ev.preventDefault?.();
      }
    });
  }
}

export default function RootLayout() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Preload icon fonts so screens don't jitter
        // On web, wrap in a race with a timeout to avoid fontfaceobserver hanging
        const fontLoad = Ionicons.loadFont();
        if (Platform.OS === "web") {
          await Promise.race([
            fontLoad,
            new Promise((resolve) => setTimeout(resolve, 1500)),
          ]);
        } else {
          await fontLoad;
        }
      } catch {
        // ignore – we still want the app to render
      } finally {
        if (!cancelled) setReady(true);
        SplashScreen.hideAsync().catch(() => {});
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!ready) {
    // Minimal placeholder while warming up – avoids white flashes
    return <View style={{ flex: 1, backgroundColor: "#F4F5F7" }} />;
  }

  return (
    <AuthProvider>
      <FeatureFlagsProvider>
        <SafeAreaProvider>
          <StatusBar style="dark" />
          <AuthGate>
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
    </AuthProvider>
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

  useEffect(() => {
    if (loading || oauthBusy) return;
    const inAuthGroup = segments[0] === "(auth)";
    if (!user && !inAuthGroup) {
      router.replace("/(auth)/login");
    } else if (user && inAuthGroup) {
      router.replace("/(tabs)");
    }
  }, [user, loading, oauthBusy, segments, router]);

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
