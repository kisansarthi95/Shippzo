/**
 * Offline / Pending-sync banner.
 *
 * Shows two states (mutually exclusive, top-priority is offline):
 *   1. "You're offline" — red bar, lasts as long as the device is off-net.
 *   2. "N pending — Sync now" — amber bar, shown when the queue is non-empty.
 *      Tapping it triggers an immediate flush attempt.
 *
 * Visibility:
 *   - Stays on the very top of the screen via absolute positioning so
 *     it doesn't reflow content.
 *   - Self-mounts the SyncQueue.init() listener exactly once on first
 *     render, so any screen including this banner gets the auto-flush
 *     behaviour for free.
 */
import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Platform } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import NetInfo from "@react-native-community/netinfo";
import { SyncQueue } from "../lib/syncQueue";

export default function OfflineBanner() {
  const [online, setOnline] = useState<boolean>(true);
  const [pending, setPending] = useState<number>(0);
  const [errored, setErrored] = useState<number>(0);
  const [busy, setBusy] = useState<boolean>(false);

  // Wire up the queue + NetInfo listeners exactly once.
  useEffect(() => {
    SyncQueue.init();

    let cancelled = false;
    const refresh = async () => {
      try {
        const [p, e] = await Promise.all([
          SyncQueue.pendingCount(),
          SyncQueue.erroredCount(),
        ]);
        if (!cancelled) {
          setPending(p);
          setErrored(e);
        }
      } catch { /* ignore */ }
    };
    refresh();
    const unsub = SyncQueue.subscribe(refresh);

    const ns = NetInfo.addEventListener((s) => {
      setOnline(!!s.isConnected);
    });
    NetInfo.fetch().then((s) => setOnline(!!s.isConnected)).catch(() => {});

    return () => {
      cancelled = true;
      unsub();
      ns();
    };
  }, []);

  const onRetry = async () => {
    setBusy(true);
    try {
      await SyncQueue.flush();
    } finally {
      setBusy(false);
    }
  };

  // Priority: offline banner > pending sync banner > nothing.
  if (!online) {
    return (
      <View style={[styles.bar, styles.barOffline]} testID="offline-banner">
        <Ionicons name="cloud-offline" size={14} color="#fff" />
        <Text style={styles.txt}>
          You're offline — new shipments will be saved & synced when back online
        </Text>
      </View>
    );
  }

  if (pending > 0 || errored > 0) {
    return (
      <TouchableOpacity
        testID="pending-sync-banner"
        onPress={onRetry}
        activeOpacity={0.85}
        style={[styles.bar, styles.barPending]}
        disabled={busy}
      >
        <Ionicons
          name={errored > 0 ? "alert-circle" : "cloud-upload-outline"}
          size={14}
          color="#92400E"
        />
        <Text style={[styles.txt, { color: "#78350F" }]}>
          {busy
            ? "Syncing…"
            : errored > 0
              ? `${pending} pending · ${errored} errored — Tap to retry`
              : `${pending} shipment${pending === 1 ? "" : "s"} pending sync — Tap to retry`}
        </Text>
        <Ionicons name="refresh" size={14} color="#92400E" />
      </TouchableOpacity>
    );
  }

  return null;
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderBottomWidth: 1,
  },
  barOffline: {
    backgroundColor: "#DC2626",
    borderBottomColor: "#991B1B",
  },
  barPending: {
    backgroundColor: "#FEF3C7",
    borderBottomColor: "#FCD34D",
  },
  txt: {
    flex: 1,
    color: "#fff",
    fontSize: 11.5,
    fontWeight: "700",
    letterSpacing: 0.2,
    ...Platform.select({ android: { paddingTop: 1 } }),
  },
});
