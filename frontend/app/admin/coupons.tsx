/**
 * Admin → Coupons (2026-04-30 Phase-2)
 *
 * Admin-only screen for managing discount codes + usage analytics.
 *
 * Feature set (v2):
 *   • Full CRUD on coupons (Code / type / value / validity / max_uses /
 *     plan filter / cycle filter / active)
 *   • NATIVE DATE PICKER for valid_from / valid_to (cross-platform
 *     via @react-native-community/datetimepicker). No more YYYY-MM-DD
 *     text inputs that invited typos.
 *   • Restricted-to-users allow-list — if admin enters a list of
 *     emails, only those users can redeem the coupon. Supports paste
 *     of comma-separated or newline-separated lists. Includes a
 *     "Share via WhatsApp" row that generates a ready-to-send
 *     message with the code (bulk distribution alternative to email,
 *     since the app doesn't have a transactional email provider yet).
 *   • Analytics dashboard at top — total coupons by status, total
 *     redemptions and total discount given, plus per-coupon breakdown.
 *     Stays live by reading from razorpay_orders aggregation.
 */
import React, { useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  Switch, ActivityIndicator, Alert, Modal, Platform, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import DateTimePicker from "@react-native-community/datetimepicker";
import Constants from "expo-constants";
import { Api, Coupon, CouponCreatePayload } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type PaidPlan = "silver" | "gold" | "platinum";
const PAID_PLANS: { key: PaidPlan; name: string }[] = [
  { key: "silver",   name: "Silver" },
  { key: "gold",     name: "Gold" },
  { key: "platinum", name: "Platinum" },
];

const STATUS_COLORS: Record<string, { bg: string; fg: string; label: string }> = {
  active:    { bg: "#DCFCE7", fg: "#15803D", label: "Active" },
  paused:    { bg: "#FEF3C7", fg: "#92400E", label: "Paused" },
  scheduled: { bg: "#DBEAFE", fg: "#1D4ED8", label: "Scheduled" },
  expired:   { bg: "#FEE2E2", fg: "#B91C1C", label: "Expired" },
  exhausted: { bg: "#E0E7FF", fg: "#4338CA", label: "Used up" },
  deleted:   { bg: "#F1F5F9", fg: "#475569", label: "Deleted" },
};

type Analytics = {
  totals: { redemptions: number; total_discount: number; total_revenue: number };
  coupons: Array<{
    code: string;
    redemptions: number;
    total_discount: number;
    total_revenue: number;
    plans: string[];
    cycles: string[];
    last_redeemed?: string;
    status: string;
    discount_type?: string;
    discount_value?: number;
  }>;
  total_coupons: number;
  status_counts: Record<string, number>;
};

function fmtDate(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("en-IN", {
      day: "2-digit", month: "short", year: "numeric",
    });
  } catch { return iso.slice(0, 10); }
}

function isoDay(d: Date): string {
  return new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())).toISOString();
}

function parseEmailList(raw: string): string[] {
  if (!raw) return [];
  return Array.from(new Set(
    raw.split(/[\s,;\n]+/)
      .map((x) => x.trim().toLowerCase())
      .filter((x) => !!x && x.includes("@")),
  ));
}

