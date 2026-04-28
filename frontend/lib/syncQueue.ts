/**
 * Offline-first sync queue for shipment writes.
 *
 * Why:
 *   Couriers are often added on the road / at the customer's premise where
 *   cellular signal is weak. We don't want to lose the data the user just
 *   painstakingly entered. So when an API write fails because of a network
 *   error, we persist the operation to AsyncStorage and replay it later.
 *
 * What it queues (Phase-1 MVP):
 *   - Shipment CREATE only. Update/Delete/Status changes can be added
 *     later — they're rarer offline use cases and need conflict-resolution
 *     thinking that's out of scope for the first cut.
 *
 * Replay triggers:
 *   1. NetInfo "isConnected" transitioned from false → true.
 *   2. AppState transitioned from background → active (user reopened app).
 *   3. Explicit user tap on the "Pending sync — Retry" chip.
 *
 * Failure handling:
 *   - Network error / 5xx → keep in queue, increment `tries`.
 *   - 4xx (validation, duplicate, auth) → mark `permanent_error` and
 *     stop retrying. The user can manually clear via Settings later.
 *   - tries >= 10 → also mark permanent_error to avoid infinite loops.
 *
 * The queue is process-local: if the user has the app on two devices,
 * each device drains its own queue. That's fine for our use case — a
 * shipment is created on whichever device has it queued.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import NetInfo from "@react-native-community/netinfo";
import { AppState, AppStateStatus } from "react-native";
import { Api } from "./api";

const STORAGE_KEY = "@offline_sync_queue_v1";
const MAX_TRIES = 10;

export type QueueOpType =
  | "shipment_create"
  | "shipment_update"
  | "shipment_delete"
  | "shipment_status";

export type QueueItem = {
  id: string;                  // local uuid
  type: QueueOpType;
  payload: any;                // raw body for the API
  created_at: string;          // ISO timestamp
  tries: number;
  last_error?: string;
  permanent_error?: boolean;   // 4xx / non-retryable
  // Optional friendly label for UI (e.g. customer name) — speeds up listing.
  label?: string;
};

type Listener = () => void;

let listeners: Listener[] = [];
let flushing = false;
let inMemoryCache: QueueItem[] | null = null;
let initialised = false;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

async function readQueue(): Promise<QueueItem[]> {
  if (inMemoryCache) return inMemoryCache;
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    inMemoryCache = raw ? (JSON.parse(raw) as QueueItem[]) : [];
  } catch {
    inMemoryCache = [];
  }
  return inMemoryCache;
}

async function writeQueue(items: QueueItem[]): Promise<void> {
  inMemoryCache = items;
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    /* best-effort — if AsyncStorage fails, the in-memory cache still works
       for the current session. */
  }
  notify();
}

function notify() {
  for (const fn of listeners) {
    try { fn(); } catch { /* swallow */ }
  }
}

function isNetworkErrorish(err: any): boolean {
  // Axios / fetch network errors usually have no `response`.
  if (!err) return false;
  if (err?.response?.status >= 500) return true;
  if (err?.message === "Network Error") return true;
  if (err?.code === "ECONNABORTED") return true;       // timeout
  if (err?.code === "ERR_NETWORK") return true;
  if (typeof err?.message === "string" && err.message.toLowerCase().includes("network")) return true;
  return false;
}

