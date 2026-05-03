/**
 * WhatsApp Guard (Phase-15 D)
 * ---------------------------
 * Centralised pre-send safety net for WhatsApp share intents. Used
 * by every screen that opens wa.me / whatsapp:// for a customer
 * message (scanner, dispatch confirmation, delivery confirmation,
 * feedback request, manual ship-out shipments tab, etc).
 *
 * Behaviour at the daily limit:
 *   - admin "Allow override" = OFF      → hard block (Alert "limit reached")
 *   - admin "Allow override" = ON       → confirm dialog "WhatsApp may
 *                                          flag your number, send anyway?"
 *
 * On every successful send we call the backend daily-increment endpoint
 * so the per-user counter stays in sync across devices/sessions.
 */
import { Alert, Linking, Platform } from "react-native";
import { Api } from "./api";

export type WhatsAppGuardResult =
  | { ok: true; sent_today: number; limit: number }
  | { ok: false; reason: "blocked" | "user_cancelled" | "error"; detail?: string };

const E164_PREFIX = "91";

const buildE164 = (phone: string): string => {
  const digits = String(phone || "").replace(/\D/g, "");
  if (!digits) return "";
  if (digits.length === 10) return `${E164_PREFIX}${digits}`;
  if (digits.startsWith("91") && digits.length === 12) return digits;
  return digits;
};

/**
 * The actual share intent — used internally by `requestWhatsAppSend`,
 * but exported in case callers want to bypass the guard for non-customer
 * messages (e.g. forwarding a coupon to a friend).
 */
export const openWhatsAppShare = async (
  phone: string,
  message: string,
): Promise<boolean> => {
  const e164 = buildE164(phone);
  if (!e164) {
    Alert.alert("Invalid phone", "Couldn't open WhatsApp — phone is empty.");
    return false;
  }

  // Prefer the native scheme on mobile; fall back to wa.me which works
  // on web and on devices without the native app.
  const native = `whatsapp://send?phone=${e164}&text=${encodeURIComponent(message)}`;
  const web = `https://wa.me/${e164}?text=${encodeURIComponent(message)}`;

  try {
    if (Platform.OS === "web") {
      await Linking.openURL(web);
      return true;
    }
    const can = await Linking.canOpenURL(native);
    if (can) {
      await Linking.openURL(native);
      return true;
    }
    await Linking.openURL(web);
    return true;
  } catch {
    try {
      await Linking.openURL(web);
      return true;
    } catch (e: any) {
      Alert.alert("WhatsApp error", String(e?.message || "Could not open WhatsApp."));
      return false;
    }
  }
};

