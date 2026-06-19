/**
 * Phase-29 — Super-Admin Article CMS.
 *
 * Lives at /admin/articles. Lets a Super Admin add, edit, hide and
 * delete the in-app support articles surfaced under /support-center.
 * Mirrors the proven CRUD shape of /admin/faq.
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

type Row = {
  id:         string;
  title:      string;
  summary:    string;
  icon:       string;
  category:   string;
  sort_order: number;
  is_visible: boolean;
  body?:      string;
  created_at?: string;
  updated_at?: string;
};

type Draft = {
  id:         string;
  title:      string;
  summary:    string;
  body:       string;
  icon:       string;
  category:   string;
  sort_order: string;
  is_visible: boolean;
};

const EMPTY_DRAFT: Draft = {
  id: "", title: "", summary: "", body: "",
  icon: "document-text-outline", category: "General",
  sort_order: "100", is_visible: true,
};

export default function AdminArticlesScreen() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [items, setItems]     = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]     = useState("");
  const [query, setQuery]     = useState("");

  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft]         = useState<Draft>(EMPTY_DRAFT);
  const [saving, setSaving]       = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setError("");
      const r = await Api.adminArticlesList();
      setItems(r.items || []);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Could not load");
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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      it.title.toLowerCase().includes(q)
        || (it.category || "").toLowerCase().includes(q)
        || (it.summary  || "").toLowerCase().includes(q)
        || it.id.toLowerCase().includes(q),
    );
  }, [items, query]);

  const openCreate = () => {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
    setModalOpen(true);
  };

  const openEdit = async (row: Row) => {
    try {
      // Fetch full doc with body — list endpoint omits body.
      const r = await Api.adminArticleGet(row.id);
      const a = r.item;
      setEditingId(a.id);
      setDraft({
        id:         a.id,
        title:      a.title,
        summary:    a.summary || "",
        body:       a.body || "",
        icon:       a.icon || "document-text-outline",
        category:   a.category || "General",
        sort_order: String(a.sort_order || 100),
        is_visible: !!a.is_visible,
      });
      setModalOpen(true);
    } catch (e: any) {
      Alert.alert("Could not open", e?.response?.data?.detail || e?.message || "Open failed");
    }
  };

  const save = async () => {
    const title = draft.title.trim();
    const body  = draft.body.trim();
    if (!title || body.length < 3) {
      Alert.alert("Missing fields", "Title and Body are required.");
      return;
    }
    const so = Math.max(0, Math.min(99999, parseInt(draft.sort_order || "100", 10) || 100));
    setSaving(true);
    try {
      if (editingId) {
        await Api.adminArticleUpdate(editingId, {
          title,
          summary:    draft.summary.trim(),
          body,
          icon:       draft.icon.trim() || "document-text-outline",
          category:   draft.category.trim() || "General",
          sort_order: so,
          is_visible: draft.is_visible,
        });
      } else {
        await Api.adminArticleCreate({
          id:         draft.id.trim() || undefined,
          title,
          summary:    draft.summary.trim(),
          body,
          icon:       draft.icon.trim() || "document-text-outline",
          category:   draft.category.trim() || "General",
          sort_order: so,
          is_visible: draft.is_visible,
        });
      }
      setModalOpen(false);
      await load();
    } catch (e: any) {
      Alert.alert("Could not save", e?.response?.data?.detail || e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const toggleVisibility = async (row: Row) => {
    setTogglingId(row.id);
    try {
      const r = await Api.adminArticleUpdate(row.id, { is_visible: !row.is_visible });
      setItems((prev) =>
        prev.map((it) => (it.id === row.id ? { ...it, ...r.item } : it)),
      );
    } catch (e: any) {
      Alert.alert("Toggle failed", e?.response?.data?.detail || e?.message || "");
    } finally {
      setTogglingId(null);
    }
  };

  const confirmDelete = (row: Row) => {
    Alert.alert(
      "Delete article",
      `Delete "${row.title}"? This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.adminArticleDelete(row.id);
              setItems((prev) => prev.filter((it) => it.id !== row.id));
            } catch (e: any) {
              Alert.alert("Delete failed", e?.response?.data?.detail || e?.message || "");
            }
          },
        },
      ],
    );
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.muted}>Loading articles…</Text>
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />

      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <PhIcon name="arrow-back" size={22} color="#0F172A" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Articles</Text>
          <Text style={styles.sub}>
            {items.length} total · {items.filter((i) => i.is_visible).length} visible
          </Text>
        </View>
        <TouchableOpacity
          testID="admin-articles-add"
          onPress={openCreate}
          style={styles.addBtn}
        >
          <PhIcon name="add" size={18} color="#fff" />
          <Text style={styles.addTxt}>Add</Text>
        </TouchableOpacity>
      </View>

      <SearchBar
        value={query}
        onChangeText={setQuery}
        placeholder="Search by title / category / id…"
        testID="admin-articles-search"
      />

      {error ? (
        <View style={styles.errorBox}>
          <PhIcon name="alert-circle-outline" size={18} color="#DC2626" />
          <Text style={styles.errorTxt}>{error}</Text>
        </View>
      ) : null}

      <ScrollView
        contentContainerStyle={{ padding: 12, paddingBottom: 40 }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => { setRefreshing(true); load(); }}
            tintColor={colors.primary}
          />
        }
      >
        {filtered.length === 0 ? (
          <View style={styles.empty}>
            <PhIcon name="document-text-outline" size={32} color="#94A3B8" />
            <Text style={styles.emptyTxt}>
              {query.trim() ? `No articles match "${query}".` : "No articles yet. Tap Add to create one."}
            </Text>
          </View>
        ) : (
          filtered.map((row) => (
            <View key={row.id} style={styles.card}>
              <View style={styles.cardHead}>
                <View style={styles.cardIcon}>
                  <PhIcon
                    name={(row.icon as any) || "document-text-outline"}
                    size={20}
                    color="#1E40AF"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.cardTitle} numberOfLines={2}>{row.title}</Text>
                  <Text style={styles.cardMeta}>
                    {row.category} · sort {row.sort_order} · id: {row.id}
                  </Text>
                </View>
                {togglingId === row.id ? (
                  <ActivityIndicator size="small" color={colors.primary} />
                ) : (
                  <Switch
                    value={row.is_visible}
                    onValueChange={() => toggleVisibility(row)}
                    thumbColor={row.is_visible ? colors.primary : "#94A3B8"}
                  />
                )}
              </View>
              {!!row.summary && (
                <Text style={styles.cardSummary} numberOfLines={3}>{row.summary}</Text>
              )}
              <View style={styles.cardActions}>
                <TouchableOpacity style={styles.actionBtn} onPress={() => openEdit(row)}>
                  <PhIcon name="create-outline" size={16} color="#1E40AF" />
                  <Text style={styles.actionTxt}>Edit</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.actionBtn} onPress={() => confirmDelete(row)}>
                  <PhIcon name="trash-outline" size={16} color="#DC2626" />
                  <Text style={[styles.actionTxt, { color: "#DC2626" }]}>Delete</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))
        )}
      </ScrollView>

      {/* Create / Edit modal */}
      <Modal visible={modalOpen} animationType="slide" onRequestClose={() => setModalOpen(false)}>
        <SafeAreaView style={styles.safe} edges={["top"]}>
          <KeyboardAvoidingView
            style={{ flex: 1 }}
            behavior={Platform.OS === "ios" ? "padding" : undefined}
          >
            <View style={styles.modalHead}>
              <TouchableOpacity onPress={() => !saving && setModalOpen(false)} style={styles.backBtn}>
                <PhIcon name="close" size={22} color="#0F172A" />
              </TouchableOpacity>
              <Text style={styles.title}>
                {editingId ? "Edit Article" : "Add Article"}
              </Text>
              <View style={{ width: 40 }} />
            </View>
            <ScrollView
              style={{ flex: 1 }}
              contentContainerStyle={{ padding: 16, paddingBottom: 60 }}
              keyboardShouldPersistTaps="handled"
            >
              {!editingId && (
                <>
                  <Text style={styles.fieldLabel}>Stable ID (optional)</Text>
                  <Text style={styles.hint}>Leave blank to auto-generate. Used in the URL.</Text>
                  <TextInput
                    value={draft.id}
                    onChangeText={(v) => setDraft({ ...draft, id: v.replace(/[^a-z0-9-]/gi, "-").toLowerCase() })}
                    placeholder="e.g. how-to-import"
                    autoCapitalize="none"
                    style={styles.input}
                  />
                </>
              )}
              <Text style={styles.fieldLabel}>Title *</Text>
              <TextInput
                value={draft.title}
                onChangeText={(v) => setDraft({ ...draft, title: v })}
                placeholder="e.g. How to generate a shipping label?"
                style={styles.input}
                testID="admin-article-title"
              />
              <Text style={styles.fieldLabel}>Category</Text>
              <TextInput
                value={draft.category}
                onChangeText={(v) => setDraft({ ...draft, category: v })}
                placeholder="e.g. Getting started"
                style={styles.input}
              />
              <Text style={styles.fieldLabel}>Summary</Text>
              <Text style={styles.hint}>One-line teaser shown on the list page.</Text>
              <TextInput
                value={draft.summary}
                onChangeText={(v) => setDraft({ ...draft, summary: v })}
                placeholder="One-line description"
                style={[styles.input, styles.inputArea, { minHeight: 60 }]}
                multiline
              />
              <Text style={styles.fieldLabel}>Body *</Text>
              <Text style={styles.hint}>
                Use blank lines to separate paragraphs. Lines starting with
                {" "}1./•/- become list items.
              </Text>
              <TextInput
                value={draft.body}
                onChangeText={(v) => setDraft({ ...draft, body: v })}
                placeholder="Write the article content here…"
                style={[styles.input, styles.inputArea, { minHeight: 220 }]}
                multiline
                testID="admin-article-body"
              />
              <Text style={styles.fieldLabel}>Icon name</Text>
              <Text style={styles.hint}>Ionicon name (e.g. document-text-outline, receipt-outline).</Text>
              <TextInput
                value={draft.icon}
                onChangeText={(v) => setDraft({ ...draft, icon: v })}
                placeholder="document-text-outline"
                autoCapitalize="none"
                style={styles.input}
              />
              <View style={{ flexDirection: "row", gap: 10 }}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Sort order</Text>
                  <TextInput
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
                style={styles.cancelBtn}
                onPress={() => !saving && setModalOpen(false)}
                disabled={saving}
              >
                <Text style={styles.cancelTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="admin-article-save"
                style={[styles.saveBtn, saving && { opacity: 0.6 }]}
                onPress={save}
                disabled={saving}
              >
                {saving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <Text style={styles.saveTxt}>
                    {editingId ? "Save changes" : "Create article"}
                  </Text>
                )}
              </TouchableOpacity>
            </View>
          </KeyboardAvoidingView>
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F4F5F7" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", gap: 8 },
  muted: { color: "#64748B" },

  header: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 10, paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
    gap: 8,
  },
  backBtn: { padding: 6 },
  title: { fontSize: 18, fontWeight: "800", color: "#0F172A" },
  sub: { fontSize: 12, color: "#64748B", marginTop: 2 },

  addBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 8, backgroundColor: colors.primary,
  },
  addTxt: { color: "#fff", fontWeight: "700" },

  searchWrap: {
    flexDirection: "row", alignItems: "center", gap: 8,
    marginHorizontal: 12, marginTop: 12,
    paddingHorizontal: 12, paddingVertical: 8,
    backgroundColor: "#fff",
    borderRadius: 10, borderWidth: 1, borderColor: "#E5E7EB",
  },
  searchInput: { flex: 1, color: "#0F172A", fontSize: 14, paddingVertical: 2 },

  errorBox: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#FEE2E2",
    paddingHorizontal: 12, paddingVertical: 10,
    margin: 12, borderRadius: 8,
  },
  errorTxt: { color: "#991B1B", flex: 1 },

  card: {
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginTop: 10,
    padding: 14,
  },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  cardIcon: {
    width: 38, height: 38, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#DBEAFE",
  },
  cardTitle: { fontSize: 14, fontWeight: "700", color: "#0F172A" },
  cardMeta:  { fontSize: 11, color: "#64748B", marginTop: 2 },
  cardSummary: { fontSize: 12, color: "#475569", marginTop: 8, lineHeight: 18 },
  cardActions: {
    flexDirection: "row",
    marginTop: 10, paddingTop: 10,
    borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  actionBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center", justifyContent: "center",
    paddingVertical: 8, gap: 6,
  },
  actionTxt: { fontWeight: "700", color: "#1E40AF", fontSize: 13 },

  empty: {
    alignItems: "center", justifyContent: "center",
    padding: 40, gap: 8,
  },
  emptyTxt: { color: "#64748B", textAlign: "center" },

  modalHead: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 10, paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
  },

  fieldLabel: {
    fontSize: 13, fontWeight: "700",
    color: "#0F172A",
    marginTop: 12, marginBottom: 6,
  },
  hint: { fontSize: 11.5, color: "#64748B", marginBottom: 6, lineHeight: 16 },
  input: {
    borderWidth: 1, borderColor: "#CBD5E1",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "ios" ? 10 : 8,
    backgroundColor: "#fff",
    color: "#0F172A", fontSize: 14,
    marginVertical: 2,
  },
  inputArea: { textAlignVertical: "top" },

  modalActions: {
    flexDirection: "row", gap: 10,
    paddingHorizontal: 14, paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 14,
    backgroundColor: "#fff",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  cancelBtn: {
    flex: 1,
    alignItems: "center", justifyContent: "center",
    paddingVertical: 14,
    borderRadius: 8, backgroundColor: "#F1F5F9",
  },
  cancelTxt: { fontWeight: "700", color: "#475569" },
  saveBtn: {
    flex: 1.4,
    alignItems: "center", justifyContent: "center",
    paddingVertical: 14,
    borderRadius: 8, backgroundColor: colors.primary,
  },
  saveTxt: { fontWeight: "800", color: "#fff" },
});
