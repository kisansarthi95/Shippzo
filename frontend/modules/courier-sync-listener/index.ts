/**
 * courier-sync-listener — JS bridge for the Android NotificationListenerService
 * that auto-forwards courier SMS notifications to the backend ingest endpoint.
 *
 * Available platforms: Android only.
 *   - iOS gracefully no-ops (returns sensible defaults so callers do not crash).
 *   - Expo Go ALSO no-ops because the native code is not bundled into Expo Go
 *     — operators must publish an EAS dev / production build to use this.
 */
import { NativeModule, requireOptionalNativeModule } from "expo";
import { Platform } from "react-native";

type IngestConfig = {
  /** Full backend URL ending in /api (no trailing slash). */
  backendUrl: string;
  /** JWT auth token of the logged-in user. */
  authToken: string;
  /** Opaque per-install identifier so the backend audit log can group events. */
  deviceId: string;
  /**
   * Sender substring filter — case-insensitive. Notifications whose sender
   * / title does not contain this string are dropped before any network call.
   * Default "INPOST" (India Post DLT senders like "VA-INPOST-G").
   */
  senderPattern?: string;
};

type ListenerStatus = {
  available: boolean;          // is the native module present at runtime?
  permissionGranted: boolean;  // has the user enabled Notification Access?
  ingestConfigured: boolean;   // do we have a backendUrl + authToken cached?
  enabled: boolean;            // master switch — false suppresses forwarding
};

interface CourierSyncListenerEvents {
  // Reserved for future: "onIngestResult" with delivery stats.
}

declare class CourierSyncListenerNativeModule extends NativeModule<CourierSyncListenerEvents> {
  isAvailable(): boolean;
  isPermissionGranted(): boolean;
  openNotificationAccessSettings(): void;
  setIngestConfig(cfg: IngestConfig): void;
  setEnabled(enabled: boolean): void;
  getStatus(): ListenerStatus;
}

// `requireOptionalNativeModule` returns null when the native module is not
// linked (Expo Go, iOS, web). All call-sites below must defend against that.
const NativeMod = requireOptionalNativeModule<CourierSyncListenerNativeModule>(
  "CourierSyncListener",
);

const FALLBACK_STATUS: ListenerStatus = {
  available:          false,
  permissionGranted:  false,
  ingestConfigured:   false,
  enabled:            false,
};

export const CourierSyncListener = {
  /** True when the native module is bundled and the OS is supported. */
  isAvailable(): boolean {
    if (Platform.OS !== "android") return false;
    try {
      return !!NativeMod && NativeMod.isAvailable();
    } catch {
      return false;
    }
  },

  /** Has the user granted "Notification Access" in System Settings? */
  isPermissionGranted(): boolean {
    if (!CourierSyncListener.isAvailable()) return false;
    try {
      return NativeMod!.isPermissionGranted();
    } catch {
      return false;
    }
  },

  /** Opens the system Notification Access settings screen for our app. */
  openNotificationAccessSettings(): void {
    if (!CourierSyncListener.isAvailable()) return;
    try {
      NativeMod!.openNotificationAccessSettings();
    } catch {
      /* no-op */
    }
  },

  /**
   * Persist the backend ingest config so the native service can POST without
   * needing the React layer to be running. Safe to call repeatedly — each
   * call overwrites the previous one.
   */
  setIngestConfig(cfg: IngestConfig): void {
    if (!CourierSyncListener.isAvailable()) return;
    try {
      NativeMod!.setIngestConfig({
        backendUrl:    cfg.backendUrl,
        authToken:     cfg.authToken,
        deviceId:      cfg.deviceId,
        senderPattern: cfg.senderPattern || "INPOST",
      });
    } catch {
      /* no-op */
    }
  },

  /** Master switch — when false the listener still runs but drops notifications. */
  setEnabled(enabled: boolean): void {
    if (!CourierSyncListener.isAvailable()) return;
    try {
      NativeMod!.setEnabled(!!enabled);
    } catch {
      /* no-op */
    }
  },

  /** Snapshot of all flags — handy for the onboarding screen status pill. */
  getStatus(): ListenerStatus {
    if (!CourierSyncListener.isAvailable()) return { ...FALLBACK_STATUS };
    try {
      return NativeMod!.getStatus();
    } catch {
      return { ...FALLBACK_STATUS };
    }
  },
};

export type { IngestConfig, ListenerStatus };
export default CourierSyncListener;
