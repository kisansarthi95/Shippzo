import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TextInput,
  TouchableOpacity,
  Alert,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
  Modal,
  FlatList,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Api, Courier, SheetOrder } from "../../lib/api";
import { colors } from "../../lib/theme";

function splitAddress(full: string): {
  line1: string;
  line2: string;
  city: string;
  state: string;
  pincode: string;
} {
  const clean = (full || "").trim();
  if (!clean) return { line1: "", line2: "", city: "", state: "", pincode: "" };
  const pinMatch = clean.match(/(\d{6})/);
  const pincode = pinMatch ? pinMatch[1] : "";
  const parts = clean.split(/[,\n]/).map((p) => p.trim()).filter(Boolean);
  let line1 = parts[0] || "";
  let line2 = parts[1] || "";
  let city = "";
  let state = "";
  if (parts.length >= 3) city = parts[parts.length - 2] || "";
  if (parts.length >= 2) {
    const last = parts[parts.length - 1].replace(/\d{6}/, "").trim();
    state = last || state;
  }
  return { line1, line2, city, state, pincode };
}

export default function AddShipment() {
  const router = useRouter();
  const params = useLocalSearchParams<{ scanned?: string; fromSheet?: string }>();

  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [selectedCourier, setSelectedCourier] = useState<Courier | null>(null);
  const [autoTracking, setAutoTracking] = useState(true);
  const [nextPreview, setNextPreview] = useState<string>("");

  const [trackingId, setTrackingId] = useState("");
  const [orderId, setOrderId] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [addr1, setAddr1] = useState("");
  const [addr2, setAddr2] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [paymentMode, setPaymentMode] = useState<"COD" | "Prepaid">("Prepaid");
  const [amount, setAmount] = useState("");
  const [itemsText, setItemsText] = useState(""); // newline or comma separated
  const [weight, setWeight] = useState("");
  const [sheetRowKey, setSheetRowKey] = useState("");
  const [saving, setSaving] = useState(false);

  const [sheetConnected, setSheetConnected] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importLoading, setImportLoading] = useState(false);
  const [importOrders, setImportOrders] = useState<SheetOrder[]>([]);
  const [importFilter, setImportFilter] = useState<"pending" | "all">("pending");
  const [importSearch, setImportSearch] = useState("");

  useEffect(() => {
    (async () => {
      const [cs, settings] = await Promise.all([
        Api.listCouriers(),
        Api.getSettings(),
      ]);
      setCouriers(cs);
      if (cs.length > 0) setSelectedCourier(cs[0]);
      setSheetConnected(Boolean(settings.sheet?.sheet_id));
    })();
  }, []);

  useEffect(() => {
    if (!selectedCourier) return;
    Api.peekNextTracking(selectedCourier.id)
      .then((r) => setNextPreview(r.tracking_id))
      .catch(() => setNextPreview(""));
  }, [selectedCourier]);

  useEffect(() => {
    if (autoTracking && nextPreview && !params.scanned) {
      setTrackingId(nextPreview);
    }
  }, [autoTracking, nextPreview, params.scanned]);

  useEffect(() => {
    if (params.scanned) {
      setAutoTracking(false);
      setTrackingId(String(params.scanned));
    }
  }, [params.scanned]);

  const openImport = useCallback(async () => {
    if (!sheetConnected) {
      Alert.alert(
        "Google Sheet not connected",
        "Go to Settings → Google Sheet and paste your sheet link first."
      );
      return;
    }
    setShowImport(true);
    setImportLoading(true);
    try {
      const res = await Api.sheetsOrders();
      setImportOrders(res.orders);
      if (res.headers_changed) {
        Alert.alert(
          "Sheet columns changed",
          "Your sheet's column structure has changed. Open Settings → Google Sheet to re-map columns."
        );
      }
    } catch (e: any) {
      Alert.alert("Import error", e?.response?.data?.detail || e?.message || "Failed");
      setShowImport(false);
    } finally {
      setImportLoading(false);
    }
  }, [sheetConnected]);

  const pickOrder = (o: SheetOrder) => {
    const addr = splitAddress(o.address);
    setOrderId(o.order_id);
    setCustomerName(o.customer_name);
    setCustomerPhone(o.phone);
    setAddr1(addr.line1);
    setAddr2(addr.line2);
    setCity(o.city || addr.city);
    setState(o.state || addr.state);
    setPincode(o.pincode || addr.pincode);
    const amt = (o.amount || "").replace(/[^\d.]/g, "");
    setAmount(amt);
    const items = (o.item || "")
      .split(/[,\n;|]/)
      .map((s) => s.trim())
      .filter(Boolean);
    setItemsText(items.join("\n"));
    setSheetRowKey(o.row_key);
    setShowImport(false);
  };

  const resetForm = () => {
    setOrderId("");
    setCustomerName("");
    setCustomerPhone("");
    setAddr1("");
    setAddr2("");
    setCity("");
    setState("");
    setPincode("");
    setPaymentMode("Prepaid");
    setAmount("");
    setItemsText("");
    setWeight("");
    setSheetRowKey("");
  };

  const save = useCallback(
    async (thenPrint: boolean) => {
      if (!customerName.trim()) {
        Alert.alert("Validation", "Customer name is required");
        return;
      }
      if (!trackingId.trim()) {
        Alert.alert("Validation", "Tracking ID is required");
        return;
      }
      setSaving(true);
      try {
        let finalTracking = trackingId.trim();
        if (autoTracking && selectedCourier) {
          const r = await Api.consumeTracking(selectedCourier.id);
          finalTracking = r.tracking_id;
        }
        const items = itemsText
          .split(/\n|,|;/)
          .map((s) => s.trim())
          .filter(Boolean);
        const created = await Api.createShipment({
          tracking_id: finalTracking,
          courier_id: selectedCourier?.id,
          courier_name: selectedCourier?.name,
          order_id: orderId.trim(),
          customer_name: customerName.trim(),
          customer_phone: customerPhone.trim(),
          address_line1: addr1.trim(),
          address_line2: addr2.trim(),
          city: city.trim(),
          state: state.trim(),
          pincode: pincode.trim(),
          payment_mode: paymentMode,
          amount: Number(amount) || 0,
          items,
          item_description: items.join(", "),
          weight: weight.trim(),
          sheet_row_key: sheetRowKey,
        });
        resetForm();
        if (thenPrint) {
          router.replace(`/label/${created.id}`);
        } else {
          Alert.alert("Saved", `Shipment ${created.tracking_id} saved.`, [
            { text: "OK", onPress: () => router.replace("/(tabs)/shipments") },
          ]);
        }
      } catch (e: any) {
        Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed to save");
      } finally {
        setSaving(false);
      }
    },
    [
      autoTracking,
      selectedCourier,
      trackingId,
      orderId,
      customerName,
      customerPhone,
      addr1,
      addr2,
      city,
      state,
      pincode,
      paymentMode,
      amount,
      itemsText,
      weight,
      sheetRowKey,
      router,
    ]
  );

  const filteredImport = importOrders.filter((o) => {
    if (importFilter === "pending" && o.already_shipped) return false;
    const q = importSearch.trim().toLowerCase();
    if (!q) return true;
    return (
      o.order_id.toLowerCase().includes(q) ||
      o.customer_name.toLowerCase().includes(q) ||
      o.phone.toLowerCase().includes(q) ||
      o.city.toLowerCase().includes(q)
    );
  });

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>New Shipment</Text>
        <TouchableOpacity
          testID="scan-tracking-btn"
          onPress={() => router.push("/scanner?returnTo=add")}
          style={styles.scanPill}
        >
          <Ionicons name="scan" size={16} color={colors.primary} />
          <Text style={styles.scanPillText}>Scan</Text>
        </TouchableOpacity>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          testID="add-scroll"
          contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
          keyboardShouldPersistTaps="handled"
        >
          {/* Import from Sheet */}
          <TouchableOpacity
            testID="import-from-sheet-btn"
            onPress={openImport}
            style={[
              styles.importBtn,
              !sheetConnected && { opacity: 0.55 },
            ]}
          >
            <Ionicons name="cloud-download" size={20} color="#fff" />
            <View style={{ flex: 1 }}>
              <Text style={styles.importBtnTitle}>
                {sheetConnected ? "Import from Google Sheet" : "Connect Google Sheet in Settings"}
              </Text>
              <Text style={styles.importBtnSub}>
                {sheetConnected
                  ? "Auto-fill customer, order, amount from your form/sheet"
                  : "Settings → Google Sheet → paste link"}
              </Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color="#fff" />
          </TouchableOpacity>

          {/* Courier */}
          <Section title="Courier Partner">
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={{ gap: 8, paddingRight: 16 }}
            >
              {couriers.map((c) => {
                const active = selectedCourier?.id === c.id;
                return (
                  <TouchableOpacity
                    key={c.id}
                    testID={`courier-pill-${c.name}`}
                    style={[styles.pill, active && styles.pillActive]}
                    onPress={() => setSelectedCourier(c)}
                  >
                    <Text style={[styles.pillText, active && { color: "#fff" }]}>
                      {c.name}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </Section>

          {/* Tracking */}
          <Section title="Tracking ID">
            <View style={styles.toggleRow}>
              <TouchableOpacity
                testID="auto-tracking-toggle"
                style={[styles.toggleBtn, autoTracking && styles.toggleBtnActive]}
                onPress={() => {
                  setAutoTracking(true);
                  if (nextPreview) setTrackingId(nextPreview);
                }}
              >
                <Ionicons
                  name="repeat"
                  size={14}
                  color={autoTracking ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, autoTracking && { color: "#fff" }]}>
                  Auto Series
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="manual-tracking-toggle"
                style={[styles.toggleBtn, !autoTracking && styles.toggleBtnActive]}
                onPress={() => setAutoTracking(false)}
              >
                <Ionicons
                  name="create-outline"
                  size={14}
                  color={!autoTracking ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, !autoTracking && { color: "#fff" }]}>
                  Manual / Scan
                </Text>
              </TouchableOpacity>
            </View>
            <TextInput
              testID="tracking-id-input"
              value={trackingId}
              editable={!autoTracking}
              onChangeText={setTrackingId}
              placeholder={autoTracking ? nextPreview : "Enter tracking ID"}
              placeholderTextColor="#9CA3AF"
              style={[styles.input, styles.trackingInput]}
              autoCapitalize="characters"
            />
            {autoTracking && nextPreview ? (
              <Text style={styles.hint}>Next auto: {nextPreview}</Text>
            ) : null}
          </Section>

          {/* Order */}
          <Section title="Order Details">
            <Field label="Order ID">
              <TextInput
                testID="order-id-input"
                value={orderId}
                onChangeText={setOrderId}
                placeholder="Order ID / Invoice #"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Items / Products">
              <TextInput
                testID="items-input"
                value={itemsText}
                onChangeText={setItemsText}
                placeholder="One item per line (or comma separated)"
                placeholderTextColor="#9CA3AF"
                multiline
                style={[styles.input, { height: 80, textAlignVertical: "top", paddingTop: 10 }]}
              />
            </Field>
          </Section>

          {/* Customer */}
          <Section title="Customer">
            <Field label="Name *">
              <TextInput
                testID="customer-name-input"
                value={customerName}
                onChangeText={setCustomerName}
                placeholder="Full name"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Phone">
              <TextInput
                testID="customer-phone-input"
                value={customerPhone}
                onChangeText={setCustomerPhone}
                placeholder="10-digit mobile"
                placeholderTextColor="#9CA3AF"
                keyboardType="phone-pad"
                style={styles.input}
              />
            </Field>
          </Section>

          {/* Address */}
          <Section title="Delivery Address">
            <Field label="Address Line 1">
              <TextInput
                testID="addr1-input"
                value={addr1}
                onChangeText={setAddr1}
                placeholder="House / Street"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Address Line 2">
              <TextInput
                testID="addr2-input"
                value={addr2}
                onChangeText={setAddr2}
                placeholder="Area / Landmark"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <View style={styles.grid2}>
              <View style={{ flex: 1 }}>
                <Field label="City">
                  <TextInput
                    testID="city-input"
                    value={city}
                    onChangeText={setCity}
                    placeholder="City"
                    placeholderTextColor="#9CA3AF"
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ width: 12 }} />
              <View style={{ flex: 1 }}>
                <Field label="Pincode">
                  <TextInput
                    testID="pincode-input"
                    value={pincode}
                    onChangeText={setPincode}
                    placeholder="6-digit"
                    placeholderTextColor="#9CA3AF"
                    keyboardType="number-pad"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            <Field label="State">
              <TextInput
                testID="state-input"
                value={state}
                onChangeText={setState}
                placeholder="State"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
          </Section>

          {/* Payment & Parcel */}
          <Section title="Payment & Parcel">
            <View style={styles.toggleRow}>
              <TouchableOpacity
                testID="prepaid-toggle"
                style={[
                  styles.toggleBtn,
                  paymentMode === "Prepaid" && styles.toggleBtnActive,
                ]}
                onPress={() => setPaymentMode("Prepaid")}
              >
                <Ionicons
                  name="card"
                  size={14}
                  color={paymentMode === "Prepaid" ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, paymentMode === "Prepaid" && { color: "#fff" }]}>
                  Prepaid
                </Text>
              </TouchableOpacity>
              <TouchableOpacity
                testID="cod-toggle"
                style={[
                  styles.toggleBtn,
                  paymentMode === "COD" && styles.toggleBtnActive,
                ]}
                onPress={() => setPaymentMode("COD")}
              >
                <Ionicons
                  name="cash"
                  size={14}
                  color={paymentMode === "COD" ? "#fff" : colors.text}
                />
                <Text style={[styles.toggleText, paymentMode === "COD" && { color: "#fff" }]}>
                  COD
                </Text>
              </TouchableOpacity>
            </View>
            <Field label={paymentMode === "COD" ? "COD Amount (₹)" : "Order Amount (₹)"}>
              <TextInput
                testID="amount-input"
                value={amount}
                onChangeText={setAmount}
                placeholder={paymentMode === "COD" ? "Amount to collect" : "Order value"}
                placeholderTextColor="#9CA3AF"
                keyboardType="decimal-pad"
                style={styles.input}
              />
            </Field>
            <Field label="Weight">
              <TextInput
                testID="weight-input"
                value={weight}
                onChangeText={setWeight}
                placeholder="e.g. 0.5 kg"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
          </Section>

          <View style={styles.ctaRow}>
            <TouchableOpacity
              testID="save-shipment-btn"
              style={styles.secondaryBtn}
              disabled={saving}
              onPress={() => save(false)}
            >
              {saving ? (
                <ActivityIndicator color={colors.text} />
              ) : (
                <>
                  <Ionicons name="save-outline" size={18} color={colors.text} />
                  <Text style={styles.secondaryBtnText}>Save</Text>
                </>
              )}
            </TouchableOpacity>
            <TouchableOpacity
              testID="save-print-btn"
              style={styles.primaryBtn}
              disabled={saving}
              onPress={() => save(true)}
            >
              <Ionicons name="print" size={18} color="#fff" />
              <Text style={styles.primaryBtnText}>Save & Print</Text>
            </TouchableOpacity>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      {/* Import Modal */}
      <Modal visible={showImport} animationType="slide" onRequestClose={() => setShowImport(false)}>
        <SafeAreaView style={styles.modalSafe}>
          <View style={styles.modalHeader}>
            <TouchableOpacity
              testID="import-close"
              onPress={() => setShowImport(false)}
              style={styles.modalClose}
            >
              <Ionicons name="close" size={22} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.modalTitle}>Import from Sheet</Text>
            <TouchableOpacity
              testID="import-refresh"
              onPress={openImport}
              style={styles.modalClose}
            >
              <Ionicons name="refresh" size={20} color={colors.text} />
            </TouchableOpacity>
          </View>

          <View style={styles.modalSearchWrap}>
            <Ionicons name="search" size={16} color={colors.textMuted} />
            <TextInput
              testID="import-search"
              placeholder="Search order, name, phone"
              placeholderTextColor="#9CA3AF"
              value={importSearch}
              onChangeText={setImportSearch}
              style={styles.modalSearch}
            />
          </View>
          <View style={styles.filterRow}>
            <TouchableOpacity
              testID="import-filter-pending"
              onPress={() => setImportFilter("pending")}
              style={[
                styles.filterPill,
                importFilter === "pending" && styles.filterPillActive,
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  importFilter === "pending" && { color: "#fff" },
                ]}
              >
                Not yet shipped
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="import-filter-all"
              onPress={() => setImportFilter("all")}
              style={[
                styles.filterPill,
                importFilter === "all" && styles.filterPillActive,
              ]}
            >
              <Text
                style={[
                  styles.filterText,
                  importFilter === "all" && { color: "#fff" },
                ]}
              >
                All {importOrders.length ? `(${importOrders.length})` : ""}
              </Text>
            </TouchableOpacity>
          </View>

          {importLoading ? (
            <ActivityIndicator color={colors.primary} style={{ marginTop: 24 }} />
          ) : (
            <FlatList
              data={filteredImport}
              keyExtractor={(o) => o.row_key || String(o.row_index)}
              contentContainerStyle={{ padding: 12, paddingBottom: 32 }}
              ListEmptyComponent={
                <Text style={styles.emptyImport}>
                  {importOrders.length === 0
                    ? "No rows found in your sheet."
                    : "No matching orders."}
                </Text>
              }
              renderItem={({ item }) => (
                <TouchableOpacity
                  testID={`import-row-${item.row_index}`}
                  onPress={() => pickOrder(item)}
                  style={[
                    styles.orderCard,
                    item.already_shipped && { opacity: 0.55 },
                  ]}
                >
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                      <Text style={styles.orderCustomer}>
                        {item.customer_name || "(no name)"}
                      </Text>
                      {item.already_shipped && (
                        <View style={styles.shippedChip}>
                          <Text style={styles.shippedChipText}>SHIPPED</Text>
                        </View>
                      )}
                    </View>
                    <Text style={styles.orderLine}>
                      {item.order_id ? `Order #${item.order_id} · ` : ""}
                      {item.phone || "no phone"}
                    </Text>
                    <Text style={styles.orderLine} numberOfLines={1}>
                      {[item.city, item.state, item.pincode].filter(Boolean).join(", ")}
                    </Text>
                    <Text style={styles.orderItem} numberOfLines={2}>
                      📦 {item.item || "—"} · ₹{item.amount || "0"}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={20} color={colors.textMuted} />
                </TouchableOpacity>
              )}
            />
          )}
        </SafeAreaView>
      </Modal>
    </SafeAreaView>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 10,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  scanPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 2,
    borderColor: colors.primary,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 999,
  },
  scanPillText: { color: colors.primary, fontWeight: "700" },
  importBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.secondary,
    borderRadius: 12,
    padding: 14,
    marginBottom: 14,
  },
  importBtnTitle: { color: "#fff", fontWeight: "800", fontSize: 14 },
  importBtnSub: { color: "rgba(255,255,255,0.7)", fontSize: 11, marginTop: 2 },
  section: {
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 14,
    marginBottom: 14,
  },
  sectionTitle: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
    marginBottom: 10,
  },
  fieldLabel: {
    fontSize: 12,
    color: colors.textMuted,
    fontWeight: "700",
    marginBottom: 6,
  },
  input: {
    minHeight: 46,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 14,
    fontSize: 15,
    color: colors.text,
    backgroundColor: colors.surface,
  },
  trackingInput: {
    fontFamily: "Courier",
    fontWeight: "800",
    letterSpacing: 2,
    fontSize: 17,
  },
  pill: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  pillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  pillText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  toggleRow: { flexDirection: "row", gap: 8, marginBottom: 10 },
  toggleBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
  },
  toggleBtnActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  toggleText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  hint: { fontSize: 12, color: colors.textMuted, marginTop: 6 },
  grid2: { flexDirection: "row" },
  ctaRow: { flexDirection: "row", gap: 10, marginTop: 6 },
  secondaryBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.secondary,
    borderRadius: 12,
  },
  secondaryBtnText: { fontWeight: "800", color: colors.text },
  primaryBtn: {
    flex: 1.3,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: colors.primary,
    borderRadius: 12,
  },
  primaryBtnText: { fontWeight: "800", color: "#fff" },

  modalSafe: { flex: 1, backgroundColor: colors.background },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  modalClose: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: "#F3F4F6",
    justifyContent: "center",
    alignItems: "center",
  },
  modalTitle: { fontSize: 17, fontWeight: "800", color: colors.text },
  modalSearchWrap: {
    marginHorizontal: 12,
    marginTop: 10,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    height: 44,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    backgroundColor: "#fff",
  },
  modalSearch: { flex: 1, color: colors.text, fontSize: 14 },
  filterRow: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  filterPill: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: { fontWeight: "700", fontSize: 12, color: colors.text },
  orderCard: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  orderCustomer: { fontSize: 15, fontWeight: "800", color: colors.text },
  orderLine: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  orderItem: { fontSize: 12, color: colors.text, marginTop: 4, fontWeight: "600" },
  shippedChip: {
    backgroundColor: colors.successBg,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
  },
  shippedChipText: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.successText,
    letterSpacing: 0.5,
  },
  emptyImport: {
    textAlign: "center",
    color: colors.textMuted,
    marginTop: 30,
  },
});
