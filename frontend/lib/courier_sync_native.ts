/**
 * courier_sync_native.ts — Phase F8.0
 * -----------------------------------------------------------------
 * ONE shared entry-point that keeps the Android NotificationListener
 * in sync with the user's Courier Partner settings. Fixes the three
 * root causes that silently killed SMS auto-sync:
 *
 *   1. The native master switch (`enabled`) was ONLY set by the legacy
 *      partner toggle on /courier-sync — enabling Auto Sync from the
 *      Courier Partner settings screen never armed the listener.
 *   2. Backend URL + JWT were pushed to the native layer ONLY when the
 *      /courier-sync screen was opened, so most sessions ran with an
 *      empty / stale config.
 *   3. `senderPattern` was hardcoded to "IndiaPost", dropping every
 *      other courier's SMS on-device.
 *
 * Now `syncNativeCourierSync()` is called:
 *   • on app start (after auth) — see CourierSyncNativeBridge in
 *     app/_layout.tsx,
 *   • whenever the app returns to the foreground,
 *   • after any courier save (courier/[id].tsx) or partner toggle
 *     (courier-sync.tsx).
 *
 * It reads ALL enabled couriers' `auto_sync_sender_patterns` (the
 * configurable Scanning Rules), derives safe substring needles, joins
 * them with "|" for the native multi-pattern filter, arms the master
 * switch, and drains the offline SMS queue.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";
import { Api } from "./api";
import CourierSyncListener from "../modules/courier-sync-listener";

/**
 * A sender pattern from courier settings may be a regex fragment
 * (e.g. "\\bINPOST"). The native filter does plain substring matching,
 * so extract only the alphanumeric tokens (≥3 chars) as needles.
 * "INPOST" → ["INPOST"], "VA-INPOST-G" → ["INPOST"].
 */
function patternToNeedles(pattern: string): string[] {
  return String(pattern || "")
    .split(/[^A-Za-z0-9]+/)
    .map((t) => t.trim())
    .filter((t) => t.length >= 3);
}

export type NativeSyncResult = {
  enabled: boolean;
  patterns: string[];
} | null;

export async function syncNativeCourierSync(): Promise<NativeSyncResult> {
  if (Platform.OS !== "android" || !CourierSyncListener.isAvailable()) {
    return null;
  }
  try {
    const token = (await AsyncStorage.getItem("@auth_token")) || "";
    const backendUrl = process.env.EXPO_PUBLIC_BACKEND_URL || "";
    if (!token || !backendUrl) {
      console.log("[courier-sync-native] skipped — no token/backendUrl yet");
      return null;
    }

    let deviceId = await AsyncStorage.getItem("@device_id");
    if (!deviceId) {
      deviceId = `dev_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      await AsyncStorage.setItem("@device_id", deviceId);
    }

    // ── Collect sender needles from BOTH config sources ─────────
    //   a. Per-courier Scanning Rules (couriers collection) — the
    //      modern path, primary authority.
    //   b. Legacy hardcoded partners (courier_partner_configs) — kept
    //      working for users who never opened the new courier UI.
    const patterns: string[] = [];
    let anyEnabled = false;

    const pushNeedles = (raw: string) => {
      for (const n of patternToNeedles(raw)) {
        const up = n.toUpperCase();
        if (!patterns.some((p) => p.toUpperCase() === up)) patterns.push(n);
      }
    };

    const [couriersRes, partnersRes] = await Promise.allSettled([
      Api.listCouriers(),
      Api.courierSyncListPartners(),
    ]);

    if (couriersRes.status === "fulfilled") {
      for (const c of couriersRes.value || []) {
        if (!(c as any).auto_sync_enabled) continue;
        anyEnabled = true;
        for (const p of (c as any).auto_sync_sender_patterns || []) {
          pushNeedles(String(p || ""));
        }
        // India Post brand tag — every real India Post SMS body ends
        // with "- IndiaPost" regardless of the DLT header prefix.
        const nm = String((c as any).name || "").toLowerCase();
        if (nm.includes("india") && nm.includes("post")) {
          pushNeedles("IndiaPost");
        }
      }
    }
    if (partnersRes.status === "fulfilled") {
      for (const p of partnersRes.value || []) {
        if (!p.enabled) continue;
        anyEnabled = true;
        pushNeedles(p.sender_pattern || "");
        if (p.key === "india_post") pushNeedles("IndiaPost");
      }
    }
    if (
      couriersRes.status !== "fulfilled" &&
      partnersRes.status !== "fulfilled"
    ) {
      // Both lookups failed (offline?) — keep the previously stored
      // native config untouched rather than disarming the listener.
      console.log("[courier-sync-native] skipped — config fetch failed");
      return null;
    }

    const senderPattern = patterns.join("|") || "IndiaPost";
    CourierSyncListener.setIngestConfig({
      backendUrl,
      authToken: token,
      deviceId,
      senderPattern,
    });
    CourierSyncListener.setEnabled(anyEnabled);
    // Drain anything that queued while offline / logged-out.
    CourierSyncListener.flushPendingQueue();

    console.log(
      `[courier-sync-native] synced enabled=${anyEnabled} patterns=${senderPattern}`,
    );
    return { enabled: anyEnabled, patterns };
  } catch (e: any) {
    console.log("[courier-sync-native] sync failed:", e?.message || e);
    return null;
  }
}
