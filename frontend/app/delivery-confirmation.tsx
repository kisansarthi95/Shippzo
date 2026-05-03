/**
 * Delivery Confirmation (Phase-12 — refreshed)
 * --------------------------------------------
 * Post-shipping confirmation system. Once a parcel has been "Shipped"
 * for >= the per-courier `delivery_eta_days` (resolved via courier
 * rules), it lands here. Admin selects rows, taps "Send WhatsApp" to
 * fire the resolved `delivery_confirmation` template, then optionally
 * "Mark as Delivered" after the customer confirms.
 *
 * Phase-12 changes:
 *   • Per-courier ETA rules (Demo Courier 5d, Indian Post 8d, …)
 *   • Editable rules via the "Edit Rule" pill (per-user override).
 *   • Multi-language template ping (gu / hi / en) — language pill in
 *     the banner; user's saved default applies by default.
 *   • Template content pulled from server (resolveTemplate cascade).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Linking,
  Alert,
  Platform,
  Modal,
  Pressable,
  TextInput,
  KeyboardAvoidingView,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Stack } from "expo-router";
import { Api, Shipment } from "../lib/api";
import {
  preflightBatchWhatsApp,
  openWhatsAppShare,
  requestWhatsAppSend,
} from "../lib/whatsappGuard";
import DailyLimitBanner from "../components/DailyLimitBanner";

type ConfStatus = "pending" | "sent" | "replied" | "confirmed" | "failed";
type Tab = "list" | "sent" | "replied" | "pending";

type ConfShipment = Shipment & {
  days_since_shipped: number;
  courier_eta_days: number;
};

const STATUS_STYLE: Record<ConfStatus, { bg: string; fg: string; label: string }> = {
  pending:   { bg: "#FFF3E0", fg: "#B45309", label: "Pending" },
  sent:      { bg: "#EEE9FF", fg: "#6B5BFF", label: "Sent" },
  replied:   { bg: "#DDEFFF", fg: "#1E40AF", label: "Replied" },
  confirmed: { bg: "#E6F7EE", fg: "#1F9D55", label: "Confirmed" },
  failed:    { bg: "#FFE5E5", fg: "#991B1B", label: "Failed" },
};

const LANG_OPTIONS = [
  { key: "gu", label: "ગુજરાતી" },
  { key: "hi", label: "हिन्दी" },
  { key: "en", label: "English" },
];

// ---------------------------------------------------------------------------
// Inline Edit-Rule modal — lets the user override delivery_eta_days per
// courier. Falls back to admin defaults / global default when unset.
// ---------------------------------------------------------------------------

type RuleEditorProps = {
  visible: boolean;
  onClose: () => void;
  onSaved: () => void;
};

function CourierRulesEditor({ visible, onClose, onSaved }: RuleEditorProps) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [adminRules, setAdminRules] = useState<
    Record<string, { delivery_eta_days: number }>
  >({});
  const [userRules, setUserRules] = useState<
    Record<string, { delivery_eta_days: number | string }>
  >({});
  const [courierNames, setCourierNames] = useState<string[]>([]);
  const [defaultEta, setDefaultEta] = useState(5);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await Api.meCourierRules();
      setAdminRules(data.admin_rules || {});
      setUserRules(data.user_rules || {});
      setCourierNames(data.courier_names || []);
      setDefaultEta(data.default_eta_days || 5);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (visible) load(); }, [visible, load]);

  // Determine the list of couriers to show: union of user's couriers,
  // admin-rule keys, and a fallback "_default_" row that sets the
  // catch-all override for any courier not explicitly listed.
  const rows = useMemo(() => {
    const set = new Set<string>(courierNames);
    Object.keys(adminRules).forEach((k) => set.add(k));
    Object.keys(userRules).forEach((k) => set.add(k));
    set.delete("_default_");
    const arr = Array.from(set).sort();
    // _default_ pinned at the top so users always see the fallback.
    return ["_default_", ...arr];
  }, [courierNames, adminRules, userRules]);

  const renderEta = (key: string): string => {
    const u = userRules[key]?.delivery_eta_days;
    if (u !== undefined && u !== "") return String(u);
    const a = adminRules[key]?.delivery_eta_days;
    if (a !== undefined && a !== null) return String(a);
    return String(defaultEta);
  };

  const setEta = (key: string, val: string) => {
    // Only digits, blank means "clear override".
    const sanitized = val.replace(/[^0-9]/g, "");
    setUserRules((prev) => {
      const next = { ...prev };
      if (sanitized === "") {
        delete next[key];
      } else {
        next[key] = { delivery_eta_days: sanitized };
      }
      return next;
    });
  };

  const save = async () => {
    setSaving(true);
    try {
      // Cast strings to ints; drop blanks server-side does too but
      // doing it here keeps the UI honest about what'll persist.
      const rules: Record<string, { delivery_eta_days: number }> = {};
      for (const [k, v] of Object.entries(userRules)) {
        const n = parseInt(String(v.delivery_eta_days), 10);
        if (!Number.isFinite(n) || n < 0 || n > 60) continue;
        rules[k] = { delivery_eta_days: n };
      }
      await Api.meSaveCourierRules(rules);
      onSaved();
      onClose();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={editor.scrim}
      >
        <View style={editor.sheet}>
          <View style={editor.sheetHeader}>
            <Text style={editor.sheetTitle}>Edit Courier Rules</Text>
            <TouchableOpacity onPress={onClose} hitSlop={12}>
              <Ionicons name="close" size={22} color="#374151" />
            </TouchableOpacity>
          </View>
          <Text style={editor.sheetHint}>
            Set how many days after shipping each courier typically takes to
            deliver. We'll auto-flag parcels for delivery confirmation once
            they cross this threshold.
          </Text>
          {loading ? (
            <ActivityIndicator color="#6B5BFF" style={{ marginVertical: 24 }} />
          ) : (
            <ScrollView
              style={{ maxHeight: 380 }}
              contentContainerStyle={{ paddingBottom: 8 }}
              keyboardShouldPersistTaps="handled"
            >
              {rows.map((key) => {
                const isDefault = key === "_default_";
                const placeholder = adminRules[key]?.delivery_eta_days
                  ? String(adminRules[key].delivery_eta_days)
                  : String(defaultEta);
                const userVal = userRules[key]?.delivery_eta_days;
                return (
                  <View key={key} style={editor.row}>
                    <View style={{ flex: 1 }}>
                      <Text style={editor.rowName}>
                        {isDefault ? "All other couriers (default)" : key}
                      </Text>
                      {!isDefault && adminRules[key]?.delivery_eta_days !== undefined && (
                        <Text style={editor.rowHint}>
                          Admin default: {adminRules[key].delivery_eta_days} days
                        </Text>
                      )}
                    </View>
                    <View style={editor.inputWrap}>
                      <TextInput
                        style={editor.input}
                        keyboardType="number-pad"
                        value={userVal === undefined ? "" : String(userVal)}
                        onChangeText={(t) => setEta(key, t)}
                        placeholder={placeholder}
                        placeholderTextColor="#9CA3AF"
                        maxLength={2}
                      />
                      <Text style={editor.inputSuffix}>days</Text>
                    </View>
                  </View>
                );
              })}
            </ScrollView>
          )}
          <View style={editor.actions}>
            <TouchableOpacity
              style={[editor.btnGhost, saving && { opacity: 0.5 }]}
              onPress={onClose}
              disabled={saving}
            >
              <Text style={editor.btnGhostText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[editor.btnPrimary, saving && { opacity: 0.5 }]}
              onPress={save}
              disabled={saving}
            >
              <Text style={editor.btnPrimaryText}>
                {saving ? "Saving…" : "Save Rules"}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main screen
// ---------------------------------------------------------------------------

export default function DeliveryConfirmation() {
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("list");
  const [rows, setRows] = useState<ConfShipment[]>([]);
  const [counts, setCounts] = useState({ list: 0, sent: 0, replied: 0, pending: 0 });
  const [etaRange, setEtaRange] = useState({ min: 5, max: 5 });
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [editorOpen, setEditorOpen] = useState(false);
  const [defaultLang, setDefaultLang] = useState("gu");
  const [langPickerOpen, setLangPickerOpen] = useState(false);
  const [templateCache, setTemplateCache] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [res, tplMeta] = await Promise.all([
        Api.deliveryConfListV2(),
        Api.meWhatsAppTemplates().catch(() => null),
      ]);
      setRows(res.shipments || []);
      setCounts(res.counts);
      setEtaRange({ min: res.eta_min, max: res.eta_max });
      if (tplMeta) setDefaultLang(tplMeta.default_language || "gu");
      setSelected((prev) => {
        const keep: Record<string, boolean> = {};
        const ids = new Set((res.shipments || []).map((r) => r.id));
        for (const k of Object.keys(prev)) if (ids.has(k) && prev[k]) keep[k] = true;
        return keep;
      });
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const visible = useMemo(() => {
    if (tab === "list") return rows;
    return rows.filter((r) => {
      const cs = (r as any).confirmation_status || "pending";
      if (tab === "sent")    return cs === "sent";
      if (tab === "replied") return cs === "replied";
      if (tab === "pending") return cs === "pending";
      return false;
    });
  }, [rows, tab]);

  const selectedIds = useMemo(
    () => Object.keys(selected).filter((k) => selected[k]),
    [selected],
  );
  const allSelected = visible.length > 0 && visible.every((r) => selected[r.id]);

  const toggleOne = (id: string) =>
    setSelected((p) => ({ ...p, [id]: !p[id] }));
  const toggleAll = () => {
    if (allSelected) {
      setSelected({});
    } else {
      const next: Record<string, boolean> = {};
      for (const r of visible) next[r.id] = true;
      setSelected(next);
    }
  };

  // Substitute template variables.
  const fillTemplate = (template: string, s: ConfShipment): string => {
    const vars: Record<string, string> = {
      customer_name: String(s.customer_name || ""),
      order_id: String(s.order_id || s.tracking_id || ""),
      tracking_id: String(s.tracking_id || ""),
      courier: String(s.courier_name || ""),
      eta_days: String(s.courier_eta_days || ""),
    };
    return template.replace(/\{(\w+)\}/g, (_m, k) => vars[k] ?? "");
  };

  const getTemplate = useCallback(
    async (lang: string): Promise<string> => {
      if (templateCache[lang]) return templateCache[lang];
      try {
        const res = await Api.resolveTemplate("delivery_confirmation", lang);
        setTemplateCache((p) => ({ ...p, [lang]: res.template }));
        return res.template;
      } catch {
        return "";
      }
    },
    [templateCache],
  );

  const openWhatsApp = async (phone: string, message: string): Promise<boolean> => {
    const digits = (phone || "").replace(/\D/g, "");
    if (digits.length < 10) return false;
    const e164 = digits.length === 10 ? "91" + digits : digits;
    const url = `whatsapp://send?phone=${e164}&text=${encodeURIComponent(message)}`;
    try {
      const ok = await Linking.canOpenURL(url);
      if (!ok) {
        await Linking.openURL(`https://wa.me/${e164}?text=${encodeURIComponent(message)}`);
      } else {
        await Linking.openURL(url);
      }
      return true;
    } catch {
      try {
        await Linking.openURL(`https://wa.me/${e164}?text=${encodeURIComponent(message)}`);
        return true;
      } catch {
        return false;
      }
    }
  };

  const handleSendWhatsApp = async () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels you want to message.");
      return;
    }
    const lang = defaultLang || "gu";
    const tpl = await getTemplate(lang);
    if (!tpl) {
      Alert.alert(
        "Template missing",
        "Couldn't load the delivery template. Configure it in Settings → WhatsApp Templates.",
      );
      return;
    }
    // Phase-15 D: pre-flight WhatsApp daily limit ONCE for the batch.
    const guard = await preflightBatchWhatsApp(selectedIds.length, {
      batchLabel: "delivery confirmation",
    });
    if (!guard.ok) return;

    setBusy(true);
    try {
      const markRes = await Api.deliveryConfMarkSent(selectedIds);
      const ok = markRes.updated;
      const skipped = markRes.skipped;
      const toMsg = rows.filter((r) => markRes.updated_ids.includes(r.id));
      let opened = 0;
      let limitHit = false;
      for (const r of toMsg) {
        if (limitHit) break;
        const phone = String(r.customer_phone || "").trim();
        const msg = fillTemplate(tpl, r);
        try {
          await Api.meWhatsAppDailyIncrement(guard.force);
        } catch {
          limitHit = true;
          break;
        }
        const ok2 = await openWhatsAppShare(phone, msg);
        if (ok2) opened += 1;
        if (Platform.OS === "android") {
          await new Promise((r) => setTimeout(r, 350));
        }
      }
      Alert.alert(
        "WhatsApp sent",
        `${opened} chat(s) opened.\n${ok} marked sent.\n${skipped} skipped (already sent today).` +
          (limitHit ? "\n\n⚠️ Stopped: WhatsApp daily limit hit mid-batch." : ""),
      );
      setSelected({});
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setBusy(false);
    }
  };

  const handleMarkDelivered = async () => {
    if (selectedIds.length === 0) {
      Alert.alert("No selection", "Tick the parcels the customer has confirmed.");
      return;
    }
    Alert.alert(
      "Mark as Delivered",
      `Confirm ${selectedIds.length} parcel(s) as delivered? This cannot be undone.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Confirm",
          style: "destructive",
          onPress: async () => {
            setBusy(true);
            try {
              const res = await Api.deliveryConfMarkDelivered(selectedIds);
              Alert.alert("Done", `${res.updated} of ${res.requested} marked Delivered.`);
              setSelected({});
              load();
            } catch (e: any) {
              Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
            } finally {
              setBusy(false);
            }
          },
        },
      ],
    );
  };

  const ruleLabel =
    etaRange.min === etaRange.max
      ? `${etaRange.min} days rule`
      : `${etaRange.min}–${etaRange.max} days rule`;

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen
        options={{ title: "Delivery Confirmation", headerBackTitle: "Back" }}
      />

      <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 120 }}>
        {/* Phase-15 D — anti-block daily WhatsApp limit indicator. */}
        <DailyLimitBanner variant="strip" showAtPct={50} />

        {/* Top banner */}
        <View style={styles.banner}>
          <View style={styles.bannerIcon}>
            <Ionicons name="alert-circle" size={22} color="#B45309" />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={styles.bannerTitle}>
              Need Confirmation · {counts.list}{" "}
              <Text style={styles.bannerSub}>parcels</Text>
            </Text>
            <Text style={styles.bannerHint}>
              Based on courier {ruleLabel}
            </Text>
          </View>
          <TouchableOpacity
            style={styles.editPill}
            onPress={() => setEditorOpen(true)}
            testID="dc-edit-rule"
          >
            <Ionicons name="create-outline" size={14} color="#1F2937" />
            <Text style={styles.editPillText}>Edit Rule</Text>
          </TouchableOpacity>
        </View>

        {/* Language pill row */}
        <View style={styles.langRow}>
          <Text style={styles.langRowLabel}>Send messages in:</Text>
          <TouchableOpacity
            style={styles.langPill}
            onPress={() => setLangPickerOpen(true)}
          >
            <Text style={styles.langPillText}>
              {LANG_OPTIONS.find((l) => l.key === defaultLang)?.label || "Lang"}
            </Text>
            <Ionicons name="chevron-down" size={14} color="#6B5BFF" />
          </TouchableOpacity>
        </View>

        {/* Tabs */}
        <View style={styles.tabs}>
          {(["list", "sent", "replied", "pending"] as Tab[]).map((t) => {
            const active = tab === t;
            const n =
              t === "list" ? counts.list :
              t === "sent" ? counts.sent :
              t === "replied" ? counts.replied : counts.pending;
            return (
              <TouchableOpacity
                key={t}
                style={[styles.tabBtn, active && styles.tabBtnActive]}
                onPress={() => setTab(t)}
                testID={`dc-tab-${t}`}
              >
                <Text style={[styles.tabText, active && styles.tabTextActive]}>
                  {t === "list" ? "List" : t === "sent" ? "Sent" : t === "replied" ? "Replied" : "Pending"}
                </Text>
                <View style={[styles.tabBadge, active && { backgroundColor: "rgba(255,255,255,0.25)" }]}>
                  <Text style={[styles.tabBadgeText, active && { color: "#fff" }]}>{n}</Text>
                </View>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* Select-all row */}
        {visible.length > 0 && (
          <TouchableOpacity style={styles.selectAll} onPress={toggleAll}>
            <Ionicons
              name={allSelected ? "checkbox" : "square-outline"}
              size={20}
              color={allSelected ? "#6B5BFF" : "#9CA3AF"}
            />
            <Text style={styles.selectAllText}>
              {allSelected ? "Deselect all" : "Select all"}
              <Text style={styles.selectAllCount}>
                {"  · " + visible.length + " visible, " + selectedIds.length + " selected"}
              </Text>
            </Text>
          </TouchableOpacity>
        )}

        {/* List */}
        {loading ? (
          <ActivityIndicator color="#6B5BFF" style={{ marginVertical: 36 }} />
        ) : visible.length === 0 ? (
          <View style={styles.empty}>
            <Ionicons name="checkmark-circle" size={36} color="#D4D4D8" />
            <Text style={styles.emptyText}>No parcels in this bucket. All caught up!</Text>
          </View>
        ) : (
          visible.map((r) => {
            const cs = ((r as any).confirmation_status || "pending") as ConfStatus;
            const meta = STATUS_STYLE[cs];
            const sel = !!selected[r.id];
            const phone = String(r.customer_phone || "").trim();
            return (
              <View key={r.id} style={[styles.row, sel && styles.rowSel]}>
                <TouchableOpacity onPress={() => toggleOne(r.id)} hitSlop={8}>
                  <Ionicons
                    name={sel ? "checkbox" : "square-outline"}
                    size={22}
                    color={sel ? "#6B5BFF" : "#9CA3AF"}
                  />
                </TouchableOpacity>
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.rowOrder}>
                    {r.order_id || r.tracking_id}
                    <Text style={styles.rowDaysTag}>
                      {"  "}· {r.days_since_shipped}d / {r.courier_eta_days}d
                    </Text>
                  </Text>
                  <Text style={styles.rowName} numberOfLines={1}>
                    {(r as any).customer_name || "—"}
                    {phone ? <Text style={styles.rowPhone}>{"  " + phone}</Text> : null}
                  </Text>
                  <Text style={styles.rowCourier} numberOfLines={1}>
                    {(r as any).courier_name || "Courier —"}
                  </Text>
                </View>
                <View style={{ alignItems: "flex-end", gap: 6 }}>
                  <View style={[styles.statusBadge, { backgroundColor: meta.bg }]}>
                    <Text style={[styles.statusBadgeText, { color: meta.fg }]}>{meta.label}</Text>
                  </View>
                  <TouchableOpacity
                    onPress={async () => {
                      const tpl = await getTemplate(defaultLang);
                      if (!tpl) return;
                      await requestWhatsAppSend(phone, fillTemplate(tpl, r), {
                        templateLabel: "Delivery confirmation",
                      });
                    }}
                    hitSlop={8}
                  >
                    <Ionicons name="logo-whatsapp" size={20} color="#25D366" />
                  </TouchableOpacity>
                </View>
              </View>
            );
          })
        )}
      </ScrollView>

      {/* Sticky bottom action bar */}
      <View style={styles.stickyBar}>
        <Text style={styles.selCountText}>
          {selectedIds.length > 0 ? `${selectedIds.length} selected` : "Select parcels above"}
        </Text>
        <TouchableOpacity
          style={[styles.waBtn, (selectedIds.length === 0 || busy) && styles.btnDisabled]}
          onPress={handleSendWhatsApp}
          disabled={selectedIds.length === 0 || busy}
          testID="dc-send-whatsapp"
        >
          <Ionicons name="logo-whatsapp" size={16} color="#fff" />
          <Text style={styles.waBtnText}>Send WhatsApp</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.deliveredBtn, (selectedIds.length === 0 || busy) && styles.btnDisabled]}
          onPress={handleMarkDelivered}
          disabled={selectedIds.length === 0 || busy}
          testID="dc-mark-delivered"
        >
          <Ionicons name="checkmark-done" size={16} color="#fff" />
          <Text style={styles.deliveredBtnText}>Mark as Delivered</Text>
        </TouchableOpacity>
      </View>

      <CourierRulesEditor
        visible={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSaved={load}
      />

      {/* Language picker modal */}
      <Modal
        visible={langPickerOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setLangPickerOpen(false)}
      >
        <Pressable style={editor.scrim} onPress={() => setLangPickerOpen(false)}>
          <View style={[editor.sheet, { gap: 6 }]}>
            <Text style={editor.sheetTitle}>Send in language</Text>
            {LANG_OPTIONS.map((l) => (
              <TouchableOpacity
                key={l.key}
                style={[editor.row, defaultLang === l.key && { backgroundColor: "#F8F7FF" }]}
                onPress={() => {
                  setDefaultLang(l.key);
                  setTemplateCache({}); // bust cache for new language
                  setLangPickerOpen(false);
                }}
              >
                <Text style={editor.rowName}>{l.label}</Text>
                {defaultLang === l.key && (
                  <Ionicons name="checkmark" size={18} color="#6B5BFF" />
                )}
              </TouchableOpacity>
            ))}
            <Text style={[editor.sheetHint, { textAlign: "center" }]}>
              Set permanent default in Settings → WhatsApp Templates.
            </Text>
          </View>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#FFF3E0",
    borderWidth: 1, borderColor: "#FCD7A0", borderRadius: 14,
    padding: 12, gap: 12, marginBottom: 10,
  },
  bannerIcon: {
    width: 40, height: 40, borderRadius: 10,
    backgroundColor: "#FFE5BA",
    alignItems: "center", justifyContent: "center",
  },
  bannerTitle: { fontSize: 15, fontWeight: "800", color: "#7C2D12" },
  bannerSub: { fontWeight: "600", color: "#B45309" },
  bannerHint: { fontSize: 12, color: "#9A5F22", marginTop: 2 },
  editPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#fff", borderRadius: 999,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  editPillText: { fontSize: 11, fontWeight: "800", color: "#1F2937" },
  langRow: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 4, marginBottom: 10, gap: 8,
  },
  langRowLabel: { fontSize: 12, color: "#6B7280", fontWeight: "600" },
  langPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 5,
    backgroundColor: "#F8F7FF", borderRadius: 999,
    borderWidth: 1, borderColor: "#D8D2FF",
  },
  langPillText: { fontSize: 12, fontWeight: "800", color: "#6B5BFF" },
  tabs: { flexDirection: "row", gap: 6, marginBottom: 12 },
  tabBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    paddingVertical: 10, paddingHorizontal: 10, borderRadius: 10,
    backgroundColor: "#F3F4F6", borderWidth: 1, borderColor: "#E5E7EB", gap: 6,
  },
  tabBtnActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  tabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  tabTextActive: { color: "#fff" },
  tabBadge: {
    backgroundColor: "#E5E7EB", paddingHorizontal: 6,
    borderRadius: 999, minWidth: 22, alignItems: "center",
  },
  tabBadgeText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  selectAll: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingVertical: 8, paddingHorizontal: 4,
  },
  selectAllText: { fontSize: 13, fontWeight: "700", color: "#111827" },
  selectAllCount: { color: "#6B7280", fontWeight: "500" },
  empty: { alignItems: "center", paddingVertical: 32, gap: 8 },
  emptyText: { color: "#9CA3AF", fontSize: 13 },
  row: {
    flexDirection: "row", alignItems: "center", padding: 12,
    backgroundColor: "#FFF", borderRadius: 12,
    borderWidth: 1, borderColor: "#EDEEF1", marginBottom: 8,
  },
  rowSel: { borderColor: "#6B5BFF", backgroundColor: "#F8F7FF" },
  rowOrder: { fontSize: 14, fontWeight: "800", color: "#111827" },
  rowDaysTag: { color: "#F97316", fontWeight: "800", fontSize: 12 },
  rowName: { marginTop: 2, fontSize: 12, color: "#374151" },
  rowPhone: { color: "#6B7280", fontWeight: "500" },
  rowCourier: { marginTop: 2, fontSize: 11, color: "#9CA3AF" },
  statusBadge: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
    minWidth: 70, alignItems: "center",
  },
  statusBadgeText: { fontSize: 11, fontWeight: "800" },
  stickyBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    backgroundColor: "#FFF", borderTopWidth: 1, borderColor: "#EDEEF1",
    paddingHorizontal: 10, paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 12,
    flexDirection: "row", alignItems: "center", gap: 8,
  },
  selCountText: {
    fontSize: 12, fontWeight: "700",
    color: "#111827", marginRight: 4,
  },
  waBtn: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center",
    gap: 5, paddingVertical: 12, borderRadius: 10,
    backgroundColor: "#6B5BFF",
  },
  waBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  deliveredBtn: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center",
    gap: 5, paddingVertical: 12, borderRadius: 10,
    backgroundColor: "#1F9D55",
  },
  deliveredBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
  btnDisabled: { opacity: 0.5 },
});

