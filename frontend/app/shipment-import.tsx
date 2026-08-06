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
  ActivityIndicator, Switch, Modal, Pressable, Platform, TextInput,
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

type PickedFile = { uri: string; name: string; mime: string; webFile?: any };

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

  // Layout controls — Header row / Data-start row / Data-start column.
  // Loaded from saved settings on type change; re-preview fires when any
  // of these change AND a file is already picked.
  const [headerRow, setHeaderRow]         = useState<number>(1);
  const [dataStartRow, setDataStartRow]   = useState<number>(2);
  const [headerCol, setHeaderCol]         = useState<number>(1);
  const [showLayoutHint, setShowLayoutHint] = useState<boolean>(false);

  // Phase F6.3 — Payment Batch metadata (only for cod_payment imports).
  // When any field is filled, it's sent to the backend which creates a
  // PaymentBatch record + stamps payment_batch_id on every settled
  // shipment. Reference # gets a duplicate-detection warning.
  const [pbName, setPbName]                 = useState<string>("");
  const [pbDescription, setPbDescription]   = useState<string>("");
  const [pbPaymentDate, setPbPaymentDate]   = useState<string>("");
  // Phase F9.1 — Native calendar picker for Payment Date. We store
  // the date internally as YYYY-MM-DD (canonical ISO — same format
  // the backend has always accepted) but render DD-MM-YYYY on screen
  // so operators read it the way they write cheques in India.
  const [showDatePicker, setShowDatePicker] = useState(false);
  const _dpModule = (() => {
    if (Platform.OS === "web") return null;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      return require("@react-native-community/datetimepicker").default;
    } catch { return null; }
  })();
  const [pbPaymentMode, setPbPaymentMode]   = useState<string>("cheque");
  const [pbReferenceNumber, setPbReferenceNumber] = useState<string>("");
  const [pbBankName, setPbBankName]         = useState<string>("");
  const [pbNotes, setPbNotes]               = useState<string>("");
  const [pbDupWarning, setPbDupWarning]     = useState<{ name?: string; payment_date?: string } | null>(null);
  const [pbOverrideDuplicate, setPbOverrideDuplicate] = useState<boolean>(false);

  // Load saved mapping + layout when import type is picked.
  useEffect(() => {
    if (!importType) return;
    let cancelled = false;
    Api.getShipmentImportMapping(importType)
      .then((r) => {
        if (cancelled) return;
        if (r.mapping && Object.keys(r.mapping).length) {
          setMapping(r.mapping);
        }
        if (r.header_row) setHeaderRow(r.header_row);
        if (r.data_start_row) setDataStartRow(r.data_start_row);
        if (r.header_col) setHeaderCol(r.header_col);
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

  const runPreview = useCallback(
    async (file: PickedFile, layoutOverride?: { header_row: number; data_start_row: number; header_col: number }) => {
      if (!importType) return;
      setLoading(true);
      try {
        const layout = layoutOverride ?? {
          header_row: headerRow,
          data_start_row: dataStartRow,
          header_col: headerCol,
        };
        const p = await Api.shipmentImportPreview(
          file.uri, file.name, file.mime, importType, layout, file.webFile,
        );
        setPreview(p);
        // Merge SUGGESTED with any saved mapping — saved wins.
        setMapping((prev) => {
          const merged: Record<string, string> = { ...(p.suggested || {}) };
          for (const [k, v] of Object.entries(prev)) {
            if (v && p.columns.includes(k)) merged[k] = v;
          }
          return merged;
        });
      } catch (e: any) {
        Alert.alert(
          "Could not read file",
          e?.response?.data?.detail || e?.message || "Unknown",
        );
        setPreview(null);
      } finally {
        setLoading(false);
      }
    },
    [importType, headerRow, dataStartRow, headerCol],
  );

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
        // On Web, expo-document-picker attaches the real File object at
        // `assets[0].file`. Preserve it so axios FormData can send a
        // proper multipart body instead of stringifying `{uri,name,type}`.
        webFile: (a as any).file,
      };
      setPicked(file);
      await runPreview(file);
    } catch (e: any) {
      Alert.alert("Picker error", e?.message || "Could not open file picker");
    }
  }, [importType, runPreview]);

  const applyLayout = useCallback(
    (patch: Partial<{ header_row: number; data_start_row: number; header_col: number }>) => {
      const nextHR = patch.header_row     ?? headerRow;
      const nextDR = patch.data_start_row ?? dataStartRow;
      const nextHC = patch.header_col     ?? headerCol;
      if (patch.header_row     !== undefined) setHeaderRow(nextHR);
      if (patch.data_start_row !== undefined) setDataStartRow(nextDR);
      if (patch.header_col     !== undefined) setHeaderCol(nextHC);
      if (picked) {
        runPreview(picked, { header_row: nextHR, data_start_row: nextDR, header_col: nextHC });
      }
    },
    [picked, headerRow, dataStartRow, headerCol, runPreview],
  );

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

  // Phase F6.3 — Duplicate-reference check with 500ms debounce. Fires
  // whenever the user edits the reference number. Non-blocking — just
  // renders a warning banner they can override at commit time.
  useEffect(() => {
    if (importType !== "cod_payment" || !pbReferenceNumber.trim()) {
      setPbDupWarning(null);
      return;
    }
    const ref = pbReferenceNumber.trim();
    const t = setTimeout(() => {
      Api.checkPaymentBatchDuplicate(ref)
        .then((r) => setPbDupWarning(r.duplicate ? r.batch : null))
        .catch(() => setPbDupWarning(null));
    }, 500);
    return () => clearTimeout(t);
  }, [pbReferenceNumber, importType]);

  const commit = async () => {
    if (!importType || !picked || !preview) return;
    // Phase F9.1 — Batch validation. Before firing the commit we make
    // sure both Batch Name and Payment Date are filled whenever the
    // user has started typing ANY payment-batch field. Silent skip
    // (no batch at all) is still allowed if all fields are blank —
    // this preserves the "plain COD update" path.
    if (
      importType === "cod_payment" &&
      (pbName.trim() || pbReferenceNumber.trim() || pbPaymentDate.trim()
       || pbBankName.trim() || pbDescription.trim() || pbNotes.trim())
    ) {
      const missing: string[] = [];
      if (!pbName.trim())         missing.push("Batch Name");
      if (!pbPaymentDate.trim())  missing.push("Payment Date");
      if (missing.length) {
        Alert.alert(
          "Missing required fields",
          `Please provide: ${missing.join(", ")}.`,
        );
        return;
      }
    }
    setConfirmOpen(false);
    setCommitting(true);
    try {
      // Assemble the payment_batch payload if the user filled any of it
      // in AND we're doing a cod_payment import. If required fields are
      // empty we skip sending it altogether (server treats it as
      // "no batch" and just does the plain COD update).
      let paymentBatchJson: string | undefined;
      if (
        importType === "cod_payment" &&
        (pbName.trim() || pbReferenceNumber.trim() || pbPaymentDate.trim())
      ) {
        paymentBatchJson = JSON.stringify({
          name:             pbName.trim(),
          description:      pbDescription.trim(),
          payment_date:     pbPaymentDate.trim(),
          payment_mode:     pbPaymentMode,
          reference_number: pbReferenceNumber.trim(),
          bank_name:        pbBankName.trim(),
          notes:            pbNotes.trim(),
        });
      }
      const r = await Api.shipmentImportCommit(
        picked.uri, picked.name, picked.mime, importType, mapping, saveDefault,
        { header_row: headerRow, data_start_row: dataStartRow, header_col: headerCol },
        picked.webFile,
        paymentBatchJson,
        pbOverrideDuplicate,
      );
      setResultOpen(r);
    } catch (e: any) {
      // Surface the backend's 409 duplicate warning nicely so the user
      // can choose to override or edit the reference number.
      const detail = e?.response?.data?.detail;
      if (detail && typeof detail === "object" && detail.error === "duplicate_reference") {
        Alert.alert(
          "Duplicate Reference",
          detail.message + "\n\nTap \"Override\" to import anyway.",
          [
            { text: "Cancel", style: "cancel" },
            {
              text: "Override",
              style: "destructive",
              onPress: async () => {
                setPbOverrideDuplicate(true);
                // Re-invoke commit with override flag on. Setting state
                // and immediately calling won't propagate — pass the
                // flag directly through a small closure instead.
                setCommitting(true);
                try {
                  const rr = await Api.shipmentImportCommit(
                    picked.uri, picked.name, picked.mime, importType, mapping, saveDefault,
                    { header_row: headerRow, data_start_row: dataStartRow, header_col: headerCol },
                    picked.webFile,
                    JSON.stringify({
                      name:             pbName.trim(),
                      description:      pbDescription.trim(),
                      payment_date:     pbPaymentDate.trim(),
                      payment_mode:     pbPaymentMode,
                      reference_number: pbReferenceNumber.trim(),
                      bank_name:        pbBankName.trim(),
                      notes:            pbNotes.trim(),
                    }),
                    true,
                  );
                  setResultOpen(rr);
                } catch (ee: any) {
                  Alert.alert("Import failed", ee?.response?.data?.detail || ee?.message || "Unknown");
                } finally {
                  setCommitting(false);
                }
              },
            },
          ],
        );
      } else {
        Alert.alert("Import failed", detail || e?.message || "Unknown error");
      }
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

        {/* Step 2 — Layout (header + data start) — only after a file is picked */}
        {picked ? (
          <View style={styles.stepCard}>
            <View style={{ flexDirection: "row", alignItems: "center" }}>
              <Text style={[styles.stepLabel, { flex: 1 }]}>
                2.  Header &amp; Data layout
              </Text>
              <TouchableOpacity onPress={() => setShowLayoutHint((v) => !v)} hitSlop={8}>
                <PhIcon name="information-circle-outline" size={18} color="#64748B" />
              </TouchableOpacity>
            </View>
            <Text style={styles.helpText}>
              Tell us which row has the column names and where the actual data begins.
              {" "}Useful when your file has title/blank rows on top.
            </Text>

            <View style={styles.layoutRow}>
              <LayoutStepper
                label="Header Row"
                value={headerRow}
                min={1}
                max={Math.max(1, preview?.raw_total_rows ?? 20)}
                onChange={(v) => applyLayout({ header_row: v })}
              />
              <LayoutStepper
                label="Data Start Row"
                value={dataStartRow}
                min={headerRow + 1}
                max={Math.max(headerRow + 1, preview?.raw_total_rows ?? 20)}
                onChange={(v) => applyLayout({ data_start_row: v })}
              />
              <LayoutStepper
                label="Data Start Col"
                value={headerCol}
                min={1}
                max={20}
                onChange={(v) => applyLayout({ header_col: v })}
              />
            </View>

            {/* Peek of first 8 rows (raw) so user can spot the correct header row */}
            {preview?.raw_preview && preview.raw_preview.length > 0 ? (
              <View style={{ marginTop: 12 }}>
                <Text style={styles.peekTitle}>File preview (first {preview.raw_preview.length} rows)</Text>
                <ScrollView horizontal showsHorizontalScrollIndicator={true} style={styles.peekWrap}>
                  <View>
                    {preview.raw_preview.map((row, rIdx) => {
                      const oneBased = rIdx + 1;
                      const isHeader = oneBased === headerRow;
                      const isData = oneBased >= dataStartRow;
                      const isSkip = oneBased < headerRow || (oneBased > headerRow && oneBased < dataStartRow);
                      return (
                        <View
                          key={rIdx}
                          style={[
                            styles.peekRow,
                            isHeader && styles.peekRowHeader,
                            isData && styles.peekRowData,
                            isSkip && styles.peekRowSkip,
                          ]}
                        >
                          <TouchableOpacity
                            onPress={() => applyLayout({ header_row: oneBased, data_start_row: oneBased + 1 })}
                            style={styles.peekRowNum}
                          >
                            <Text style={styles.peekRowNumTxt}>{oneBased}</Text>
                          </TouchableOpacity>
                          {row.slice(0, 10).map((cell, cIdx) => {
                            const colOneBased = cIdx + 1;
                            const beforeStart = colOneBased < headerCol;
                            return (
                              <View
                                key={cIdx}
                                style={[
                                  styles.peekCell,
                                  beforeStart && styles.peekCellSkip,
                                ]}
                              >
                                <Text style={styles.peekCellTxt} numberOfLines={1}>
                                  {cell || " "}
                                </Text>
                              </View>
                            );
                          })}
                        </View>
                      );
                    })}
                  </View>
                </ScrollView>
                <Text style={styles.peekLegend}>
                  <Text style={{ color: "#2563EB", fontWeight: "700" }}>Blue</Text> = header ·{" "}
                  <Text style={{ color: "#059669", fontWeight: "700" }}>Green</Text> = data ·{" "}
                  <Text style={{ color: "#94A3B8", fontWeight: "700" }}>Grey</Text> = skipped · Tap a row number to set as header
                </Text>
              </View>
            ) : null}

            {showLayoutHint ? (
              <View style={styles.layoutHint}>
                <Text style={styles.layoutHintTxt}>
                  Example: A courier remit sheet often has a title on row 1, a blank
                  row 2, headers on row 3, and data from row 4. Set{" "}
                  <Text style={{ fontWeight: "700" }}>Header Row = 3</Text> and{" "}
                  <Text style={{ fontWeight: "700" }}>Data Start Row = 4</Text>.
                </Text>
              </View>
            ) : null}
          </View>
        ) : null}

        {/* Phase F6.3 — Payment Batch metadata (only for COD Payment) */}
        {importType === "cod_payment" && picked ? (
          <View style={styles.stepCard}>
            <Text style={styles.stepLabel}>3.  Payment Batch (optional)</Text>
            <Text style={styles.helpText}>
              Group this settlement under a Payment Batch so you can later filter
              &quot;which parcels were paid in this cheque / UTR?&quot;. Leave blank to skip.
            </Text>

            {pbDupWarning ? (
              <View style={styles.warnBanner}>
                <PhIcon name="warning" size={16} color="#B45309" />
                <Text style={styles.warnBannerTxt}>
                  Reference already used in batch{" "}
                  <Text style={{ fontWeight: "700" }}>{pbDupWarning.name}</Text>{" "}
                  ({pbDupWarning.payment_date}). Import will be blocked unless
                  you Override at commit.
                </Text>
              </View>
            ) : null}

            {/* Phase F9.1 — Batch Name is now REQUIRED. Red asterisk in
                the label signals the requirement; validation in
                commit() enforces it. */}
            <PbField label="Batch Name *" value={pbName} onChangeText={setPbName}
              placeholder="e.g. India Post Aug W2" />
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                {/* Phase F9.1 — Native calendar Date Picker for Payment
                    Date. Stored internally as YYYY-MM-DD (backend
                    canonical) but displayed as DD-MM-YYYY. Manual text
                    entry is intentionally removed to eliminate the
                    common "DD/MM vs MM/DD" ambiguity that caused
                    reconciliation drift in past batches. */}
                <Text style={styles.pbLabel}>Payment Date *</Text>
                <TouchableOpacity
                  onPress={() => setShowDatePicker(true)}
                  style={styles.pbDateBtn}
                  activeOpacity={0.75}
                >
                  <Text style={[
                    styles.pbDateTxt,
                    !pbPaymentDate && { color: "#94A3B8" },
                  ]}>
                    {pbPaymentDate
                      ? (() => {
                          const [y, m, d] = pbPaymentDate.split("-");
                          return `${d}-${m}-${y}`;
                        })()
                      : "Pick a date"}
                  </Text>
                  <PhIcon name="calendar-outline" size={16} color="#64748B" />
                </TouchableOpacity>
                {showDatePicker && _dpModule && (() => {
                  const DateTimePicker = _dpModule;
                  const parsed = pbPaymentDate
                    ? new Date(pbPaymentDate + "T00:00:00")
                    : new Date();
                  return (
                    <DateTimePicker
                      value={parsed}
                      mode="date"
                      display={Platform.OS === "ios" ? "inline" : "default"}
                      maximumDate={new Date()}
                      onChange={(_event: any, selected?: Date) => {
                        // On Android the picker auto-closes; on iOS we
                        // dismiss on any confirm/cancel.
                        setShowDatePicker(Platform.OS === "ios" ? false : false);
                        if (selected) {
                          const y = selected.getFullYear();
                          const m = String(selected.getMonth() + 1).padStart(2, "0");
                          const d = String(selected.getDate()).padStart(2, "0");
                          setPbPaymentDate(`${y}-${m}-${d}`);
                        }
                      }}
                    />
                  );
                })()}
              </View>
              <View style={{ flex: 1 }}>
                <Text style={styles.pbLabel}>Payment Mode</Text>
                <View style={styles.pbModeRow}>
                  {["cheque", "neft", "upi", "bank_transfer", "other"].map((m) => (
                    <TouchableOpacity
                      key={m}
                      style={[
                        styles.pbModeChip,
                        pbPaymentMode === m && styles.pbModeChipActive,
                      ]}
                      onPress={() => setPbPaymentMode(m)}
                    >
                      <Text style={[
                        styles.pbModeChipTxt,
                        pbPaymentMode === m && { color: "#fff" },
                      ]}>{m.replace("_", " ").toUpperCase()}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            </View>
            <PbField
              label="Reference Number (Cheque # / UTR / Txn ID)"
              value={pbReferenceNumber}
              onChangeText={(v) => { setPbReferenceNumber(v); setPbOverrideDuplicate(false); }}
              placeholder="e.g. CHK-2345678"
            />
            <View style={{ flexDirection: "row", gap: 10 }}>
              <View style={{ flex: 1 }}>
                <PbField label="Bank Name (optional)" value={pbBankName} onChangeText={setPbBankName}
                  placeholder="e.g. SBI" />
              </View>
              <View style={{ flex: 1 }}>
                <PbField label="Description" value={pbDescription} onChangeText={setPbDescription}
                  placeholder="Optional" />
              </View>
            </View>
            <PbField label="Notes (optional)" value={pbNotes} onChangeText={setPbNotes}
              placeholder="Extra notes" multiline />
          </View>
        ) : null}

        {/* Step — column mapping */}
        {preview ? (
          <View style={styles.stepCard}>
            <Text style={styles.stepLabel}>
              {importType === "cod_payment" ? "4" : "3"}.  Map columns ({mappedCount}/{preview.columns.length})
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
            {/* Phase F7.6 — Delivery-import-only "Booking Date" tally.
                Server sends these counters on every import commit but
                they're always 0 for booking / cod_payment imports, so
                we only render the section when at least one row was
                touched by the booking_date rule. */}
            {(() => {
              const bu = Number(resultOpen?.booking_date_updated || 0);
              const bs = Number(resultOpen?.booking_date_skipped || 0);
              const bi = Number(resultOpen?.booking_date_invalid || 0);
              if (bu + bs + bi === 0) return null;
              return (
                <>
                  <View style={styles.resultDivider}>
                    <Text style={styles.resultDividerTxt}>Booking Date</Text>
                  </View>
                  <View style={styles.resultRow}>
                    <ResultStat n={bu} l="Updated"                tint="#10B981" />
                    <ResultStat n={bs} l="Skipped (Already)"      tint="#64748B" />
                    <ResultStat n={bi} l="Invalid Date"           tint="#DC2626" />
                  </View>
                </>
              );
            })()}
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

/** Compact labelled text input used across the Payment Batch section. */
function PbField({
  label, value, onChangeText, placeholder, multiline,
}: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  return (
    <View style={{ marginTop: 8 }}>
      <Text style={styles.pbLabel}>{label}</Text>
      <TextInput
        style={[styles.pbInput, multiline && { height: 60, textAlignVertical: "top" }]}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor="#94A3B8"
        multiline={!!multiline}
      />
    </View>
  );
}

/** Small +/- stepper used for Header Row / Data Start Row / Data Start Column.
 *  Keeps input touch-friendly (44pt targets) and clamps to [min,max]. */
function LayoutStepper({
  label, value, min, max, onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  const dec = () => onChange(Math.max(min, value - 1));
  const inc = () => onChange(Math.min(max, value + 1));
  return (
    <View style={styles.stepperWrap}>
      <Text style={styles.stepperLbl}>{label}</Text>
      <View style={styles.stepperRow}>
        <TouchableOpacity
          testID={`stepper-${label.replace(/\s/g, "-").toLowerCase()}-dec`}
          onPress={dec}
          style={[styles.stepperBtn, value <= min && { opacity: 0.4 }]}
          disabled={value <= min}
        >
          <PhIcon name="remove" size={16} color="#0F172A" />
        </TouchableOpacity>
        <View style={styles.stepperVal}>
          <Text style={styles.stepperValTxt}>{value}</Text>
        </View>
        <TouchableOpacity
          testID={`stepper-${label.replace(/\s/g, "-").toLowerCase()}-inc`}
          onPress={inc}
          style={[styles.stepperBtn, value >= max && { opacity: 0.4 }]}
          disabled={value >= max}
        >
          <PhIcon name="add" size={16} color="#0F172A" />
        </TouchableOpacity>
      </View>
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
  // Phase F7.6 — Booking Date section divider inside the summary sheet.
  resultDivider: {
    marginTop: 10, marginBottom: 2, paddingTop: 6,
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  resultDividerTxt: {
    fontSize: 11, fontWeight: "800", color: "#64748B",
    letterSpacing: 0.6, textTransform: "uppercase",
    paddingHorizontal: 4,
  },
  viewBatchBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    marginTop: 10, padding: 10, borderRadius: 8, backgroundColor: "#EFF6FF",
  },
  viewBatchTxt: { color: colors.primary, fontWeight: "600", fontSize: 13 },

  // ── Layout stepper + file peek ──
  layoutRow: {
    flexDirection: "row", gap: 8, marginTop: 4,
  },
  stepperWrap: { flex: 1 },
  stepperLbl: { fontSize: 10, fontWeight: "700", color: "#64748B", letterSpacing: 0.3, marginBottom: 4, textAlign: "center" },
  stepperRow: {
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    backgroundColor: "#F1F5F9", borderRadius: 8, overflow: "hidden",
  },
  stepperBtn: {
    width: 32, height: 34, alignItems: "center", justifyContent: "center",
  },
  stepperVal: {
    flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: "#fff", height: 34,
  },
  stepperValTxt: { fontSize: 15, fontWeight: "700", color: "#0F172A" },

  peekTitle: { fontSize: 11, fontWeight: "700", color: "#64748B", letterSpacing: 0.3, marginBottom: 6 },
  peekWrap: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    backgroundColor: "#FAFAFA", maxHeight: 260,
  },
  peekRow: { flexDirection: "row", alignItems: "stretch", borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  peekRowHeader: { backgroundColor: "#DBEAFE" },
  peekRowData:   { backgroundColor: "#DCFCE7" },
  peekRowSkip:   { backgroundColor: "#F1F5F9", opacity: 0.55 },
  peekRowNum:    {
    width: 30, alignItems: "center", justifyContent: "center",
    borderRightWidth: 1, borderRightColor: "#E5E7EB", backgroundColor: "rgba(15,23,42,0.05)",
  },
  peekRowNumTxt: { fontSize: 10, fontWeight: "800", color: "#0F172A" },
  peekCell: {
    width: 110, paddingHorizontal: 6, paddingVertical: 5,
    borderRightWidth: 1, borderRightColor: "#F1F5F9",
  },
  peekCellSkip: { backgroundColor: "rgba(148,163,184,0.15)" },
  peekCellTxt:  { fontSize: 11, color: "#0F172A" },
  peekLegend:   { fontSize: 10, color: "#64748B", marginTop: 6, lineHeight: 14 },

  layoutHint: {
    marginTop: 10, padding: 8, backgroundColor: "#EFF6FF", borderRadius: 6,
  },
  layoutHintTxt: { fontSize: 11, color: "#1E40AF", lineHeight: 15 },

  // Phase F6.3 — Payment Batch inputs
  pbLabel: { fontSize: 10, fontWeight: "800", color: "#64748B", letterSpacing: 0.3, marginBottom: 4 },
  // Phase F9.1 — clickable pill that opens the native calendar picker
  // for Payment Date. Styled to visually match the neighbouring
  // <PbField> so the row keeps a consistent rhythm.
  pbDateBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderWidth: 1,
    borderColor: "#CBD5E1",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: "#F8FAFC",
    minHeight: 40,
  },
  pbDateTxt: { fontSize: 13, color: "#0F172A", fontWeight: "600" },
  pbInput: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 10, paddingVertical: 8, fontSize: 13, color: "#0F172A",
    backgroundColor: "#fff", minHeight: 38,
  },
  pbModeRow: { flexDirection: "row", flexWrap: "wrap", gap: 4, marginTop: 2 },
  pbModeChip: {
    paddingHorizontal: 8, paddingVertical: 6, borderRadius: 6,
    backgroundColor: "#F1F5F9", borderWidth: 1, borderColor: "#E5E7EB",
  },
  pbModeChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  pbModeChipTxt: { fontSize: 10, fontWeight: "700", color: "#334155" },
});
