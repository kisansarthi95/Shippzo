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
  ActivityIndicator, Alert, BackHandler, KeyboardAvoidingView, Platform,
  ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View,
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
      { key: "address_line1", label: "Address",       multiline: true },
      { key: "address_line2", label: "Address Line 2", multiline: true },
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
      { key: "amount",       label: "Amount (₹)",       keyboardType: "numeric" },
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
  const valueOf = (k: keyof PendingOrder): string => {
    const v = draft[k] !== undefined ? draft[k] : (original as any)[k];
    if (v === undefined || v === null) return "";
    return String(v);
  };

  const setValue = (k: keyof PendingOrder, v: string) => {
    dirtyRef.current = true;
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
              {g.fields.map((f) => (
                <View key={String(f.key)} style={styles.fieldRow}>
                  <Text style={styles.fieldLabel}>{f.label}</Text>
                  <TextInput
                    value={valueOf(f.key)}
                    onChangeText={(t) => setValue(f.key, t)}
                    placeholder={f.placeholder || `Enter ${f.label.toLowerCase()}`}
                    placeholderTextColor="#94A3B8"
                    keyboardType={f.keyboardType || "default"}
                    multiline={!!f.multiline}
                    numberOfLines={f.multiline ? 2 : 1}
                    style={[styles.fieldInput, f.multiline && { minHeight: 56, textAlignVertical: "top" }]}
                  />
                </View>
              ))}
            </View>
          ))}
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
});
