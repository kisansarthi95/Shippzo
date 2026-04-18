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
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Api, Courier } from "../../lib/api";
import { colors } from "../../lib/theme";

export default function AddShipment() {
  const router = useRouter();
  const params = useLocalSearchParams<{ scanned?: string }>();

  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [selectedCourier, setSelectedCourier] = useState<Courier | null>(null);
  const [autoTracking, setAutoTracking] = useState(true);
  const [nextPreview, setNextPreview] = useState<string>("");

  const [trackingId, setTrackingId] = useState("");
  const [customerName, setCustomerName] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [addr1, setAddr1] = useState("");
  const [addr2, setAddr2] = useState("");
  const [city, setCity] = useState("");
  const [state, setState] = useState("");
  const [pincode, setPincode] = useState("");
  const [paymentMode, setPaymentMode] = useState<"COD" | "Prepaid">("Prepaid");
  const [codAmount, setCodAmount] = useState("");
  const [weight, setWeight] = useState("");
  const [itemDesc, setItemDesc] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      const cs = await Api.listCouriers();
      setCouriers(cs);
      if (cs.length > 0) setSelectedCourier(cs[0]);
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

  const resetForm = () => {
    setCustomerName("");
    setCustomerPhone("");
    setAddr1("");
    setAddr2("");
    setCity("");
    setState("");
    setPincode("");
    setPaymentMode("Prepaid");
    setCodAmount("");
    setWeight("");
    setItemDesc("");
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
        const created = await Api.createShipment({
          tracking_id: finalTracking,
          courier_id: selectedCourier?.id,
          courier_name: selectedCourier?.name,
          customer_name: customerName.trim(),
          customer_phone: customerPhone.trim(),
          address_line1: addr1.trim(),
          address_line2: addr2.trim(),
          city: city.trim(),
          state: state.trim(),
          pincode: pincode.trim(),
          payment_mode: paymentMode,
          cod_amount: paymentMode === "COD" ? Number(codAmount) || 0 : 0,
          weight: weight.trim(),
          item_description: itemDesc.trim(),
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
        Alert.alert("Error", e?.message || "Failed to save");
      } finally {
        setSaving(false);
      }
    },
    [
      autoTracking,
      selectedCourier,
      trackingId,
      customerName,
      customerPhone,
      addr1,
      addr2,
      city,
      state,
      pincode,
      paymentMode,
      codAmount,
      weight,
      itemDesc,
      router,
    ]
  );

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
                <Text
                  style={[
                    styles.toggleText,
                    autoTracking && { color: "#fff" },
                  ]}
                >
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
                <Text
                  style={[
                    styles.toggleText,
                    !autoTracking && { color: "#fff" },
                  ]}
                >
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
              <Text style={styles.hint}>
                Next auto-tracking: {nextPreview}
              </Text>
            ) : null}
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

          {/* Payment */}
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
                <Text
                  style={[
                    styles.toggleText,
                    paymentMode === "Prepaid" && { color: "#fff" },
                  ]}
                >
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
                <Text
                  style={[
                    styles.toggleText,
                    paymentMode === "COD" && { color: "#fff" },
                  ]}
                >
                  COD
                </Text>
              </TouchableOpacity>
            </View>
            {paymentMode === "COD" && (
              <Field label="COD Amount (₹)">
                <TextInput
                  testID="cod-amount-input"
                  value={codAmount}
                  onChangeText={setCodAmount}
                  placeholder="Amount to collect"
                  placeholderTextColor="#9CA3AF"
                  keyboardType="number-pad"
                  style={styles.input}
                />
              </Field>
            )}
            <View style={styles.grid2}>
              <View style={{ flex: 1 }}>
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
              </View>
              <View style={{ width: 12 }} />
              <View style={{ flex: 1 }}>
                <Field label="Item">
                  <TextInput
                    testID="item-input"
                    value={itemDesc}
                    onChangeText={setItemDesc}
                    placeholder="Contents"
                    placeholderTextColor="#9CA3AF"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
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
    height: 46,
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
  pillActive: {
    backgroundColor: colors.secondary,
    borderColor: colors.secondary,
  },
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
  toggleBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
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
});
