// Tiny in-memory bridge so the scanner can return a scanned value
// (plus the matched courier, if any) back to the Add / Dashboard
// screen without unmounting them (router.back preserves the stack).
//
// Phase-4d: also carries `courier_id` when the scanner was able to
// auto-detect the courier from the tracking-ID format rules.

export type ScanResult = {
  value: string;
  courier_id?: string | null;
  courier_name?: string | null;
};

let pending: ScanResult | null = null;

export const scannerBridge = {
  /**
   * Legacy signature — accepts either a raw string (old callers) or
   * a full ScanResult. Both end up as a ScanResult in storage.
   */
  push(v: string | ScanResult, meta?: { courier_id?: string; courier_name?: string }) {
    if (typeof v === "string") {
      pending = {
        value: v,
        courier_id: meta?.courier_id ?? null,
        courier_name: meta?.courier_name ?? null,
      };
    } else {
      pending = v;
    }
  },
  consume(): ScanResult | null {
    const v = pending;
    pending = null;
    return v;
  },
};
