import React, { useCallback, useEffect, useState } from "react";
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
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { Api, Courier, Settings as SettingsT, SenderAddress } from "../../lib/api";
import { colors } from "../../lib/theme";

export default function SettingsScreen() {
  const [sender, setSender] = useState<SenderAddress>({
    name: "",
    phone: "",
    address_line1: "",
    address_line2: "",
    city: "",
    state: "",
    pincode: "",
    show_contact: true,
  });
  const [template, setTemplate] = useState("");
  const [etaDays, setEtaDays] = useState("7");
  const [couriers, setCouriers] = useState<Courier[]>([]);

  const [newCourierName, setNewCourierName] = useState("");
  const [newPrefix, setNewPrefix] = useState("");
  const [newNextNum, setNewNextNum] = useState("1");

  const load = useCallback(async () => {
    const [s, cs] = await Promise.all([Api.getSettings(), Api.listCouriers()]);
    setSender(s.sender);
    setTemplate(s.whatsapp_template);
    setEtaDays(String(s.default_eta_days));
    setCouriers(cs);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saveAll = async () => {
    try {
      await Api.updateSettings({
        sender,
        whatsapp_template: template,
        default_eta_days: Number(etaDays) || 7,
      } as Partial<SettingsT>);
      Alert.alert("Saved", "Settings saved successfully.");
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    }
  };

  const addCourier = async () => {
    if (!newCourierName.trim()) return;
    await Api.createCourier({
      name: newCourierName.trim(),
      series_prefix: newPrefix.trim(),
      next_number: Number(newNextNum) || 1,
      number_padding: 4,
    });
    setNewCourierName("");
    setNewPrefix("");
    setNewNextNum("1");
    load();
  };

  const updateCourierField = async (
    c: Courier,
    field: keyof Courier,
    value: any
  ) => {
    await Api.updateCourier(c.id, { [field]: value } as any);
    load();
  };

  const removeCourier = (c: Courier) => {
    Alert.alert("Delete", `Delete ${c.name}?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          await Api.deleteCourier(c.id);
          load();
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
                <Text style={styles.switchHint}>
                  Toggle off to hide sender phone on printed labels
                </Text>
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

          {/* WhatsApp */}
          <Section title="WhatsApp Template" icon="logo-whatsapp">
            <Text style={styles.hint}>
              Use placeholders: {"{customer_name}"}, {"{courier}"},{" "}
              {"{tracking_id}"}, {"{eta_days}"}
            </Text>
            <TextInput
              testID="whatsapp-template-input"
              value={template}
              onChangeText={setTemplate}
              multiline
              placeholder="Message to customer"
              placeholderTextColor="#9CA3AF"
              style={[styles.input, { height: 110, textAlignVertical: "top", paddingTop: 10 }]}
            />
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

          <TouchableOpacity testID="save-settings-btn" style={styles.saveBtn} onPress={saveAll}>
            <Ionicons name="save" size={18} color="#fff" />
            <Text style={styles.saveBtnText}>Save Settings</Text>
          </TouchableOpacity>

          {/* Couriers */}
          <Section title="Courier Partners" icon="rocket-outline">
            {couriers.map((c) => (
              <View
                key={c.id}
                style={styles.courierCard}
                testID={`courier-card-${c.name}`}
              >
                <View style={{ flex: 1 }}>
                  <Text style={styles.courierName}>{c.name}</Text>
                  <Text style={styles.courierSub}>
                    Prefix:{" "}
                    <Text style={styles.mono}>{c.series_prefix || "—"}</Text>{" "}
                    · Next:{" "}
                    <Text style={styles.mono}>
                      {String(c.next_number).padStart(c.number_padding, "0")}
                    </Text>
                  </Text>
                </View>
                <TouchableOpacity
                  onPress={() => {
                    Alert.prompt?.(
                      "Edit next number",
                      `Set next tracking number for ${c.name}`,
                      (text) => {
                        const n = Number(text);
                        if (!isNaN(n)) updateCourierField(c, "next_number", n);
                      },
                      "plain-text",
                      String(c.next_number)
                    );
                  }}
                  testID={`edit-courier-${c.name}`}
                  style={styles.actionIcon}
                >
                  <Ionicons name="create-outline" size={18} color={colors.text} />
                </TouchableOpacity>
                <TouchableOpacity
                  onPress={() => removeCourier(c)}
                  testID={`delete-courier-${c.name}`}
                  style={styles.actionIcon}
                >
                  <Ionicons
                    name="trash-outline"
                    size={18}
                    color={colors.dangerText}
                  />
                </TouchableOpacity>
              </View>
            ))}

            <View style={styles.addCourierBox}>
              <Text style={styles.addTitle}>+ Add Courier</Text>
              <TextInput
                testID="new-courier-name-input"
                placeholder="Courier name"
                placeholderTextColor="#9CA3AF"
                value={newCourierName}
                onChangeText={setNewCourierName}
                style={[styles.input, { marginBottom: 8 }]}
              />
              <View style={{ flexDirection: "row", gap: 8 }}>
                <TextInput
                  testID="new-courier-prefix-input"
                  placeholder="Prefix e.g. ND"
                  placeholderTextColor="#9CA3AF"
                  value={newPrefix}
                  onChangeText={setNewPrefix}
                  style={[styles.input, { flex: 1 }]}
                  autoCapitalize="characters"
                />
                <TextInput
                  testID="new-courier-start-input"
                  placeholder="Start number"
                  placeholderTextColor="#9CA3AF"
                  value={newNextNum}
                  onChangeText={setNewNextNum}
                  keyboardType="number-pad"
                  style={[styles.input, { flex: 1 }]}
                />
              </View>
              <TouchableOpacity
                testID="add-courier-btn"
                style={[styles.saveBtn, { marginTop: 10 }]}
                onPress={addCourier}
              >
                <Ionicons name="add" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>Add Courier</Text>
              </TouchableOpacity>
            </View>
          </Section>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon: keyof typeof Ionicons.glyphMap;
  children: React.ReactNode;
}) {
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
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 8,
  },
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
  hint: { fontSize: 12, color: colors.textMuted, marginBottom: 8 },
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
  courierCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    marginBottom: 8,
  },
  courierName: { fontWeight: "800", color: colors.text, fontSize: 14 },
  courierSub: { fontSize: 12, color: colors.textMuted, marginTop: 2 },
  mono: { fontFamily: "Courier", fontWeight: "800", color: colors.text },
  actionIcon: {
    width: 34,
    height: 34,
    borderRadius: 8,
    backgroundColor: "#F9FAFB",
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  addCourierBox: {
    marginTop: 8,
    padding: 12,
    backgroundColor: "#F9FAFB",
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
    borderRadius: 10,
  },
  addTitle: {
    fontSize: 13,
    fontWeight: "800",
    color: colors.text,
    marginBottom: 8,
  },
});
