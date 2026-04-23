import { useEffect, useState } from "react";
import { Stack, useRouter, useSegments } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";
import { Platform, View, ActivityIndicator } from "react-native";
import * as SplashScreen from "expo-splash-screen";
import { Ionicons } from "@expo/vector-icons";
import { AuthProvider, useAuth } from "../lib/auth";

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
          </Stack>
        </AuthGate>
      </SafeAreaProvider>
    </AuthProvider>
  );
}

/** Redirect between (auth) and (tabs) based on login state. */
function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const segments = useSegments();
  const router = useRouter();

  useEffect(() => {
    if (loading) return;
    const inAuthGroup = segments[0] === "(auth)";
    if (!user && !inAuthGroup) {
      router.replace("/(auth)/login");
    } else if (user && inAuthGroup) {
      router.replace("/(tabs)");
    }
  }, [user, loading, segments, router]);

  if (loading) {
    return (
      <View style={{ flex: 1, justifyContent: "center", alignItems: "center", backgroundColor: "#F4F5F7" }}>
        <ActivityIndicator color="#111" />
      </View>
    );
  }
  return <>{children}</>;
}
