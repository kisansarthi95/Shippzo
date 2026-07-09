/**
 * /shipment-import — Phase F6.0 Shipment Import System.
 *
 * Bulk update EXISTING shipments via CSV/XLSX. Match key: tracking_id
 * (also falls back to manual_tracking_id / order_id / master_order_id).
 * Three modes:
 *   • Booking     — update courier / customer / items / address etc.
 *                   cross-verify weight, payment, COD amount.
 *   • Delivery    — mark parcels Delivered + delivered_at + POD.
 *   • COD Payment — record COD amount collected + remittance date + payer.
 *
 * User Requirement: "cross-verify — if not matching, download and keep
 * both data saved." → we NEVER override existing weight/payment_mode/amount
 * on mismatch. Instead, we log both values in the batch record so the
 * merchant can download the mismatch CSV and reconcile manually.
 *
 * Flow: type-picker → file-picker → server preview → mapping matrix →
 *       pre-import summary → commit → post-import summary modal.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  View, Text, TouchableOpacity, ScrollView, StyleSheet, Alert,
  ActivityIndicator, Switch, Modal, Pressable, Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import * as DocumentPicker from "expo-document-picker";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";
import PhIcon from "../components/PhIcon";

type ImportType = "booking" | "delivery" | "cod_payment";

type TargetField = { key: string; label: string; required: boolean };

type Preview = Awaited<ReturnType<typeof Api.shipmentImportPreview>>;
type Commit  = Awaited<ReturnType<typeof Api.shipmentImportCommit>>;

type PickedFile = { uri: string; name: string; mime: string };

const IMPORT_TYPE_META: Record<
  ImportType,
  { title: string; subtitle: string; icon: any; tint: string }
> = {
  booking: {
    title: "Booking Update",
    subtitle: "Sync courier/customer/items/address for existing shipments.",
    icon: "cube-outline",
    tint: "#2563EB",
  },
  delivery: {
    title: "Delivery Update",
    subtitle: "Mark parcels Delivered + delivered date + POD reference.",
    icon: "checkmark-done-outline",
    tint: "#10B981",
  },
  cod_payment: {
    title: "COD Payment",
    subtitle: "Record COD collected + remittance date + payer name.",
    icon: "cash-outline",
    tint: "#F59E0B",
  },
};

export default function ShipmentImportScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ import_type?: string }>();
  const initialType = (params.import_type as ImportType) || null;

  const [importType, setImportType]   = useState<ImportType | null>(initialType);
  const [picked, setPicked]           = useState<PickedFile | null>(null);
  const [preview, setPreview]         = useState<Preview | null>(null);
  const [mapping, setMapping]         = useState<Record<string, string>>({});
  const [saveDefault, setSaveDefault] = useState(false);
  const [loading, setLoading]         = useState(false);
  const [committing, setCommitting]   = useState(false);
  const [pickerCol, setPickerCol]     = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [resultOpen, setResultOpen]   = useState<Commit | null>(null);

  // Load saved mapping when import type is picked.
  useEffect(() => {
    if (!importType) return;
    let cancelled = false;
    Api.getShipmentImportMapping(importType)
      .then((r) => {
        if (cancelled) return;
        if (r.mapping && Object.keys(r.mapping).length) {
          setMapping(r.mapping);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [importType]);

  const targetFields: TargetField[] = useMemo(
    () => preview?.target_fields || [],
    [preview],
  );
  const crossVerifySet = useMemo(
    () => new Set(preview?.cross_verify || []),
    [preview],
  );
  const targetByKey = useMemo(() => {
    const m: Record<string, TargetField> = {};
    for (const t of targetFields) m[t.key] = t;
    return m;
  }, [targetFields]);

  const pickFile = useCallback(async () => {
    if (!importType) return;
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
        const p = await Api.shipmentImportPreview(file.uri, file.name, file.mime, importType);
        setPreview(p);
        // Merge SUGGESTED with any saved mapping — saved wins.
        setMapping((prev) => {
          const merged: Record<string, string> = { ...(p.suggested || {}) };
          for (const [k, v] of Object.entries(prev)) {
            if (v) merged[k] = v;
          }
          return merged;
        });
      } catch (e: any) {
        Alert.alert(
          "Could not read file",
          e?.response?.data?.detail || e?.message || "Unknown",
        );
        setPicked(null);
        setPreview(null);
      } finally {
        setLoading(false);
      }
    } catch (e: any) {
      Alert.alert("Picker error", e?.message || "Could not open file picker");
    }
  }, [importType]);

  const setColMapping = (col: string, field: string) => {
    setMapping((m) => {
      const next = { ...m };
      if (!field) delete next[col];
      else next[col] = field;
      return next;
    });
  };

  const hasTrackingMapped = useMemo(
    () => Object.values(mapping).includes("tracking_id"),
    [mapping],
  );

  const mappedCount = useMemo(
    () => Object.values(mapping).filter(Boolean).length,
    [mapping],
  );

  // Pre-import summary counts — surfaced in the confirmation modal.
  const preSummary = useMemo(() => {
    if (!preview) return null;
    return {
      total:   preview.total_rows,
      matched: preview.rows_with_tracking,
    };
  }, [preview]);

  const openConfirm = () => {
    if (!importType || !picked || !preview) return;
    if (!hasTrackingMapped) {
      Alert.alert(
        "Tracking Number required",
        "Map at least one column to Tracking Number before importing.",
      );
      return;
    }
    setConfirmOpen(true);
  };

  const commit = async () => {
    if (!importType || !picked || !preview) return;
    setConfirmOpen(false);
    setCommitting(true);
    try {
      const r = await Api.shipmentImportCommit(
        picked.uri, picked.name, picked.mime, importType, mapping, saveDefault,
      );
      setResultOpen(r);
    } catch (e: any) {
      Alert.alert("Import failed", e?.response?.data?.detail || e?.message || "Unknown error");
    } finally {
      setCommitting(false);
    }
  };

  const resetToPicker = () => {
    setResultOpen(null);
    setPicked(null);
    setPreview(null);
    setMapping({});
  };

  // ─── Type Picker Screen ───────────────────────────
  if (!importType) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <Stack.Screen options={{ headerShown: false }} />
        <View style={styles.header}>
          <TouchableOpacity onPress={() => router.back()} hitSlop={8}>
            <PhIcon name="arrow-back" size={22} color={colors.text} />
          </TouchableOpacity>
          <Text style={styles.title}>Upload Shipment Data</Text>
          <TouchableOpacity
            onPress={() => router.push("/shipment-import-history" as any)}
            hitSlop={8}
          >
            <PhIcon name="time-outline" size={22} color={colors.text} />
          </TouchableOpacity>
        </View>
        <ScrollView contentContainerStyle={styles.body}>
          <View style={styles.stepCard}>
            <Text style={styles.stepLabel}>Choose import type</Text>
            <Text style={styles.helpText}>
                Rows are matched by <Text style={{ fontWeight: "700" }}>Tracking Number</Text>.
              Weight / Payment Mode / COD Amount are cross-verified — mismatches are logged for
              your review and never overwrite existing values.
            </Text>
            {(Object.keys(IMPORT_TYPE_META) as ImportType[]).map((k) => {
              const meta = IMPORT_TYPE_META[k];
              return (
                <TouchableOpacity
                  key={k}
                  testID={`import-type-${k}`}
                  style={styles.typeCard}
                  onPress={() => setImportType(k)}
                >
                  <View style={[styles.typeIcon, { backgroundColor: meta.tint + "22" }]}>
                    <PhIcon name={meta.icon} size={22} color={meta.tint} />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.typeTitle}>{meta.title}</Text>
                    <Text style={styles.typeSub}>{meta.subtitle}</Text>
                  </View>
                  <PhIcon name="chevron-forward" size={18} color="#94A3B8" />
                </TouchableOpacity>
              );
            })}
          </View>

          <TouchableOpacity
            style={styles.historyLink}
            onPress={() => router.push("/shipment-import-history" as any)}
          >
            <PhIcon name="time-outline" size={16} color={colors.primary} />
            <Text style={styles.historyLinkTxt}>View Import History</Text>
          </TouchableOpacity>
        </ScrollView>
      </SafeAreaView>
    );
  }

  // ─── Mapping Screen ───────────────────────────────
  const meta = IMPORT_TYPE_META[importType];

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <Stack.Screen options={{ headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity
          onPress={() => {
            if (picked || preview) {
              setPicked(null); setPreview(null); setMapping({});
            }
            setImportType(null);
          }}
          hitSlop={8}
        >
          <PhIcon name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1, alignItems: "center" }}>
          <Text style={styles.title}>{meta.title}</Text>
          <View style={[styles.typeBadge, { backgroundColor: meta.tint + "18" }]}>
            <Text style={[styles.typeBadgeTxt, { color: meta.tint }]}>
              Match by Tracking Number
            </Text>
          </View>
        </View>
        <TouchableOpacity
          onPress={() => router.push("/shipment-import-history" as any)}
          hitSlop={8}
        >
          <PhIcon name="time-outline" size={22} color={colors.text} />
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={styles.body}
        keyboardShouldPersistTaps="handled"
      >
        {/* Step 1 — pick file */}
        <View style={styles.stepCard}>
          <Text style={styles.stepLabel}>1.  Choose file</Text>
          <Text style={styles.helpText}>
            Supported: .csv, .xlsx · max 10 MB
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

        {/* Step 2 — column mapping */}
        {preview ? (
          <View style={styles.stepCard}>
            <Text style={styles.stepLabel}>
              2.  Map columns ({mappedCount}/{preview.columns.length})
            </Text>
            <Text style={styles.helpText}>
              Tap a column to pick the shipment field. Fields marked{" "}
              <Text style={{ color: "#DC2626", fontWeight: "700" }}>*</Text> are required.
              Fields marked{" "}
              <View style={styles.crossVerifyBadgeInline}>
                <PhIcon name="shield-checkmark" size={10} color="#B45309" />
                <Text style={styles.crossVerifyBadgeTxtInline}>CROSS-VERIFY</Text>
              </View>{" "}
              are validated — mismatches are logged without overriding existing values.
            </Text>

            {!hasTrackingMapped ? (
              <View style={styles.warnBanner}>
                <PhIcon name="warning" size={16} color="#B45309" />
                <Text style={styles.warnBannerTxt}>
                  Map a column to <Text style={{ fontWeight: "700" }}>Tracking Number</Text> to enable Import.
                </Text>
              </View>
            ) : null}

            {preview.columns.map((c) => {
              const cur = mapping[c] || "";
              const curMeta = cur ? targetByKey[cur] : null;
              const isCross = cur && crossVerifySet.has(cur);
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
                    {isCross ? (
                      <PhIcon name="shield-checkmark" size={11} color="#B45309" />
                    ) : null}
                    <Text style={[styles.fieldPillTxt, !cur && { color: "#94A3B8" }]}>
                      {curMeta ? curMeta.label : (cur || "Ignore")}
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
                  Next upload for {meta.title.toLowerCase()} auto-applies these choices.
                </Text>
              </View>
              <Switch
                value={saveDefault}
                onValueChange={setSaveDefault}
                trackColor={{ false: "#E5E7EB", true: colors.primary }}
              />
            </View>

            <TouchableOpacity
              style={[
                styles.commitBtn,
                (!hasTrackingMapped || committing) && { opacity: 0.5 },
              ]}
              onPress={openConfirm}
              disabled={!hasTrackingMapped || committing}
            >
              {committing ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <PhIcon name="checkmark" size={18} color="#fff" />
                  <Text style={styles.commitBtnTxt}>
                    Preview Import ({preview.total_rows} {preview.total_rows === 1 ? "row" : "rows"})
                  </Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        ) : null}
      </ScrollView>

      {/* Field picker sheet */}
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
            <ScrollView style={{ maxHeight: 480 }}>
              <TouchableOpacity
                style={styles.fieldOption}
                onPress={() => { setColMapping(pickerCol!, ""); setPickerCol(null); }}
              >
                <Text style={[styles.fieldOptionTxt, { color: "#94A3B8" }]}>
                  (Ignore this column)
                </Text>
              </TouchableOpacity>
              {targetFields.map((f) => {
                const isCross = crossVerifySet.has(f.key);
                return (
                  <TouchableOpacity
                    key={f.key}
                    style={styles.fieldOption}
                    onPress={() => { setColMapping(pickerCol!, f.key); setPickerCol(null); }}
                  >
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                      <Text style={styles.fieldOptionTxt}>
                        {f.label}
                        {f.required ? <Text style={{ color: "#DC2626" }}> *</Text> : null}
                      </Text>
                      {isCross ? (
                        <View style={styles.crossVerifyBadge}>
                          <PhIcon name="shield-checkmark" size={10} color="#B45309" />
                          <Text style={styles.crossVerifyBadgeTxt}>CROSS-VERIFY</Text>
                        </View>
                      ) : null}
                    </View>
                    <Text style={styles.fieldOptionSub}>{f.key}</Text>
                  </TouchableOpacity>
                );
              })}
            </ScrollView>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Pre-import confirm sheet */}
      <Modal
        visible={confirmOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setConfirmOpen(false)}
      >
        <Pressable style={styles.modalScrim} onPress={() => setConfirmOpen(false)}>
          <Pressable style={styles.confirmSheet}>
            <Text style={styles.confirmTitle}>Ready to Import?</Text>
            <Text style={styles.confirmSub}>
              {meta.title} · {preview?.filename}
            </Text>
            <View style={styles.confirmStatsRow}>
              <View style={styles.confirmStat}>
                <Text style={styles.confirmStatN}>{preSummary?.total ?? 0}</Text>
                <Text style={styles.confirmStatL}>Total rows</Text>
              </View>
              <View style={styles.confirmStat}>
                <Text style={[styles.confirmStatN, { color: "#10B981" }]}>
                  {preSummary?.matched ?? 0}
                </Text>
                <Text style={styles.confirmStatL}>Rows with Tracking</Text>
              </View>
              <View style={styles.confirmStat}>
                <Text style={[styles.confirmStatN, { color: "#B45309" }]}>
                  {(preSummary?.total ?? 0) - (preSummary?.matched ?? 0)}
                </Text>
                <Text style={styles.confirmStatL}>Missing Tracking</Text>
              </View>
            </View>
            <View style={styles.confirmNoteBox}>
              <PhIcon name="information-circle" size={14} color="#0369A1" />
              <Text style={styles.confirmNote}>
                Existing shipments will be UPDATED only if Tracking Number matches. Rows
                that don&apos;t match will be listed in the mismatch report — nothing gets
                deleted or created.
              </Text>
            </View>
            <View style={{ flexDirection: "row", gap: 8, marginTop: 8 }}>
              <TouchableOpacity
                style={[styles.confirmBtn, { backgroundColor: "#F1F5F9" }]}
                onPress={() => setConfirmOpen(false)}
              >
                <Text style={[styles.confirmBtnTxt, { color: "#0F172A" }]}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles.confirmBtn, { backgroundColor: colors.primary }]}
                onPress={commit}
              >
                <Text style={[styles.confirmBtnTxt, { color: "#fff" }]}>Confirm & Import</Text>
              </TouchableOpacity>
            </View>
          </Pressable>
        </Pressable>
      </Modal>

      {/* Post-import result sheet */}
      <Modal
        visible={!!resultOpen}
        transparent
        animationType="slide"
        onRequestClose={() => resetToPicker()}
      >
        <Pressable style={styles.modalScrim} onPress={() => {}}>
          <Pressable style={styles.confirmSheet}>
            <View style={{ alignItems: "center", marginBottom: 8 }}>
              <PhIcon name="checkmark-circle" size={40} color="#10B981" />
              <Text style={styles.confirmTitle}>Import Complete</Text>
              <Text style={styles.confirmSub}>{resultOpen?.filename}</Text>
            </View>
            <View style={styles.resultRow}>
              <ResultStat n={resultOpen?.matched_updated || 0} l="Updated" tint="#10B981" />
              <ResultStat n={resultOpen?.matched_mismatch || 0} l="Mismatch" tint="#B45309" />
              <ResultStat n={resultOpen?.unmatched || 0} l="Unmatched" tint="#DC2626" />
            </View>
            <View style={styles.resultRow}>
              <ResultStat n={resultOpen?.matched_no_change || 0} l="No Change" tint="#64748B" />
              <ResultStat n={resultOpen?.errors || 0} l="Errors" tint="#DC2626" />
              <ResultStat n={resultOpen?.total_rows || 0} l="Total" tint="#0F172A" />
            </View>
            <TouchableOpacity
              style={styles.viewBatchBtn}
              onPress={() => {
                const bid = resultOpen?.batch_id;
                resetToPicker();
                if (bid) router.push(`/shipment-import-batch?id=${bid}` as any);
              }}
            >
              <PhIcon name="document-text-outline" size={16} color={colors.primary} />
              <Text style={styles.viewBatchTxt}>View Batch Detail & Download Report</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.confirmBtn, { backgroundColor: colors.primary, marginTop: 6 }]}
              onPress={() => { resetToPicker(); router.back(); }}
            >
              <Text style={[styles.confirmBtnTxt, { color: "#fff" }]}>Done</Text>
            </TouchableOpacity>
          </Pressable>
        </Pressable>
      </Modal>
    </SafeAreaView>
  );
}

