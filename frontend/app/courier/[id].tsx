import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  Alert, KeyboardAvoidingView, Platform, ActivityIndicator, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Api, Courier } from "../../lib/api";
import { cleanPhone } from "../../lib/format";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";

export default function CourierEdit() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const isNew = id === "new";
  // Plan-gated: hide Customer-ID-on-label input for tiers without it.
  const flagLabelCustomerId = useFeatureFlag("label_customer_id");

  const [loading, setLoading] = useState(!isNew);
  const [saving, setSaving] = useState(false);

  const [name, setName] = useState("");
  const [prefix, setPrefix] = useState("");
  const [nextNumber, setNextNumber] = useState("1");
  const [padding, setPadding] = useState("5");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [website, setWebsite] = useState("");
  const [trackingTpl, setTrackingTpl] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [notes, setNotes] = useState("");
  // Tracking-ID format rules (Phase-4d — reject garbled scans)
  const [tidPrefix, setTidPrefix] = useState("");
  const [tidSuffix, setTidSuffix] = useState("");
  const [tidLength, setTidLength] = useState("");
  const [tidMinLen, setTidMinLen] = useState("");
  const [tidMaxLen, setTidMaxLen] = useState("");

  const load = useCallback(async () => {
    if (isNew) return;
    try {
      const c = await Api.getCourier(String(id));
      setName(c.name);
      setPrefix(c.series_prefix);
      setNextNumber(String(c.next_number));
      setPadding(String(c.number_padding));
      setPhone(c.contact_phone);
      setEmail(c.contact_email);
      setWebsite(c.website_url);
      setTrackingTpl(c.tracking_url_template);
      setCustomerId((c as any).customer_id || "");
      setNotes(c.notes);
      setTidPrefix((c as any).tracking_id_prefix || "");
      setTidSuffix((c as any).tracking_id_suffix || "");
      setTidLength(String((c as any).tracking_id_length || "") === "0" ? "" : String((c as any).tracking_id_length || ""));
      setTidMinLen(String((c as any).tracking_id_min_length || "") === "0" ? "" : String((c as any).tracking_id_min_length || ""));
      setTidMaxLen(String((c as any).tracking_id_max_length || "") === "0" ? "" : String((c as any).tracking_id_max_length || ""));
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed to load");
      router.back();
    } finally {
      setLoading(false);
    }
  }, [id, isNew, router]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!name.trim()) {
      Alert.alert("Validation", "Courier name is required");
      return;
    }
    setSaving(true);
    try {
      const payload: Partial<Courier> = {
        name: name.trim(),
        series_prefix: prefix.trim(),
        next_number: Number(nextNumber) || 1,
        number_padding: Number(padding) || 4,
        contact_phone: phone.trim(),
        contact_email: email.trim(),
        website_url: website.trim(),
        tracking_url_template: trackingTpl.trim(),
        customer_id: customerId.trim(),
        notes: notes.trim(),
        tracking_id_prefix: tidPrefix.trim().toUpperCase(),
        tracking_id_suffix: tidSuffix.trim().toUpperCase(),
        tracking_id_length: Number(tidLength) || 0,
        tracking_id_min_length: Number(tidMinLen) || 0,
        tracking_id_max_length: Number(tidMaxLen) || 0,
      } as any;
      if (isNew) {
        await Api.createCourier(payload);
      } else {
        await Api.updateCourier(String(id), payload);
      }
      router.back();
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed");
    } finally {
      setSaving(false);
    }
  };

  const remove = () => {
    Alert.alert("Delete courier?", `Delete ${name}?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          await Api.deleteCourier(String(id));
          router.back();
        },
      },
    ]);
  };

  const shareContact = async () => {
    const lines = [
      `${name}`,
      phone ? `📞 ${phone}` : "",
      email ? `✉️ ${email}` : "",
      website ? `🌐 ${website}` : "",
      notes ? `\n${notes}` : "",
    ].filter(Boolean).join("\n");
    if (!lines) {
      Alert.alert("Nothing to share", "Add courier contact details first.");
      return;
    }
    if (Platform.OS === "web" && phone) {
      await Clipboard.setStringAsync(lines);
      Alert.alert("Copied", "Courier contact copied. Paste in WhatsApp.");
      return;
    }
    const ph = cleanPhone(phone) || "";
    const url = `https://wa.me/${ph}?text=${encodeURIComponent(lines)}`;
    Linking.openURL(url);
  };

  const copyContact = async () => {
    const lines = [
      name,
      phone ? `Phone: ${phone}` : "",
      email ? `Email: ${email}` : "",
      website ? `Website: ${website}` : "",
    ].filter(Boolean).join("\n");
    await Clipboard.setStringAsync(lines);
    Alert.alert("Copied", "Courier contact copied to clipboard.");
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.safe}>
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity testID="courier-back" onPress={() => router.back()} style={styles.backBtn}>
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>
          {isNew ? "Add Courier" : name || "Edit Courier"}
        </Text>
        {!isNew ? (
          <TouchableOpacity testID="courier-delete" onPress={remove} style={styles.backBtn}>
            <Ionicons name="trash-outline" size={20} color={colors.dangerText} />
          </TouchableOpacity>
        ) : (
          <View style={{ width: 40 }} />
        )}
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled">

          <Section title="Basic">
            <Field label="Courier Name *">
              <TextInput testID="courier-name-input" value={name} onChangeText={setName}
                placeholder="e.g. Nandan Courier" placeholderTextColor="#9CA3AF" style={styles.input} />
            </Field>
          </Section>

          <Section title="Tracking Series">
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <Field label="Prefix">
                  <TextInput testID="courier-prefix-input" value={prefix} onChangeText={setPrefix}
                    placeholder="e.g. ND" placeholderTextColor="#9CA3AF"
                    autoCapitalize="characters" style={styles.input} />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Padding (digits)">
                  <TextInput testID="courier-padding-input" value={padding} onChangeText={setPadding}
                    keyboardType="number-pad" placeholder="5" placeholderTextColor="#9CA3AF"
                    style={styles.input} />
                </Field>
              </View>
            </View>
            <Field label="Next Number">
              <TextInput testID="courier-next-input" value={nextNumber} onChangeText={setNextNumber}
                keyboardType="number-pad" placeholder="1" placeholderTextColor="#9CA3AF"
                style={styles.input} />
            </Field>
            <View style={styles.preview}>
              <Text style={styles.previewLabel}>Next tracking ID will be:</Text>
              <Text style={styles.previewTrack}>
                {`${prefix}${String(Number(nextNumber) || 0).padStart(Number(padding) || 4, "0")}`}
              </Text>
            </View>
          </Section>

          <Section title="Tracking-ID Format Validation (for scans)">
            <Text style={styles.hint}>
              Set these so the camera scanner and manual entry reject
              garbled or wrong reads. Example — India Post Speed Post
              IDs always start with <Text style={{ fontWeight: "900" }}>EG</Text>,
              end with <Text style={{ fontWeight: "900" }}>IN</Text>, and
              are 13 characters long.
            </Text>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <Field label="Starts with">
                  <TextInput
                    testID="courier-tid-prefix"
                    value={tidPrefix}
                    onChangeText={(t) => setTidPrefix(t.toUpperCase())}
                    placeholder="e.g. EG"
                    placeholderTextColor="#9CA3AF"
                    autoCapitalize="characters"
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Ends with">
                  <TextInput
                    testID="courier-tid-suffix"
                    value={tidSuffix}
                    onChangeText={(t) => setTidSuffix(t.toUpperCase())}
                    placeholder="e.g. IN"
                    placeholderTextColor="#9CA3AF"
                    autoCapitalize="characters"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            <Field label="Exact length (chars)">
              <TextInput
                testID="courier-tid-length"
                value={tidLength}
                onChangeText={(t) => setTidLength(t.replace(/\D/g, ""))}
                keyboardType="number-pad"
                placeholder="e.g. 13  (leave blank if variable)"
                placeholderTextColor="#9CA3AF"
                style={styles.input}
              />
            </Field>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <Field label="Min length">
                  <TextInput
                    testID="courier-tid-min"
                    value={tidMinLen}
                    onChangeText={(t) => setTidMinLen(t.replace(/\D/g, ""))}
                    keyboardType="number-pad"
                    placeholder="blank = off"
                    placeholderTextColor="#9CA3AF"
                    style={styles.input}
                  />
                </Field>
              </View>
              <View style={{ flex: 1 }}>
                <Field label="Max length">
                  <TextInput
                    testID="courier-tid-max"
                    value={tidMaxLen}
                    onChangeText={(t) => setTidMaxLen(t.replace(/\D/g, ""))}
                    keyboardType="number-pad"
                    placeholder="blank = off"
                    placeholderTextColor="#9CA3AF"
                    style={styles.input}
                  />
                </Field>
              </View>
            </View>
            {tidPrefix || tidSuffix || tidLength ? (
              <View style={[styles.preview, { backgroundColor: "#ECFDF5", borderColor: "#86EFAC" }]}>
                <Text style={[styles.previewLabel, { color: "#065F46" }]}>
                  Scanner will accept only IDs like:
                </Text>
                <Text style={[styles.previewTrack, { color: "#065F46" }]}>
                  {tidPrefix || "***"}
                  {tidLength
                    ? "".padEnd(Math.max(0, Number(tidLength) - tidPrefix.length - tidSuffix.length), "X")
                    : "XXXXXX"}
                  {tidSuffix || "***"}
                </Text>
              </View>
            ) : null}
          </Section>

          <Section title="Courier Contact (for customer queries)">
            <Field label="Phone">
              <TextInput testID="courier-phone-input" value={phone} onChangeText={setPhone}
                placeholder="Support number" placeholderTextColor="#9CA3AF"
                keyboardType="phone-pad" style={styles.input} />
            </Field>
            <Field label="Email">
              <TextInput testID="courier-email-input" value={email} onChangeText={setEmail}
                placeholder="support@example.com" placeholderTextColor="#9CA3AF"
                keyboardType="email-address" autoCapitalize="none" style={styles.input} />
            </Field>
            <Field label="Website">
              <TextInput testID="courier-website-input" value={website} onChangeText={setWebsite}
                placeholder="https://" placeholderTextColor="#9CA3AF"
                autoCapitalize="none" style={styles.input} />
            </Field>
            <Field label="Tracking URL Template">
              <Text style={styles.hint}>
                Use {"{tracking_id}"} — we'll replace it when sharing
              </Text>
              <TextInput testID="courier-tracking-url-input" value={trackingTpl}
                onChangeText={setTrackingTpl}
                placeholder="https://courier.com/track?id={tracking_id}"
                placeholderTextColor="#9CA3AF" autoCapitalize="none" style={styles.input} />
            </Field>
            {flagLabelCustomerId && (
            <Field label="Customer ID (prints on label)">
              <Text style={styles.hint}>
                Optional. Shown as small text below the courier name on the printed label
                (e.g. India Post Cust ID: 1000057527).
              </Text>
              <TextInput testID="courier-customer-id-input" value={customerId}
                onChangeText={setCustomerId}
                placeholder="e.g. 1000057527"
                placeholderTextColor="#9CA3AF" autoCapitalize="none" style={styles.input} />
            </Field>
            )}
            <Field label="Notes">
              <TextInput testID="courier-notes-input" value={notes} onChangeText={setNotes}
                placeholder="Anything to remember" placeholderTextColor="#9CA3AF"
                multiline style={[styles.input, { height: 80, textAlignVertical: "top", paddingTop: 10 }]} />
            </Field>

            {!isNew && (
              <View style={{ flexDirection: "row", gap: 8, marginTop: 6 }}>
                <TouchableOpacity testID="courier-copy-contact" onPress={copyContact}
                  style={[styles.outlineBtn, { flex: 1 }]}>
                  <Ionicons name="copy-outline" size={16} color={colors.text} />
                  <Text style={styles.outlineBtnText}>Copy Contact</Text>
                </TouchableOpacity>
                <TouchableOpacity testID="courier-whatsapp-share" onPress={shareContact}
                  style={[styles.outlineBtn, { flex: 1, borderColor: "#25D366" }]}>
                  <Ionicons name="logo-whatsapp" size={16} color="#25D366" />
                  <Text style={[styles.outlineBtnText, { color: "#25D366" }]}>WhatsApp</Text>
                </TouchableOpacity>
              </View>
            )}
          </Section>

          <TouchableOpacity testID="courier-save-btn" style={styles.saveBtn} onPress={save} disabled={saving}>
            {saving ? <ActivityIndicator color="#fff" /> : (
              <>
                <Ionicons name="save" size={18} color="#fff" />
                <Text style={styles.saveBtnText}>{isNew ? "Add Courier" : "Save Changes"}</Text>
              </>
            )}
          </TouchableOpacity>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
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
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingTop: 6, paddingBottom: 8,
  },
  backBtn: {
    width: 40, height: 40, borderRadius: 10, borderWidth: 2, borderColor: "#E5E7EB",
    backgroundColor: "#fff", justifyContent: "center", alignItems: "center",
  },
  title: { fontSize: 18, fontWeight: "800", color: colors.text, flex: 1, textAlign: "center", paddingHorizontal: 10 },
  section: {
    backgroundColor: colors.surface, borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 14,
  },
  sectionTitle: {
    fontSize: 12, color: colors.textMuted, fontWeight: "800",
    letterSpacing: 1, textTransform: "uppercase", marginBottom: 10,
  },
  fieldLabel: { fontSize: 12, color: colors.textMuted, fontWeight: "700", marginBottom: 6 },
  input: {
    minHeight: 46, borderWidth: 2, borderColor: "#E5E7EB", borderRadius: 10,
    paddingHorizontal: 14, fontSize: 15, color: colors.text, backgroundColor: colors.surface,
  },
  hint: { fontSize: 12, color: colors.textMuted, marginBottom: 4 },
  preview: {
    marginTop: 6, padding: 10, borderWidth: 2, borderColor: "#E5E7EB",
    borderStyle: "dashed", borderRadius: 10, backgroundColor: "#F9FAFB",
  },
  previewLabel: { fontSize: 11, color: colors.textMuted, fontWeight: "700" },
  previewTrack: {
    marginTop: 4, fontFamily: "Courier", fontSize: 18, fontWeight: "800",
    color: colors.text, letterSpacing: 2,
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    height: 52, backgroundColor: colors.primary, borderRadius: 12,
  },
  saveBtnText: { color: "#fff", fontWeight: "800" },
  outlineBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    height: 44, borderWidth: 2, borderColor: "#E5E7EB", borderRadius: 10, backgroundColor: "#fff",
  },
  outlineBtnText: { fontWeight: "700", color: colors.text, fontSize: 13 },
});
