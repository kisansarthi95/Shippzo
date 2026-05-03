/**
 * Expo push token registration
 * ============================
 * Centralised one-shot helper invoked from the AuthProvider/Layout
 * the first time the user lands on an authenticated screen.
 *
 * Flow:
 *   1. Check we're on a real device (bail on web/simulator).
 *   2. Ensure notification channel "default" exists (Android only).
 *   3. Ask permission if not yet granted.
 *   4. Pull the Expo push token from the projectId in app config.
 *   5. POST to /api/me/push-token so the backend can target this device.
 *
 * Re-running is idempotent — backend de-dupes by token.
 *
 * IMPORTANT: this code is silent on failure. Push is a nice-to-have,
 * not a blocker. Console logs are kept for debugging.
 */
import { Platform } from "react-native";
import * as Device from "expo-device";
import Constants from "expo-constants";
import { Api } from "./api";

let _registeredToken: string | null = null;

async function _setupAndroidChannel() {
  if (Platform.OS !== "android") return;
  try {
    // Lazy-import: expo-notifications is heavy and only needed on
    // platforms where we actually deliver push.
    const Notifications = await import("expo-notifications");
    await Notifications.setNotificationChannelAsync("default", {
      name: "Default",
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: "#FF5A00",
      sound: "default",
    });
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[push] android channel setup failed", e);
  }
}

async function _ensurePermission(): Promise<boolean> {
  try {
    const Notifications = await import("expo-notifications");
    const settings = await Notifications.getPermissionsAsync();
    if (settings.granted) return true;
    if (settings.canAskAgain === false) return false;
    const req = await Notifications.requestPermissionsAsync();
    return !!req.granted;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[push] permission request failed", e);
    return false;
  }
}

/**
 * One-shot register. Safe to call multiple times. Returns null when
 * push is unavailable (web preview / simulator / permission denied).
 */
export async function registerForPushNotificationsAsync(): Promise<string | null> {
  // Skip on web / non-physical devices — Expo Push Tokens only work
  // on real iOS/Android hardware.
  if (Platform.OS === "web") return null;
  if (!Device.isDevice) {
    // eslint-disable-next-line no-console
    console.log("[push] skipped — not a physical device");
    return null;
  }

  // Already registered in this session? Skip the round-trip.
  if (_registeredToken) return _registeredToken;

  await _setupAndroidChannel();
  const granted = await _ensurePermission();
  if (!granted) {
    // eslint-disable-next-line no-console
    console.log("[push] permission not granted");
    return null;
  }

  try {
    const Notifications = await import("expo-notifications");
    const projectId =
      Constants.expoConfig?.extra?.eas?.projectId
      || (Constants as any).easConfig?.projectId
      || (Constants as any)?.expoGoConfig?.projectId
      || undefined;

    const tokenObj = await Notifications.getExpoPushTokenAsync(
      projectId ? { projectId } : ({} as any),
    );
    const token = tokenObj?.data;
    if (!token) return null;

    // Send to backend (best-effort).
    try {
      await Api.registerPushToken({
        token,
        platform: Platform.OS,
        device_id: (Device.osBuildId || Device.modelName || "") as string,
      });
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn("[push] backend register failed", e);
    }

    _registeredToken = token;
    return token;
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn("[push] getExpoPushTokenAsync failed", e);
    return null;
  }
}

/** Clear cached token — call on logout. */
export function clearCachedPushToken() {
  _registeredToken = null;
}

/** Returns the in-memory token (or null if not yet registered). */
export function getCachedPushToken(): string | null {
  return _registeredToken;
}
