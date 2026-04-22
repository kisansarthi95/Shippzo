import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  TouchableOpacity,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Switch,
  ActivityIndicator,
  Modal,
  Linking,
  Image,
} from "react-native";
import * as ImagePicker from "expo-image-picker";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { Api, Courier, Settings as SettingsT, SenderAddress, SheetPreview, SHEET_FIELDS } from "../../lib/api";
import { colors } from "../../lib/theme";

// Professional templates with blank lines for breathing room
const PRESETS = {
  wa_gujarati: (
    "નમસ્તે {customer_name} 🙏\n" +
    "\n" +
    "તમારો ઓર્ડર #{order_id} સફળતાપૂર્વક મોકલવામાં આવ્યો છે.\n" +
    "\n" +
    "📦 Courier: {courier}\n" +
    "🔖 Tracking ID: {tracking_id}\n" +
    "\n" +
    "🔗 Track your order:\n" +
    "{tracking_url}\n" +
    "\n" +
    "⏱ અપેક્ષિત ડિલિવરી: {eta_days} દિવસ\n" +
    "\n" +
    "આભાર!"
  ),
  wa_english: (
    "Hi {customer_name} 👋\n" +
    "\n" +
    "Your order #{order_id} has been shipped.\n" +
    "\n" +
    "📦 Courier: {courier}\n" +
    "🔖 Tracking ID: {tracking_id}\n" +
    "\n" +
    "🔗 Track here:\n" +
    "{tracking_url}\n" +
    "\n" +
    "⏱ Expected delivery: {eta_days} days\n" +
    "\n" +
    "Thank you for your order!"
  ),
  copy_pro: (
    "Hi {customer_name},\n" +
    "\n" +
    "Your order #{order_id} has been shipped.\n" +
    "\n" +
    "Courier: {courier}\n" +
    "Tracking ID: {tracking_id}\n" +
    "Amount: ₹{amount}\n" +
    "\n" +
    "Track your order:\n" +
    "{tracking_url}\n" +
    "\n" +
    "Thank you!"
  ),
};

// Sample data for live preview
const SAMPLE = {
  customer_name: "Ramesh Patel",
  order_id: "ORD-1001",
  courier: "Nandan Courier",
  tracking_id: "ND00123",
  tracking_url: "https://nandancourier.com/track?id=ND00123",
  amount: "850",
  eta_days: "7",
};

function fillTemplate(tpl: string, brand?: string): string {
  let out = tpl;
  Object.entries(SAMPLE).forEach(([k, v]) => {
    out = out.replace(new RegExp(`\\{${k}\\}`, "g"), v);
  });
  if (brand) out = `${out}\n\n— ${brand}`;
  return out;
}

