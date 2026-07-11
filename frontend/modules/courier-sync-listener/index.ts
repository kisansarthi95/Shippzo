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
  pendingQueueCount?: number;  // SMS payloads queued while offline (F8.0)
};

/** Payload of the "onIngestResult" event — fired after every
 *  SUCCESSFUL POST to /api/courier-sync/ingest (incl. queued retries). */
type IngestResultEvent = {
  status: number;   // HTTP status (2xx)
  body: string;     // raw response body (JSON string with action/shipment_id)
};

interface CourierSyncListenerEvents {
  onIngestResult(event: IngestResultEvent): void;
}

declare class CourierSyncListenerNativeModule extends NativeModule<CourierSyncListenerEvents> {
  isAvailable(): boolean;
  isPermissionGranted(): boolean;
  openNotificationAccessSettings(): void;
  setIngestConfig(cfg: IngestConfig): void;
  setEnabled(enabled: boolean): void;
  getStatus(): ListenerStatus;
  flushPendingQueue(): void;
  getPendingQueueCount(): number;
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

  /**
   * Phase F8.0 — retry-deliver any SMS payloads that queued up while
   * the device was offline. Fire-and-forget (native does the HTTP on
   * a background thread).
   */
  flushPendingQueue(): void {
    if (!CourierSyncListener.isAvailable()) return;
    try {
      NativeMod!.flushPendingQueue();
    } catch {
      /* no-op */
    }
  },

  /** Number of SMS payloads waiting for connectivity. */
  getPendingQueueCount(): number {
    if (!CourierSyncListener.isAvailable()) return 0;
    try {
      return NativeMod!.getPendingQueueCount() || 0;
    } catch {
      return 0;
    }
  },

  /**
   * Phase F8.0 — subscribe to successful ingest results so open
   * screens can refetch the moment an SMS updates a shipment.
   * Returns a subscription with `.remove()`, or null when the native
   * module is absent (Expo Go / iOS / web).
   */
  addIngestResultListener(
    cb: (e: IngestResultEvent) => void,
  ): { remove: () => void } | null {
    if (!CourierSyncListener.isAvailable()) return null;
    try {
      return NativeMod!.addListener("onIngestResult", cb);
    } catch {
      return null;
    }
  },
};

export type { IngestConfig, ListenerStatus, IngestResultEvent };
export default CourierSyncListener;
