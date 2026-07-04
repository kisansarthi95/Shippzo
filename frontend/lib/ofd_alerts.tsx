/**
 * Phase F4.4 — Out-for-Delivery 2-hour SLA alert hook + banner.
 *
 * Companion to `new_order_alert.tsx`. Polls
 * `GET /api/courier-sync/ofd-alerts?hours=2` every 5 minutes. When the
 * backend returns any shipment that:
 *    1. Has an `out_for_delivery_at` older than 2h AND
 *    2. Is NOT Delivered AND
 *    3. Has NOT already been alerted (`ofd_alert_fired_at` empty)
 *
 * the hook:
 *    1. Fires a local expo-notification (title + body identifying the AWB,
 *       courier person, and how long ago the OFD SMS landed).
 *    2. Marks the shipment via `PUT /api/courier-sync/ofd-alerts/{id}/fired`
 *       so subsequent polls do NOT re-alert.
 *    3. Shows an in-app slide-down banner so the operator notices while
 *       Shippzo is open.
 *
 * Non-goals (Phase F4.4):
 *    • WhatsApp alerts — will piggyback on the same trigger once the
 *      WhatsApp Provider is wired to notify_admin templates.
 *    • Escalation cascades (30/60/90 min tiers) — v2 concern.
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
import { useRouter } from "expo-router";
import { Api } from "./api";
import { useAuth } from "./auth";
import PhIcon from "../components/PhIcon";

// Poll every 5 minutes. Backend endpoint is cheap (indexed shipment
// scan filtered by user_id + status != Delivered + OFD ts present)
// so this is well within any reasonable rate budget.
const POLL_INTERVAL_MS = 5 * 60 * 1000;

type Alert = {
  shipment_id:      string;
  tracking_id:      string;
  customer_name:    string;
  customer_phone:   string;
  courier_name:     string;
  hours_elapsed:    number;
  delivery_person:  string;
  delivery_beat:    string;
  attempts:         number;
};

async function fireLocalOfdNotification(a: Alert) {
  try {
    const Notifications = await import("expo-notifications");
    // Same handler as new_order — safe to set multiple times.
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowAlert:  true,
        shouldShowBanner: true,
        shouldShowList:   true,
        shouldPlaySound:  true,
        shouldSetBadge:   false,
      }),
    });
    const who = a.delivery_person
      ? `${a.delivery_person}${a.delivery_beat ? ` (${a.delivery_beat})` : ""}`
      : "courier";
    await Notifications.scheduleNotificationAsync({
      content: {
        title: `⚠️ ${a.tracking_id} still not delivered`,
        body:
          `Out for delivery ${a.hours_elapsed.toFixed(1)}h ago by ${who}. ` +
          `Please contact ${a.customer_name || "customer"} or the courier.`,
        sound: "default",
        data: {
          type:        "ofd_alert",
          screen:      "shipment_details",
          shipment_id: a.shipment_id,
        },
      },
      trigger: null,
    });
  } catch {
    /* expo-notifications not linked — banner still fires. */
  }
}

// ---------------------- Provider + Context ----------------------------
type BannerState = {
  visible: boolean;
  alerts:  Alert[];   // latest tick's alerts, deduped
};

const Ctx = createContext<{ dismiss: () => void }>({ dismiss: () => {} });

export function OfdAlertProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const router = useRouter();
  const [state, setState] = useState<BannerState>({ visible: false, alerts: [] });
  const translateY = useRef(new Animated.Value(-140)).current;
  // Track which shipment ids we've already fired locally in THIS session
  // to avoid a re-alert if the backend's `ofd_alert_fired_at` write
  // races with the next poll.
  const firedThisSession = useRef<Set<string>>(new Set());

  const showBanner = useCallback(
    (alerts: Alert[]) => {
      setState({ visible: true, alerts });
      Animated.spring(translateY, {
        toValue: 0,
        useNativeDriver: true,
        friction: 7,
      }).start();
      setTimeout(() => {
        Animated.timing(translateY, {
          toValue: -140,
          duration: 280,
          useNativeDriver: true,
        }).start(() => setState({ visible: false, alerts: [] }));
      }, 7000);
    },
    [translateY],
  );

  const dismiss = useCallback(() => {
    Animated.timing(translateY, {
      toValue: -140,
      duration: 220,
      useNativeDriver: true,
    }).start(() => setState({ visible: false, alerts: [] }));
  }, [translateY]);

  useEffect(() => {
    if (!user) {
      firedThisSession.current.clear();
      return;
    }
    let cancelled = false;

    const tick = async () => {
      try {
        const r = await Api.courierSyncOfdAlerts(2);
        if (cancelled || !r?.alerts || r.alerts.length === 0) return;
        const fresh = r.alerts.filter(
          (a) => !firedThisSession.current.has(a.shipment_id),
        );
        if (fresh.length === 0) return;
        // Batch banner — one line per alert (max 3 shown).
        showBanner(fresh.slice(0, 3));
        // Fire each local notification + mark server-side.
        for (const a of fresh) {
          firedThisSession.current.add(a.shipment_id);
          fireLocalOfdNotification(a);
          Api.courierSyncMarkOfdAlertFired(a.shipment_id).catch(() => {
            // If the mark fails, we still keep the session-side flag so
            // we don't spam the user. Next app boot will re-check
            // backend and re-alert if still valid.
          });
        }
      } catch {
        /* backend hiccup — swallow */
      }
    };

    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [user, showBanner]);

  const value = useMemo(() => ({ dismiss }), [dismiss]);

  return (
    <Ctx.Provider value={value}>
      {children}
      {state.visible && state.alerts.length > 0 ? (
        <Animated.View
          pointerEvents="box-none"
          style={[
            styles.banner,
            {
              transform: [{ translateY }],
              paddingTop: Platform.OS === "ios" ? 52 : 28,
            },
          ]}
        >
          <TouchableOpacity
            style={styles.bannerInner}
            activeOpacity={0.9}
            onPress={() => {
              dismiss();
              // Deep-link to the first shipment's detail page.
              const first = state.alerts[0];
              if (first?.shipment_id) {
                router.push(`/shipment-details/${first.shipment_id}` as any);
              }
            }}
          >
            <View style={styles.bannerIcon}>
              <PhIcon name="alert-circle" size={22} color="#fff" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.bannerTitle}>
                ⚠️{" "}
                {state.alerts.length === 1
                  ? `Not delivered yet — ${state.alerts[0].tracking_id}`
                  : `${state.alerts.length} shipments overdue delivery`}
              </Text>
              <Text style={styles.bannerSub}>
                {state.alerts.length === 1
                  ? `${state.alerts[0].hours_elapsed.toFixed(1)}h since Out for Delivery — tap to open`
                  : "Tap to review Out-for-Delivery status"}
              </Text>
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

export function useOfdAlert() {
  return useContext(Ctx);
}

const styles = StyleSheet.create({
  banner: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    zIndex: 9998,   // just below new_order banner (9999)
    paddingHorizontal: 12,
    paddingBottom: 12,
  },
  bannerInner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: "#DC2626",   // urgent red — differentiates from primary
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    boxShadow: "0px 6px 18px rgba(0,0,0,0.22)",
    elevation: 10,
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
    marginTop: 2,
  },
  bannerClose: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "rgba(0,0,0,0.20)",
    alignItems: "center",
    justifyContent: "center",
  },
});