function ResultStat({ n, l, tint }: { n: number; l: string; tint: string }) {
  return (
    <View style={styles.confirmStat}>
      <Text style={[styles.confirmStatN, { color: tint }]}>{n}</Text>
      <Text style={styles.confirmStatL}>{l}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe:     { flex: 1, backgroundColor: "#F4F5F7" },
  header:   {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 16, paddingVertical: 12, gap: 12,
  },
  title:    { fontSize: 17, fontWeight: "700", color: "#0F172A" },
  typeBadge:{ marginTop: 4, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10 },
  typeBadgeTxt: { fontSize: 11, fontWeight: "700", letterSpacing: 0.4 },
  body:     { padding: 16, paddingBottom: 40, gap: 12 },
  stepCard: { backgroundColor: "#fff", borderRadius: 14, padding: 16, borderWidth: 1, borderColor: "#E5E7EB" },
  stepLabel:{ fontSize: 15, fontWeight: "700", color: "#0F172A", marginBottom: 4 },
  helpText: { fontSize: 12, color: "#64748B", marginBottom: 12, lineHeight: 17 },
  pickBtn:  {
    flexDirection: "row", alignItems: "center", gap: 8, justifyContent: "center",
    backgroundColor: colors.primary, padding: 12, borderRadius: 10,
  },
  pickBtnTxt:{ color: "#fff", fontWeight: "600", fontSize: 14 },
  fileName: { marginTop: 10, fontSize: 13, color: "#0F172A" },

  typeCard: {
    flexDirection: "row", alignItems: "center", gap: 12,
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9",
  },
  typeIcon: {
    width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center",
  },
  typeTitle:{ fontSize: 14, fontWeight: "700", color: "#0F172A" },
  typeSub:  { fontSize: 12, color: "#64748B", marginTop: 2 },

  historyLink: {
    flexDirection: "row", alignItems: "center", gap: 6, alignSelf: "center",
    padding: 12,
  },
  historyLinkTxt: { color: colors.primary, fontWeight: "600", fontSize: 13 },

  warnBanner: {
    flexDirection: "row", alignItems: "center", gap: 8,
    backgroundColor: "#FFFBEB", borderColor: "#FDE68A", borderWidth: 1,
    padding: 10, borderRadius: 8, marginBottom: 10,
  },
  warnBannerTxt: { flex: 1, fontSize: 12, color: "#92400E" },

  colRow:    { flexDirection: "row", alignItems: "center", paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  colHeader: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  colSample: { fontSize: 11, color: "#94A3B8", marginTop: 2 },
  fieldPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    backgroundColor: "#F1F5F9", paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 20, flexShrink: 0, maxWidth: 220,
  },
  fieldPillTxt: { fontSize: 12, fontWeight: "600", color: "#0F172A" },

  crossVerifyBadge: {
    flexDirection: "row", alignItems: "center", gap: 2,
    backgroundColor: "#FEF3C7", paddingHorizontal: 5, paddingVertical: 2, borderRadius: 4,
  },
  crossVerifyBadgeTxt: { fontSize: 8, fontWeight: "800", color: "#B45309", letterSpacing: 0.5 },
  crossVerifyBadgeInline: {
    flexDirection: "row", alignItems: "center", gap: 2,
    backgroundColor: "#FEF3C7", paddingHorizontal: 4, paddingVertical: 1, borderRadius: 3,
  },
  crossVerifyBadgeTxtInline: { fontSize: 8, fontWeight: "800", color: "#B45309", letterSpacing: 0.5 },

  toggleRow:   { flexDirection: "row", alignItems: "center", marginTop: 14, paddingTop: 12, borderTopWidth: 1, borderTopColor: "#F1F5F9" },
  toggleTitle: { fontSize: 13, fontWeight: "600", color: "#0F172A" },
  toggleSub:   { fontSize: 11, color: "#64748B", marginTop: 2, paddingRight: 12 },
  commitBtn:   {
    flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 8,
    backgroundColor: colors.primary, padding: 14, borderRadius: 10, marginTop: 14,
  },
  commitBtnTxt:{ color: "#fff", fontWeight: "700", fontSize: 14 },

  modalScrim:  { flex: 1, backgroundColor: "rgba(0,0,0,0.4)", justifyContent: "flex-end" },
  modalSheet:  {
    backgroundColor: "#fff", padding: 16,
    borderTopLeftRadius: 18, borderTopRightRadius: 18, paddingBottom: 32,
  },
  modalTitle:  { fontSize: 15, fontWeight: "700", color: "#0F172A", marginBottom: 12 },
  fieldOption: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  fieldOptionTxt: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  fieldOptionSub: { fontSize: 11, color: "#94A3B8", marginTop: 2 },

  confirmSheet: {
    backgroundColor: "#fff", padding: 20,
    borderTopLeftRadius: 20, borderTopRightRadius: 20,
    paddingBottom: Platform.OS === "ios" ? 36 : 24,
  },
  confirmTitle: { fontSize: 17, fontWeight: "700", color: "#0F172A", textAlign: "center" },
  confirmSub:   { fontSize: 12, color: "#64748B", textAlign: "center", marginBottom: 12 },
  confirmStatsRow: {
    flexDirection: "row", justifyContent: "space-around",
    backgroundColor: "#F8FAFC", borderRadius: 12, padding: 14, marginTop: 4,
  },
  confirmStat:  { flex: 1, alignItems: "center" },
  confirmStatN: { fontSize: 22, fontWeight: "800", color: "#0F172A" },
  confirmStatL: { fontSize: 11, color: "#64748B", marginTop: 2 },
  confirmNoteBox: {
    flexDirection: "row", alignItems: "flex-start", gap: 6,
    backgroundColor: "#ECFEFF", borderRadius: 8, padding: 10, marginTop: 12,
  },
  confirmNote:  { flex: 1, fontSize: 11, color: "#155E75", lineHeight: 16 },
  confirmBtn:   { flex: 1, padding: 14, borderRadius: 10, alignItems: "center" },
  confirmBtnTxt:{ fontWeight: "700", fontSize: 14 },

  resultRow: {
    flexDirection: "row", justifyContent: "space-around",
    backgroundColor: "#F8FAFC", borderRadius: 12, padding: 12, marginTop: 6,
  },
  viewBatchBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    marginTop: 10, padding: 10, borderRadius: 8, backgroundColor: "#EFF6FF",
  },
  viewBatchTxt: { color: colors.primary, fontWeight: "600", fontSize: 13 },
});
