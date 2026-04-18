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
} from "react-native";import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useFocusEffect } from "expo-router";
import { Api, Courier, Settings as SettingsT, SenderAddress, SheetPreview, SHEET_FIELDS } from "../../lib/api";
import { colors } from "../../lib/theme";

export default function SettingsScreen() {
  const router = useRouter();

  const [sender, setSender] = useState<SenderAddress>({
    name: "", phone: "", address_line1: "", address_line2: "",
    city: "", state: "", pincode: "", show_contact: true,
  });
  const [template, setTemplate] = useState("");
  const [copyTemplate, setCopyTemplate] = useState("");
  const [etaDays, setEtaDays] = useState("7");
  const [couriers, setCouriers] = useState<Courier[]>([]);

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
        whatsapp_template: template,
        copy_template: copyTemplate,
        default_eta_days: Number(etaDays) || 7,
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
                Use: {"{customer_name}"}, {"{courier}"}, {"{tracking_id}"}, {"{eta_days}"}
              </Text>
              <TextInput
                testID="whatsapp-template-input"
                value={template}
                onChangeText={setTemplate}
                multiline
                style={[styles.input, { height: 100, textAlignVertical: "top", paddingTop: 10 }]}
              />
            </Field>
            <Field label="Copy-All Template (for quick copy)">
              <Text style={styles.hint}>
                Use: {"{customer_name}"}, {"{order_id}"}, {"{courier}"}, {"{tracking_id}"}, {"{tracking_url}"}, {"{amount}"}
              </Text>
              <TextInput
                testID="copy-template-input"
                value={copyTemplate}
                onChangeText={setCopyTemplate}
                multiline
                style={[styles.input, { height: 100, textAlignVertical: "top", paddingTop: 10 }]}
              />
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
});
