/**
 * Stable per-device fingerprint for anti-abuse checks (Phase-2b).
 *
 * Goal: stop a single user from creating dozens of free-trial accounts
 * by simply changing the email address each time.
 *
 * What we capture:
 *   • Mobile (iOS): `Application.getIosIdForVendorAsync()` — stable
 *     across reinstalls of the same vendor's apps on the same device
 *     (resets only on full device wipe).
 *   • Mobile (Android): `Application.getAndroidId()` — stable for the
 *     life of the install + per-user / per-app combo.
 *   • Web: a self-generated UUID stored in AsyncStorage (best we can
 *     do without a real device-id; clearing storage resets it, which
 *     is fine — most abuse comes from native installs anyway).
 *   • Add a bucket of `Device.modelId` + `Device.osVersion` so that
 *     factory-reset wipes don't trivially collide with a fresh device.
 *
 * Storage: After first successful resolution we cache the final hash
 * in AsyncStorage so we don't re-query native APIs on every app launch.
 *
 * Privacy note: the value sent to the server is a *hash*, not the raw
 * device id. The server stores only the hash. We never send GPS, mac
 * address, ad-id, or anything that could deanonymise the user.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Application from "expo-application";
import * as Device from "expo-device";
import { Platform } from "react-native";

const CACHE_KEY = "@device_fingerprint_v1";

/** Tiny non-cryptographic hash (FNV-1a). Output stable across runs. */
function fnv1aHex(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return ("00000000" + h.toString(16)).slice(-8);
}

/** Generate a v4-ish UUID without bringing in a heavyweight dep. */
function quickUuid(): string {
  return "wfp-" + Date.now().toString(36) + "-" +
    Math.random().toString(36).slice(2, 10) +
    Math.random().toString(36).slice(2, 10);
}

async function resolveRaw(): Promise<string> {
  try {
    if (Platform.OS === "ios") {
      const v = await Application.getIosIdForVendorAsync();
      if (v) return `ios:${v}`;
    } else if (Platform.OS === "android") {
      // Note: getAndroidId is sync.
      const a = (Application as any).getAndroidId?.();
      if (a) return `and:${a}`;
    } else {
      // Web — keep a persistent uuid in storage.
      let v = await AsyncStorage.getItem("@device_fingerprint_web_uuid");
      if (!v) {
        v = quickUuid();
        try { await AsyncStorage.setItem("@device_fingerprint_web_uuid", v); } catch {/* ignore */}
      }
      return `web:${v}`;
    }
  } catch {
    /* fall through to UUID fallback */
  }
  // Native fallback if the platform-specific id isn't accessible (e.g.
  // user denied tracking permission, or running on an unsupported
  // hardware where these APIs are no-ops).
  let v = await AsyncStorage.getItem("@device_fingerprint_fallback_uuid");
  if (!v) {
    v = quickUuid();
    try { await AsyncStorage.setItem("@device_fingerprint_fallback_uuid", v); } catch {/* ignore */}
  }
  return `fb:${v}`;
}

/** Returns a stable, opaque, hashed fingerprint suitable for server use. */
export async function getDeviceFingerprint(): Promise<string> {
  // Cached?
  try {
    const cached = await AsyncStorage.getItem(CACHE_KEY);
    if (cached && cached.length > 0) return cached;
  } catch {/* ignore */}

  const raw = await resolveRaw();
  // Mix in some non-PII hardware info so a wiped device doesn't
  // collide with another device of the same model.
  const meta = [
    Device.osName || "",
    Device.osVersion || "",
    Device.modelId || "",
    Device.brand || "",
    Platform.OS,
  ].join("|");
  const hash = fnv1aHex(raw) + "-" + fnv1aHex(meta) + "-" + fnv1aHex(raw + meta);

  try { await AsyncStorage.setItem(CACHE_KEY, hash); } catch {/* ignore */}
  return hash;
}

/** Best-effort getter that never throws — returns "" on any failure. */
export async function safeGetDeviceFingerprint(): Promise<string> {
  try {
    return await getDeviceFingerprint();
  } catch {
    return "";
  }
}
