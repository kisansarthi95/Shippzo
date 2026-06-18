/**
 * Admin → Master Sheet config (Phase-B).
 *
 * Lets the admin set the Google Sheet ID + Tab name for the GLOBAL
 * Master Sheet. Every user's saves get appended here (with user_id +
 * user_name columns) so the admin has a single cross-tenant view.
 *
 * NOTE: This is SEPARATE from a user's own per-user sheet (which is
 * configured in Settings → Business → "Google Sheet (Orders source)"
 * and stays private to that user).
 */
import React, { useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput,
  ActivityIndicator, Alert, Platform,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { SafeAreaView } from "react-native-safe-area-context";
import { useRouter } from "expo-router";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { colors } from "../../lib/theme";

type AdminCfg = {
  master_sheet_id: string;
  master_sheet_tab: string;
};

/**
 * Strip a Google Sheets URL down to its raw spreadsheet id.
 * Accepts both raw IDs and full URLs like
 *   https://docs.google.com/spreadsheets/d/<ID>/edit#gid=0
 */
function extractSheetId(input: string): string {
  const m = input.match(/\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/);
  return m ? m[1] : input.trim();
}

export default function AdminMasterSheetScreen() {
  const router = useRouter();
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [cfg, setCfg] = useState<AdminCfg>({ master_sheet_id: "", master_sheet_tab: "" });
  const [originalSnap, setOriginalSnap] = useState("");
  const [saEmail, setSaEmail] = useState<string>("");

  // Gate-keep: redirect non-admins.
  useEffect(() => {
    if (user && !(user as any).is_admin) {
      Alert.alert("Access denied", "Only the platform admin can edit the Master Sheet.");
      router.replace("/(tabs)/settings");
    }
  }, [user, router]);

  // Load admin config + service-account email.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [r1, r2] = await Promise.all([
          api.get<AdminCfg>("/admin/global-config"),
          api.get<{ email?: string }>("/sheets/service-account").catch(() => ({ data: {} } as any)),
        ]);
        if (cancelled) return;
        const next: AdminCfg = {
          master_sheet_id:  r1.data.master_sheet_id || "",
          master_sheet_tab: r1.data.master_sheet_tab || "Sheet1",
        };
        setCfg(next);
        setOriginalSnap(JSON.stringify(next));
        setSaEmail(String((r2 as any)?.data?.email || ""));
      } catch (e: any) {
        Alert.alert(
          "Could not load admin config",
          e?.response?.data?.detail || e?.message || "Please try again.",
        );
      } finally {
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const dirty = useMemo(() => JSON.stringify(cfg) !== originalSnap, [cfg, originalSnap]);

  const onSave = async () => {
    if (!cfg.master_sheet_id.trim()) {
      Alert.alert("Sheet ID required", "Please paste a Google Sheet URL or ID.");
      return;
    }
    setSaving(true);
    try {
      const id = extractSheetId(cfg.master_sheet_id);
      const tab = cfg.master_sheet_tab.trim() || "Sheet1";
      await api.put("/admin/global-config", {
        master_sheet_id: id,
        master_sheet_tab: tab,
      });
      const next = { master_sheet_id: id, master_sheet_tab: tab };
      setCfg(next);
      setOriginalSnap(JSON.stringify(next));
      Alert.alert("✅ Master Sheet saved", `Sheet ID: ${id}\nTab: ${tab}`);
    } catch (e: any) {
      Alert.alert(
        "Save failed",
        e?.response?.data?.detail || e?.message || "Please try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  const onTestConnection = async () => {
    setProbing(true);
    try {
      const r = await api.get<{
        ok: boolean;
        tab?: string;
        rows?: number;
      }>("/sheets/probe");
      if (r.data.ok) {
        Alert.alert(
          "✅ Connection OK",
          `Tab: ${r.data.tab || "—"}\nRows: ${r.data.rows ?? "—"}\n\n` +
          `The service account can read & write to your Master Sheet.`,
        );
      } else {
        Alert.alert("Probe returned not-ok", JSON.stringify(r.data));
      }
    } catch (e: any) {
      Alert.alert(
        "Connection failed",
        e?.response?.data?.detail || e?.message ||
        "Make sure the service account email has Editor access to the sheet.",
      );
    } finally {
      setProbing(false);
    }
  };

  // ──────────────────────────────────────────────────────────────────
  // Copy Master Sheet header row to clipboard.
  //
  // Phase-31 (rev-2): the Master Sheet schema grew from 19 to 35
  // columns to cover full shipment fidelity (courier_name, tracking_id,
  // box_dimensions, dispatched_at, custom_values, … 16 new fields).
  // The admin pastes the freshly-fetched row into A1 of the Master
  // Sheet so column ordering matches what `sheet_writer.py` writes.
  //
  // We fetch from /api/admin/master-sheet/header (returns the 35-cell
  // array generated from sheet_writer.COLUMNS — always reflects the
  // CURRENT schema), join with tabs for direct Excel/Sheets paste,
  // and dump to the clipboard. Idempotent: re-pasting the same row
  // is a no-op.
  // ──────────────────────────────────────────────────────────────────
  const [copyingHeaders, setCopyingHeaders] = useState(false);
  const onCopyHeaders = async () => {
    setCopyingHeaders(true);
    try {
      const r = await api.get<{
        headers: string[];
        count: number;
        last_column_letter: string;
        paste_range: string;
        note: string;
      }>("/admin/master-sheet/header");
      const rowTsv = (r.data.headers || []).join("\t");
      await Clipboard.setStringAsync(rowTsv);
      Alert.alert(
        `📋 ${r.data.count} headers copied`,
        `Paste into row 1 of your Master Sheet (${r.data.paste_range}).\n\n` +
        `Tip: in Google Sheets click cell A1 and press Ctrl/Cmd+V — the ` +
        `${r.data.count} columns will fill across in one shot.\n\n` +
        `Schema: 19 base columns + 16 Phase-31 shipment-fidelity columns ` +
        `(Courier Name → Custom Values).`,
      );
    } catch (e: any) {
      Alert.alert(
        "Copy failed",
        e?.response?.data?.detail || e?.message ||
        "Could not fetch the header row. Please try again.",
      );
    } finally {
      setCopyingHeaders(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.wrap}>
        <ActivityIndicator size="large" color="#7C3AED" style={{ marginTop: 60 }} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.wrap}>
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={10}>
          <PhIcon name="chevron-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title}>Master Sheet (Admin)</Text>
        <View style={{ width: 24 }} />
      </View>

      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        {/* Service-account share panel */}
        {saEmail ? (
          <View style={styles.saBox}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <PhIcon name="shield-checkmark" size={14} color="#1E40AF" />
              <Text style={styles.saTitle}>Service Account email</Text>
            </View>
            <Text style={styles.saSub}>
              Open your Master Sheet → Share → paste this email → pick "Editor" → Send.
            </Text>
            <View style={styles.saEmailRow}>
              <Text style={styles.saEmail} selectable numberOfLines={1}>{saEmail}</Text>
            </View>
          </View>
        ) : null}

        {/* Master Sheet ID */}
        <Text style={styles.label}>Master Sheet URL or ID</Text>
        <TextInput
          testID="admin-master-sheet-id"
          style={styles.input}
          value={cfg.master_sheet_id}
          onChangeText={(t) => setCfg((p) => ({ ...p, master_sheet_id: t }))}
          placeholder="Paste full URL OR raw spreadsheet ID"
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Text style={styles.hint}>
          Eg `https://docs.google.com/spreadsheets/d/1AbC…xyz/edit#gid=0`
          — we'll extract the ID automatically.
        </Text>

        {/* Tab name */}
        <Text style={[styles.label, { marginTop: 16 }]}>Tab / Worksheet name</Text>
        <TextInput
          testID="admin-master-sheet-tab"
          style={styles.input}
          value={cfg.master_sheet_tab}
          onChangeText={(t) => setCfg((p) => ({ ...p, master_sheet_tab: t }))}
          placeholder="Sheet1"
          placeholderTextColor="#9CA3AF"
          autoCapitalize="none"
          autoCorrect={false}
        />
        <Text style={styles.hint}>
          Default: "Sheet1". Use the exact tab name as it appears in Google Sheets
          (case-sensitive).
        </Text>

        {/* Action row */}
        <View style={styles.actionRow}>
          <TouchableOpacity
            testID="admin-master-sheet-test"
            onPress={onTestConnection}
            disabled={probing}
            style={[styles.btn, styles.btnGhost, probing && { opacity: 0.6 }]}
          >
            {probing ? (
              <ActivityIndicator size="small" color="#7C3AED" />
            ) : (
              <>
                <PhIcon name="checkmark-circle-outline" size={16} color="#7C3AED" />
                <Text style={styles.btnGhostTxt}>Test Connection</Text>
              </>
            )}
          </TouchableOpacity>

          <TouchableOpacity
            testID="admin-master-sheet-save"
            onPress={onSave}
            disabled={saving || !dirty}
            style={[styles.btn, styles.btnPrimary, (saving || !dirty) && { opacity: 0.5 }]}
          >
            {saving ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <PhIcon name="save-outline" size={16} color="#fff" />
                <Text style={styles.btnPrimaryTxt}>Save</Text>
              </>
            )}
          </TouchableOpacity>
        </View>

        {/* Phase-31 — Copy Master Sheet header row.
            Sits below the primary action row so the admin only sees
            it AFTER the sheet is configured + tested. Pasting the
            row is a one-time setup per sheet (or whenever the schema
            grows again). */}
        <TouchableOpacity
          testID="admin-master-sheet-copy-headers"
          onPress={onCopyHeaders}
          disabled={copyingHeaders}
          style={[styles.btnWide, copyingHeaders && { opacity: 0.6 }]}
          activeOpacity={0.8}
        >
          {copyingHeaders ? (
            <ActivityIndicator size="small" color="#0F766E" />
          ) : (
            <>
              <PhIcon name="copy-outline" size={16} color="#0F766E" />
              <Text style={styles.btnWideTxt}>
                Copy 35-Column Header Row
              </Text>
            </>
          )}
        </TouchableOpacity>
        <Text style={styles.hintCenter}>
          Tap to copy the canonical header row, then paste into{" "}
          <Text style={{ fontWeight: "700" }}>row 1</Text> of your Master Sheet.
        </Text>

        {/* Info card */}
        <View style={styles.infoCard}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <PhIcon name="information-circle" size={16} color="#1E3A8A" />
            <Text style={styles.infoTitle}>How dual-write works</Text>
          </View>
          <Text style={styles.infoBody}>
            • Every user's order writes to THIS Master Sheet AND their own personal sheet (Settings → Google Sheet).{"\n"}
            • Master Sheet now has 35 columns (Phase-31): the original 19 + 16 shipment-fidelity columns (Courier Name, Tracking ID, Box Dimensions, Dispatched At, etc.). Tap "Copy 35-Column Header Row" above to paste the canonical headers into row 1.{"\n"}
            • Per-user sheets only show that user's own rows (no cross-tenant leakage).{"\n"}
            • If a user hasn't linked their own sheet, only the Master Sheet receives writes — that's fine.
          </Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  title: { fontSize: 17, fontWeight: "800", color: colors.text },
  scroll: { padding: 16, paddingBottom: 40 },
  saBox: {
    backgroundColor: "#DBEAFE",
    borderColor: "#93C5FD",
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    marginBottom: 16,
    gap: 6,
  },
  saTitle: { fontSize: 13, fontWeight: "800", color: "#1E3A8A" },
  saSub: { fontSize: 12, color: "#1E3A8A" },
  saEmailRow: {
    backgroundColor: "#fff",
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    marginTop: 4,
  },
  saEmail: { fontSize: 12, fontWeight: "700", color: "#1E3A8A" },
  label: { fontSize: 13, fontWeight: "800", color: colors.text, marginBottom: 6 },
  input: {
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "ios" ? 12 : 8,
    backgroundColor: "#fff",
    color: colors.text,
    fontSize: 14,
  },
  hint: { fontSize: 11, color: "#6B7280", marginTop: 4, lineHeight: 15 },
  actionRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 24,
  },
  btn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    paddingVertical: 12,
    borderRadius: 10,
  },
  btnGhost: {
    backgroundColor: "#F5F3FF",
    borderWidth: 1,
    borderColor: "#DDD6FE",
  },
  btnGhostTxt: { color: "#7C3AED", fontWeight: "800", fontSize: 13 },
  btnPrimary: { backgroundColor: "#7C3AED" },
  btnPrimaryTxt: { color: "#fff", fontWeight: "800", fontSize: 13 },
  // Phase-31 — full-width secondary button (Copy Headers).
  btnWide: {
    marginTop: 12,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#CCFBF1",
    borderWidth: 1,
    borderColor: "#5EEAD4",
  },
  btnWideTxt: { color: "#0F766E", fontWeight: "800", fontSize: 13 },
  hintCenter: {
    fontSize: 11,
    color: "#6B7280",
    marginTop: 6,
    textAlign: "center",
    lineHeight: 15,
  },
  infoCard: {
    marginTop: 24,
    backgroundColor: "#EFF6FF",
    borderColor: "#BFDBFE",
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    gap: 6,
  },
  infoTitle: { fontSize: 13, fontWeight: "800", color: "#1E3A8A" },
  infoBody: { fontSize: 12, color: "#1E3A8A", lineHeight: 18 },
});
