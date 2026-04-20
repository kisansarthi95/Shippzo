// Tiny in-memory bridge so scanner can return a value without
// unmounting the caller screen (router.back preserves stack).
let pending: string | null = null;

export const scannerBridge = {
  push(v: string) {
    pending = v;
  },
  consume(): string | null {
    const v = pending;
    pending = null;
    return v;
  },
};
