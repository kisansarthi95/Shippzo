/**
 * screenCache — tiny in-memory cache keyed by screen name.
 *
 * Goal: when the user re-opens a screen they recently visited, render
 * the previously-fetched data IMMEDIATELY (no blocking spinner) while
 * a background refresh fetches the latest. Cache survives navigation
 * but is dropped when the JS bundle reloads (intentional — keeps it
 * lightweight; SecureStore / AsyncStorage would be premature for this
 * use case and adds I/O latency on every paint).
 *
 * Usage:
 *
 *   import { screenCache } from "../lib/screenCache";
 *
 *   const cached = screenCache.get<MyDataShape>("customers");
 *   const [data, setData] = useState<MyDataShape | null>(cached);
 *   const [loading, setLoading] = useState(!cached); // only block when nothing cached
 *
 *   useEffect(() => {
 *     // ALWAYS refresh in background — show stale data first, then
 *     // swap to fresh data once it arrives.
 *     fetchData().then((fresh) => {
 *       setData(fresh);
 *       screenCache.set("customers", fresh);
 *     }).finally(() => setLoading(false));
 *   }, []);
 *
 * TTL: optional per-key freshness window. Default = 5 minutes. Older
 * entries are still returned (so the user sees SOMETHING) but the
 * caller can decide to force a sync wait by checking `isFresh()`.
 */
type Entry = { value: unknown; ts: number };

const store = new Map<string, Entry>();
const DEFAULT_TTL_MS = 5 * 60 * 1000;

export const screenCache = {
  /** Read a previously-cached value (any age). */
  get<T = unknown>(key: string): T | null {
    const e = store.get(key);
    return e ? (e.value as T) : null;
  },

  /** True when the cached value exists and is younger than `ttlMs`. */
  isFresh(key: string, ttlMs: number = DEFAULT_TTL_MS): boolean {
    const e = store.get(key);
    if (!e) return false;
    return Date.now() - e.ts < ttlMs;
  },

  /** Write / overwrite a cache entry with the current timestamp. */
  set<T>(key: string, value: T): T {
    store.set(key, { value, ts: Date.now() });
    return value;
  },

  /** Drop one or all entries (used after logout / user switch). */
  invalidate(key?: string) {
    if (key) store.delete(key);
    else store.clear();
  },

  /** Returns the age in ms (or Infinity if missing). */
  ageMs(key: string): number {
    const e = store.get(key);
    return e ? Date.now() - e.ts : Infinity;
  },
};