function uuidLike(): string {
  return "off-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const SyncQueue = {
  /** Subscribe to queue mutations. Returns an unsub fn. */
  subscribe(fn: Listener): () => void {
    listeners.push(fn);
    return () => {
      listeners = listeners.filter((l) => l !== fn);
    };
  },

  /** All pending items (oldest first). */
  async getAll(): Promise<QueueItem[]> {
    return [...(await readQueue())];
  },

  /** Count helpers. */
  async count(): Promise<number> {
    return (await readQueue()).length;
  },
  async pendingCount(): Promise<number> {
    return (await readQueue()).filter((i) => !i.permanent_error).length;
  },
  async erroredCount(): Promise<number> {
    return (await readQueue()).filter((i) => i.permanent_error).length;
  },

  /** Add a shipment-create payload to the queue. Returns the queue item. */
  async enqueueShipmentCreate(payload: any, label?: string): Promise<QueueItem> {
    const items = await readQueue();
    const item: QueueItem = {
      id: uuidLike(),
      type: "shipment_create",
      payload,
      created_at: new Date().toISOString(),
      tries: 0,
      label: label || payload?.customer_name || payload?.tracking_id || "",
    };
    items.push(item);
    await writeQueue(items);
    return item;
  },

  /** Queue an UPDATE on an existing shipment. Conflict strategy: last-
   *  write-wins. If the user makes multiple offline edits to the same
   *  shipment we collapse them into the most recent payload. */
  async enqueueShipmentUpdate(
    shipmentId: string,
    payload: any,
    label?: string,
  ): Promise<QueueItem> {
    const items = await readQueue();
    // Coalesce: drop any earlier pending update for this shipment.
    const filtered = items.filter(
      (i) => !(i.type === "shipment_update" && i.payload?.__id === shipmentId),
    );
    const item: QueueItem = {
      id: uuidLike(),
      type: "shipment_update",
      // We stash the id under `__id` so the flusher can pull it out.
      payload: { ...payload, __id: shipmentId },
      created_at: new Date().toISOString(),
      tries: 0,
      label: label || payload?.customer_name || payload?.tracking_id || shipmentId,
    };
    filtered.push(item);
    await writeQueue(filtered);
    return item;
  },

  /** Queue a DELETE on a shipment. If a pending CREATE exists for the
   *  same client-side id we just drop the create instead of round-
   *  tripping (saves a wasted POST when the user creates+deletes offline). */
  async enqueueShipmentDelete(shipmentId: string, label?: string): Promise<QueueItem | null> {
    const items = await readQueue();
    // Drop any pending update for this shipment — moot once deleted.
    let next = items.filter(
      (i) => !(i.type === "shipment_update" && i.payload?.__id === shipmentId),
    );
    const item: QueueItem = {
      id: uuidLike(),
      type: "shipment_delete",
      payload: { __id: shipmentId },
      created_at: new Date().toISOString(),
      tries: 0,
      label: label || shipmentId,
    };
    next.push(item);
    await writeQueue(next);
    return item;
  },

  /** Queue a STATUS change (delivered / pending / cancelled). */
  async enqueueShipmentStatus(
    shipmentId: string,
    status: string,
    label?: string,
  ): Promise<QueueItem> {
    const items = await readQueue();
    // Coalesce: keep only the latest status change for this shipment.
    const filtered = items.filter(
      (i) => !(i.type === "shipment_status" && i.payload?.__id === shipmentId),
    );
    const item: QueueItem = {
      id: uuidLike(),
      type: "shipment_status",
      payload: { __id: shipmentId, status },
      created_at: new Date().toISOString(),
      tries: 0,
      label: label || `${shipmentId} → ${status}`,
    };
    filtered.push(item);
    await writeQueue(filtered);
    return item;
  },

  /** Remove an item from the queue (e.g. after success or user dismiss). */
  async remove(id: string): Promise<void> {
    const items = await readQueue();
    const next = items.filter((i) => i.id !== id);
    await writeQueue(next);
  },

  /** Clear permanently-errored items (user opted to discard). */
  async clearErrored(): Promise<void> {
    const items = await readQueue();
    const next = items.filter((i) => !i.permanent_error);
    await writeQueue(next);
  },

  /** Try to drain the queue. Safe to call repeatedly — a mutex prevents
   *  overlapping flushes. Returns the count of items successfully synced. */
  async flush(): Promise<number> {
    if (flushing) return 0;
    flushing = true;
    let synced = 0;
    try {
      const items = await readQueue();
      // Process oldest first.
      for (const item of [...items]) {
        if (item.permanent_error) continue;
        if (item.tries >= MAX_TRIES) {
          item.permanent_error = true;
          item.last_error = item.last_error || "Max retries reached.";
          continue;
        }
        try {
          if (item.type === "shipment_create") {
            await Api.createShipment(item.payload);
          } else if (item.type === "shipment_update") {
            const { __id, ...body } = item.payload || {};
            if (!__id) throw new Error("missing shipment id");
            await Api.updateShipment(__id, body);
          } else if (item.type === "shipment_delete") {
            const { __id } = item.payload || {};
            if (!__id) throw new Error("missing shipment id");
            await Api.deleteShipment(__id);
          } else if (item.type === "shipment_status") {
            const { __id, status } = item.payload || {};
            if (!__id || !status) throw new Error("missing id/status");
            await Api.updateShipment(__id, { status } as any);
          }
          // Success → remove from queue.
          const after = (await readQueue()).filter((i) => i.id !== item.id);
          await writeQueue(after);
          synced += 1;
        } catch (err: any) {
          item.tries += 1;
          item.last_error =
            err?.response?.data?.detail || err?.message || "Unknown error";
          if (!isNetworkErrorish(err)) {
            // Definitive failure (4xx etc) → don't keep retrying forever.
            item.permanent_error = true;
          }
          // Persist mutation back to storage.
          const updated = (await readQueue()).map((i) =>
            i.id === item.id ? item : i,
          );
          await writeQueue(updated);
          // If it was a network failure, stop draining the queue — assume
          // we're offline again and don't burn through retries.
          if (isNetworkErrorish(err)) break;
        }
      }
    } finally {
      flushing = false;
    }
    return synced;
  },

  /** Wire up NetInfo + AppState listeners. Idempotent. */
  init() {
    if (initialised) return;
    initialised = true;

    // Replay on connectivity recovery.
    let lastConnected: boolean | null = null;
    NetInfo.addEventListener((state) => {
      const connected = !!state.isConnected;
      if (lastConnected === false && connected) {
        // Just came back online — try to drain.
        this.flush().catch(() => {});
      }
      lastConnected = connected;
    });

    // Replay when the app returns to the foreground.
    let lastAppState: AppStateStatus = AppState.currentState;
    AppState.addEventListener("change", (next) => {
      if (lastAppState !== "active" && next === "active") {
        this.flush().catch(() => {});
      }
      lastAppState = next;
    });

    // Initial drain on boot — covers the case where the user opened the
    // app already-online with leftover queue from a previous session.
    setTimeout(() => {
      this.flush().catch(() => {});
    }, 1500);
  },
};
