/**
 * /app/webhooks.tsx — Phase F3 multi-webhook management screen.
 *
 * Lists every webhook the user has configured and lets them:
 *   • Create a new webhook (Name + Event Type)
 *   • Copy / share each webhook URL
 *   • Pause (toggle enabled), rotate the secret, or delete
 *   • Drill into a single webhook to edit its name / event_type and
 *     configure the JSON-key → schema-field mapping
 *
 * Routing: /webhooks lists; /webhooks/[id] (TODO P2) detail editor.
 * For now the inline list is enough for the most common path —
 * "create a webhook, copy its URL, paste it into Dukaan/Shopify."
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet, RefreshControl,
  TextInput, ActivityIndicator, Alert, Modal, FlatList, Switch,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as Clipboard from "expo-clipboard";
import { useRouter, Stack } from "expo-router";
import PhIcon from "../components/PhIcon";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";

type EventType = { key: string; label: string; description: string };
type Webhook = {
  id: string;
  name: string;
  event_type: string;
  source_app?: string;
  secret: string;
  url: string;
  enabled: boolean;
  mapping: Record<string, string>;
  created_at: string;
  secret_rotated_at: string;
  stats: {
    total_received: number;
    total_imported: number;
    last_received_at: string;
  };
  recent_samples: { received_at: string; payload: any }[];
};

// Coloured badge per event type (kept centralised for UI consistency).
const EVENT_BADGE: Record<string, { bg: string; fg: string; emoji: string }> = {
  new_order:           { bg: "#DCFCE7", fg: "#166534", emoji: "🟢" },
  order_status_update: { bg: "#DBEAFE", fg: "#1E40AF", emoji: "🔄" },
  abandoned_order:     { bg: "#FED7AA", fg: "#9A3412", emoji: "🛒" },
  customer_created:    { bg: "#E9D5FF", fg: "#6B21A8", emoji: "👤" },
  customer_updated:    { bg: "#E9D5FF", fg: "#6B21A8", emoji: "✏️" },
  custom:              { bg: "#F1F5F9", fg: "#334155", emoji: "⚙️" },
};

export default function WebhooksScreen() {
  const router = useRouter();
  const [loading, setLoading]         = useState(true);
  const [refreshing, setRefreshing]   = useState(false);
  const [eventTypes, setEventTypes]   = useState<EventType[]>([]);
  const [webhooks, setWebhooks]       = useState<Webhook[]>([]);

  // Create-new modal state.
  const [createOpen, setCreateOpen]   = useState(false);
  const [newName, setNewName]         = useState("");
  const [newEventType, setNewEventType] = useState("new_order");
  const [newSourceApp, setNewSourceApp] = useState("");
  const [creating, setCreating]       = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, types] = await Promise.all([
        Api.listWebhooks(),
        Api.listWebhookEventTypes(),
      ]);
      setWebhooks(list.webhooks || []);
      setEventTypes(types.event_types || []);
    } catch (e: any) {
      Alert.alert("Couldn't load webhooks", e?.message || "Try again.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = () => { setRefreshing(true); load(); };

  const labelFor = (key: string) =>
    eventTypes.find((e) => e.key === key)?.label || key;

  const create = async () => {
    if (!newName.trim()) {
      Alert.alert("Name required", "Give this webhook a short name like \"Shopify\" or \"Dukaan\".");
      return;
    }
    setCreating(true);
    try {
      await Api.createWebhook({
        name:       newName.trim(),
        event_type: newEventType,
        source_app: newSourceApp || undefined,
      });
      setCreateOpen(false);
      setNewName("");
      setNewEventType("new_order");
      setNewSourceApp("");
      await load();
    } catch (e: any) {
      Alert.alert(
        "Couldn't create webhook",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setCreating(false);
    }
  };

  const copy = async (text: string) => {
    await Clipboard.setStringAsync(text);
    Alert.alert("Copied!", "URL copied to clipboard.");
  };

  const toggleEnabled = async (wh: Webhook) => {
    try {
      await Api.updateWebhook(wh.id, { enabled: !wh.enabled });
      setWebhooks((prev) =>
        prev.map((w) => (w.id === wh.id ? { ...w, enabled: !w.enabled } : w)),
      );
    } catch (e: any) {
      Alert.alert("Update failed", e?.message || "Try again.");
    }
  };

  const rotate = (wh: Webhook) => {
    Alert.alert(
      "Rotate secret?",
      `The OLD URL will stop working immediately. You'll need to update the URL inside ${wh.name} after rotating. Continue?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Rotate",
          style: "destructive",
          onPress: async () => {
            try {
              await Api.rotateWebhook(wh.id);
              await load();
              Alert.alert("Secret rotated", "Copy the new URL and update it in your store.");
            } catch (e: any) {
              Alert.alert("Rotate failed", e?.message || "Try again.");
            }
          },
        },
      ],
    );
  };

  const remove = (wh: Webhook) => {
    Alert.alert(
      "Delete this webhook?",
      `\"${wh.name}\" will stop receiving orders. This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: async () => {
            try {
              await Api.deleteWebhook(wh.id);
              await load();
            } catch (e: any) {
              Alert.alert("Delete failed", e?.message || "Try again.");
            }
          },
        },
      ],
    );
  };

  const editMapping = (wh: Webhook) => {
    // Phase F3 — route to the per-webhook mapping editor scoped to
    // this specific user_webhooks row. Each webhook gets its own
    // mapping (Dukaan's `shipping_name` ≠ Shopify's
    // `shipping_address.first_name` — and certainly ≠ a custom
    // webhook's user-defined keys).
    router.push({
      pathname: "/webhook-mapping",
      params: { wh_id: wh.id },
    } as any);
  };

  if (loading) {
    return (
      <SafeAreaView style={[styles.safe, styles.center]}>
        <ActivityIndicator size="large" color={colors.primary} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <Stack.Screen options={{ title: "Webhooks", headerShown: true }} />

      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} />
        }
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.headerRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.title}>Webhooks</Text>
            <Text style={styles.subtitle}>
              Add unlimited webhooks — one per storefront, marketplace
              or automation. Each gets a unique URL you paste into the
              source app.
            </Text>
          </View>
          <TouchableOpacity
            testID="btn-create-webhook"
            style={styles.addBtn}
            onPress={() => { setNewName(""); setNewEventType("new_order"); setCreateOpen(true); }}
            activeOpacity={0.85}
          >
            <PhIcon name="add" size={18} color="#fff" />
            <Text style={styles.addBtnText}>New</Text>
          </TouchableOpacity>
        </View>

        {webhooks.length === 0 ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyEmoji}>🔌</Text>
            <Text style={styles.emptyTitle}>No webhooks yet</Text>
            <Text style={styles.emptySub}>
              Tap "New" to create your first webhook. We'll generate a
              unique URL — paste it into Dukaan / Shopify / Zapier and
              orders flow straight into your Pending Orders inbox.
            </Text>
          </View>
        ) : null}

        {webhooks.map((wh) => {
          const badge = EVENT_BADGE[wh.event_type] || EVENT_BADGE.custom;
          return (
            <View
              key={wh.id}
              style={[styles.card, !wh.enabled && styles.cardPaused]}
            >
              <View style={styles.cardHeader}>
                <View style={{ flex: 1, gap: 4 }}>
                  <Text style={styles.cardName} numberOfLines={1}>
                    {wh.name || "Untitled webhook"}
                  </Text>
                  <View style={styles.badgeRow}>
                    <View
                      style={[
                        styles.eventBadge,
                        { backgroundColor: badge.bg },
                      ]}
                    >
                      <Text style={[styles.eventBadgeTxt, { color: badge.fg }]}>
                        {badge.emoji} {labelFor(wh.event_type)}
                      </Text>
                    </View>
                    {!wh.enabled ? (
                      <View style={[styles.eventBadge, { backgroundColor: "#FEE2E2" }]}>
                        <Text style={[styles.eventBadgeTxt, { color: "#991B1B" }]}>
                          ⏸ Paused
                        </Text>
                      </View>
                    ) : null}
                    {wh.source_app ? (
                      <View style={[styles.eventBadge, { backgroundColor: "#E0E7FF" }]}>
                        <Text style={[styles.eventBadgeTxt, { color: "#3730A3" }]}>
                          🏷 {wh.source_app.charAt(0).toUpperCase() + wh.source_app.slice(1)}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                </View>
                <Switch
                  value={wh.enabled}
                  onValueChange={() => toggleEnabled(wh)}
                  thumbColor={wh.enabled ? colors.primary : "#fff"}
                  trackColor={{ false: "#CBD5E1", true: "#FFD9B0" }}
                />
              </View>

              <View style={styles.urlBox}>
                <Text style={styles.urlText} numberOfLines={2} selectable>
                  {wh.url}
                </Text>
                <TouchableOpacity
                  style={styles.copyBtn}
                  onPress={() => copy(wh.url)}
                  activeOpacity={0.8}
                >
                  <PhIcon name="copy" size={16} color={colors.primary} />
                  <Text style={styles.copyBtnTxt}>Copy</Text>
                </TouchableOpacity>
              </View>

              <View style={styles.statsRow}>
                <View style={styles.statItem}>
                  <Text style={styles.statValue}>{wh.stats?.total_received ?? 0}</Text>
                  <Text style={styles.statLabel}>Received</Text>
                </View>
                <View style={styles.statItem}>
                  <Text style={styles.statValue}>{wh.stats?.total_imported ?? 0}</Text>
                  <Text style={styles.statLabel}>Imported</Text>
                </View>
                <View style={styles.statItem}>
                  <Text style={styles.statValue}>
                    {Object.keys(wh.mapping || {}).length}
                  </Text>
                  <Text style={styles.statLabel}>Keys mapped</Text>
                </View>
              </View>

              <View style={styles.actionsRow}>
                <TouchableOpacity
                  style={styles.actionBtn}
                  onPress={() => editMapping(wh)}
                  activeOpacity={0.8}
                >
                  <PhIcon name="settings" size={14} color="#475569" />
                  <Text style={styles.actionBtnTxt}>Mapping</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.actionBtn}
                  onPress={() => rotate(wh)}
                  activeOpacity={0.8}
                >
                  <PhIcon name="refresh" size={14} color="#475569" />
                  <Text style={styles.actionBtnTxt}>Rotate</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.actionBtn, styles.actionBtnDanger]}
                  onPress={() => remove(wh)}
                  activeOpacity={0.8}
                >
                  <PhIcon name="delete" size={14} color="#DC2626" />
                  <Text style={[styles.actionBtnTxt, { color: "#DC2626" }]}>Delete</Text>
                </TouchableOpacity>
              </View>
            </View>
          );
        })}
      </ScrollView>

      {/* ── Create New Webhook modal ────────────────────────────── */}
      <Modal
        visible={createOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setCreateOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalSheet}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>New Webhook</Text>
              <TouchableOpacity
                onPress={() => setCreateOpen(false)}
                hitSlop={{ top: 8, right: 8, bottom: 8, left: 8 }}
              >
                <PhIcon name="close" size={20} color="#475569" />
              </TouchableOpacity>
            </View>

            <Text style={styles.label}>
              Name <Text style={{ color: "#DC2626" }}>*</Text>
            </Text>
            <TextInput
              testID="new-webhook-name"
              value={newName}
              onChangeText={setNewName}
              placeholder="e.g. Shopify, Dukaan, Meesho"
              maxLength={32}
              style={styles.input}
              placeholderTextColor="#94A3B8"
              autoFocus
            />
            <Text style={styles.hint}>
              Shown as the source badge on every order received from this webhook.
            </Text>

            <Text style={[styles.label, { marginTop: 14 }]}>
              Source App
            </Text>
            <View style={styles.sourceRow}>
              {[
                { key: "",           label: "None" },
                { key: "shopify",    label: "Shopify" },
                { key: "dukaan",     label: "Dukaan" },
                { key: "meesho",     label: "Meesho" },
                { key: "woocommerce",label: "Woo" },
                { key: "other",      label: "Other" },
              ].map((opt) => {
                const sel = newSourceApp === opt.key;
                return (
                  <TouchableOpacity
                    key={opt.key || "_none"}
                    testID={`source-app-${opt.key || "none"}`}
                    onPress={() => setNewSourceApp(opt.key)}
                    style={[
                      styles.sourceChip,
                      sel && styles.sourceChipSel,
                    ]}
                    activeOpacity={0.75}
                  >
                    <Text
                      style={[
                        styles.sourceChipTxt,
                        sel && styles.sourceChipTxtSel,
                      ]}
                    >
                      {opt.label}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>
            <Text style={styles.hint}>
              Optional. Tagging the source app keeps order-IDs from
              Shopify/Dukaan/etc. from colliding when you receive
              status updates.
            </Text>

            <Text style={[styles.label, { marginTop: 14 }]}>
              Event Type <Text style={{ color: "#DC2626" }}>*</Text>
            </Text>
            <FlatList
              data={eventTypes}
              keyExtractor={(item) => item.key}
              keyboardShouldPersistTaps="handled"
              style={{ maxHeight: 320 }}
              ItemSeparatorComponent={() => <View style={styles.sep} />}
              renderItem={({ item }) => {
                const sel    = item.key === newEventType;
                const badge  = EVENT_BADGE[item.key] || EVENT_BADGE.custom;
                return (
                  <TouchableOpacity
                    testID={`event-type-${item.key}`}
                    style={[styles.evtRow, sel && styles.evtRowSel]}
                    onPress={() => setNewEventType(item.key)}
                    activeOpacity={0.7}
                  >
                    <View
                      style={[
                        styles.evtBadge,
                        { backgroundColor: badge.bg },
                      ]}
                    >
                      <Text style={[styles.evtBadgeTxt, { color: badge.fg }]}>
                        {badge.emoji}
                      </Text>
                    </View>
                    <View style={{ flex: 1 }}>
                      <Text style={styles.evtLabel}>{item.label}</Text>
                      <Text style={styles.evtDesc} numberOfLines={2}>
                        {item.description}
                      </Text>
                    </View>
                    {sel ? (
                      <PhIcon name="checkmark" size={18} color={colors.primary} />
                    ) : null}
                  </TouchableOpacity>
                );
              }}
            />

            <TouchableOpacity
              testID="confirm-create-webhook"
              style={[styles.primaryBtn, creating && { opacity: 0.5 }]}
              disabled={creating}
              onPress={create}
              activeOpacity={0.85}
            >
              {creating ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryBtnTxt}>Create webhook</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:     { flex: 1, backgroundColor: colors.background },
  center:   { alignItems: "center", justifyContent: "center" },
  scroll:   { padding: 16, paddingBottom: 30, gap: 14 },
  headerRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12,
    marginBottom: 4,
  },
  title:    { fontSize: 22, fontWeight: "800", color: colors.text },
  subtitle: { fontSize: 13, color: colors.textMuted, marginTop: 4, lineHeight: 18 },
  addBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 14, paddingVertical: 10,
    borderRadius: 10,
    marginTop: 4,
  },
  addBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },

  emptyCard: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderRadius: 16,
    padding: 28,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
    marginTop: 8,
  },
  emptyEmoji: { fontSize: 42, marginBottom: 6 },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: colors.text, marginBottom: 4 },
  emptySub:   { fontSize: 13, color: colors.textMuted, textAlign: "center", lineHeight: 19 },

  card: {
    backgroundColor: colors.surface,
    borderRadius: 14,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    padding: 14,
    gap: 10,
  },
  cardPaused: { opacity: 0.6 },
  cardHeader: { flexDirection: "row", alignItems: "center", gap: 10 },
  cardName: { fontSize: 16, fontWeight: "800", color: colors.text },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  eventBadge: {
    paddingHorizontal: 10, paddingVertical: 4,
    borderRadius: 999, alignSelf: "flex-start",
  },
  eventBadgeTxt: { fontSize: 11, fontWeight: "800" },
  urlBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#F8FAFC",
    borderWidth: 1, borderColor: "#E2E8F0",
    borderRadius: 10,
    paddingHorizontal: 10, paddingVertical: 8,
  },
  urlText: { flex: 1, fontSize: 11, color: "#475569", fontFamily: "monospace" },
  copyBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#FFF7ED",
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 8,
  },
  copyBtnTxt: { color: colors.primary, fontWeight: "800", fontSize: 12 },

  statsRow: {
    flexDirection: "row",
    backgroundColor: "#F8FAFC",
    borderRadius: 10,
    paddingVertical: 10,
  },
  statItem:  { flex: 1, alignItems: "center" },
  statValue: { fontSize: 17, fontWeight: "800", color: colors.text },
  statLabel: { fontSize: 11, color: colors.textMuted, marginTop: 2 },

  actionsRow: { flexDirection: "row", gap: 8 },
  actionBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 4,
    backgroundColor: "#F1F5F9",
    paddingVertical: 9,
    borderRadius: 8,
  },
  actionBtnTxt: { fontSize: 12, fontWeight: "700", color: "#475569" },
  actionBtnDanger: { backgroundColor: "#FEE2E2" },

  // Modal
  modalBackdrop: { flex: 1, backgroundColor: "rgba(15,23,42,0.45)", justifyContent: "flex-end" },
  modalSheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingTop: 14, paddingHorizontal: 16, paddingBottom: 22,
    maxHeight: "92%",
  },
  modalHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", marginBottom: 12,
  },
  modalTitle: { fontSize: 18, fontWeight: "800", color: colors.text },
  label: { fontSize: 12, fontWeight: "800", color: colors.text, marginBottom: 6, letterSpacing: 0.4 },
  input: {
    backgroundColor: "#fff",
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12, height: 46,
    fontSize: 15, color: colors.text,
  },
  hint: { fontSize: 11, color: colors.textMuted, marginTop: 4, lineHeight: 16 },
  sep:  { height: 1, backgroundColor: "#F1F5F9" },
  sourceRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 2,
  },
  sourceChip: {
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 20,
    flexShrink: 0,
    borderWidth: 1.5, borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  sourceChipSel: {
    borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  sourceChipTxt: { fontSize: 12, fontWeight: "700", color: "#475569" },
  sourceChipTxtSel: { color: colors.primary },
  evtRow: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingVertical: 12, paddingHorizontal: 6,
  },
  evtRowSel: { backgroundColor: "#FFF7ED", borderRadius: 10 },
  evtBadge: {
    width: 36, height: 36, borderRadius: 18,
    alignItems: "center", justifyContent: "center",
  },
  evtBadgeTxt: { fontSize: 18 },
  evtLabel: { fontSize: 14, fontWeight: "800", color: colors.text },
  evtDesc:  { fontSize: 12, color: colors.textMuted, marginTop: 2, lineHeight: 16 },
  primaryBtn: {
    marginTop: 16, height: 48, borderRadius: 12,
    backgroundColor: colors.primary,
    alignItems: "center", justifyContent: "center",
  },
  primaryBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 15 },
});
