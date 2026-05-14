/**
 * Phase-21 — New-Order Alert hook + in-app banner.
 *
 * Polls /orders/pending-count every 30 s. When the count goes UP since
 * the previous poll, it:
 *   1. Triggers a happy haptic burst (success notification feel).
 *   2. Fires a local expo-notifications push so the mobile screen lights
 *      up with the system's default chime — even when the app is in the
 *      background, the OS plays the sound + shows the banner.
 *   3. Shows an in-app slide-down banner inside the Provider so the
 *      operator notices instantly when the app IS foregrounded.
 *
 * Plan-gated:
 *   - `new_order_sound` — controls the audible chime + local notification.
 *     When unticked, the banner still appears (visual only).
 *
 * Mounted once globally from app/(tabs)/_layout.tsx via <NewOrderAlertProvider>.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Animated,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import * as Haptics from "expo-haptics";
import { useRouter } from "expo-router";
import { Api } from "./api";
import { useAuth } from "./auth";
import { useFeatureFlag } from "./feature_flags";
import { colors } from "./theme";
import PhIcon from "../components/PhIcon";

// ------------------------- Local notification helper -------------------
// Lazy-loaded so the (heavy) expo-notifications module isn't pulled in
// on app start unless the user actually has the feature flag on.
async function fireLocalNewOrderNotification(delta: number) {
  try {
    const Notifications = await import("expo-notifications");
    // Foreground handler: by default iOS suppresses the OS banner while
    // the app is active. We register a handler that ALWAYS shows the
    // banner + plays the sound so the operator hears the chime even
    // when their phone is unlocked and Shippzo is open.
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowAlert: true,
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: true,
        shouldSetBadge: false,
      }),
    });
    await Notifications.scheduleNotificationAsync({
      content: {
        title: "🎉 New order!",
        body:
          delta === 1
            ? "1 fresh order just arrived. Let's ship it!"
            : `${delta} fresh orders just arrived.`,
        sound: "default",
        // Phase-21 — Deep-link payload. Read by the root layout's
        // addNotificationResponseReceivedListener so a tap from the
        // mobile lock-screen / banner takes the operator straight to
        // the Orders tab where the new arrivals are visible.
        data: { type: "new_order", screen: "orders" },
      },
      trigger: null, // immediate
    });
  } catch {
    // expo-notifications may not be available on web / unsupported
    // platforms — silent fallback, the in-app banner still shows.
  }
}

// ------------------------- Provider + Context --------------------------
type AlertState = {
  visible: boolean;
  delta: number;
};

const Ctx = createContext<{ dismiss: () => void }>({ dismiss: () => {} });

export function NewOrderAlertProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const soundEnabled = useFeatureFlag("new_order_sound");

  const lastCountRef = useRef<number | null>(null);
  const [alert, setAlert] = useState<AlertState>({ visible: false, delta: 0 });
  const translateY = useRef(new Animated.Value(-120)).current;

  const showBanner = useCallback(
    (delta: number) => {
      setAlert({ visible: true, delta });
      Animated.spring(translateY, {
        toValue: 0,
        useNativeDriver: true,
        friction: 7,
      }).start();
      // Auto-dismiss after 5 s.
      setTimeout(() => {
        Animated.timing(translateY, {
          toValue: -120,
          duration: 280,
          useNativeDriver: true,
        }).start(() => setAlert({ visible: false, delta: 0 }));
      }, 5000);
    },
    [translateY],
  );

  const dismiss = useCallback(() => {
    Animated.timing(translateY, {
      toValue: -120,
      duration: 220,
      useNativeDriver: true,
    }).start(() => setAlert({ visible: false, delta: 0 }));
  }, [translateY]);

  // Poll loop — only while a user is logged in.
  useEffect(() => {
    if (!user) {
      lastCountRef.current = null;
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await Api.pendingOrdersCount();
        const cur = Number(r?.count ?? 0);
        const prev = lastCountRef.current;
        // First read after login → just record the baseline; we don't
        // know whether the cached count grew or not, so no notification.
        if (prev === null) {
          lastCountRef.current = cur;
          return;
        }
        if (cancelled) return;
        const delta = cur - prev;
        if (delta > 0) {
          // Visual banner — runs regardless of plan tier.
          showBanner(delta);
          // Audio + system notification — plan-gated.
          if (soundEnabled) {
            // Haptic burst — happy bump, supported on both iOS & Android.
            Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
              .catch(() => {});
            // Foreground / background local notification with default
            // chime. iOS plays the standard "ding"; Android the channel
            // default sound — both feel pleasant.
            fireLocalNewOrderNotification(delta);
          }
        }
        lastCountRef.current = cur;
      } catch {
        /* swallow — transient backend hiccup shouldn't break the UI */
      }
    };
    // First tick on the leading edge so we register the baseline ASAP,
    // then every 30 s on the trailing edge.
    tick();
    const id = setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [user, soundEnabled, showBanner]);

  const value = useMemo(() => ({ dismiss }), [dismiss]);

  return (
    <Ctx.Provider value={value}>
      {children}
      {alert.visible ? (
        <Animated.View
          pointerEvents="box-none"
          style={[
            styles.banner,
            {
              transform: [{ translateY }],
              // Safe-area padding: the banner is absolutely positioned
              // at the TOP of the viewport so on iOS we need to push it
              // below the notch. 44 is the universal status-bar height.
              paddingTop: Platform.OS === "ios" ? 52 : 28,
            },
          ]}
        >
          <TouchableOpacity
            style={styles.bannerInner}
            activeOpacity={0.9}
            onPress={() => {
              dismiss();
              // Take the operator straight to the Orders tab so they
              // can act on the new arrival immediately.
              router.push("/(tabs)/orders");
            }}
          >
            <View style={styles.bannerIcon}>
              <PhIcon name="notifications" size={20} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.bannerTitle}>
                🎉 {alert.delta === 1 ? "New order!" : `${alert.delta} new orders!`}
              </Text>
              <Text style={styles.bannerSub}>Tap to open Orders</Text>
            </View>
            <TouchableOpacity onPress={dismiss} hitSlop={10} style={styles.bannerClose}>
              <PhIcon name="close" size={16} color="#fff" />
            </TouchableOpacity>
          </TouchableOpacity>
        </Animated.View>
      ) : null}
    </Ctx.Provider>
  );
}

export function useNewOrderAlert() {
  return useContext(Ctx);
}

const styles = StyleSheet.create({
  banner: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    zIndex: 9999,
    paddingHorizontal: 12,
    paddingBottom: 12,
  },
  bannerInner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.primary,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    boxShadow: "0px 6px 18px rgba(0,0,0,0.18)",
    elevation: 8,
  },
  bannerIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.18)",
    alignItems: "center",
    justifyContent: "center",
  },
  bannerTitle: {
    color: "#fff",
    fontSize: 15,
    fontWeight: "800",
  },
  bannerSub: {
    color: "rgba(255,255,255,0.85)",
    fontSize: 12,
    marginTop: 1,
  },
  bannerClose: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "rgba(0,0,0,0.18)",
    alignItems: "center",
    justifyContent: "center",
  },
});