export default function SettingsScreen() {
  const router = useRouter();

  const [sender, setSender] = useState<SenderAddress>({
    name: "", phone: "", address_line1: "", address_line2: "",
    city: "", state: "", pincode: "", show_contact: true,
  });
  const [template, setTemplate] = useState("");
  const [copyTemplate, setCopyTemplate] = useState("");
  const [showPreview, setShowPreview] = useState(true);
  const [etaDays, setEtaDays] = useState("7");
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [brandName, setBrandName] = useState("");
  const [brandLogo, setBrandLogo] = useState("");
  const [preferLogo, setPreferLogo] = useState(true);
  const [logoShape, setLogoShape] = useState<"square" | "wide">("square");
  const [shipmentTagline, setShipmentTagline] = useState("");
  const [labelFields, setLabelFields] = useState({
    oid: true, dispatch_date: true, weight: true, item: true, phone: true,
    customer_id: true, token_info: false, box_dimensions: false, shipment_notes: false,
  });

  // Sheet
  const [sheetUrl, setSheetUrl] = useState("");
  const [sheetStatus, setSheetStatus] = useState<"idle" | "loading" | "connected">("idle");
  const [preview, setPreview] = useState<SheetPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [pickerForField, setPickerForField] = useState<string | null>(null);
  const [connectedSheetId, setConnectedSheetId] = useState("");
  const [connectedHeaders, setConnectedHeaders] = useState<string[]>([]);

  const load = useCallback(async () => {
    const [s, cs] = await Promise.all([Api.getSettings(), Api.listCouriers()]);
    setSender(s.sender);
    setTemplate(s.whatsapp_template);
    setCopyTemplate(s.copy_template);
    setEtaDays(String(s.default_eta_days));
    setCouriers(cs);
    setBrandName(s.brand?.name || "");
    setBrandLogo(s.brand?.logo_base64 || "");
    setPreferLogo((s as any).prefer_logo !== false);
    setLogoShape(((s as any).logo_shape as any) === "wide" ? "wide" : "square");
    if ((s as any).label_fields) {
      setLabelFields((prev) => ({ ...prev, ...(s as any).label_fields }));
    }
    setShipmentTagline(String((s as any).shipment_tagline || ""));
    if (s.sheet?.sheet_id) {
      setSheetStatus("connected");
      setSheetUrl(s.sheet.url);
      setConnectedSheetId(s.sheet.sheet_id);
      setConnectedHeaders(s.sheet.headers || []);
      setMapping(s.sheet.column_mapping || {});
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const saveSender = async () => {
    try {
      await Api.updateSettings({
        sender,
        brand: { name: brandName, logo_base64: brandLogo },
        whatsapp_template: template,
        copy_template: copyTemplate,
        default_eta_days: Number(etaDays) || 7,
        prefer_logo: preferLogo,
        logo_shape: logoShape,
        shipment_tagline: shipmentTagline,
        label_fields: labelFields,
      } as Partial<SettingsT>);
      Alert.alert("Saved", "Settings saved successfully.");
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const fetchPreview = async () => {
    if (!sheetUrl.trim()) {
      Alert.alert("Paste link", "Please paste your Google Sheet URL");
      return;
    }
    setSheetStatus("loading");
    try {
      const p = await Api.sheetsPreview(sheetUrl.trim());
      setPreview(p);
      setMapping(p.auto_mapping || {});
      setSheetStatus("idle");
    } catch (e: any) {
      setSheetStatus("idle");
      Alert.alert("Error", e?.response?.data?.detail || "Failed to load sheet. Make sure it's shared with 'Anyone with the link'.");
    }
  };

  const saveSheet = async () => {
    if (!preview) return;
    try {
      await Api.updateSettings({
        sheet: {
          url: sheetUrl.trim(),
          sheet_id: preview.sheet_id,
          gid: preview.gid,
          tab_name: "",
          headers: preview.headers,
          column_mapping: mapping,
        },
      } as Partial<SettingsT>);
      setSheetStatus("connected");
      setConnectedSheetId(preview.sheet_id);
      setConnectedHeaders(preview.headers);
      Alert.alert("Connected", "Google Sheet connected. Column mapping saved.");
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const disconnectSheet = () => {
    Alert.alert("Disconnect Sheet?", "Stored column mapping will be cleared.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Disconnect",
        style: "destructive",
        onPress: async () => {
          await Api.updateSettings({
            sheet: {
              url: "", sheet_id: "", gid: "", tab_name: "",
              headers: [], column_mapping: {},
            },
          } as Partial<SettingsT>);
          setSheetUrl("");
          setPreview(null);
          setMapping({});
          setSheetStatus("idle");
          setConnectedSheetId("");
          setConnectedHeaders([]);
        },
      },
    ]);
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Settings</Text>
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          testID="settings-scroll"
          contentContainerStyle={{ padding: 16, paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled"
        >
          {/* Google Sheet */}
          <Section title="Google Sheet (Orders source)" icon="logo-google">
            <View style={styles.sampleBox} testID="sheet-sample-box">
              <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Ionicons name="bulb-outline" size={14} color={colors.primary} />
                <Text style={styles.sampleTitle}>First time? Use the sample template</Text>
              </View>
              <Text style={styles.sampleText}>
                Download the sample CSV → open Google Sheets → File → Import → upload CSV → Replace current sheet. Share as "Anyone with the link → Viewer" and paste URL below.
              </Text>
              <Text style={styles.sampleCols}>
                Columns: Timestamp · Order ID · Name · Phone · Address · City · State · Pincode · Item · Amount · Payment Mode
              </Text>
              <TouchableOpacity
                testID="sheet-download-sample"
                style={styles.sampleBtn}
                onPress={() =>
                  Linking.openURL(
                    `${process.env.EXPO_PUBLIC_BACKEND_URL}/api/sheets/sample-template`
                  )
                }
              >
                <Ionicons name="download-outline" size={16} color="#fff" />
                <Text style={styles.sampleBtnText}>Download Sample CSV</Text>
              </TouchableOpacity>
            </View>

            {sheetStatus === "connected" && !preview ? (
              <View>
                <View style={styles.connectedBox}>
                  <Ionicons name="checkmark-circle" size={18} color={colors.successText} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.connectedTitle}>Connected</Text>
                    <Text style={styles.connectedSub} numberOfLines={1}>
                      Sheet: {connectedSheetId}
                    </Text>
                    <Text style={styles.connectedSub}>
                      {connectedHeaders.length} columns mapped
                    </Text>
                  </View>
                </View>
                <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                  <TouchableOpacity
                    testID="sheet-open-btn"
                    style={[styles.outlineBtn, { flex: 1 }]}
                    onPress={() => sheetUrl && Linking.openURL(sheetUrl)}
                  >
                    <Ionicons name="open-outline" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Open Sheet</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-remap-btn"
                    style={[styles.outlineBtn, { flex: 1 }]}
                    onPress={fetchPreview}
                  >
                    <Ionicons name="refresh" size={16} color={colors.text} />
                    <Text style={styles.outlineBtnText}>Refresh / Re-map</Text>
                  </TouchableOpacity>
                  <TouchableOpacity
                    testID="sheet-disconnect-btn"
                    style={[styles.outlineBtn, { borderColor: colors.dangerText }]}
                    onPress={disconnectSheet}
                  >
                    <Ionicons name="trash-outline" size={16} color={colors.dangerText} />
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <>
                <Text style={styles.hint}>
                  Share your sheet: File → Share → General access → "Anyone with the link → Viewer". Then paste link below.
                </Text>
                <TextInput
                  testID="sheet-url-input"
                  value={sheetUrl}
                  onChangeText={setSheetUrl}
                  placeholder="https://docs.google.com/spreadsheets/d/..."
                  placeholderTextColor="#9CA3AF"
                  style={[styles.input, { marginTop: 8 }]}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
                <TouchableOpacity
                  testID="sheet-preview-btn"
                  onPress={fetchPreview}
                  style={[styles.saveBtn, { marginTop: 10 }]}
                  disabled={sheetStatus === "loading"}
                >
                  {sheetStatus === "loading" ? (
                    <ActivityIndicator color="#fff" />
                  ) : (
                    <>
                      <Ionicons name="download" size={18} color="#fff" />
                      <Text style={styles.saveBtnText}>Fetch & Preview</Text>
                    </>
                  )}
                </TouchableOpacity>
              </>
            )}

            {preview && (
              <View style={{ marginTop: 16 }}>
                <Text style={styles.subTitle}>
                  {preview.total_rows} rows · {preview.headers.length} columns
                </Text>
                <Text style={[styles.hint, { marginTop: 4 }]}>
                  Map each field to a column from your sheet:
                </Text>
                {SHEET_FIELDS.map((f) => (
                  <View key={f.key} style={styles.mapRow} testID={`map-row-${f.key}`}>
                    <Text style={styles.mapLabel}>{f.label}</Text>
                    <TouchableOpacity
                      testID={`map-pick-${f.key}`}
                      style={styles.mapPick}
                      onPress={() => setPickerForField(f.key)}
                    >
                      <Text
                        style={[
                          styles.mapPickText,
                          !mapping[f.key] && { color: "#9CA3AF" },
                        ]}
                        numberOfLines={1}
                      >
                        {mapping[f.key] || "— pick column —"}
                      </Text>
                      <Ionicons name="chevron-down" size={16} color={colors.textMuted} />
                    </TouchableOpacity>
                  </View>
                ))}
                <TouchableOpacity
                  testID="sheet-save-mapping-btn"
                  onPress={saveSheet}
                  style={[styles.saveBtn, { marginTop: 12 }]}
                >
                  <Ionicons name="save" size={18} color="#fff" />
                  <Text style={styles.saveBtnText}>Save Mapping & Connect</Text>
                </TouchableOpacity>
              </View>
            )}
          </Section>

          {/* Column picker modal */}
          <Modal
            visible={!!pickerForField}
            transparent
            animationType="fade"
            onRequestClose={() => setPickerForField(null)}
          >
            <TouchableOpacity
              style={styles.modalBackdrop}
              activeOpacity={1}
              onPress={() => setPickerForField(null)}
            >
              <View style={styles.pickerCard}>
                <Text style={styles.pickerTitle}>
                  Pick column for{" "}
                  {SHEET_FIELDS.find((f) => f.key === pickerForField)?.label}
                </Text>
                <ScrollView style={{ maxHeight: 320 }}>
                  <TouchableOpacity
                    style={styles.pickerItem}
                    onPress={() => {
                      if (pickerForField) {
                        setMapping((m) => {
                          const copy = { ...m };
                          delete copy[pickerForField];
                          return copy;
                        });
                      }
                      setPickerForField(null);
                    }}
                  >
                    <Text style={[styles.pickerItemText, { color: colors.textMuted }]}>
                      — None —
                    </Text>
                  </TouchableOpacity>
                  {(preview?.headers || []).map((h) => (
                    <TouchableOpacity
                      key={h}
                      style={styles.pickerItem}
                      onPress={() => {
                        if (pickerForField) {
                          setMapping((m) => ({ ...m, [pickerForField]: h }));
                        }
                        setPickerForField(null);
                      }}
                    >
                      <Text style={styles.pickerItemText}>{h}</Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              </View>
            </TouchableOpacity>
          </Modal>

          {/* Brand */}
          <Section title="Brand on Labels" icon="pricetag-outline">
            <Text style={styles.hint}>
              Shown at the top of every printed label. Upload a logo OR type a business name — and pick which one to show.
            </Text>
            <Field label="Brand / Business Name">
              <TextInput
                testID="brand-name-input"
                value={brandName}
                onChangeText={setBrandName}
                placeholder="e.g. Kisan Sarthi Organic"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Logo (optional)">
              <View style={styles.segmentRow}>
                <TouchableOpacity
                  testID="logo-shape-square"
                  style={[styles.segmentBtn, logoShape === "square" && styles.segmentBtnActive]}
                  onPress={() => setLogoShape("square")}
                >
                  <Ionicons name="square-outline" size={14} color={logoShape === "square" ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, logoShape === "square" && styles.segmentTextActive]}>
                    Square (1:1)
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="logo-shape-wide"
                  style={[styles.segmentBtn, logoShape === "wide" && styles.segmentBtnActive]}
                  onPress={() => setLogoShape("wide")}
                >
                  <Ionicons name="remove-outline" size={18} color={logoShape === "wide" ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, logoShape === "wide" && styles.segmentTextActive]}>
                    Wide / Banner (4:1)
                  </Text>
                </TouchableOpacity>
              </View>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginTop: 8 }}>
                {brandLogo ? (
                  <Image
                    source={{ uri: brandLogo.startsWith("data:") ? brandLogo : `data:image/png;base64,${brandLogo}` }}
                    style={
                      logoShape === "wide"
                        ? { width: 160, height: 40, borderRadius: 6, backgroundColor: "#F3F4F6" }
                        : { width: 70, height: 70, borderRadius: 10, backgroundColor: "#F3F4F6" }
                    }
                    resizeMode="contain"
                  />
                ) : (
                  <View
                    style={
                      logoShape === "wide"
                        ? { width: 160, height: 40, borderRadius: 6, backgroundColor: "#F3F4F6", alignItems: "center", justifyContent: "center" }
                        : { width: 70, height: 70, borderRadius: 10, backgroundColor: "#F3F4F6", alignItems: "center", justifyContent: "center" }
                    }
                  >
                    <Ionicons name="image-outline" size={28} color={colors.textMuted} />
                  </View>
                )}
                <View style={{ flex: 1, gap: 6 }}>
                  <TouchableOpacity
                    testID="brand-logo-upload"
                    style={[styles.smallBtn, { backgroundColor: colors.primary }]}
                    onPress={async () => {
                      try {
                        const res = await ImagePicker.launchImageLibraryAsync({
                          mediaTypes: ImagePicker.MediaTypeOptions.Images,
                          base64: true,
                          quality: 0.85,
                          allowsEditing: true,
                          aspect: logoShape === "wide" ? [4, 1] : [1, 1],
                        });
                        if (!res.canceled && res.assets?.[0]?.base64) {
                          setBrandLogo(`data:image/png;base64,${res.assets[0].base64}`);
                        }
                      } catch (e: any) {
                        Alert.alert("Upload failed", e?.message || "Could not pick image");
                      }
                    }}
                  >
                    <Ionicons name="cloud-upload-outline" size={14} color="#fff" />
                    <Text style={styles.smallBtnText}>
                      {brandLogo ? "Change Logo" : `Upload ${logoShape === "wide" ? "Wide" : "Square"} Logo`}
                    </Text>
                  </TouchableOpacity>
                  {!!brandLogo && (
                    <TouchableOpacity
                      testID="brand-logo-remove"
                      style={[styles.smallBtn, { backgroundColor: "#EF4444" }]}
                      onPress={() => setBrandLogo("")}
                    >
                      <Ionicons name="trash-outline" size={14} color="#fff" />
                      <Text style={styles.smallBtnText}>Remove</Text>
                    </TouchableOpacity>
                  )}
                </View>
              </View>
              <Text style={styles.hint}>
                💡 Tip: Pick the shape FIRST, then upload. Cropper will guide you to the right aspect.
              </Text>
            </Field>
            <Field label="Show on Label">
              <View style={styles.segmentRow}>
                <TouchableOpacity
                  testID="prefer-logo-on"
                  style={[styles.segmentBtn, preferLogo && styles.segmentBtnActive]}
                  onPress={() => setPreferLogo(true)}
                  disabled={!brandLogo}
                >
                  <Ionicons name="image" size={14} color={preferLogo ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, preferLogo && styles.segmentTextActive]}>
                    Logo {!brandLogo && "(upload first)"}
                  </Text>
                </TouchableOpacity>
                <TouchableOpacity
                  testID="prefer-logo-off"
                  style={[styles.segmentBtn, !preferLogo && styles.segmentBtnActive]}
                  onPress={() => setPreferLogo(false)}
                >
                  <Ionicons name="text" size={14} color={!preferLogo ? "#fff" : colors.text} />
                  <Text style={[styles.segmentText, !preferLogo && styles.segmentTextActive]}>
                    Brand Name
                  </Text>
                </TouchableOpacity>
              </View>
              <Text style={styles.hint}>
                {preferLogo && brandLogo ? "🖼 Labels will show your logo" : "🔤 Labels will show the brand name"}
              </Text>
            </Field>
          </Section>

          {/* Label Customization — field visibility toggles */}
          <Section title="Label Fields (Show / Hide)" icon="options-outline">
            <Text style={styles.hint}>
              Toggle which optional fields appear on the printed label.
              Core fields (Name, Address, Barcode, PAID/COD) always show.
            </Text>
            {/* Brand Tagline (prints under the sender brand name in the label footer) */}
            <View style={{ marginBottom: 6 }}>
              <Text style={styles.hint}>
                Brand Tagline — prints once on every label, under your brand name in the
                "From" footer area (e.g. "Har Pal Prakruti Ke Sang"). Set once, applies to all.
              </Text>
              <TextInput
                testID="shipment-tagline-input"
                value={shipmentTagline}
                onChangeText={setShipmentTagline}
                placeholder="e.g. Har Pal Prakruti Ke Sang"
                placeholderTextColor="#9CA3AF"
                style={[styles.input, { marginBottom: 6 }]}
              />
            </View>
            {([
              { key: "oid" as const, label: "Order ID (OID)" },
              { key: "dispatch_date" as const, label: "Dispatch Date (DD)" },
              { key: "weight" as const, label: "Weight" },
              { key: "item" as const, label: "Item Description" },
              { key: "phone" as const, label: "Customer Phone" },
              { key: "customer_id" as const, label: "Courier Customer ID" },
              { key: "token_info" as const, label: "Token / Advance Info (footer)" },
              { key: "box_dimensions" as const, label: "Box Dimensions" },
              { key: "shipment_notes" as const, label: "Shipment Notes" },
            ]).map((f) => (
              <View key={f.key} style={styles.toggleRow}>
                <Text style={styles.toggleLabel}>{f.label}</Text>
                <Switch
                  testID={`label-toggle-${f.key}`}
                  value={!!(labelFields as any)[f.key]}
                  onValueChange={(v) =>
                    setLabelFields({ ...labelFields, [f.key]: v })
                  }
                  trackColor={{ false: "#E5E7EB", true: colors.primary }}
                  thumbColor="#fff"
                />
              </View>
            ))}
          </Section>

          {/* Sender */}
          <Section title="Sender / From Address" icon="business-outline">
            <Field label="Business / Sender Name">
              <TextInput
                testID="sender-name-input"
                value={sender.name}
                onChangeText={(v) => setSender({ ...sender, name: v })}
                placeholder="Your shop name"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Phone">
              <TextInput
                testID="sender-phone-input"
                value={sender.phone}
                onChangeText={(v) => setSender({ ...sender, phone: v })}
                placeholder="Contact number"
                placeholderTextColor="#9CA3AF"
                keyboardType="phone-pad"
                style={styles.input}
              />
            </Field>
            <Field label="Address Line 1">
              <TextInput
                testID="sender-addr1-input"
                value={sender.address_line1}
                onChangeText={(v) => setSender({ ...sender, address_line1: v })}
                placeholder="Shop / Street"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <Field label="Address Line 2">
              <TextInput
                testID="sender-addr2-input"
                value={sender.address_line2}
                onChangeText={(v) => setSender({ ...sender, address_line2: v })}
                placeholder="Area / Landmark"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <Field label="City">
                  <TextInput
                    testID="sender-city-input"
                    value={sender.city}
                    onChangeText={(v) => setSender({ ...sender, city: v })}
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Pincode">
                  <TextInput
                    testID="sender-pincode-input"
                    value={sender.pincode}
                    onChangeText={(v) => setSender({ ...sender, pincode: v })}
                    keyboardType="number-pad"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            <Field label="State">
              <TextInput
                testID="sender-state-input"
                value={sender.state}
                onChangeText={(v) => setSender({ ...sender, state: v })}
                style={styles.input}
              />
            </Field>
            <View style={styles.switchRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.switchLabel}>Show contact on labels</Text>
                <Text style={styles.switchHint}>Toggle off to hide sender phone on printed labels</Text>
              </View>
              <Switch
                testID="show-contact-switch"
                value={sender.show_contact}
                onValueChange={(v) => setSender({ ...sender, show_contact: v })}
                trackColor={{ false: "#D1D5DB", true: colors.primary }}
                thumbColor="#fff"
              />
            </View>
          </Section>

          {/* Templates */}
          <Section title="Customer Messages" icon="chatbubbles-outline">
            <View style={styles.infoBox} testID="messages-info-box">
              <Ionicons name="information-circle-outline" size={16} color={colors.primary} />
              <Text style={styles.infoText}>
                <Text style={{ fontWeight: "800" }}>How templates work:</Text> These messages are NOT auto-sent. When you tap the WhatsApp or Copy icons on a shipment, the app fills in the template with actual values (customer name, tracking ID, etc.) and opens WhatsApp or copies to clipboard. The ETA days field is just a placeholder number you can customize — it's not a scheduler.
              </Text>
            </View>
            <Field label="WhatsApp Template (for notification)">
              <Text style={styles.hint}>
                Use: {"{customer_name}"}, {"{order_id}"}, {"{courier}"}, {"{tracking_id}"}, {"{tracking_url}"}, {"{amount}"}, {"{eta_days}"}
              </Text>
              <View style={styles.presetRow}>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setTemplate(PRESETS.wa_gujarati)}
                  testID="preset-wa-gu"
                >
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>ગુજરાતી Professional</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setTemplate(PRESETS.wa_english)}
                  testID="preset-wa-en"
                >
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>English Professional</Text>
                </TouchableOpacity>
              </View>
              <TextInput
                testID="whatsapp-template-input"
                value={template}
                onChangeText={setTemplate}
                multiline
                style={[styles.input, { height: 160, textAlignVertical: "top", paddingTop: 10 }]}
              />
              {showPreview && (
                <View style={styles.previewBox}>
                  <View style={styles.previewHeader}>
                    <Ionicons name="eye-outline" size={14} color={colors.primary} />
                    <Text style={styles.previewTitle}>Live Preview (WhatsApp)</Text>
                  </View>
                  <Text style={styles.previewText}>
                    {fillTemplate(template || PRESETS.wa_gujarati, brandName)}
                  </Text>
                </View>
              )}
            </Field>
            <Field label="Copy-All Template (for quick copy)">
              <Text style={styles.hint}>
                Use: {"{customer_name}"}, {"{order_id}"}, {"{courier}"}, {"{tracking_id}"}, {"{tracking_url}"}, {"{amount}"}
              </Text>
              <View style={styles.presetRow}>
                <TouchableOpacity
                  style={styles.presetBtn}
                  onPress={() => setCopyTemplate(PRESETS.copy_pro)}
                  testID="preset-copy-pro"
                >
                  <Ionicons name="sparkles-outline" size={13} color={colors.primary} />
                  <Text style={styles.presetText}>Professional Layout</Text>
                </TouchableOpacity>
              </View>
              <TextInput
                testID="copy-template-input"
                value={copyTemplate}
                onChangeText={setCopyTemplate}
                multiline
                style={[styles.input, { height: 160, textAlignVertical: "top", paddingTop: 10 }]}
              />
              {showPreview && (
                <View style={styles.previewBox}>
                  <View style={styles.previewHeader}>
                    <Ionicons name="eye-outline" size={14} color={colors.primary} />
                    <Text style={styles.previewTitle}>Live Preview (Copy)</Text>
                  </View>
                  <Text style={styles.previewText}>
                    {fillTemplate(copyTemplate || PRESETS.copy_pro, brandName)}
                  </Text>
                </View>
              )}
            </Field>
            <Field label="Default ETA (days)">
              <TextInput
                testID="eta-days-input"
                value={etaDays}
                onChangeText={setEtaDays}
                keyboardType="number-pad"
                style={styles.input}
              />
            </Field>
          </Section>

          <TouchableOpacity testID="save-settings-btn" style={styles.saveBtn} onPress={saveSender}>
            <Ionicons name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Settings</Text>
          </TouchableOpacity>

          {/* Couriers */}
          <Section title="Courier Partners" icon="rocket-outline">
            {couriers.map((c) => (
              <TouchableOpacity
                key={c.id}
                testID={`courier-card-${c.name}`}
                style={styles.courierCard}
                onPress={() => router.push(`/courier/${c.id}`)}
              >
                <View style={{ flex: 1 }}>
                  <Text style={styles.courierName}>{c.name}</Text>
                  <Text style={styles.courierSub}>
                    Prefix: <Text style={styles.mono}>{c.series_prefix || "—"}</Text> ·
                    Next: <Text style={styles.mono}>
                      {String(c.next_number).padStart(c.number_padding, "0")}
                    </Text>
                  </Text>
                  {c.contact_phone ? (
                    <Text style={styles.courierSub}>📞 {c.contact_phone}</Text>
                  ) : null}
                </View>
                <Ionicons name="chevron-forward" size={20} color={colors.textMuted} />
              </TouchableOpacity>
            ))}
            <TouchableOpacity
              testID="add-courier-btn"
              style={[styles.saveBtn, { marginTop: 10 }]}
              onPress={() => router.push("/courier/new")}
            >
              <Ionicons name="add" size={18} color="#fff" />
              <Text style={styles.saveBtnText}>Add Courier Partner</Text>
            </TouchableOpacity>
          </Section>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Section({
  title, icon, children,
}: { title: string; icon: keyof typeof Ionicons.glyphMap; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <Ionicons name={icon} size={16} color={colors.primary} />
        <Text style={styles.sectionTitle}>{title}</Text>
      </View>
      {children}
    </View>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ marginBottom: 10 }}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: { paddingHorizontal: 20, paddingTop: 10, paddingBottom: 8 },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
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
    color: colors.text,
    fontWeight: "800",
    letterSpacing: 1,
    textTransform: "uppercase",
  },
  subTitle: { fontSize: 13, fontWeight: "800", color: colors.text },
  fieldLabel: { fontSize: 12, color: colors.textMuted, fontWeight: "700", marginBottom: 6 },
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
  hint: { fontSize: 12, color: colors.textMuted, marginBottom: 4 },
  toggleRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: "#F3F4F6",
  },
  toggleLabel: {
    flex: 1,
    fontSize: 14,
    color: colors.text,
    fontWeight: "600",
    paddingRight: 10,
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: "#F1F1F1",
  },
  switchLabel: { fontWeight: "700", color: colors.text },
  switchHint: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  saveBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 50,
    backgroundColor: colors.primary,
    borderRadius: 12,
    marginBottom: 6,
  },
  saveBtnText: { color: "#fff", fontWeight: "800" },
  outlineBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 42,
    paddingHorizontal: 12,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  outlineBtnText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  courierCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    marginBottom: 8,
    backgroundColor: "#fff",
  },
  courierName: { fontWeight: "800", color: colors.text, fontSize: 14 },
  courierSub: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  mono: { fontFamily: "Courier", fontWeight: "800", color: colors.text },

  connectedBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: 12,
    backgroundColor: colors.successBg,
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#D1FAE5",
  },
  connectedTitle: { fontWeight: "800", color: colors.successText, fontSize: 14 },
  connectedSub: { fontSize: 11, color: colors.successText, marginTop: 2 },

  mapRow: {
    flexDirection: "row",
    alignItems: "center",
    marginBottom: 8,
    gap: 10,
  },
  mapLabel: {
    width: 120,
    fontSize: 12,
    fontWeight: "700",
    color: colors.text,
  },
  mapPick: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    height: 42,
    paddingHorizontal: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 8,
    backgroundColor: "#fff",
  },
  mapPickText: { flex: 1, color: colors.text, fontSize: 13, fontWeight: "600" },

  modalBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    padding: 20,
  },
  pickerCard: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 16,
    maxHeight: "70%",
  },
  pickerTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 10,
  },
  pickerItem: {
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#F3F4F6",
  },
  pickerItemText: { color: colors.text, fontWeight: "600" },

  sampleBox: {
    padding: 12,
    borderWidth: 2,
    borderStyle: "dashed",
    borderColor: colors.primary,
    borderRadius: 10,
    marginBottom: 12,
    backgroundColor: "#FFF7ED",
  },
  sampleTitle: {
    fontSize: 13,
    fontWeight: "800",
    color: colors.text,
  },
  sampleText: { fontSize: 12, color: colors.text, marginTop: 6, lineHeight: 17 },
  sampleCols: {
    fontSize: 11,
    color: colors.textMuted,
    marginTop: 6,
    fontFamily: "Courier",
  },
  sampleBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    height: 40,
    backgroundColor: colors.primary,
    borderRadius: 8,
    marginTop: 10,
  },
  sampleBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },

  infoBox: {
    flexDirection: "row",
    gap: 8,
    padding: 10,
    backgroundColor: "#FFF7ED",
    borderWidth: 1,
    borderColor: "#FED7AA",
    borderRadius: 8,
    marginBottom: 10,
  },
  infoText: {
    flex: 1,
    color: colors.text,
    fontSize: 12,
    lineHeight: 17,
  },
  presetRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginBottom: 8,
  },
  presetBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: "#FFF7ED",
  },
  presetText: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.primary,
  },
  smallBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
  },
  smallBtnText: {
    fontSize: 12,
    fontWeight: "800",
    color: "#fff",
  },
  segmentRow: {
    flexDirection: "row",
    gap: 6,
    marginTop: 2,
    marginBottom: 4,
  },
  segmentBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 10,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1.5,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  segmentBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  segmentText: {
    fontSize: 12,
    fontWeight: "800",
    color: colors.text,
  },
  segmentTextActive: {
    color: "#fff",
  },
  previewBox: {
    marginTop: 8,
    padding: 12,
    backgroundColor: "#ECFDF5",
    borderWidth: 1,
    borderColor: "#A7F3D0",
    borderRadius: 10,
  },
  previewHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    marginBottom: 6,
    paddingBottom: 6,
    borderBottomWidth: 1,
    borderBottomColor: "#A7F3D0",
  },
  previewTitle: {
    fontSize: 12,
    fontWeight: "800",
    color: "#065F46",
  },
  previewText: {
    fontSize: 13,
    color: "#064E3B",
    lineHeight: 20,
  },
});
