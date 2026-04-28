/**
 * Tracking-ID format validation utility.
 *
 * Enforces per-courier rules (prefix / suffix / length / min-max /
 * optional regex) so the scanner and manual entry reject garbled
 * reads or wrong courier IDs.
 *
 * Example (India Post Speed Post):
 *   courier.tracking_id_prefix = "EG"
 *   courier.tracking_id_suffix = "IN"
 *   courier.tracking_id_length = 13
 *
 * A scanned value like "EG3508984YW5N" fails length and doesn't
 * end with "IN" — `validateTrackingId` returns { ok: false,
 * reason: "..." }. The scanner shows a red ✗ and keeps scanning.
 */
import type { Courier } from "./api";

export type ValidationResult = {
  ok: boolean;
  reason?: string;       // human-readable when !ok
  normalised?: string;   // trimmed + uppercased when ok
};

/**
 * Validate a raw scanned / typed tracking ID against a courier's
 * configured format rules. Returns ok:true when no rules are set
 * (permissive by default) so we don't break couriers the admin
 * hasn't configured yet.
 */
export function validateTrackingId(
  raw: string,
  courier: Partial<Courier> | null | undefined,
): ValidationResult {
  const trimmed = (raw || "").trim();
  if (!trimmed) {
    return { ok: false, reason: "Tracking ID is empty." };
  }
  // Global sanity check — reject anything with whitespace in the middle
  // (most barcode misreads inject a space before the trailing IN).
  if (/\s/.test(trimmed)) {
    return { ok: false, reason: "Contains a space — likely a bad scan." };
  }
  const upper = trimmed.toUpperCase();

  if (!courier) {
    return { ok: true, normalised: upper };
  }

  const prefix = (courier.tracking_id_prefix || "").trim().toUpperCase();
  const suffix = (courier.tracking_id_suffix || "").trim().toUpperCase();
  const exactLen = Number(courier.tracking_id_length || 0) || 0;
  const minLen   = Number(courier.tracking_id_min_length || 0) || 0;
  const maxLen   = Number(courier.tracking_id_max_length || 0) || 0;
  const pattern  = (courier.tracking_id_pattern || "").trim();

  if (prefix && !upper.startsWith(prefix)) {
    return {
      ok: false,
      reason: `Should start with "${prefix}" (you got "${upper.slice(0, prefix.length)}")`,
    };
  }
  if (suffix && !upper.endsWith(suffix)) {
    return {
      ok: false,
      reason: `Should end with "${suffix}" (you got "${upper.slice(-suffix.length)}")`,
    };
  }
  if (exactLen && upper.length !== exactLen) {
    return {
      ok: false,
      reason: `Expected exactly ${exactLen} characters (got ${upper.length}).`,
    };
  }
  if (minLen && upper.length < minLen) {
    return {
      ok: false,
      reason: `Too short — minimum ${minLen} characters (got ${upper.length}).`,
    };
  }
  if (maxLen && upper.length > maxLen) {
    return {
      ok: false,
      reason: `Too long — maximum ${maxLen} characters (got ${upper.length}).`,
    };
  }
  if (pattern) {
    try {
      const re = new RegExp(pattern);
      if (!re.test(upper)) {
        return {
          ok: false,
          reason: `Doesn't match this courier's expected format.`,
        };
      }
    } catch {
      // malformed regex — silently ignore so we don't block the user.
    }
  }
  return { ok: true, normalised: upper };
}

/**
 * Given a fresh scan, try every courier in the list and return the
 * first courier whose rules accept the value. Useful when the user
 * scans before picking a courier. Returns null if no courier matches
 * (=> likely a garbled read).
 */
export function findMatchingCourier(
  raw: string,
  couriers: Partial<Courier>[],
): Partial<Courier> | null {
  for (const c of couriers) {
    const prefix = (c.tracking_id_prefix || "").trim();
    const suffix = (c.tracking_id_suffix || "").trim();
    // Only consider couriers that have SOME format rule configured —
    // otherwise every courier would "match" an unconstrained scan.
    if (!prefix && !suffix && !c.tracking_id_length) continue;
    if (validateTrackingId(raw, c).ok) return c;
  }
  return null;
}
