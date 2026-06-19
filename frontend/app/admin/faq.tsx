/**
 * Super-Admin FAQ Manager
 * -----------------------
 *
 * Phase-27 — CRUD UI for the `faq_items` collection. Lives under
 * /admin/faq and is reachable only by users whose `is_admin === true`
 * — the backend re-validates this on every request via the same
 * `_require_admin` helper the rest of /admin uses.
 *
 * Capabilities:
 *   • List EVERY FAQ row (including hidden) with category, q/a snippet,
 *     visibility toggle pill and sort-order chip.
 *   • Inline search across category / q / a.
 *   • Create new FAQ (modal).
 *   • Edit existing FAQ (same modal, prefilled).
 *   • Hide/Unhide a single FAQ with a one-tap toggle (PATCH
 *     is_visible). No need to re-save the whole row.
 *   • Delete with confirm prompt.
 *
 * No reorder UI in this first cut — sort_order can be edited inside
 * the create/edit modal manually. A drag-handle reorder will be added
 * once we have a stable react-native-draggable-flatlist setup.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";

import PhIcon from "../../components/PhIcon";
import SearchBar from "../../components/SearchBar";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";
import { useAuth } from "../../lib/auth";

// ─── Local types ────────────────────────────────────────────────────
// Mirror the Pydantic schema but with everything optional on the
// `update` shape so we can reuse the same form for create + edit.
type FAQRow = {
  id:         string;
  category:   string;
  q:          string;
  a:          string;
  sort_order: number;
  is_visible: boolean;
  created_at?: string;
  updated_at?: string;
};

type Draft = {
  id:         string;   // empty on "create" → backend auto-generates
  category:   string;
  q:          string;
  a:          string;
  sort_order: string;   // string in the form, parsed to int on save
  is_visible: boolean;
};

const EMPTY_DRAFT: Draft = {
  id: "", category: "", q: "", a: "",
  sort_order: "100", is_visible: true,
};


export default function AdminFAQScreen() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [items, setItems]         = useState<FAQRow[]>([]);
  const [loading, setLoading]     = useState<boolean>(true);
  const [refreshing, setRefreshing] = useState<boolean>(false);
  const [error, setError]         = useState<string>("");
  const [query, setQuery]         = useState<string>("");

  // Modal state — `editing` holds the row being edited; when null the
  // modal is hidden. When an empty Draft is provided, the modal acts
  // as a "create" form.
  const [modalOpen, setModalOpen] = useState<boolean>(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft]         = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving]       = useState<boolean>(false);

  // Per-row spinner state for the visibility toggle (so the rest of
  // the list stays interactive while we PATCH).
  const [togglingId, setTogglingId] = useState<string | null>(null);

  // ─── Bootstrap ────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      setError("");
      const r = await Api.adminFaqList();
      setItems(r.items || []);
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Could not load FAQs";
      setError(detail);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (!user) { router.replace("/(auth)/login" as any); return; }
    if (!user.is_admin) {
      Alert.alert("Admin only", "This screen is restricted to administrators.");
      router.back();
      return;
    }
    load();
  }, [authLoading, user, load, router]);

  // ─── Filtering ────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) =>
        it.q.toLowerCase().includes(q) ||
        it.a.toLowerCase().includes(q) ||
        it.category.toLowerCase().includes(q) ||
        it.id.toLowerCase().includes(q),
    );
  }, [items, query]);

  // ─── Open create / edit modal ────────────────────────────────────
  const openCreate = () => {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setModalOpen(true);
  };

  const openEdit = (row: FAQRow) => {
    setEditingId(row.id);
    setDraft({
      id:         row.id,
      category:   row.category,
      q:          row.q,
      a:          row.a,
      sort_order: String(row.sort_order || 100),
      is_visible: !!row.is_visible,
    });
    setModalOpen(true);
  };

  // ─── Save (create OR update) ─────────────────────────────────────
  const save = async () => {
    const cat = draft.category.trim();
    const q   = draft.q.trim();
    const a   = draft.a.trim();
    if (!cat || !q || !a) {
      Alert.alert("Missing fields", "Category, Question and Answer are all required.");
      return;
    }
    const so = Math.max(0, Math.min(99999, parseInt(draft.sort_order || "100", 10) || 100));
    setSaving(true);
    try {
      if (editingId) {
        await Api.adminFaqUpdate(editingId, {
          category:   cat,
          q,
          a,
          sort_order: so,
          is_visible: draft.is_visible,
        });
      } else {
        await Api.adminFaqCreate({
          id:         draft.id.trim() || undefined,
          category:   cat,
          q,
          a,
          sort_order: so,
          is_visible: draft.is_visible,
        });
      }
      setModalOpen(false);
      await load();
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Save failed";
      Alert.alert("Could not save", detail);
    } finally {
      setSaving(false);
    }
  };

  // ─── Per-row visibility toggle ───────────────────────────────────
  const toggleVisibility = async (row: FAQRow) => {
    setTogglingId(row.id);
    try {
      await Api.adminFaqUpdate(row.id, { is_visible: !row.is_visible });
      // Optimistic local update — saves a full re-fetch round-trip.
      setItems((prev) =>
        prev.map((p) => (p.id === row.id ? { ...p, is_visible: !p.is_visible } : p)),
      );
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || "Toggle failed";
      Alert.alert("Could not update", detail);
    } finally {
      setTogglingId(null);
    }
  };

  // ─── Delete with confirm ─────────────────────────────────────────
  const remove = (row: FAQRow) => {
    Alert.alert(
      "Delete FAQ?",
      `This permanently deletes "${row.q}". This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.adminFaqDelete(row.id);
              setItems((prev) => prev.filter((p) => p.id !== row.id));
            } catch (e: any) {
              const detail = e?.response?.data?.detail || e?.message || "Delete failed";
              Alert.alert("Could not delete", detail);
            }
          },
        },
      ],
    );
  };

  const stats = useMemo(() => {
    const visible = items.filter((i) => i.is_visible).length;
    return { total: items.length, visible, hidden: items.length - visible };
  }, [items]);

  // ─── Render ──────────────────────────────────────────────────────
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen
        options={{
          title: "Manage FAQs",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />

      {/* Stat bar + search + add button */}
      <View style={styles.toolbar}>
        <View style={styles.statsRow}>
          <View style={[styles.statChip, { backgroundColor: "#F1F5F9" }]}>
            <Text style={styles.statTxt}>Total {stats.total}</Text>
          </View>
          <View style={[styles.statChip, { backgroundColor: "#DCFCE7" }]}>
            <Text style={[styles.statTxt, { color: "#15803D" }]}>
              Visible {stats.visible}
            </Text>
          </View>
          <View style={[styles.statChip, { backgroundColor: "#FEE2E2" }]}>
            <Text style={[styles.statTxt, { color: "#991B1B" }]}>
              Hidden {stats.hidden}
            </Text>
          </View>
        </View>
        <View style={styles.searchRow}>
          <View style={{ flex: 1 }}>
            <SearchBar
              testID="admin-faq-search"
              value={query}
              onChangeText={setQuery}
              onClear={() => {/* no extra filters here */}}
              placeholder="Search by question, answer or category…"
              containerStyle={{ marginHorizontal: 0, marginTop: 0 }}
            />
          </View>
          <TouchableOpacity
            testID="admin-faq-add"
            style={styles.addBtn}
            onPress={openCreate}
            activeOpacity={0.85}
          >
            <PhIcon name="add" size={16} color="#fff" />
            <Text style={styles.addBtnTxt}>Add</Text>
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 16, paddingBottom: 80 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={colors.primary}
          />
        }
      >
        {loading ? (
          <View style={styles.center}>
            <ActivityIndicator color={colors.primary} />
            <Text style={styles.mutedTxt}>Loading FAQs…</Text>
          </View>
        ) : error ? (
          <View style={styles.center}>
            <PhIcon name="warning" size={28} color="#DC2626" />
            <Text style={styles.errTxt}>{error}</Text>
            <TouchableOpacity onPress={load} style={styles.retryBtn}>
              <Text style={styles.retryBtnTxt}>Retry</Text>
            </TouchableOpacity>
          </View>
        ) : filtered.length === 0 ? (
          <View style={styles.center}>
            <PhIcon name="search" size={28} color="#CBD5E1" />
            <Text style={styles.mutedTxt}>No FAQs match this search.</Text>
          </View>
        ) : (
          filtered.map((row) => (
            <View
              key={row.id}
              testID={`admin-faq-row-${row.id}`}
              style={[styles.card, !row.is_visible && styles.cardHidden]}
            >
              <View style={styles.cardHeader}>
                <View style={styles.catChip}>
                  <Text style={styles.catChipTxt}>{row.category}</Text>
                </View>
                <View style={styles.orderChip}>
                  <Text style={styles.orderChipTxt}>#{row.sort_order}</Text>
                </View>
                {!row.is_visible ? (
                  <View style={[styles.statChip, { backgroundColor: "#FEE2E2" }]}>
                    <Text style={[styles.statTxt, { color: "#991B1B" }]}>Hidden</Text>
                  </View>
                ) : null}
              </View>

              <Text style={styles.cardQ}>{row.q}</Text>
              <Text style={styles.cardA} numberOfLines={3}>{row.a}</Text>
              <Text style={styles.cardId}>id: {row.id}</Text>

              <View style={styles.actionsRow}>
                <View style={styles.toggleRow}>
                  <Text style={styles.toggleLabel}>Visible</Text>
                  {togglingId === row.id ? (
                    <ActivityIndicator size="small" color={colors.primary} />
                  ) : (
                    <Switch
                      testID={`admin-faq-visible-${row.id}`}
                      value={row.is_visible}
                      onValueChange={() => toggleVisibility(row)}
                      thumbColor={row.is_visible ? colors.primary : "#94A3B8"}
                    />
                  )}
                </View>
                <View style={{ flex: 1 }} />
                <TouchableOpacity
                  testID={`admin-faq-edit-${row.id}`}
                  style={[styles.iconBtn, { backgroundColor: "#EEF2FF" }]}
                  onPress={() => openEdit(row)}
                >
                  <PhIcon name="edit" size={16} color="#4338CA" />
                </TouchableOpacity>
                <TouchableOpacity
                  testID={`admin-faq-delete-${row.id}`}
                  style={[styles.iconBtn, { backgroundColor: "#FEE2E2" }]}
                  onPress={() => remove(row)}
                >
                  <PhIcon name="trash" size={16} color="#991B1B" />
                </TouchableOpacity>
              </View>
            </View>
          ))
        )}
      </ScrollView>

      {/* ─── Create / Edit modal ─────────────────────────────────── */}
      <Modal
        visible={modalOpen}
        animationType="slide"
        transparent
        onRequestClose={() => !saving && setModalOpen(false)}
      >
        <KeyboardAvoidingView
          style={styles.modalBackdrop}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {editingId ? "Edit FAQ" : "Add new FAQ"}
              </Text>
              <Pressable onPress={() => !saving && setModalOpen(false)} hitSlop={8}>
                <PhIcon name="close" size={20} color="#475569" />
              </Pressable>
            </View>

            <ScrollView
              style={{ flexGrow: 0 }}
              contentContainerStyle={{ paddingBottom: 8 }}
              keyboardShouldPersistTaps="handled"
            >
              {!editingId ? (
                <>
                  <Text style={styles.fieldLabel}>ID (optional)</Text>
                  <TextInput
                    testID="admin-faq-modal-id"
                    value={draft.id}
                    onChangeText={(v) => setDraft({ ...draft, id: v })}
                    placeholder="Leave blank for auto-generated"
                    style={styles.input}
                    autoCapitalize="none"
                  />
                </>
              ) : null}

              <Text style={styles.fieldLabel}>Category *</Text>
              <TextInput
                testID="admin-faq-modal-category"
                value={draft.category}
                onChangeText={(v) => setDraft({ ...draft, category: v })}
                placeholder="e.g. Getting started"
                style={styles.input}
              />

              <Text style={styles.fieldLabel}>Question *</Text>
              <TextInput
                testID="admin-faq-modal-q"
                value={draft.q}
                onChangeText={(v) => setDraft({ ...draft, q: v })}
                placeholder="The visible question text"
                style={styles.input}
                multiline
              />

              <Text style={styles.fieldLabel}>Answer *</Text>
              <TextInput
                testID="admin-faq-modal-a"
                value={draft.a}
                onChangeText={(v) => setDraft({ ...draft, a: v })}
                placeholder="The visible answer body"
                style={[styles.input, styles.inputArea]}
                multiline
                numberOfLines={6}
              />

              <View style={{ flexDirection: "row", gap: 10 }}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Sort order</Text>
                  <TextInput
                    testID="admin-faq-modal-sort"
                    value={draft.sort_order}
                    onChangeText={(v) => setDraft({ ...draft, sort_order: v.replace(/\D/g, "") })}
                    placeholder="100"
                    keyboardType="number-pad"
                    style={styles.input}
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Visible</Text>
                  <View style={[styles.input, { justifyContent: "center" }]}>
                    <Switch
                      testID="admin-faq-modal-visible"
                      value={draft.is_visible}
                      onValueChange={(v) => setDraft({ ...draft, is_visible: v })}
                      thumbColor={draft.is_visible ? colors.primary : "#94A3B8"}
                    />
                  </View>
                </View>
              </View>
            </ScrollView>

            <View style={styles.modalActions}>
              <TouchableOpacity
                style={[styles.modalCancelBtn]}
                onPress={() => !saving && setModalOpen(false)}
                disabled={saving}
              >
                <Text style={styles.modalCancelTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="admin-faq-modal-save"
                style={[styles.modalSaveBtn, saving && { opacity: 0.6 }]}
                onPress={save}
                disabled={saving}
              >
                {saving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.modalSaveTxt}>
                    {editingId ? "Save changes" : "Create FAQ"}
                  </Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

// ─── Styles ──────────────────────────────────────────────────────────
const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F4F5F7" },

  toolbar: {
    paddingHorizontal: 16,
    paddingTop: 8,
    paddingBottom: 12,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    gap: 10,
  },
  statsRow: { flexDirection: "row", gap: 8 },
  statChip: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
  },
  statTxt: { fontSize: 11.5, fontWeight: "800", color: "#475569" },

  searchRow: { flexDirection: "row", gap: 8 },
  searchInner: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    backgroundColor: "#F1F5F9",
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: Platform.OS === "ios" ? 10 : 6,
  },
  searchInput: { flex: 1, fontSize: 13, color: "#0F172A", paddingVertical: 0 },
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 12,
    borderRadius: 10,
  },
  addBtnTxt: { color: "#fff", fontSize: 13, fontWeight: "800" },

  center: { alignItems: "center", justifyContent: "center", padding: 40, gap: 10 },
  mutedTxt: { fontSize: 13, color: "#64748B", fontWeight: "700" },
  errTxt: { fontSize: 13, color: "#DC2626", textAlign: "center" },
  retryBtn: {
    backgroundColor: colors.primary,
    paddingHorizontal: 18,
    paddingVertical: 9,
    borderRadius: 8,
  },
  retryBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  cardHidden: { opacity: 0.65, borderColor: "#FCA5A5" },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  catChip: {
    backgroundColor: "#EEF2FF",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
  },
  catChipTxt: {
    color: "#4338CA",
    fontWeight: "800",
    fontSize: 10.5,
    letterSpacing: 0.3,
  },
  orderChip: {
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 999,
  },
  orderChipTxt: { color: "#92400E", fontWeight: "800", fontSize: 10.5 },

  cardQ: {
    fontSize: 14.5,
    fontWeight: "800",
    color: "#0F172A",
    marginTop: 10,
  },
  cardA: {
    fontSize: 12.5,
    color: "#334155",
    lineHeight: 18,
    marginTop: 4,
  },
  cardId: {
    fontSize: 11,
    color: "#94A3B8",
    marginTop: 6,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
  },

  actionsRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#E5E7EB",
  },
  toggleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  toggleLabel: { fontSize: 12, fontWeight: "800", color: "#475569" },
  iconBtn: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: "center", justifyContent: "center",
  },

  // Modal
  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.5)",
    justifyContent: "flex-end",
  },
  modalCard: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 18,
    borderTopRightRadius: 18,
    padding: 16,
    maxHeight: "92%",
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 12,
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  fieldLabel: { fontSize: 12, fontWeight: "800", color: "#475569", marginTop: 10, marginBottom: 4 },
  input: {
    backgroundColor: "#F8FAFC",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#E2E8F0",
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: "#0F172A",
  },
  inputArea: { minHeight: 110, textAlignVertical: "top" },
  modalActions: { flexDirection: "row", gap: 10, marginTop: 16 },
  modalCancelBtn: {
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#F1F5F9",
  },
  modalCancelTxt: { color: "#475569", fontWeight: "800", fontSize: 13 },
  modalSaveBtn: {
    flex: 1,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: colors.primary,
    alignItems: "center",
  },
  modalSaveTxt: { color: "#fff", fontWeight: "800", fontSize: 13.5 },
});