const editor = StyleSheet.create({
  scrim: {
    flex: 1, backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    padding: 16, paddingBottom: Platform.OS === "ios" ? 28 : 16,
    gap: 8,
  },
  sheetHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between",
  },
  sheetTitle: { fontSize: 17, fontWeight: "800", color: "#111827" },
  sheetHint: { fontSize: 12, color: "#6B7280", lineHeight: 17, marginBottom: 4 },
  row: {
    flexDirection: "row", alignItems: "center",
    paddingVertical: 10, paddingHorizontal: 10,
    borderRadius: 10, backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#EDEEF1",
    marginBottom: 6, gap: 10,
  },
  rowName: { fontSize: 14, fontWeight: "700", color: "#111827" },
  rowHint: { fontSize: 11, color: "#9CA3AF", marginTop: 2 },
  inputWrap: {
    flexDirection: "row", alignItems: "center",
    backgroundColor: "#fff", borderRadius: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 8,
  },
  input: {
    width: 42, paddingVertical: 6,
    fontSize: 14, fontWeight: "700", color: "#111827",
    textAlign: "right",
  },
  inputSuffix: { fontSize: 11, color: "#9CA3AF", marginLeft: 4 },
  actions: { flexDirection: "row", gap: 10, marginTop: 8 },
  btnGhost: {
    flex: 1, paddingVertical: 12, borderRadius: 10,
    backgroundColor: "#F3F4F6", alignItems: "center",
  },
  btnGhostText: { fontSize: 13, fontWeight: "800", color: "#374151" },
  btnPrimary: {
    flex: 1, paddingVertical: 12, borderRadius: 10,
    backgroundColor: "#6B5BFF", alignItems: "center",
  },
  btnPrimaryText: { fontSize: 13, fontWeight: "800", color: "#fff" },
});
