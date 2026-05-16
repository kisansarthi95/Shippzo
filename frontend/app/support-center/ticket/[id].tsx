/**
 * Phase-21 — Support Ticket Detail screen.
 *
 * Layout:
 *   • Header card — title, status pill, category + priority + open
 *     date metadata, optional "Close ticket" button (for resolved
 *     tickets, hidden after close).
 *   • Conversation thread — chat-style bubbles. User on right (orange
 *     primary), admin on left (white card).
 *   • Composer bar at bottom — multiline input + send button. Sends
 *     POST /support/tickets/{id}/reply and appends the new bubble
 *     optimistically while the request is in-flight.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import PhIcon from "../../../components/PhIcon";
import { colors } from "../../../lib/theme";
import { Api, SupportMessage, SupportTicket } from "../../../lib/api";
import { useAuth } from "../../../lib/auth";
import { useFocusEffect } from "expo-router";

const STATUS_LABEL: Record<SupportTicket["status"], string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
};

const STATUS_BG: Record<SupportTicket["status"], string> = {
  open: "#FFEDD5",
  in_progress: "#DBEAFE",
  resolved: "#DCFCE7",
  closed: "#E2E8F0",
};

const STATUS_FG: Record<SupportTicket["status"], string> = {
  open: "#9A3412",
  in_progress: "#1D4ED8",
  resolved: "#15803D",
  closed: "#475569",
};

function relTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString();
}

export default function TicketDetail() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { user } = useAuth();
  const isAdmin = !!(user as any)?.is_admin;

  const [ticket, setTicket] = useState<SupportTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [savingStatus, setSavingStatus] = useState(false);
  const scrollRef = useRef<ScrollView | null>(null);
  const pollRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastSeenCountRef = useRef<number>(0);

  const load = useCallback(async (silent = false) => {
    try {
      const t = await Api.supportGetTicket(String(id));
      // Skip the state update on silent polls when nothing changed
      // so we don't re-render or re-scroll unnecessarily.
      const incoming = t.messages?.length || 0;
      if (silent && incoming === lastSeenCountRef.current) {
        return;
      }
      lastSeenCountRef.current = incoming;
      setTicket(t);
    } catch (e: any) {
      if (!silent) {
        Alert.alert("Failed", e?.response?.data?.detail || "Could not load ticket.");
      }
    } finally {
      if (!silent) setLoading(false);
    }
  }, [id]);

  useEffect(() => { load(false); }, [load]);

  // Poll every 8 seconds while focused — picks up admin replies (or
  // user replies if admin is viewing) within a few seconds without
  // requiring a WebSocket. Quiet (no spinner) on the silent path.
  useFocusEffect(
    useCallback(() => {
      pollRef.current = setInterval(() => { load(true); }, 8_000);
      return () => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
      };
    }, [load]),
  );

  // Admin status changer — used from the status pills in the header
  // when an admin is viewing the ticket. Optimistic UI: flip locally
  // first, rollback on failure.
  const setTicketStatus = useCallback(async (next: SupportTicket["status"]) => {
    if (!ticket || ticket.status === next) return;
    const prev = ticket.status;
    setTicket((t) => (t ? { ...t, status: next } : t));
    setSavingStatus(true);
    try {
      await Api.adminSetSupportStatus(String(id), next);
    } catch (e: any) {
      setTicket((t) => (t ? { ...t, status: prev } : t));
      Alert.alert("Failed", e?.response?.data?.detail || "Could not update status.");
    } finally {
      setSavingStatus(false);
    }
  }, [ticket, id]);

  // Auto-scroll the thread to the bottom whenever new messages appear.
  useEffect(() => {
    if (!ticket?.messages?.length) return;
    requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
  }, [ticket?.messages?.length]);

  const send = async () => {
    const body = reply.trim();
    if (!body) return;
    setSending(true);
    // Optimistic bubble — appended immediately so the UI feels instant.
    // The role mirrors the signed-in user so admin replies render as
    // "admin" bubbles (left side) and user replies render as "user"
    // (right side, primary colour).
    const optimistic: SupportMessage = {
      id: `optimistic-${Date.now()}`,
      author_id: "me",
      author_name: isAdmin ? "Support" : "You",
      author_role: isAdmin ? "admin" : "user",
      body,
      created_at: new Date().toISOString(),
    };
    setTicket((prev) =>
      prev ? { ...prev, messages: [...(prev.messages || []), optimistic] } : prev,
    );
    setReply("");
    try {
      const r = await Api.supportReply(String(id), body);
      // Swap the optimistic bubble for the real one (so id is durable).
      setTicket((prev) => {
        if (!prev) return prev;
        const next = (prev.messages || []).map((m) =>
          m.id === optimistic.id ? r.message : m,
        );
        // Bump cached message count so the next silent poll doesn't
        // re-trigger a re-render with the same count.
        lastSeenCountRef.current = next.length;
        return { ...prev, messages: next };
      });
    } catch (e: any) {
      // Rollback the optimistic bubble on failure so we don't lie to the user.
      setTicket((prev) =>
        prev
          ? {
              ...prev,
              messages: (prev.messages || []).filter((m) => m.id !== optimistic.id),
            }
          : prev,
      );
      Alert.alert("Failed", e?.response?.data?.detail || "Could not send your reply.");
    } finally {
      setSending(false);
    }
  };

  const closeTicket = () => {
    Alert.alert(
      "Close this ticket?",
      "You can always create a new request later if you need more help.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Close",
          style: "destructive",
          onPress: async () => {
            try {
              await Api.supportCloseTicket(String(id));
              await load(false);
            } catch (e: any) {
              Alert.alert("Failed", e?.response?.data?.detail || "Try again.");
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <View style={[styles.root, { alignItems: "center", justifyContent: "center" }]}>
        <Stack.Screen options={{ title: "Request", headerShadowVisible: false }} />
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }
  if (!ticket) return null;

  const status = ticket.status;
  const isClosed = status === "closed";

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      keyboardVerticalOffset={Platform.OS === "ios" ? 88 : 0}
      style={{ flex: 1, backgroundColor: "#F4F5F7" }}
    >
      <Stack.Screen
        options={{
          title: `Request #${ticket.id.slice(0, 8)}`,
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <ScrollView
        ref={scrollRef}
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 12, paddingBottom: 16 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* Header card */}
        <View style={styles.headCard}>
          <View style={styles.headRow}>
            <View style={[styles.statusPill, { backgroundColor: STATUS_BG[status] }]}>
              <Text style={[styles.statusPillTxt, { color: STATUS_FG[status] }]}>
                {STATUS_LABEL[status]}
              </Text>
            </View>
            <Text style={styles.metaTxt}>
              {ticket.category} · opened {relTime(ticket.created_at)}
            </Text>
          </View>
          <Text style={styles.headTitle}>{ticket.title}</Text>
          {/* Admin viewer info — show the customer email + reply-as
              badge so the owner knows whose ticket they're on. */}
          {isAdmin ? (
            <Text style={[styles.metaTxt, { marginTop: 4 }]}>
              👤 {ticket.user_email || "Unknown user"} · Replying as Admin
            </Text>
          ) : null}
          {!isClosed && status === "resolved" ? (
            <TouchableOpacity
              testID="ticket-close-btn"
              onPress={closeTicket}
              style={styles.closeBtn}
            >
              <PhIcon name="checkmark-circle" size={14} color={colors.primary} />
              <Text style={styles.closeBtnTxt}>Close this ticket</Text>
            </TouchableOpacity>
          ) : null}
        </View>

        {/* Admin status control bar — flip ticket through the
            workflow with a single tap. Hidden for regular users. */}
        {isAdmin && !isClosed ? (
          <View style={styles.adminBar}>
            <Text style={styles.adminBarLbl}>Status:</Text>
            {(["open", "in_progress", "resolved", "closed"] as const).map((s) => {
              const active = status === s;
              return (
                <TouchableOpacity
                  key={s}
                  onPress={() => setTicketStatus(s)}
                  disabled={savingStatus || active}
                  activeOpacity={0.85}
                  style={[styles.adminChip, active && styles.adminChipActive]}
                >
                  <Text style={[styles.adminChipTxt, active && styles.adminChipTxtActive]}>
                    {STATUS_LABEL[s]}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        ) : null}

        {/* Messages thread */}
        {(ticket.messages || []).map((m) => {
          // "mine" is the bubble for the signed-in user — admin
          // messages on the right when admin is viewing, user
          // messages on the right when the customer is viewing.
          const mine = isAdmin
            ? m.author_role === "admin"
            : m.author_role === "user";
          return (
            <View
              key={m.id}
              style={[
                styles.bubbleWrap,
                { alignItems: mine ? "flex-end" : "flex-start" },
              ]}
            >
              <View
                style={[
                  styles.bubble,
                  mine ? styles.bubbleMine : styles.bubbleOther,
                ]}
              >
                <Text style={[styles.bubbleAuthor, mine && { color: "rgba(255,255,255,0.85)" }]}>
                  {mine ? "You" : (m.author_name || (m.author_role === "admin" ? "Support" : "User"))}
                </Text>
                <Text style={[styles.bubbleBody, mine && { color: "#fff" }]}>
                  {m.body}
                </Text>
                <Text style={[styles.bubbleTime, mine && { color: "rgba(255,255,255,0.7)" }]}>
                  {relTime(m.created_at)}
                </Text>
              </View>
            </View>
          );
        })}
      </ScrollView>

      {/* Composer */}
      {isClosed ? (
        <View style={[styles.composerClosed, { paddingBottom: insets.bottom + 10 }]}>
          <Text style={styles.composerClosedTxt}>This ticket is closed.</Text>
          <TouchableOpacity
            onPress={() => router.push("/support-center/create" as any)}
            style={styles.composerClosedBtn}
          >
            <Text style={styles.composerClosedBtnTxt}>Create new request</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={[styles.composer, { paddingBottom: insets.bottom + 8 }]}>
          <TextInput
            testID="ticket-reply-input"
            value={reply}
            onChangeText={setReply}
            placeholder="Type your reply…"
            placeholderTextColor="#9CA3AF"
            style={styles.composerInput}
            multiline
            maxLength={5000}
          />
          <TouchableOpacity
            testID="ticket-reply-send"
            onPress={send}
            disabled={sending || !reply.trim()}
            activeOpacity={0.85}
            style={[
              styles.composerSend,
              (!reply.trim() || sending) && { opacity: 0.45 },
            ]}
          >
            <PhIcon name="send" size={16} color="#fff" />
          </TouchableOpacity>
        </View>
      )}
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  headCard: {
    backgroundColor: "#fff", borderRadius: 14,
    padding: 14, marginBottom: 10,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.04)", elevation: 1,
  },
  headRow: { flexDirection: "row", alignItems: "center", gap: 10 },

  // Admin status bar — only rendered when an admin is viewing.
  adminBar: {
    flexDirection: "row", flexWrap: "wrap", alignItems: "center",
    backgroundColor: "#FEF3C7", borderRadius: 12,
    paddingHorizontal: 10, paddingVertical: 8, marginBottom: 10, gap: 6,
    borderWidth: 1, borderColor: "#FDE68A",
  },
  adminBarLbl: { fontSize: 11.5, fontWeight: "800", color: "#92400E", marginRight: 2 },
  adminChip: {
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 999, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#FDE68A",
  },
  adminChipActive: {
    backgroundColor: "#92400E", borderColor: "#92400E",
  },
  adminChipTxt:       { fontSize: 11, fontWeight: "700", color: "#92400E" },
  adminChipTxtActive: { color: "#fff" },
  statusPill: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  statusPillTxt: { fontSize: 10.5, fontWeight: "900", letterSpacing: 0.3 },
  metaTxt: { fontSize: 12, color: "#94A3B8", flex: 1 },
  headTitle: { fontSize: 16, fontWeight: "800", color: "#0F172A", marginTop: 10 },
  closeBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    alignSelf: "flex-start", marginTop: 12,
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 8, backgroundColor: "#FFF7ED",
  },
  closeBtnTxt: { color: colors.primary, fontSize: 12, fontWeight: "800" },

  bubbleWrap: { marginVertical: 4 },
  bubble: { maxWidth: "82%", borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10 },
  bubbleMine: {
    backgroundColor: colors.primary,
    borderTopRightRadius: 4,
  },
  bubbleOther: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 4,
    boxShadow: "0px 1px 3px rgba(0,0,0,0.04)", elevation: 1,
  },
  bubbleAuthor: { fontSize: 11, fontWeight: "800", color: "#475569", marginBottom: 4 },
  bubbleBody: { fontSize: 14, color: "#0F172A", lineHeight: 19 },
  bubbleTime: { fontSize: 10, color: "#94A3B8", marginTop: 6 },

  composer: {
    flexDirection: "row", alignItems: "flex-end", gap: 8,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 12, paddingTop: 8,
  },
  composerInput: {
    flex: 1, maxHeight: 120, minHeight: 40,
    backgroundColor: "#F4F5F7", borderRadius: 14,
    paddingHorizontal: 12, paddingTop: 10, paddingBottom: 10,
    fontSize: 14, color: "#0F172A",
  },
  composerSend: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: colors.primary,
    alignItems: "center", justifyContent: "center",
  },

  composerClosed: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 16, paddingTop: 12,
  },
  composerClosedTxt: { flex: 1, color: "#64748B", fontSize: 13, fontWeight: "600" },
  composerClosedBtn: {
    backgroundColor: colors.primary,
    paddingHorizontal: 14, paddingVertical: 10, borderRadius: 10,
  },
  composerClosedBtnTxt: { color: "#fff", fontSize: 12.5, fontWeight: "800" },
});
