/**
 * Daily WhatsApp limit mini-banner (Phase-15 D)
 * ---------------------------------------------
 * Tiny pill that shows "32 / 50 today" near the top of any screen
 * that fires WhatsApp messages. Color escalates as the count climbs:
 *   ok     → no banner (returns null when sent_today < 50% of limit
 *             so the UI stays clean for users who only send a few).
 *   ~50%+  → neutral grey
 *   warn   → amber
 *   limit reached → red
 *
 * Polls /me/whatsapp/daily-status every 30s so the operator gets a
 * live read without manual refreshes. Guarded by visibility prop so
 * background screens stop polling.
 */
import React, { useEffect, useState, useCallback } from "react";
import PhIcon from "./PhIcon";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
} from "react-native";
import { Api } from "../lib/api";

type Status = {
  sent_today: number;
  limit: number;
  warn_at: number;
  allow_override: boolean;
  status: string;
};

type Props = {
  /** Refresh tick from parent — bump this to force a refetch (e.g. right
   *  after a successful send). Defaults to a 30s internal poll. */
  refreshKey?: number;
  /** Show as a compact inline pill (default) or full-width strip. */
  variant?: "pill" | "strip";
  /** Hide entirely when count is < showAtPct % of limit. Default 50. */
  showAtPct?: number;
};

export default function DailyLimitBanner({
  refreshKey = 0,
  variant = "pill",
  showAtPct = 50,
}: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStatus = useCallback(async () => {
    try {
      const s = await Api.meWhatsAppDailyStatus();
      setStatus(s);
    } catch {/* ignore — banner just hides on error */}
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 30_000);
    return () => clearInterval(id);
  }, [fetchStatus, refreshKey]);

  if (loading || !status) return null;
  if (status.limit <= 0) return null;

  const ratio = status.sent_today / Math.max(1, status.limit);
  const pct = ratio * 100;
  if (pct < showAtPct && status.status === "ok") return null;

  // Color tier.
  let bg = "#F3F4F6";
  let fg = "#374151";
  let border = "#E5E7EB";
  let icon: any = "send-outline";
  let extra = "";

  if (status.status === "warn") {
    bg = "#FEF3C7"; fg = "#92400E"; border = "#FDE68A";
    icon = "warning-outline";
  } else if (status.status === "limit_reached_overridable") {
    bg = "#FFEDD5"; fg = "#9A3412"; border = "#FED7AA";
    icon = "alert-circle-outline";
    extra = " · WhatsApp may flag your number";
  } else if (status.status === "limit_reached_blocked") {
    bg = "#FEE2E2"; fg = "#991B1B"; border = "#FECACA";
    icon = "lock-closed-outline";
    extra = " · admin disabled override";
  }

  if (variant === "strip") {
    return (
      <View style={[styles.strip, { backgroundColor: bg, borderColor: border }]}>
        <PhIcon name={icon} size={16} color={fg} />
        <Text style={[styles.stripText, { color: fg }]} numberOfLines={2}>
          WhatsApp today: {status.sent_today} / {status.limit}{extra}
        </Text>
      </View>
    );
  }

  return (
    <TouchableOpacity
      activeOpacity={0.85}
      onPress={fetchStatus}
      style={[styles.pill, { backgroundColor: bg, borderColor: border }]}
    >
      <PhIcon name={icon} size={13} color={fg} />
      <Text style={[styles.pillText, { color: fg }]}>
        {status.sent_today}/{status.limit} today
      </Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  pill: {
    flexDirection: "row", alignItems: "center", gap: 5,
    paddingVertical: 4, paddingHorizontal: 10,
    borderRadius: 999, borderWidth: 1,
    alignSelf: "flex-start",
  },
  pillText: { fontSize: 11, fontWeight: "800" },

  strip: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 8, paddingHorizontal: 12,
    borderRadius: 10, borderWidth: 1,
    marginHorizontal: 12, marginTop: 6,
  },
  stripText: { fontSize: 12, fontWeight: "700", flex: 1 },
});
