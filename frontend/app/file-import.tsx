/**
 * /file-import screen — Phase F1 CSV/XLSX bulk-import to pending orders.
 *
 * Flow:
 *   1. User taps "Upload" on the Orders tab → router.push("/file-import")
 *      OR opens this from Settings → "CSV/Excel Import Mapping".
 *   2. We open the system DocumentPicker for .csv / .xlsx.
 *   3. POST /api/orders/import/preview → server returns columns +
 *      first-N sample rows + a suggested mapping (saved-default + naive
 *      header match).
 *   4. User confirms / tweaks the mapping using a dropdown per column.
 *   5. On "Import" → POST /api/orders/import/commit. The server
 *      inserts pending_orders rows with source="file" and the row
 *      shows up immediately on the Orders tab (badge: FILE).
 *
 * Settings entry — when launched from Settings (mode=settings via
 * search params), we still let the user upload a sample to map; the
 * "Save as default" toggle is the primary value. The Import button is
 * still functional in case the user wants to map and import in one go.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet, Alert,
  ActivityIndicator, Switch, Modal, Pressable,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";
import PhIcon from "../components/PhIcon";

type Preview = Awaited<ReturnType<typeof Api.fileImportPreview>>;

type PickedFile = { uri: string; name: string; mime: string };

const FIELD_LABEL: Record<string, string> = {
  // Identity
  customer_name:      "Customer Name",
  customer_phone:     "Customer Phone (mobile)",
  customer_alt_phone: "Alternate Phone",
  customer_email:     "Email Address",
  customer_gstin:     "GSTIN",
  // Address (virtual)
  address:            "Address (line1 + line2 auto-merge)",
  city:               "City",
  state:              "State",
  pincode:            "Pincode",
  // Payment
  amount:             "Order Amount (₹)",
  token_amount:       "Order Token / Advance (₹)",
  payment_mode:       "Payment Mode (COD / PAID)",
  // Items / parcel
  items:              "Items / Products",
  category:           "Item Category",
  weight:             "Parcel Weight",
  box_dimensions:     "Box Size (e.g. 10×8×4)",
  box_length:         "Box Length",
  box_width:          "Box Width",
  box_height:         "Box Height",
  // Misc
  courier_hint:       "Courier (hint)",
  order_id:           "Order ID",
  notes:              "Notes / Remarks",
  // Phase F2.1 — order state + creation timestamp
  status:             "Status (Pending / Shipped / Delivered…)",
  created_at_override: "Order Date / Timestamp",
};

export default function FileImportScreen() {
  const params = useLocalSearchParams<{ mode?: string }>();
  const settingsMode = params.mode === "settings";
  const router = useRouter();

  const [picked, setPicked] = useState<PickedFile | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [saveDefault, setSaveDefault] = useState(false);
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [pickerCol, setPickerCol] = useState<string | null>(null);

  // Load saved default once on mount so settings mode shows it.
  useEffect(() => {
    let cancelled = false;
    Api.getFileImportMapping().then((r) => {
      if (cancelled) return;
      if (r.mapping && Object.keys(r.mapping).length) {
        setMapping(r.mapping);
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const pickFile = async () => {
    try {
      const r = await DocumentPicker.getDocumentAsync({
        type: [
          "text/csv",
          "application/vnd.ms-excel",
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (r.canceled || !r.assets?.[0]) return;
      const a = r.assets[0];
      const file: PickedFile = {
        uri:  a.uri,
        name: a.name || "upload",
        mime: a.mimeType || "text/csv",
      };
      setPicked(file);
      setLoading(true);
      try {
        const p = await Api.fileImportPreview(file.uri, file.name, file.mime);
        setPreview(p);
        setMapping((prev) => ({ ...p.suggested, ...prev }));
      } catch (e: any) {
        Alert.alert("Could not read file", e?.response?.data?.detail || e?.message || "Unknown");
        setPicked(null);
        setPreview(null);
      } finally {
        setLoading(false);
      }
    } catch (e: any) {
      Alert.alert("Picker error", e?.message || "Could not open file picker");
    }
  };

  const setColMapping = (col: string, field: string) => {
    setMapping((m) => {
      const next = { ...m };
      if (!field) delete next[col];
      else next[col] = field;
      return next;
    });
  };

  const mappedCount = useMemo(() => Object.values(mapping).filter(Boolean).length, [mapping]);

  // Phase F1.1 — count of columns mapped to the virtual "address" so
  // we can show a friendly auto-merge hint.
  const addressColCount = useMemo(
    () => Object.values(mapping).filter((f) => f === "address").length,
    [mapping],
  );

  const commit = async () => {
    if (!picked || !preview) return;
    if (!mapping["customer_name" as any] && !Object.values(mapping).includes("customer_name")
        && !Object.values(mapping).includes("customer_phone")) {
      Alert.alert("Mapping incomplete", "Map at least Customer Name OR Customer Phone before importing.");
      return;
    }
    setCommitting(true);
    try {
      const r = await Api.fileImportCommit(
        picked.uri, picked.name, picked.mime, mapping, saveDefault,
      );
      Alert.alert(
        "Import complete 🎉",
        `${r.imported} of ${r.total} rows imported${r.skipped ? `; ${r.skipped} skipped` : ""}.`,
        [{
          text: "View Orders",
          onPress: () => router.replace("/(tabs)/orders" as any),
        }],
      );
    } catch (e: any) {
      Alert.alert("Import failed", e?.response?.data?.detail || e?.message || "Unknown error");
    } finally {
      setCommitting(false);
    }
  };

  const saveOnlyMapping = async () => {
    if (Object.keys(mapping).length === 0) {
      Alert.alert("Empty mapping", "Pick a file first to define column → field mappings.");
      return;
    }
    try {
      await Api.putFileImportMapping(mapping);
      Alert.alert("Saved", "Default mapping saved. It'll be auto-applied on the next upload.");
    } catch (e: any) {
      Alert.alert("Save failed", e?.response?.data?.detail || e?.message || "Unknown error");
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>
          {settingsMode ? "CSV / Excel Mapping" : "Import Orders from File"}
        </Text>
        <View style={{ width: 22 }} />
      </View>

      <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
        {/* Step 1: pick file */}
        <View style={styles.stepCard}>
          <Text style={styles.stepLabel}>1.  Choose file</Text>
          <Text style={styles.helpText}>
            Supported: .csv, .xlsx · max 5,000 rows · max 10 MB
          </Text>
          <TouchableOpacity style={styles.pickBtn} onPress={pickFile}>
            <PhIcon name="cloud-upload" size={20} color="#fff" />
            <Text style={styles.pickBtnTxt}>
              {picked ? "Choose another file" : "Pick a CSV or Excel file"}
            </Text>
          </TouchableOpacity>
          {picked ? (
            <Text style={styles.fileName}>📄 {picked.name}</Text>
          ) : null}
          {loading ? (
            <View style={{ marginTop: 12 }}>
              <ActivityIndicator color={colors.primary} />
            </View>
          ) : null}
        </View>

        {/* Step 2: column mapping */}
        {preview ? (
          <View style={styles.stepCard}>
            <Text style={styles.stepLabel}>
              2.  Map columns ({mappedCount}/{preview.columns.length})
            </Text>
            <Text style={styles.helpText}>
              Tap a row to choose which schema field that column maps to. Leave blank to ignore it.
            </Text>
            {addressColCount >= 2 ? (
              <View style={{
                backgroundColor: "#ECFDF5", borderColor: "#A7F3D0", borderWidth: 1,
                borderRadius: 8, padding: 10, marginBottom: 8, flexDirection: "row", gap: 8,
              }}>
                <PhIcon name="information-circle" size={16} color="#047857" />
                <Text style={{ flex: 1, fontSize: 12, color: "#065F46" }}>
                  {addressColCount} columns mapped to <Text style={{ fontWeight: "700" }}>Address</Text> — they'll merge with a space separator on import (e.g. line1 + " " + line2).
                </Text>
              </View>
            ) : null}
            {preview.columns.map((c) => {
              const cur = mapping[c] || "";
              return (
                <TouchableOpacity
                  key={c}
                  style={styles.colRow}
                  onPress={() => setPickerCol(c)}
                >
                  <View style={{ flex: 1 }}>
                    <Text style={styles.colHeader}>{c}</Text>
                    <Text style={styles.colSample} numberOfLines={1}>
                      {(preview.sample_rows[0]?.[c] ?? "").slice(0, 60) || "—"}
                    </Text>
                  </View>
                  <View style={styles.fieldPill}>
                    <Text style={[styles.fieldPillTxt, !cur && { color: "#94A3B8" }]}>
                      {cur
                        ? (cur.startsWith("custom:")
                            ? `★ ${preview.custom_fields?.find(
                                (cf: any) => `custom:${cf.id}` === cur,
                              )?.label || "Custom Field"}`
                            : (FIELD_LABEL[cur] || cur))
                        : "Ignore"}
                    </Text>
                    <PhIcon name="chevron-down" size={14} color="#64748B" />
                  </View>
                </TouchableOpacity>
              );
            })}

            <View style={styles.toggleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.toggleTitle}>Save as default mapping</Text>
                <Text style={styles.toggleSub}>
                  Next upload auto-applies these column → field assignments.
                </Text>
              </View>
              <Switch
                value={saveDefault}
                onValueChange={setSaveDefault}
                trackColor={{ false: "#E5E7EB", true: colors.primary }}
              />
            </View>

            <View style={{ flexDirection: "row", gap: 8, marginTop: 16 }}>
              {settingsMode ? (
                <TouchableOpacity
                  style={[styles.commitBtn, { backgroundColor: "#475569" }]}
                  onPress={saveOnlyMapping}
                >
                  <Text style={styles.commitBtnTxt}>Save mapping only</Text>
                </TouchableOpacity>
              ) : null}
              <TouchableOpacity
                style={[styles.commitBtn, committing && { opacity: 0.6 }]}
                onPress={commit}
                disabled={committing}
              >
                {committing ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <PhIcon name="checkmark" size={18} color="#fff" />
                    <Text style={styles.commitBtnTxt}>
                      Import {preview.total_rows} {preview.total_rows === 1 ? "row" : "rows"}
                    </Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          </View>
        ) : null}
      </ScrollView>

      {/* Schema-field picker modal */}
      <Modal
        visible={pickerCol !== null}
        transparent
        animationType="slide"
        onRequestClose={() => setPickerCol(null)}
      >
        <Pressable style={styles.modalScrim} onPress={() => setPickerCol(null)}>
          <Pressable style={styles.modalSheet}>
            <Text style={styles.modalTitle}>
              Map column “{pickerCol}” →
            </Text>
            <ScrollView style={{ maxHeight: 420 }}>
              <TouchableOpacity
                style={styles.fieldOption}
                onPress={() => { setColMapping(pickerCol!, ""); setPickerCol(null); }}
              >
                <Text style={[styles.fieldOptionTxt, { color: "#94A3B8" }]}>(Ignore this column)</Text>
              </TouchableOpacity>
              {(preview?.schema_fields || []).map((f) => (
                <TouchableOpacity
                  key={f}
                  style={styles.fieldOption}
                  onPress={() => { setColMapping(pickerCol!, f); setPickerCol(null); }}
                >
                  <Text style={styles.fieldOptionTxt}>{FIELD_LABEL[f] || f}</Text>
                  <Text style={styles.fieldOptionSub}>{f}</Text>
                </TouchableOpacity>
              ))}
              {/* Phase F2.1 — per-user Custom Fields appear as their own
                  group at the bottom of the picker so the import
                  pipeline matches whatever the Add-Shipment form
                  exposes. Mapping value is stored as "custom:<id>". */}
              {(preview?.custom_fields || []).length > 0 ? (
                <>
                  <View style={styles.customGroupHeader}>
                    <PhIcon name="star" size={12} color="#7C3AED" />
                    <Text style={styles.customGroupHeaderTxt}>
                      Your Custom Fields
                    </Text>
                  </View>
                  {(preview?.custom_fields || []).map((cf: any) => {
                    const v = `custom:${cf.id}`;
                    return (
                      <TouchableOpacity
                        key={v}
                        style={styles.fieldOption}
                        onPress={() => { setColMapping(pickerCol!, v); setPickerCol(null); }}
                      >
                        <Text style={[styles.fieldOptionTxt, { color: "#7C3AED" }]}>
                          ★ {cf.label || cf.id}
                        </Text>
                        <Text style={styles.fieldOptionSub}>{v}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </>
              ) : null}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe:    { flex: 1, backgroundColor: "#F4F5F7" },
  header:  { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 16, paddingVertical: 12 },
  title:   { fontSize: 17, fontWeight: "700", color: "#0F172A" },
  body:    { padding: 16, paddingBottom: 40, gap: 12 },
  stepCard: { backgroundColor: "#fff", borderRadius: 14, padding: 16, borderWidth: 1, borderColor: "#E5E7EB" },
  stepLabel: { fontSize: 15, fontWeight: "700", color: "#0F172A", marginBottom: 4 },
  helpText: { fontSize: 12, color: "#64748B", marginBottom: 12 },
  pickBtn:  { flexDirection: "row", alignItems: "center", gap: 8, justifyContent: "center", backgroundColor: colors.primary, padding: 12, borderRadius: 10 },
  pickBtnTxt: { color: "#fff", fontWeight: "600", fontSize: 14 },
  fileName: { marginTop: 10, fontSize: 13, color: "#0F172A" },
  colRow:   { flexDirection: "row", alignItems: "center", paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  colHeader: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  colSample: { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  fieldPill: { flexDirection: "row", alignItems: "center", gap: 4, backgroundColor: "#F1F5F9", paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999 },
  fieldPillTxt: { fontSize: 12, fontWeight: "600", color: "#0F172A" },
  toggleRow: { flexDirection: "row", alignItems: "center", marginTop: 16, paddingTop: 16, borderTopWidth: 1, borderTopColor: "#F1F5F9" },
  toggleTitle: { fontSize: 13, fontWeight: "600", color: "#0F172A" },
  toggleSub: { fontSize: 11, color: "#64748B", marginTop: 2, paddingRight: 12 },
  commitBtn: { flex: 1, flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 8, backgroundColor: colors.primary, padding: 14, borderRadius: 10 },
  commitBtnTxt: { color: "#fff", fontWeight: "700", fontSize: 14 },
  modalScrim: { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  modalSheet: { backgroundColor: "#fff", padding: 16, borderTopLeftRadius: 18, borderTopRightRadius: 18, paddingBottom: 32 },
  modalTitle: { fontSize: 15, fontWeight: "700", color: "#0F172A", marginBottom: 12 },
  fieldOption: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  fieldOptionTxt: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  fieldOptionSub: { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  customGroupHeader: { flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 14, paddingBottom: 6, marginTop: 4, borderTopWidth: 1, borderTopColor: "#E5E7EB" },
  customGroupHeaderTxt: { fontSize: 11, fontWeight: "700", color: "#7C3AED", textTransform: "uppercase", letterSpacing: 0.5 },
});