const userConfirm = (
  title: string,
  message: string,
  positiveLabel: string,
): Promise<boolean> => {
  return new Promise((resolve) => {
    Alert.alert(
      title,
      message,
      [
        { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
        { text: positiveLabel, style: "destructive", onPress: () => resolve(true) },
      ],
      { cancelable: true, onDismiss: () => resolve(false) },
    );
  });
};

/**
 * Preflight for BULK WhatsApp sends (dispatch / delivery confirmation
 * loops). Asks the user ONCE whether to proceed if the batch would
 * cross the daily limit, then returns a `force` flag the caller can
 * pass to `Api.meWhatsAppDailyIncrement(force)` for every iteration.
 *
 * Returns:
 *   { ok: true, force: boolean, status }  → caller should run the loop
 *   { ok: false, reason }                  → don't send anything
 *
 * The caller is responsible for calling `openWhatsAppShare` +
 * `Api.meWhatsAppDailyIncrement(force)` for each parcel in the batch.
 */
export const preflightBatchWhatsApp = async (
  count: number,
  opts?: { batchLabel?: string },
): Promise<
  | { ok: true; force: boolean; sent_today: number; limit: number }
  | { ok: false; reason: "blocked" | "user_cancelled" | "error"; detail?: string }
> => {
  if (count <= 0) {
    return { ok: false, reason: "user_cancelled", detail: "empty batch" };
  }
  let status;
  try {
    status = await Api.meWhatsAppDailyStatus();
  } catch {
    // If the status endpoint is down, allow the batch (counters get
    // updated as each iteration calls increment server-side).
    return { ok: true, force: false, sent_today: 0, limit: 0 };
  }

  const limit = status.limit;
  const remaining = Math.max(0, limit - status.sent_today);
  const willCross = count > remaining;

  // Hard block.
  if (status.status === "limit_reached_blocked") {
    Alert.alert(
      "Daily WhatsApp limit reached",
      `Already sent ${status.sent_today}/${limit} today. Admin has ` +
        `disabled override — none of the ${count} message(s) will be sent. ` +
        `Try again tomorrow.`,
      [{ text: "OK", style: "cancel" }],
    );
    return { ok: false, reason: "blocked", detail: "limit_reached_blocked" };
  }

  // Soft limit reached — ask once for the entire batch.
  if (status.status === "limit_reached_overridable") {
    const confirmed = await new Promise<boolean>((resolve) => {
      Alert.alert(
        "Daily limit reached",
        `Already sent ${status.sent_today}/${limit} today. Sending these ` +
          `${count} more may flag your WhatsApp number per their policy.\n\n` +
          `Send all ${count} anyway?`,
        [
          { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
          { text: "Send All", style: "destructive", onPress: () => resolve(true) },
        ],
        { cancelable: true, onDismiss: () => resolve(false) },
      );
    });
    if (!confirmed) return { ok: false, reason: "user_cancelled" };
    return { ok: true, force: true, sent_today: status.sent_today, limit };
  }

  // Not at limit yet but the batch will cross it.
  if (willCross && status.allow_override) {
    const confirmed = await new Promise<boolean>((resolve) => {
      Alert.alert(
        "Batch will cross daily limit",
        `You're about to send ${count} message(s). After ${remaining}, ` +
          `you'll be past today's limit of ${limit}. WhatsApp may flag ` +
          `your number for the rest. Continue?`,
        [
          { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
          { text: "Send All", style: "destructive", onPress: () => resolve(true) },
        ],
        { cancelable: true, onDismiss: () => resolve(false) },
      );
    });
    if (!confirmed) return { ok: false, reason: "user_cancelled" };
    // The caller should still pass force=true ONLY for messages past
    // the limit. Easiest contract: call increment() with force=false
    // up to limit, then force=true after — but that's complex. Simpler:
    // pass force=true to ALL of them; the backend's increment endpoint
    // accepts force=true even when not yet at limit (it's a no-op then).
    return { ok: true, force: true, sent_today: status.sent_today, limit };
  }

  if (willCross && !status.allow_override) {
    Alert.alert(
      "Batch exceeds daily limit",
      `You can only send ${remaining} more message(s) today (limit ${limit}). ` +
        `Admin has disabled override. Reduce your selection or try ` +
        `again tomorrow.`,
      [{ text: "OK", style: "cancel" }],
    );
    return { ok: false, reason: "blocked", detail: "batch_exceeds_limit" };
  }

  // Plenty of headroom — no confirmation needed.
  return { ok: true, force: false, sent_today: status.sent_today, limit };
};

/**
 * Pre-flight + actually-send wrapper for SINGLE-row WhatsApp sends
 * (manual ship-out, single-row dispatch / delivery confirmation, etc).
 *
 * 1. Checks current daily-status from the backend.
 * 2. If at hard limit (override OFF) → `Alert` and return blocked.
 * 3. If at soft limit (override ON)  → confirm dialog → user can choose.
 * 4. Otherwise (or after user confirms): calls daily-increment then
 *    opens the WhatsApp share intent.
 *
 * Counter increment uses `force=true` only when the user explicitly
 * confirmed pushing past the limit, mirroring the backend contract.
 */
export const requestWhatsAppSend = async (
  phone: string,
  message: string,
  opts?: { templateLabel?: string },
): Promise<WhatsAppGuardResult> => {
  // Step 1 — read current daily status (cheap GET, no LLM cost).
  let status;
  try {
    status = await Api.meWhatsAppDailyStatus();
  } catch {
    // If status endpoint fails, don't block the user — degrade
    // gracefully and skip increment, but still open WA.
    const ok = await openWhatsAppShare(phone, message);
    return ok
      ? { ok: true, sent_today: 0, limit: 0 }
      : { ok: false, reason: "error", detail: "share intent failed" };
  }

  let force = false;

  // Step 2 — hard block (admin disabled override).
  if (status.status === "limit_reached_blocked") {
    Alert.alert(
      "Daily WhatsApp limit reached",
      `You have already sent ${status.sent_today}/${status.limit} messages today. ` +
        `Per WhatsApp's spam policy, sending more may flag or block your number.\n\n` +
        `Admin has disabled override — please retry tomorrow.`,
      [{ text: "OK", style: "cancel" }],
    );
    return { ok: false, reason: "blocked", detail: "limit_reached_blocked" };
  }

  // Step 3 — soft block (override allowed): ask the user.
  if (status.status === "limit_reached_overridable") {
    const confirmed = await userConfirm(
      "Daily limit reached",
      `You have already sent ${status.sent_today}/${status.limit} WhatsApp messages today.\n\n` +
        `Sending more may flag your number per WhatsApp's policy. ` +
        `Continue anyway?`,
      "Send Anyway",
    );
    if (!confirmed) {
      return { ok: false, reason: "user_cancelled" };
    }
    force = true;
  }

  // Step 4 — light warning (still under the hard limit).
  if (status.status === "warn") {
    // Non-blocking inline notice — we still send right away.
    // Toast-style; using a tiny Alert variant to stay portable.
    // Don't block the flow — inform asynchronously.
    setTimeout(() => {
      Alert.alert(
        "Approaching daily limit",
        `${status.sent_today}/${status.limit} messages sent today. ` +
          `Slow down to keep your WhatsApp number safe.`,
        [{ text: "OK", style: "cancel" }],
      );
    }, 0);
  }

  // Step 5 — increment counter, then fire the share intent.
  let after;
  try {
    after = await Api.meWhatsAppDailyIncrement(force);
  } catch (e: any) {
    // Only the backend can refuse here — usually because limit
    // changed mid-flight and override toggled off.
    Alert.alert(
      "Could not send",
      String(e?.response?.data?.detail || e?.message || "Daily limit hit."),
    );
    return { ok: false, reason: "blocked", detail: "increment_refused" };
  }

  const opened = await openWhatsAppShare(phone, message);
  if (!opened) {
    return { ok: false, reason: "error", detail: "share intent failed" };
  }
  return { ok: true, sent_today: after.sent_today, limit: after.limit };
};
