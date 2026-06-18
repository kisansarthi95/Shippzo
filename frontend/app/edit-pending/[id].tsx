/**
 * /app/edit-pending/[id].tsx — Edit a pending order before shipping.
 *
 * Phase C (May 2026): every pending-order card on the Orders tab now
 * carries an "Edit" affordance. Tapping it lands here. The form is
 * pre-filled from the source PendingOrder document (paste / file /
 * webhook) so the user can clean up sloppy parser output, fix a wrong
 * pincode, or attach extra notes BEFORE shipping creates an immutable
 * Shipment row.
 *
 * Sheet rows skip this screen entirely: their "Edit" affordance routes
 * to /(tabs)/add with prefill instead, because sheet rows live on the
 * user's Google Sheet, not in pending_orders, and the Add-Shipment
 * form already accepts those prefill params.
 *
 * Back-without-save guard:
 *   • If the user navigates away with no field touched → silent back.
 *   • If any field has been edited → 3-button confirm dialog
 *       Save Changes  → PUT + back
 *       Discard       → revert + back (pending row stays untouched)
 *       Continue      → close dialog, stay on form
 */
import { Stack, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator, Alert, BackHandler, KeyboardAvoidingView, Linking,
  Platform, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import PhIcon from "../../components/PhIcon";
import { Api, type PendingOrder } from "../../lib/api";


type FieldDef = {
  key: keyof PendingOrder;
  label: string;
  placeholder?: string;
  keyboardType?: "default" | "phone-pad" | "numeric" | "email-address";
  multiline?: boolean;
};

/**
 * Build a single unified address string from a PendingOrder.
 * Mirrors the helper used in `(tabs)/add.tsx` so both screens
 * pre-fill the same "full address" value regardless of which
 * shape the upstream produced (unified `address` string, legacy
 * `address_line1`+`address_line2`, or only `address_line1`).
 * Caps at 300 chars to match the input's hard limit.
 */
function unifiedAddressFrom(o: any): string {
  const unified = String(o?.address || "").trim();
  if (unified) return unified.slice(0, 300);
  const l1 = String(o?.address_line1 || "").trim();
  const l2 = String(o?.address_line2 || "").trim();
  if (l1 && l2 && l2 !== "-") return `${l1}, ${l2}`.slice(0, 300);
  return (l1 || l2).slice(0, 300);
}

const FIELD_GROUPS: { title: string; fields: FieldDef[] }[] = [
  {
    title: "Customer",
    fields: [
      { key: "customer_name",      label: "Name",       placeholder: "Riya Patel" },
      { key: "customer_phone",     label: "Phone",      keyboardType: "phone-pad" },
      { key: "customer_alt_phone", label: "Alt Phone",  keyboardType: "phone-pad" },
      { key: "customer_email",     label: "Email",      keyboardType: "email-address" },
      { key: "customer_gstin",     label: "GSTIN" },
    ],
  },
  {
    title: "Address",
    fields: [
      // Phase-6 (2026-04-28) — Unified address model.
      // `add.tsx` uses a single full-address field; the legacy
      // `address_line1` + `address_line2` split caused Smart Paste
      // to truncate addresses on the first comma. We now show ONE
      // multiline field (300-char cap) and write it into
      // `address_line1` on save, blanking `address_line2`. Both
      // legacy fields are still merged for the initial value so
      // older webhook orders are not surprised on first open.
      { key: "address_line1", label: "Address",       multiline: true },
      { key: "city",          label: "City" },
      { key: "state",         label: "State" },
      { key: "pincode",       label: "Pincode",       keyboardType: "numeric" },
    ],
  },
  {
    title: "Order",
    fields: [
      { key: "order_id",     label: "Order ID" },
      { key: "items",        label: "Items / Products", multiline: true },
      { key: "amount",       label: "COD to Collect (₹)", keyboardType: "numeric" },
      { key: "token_amount", label: "Token / Advance",  keyboardType: "numeric" },
      { key: "payment_mode", label: "Payment Mode (COD / PAID)" },
      { key: "weight",       label: "Weight" },
      { key: "category",     label: "Category" },
      { key: "notes",        label: "Notes / Remarks", multiline: true },
    ],
  },
];


export default function EditPendingScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ id: string }>();
  const id = params.id || "";

  const [original, setOriginal] = useState<PendingOrder | null>(null);
  const [draft, setDraft]       = useState<Partial<PendingOrder>>({});
  const [loading, setLoading]   = useState(true);
  const [saving, setSaving]     = useState(false);

  // Tracks "has the user touched any field since load?" — flips to
  // true on the very first onChangeText for any input. We use a ref
  // (not state) so the BackHandler closure always reads the latest
  // value without re-binding.
  const dirtyRef = useRef(false);

  // ── Load source order ───────────────────────────────────────────
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!id) {
        Alert.alert("Missing order ID");
        router.back();
        return;
      }
      try {
        // No dedicated /pending/<id> client method — fetch all and find.
        // Pending lists are tiny (<500), this is fine.
        const all = await Api.listPendingOrders({});
        const found = all.find((o) => o.id === id);
        if (!alive) return;
        if (!found) {
          Alert.alert("Order not found");
          router.back();
          return;
        }
        setOriginal(found);
        setDraft({});  // empty draft means "no changes yet"
      } catch (e: any) {
        if (alive) {
          Alert.alert("Couldn't load", e?.message || "Try again.");
          router.back();
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [id]);

  // ── Save handler ────────────────────────────────────────────────
  const save = useCallback(async (): Promise<boolean> => {
    if (!original) return false;
    if (Object.keys(draft).length === 0) return true;
    setSaving(true);
    try {
      // Numeric coercion for safety.
      const payload: any = { ...draft };
      ["amount", "token_amount", "box_length", "box_width", "box_height"].forEach((k) => {
        if (payload[k] !== undefined) {
          const v = parseFloat(String(payload[k]));
          payload[k] = Number.isNaN(v) ? 0 : v;
        }
      });
      await Api.updatePendingOrder(original.id, payload);
      dirtyRef.current = false;
      return true;
    } catch (e: any) {
      Alert.alert("Couldn't save", e?.message || "Try again.");
      return false;
    } finally {
      setSaving(false);
    }
  }, [draft, original]);

  // ── Back-without-save 3-option dialog ───────────────────────────
  const confirmBack = useCallback(() => {
    if (!dirtyRef.current) {
      router.back();
      return;
    }
    Alert.alert(
      "Unsaved changes",
      "You have edits that haven't been saved. What would you like to do?",
      [
        {
          text: "Save Changes",
          onPress: async () => {
            const ok = await save();
            if (ok) router.back();
          },
        },
        {
          text: "Discard",
          style: "destructive",
          onPress: () => {
            dirtyRef.current = false;
            router.back();
          },
        },
        {
          text: "Continue Editing",
          style: "cancel",
        },
      ],
    );
  }, [router, save]);

  // Hardware back (Android) honors the same guard.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener("hardwareBackPress", () => {
        confirmBack();
        return true;
      });
      return () => sub.remove();
    }, [confirmBack]),
  );

  // ── Render ──────────────────────────────────────────────────────
  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Edit Order" }} />
        <ActivityIndicator size="large" color="#FF6B00" />
      </SafeAreaView>
    );
  }

  if (!original) return null;

  // Use draft override when the user has edited the field; fall back
  // to original for everything else. Empty string + undefined collapse
  // for stable controlled inputs.
  //
  // Address special-case (Phase-6 unified address model): the
  // displayed `address_line1` value is the MERGED full address
  // (line1 + line2 / unified `address`), so the user sees the
  // complete delivery address in a single multiline field —
  // matching the New Shipment form in `(tabs)/add.tsx`.
  const valueOf = (k: keyof PendingOrder): string => {
    if (k === "address_line1") {
      // If the user already started editing, respect their draft —
      // otherwise compute the merged initial value.
      const drafted = draft.address_line1;
      if (drafted !== undefined) return String(drafted);
      return unifiedAddressFrom(original);
    }
    const v = draft[k] !== undefined ? draft[k] : (original as any)[k];
    if (v === undefined || v === null) return "";
    return String(v);
  };

  const setValue = (k: keyof PendingOrder, v: string) => {
    dirtyRef.current = true;
    if (k === "address_line1") {
      // Cap to 300 chars to match `add.tsx`. Also explicitly clear
      // `address_line2` so the legacy split shape never sneaks
      // back into the saved record.
      const next = v.length > 300 ? v.slice(0, 300) : v;
      setDraft((d) => ({ ...d, address_line1: next as any, address_line2: "" as any }));
      return;
    }
    setDraft((d) => ({ ...d, [k]: v as any }));
  };

  return (
    <SafeAreaView style={styles.root}>
      <Stack.Screen
        options={{
          title: "Edit Order",
          headerLeft: () => (
            <TouchableOpacity onPress={confirmBack} hitSlop={8} style={{ paddingHorizontal: 8 }}>
              <PhIcon name="chevron-back" size={22} color="#0F172A" />
            </TouchableOpacity>
          ),
        }}
      />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
      >
        <ScrollView
          contentContainerStyle={{ padding: 16, paddingBottom: 140 }}
          keyboardShouldPersistTaps="handled"
        >
          {/* Source pill */}
          <View style={styles.sourceCard}>
            <PhIcon name="information-circle-outline" size={16} color="#3B82F6" />
            <Text style={styles.sourceTxt}>
              Source: <Text style={{ fontWeight: "800" }}>{(original as any).source?.toUpperCase() || "—"}</Text>
              {"  ·  "}Master ID: {original.master_order_id || "—"}
            </Text>
          </View>

          {FIELD_GROUPS.map((g) => (
            <View key={g.title} style={styles.groupCard}>
              <Text style={styles.groupTitle}>{g.title}</Text>
              {g.fields.map((f) => {
                // Address field — apply the same UX as `(tabs)/add.tsx`:
                //   • 300-char hard cap, multiline 3 lines tall.
                //   • Friendly placeholder.
                //   • Live "x / 300" counter (red when near the limit).
                const isAddress = f.key === "address_line1";
                const v = valueOf(f.key);
                return (
                  <View key={String(f.key)} style={styles.fieldRow}>
                    <Text style={styles.fieldLabel}>{f.label}</Text>
                    <TextInput
                      value={v}
                      onChangeText={(t) => setValue(f.key, t)}
                      placeholder={
                        f.placeholder ||
                        (isAddress
                          ? "Full address (landmark, area, street)"
                          : `Enter ${f.label.toLowerCase()}`)
                      }
                      placeholderTextColor="#94A3B8"
                      keyboardType={f.keyboardType || "default"}
                      multiline={!!f.multiline}
                      numberOfLines={isAddress ? 3 : (f.multiline ? 2 : 1)}
                      maxLength={isAddress ? 300 : undefined}
                      style={[
                        styles.fieldInput,
                        f.multiline && { minHeight: 56, textAlignVertical: "top" },
                        isAddress && { minHeight: 76, paddingTop: 10 },
                      ]}
                    />
                    {isAddress ? (
                      <Text style={{
                        fontSize: 11,
                        color: v.length > 280 ? "#DC2626" : "#94A3B8",
                        marginTop: 4,
                        textAlign: "right",
                        fontWeight: v.length > 280 ? "700" : "500",
                      }}>
                        {v.length} / 300
                      </Text>
                    ) : null}
                  </View>
                );
              })}
            </View>
          ))}

          {/* Phase F3.7 — Admin Card (4th tab/section). Shown only
              when the order arrived via webhook AND we stashed the
              original payload (new ingests do). Surfaces:
                • Checkout / Order-Status URL (Dukaan ships
                  `order.order_status_url`; Shopify ships
                  `order.order_status_url` too).
                • Payment gateway / Payment link
                  (Dukaan: `order.gateway` / `order.payment_url`;
                   Shopify: `order.financial_status`).
                • The full flattened payload (key:value list) so the
                  operator can copy any extra field manually.
              We keep this read-only — edits go in the form above. */}
          {(original as any).source === "webhook" &&
           original.source_meta?.raw_payload ? (
            (() => {
              const sm    = original.source_meta || {};
              const raw   = sm.raw_payload || {};
              // Most carts wrap the actual order under .order; fall back
              // to top-level so older payload shapes still work.
              const order: any = (raw as any).order || raw;

              // Phase F3.7 fix — some carts deliver structured fields
              // (e.g. Dukaan ships
              //   order_status_url: {order_status_url: "https://..."}
              // not a plain string). Recursive walker safely extracts
              // a string from common URL-shaped keys so
              // <Text>{anything}</Text> never crashes React.
              const URL_KEYS = [
                "url", "order_status_url", "checkout_url",
                "payment_url", "recovery_url", "short_url",
                "link", "href", "src", "value",
              ];
              const _coerceStr = (v: any, depth = 0): string => {
                if (v === null || v === undefined) return "";
                if (typeof v === "string") return v;
                if (typeof v === "number" || typeof v === "boolean") return String(v);
                if (typeof v === "object" && depth < 3) {
                  for (const k of URL_KEYS) {
                    const sub = (v as any)[k];
                    if (typeof sub === "string" && sub) return sub;
                    if (sub && typeof sub === "object") {
                      const deeper = _coerceStr(sub, depth + 1);
                      if (deeper) return deeper;
                    }
                  }
                  return "";
                }
                return "";
              };

              const checkoutUrl: string =
                _coerceStr(order?.order_status_url) ||
                _coerceStr(order?.checkout_url) ||
                _coerceStr(order?.recovery_url);
              const paymentUrl: string =
                _coerceStr(order?.payment_url) ||
                _coerceStr(order?.gateway_url);
              const gateway: string =
                _coerceStr(order?.gateway) ||
                (Array.isArray(order?.payment_gateway_names)
                  ? String(order.payment_gateway_names[0] || "")
                  : "") ||
                _coerceStr(order?.financial_status);

              // Flatten one level deep so we can list common fields
              // without dumping huge nested address trees.
              const summaryRows: { k: string; v: string }[] = [];
              const pushIf = (k: string, v: any) => {
                if (v === null || v === undefined || v === "") return;
                const s =
                  typeof v === "object" ? JSON.stringify(v) : String(v);
                if (!s) return;
                summaryRows.push({ k, v: s });
              };
              pushIf("Order #",        order?.name || order?.order_number || order?.id);
              pushIf("Email",          order?.email || order?.contact_email);
              pushIf("Phone",          order?.phone || order?.contact_phone);
              pushIf("Total",          order?.total_price || order?.total);
              pushIf("Subtotal",       order?.subtotal_price);
              pushIf("Currency",       order?.currency);
              pushIf("Financial Status", order?.financial_status);
              pushIf("Fulfilment Status", order?.fulfillment_status);
              pushIf("Created (source)", order?.created_at);

              const openUrl = (url: string) => {
                if (!url) return;
                Linking.openURL(url).catch(() =>
                  Alert.alert("Open link", "Couldn't open the link in a browser."),
                );
              };

              return (
                <View style={[styles.groupCard, styles.adminCard]}>
                  <View style={styles.adminHeader}>
                    <PhIcon name="shield-checkmark-outline" size={16} color="#9333EA" />
                    <Text style={styles.adminTitle}>Admin Card</Text>
                    {!!sm.source_app && (
                      <View style={styles.adminSrcPill}>
                        <Text style={styles.adminSrcPillTxt}>
                          {sm.source_app.toUpperCase()}
                        </Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.adminHint}>
                    Original webhook payload from{" "}
                    <Text style={{ fontWeight: "800" }}>
                      {sm.webhook_name || "your webhook"}
                    </Text>
                    {sm.event_type ? ` · ${sm.event_type}` : ""}.
                  </Text>

                  {/* CTA buttons */}
                  {!!checkoutUrl && (
                    <TouchableOpacity
                      testID="admin-card-checkout-link"
                      style={styles.adminLinkBtn}
                      onPress={() => openUrl(checkoutUrl)}
                      activeOpacity={0.85}
                    >
                      <PhIcon name="open-outline" size={14} color="#fff" />
                      <Text style={styles.adminLinkBtnTxt}>Open Checkout Link</Text>
                    </TouchableOpacity>
                  )}
                  {!!paymentUrl && (
                    <TouchableOpacity
                      testID="admin-card-payment-link"
                      style={[styles.adminLinkBtn, { backgroundColor: "#10B981" }]}
                      onPress={() => openUrl(paymentUrl)}
                      activeOpacity={0.85}
                    >
                      <PhIcon name="card-outline" size={14} color="#fff" />
                      <Text style={styles.adminLinkBtnTxt}>Open Payment Link</Text>
                    </TouchableOpacity>
                  )}

                  {/* Inline read-only field rows */}
                  {!!gateway && (
                    <View style={styles.adminKvRow}>
                      <Text style={styles.adminKvKey}>Gateway</Text>
                      <Text style={styles.adminKvVal} selectable>
                        {gateway}
                      </Text>
                    </View>
                  )}
                  {!!checkoutUrl && (
                    <View style={styles.adminKvRow}>
                      <Text style={styles.adminKvKey}>Checkout URL</Text>
                      <Text style={styles.adminKvVal} selectable numberOfLines={2}>
                        {checkoutUrl}
                      </Text>
                    </View>
                  )}
                  {!!paymentUrl && (
                    <View style={styles.adminKvRow}>
                      <Text style={styles.adminKvKey}>Payment URL</Text>
                      <Text style={styles.adminKvVal} selectable numberOfLines={2}>
                        {paymentUrl}
                      </Text>
                    </View>
                  )}
                  {summaryRows.map((r) => (
                    <View key={r.k} style={styles.adminKvRow}>
                      <Text style={styles.adminKvKey}>{r.k}</Text>
                      <Text style={styles.adminKvVal} selectable numberOfLines={2}>
                        {r.v}
                      </Text>
                    </View>
                  ))}

                  {!!sm.external_order_id && (
                    <View style={styles.adminKvRow}>
                      <Text style={styles.adminKvKey}>Source Order ID</Text>
                      <Text style={styles.adminKvVal} selectable>
                        {sm.external_order_id}
                      </Text>
                    </View>
                  )}
                </View>
              );
            })()
          ) : (original as any).source === "webhook" ? (
            // Phase F3.7.1 — Legacy webhook order with no raw_payload
            // captured. Shown for orders that were ingested before the
            // payload-storage hook was added, or whose parent webhook
            // has rotated out of the recent_samples ring buffer. We
            // surface a calm explanatory hint instead of nothing so
            // the user knows the Admin Card isn't broken.
            <View style={styles.adminEmptyHint} testID="admin-card-empty-hint">
              <Text style={styles.adminEmptyHintTxt}>
                This order was imported before payload storage was
                enabled. New webhook orders will show full details
                here.
              </Text>
            </View>
          ) : null}
        </ScrollView>

        {/* Save bar */}
        <View style={styles.saveBar}>
          <TouchableOpacity
            style={styles.cancelBtn}
            onPress={confirmBack}
            disabled={saving}
          >
            <Text style={styles.cancelBtnTxt}>Cancel</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.saveBtn, (saving || Object.keys(draft).length === 0) && { opacity: 0.5 }]}
            onPress={async () => {
              const ok = await save();
              if (ok) router.back();
            }}
            disabled={saving || Object.keys(draft).length === 0}
          >
            {saving ? <ActivityIndicator color="#fff" /> : (
              <>
                <PhIcon name="check-circle" size={16} color="#fff" />
                <Text style={styles.saveBtnTxt}>Save Changes</Text>
              </>
            )}
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root:   { flex: 1, backgroundColor: "#F8FAFC" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F8FAFC" },

  sourceCard: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#EFF6FF", padding: 10, borderRadius: 10,
    borderWidth: 1, borderColor: "#BFDBFE", marginBottom: 14,
  },
  sourceTxt: { flex: 1, fontSize: 12, color: "#1E40AF" },

  groupCard: {
    backgroundColor: "#FFFFFF", padding: 14, borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB", marginBottom: 12,
  },
  groupTitle: {
    fontSize: 12, fontWeight: "800", color: "#0F172A",
    textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 10,
  },

  fieldRow:    { marginBottom: 10 },
  fieldLabel:  { fontSize: 12, fontWeight: "600", color: "#475569", marginBottom: 4 },
  fieldInput:  {
    paddingHorizontal: 10, paddingVertical: 10, borderRadius: 8,
    borderWidth: 1, borderColor: "#E2E8F0", backgroundColor: "#F8FAFC",
    fontSize: 14, color: "#0F172A",
  },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    flexDirection: "row", gap: 10, padding: 14,
    backgroundColor: "#FFFFFF",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  cancelBtn: {
    flex: 1, paddingVertical: 12, borderRadius: 10, alignItems: "center", justifyContent: "center",
    borderWidth: 1, borderColor: "#E5E7EB", backgroundColor: "#F8FAFC",
  },
  cancelBtnTxt: { fontSize: 14, fontWeight: "700", color: "#475569" },
  saveBtn: {
    flex: 2, flexDirection: "row", gap: 6, alignItems: "center", justifyContent: "center",
    paddingVertical: 12, borderRadius: 10, backgroundColor: "#FF6B00",
  },
  saveBtnTxt: { color: "#FFFFFF", fontWeight: "700", fontSize: 14 },

  // Phase F3.7 — Admin Card (4th section) styles.
  adminCard: {
    borderColor: "#E9D5FF",
    backgroundColor: "#FAF5FF",
  },
  adminHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 6,
  },
  adminTitle: {
    fontSize: 12,
    fontWeight: "900",
    color: "#6B21A8",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  adminSrcPill: {
    paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: 20,
    flexShrink: 0,
    backgroundColor: "#E0E7FF",
  },
  adminSrcPillTxt: {
    fontSize: 9, fontWeight: "800", color: "#3730A3",
  },
  adminHint: {
    fontSize: 11, color: "#7C3AED", marginBottom: 10, lineHeight: 16,
  },
  adminLinkBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 9,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: "#3B82F6",
    marginBottom: 8,
  },
  adminLinkBtnTxt: {
    color: "#fff",
    fontSize: 12.5,
    fontWeight: "800",
  },
  adminKvRow: {
    flexDirection: "row",
    paddingVertical: 6,
    borderTopWidth: 1,
    borderTopColor: "#F3E8FF",
    gap: 10,
  },
  adminKvKey: {
    width: 110,
    fontSize: 11,
    fontWeight: "700",
    color: "#7C3AED",
  },
  adminKvVal: {
    flex: 1,
    fontSize: 12,
    color: "#0F172A",
    lineHeight: 17,
  },
  // Phase F3.7.1 — Calm grey hint shown when a webhook order has no
  // raw_payload (ingested before the payload-storage hook landed, or
  // aged out of the recent_samples ring buffer).
  adminEmptyHint: {
    marginHorizontal: 16,
    marginBottom: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#F1F5F9",
    borderWidth: 1,
    borderColor: "#E2E8F0",
  },
  adminEmptyHintTxt: {
    fontSize: 12,
    color: "#64748B",
    lineHeight: 17,
    textAlign: "center",
  },
});
