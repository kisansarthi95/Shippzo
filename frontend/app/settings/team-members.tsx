/**
 * Team Members & Permissions screen — Phase A
 * --------------------------------------------
 * Owner-only page where the shop-owner manages staff who receive SLA
 * alerts and (in Phase C) will get their own login. For now the
 * permission toggles are PERSISTED but not enforced — the actual
 * permission gating per screen will land with Phase C login support.
 *
 * UX layout:
 *   • Header with quota badge ("1/1 free + 0 extra")
 *   • Add button → modal with Name / Phone / Role (suggested chips) /
 *                  Permissions (collapsible categories + per-feature
 *                  switches restricted to parent's plan features)
 *   • Cards listing existing members — Name (big), Role (small),
 *     Phone (mono), Edit / Delete actions
 *   • "Buy Extra Member" CTA when quota full → bottom sheet with
 *     Wallet / Razorpay buttons (real-wallet, mock-razorpay).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Modal,
  ActivityIndicator, RefreshControl, TextInput, Alert, Switch,
  KeyboardAvoidingView, Platform, Pressable,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Stack, router, useLocalSearchParams } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { Api } from "../../lib/api";

// Common job titles offered as quick-select chips. Free-text input
// remains available so admins aren't locked into these labels.
const ROLE_SUGGESTIONS = [
  "Operations Manager",
  "Logistics Manager",
  "Customer Service",
  "Finance",
  "Sales Executive",
  "Owner",
];

type Member = {
  id: string;
  name: string;
  phone: string;
  role: string;
  permissions: string[];
  paid_extra: boolean;
  active?: boolean;
};

type RegistryFeature = {
  key: string;
  label: string;
  description?: string;
  category: string;
};

type RegistryPayload = {
  registry: {
    categories: Array<{ key: string; label: string }>;
    features: RegistryFeature[];
  };
  my_features: string[];
};

const formatPhone = (raw: string) => {
  const d = (raw || "").replace(/\D/g, "");
  if (!d) return raw;
  if (d.length === 10) return `+91 ${d.slice(0, 5)} ${d.slice(5)}`;
  if (d.length === 12 && d.startsWith("91")) {
    return `+91 ${d.slice(2, 7)} ${d.slice(7)}`;
  }
  return `+${d}`;
};

export default function TeamMembersScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);
  const [quota, setQuota] = useState({
    free_cap: 0, free_used: 0, extra_used: 0,
    extra_member_price: 0, plan_key: "", plan_name: "",
    can_add_free: false, can_buy_extra: false,
  });
  const [registry, setRegistry] = useState<RegistryPayload | null>(null);

  // Add/edit modal
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [form, setForm] = useState({ name: "", phone: "", role: "", email: "", password: "" });
  const [perms, setPerms] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [extraToken, setExtraToken] = useState<string | null>(null);

  // Buy-extra sheet
  const [buyOpen, setBuyOpen] = useState(false);
  const [buying, setBuying] = useState(false);

  // Phase D — pick up the slot_token returned by checkout.tsx after a
  // successful Razorpay payment for an extra team-member slot. We use
  // a one-shot ref-style flag in state so re-renders don't re-trigger.
  const params = useLocalSearchParams<{
    slot_token?: string; paid_amount?: string;
  }>();
  const [pickedUpToken, setPickedUpToken] = useState<string | null>(null);
  useEffect(() => {
    const tok = String(params.slot_token || "");
    if (!tok || pickedUpToken === tok) return;
    setPickedUpToken(tok);
    setExtraToken(tok);
    setEditing(null);
    setForm({ name: "", phone: "", role: "", email: "", password: "" });
    setPerms(new Set());
    setModalOpen(true);
    Alert.alert(
      "Payment successful 🎉",
      `₹${params.paid_amount || ""} paid via Razorpay. Now fill in the team member's details.`,
    );
    // Clean the URL params so a refresh / back-nav doesn't replay.
    router.setParams({ slot_token: "", paid_amount: "" });
  }, [params.slot_token, params.paid_amount, pickedUpToken]);

  const load = useCallback(async () => {
    try {
      const [list, reg] = await Promise.all([
        Api.meTeamMembersList(),
        Api.meFeatureRegistry().catch(() => null),
      ]);
      setMembers(list.members || []);
      setQuota({
        free_cap: list.free_cap, free_used: list.free_used,
        extra_used: list.extra_used,
        extra_member_price: list.extra_member_price,
        plan_key: list.plan_key, plan_name: list.plan_name,
        can_add_free: list.can_add_free, can_buy_extra: list.can_buy_extra,
      });
      if (reg) setRegistry(reg as RegistryPayload);
    } catch (e: any) {
      console.warn("team-members load:", e?.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const onRefresh = useCallback(() => {
    setRefreshing(true); load();
  }, [load]);

  const openAdd = () => {
    setEditing(null);
    setForm({ name: "", phone: "", role: "", email: "", password: "" });
    setPerms(new Set());
    setExtraToken(null);
    setModalOpen(true);
  };

  const openEdit = (m: Member) => {
    setEditing(m);
    setForm({ name: m.name, phone: m.phone, role: m.role || "", email: (m as any).email || "", password: "" });
    setPerms(new Set(m.permissions || []));
    setExtraToken(null);
    setModalOpen(true);
  };

  const togglePerm = (key: string) => {
    const next = new Set(perms);
    if (next.has(key)) next.delete(key); else next.add(key);
    setPerms(next);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { Alert.alert("Missing", "Please enter a name"); return; }
    if (!form.phone.trim() || form.phone.replace(/\D/g, "").length < 10) {
      Alert.alert("Invalid Phone", "Please enter a valid 10-digit number"); return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        phone: form.phone.trim(),
        role: form.role.trim(),
        permissions: Array.from(perms),
        ...(form.email.trim() ? { email: form.email.trim().toLowerCase() } : {}),
        ...(form.password.trim() ? { password: form.password.trim() } : {}),
      };
      if (editing) {
        await Api.meTeamMemberUpdate(editing.id, payload);
      } else if (extraToken) {
        // Path 2: paid extra — must call /with-extra and pass token.
        await Api.meTeamMemberCreateWithExtra({ ...payload, slot_token: extraToken });
      } else {
        // Path 1: free quota.
        await Api.meTeamMemberCreate(payload);
      }
      setModalOpen(false);
      await load();
    } catch (e: any) {
      const det = e?.response?.data?.detail;
      if (det?.code === "EXTRA_REQUIRED") {
        // Owner has hit the free cap → open the buy sheet.
        setModalOpen(false);
        setBuyOpen(true);
      } else {
        Alert.alert("Save failed", typeof det === "string" ? det : (det?.message || e?.message || "Unknown error"));
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (m: Member) => {
    Alert.alert(
      "Remove team member?",
      `Are you sure you want to remove ${m.name}?`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove", style: "destructive",
          onPress: async () => {
            try {
              await Api.meTeamMemberDelete(m.id);
              await load();
            } catch (e: any) {
              Alert.alert("Delete failed", e?.message || "Unknown error");
            }
          },
        },
      ],
    );
  };

  const buyExtra = async (method: "wallet" | "razorpay") => {
    if (method === "razorpay") {
      // Phase D — REAL Razorpay flow. Push the user to the central
      // checkout.tsx screen with mode=team_extra_member; on successful
      // signature verification it routes back here with the paid
      // slot_token in query params (handled by the useEffect above).
      setBuyOpen(false);
      router.push("/checkout?mode=team_extra_member");
      return;
    }
    setBuying(true);
    try {
      const res = await Api.meTeamMemberPayExtra("wallet");
      // Wallet path: backend already deducted + the slot token is
      // pre-paid. Just open the member-detail modal.
      setExtraToken(res.slot_token);
      setBuyOpen(false);
      setEditing(null);
      setForm({ name: "", phone: "", role: "", email: "", password: "" });
      setPerms(new Set());
      setModalOpen(true);
      Alert.alert("Wallet charged", `₹${res.amount} deducted from wallet.`);
    } catch (e: any) {
      Alert.alert("Purchase failed", e?.response?.data?.detail || e?.message || "Unknown error");
    } finally {
      setBuying(false);
    }
  };

  const grouped = useMemo(() => {
    if (!registry) return [];
    const map = new Map<string, RegistryFeature[]>();
    for (const f of registry.registry.features) {
      // Restrict to features the owner actually has on their plan —
      // they can't grant what they don't own.
      if (!registry.my_features.includes(f.key)) continue;
      const list = map.get(f.category) || [];
      list.push(f);
      map.set(f.category, list);
    }
    return registry.registry.categories
      .map((c) => ({ ...c, features: map.get(c.key) || [] }))
      .filter((c) => c.features.length > 0);
  }, [registry]);

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <View style={styles.loadWrap}>
          <ActivityIndicator color="#1F4FBF" />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ title: "Team Members" }} />

      <View style={styles.headerRow}>
        <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
          <Ionicons name="chevron-back" size={22} color="#111827" />
        </TouchableOpacity>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>Team Members</Text>
          <Text style={styles.subtitle}>
            {quota.plan_name} plan · {quota.free_used}/{quota.free_cap} free
            {quota.extra_used > 0 ? ` + ${quota.extra_used} extra` : ""}
          </Text>
        </View>
      </View>

      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 12, paddingBottom: 100 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      >
        {members.length === 0 ? (
          <View style={styles.emptyCard}>
            <Ionicons name="people-outline" size={48} color="#9CA3AF" />
            <Text style={styles.emptyTitle}>No team members yet</Text>
            <Text style={styles.emptyTxt}>
              Add staff with their name, phone & role. They'll receive SLA alert
              WhatsApp messages and (later) get their own login with the
              permissions you grant.
            </Text>
          </View>
        ) : (
          members.map((m) => (
            <View key={m.id} style={styles.memberCard}>
              <View style={styles.memberAvatar}>
                <Text style={styles.memberAvatarTxt}>
                  {(m.name || "?").trim().charAt(0).toUpperCase()}
                </Text>
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <View style={styles.memberRowTop}>
                  <Text style={styles.memberName} numberOfLines={1}>{m.name}</Text>
                  {m.paid_extra && (
                    <View style={styles.extraPill}>
                      <Text style={styles.extraPillTxt}>EXTRA</Text>
                    </View>
                  )}
                </View>
                {!!m.role && <Text style={styles.memberRole}>{m.role}</Text>}
                <Text style={styles.memberPhone}>{formatPhone(m.phone)}</Text>
                <Text style={styles.memberPerms}>
                  {(m.permissions || []).length} permission{m.permissions?.length === 1 ? "" : "s"} granted
                </Text>
              </View>
              <View style={{ gap: 8 }}>
                <TouchableOpacity onPress={() => openEdit(m)} style={styles.iconBtn}>
                  <Ionicons name="create-outline" size={18} color="#1F4FBF" />
                </TouchableOpacity>
                <TouchableOpacity onPress={() => handleDelete(m)} style={styles.iconBtn}>
                  <Ionicons name="trash-outline" size={18} color="#DC2626" />
                </TouchableOpacity>
              </View>
            </View>
          ))
        )}

        {/* Quota CTA */}
        {quota.can_add_free ? (
          <TouchableOpacity style={styles.addBtn} onPress={openAdd}>
            <Ionicons name="add-circle" size={20} color="#fff" />
            <Text style={styles.addBtnTxt}>Add Team Member (Free)</Text>
          </TouchableOpacity>
        ) : quota.can_buy_extra ? (
          <TouchableOpacity style={styles.buyBtn} onPress={() => setBuyOpen(true)}>
            <Ionicons name="cart" size={20} color="#fff" />
            <Text style={styles.addBtnTxt}>
              Buy Extra Member · ₹{quota.extra_member_price}/mo
            </Text>
          </TouchableOpacity>
        ) : (
          <View style={styles.upgradeCard}>
            <Ionicons name="lock-closed" size={20} color="#92400E" />
            <Text style={styles.upgradeTxt}>
              Your {quota.plan_name} plan doesn't include team members.
              Upgrade to Gold or Platinum to add staff.
            </Text>
            <TouchableOpacity
              style={styles.upgradeBtn}
              onPress={() => router.push("/plans" as any)}
            >
              <Text style={styles.upgradeBtnTxt}>View Plans →</Text>
            </TouchableOpacity>
          </View>
        )}
      </ScrollView>

      {/* ─── Add / Edit Modal ────────────────────────────────────── */}
      <Modal
        visible={modalOpen}
        animationType="slide"
        presentationStyle="formSheet"
        onRequestClose={() => setModalOpen(false)}
      >
        <KeyboardAvoidingView
          style={{ flex: 1, backgroundColor: "#F9FAFB" }}
          behavior={Platform.OS === "ios" ? "padding" : "height"}
        >
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>
              {editing ? "Edit Member" : extraToken ? "Add Extra Member" : "Add Team Member"}
            </Text>
            <TouchableOpacity onPress={() => setModalOpen(false)}>
              <Ionicons name="close" size={26} color="#111827" />
            </TouchableOpacity>
          </View>

          <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 80 }}>
            <Text style={styles.fieldLabel}>NAME *</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g. Harsh Modi"
              placeholderTextColor="#9CA3AF"
              value={form.name}
              onChangeText={(t) => setForm({ ...form, name: t })}
            />

            <Text style={styles.fieldLabel}>PHONE NUMBER *</Text>
            <TextInput
              style={styles.input}
              placeholder="10-digit mobile number"
              placeholderTextColor="#9CA3AF"
              value={form.phone}
              onChangeText={(t) => setForm({ ...form, phone: t })}
              keyboardType="phone-pad"
              maxLength={15}
            />

            <Text style={styles.fieldLabel}>JOB ROLE</Text>
            <TextInput
              style={styles.input}
              placeholder="e.g. Operations Manager"
              placeholderTextColor="#9CA3AF"
              value={form.role}
              onChangeText={(t) => setForm({ ...form, role: t })}
            />
            <View style={styles.chipsWrap}>
              {ROLE_SUGGESTIONS.map((r) => (
                <Pressable
                  key={r}
                  onPress={() => setForm({ ...form, role: r })}
                  style={[
                    styles.roleChip,
                    form.role === r && styles.roleChipActive,
                  ]}
                >
                  <Text style={[
                    styles.roleChipTxt,
                    form.role === r && { color: "#fff" },
                  ]}>{r}</Text>
                </Pressable>
              ))}
            </View>

            {/* Phase B+C — login credentials. When provided the team
                member can log in to the app via "Team Member" toggle. */}
            <Text style={[styles.fieldLabel, { marginTop: 18 }]}>
              LOGIN EMAIL (optional)
            </Text>
            <TextInput
              style={styles.input}
              placeholder="staff@example.com"
              placeholderTextColor="#9CA3AF"
              value={form.email}
              onChangeText={(t) => setForm({ ...form, email: t })}
              keyboardType="email-address"
              autoCapitalize="none"
            />
            <Text style={[styles.fieldLabel, { marginTop: 12 }]}>
              {editing ? "NEW PASSWORD (leave blank to keep)" : "PASSWORD (optional)"}
            </Text>
            <TextInput
              style={styles.input}
              placeholder="Min 6 characters"
              placeholderTextColor="#9CA3AF"
              value={form.password}
              onChangeText={(t) => setForm({ ...form, password: t })}
              secureTextEntry
            />
            <Text style={styles.permsHelper}>
              Without email + password the staff member appears in SLA
              alerts only. Add credentials to let them log in.
            </Text>

            {/* Permissions */}
            <Text style={[styles.fieldLabel, { marginTop: 18 }]}>
              PERMISSIONS · {perms.size} selected
            </Text>
            <Text style={styles.permsHelper}>
              Toggle which features this member can use. They'll only see what
              you allow when they log in (Phase C).
            </Text>
            {grouped.length === 0 ? (
              <Text style={styles.permsHelper}>Loading…</Text>
            ) : (
              grouped.map((cat) => (
                <View key={cat.key} style={styles.permCategory}>
                  <Text style={styles.permCategoryLabel}>{cat.label}</Text>
                  {cat.features.map((f) => (
                    <View key={f.key} style={styles.permRow}>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.permLabel}>{f.label}</Text>
                        {!!f.description && (
                          <Text style={styles.permDesc}>{f.description}</Text>
                        )}
                      </View>
                      <Switch
                        value={perms.has(f.key)}
                        onValueChange={() => togglePerm(f.key)}
                        trackColor={{ false: "#E5E7EB", true: "#1F4FBF" }}
                        thumbColor="#fff"
                      />
                    </View>
                  ))}
                </View>
              ))
            )}
          </ScrollView>

          <View style={styles.modalFooter}>
            <TouchableOpacity
              style={[styles.saveBtn, saving && { opacity: 0.5 }]}
              disabled={saving}
              onPress={handleSave}
            >
              {saving
                ? <ActivityIndicator color="#fff" />
                : <Text style={styles.saveBtnTxt}>
                    {editing ? "Save Changes" : extraToken ? "Add Extra Member" : "Add Member"}
                  </Text>
              }
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* ─── Buy-Extra Sheet ─────────────────────────────────────── */}
      <Modal
        visible={buyOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setBuyOpen(false)}
      >
        <Pressable style={styles.sheetBg} onPress={() => setBuyOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => {}}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>Buy Extra Member</Text>
              <TouchableOpacity onPress={() => setBuyOpen(false)}>
                <Ionicons name="close" size={26} color="#111827" />
              </TouchableOpacity>
            </View>
            <Text style={styles.sheetDesc}>
              Add one more team member beyond your {quota.plan_name} quota.
              Charged ₹{quota.extra_member_price}/month per extra slot.
            </Text>

            <TouchableOpacity
              style={[styles.payBtn, { backgroundColor: "#10B981" }]}
              disabled={buying}
              onPress={() => buyExtra("wallet")}
            >
              <Ionicons name="wallet" size={20} color="#fff" />
              <View style={{ flex: 1 }}>
                <Text style={styles.payBtnTitle}>Pay from Wallet</Text>
                <Text style={styles.payBtnDesc}>Instant — deducted now</Text>
              </View>
              <Text style={styles.payBtnAmt}>₹{quota.extra_member_price}</Text>
            </TouchableOpacity>

            <TouchableOpacity
              style={[styles.payBtn, { backgroundColor: "#1F4FBF" }]}
              disabled={buying}
              onPress={() => buyExtra("razorpay")}
            >
              <Ionicons name="card" size={20} color="#fff" />
              <View style={{ flex: 1 }}>
                <Text style={styles.payBtnTitle}>Pay via Razorpay</Text>
                <Text style={styles.payBtnDesc}>UPI / Card / Netbanking</Text>
              </View>
              <Text style={styles.payBtnAmt}>₹{quota.extra_member_price}</Text>
            </TouchableOpacity>

            {buying && <ActivityIndicator color="#1F4FBF" style={{ marginTop: 12 }} />}
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#F9FAFB" },
  loadWrap: { flex: 1, alignItems: "center", justifyContent: "center" },

  headerRow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 12,
    backgroundColor: "#fff",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  backBtn: { padding: 4 },
  title: { fontSize: 18, fontWeight: "800", color: "#111827" },
  subtitle: { fontSize: 12, color: "#6B7280", marginTop: 2 },

  emptyCard: {
    alignItems: "center", padding: 32, borderRadius: 14,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  emptyTitle: { fontSize: 16, fontWeight: "800", color: "#374151", marginTop: 12 },
  emptyTxt: { fontSize: 13, color: "#6B7280", textAlign: "center", marginTop: 6, lineHeight: 19 },

  memberCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 12, borderRadius: 12,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    marginBottom: 10,
  },
  memberAvatar: {
    width: 44, height: 44, borderRadius: 22,
    backgroundColor: "#1F4FBF", alignItems: "center", justifyContent: "center",
  },
  memberAvatarTxt: { color: "#fff", fontSize: 18, fontWeight: "800" },
  memberRowTop: { flexDirection: "row", alignItems: "center", gap: 6 },
  memberName: { fontSize: 15, fontWeight: "800", color: "#111827", flexShrink: 1 },
  memberRole: { fontSize: 12.5, color: "#1F4FBF", fontWeight: "700", marginTop: 2 },
  memberPhone: { fontSize: 13, color: "#374151", fontVariant: ["tabular-nums"], marginTop: 2 },
  memberPerms: { fontSize: 11, color: "#6B7280", marginTop: 4 },
  extraPill: {
    backgroundColor: "#F59E0B", paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4,
  },
  extraPillTxt: { color: "#fff", fontSize: 9, fontWeight: "800", letterSpacing: 0.5 },
  iconBtn: {
    width: 36, height: 36, borderRadius: 8, alignItems: "center", justifyContent: "center",
    backgroundColor: "#F9FAFB", borderWidth: 1, borderColor: "#E5E7EB",
  },

  addBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: "#1F4FBF", padding: 14, borderRadius: 12, marginTop: 14,
  },
  buyBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: "#F59E0B", padding: 14, borderRadius: 12, marginTop: 14,
  },
  addBtnTxt: { color: "#fff", fontSize: 14, fontWeight: "800" },

  upgradeCard: {
    backgroundColor: "#FEF3C7", borderWidth: 1, borderColor: "#FCD34D",
    padding: 16, borderRadius: 12, marginTop: 14, alignItems: "center", gap: 8,
  },
  upgradeTxt: { fontSize: 13, color: "#92400E", textAlign: "center", lineHeight: 19 },
  upgradeBtn: { backgroundColor: "#92400E", paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  upgradeBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },

  // Modal
  modalHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    padding: 16, backgroundColor: "#fff",
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: "#E5E7EB",
  },
  modalTitle: { fontSize: 18, fontWeight: "800", color: "#111827" },
  modalFooter: {
    padding: 16, backgroundColor: "#fff",
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: "#E5E7EB",
  },
  saveBtn: { backgroundColor: "#1F4FBF", padding: 14, borderRadius: 10, alignItems: "center" },
  saveBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 14 },

  fieldLabel: { fontSize: 11, fontWeight: "800", color: "#6B7280", letterSpacing: 0.5, marginBottom: 6, marginTop: 14 },
  input: {
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 12,
    fontSize: 14, color: "#111827",
  },
  chipsWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 8 },
  roleChip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
  },
  roleChipActive: { backgroundColor: "#1F4FBF", borderColor: "#1F4FBF" },
  roleChipTxt: { fontSize: 11.5, fontWeight: "700", color: "#374151" },

  permsHelper: { fontSize: 12, color: "#6B7280", lineHeight: 17, marginTop: 4 },
  permCategory: {
    backgroundColor: "#fff", borderRadius: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginTop: 10, overflow: "hidden",
  },
  permCategoryLabel: {
    fontSize: 11, fontWeight: "800", color: "#6B7280", letterSpacing: 0.5,
    backgroundColor: "#F9FAFB", paddingHorizontal: 12, paddingVertical: 8,
  },
  permRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingHorizontal: 12, paddingVertical: 10,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: "#F3F4F6",
  },
  permLabel: { fontSize: 13.5, fontWeight: "700", color: "#111827" },
  permDesc: { fontSize: 11.5, color: "#6B7280", marginTop: 2, lineHeight: 16 },

  // Buy sheet
  sheetBg: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#fff", borderTopLeftRadius: 20, borderTopRightRadius: 20,
    padding: 18, paddingBottom: 32,
  },
  sheetHeader: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingBottom: 8,
  },
  sheetTitle: { fontSize: 17, fontWeight: "800", color: "#111827" },
  sheetDesc: { fontSize: 12.5, color: "#6B7280", marginTop: 4, lineHeight: 18 },
  payBtn: {
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 14, borderRadius: 12, marginTop: 12,
  },
  payBtnTitle: { color: "#fff", fontSize: 14, fontWeight: "800" },
  payBtnDesc:  { color: "rgba(255,255,255,0.85)", fontSize: 11.5, marginTop: 2 },
  payBtnAmt:   { color: "#fff", fontSize: 16, fontWeight: "800" },
});