export default function AdminCouponsScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [coupons, setCoupons] = useState<Coupon[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [editor, setEditor] = useState<Partial<Coupon> | null>(null);
  const [saving, setSaving] = useState(false);
  // Emails textarea state is decoupled from Coupon shape — on save we
  // parse it into restricted_to_users array.
  const [emailsRaw, setEmailsRaw] = useState<string>("");

  // Date picker modal state (shared by from + to)
  const [datePickerFor, setDatePickerFor] = useState<"from" | "to" | null>(null);

  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only admin can manage coupons.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  const load = async () => {
    setLoading(true);
    try {
      const [list, stats] = await Promise.all([
        Api.adminListCoupons(),
        Api.adminCouponAnalytics().catch(() => null),
      ]);
      setCoupons(list || []);
      setAnalytics(stats);
    } catch (e: any) {
      Alert.alert("Failed to load", e?.message || "Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openCreate = () => {
    const today = new Date();
    const oneMonth = new Date();
    oneMonth.setMonth(oneMonth.getMonth() + 1);
    setEditor({
      code: "",
      discount_type: "percent",
      discount_value: 10,
      valid_from: isoDay(today),
      valid_to:   isoDay(oneMonth),
      max_uses: null,
      applies_to_plans: [],
      billing_cycles: [],
      active: true,
      restricted_to_users: [],
    });
    setEmailsRaw("");
  };

  const openEdit = (c: Coupon) => {
    setEditor({ ...c });
    setEmailsRaw((c.restricted_to_users || []).join(", "));
  };

  const cancelEditor = () => { setEditor(null); setEmailsRaw(""); };

  const togglePlan = (k: PaidPlan) => {
    if (!editor) return;
    const cur = (editor.applies_to_plans || []) as PaidPlan[];
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
    setEditor({ ...editor, applies_to_plans: next });
  };

  const toggleCycle = (k: "monthly" | "yearly") => {
    if (!editor) return;
    const cur = (editor.billing_cycles || []) as ("monthly" | "yearly")[];
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
    setEditor({ ...editor, billing_cycles: next });
  };

  const saveEditor = async () => {
    if (!editor) return;
    const code = String(editor.code || "").trim().toUpperCase();
    const dv = Number(editor.discount_value || 0);
    if (!code) return Alert.alert("Code required");
    if (!/^[A-Z0-9_-]{3,20}$/.test(code)) {
      return Alert.alert("Invalid code", "Code must be 3-20 chars: A-Z, 0-9, _, -");
    }
    if (!(dv > 0)) return Alert.alert("Discount must be > 0");
    if (editor.discount_type === "percent" && dv > 100) {
      return Alert.alert("Percent ≤ 100");
    }
    const emails = parseEmailList(emailsRaw);
    setSaving(true);
    try {
      const payload: CouponCreatePayload = {
        code,
        discount_type: editor.discount_type as any,
        discount_value: dv,
        valid_from: editor.valid_from!,
        valid_to:   editor.valid_to!,
        max_uses:   editor.max_uses ?? null,
        applies_to_plans: (editor.applies_to_plans || []) as PaidPlan[],
        billing_cycles:   (editor.billing_cycles || []) as any,
        active:           !!editor.active,
        restricted_to_users: emails,
      };
      if (editor.id) {
        await Api.adminUpdateCoupon(editor.id, payload);
      } else {
        await Api.adminCreateCoupon(payload);
      }
      cancelEditor();
      await load();
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Save failed";
      Alert.alert("Could not save", String(msg));
    } finally {
      setSaving(false);
    }
  };

  const askDelete = (c: Coupon) => {
    Alert.alert("Delete coupon", `Remove ${c.code}?`, [
      { text: "Cancel", style: "cancel" },
      { text: "Delete", style: "destructive",
        onPress: async () => {
          try { await Api.adminDeleteCoupon(c.id); await load(); }
          catch (e: any) { Alert.alert("Delete failed", e?.message || ""); }
        } },
    ]);
  };

  const shareViaWhatsApp = (c: Coupon) => {
    const exp = fmtDate(c.valid_to);
    const disc = c.discount_type === "percent"
      ? `${c.discount_value}% off`
      : `₹${c.discount_value} off`;
    const msg =
      `🎟  Special offer on ${Constants.expoConfig?.name || "our app"}!\n\n` +
      `Use coupon *${c.code}* at checkout and get ${disc} on your plan.\n` +
      `Valid until ${exp}. Don't miss out!`;
    const url = `https://wa.me/?text=${encodeURIComponent(msg)}`;
    Linking.openURL(url).catch(() => Alert.alert("Couldn't open WhatsApp"));
  };

  const sorted = useMemo(
    () => [...coupons].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "")),
    [coupons],
  );

  // Date picker handler
  const onDatePicked = (event: any, selected?: Date) => {
    const target = datePickerFor;
    // On Android, the picker auto-dismisses on select. On iOS, we hide
    // it manually via the OK/Cancel buttons in the modal.
    if (Platform.OS === "android") setDatePickerFor(null);
    if (!selected || !editor || !target) return;
    const iso = isoDay(selected);
    if (target === "from") setEditor({ ...editor, valid_from: iso });
    else setEditor({ ...editor, valid_to: iso });
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={styles.headerBtn}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Coupons</Text>
        <TouchableOpacity onPress={openCreate} style={[styles.headerBtn, styles.primaryBtn]}>
          <PhIcon name="add" size={20} color="#fff" />
        </TouchableOpacity>
      </View>

      {loading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 80 }}>
          {/* Analytics dashboard */}
          {analytics ? (
            <View style={styles.dashCard}>
              <Text style={styles.dashTitle}>📊  Analytics</Text>
              <View style={styles.statRow}>
                <View style={styles.statBox}>
                  <Text style={styles.statNum}>{analytics.total_coupons}</Text>
                  <Text style={styles.statLbl}>Coupons</Text>
                </View>
                <View style={styles.statBox}>
                  <Text style={styles.statNum}>{analytics.totals.redemptions}</Text>
                  <Text style={styles.statLbl}>Redemptions</Text>
                </View>
                <View style={styles.statBox}>
                  <Text style={[styles.statNum, { fontSize: 16 }]}>
                    ₹{analytics.totals.total_discount.toLocaleString("en-IN")}
                  </Text>
                  <Text style={styles.statLbl}>Discount given</Text>
                </View>
              </View>
              <View style={styles.statusRow}>
                {Object.entries(analytics.status_counts).map(([k, v]) => {
                  if (!v) return null;
                  const sc = STATUS_COLORS[k] || STATUS_COLORS.active;
                  return (
                    <View key={k} style={[styles.miniPill, { backgroundColor: sc.bg }]}>
                      <Text style={[styles.miniPillTxt, { color: sc.fg }]}>
                        {v} {sc.label.toLowerCase()}
                      </Text>
                    </View>
                  );
                })}
              </View>
              {analytics.coupons.length > 0 ? (
                <>
                  <Text style={styles.dashSubTitle}>Top performers</Text>
                  {analytics.coupons.slice(0, 5).map((c) => (
                    <View key={c.code} style={styles.topRow}>
                      <Text style={styles.topCode}>{c.code}</Text>
                      <Text style={styles.topMeta}>
                        {c.redemptions} uses · ₹{c.total_discount.toLocaleString("en-IN")} discount
                      </Text>
                    </View>
                  ))}
                </>
              ) : (
                <Text style={styles.dashHint}>
                  No redemptions yet. Share a code via WhatsApp below to get started.
                </Text>
              )}
            </View>
          ) : null}

          <Text style={styles.helpTxt}>
            Coupons reduce the plan checkout price for users who enter the code on the Plans
            screen. Validity, usage limits, plan/cycle filters and per-user allow-lists are
            all enforced server-side.
          </Text>

          {sorted.length === 0 ? (
            <View style={styles.emptyCard}>
              <PhIcon name="pricetag-outline" size={32} color="#94A3B8" />
              <Text style={styles.emptyTxt}>No coupons yet.</Text>
              <TouchableOpacity onPress={openCreate} style={styles.emptyBtn}>
                <Text style={styles.emptyBtnTxt}>Create your first coupon</Text>
              </TouchableOpacity>
            </View>
          ) : (
            sorted.map((c) => {
              const sc = STATUS_COLORS[c.status] || STATUS_COLORS.active;
              const planFilter = c.applies_to_plans?.length
                ? c.applies_to_plans.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(", ")
                : "All paid plans";
              const cycleFilter = c.billing_cycles?.length
                ? c.billing_cycles.join(" + ")
                : "Monthly + Yearly";
              const usesLine = c.max_uses
                ? `${c.used_count}/${c.max_uses} used`
                : `${c.used_count} used (unlimited)`;
              const restrLine = (c.restricted_to_users && c.restricted_to_users.length)
                ? `🔒 ${c.restricted_to_users.length} user${c.restricted_to_users.length === 1 ? "" : "s"} only`
                : null;
              return (
                <TouchableOpacity
                  key={c.id}
                  testID={`coupon-row-${c.code}`}
                  style={styles.couponRow}
                  onPress={() => openEdit(c)}
                >
                  <View style={styles.rowTop}>
                    <Text style={styles.codeTxt}>{c.code}</Text>
                    <View style={[styles.statusPill, { backgroundColor: sc.bg }]}>
                      <Text style={[styles.statusTxt, { color: sc.fg }]}>{sc.label}</Text>
                    </View>
                  </View>
                  <Text style={styles.discountTxt}>
                    {c.discount_type === "percent"
                      ? `${c.discount_value}% off`
                      : `Flat ₹${c.discount_value} off`}
                  </Text>
                  <Text style={styles.metaTxt}>
                    Valid {fmtDate(c.valid_from)} → {fmtDate(c.valid_to)}
                  </Text>
                  <Text style={styles.metaTxt}>Plans: {planFilter}</Text>
                  <Text style={styles.metaTxt}>Billing: {cycleFilter}</Text>
                  <Text style={styles.metaTxt}>{usesLine}</Text>
                  {restrLine ? (
                    <Text style={[styles.metaTxt, { color: "#7C3AED", fontWeight: "700" }]}>
                      {restrLine}
                    </Text>
                  ) : null}
                  <View style={styles.rowActions}>
                    <TouchableOpacity onPress={() => shareViaWhatsApp(c)} style={styles.smallBtnSuccess}>
                      <PhIcon name="logo-whatsapp" size={14} color="#15803D" />
                      <Text style={[styles.smallBtnTxt, { color: "#15803D" }]}>Share</Text>
                    </TouchableOpacity>
                    <TouchableOpacity onPress={() => openEdit(c)} style={styles.smallBtn}>
                      <PhIcon name="create-outline" size={14} color={colors.primary} />
                      <Text style={styles.smallBtnTxt}>Edit</Text>
                    </TouchableOpacity>
                    <TouchableOpacity onPress={() => askDelete(c)} style={styles.smallBtnDanger}>
                      <PhIcon name="trash-outline" size={14} color="#B91C1C" />
                      <Text style={[styles.smallBtnTxt, { color: "#B91C1C" }]}>Delete</Text>
                    </TouchableOpacity>
                  </View>
                </TouchableOpacity>
              );
            })
          )}
        </ScrollView>
      )}

      {/* Editor modal */}
      <Modal visible={!!editor} animationType="slide" transparent onRequestClose={cancelEditor}>
        <View style={styles.modalBg}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {editor?.id ? "Edit coupon" : "New coupon"}
              </Text>
              <TouchableOpacity onPress={cancelEditor}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32 }}>
              <Text style={styles.lbl}>Code</Text>
              <TextInput
                value={editor?.code || ""}
                onChangeText={(t) => setEditor({ ...editor!, code: t.toUpperCase() })}
                placeholder="DIWALI25"
                autoCapitalize="characters"
                style={styles.input}
                editable={!editor?.id}
                placeholderTextColor="#94A3B8"
              />
              {editor?.id ? <Text style={styles.hint}>Code can't be changed after create.</Text> : null}

              <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
                <TouchableOpacity
                  onPress={() => setEditor({ ...editor!, discount_type: "percent" })}
                  style={[styles.typeBtn, editor?.discount_type === "percent" && styles.typeBtnActive]}
                >
                  <Text style={[styles.typeBtnTxt, editor?.discount_type === "percent" && { color: "#fff" }]}>% Percent</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => setEditor({ ...editor!, discount_type: "flat" })}
                  style={[styles.typeBtn, editor?.discount_type === "flat" && styles.typeBtnActive]}
                >
                  <Text style={[styles.typeBtnTxt, editor?.discount_type === "flat" && { color: "#fff" }]}>₹ Flat</Text>
                </TouchableOpacity>
              </View>

              <Text style={styles.lbl}>
                Value {editor?.discount_type === "percent" ? "(0-100)" : "(₹)"}
              </Text>
              <TextInput
                value={String(editor?.discount_value ?? "")}
                onChangeText={(t) => setEditor({ ...editor!, discount_value: Number(t.replace(/[^0-9]/g, "")) || 0 })}
                keyboardType="number-pad"
                style={styles.input}
                placeholderTextColor="#94A3B8"
              />

              {/* Date pickers (native) */}
              <Text style={styles.lbl}>Valid from</Text>
              <TouchableOpacity
                onPress={() => setDatePickerFor("from")}
                style={styles.dateBtn}
                testID="coupon-date-from"
              >
                <PhIcon name="calendar-outline" size={16} color={colors.primary} />
                <Text style={styles.dateBtnTxt}>{fmtDate(editor?.valid_from || "")}</Text>
              </TouchableOpacity>

              <Text style={styles.lbl}>Valid to</Text>
              <TouchableOpacity
                onPress={() => setDatePickerFor("to")}
                style={styles.dateBtn}
                testID="coupon-date-to"
              >
                <PhIcon name="calendar-outline" size={16} color={colors.primary} />
                <Text style={styles.dateBtnTxt}>{fmtDate(editor?.valid_to || "")}</Text>
              </TouchableOpacity>

              {datePickerFor && Platform.OS !== "web" ? (
                <DateTimePicker
                  value={new Date(
                    datePickerFor === "from" ? (editor?.valid_from || Date.now()) : (editor?.valid_to || Date.now()),
                  )}
                  mode="date"
                  display={Platform.OS === "ios" ? "inline" : "default"}
                  onChange={onDatePicked}
                  minimumDate={datePickerFor === "to" && editor?.valid_from ? new Date(editor.valid_from) : undefined}
                />
              ) : null}
              {datePickerFor && Platform.OS === "ios" ? (
                <TouchableOpacity onPress={() => setDatePickerFor(null)} style={styles.datePickDone}>
                  <Text style={styles.datePickDoneTxt}>Done</Text>
                </TouchableOpacity>
              ) : null}
              {datePickerFor && Platform.OS === "web" ? (
                // Fallback web input — native picker isn't available on web.
                // We render a plain YYYY-MM-DD field so the admin can still
                // test / use the feature from a browser.
                <View>
                  <TextInput
                    value={(datePickerFor === "from" ? editor?.valid_from : editor?.valid_to || "").slice(0, 10)}
                    onChangeText={(t) => {
                      if (!editor) return;
                      const iso = t.length === 10 ? `${t}T00:00:00.000Z` : t;
                      if (datePickerFor === "from") setEditor({ ...editor, valid_from: iso });
                      else setEditor({ ...editor, valid_to: iso });
                    }}
                    placeholder="YYYY-MM-DD"
                    style={styles.input}
                    placeholderTextColor="#94A3B8"
                  />
                  <TouchableOpacity onPress={() => setDatePickerFor(null)} style={styles.datePickDone}>
                    <Text style={styles.datePickDoneTxt}>Done</Text>
                  </TouchableOpacity>
                </View>
              ) : null}

              <Text style={styles.lbl}>Max uses (blank = unlimited)</Text>
              <TextInput
                value={editor?.max_uses == null ? "" : String(editor.max_uses)}
                onChangeText={(t) => {
                  const cleaned = t.replace(/[^0-9]/g, "");
                  setEditor({ ...editor!, max_uses: cleaned === "" ? null : Number(cleaned) });
                }}
                keyboardType="number-pad"
                placeholder="100"
                style={styles.input}
                placeholderTextColor="#94A3B8"
              />

              <Text style={styles.lbl}>Applies to plans (none = all paid)</Text>
              <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
                {PAID_PLANS.map((p) => {
                  const on = (editor?.applies_to_plans || []).includes(p.key);
                  return (
                    <TouchableOpacity key={p.key} onPress={() => togglePlan(p.key)} style={[styles.chip, on && styles.chipActive]}>
                      <Text style={[styles.chipTxt, on && { color: "#fff" }]}>{p.name}</Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

              <Text style={styles.lbl}>Billing cycles (none = both)</Text>
              <View style={{ flexDirection: "row", gap: 8 }}>
                {(["monthly", "yearly"] as const).map((c) => {
                  const on = (editor?.billing_cycles || []).includes(c);
                  return (
                    <TouchableOpacity key={c} onPress={() => toggleCycle(c)} style={[styles.chip, on && styles.chipActive]}>
                      <Text style={[styles.chipTxt, on && { color: "#fff" }]}>
                        {c === "monthly" ? "Monthly" : "Yearly"}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>

              {/* Restricted-to-users emails (Bulk distribution) */}
              <Text style={styles.lbl}>Restrict to specific users (optional)</Text>
              <Text style={styles.hint}>
                Enter customer emails (comma / newline separated). If blank, anyone can use this
                code. Use "Share" on the coupon row to pre-fill a WhatsApp message with the code.
              </Text>
              <TextInput
                value={emailsRaw}
                onChangeText={setEmailsRaw}
                placeholder="customer1@example.com, customer2@example.com"
                autoCapitalize="none"
                keyboardType="email-address"
                multiline
                style={[styles.input, { minHeight: 60, textAlignVertical: "top" }]}
                placeholderTextColor="#94A3B8"
              />
              {emailsRaw.trim() ? (
                <Text style={styles.hint}>
                  {parseEmailList(emailsRaw).length} email(s) parsed from this list.
                </Text>
              ) : null}

              <View style={[styles.rowSwitch, { marginTop: 16 }]}>
                <Text style={styles.lbl2}>Active</Text>
                <Switch
                  value={!!editor?.active}
                  onValueChange={(v) => setEditor({ ...editor!, active: v })}
                />
              </View>

              <TouchableOpacity onPress={saveEditor} disabled={saving} style={[styles.saveBtn, saving && { opacity: 0.6 }]}>
                {saving ? <ActivityIndicator color="#fff" /> : (
                  <Text style={styles.saveBtnTxt}>
                    {editor?.id ? "Save changes" : "Create coupon"}
                  </Text>
                )}
              </TouchableOpacity>
            </ScrollView>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:        { flex: 1, backgroundColor: "#F8FAFC" },
  header:      { flexDirection: "row", alignItems: "center", padding: 12, gap: 8, backgroundColor: "#fff", borderBottomWidth: 1, borderBottomColor: "#E5E7EB" },
  headerBtn:   { padding: 8, borderRadius: 8 },
  primaryBtn:  { backgroundColor: colors.primary, marginLeft: "auto" },
  headerTitle: { fontWeight: "800", fontSize: 18, color: colors.text, marginLeft: 4, flex: 1 },
  helpTxt:     { fontSize: 13, color: "#475569", marginTop: 4, marginBottom: 12, lineHeight: 18 },
  center:      { flex: 1, alignItems: "center", justifyContent: "center" },
  // Dashboard
  dashCard:    { backgroundColor: "#fff", borderRadius: 14, padding: 16, marginBottom: 16, borderWidth: 1, borderColor: "#E5E7EB" },
  dashTitle:   { fontSize: 15, fontWeight: "800", color: colors.text, marginBottom: 10 },
  dashSubTitle:{ fontSize: 13, fontWeight: "800", color: "#475569", marginTop: 12, marginBottom: 6 },
  dashHint:    { color: "#94A3B8", fontSize: 12, marginTop: 10, fontStyle: "italic" },
  statRow:     { flexDirection: "row", gap: 8, marginBottom: 8 },
  statBox:     { flex: 1, padding: 10, backgroundColor: "#F8FAFC", borderRadius: 8, alignItems: "center" },
  statNum:     { fontSize: 20, fontWeight: "900", color: colors.primary },
  statLbl:     { fontSize: 11, color: "#64748B", marginTop: 2 },
  statusRow:   { flexDirection: "row", gap: 6, flexWrap: "wrap", marginTop: 4 },
  miniPill:    { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999 },
  miniPillTxt: { fontSize: 11, fontWeight: "700" },
  topRow:      { flexDirection: "row", justifyContent: "space-between", paddingVertical: 6, borderTopWidth: 1, borderTopColor: "#F1F5F9" },
  topCode:     { fontWeight: "800", color: colors.text, fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) },
  topMeta:     { color: "#64748B", fontSize: 12 },
  // List
  emptyCard:   { padding: 24, backgroundColor: "#fff", borderRadius: 12, alignItems: "center", marginTop: 24 },
  emptyTxt:    { color: "#475569", marginTop: 8, fontSize: 14 },
  emptyBtn:    { marginTop: 12, backgroundColor: colors.primary, paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  emptyBtnTxt: { color: "#fff", fontWeight: "700" },
  couponRow:   { backgroundColor: "#fff", borderRadius: 12, padding: 14, marginBottom: 12, borderWidth: 1, borderColor: "#E5E7EB" },
  rowTop:      { flexDirection: "row", alignItems: "center", marginBottom: 6 },
  codeTxt:     { fontSize: 16, fontWeight: "800", color: colors.text, flex: 1, fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) },
  statusPill:  { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  statusTxt:   { fontSize: 11, fontWeight: "800", textTransform: "uppercase" },
  discountTxt: { color: "#047857", fontWeight: "800", fontSize: 14, marginBottom: 4 },
  metaTxt:     { color: "#64748B", fontSize: 12, lineHeight: 16 },
  rowActions:  { flexDirection: "row", gap: 8, marginTop: 10 },
  smallBtn:    { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6, backgroundColor: "#EFF6FF" },
  smallBtnDanger: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6, backgroundColor: "#FEE2E2" },
  smallBtnSuccess: { flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 6, backgroundColor: "#DCFCE7" },
  smallBtnTxt: { fontWeight: "700", color: colors.primary, fontSize: 12 },
  // Modal
  modalBg:     { flex: 1, backgroundColor: "rgba(15, 23, 42, 0.55)", justifyContent: "flex-end" },
  modalCard:   { backgroundColor: "#fff", borderTopLeftRadius: 20, borderTopRightRadius: 20, maxHeight: "92%" },
  modalHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", padding: 16, borderBottomWidth: 1, borderBottomColor: "#E5E7EB" },
  modalTitle:  { fontSize: 16, fontWeight: "800", color: colors.text },
  lbl:         { fontWeight: "700", color: colors.text, fontSize: 13, marginTop: 14, marginBottom: 6 },
  lbl2:        { fontWeight: "700", color: colors.text, fontSize: 14, flex: 1 },
  hint:        { color: "#94A3B8", fontSize: 11, marginTop: 4 },
  input:       { backgroundColor: "#F1F5F9", borderRadius: 8, padding: 12, fontSize: 14, color: colors.text },
  rowSwitch:   { flexDirection: "row", alignItems: "center" },
  typeBtn:     { paddingHorizontal: 14, paddingVertical: 10, backgroundColor: "#F1F5F9", borderRadius: 8, flex: 1, alignItems: "center" },
  typeBtnActive: { backgroundColor: colors.primary },
  typeBtnTxt:  { fontWeight: "700", color: colors.text },
  chip:        { paddingHorizontal: 12, paddingVertical: 8, backgroundColor: "#F1F5F9", borderRadius: 999 },
  chipActive:  { backgroundColor: colors.primary },
  chipTxt:     { fontWeight: "700", color: colors.text, fontSize: 13 },
  saveBtn:     { backgroundColor: colors.primary, padding: 14, borderRadius: 10, alignItems: "center", marginTop: 20 },
  saveBtnTxt:  { color: "#fff", fontWeight: "800", fontSize: 15 },
  dateBtn:     { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: "#EFF6FF", borderRadius: 8, padding: 12, borderWidth: 1, borderColor: "#BFDBFE" },
  dateBtnTxt:  { fontSize: 14, fontWeight: "700", color: colors.primary },
  datePickDone: { backgroundColor: "#F1F5F9", borderRadius: 8, padding: 10, alignItems: "center", marginTop: 8 },
  datePickDoneTxt: { fontWeight: "700", color: colors.text },
});
