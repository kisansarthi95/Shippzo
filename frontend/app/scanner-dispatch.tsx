/**
 * Scanner — "Scan to Dispatch" (Phase-9)
 * --------------------------------------
 * A warehouse-optimised, continuous-scan variant of the main scanner
 * screen. User opens this once, keeps the camera pointed at parcels,
 * and each successful read atomically flips that shipment's status
 * from Pending → Dispatch via POST /api/shipments/scan-dispatch.
 *
 * UX goals (locked per spec):
 *   • One scan = one action. NO confirmation popups. Camera never closes.
 *   • Duplicate reads within 2s are silently ignored (no toast spam).
 *   • Three outcome buckets with locked cream/warning/red palettes:
 *       moved    → cream toast "0003 moved to Dispatch successfully"
 *       already  → darker cream banner "Already in Dispatch"
 *       failed   → pink banner "Tracking 0003 not found"
 *   • Live running tallies (scanned/moved/already/failed).
 *   • Scrollable scan log (last 50) with Clear All button.
 *   • Pause/Stop controls at top right.
 *
 * Colors:
 *   Cream bg  #F4E3CF · secondary #F8EBDD · border #E6C9A8 · text #8B5E34
 *   Failed    #FFE5E5 · #991B1B
 *   Scanner   Black bg + ORANGE corner brackets + RED horizontal scan line.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Alert,
  Platform,
  TextInput,
  Vibration,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useRouter, useLocalSearchParams } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  Easing,
  cancelAnimation,
} from "react-native-reanimated";
import { Api } from "../lib/api";
import { colors } from "../lib/theme";

type Outcome = "moved" | "already" | "failed";
type Mode = "dispatch" | "ship";

type LogEntry = {
  id: string;                   // unique (tracking_id + ts)
  tracking_id: string;
  customer_name: string;
  time_label: string;           // e.g. "12:49:12 PM"
  outcome: Outcome;
  message: string;
};

const DEBOUNCE_MS = 2000;       // same barcode within 2s → ignore
const MAX_LOG_ROWS = 50;

// Mode-aware theme & copy. Each mode maps a transition bucket to its
// own locked colour palette so the same scanner component serves both
// Pending→Dispatch (cream) and Dispatch→Shipped (purple) flows.
type ModeTheme = {
  title: string;
  accent: string;              // dot + brackets + CTA accents
  movedBg: string;             // stat card / log badge / toast bg
  alreadyBg: string;           // "already" stat card / log badge bg
  failedBg: string;            // failure bg (shared across modes)
  textOnMoved: string;         // foreground on cream / purple badges
  textOnAlready: string;
  corner: string;              // scanner corner bracket color
  toastBg: string;             // full toast bg (cream vs solid purple)
  toastText: string;           // toast text color
  toastAlreadyBg: string;
  toastAlreadyText: string;
  statusActiveDot: string;
  statusActiveBg: string;
  statusActiveBorder: string;
  statusActiveText: string;
  movedLabel: string;
  alreadyLabel: string;
  badgeMovedLabel: string;
  badgeAlreadyLabel: string;
  scanCall: (tid: string) => Promise<{
    outcome: Outcome;
    reason: string;
    message: string;
    shipment: any;
  }>;
};

const MODE_CONFIG: Record<Mode, ModeTheme> = {
  dispatch: {
    title: "Active Scanner",
    accent: "#FF6B00",
    movedBg: "#F4E3CF",
    alreadyBg: "#F8EBDD",
    failedBg: "#FFE5E5",
    textOnMoved: "#8B5E34",
    textOnAlready: "#8B5E34",
    corner: "#FF6B00",
    toastBg: "#F4E3CF",
    toastText: "#5A3E2B",
    toastAlreadyBg: "#F8EBDD",
    toastAlreadyText: "#5A3E2B",
    statusActiveDot: "#FF6B00",
    statusActiveBg: "#FFF5EC",
    statusActiveBorder: "#FFD9B8",
    statusActiveText: "#FF6B00",
    movedLabel: "Moved to\nDispatch",
    alreadyLabel: "Already in\nDispatch",
    badgeMovedLabel: "Dispatch",
    badgeAlreadyLabel: "Already",
    scanCall: (tid) => Api.scanDispatch(tid),
  },
  ship: {
    title: "Dispatch Scanner",
    accent: "#6B5BFF",
    movedBg: "#EEE9FF",
    alreadyBg: "#F4F1FF",
    failedBg: "#FFE5E5",
    textOnMoved: "#6B5BFF",
    textOnAlready: "#6B5BFF",
    corner: "#6B5BFF",
    // Spec: toast bg is SOLID purple with WHITE text for this mode.
    toastBg: "#6B5BFF",
    toastText: "#FFFFFF",
    toastAlreadyBg: "#F4F1FF",
    toastAlreadyText: "#4B3FCF",
    statusActiveDot: "#6B5BFF",
    statusActiveBg: "#F4F1FF",
    statusActiveBorder: "#DAD0FF",
    statusActiveText: "#6B5BFF",
    movedLabel: "Moved to\nShipped",
    alreadyLabel: "Already\nShipped",
    badgeMovedLabel: "Shipped",
    badgeAlreadyLabel: "Already Shipped",
    scanCall: (tid) => Api.scanShip(tid),
  },
};

export default function ScannerDispatch() {
  const router = useRouter();
  const params = useLocalSearchParams<{ mode?: string }>();
  const mode: Mode = params.mode === "ship" ? "ship" : "dispatch";
  const theme = useMemo(() => MODE_CONFIG[mode], [mode]);
  const [permission, requestPermission] = useCameraPermissions();

  const [paused, setPaused] = useState(false);
  const [manualValue, setManualValue] = useState("");
  const [busy, setBusy] = useState(false);

  const [scannedCount, setScannedCount] = useState(0);
  const [movedCount, setMovedCount] = useState(0);
  const [alreadyCount, setAlreadyCount] = useState(0);
  const [failedCount, setFailedCount] = useState(0);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [toast, setToast] = useState<{ msg: string; kind: Outcome } | null>(null);

  // Debounce state — "last seen <tracking> at <timestamp>".
  const lastScanRef = useRef<{ code: string; ts: number } | null>(null);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isWeb = Platform.OS === "web";

  // Red scan-line animation (loops 0→100→0 forever while not paused).
  const linePos = useSharedValue(0);
  useEffect(() => {
    if (paused) {
      cancelAnimation(linePos);
      return;
    }
    linePos.value = 0;
    linePos.value = withRepeat(
      withTiming(1, { duration: 1800, easing: Easing.inOut(Easing.quad) }),
      -1,
      true,
    );
    return () => cancelAnimation(linePos);
  }, [paused, linePos]);

  const lineStyle = useAnimatedStyle(() => ({
    top: `${(linePos.value * 86) + 7}%`,
  }));

  // Request camera permission on mount.
  useEffect(() => {
    (async () => {
      if (!permission) return;
      if (!permission.granted) {
        await requestPermission();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permission]);

  // Clear any pending toast timer on unmount.
  useEffect(() => () => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
  }, []);

  // ---------- Core scan handler ----------
  const handleScan = useCallback(
    async (rawValue: string) => {
      if (!rawValue) return;
      if (paused) return;
      if (busy) return;

      const code = String(rawValue || "").trim();
      if (!code) return;

      // Debounce: ignore the same barcode within DEBOUNCE_MS.
      const now = Date.now();
      const last = lastScanRef.current;
      if (last && last.code === code && now - last.ts < DEBOUNCE_MS) {
        return;
      }
      lastScanRef.current = { code, ts: now };

      setBusy(true);
      setScannedCount((n) => n + 1);

      try {
        const res = await theme.scanCall(code);
        const ship = res.shipment || {};
        const customer = String((ship as any).customer_name || "").trim();
        const nowLabel = new Date().toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
        const entry: LogEntry = {
          id: `${code}-${now}`,
          tracking_id: code,
          customer_name: customer || "—",
          time_label: nowLabel,
          outcome: res.outcome,
          message: res.message,
        };
        setLog((prev) => [entry, ...prev].slice(0, MAX_LOG_ROWS));

        if (res.outcome === "moved") {
          setMovedCount((n) => n + 1);
          const destLabel = mode === "ship" ? "Shipped" : "Ready to Ship";
          // Phase-12: scanner returned `hint: "skipped_processing"` when
          // the source was Pending (not Processing). Surface a clean
          // toast explaining the recommended flow without blocking
          // progress — the shipment was still moved successfully.
          if ((res as any).hint === "skipped_processing") {
            showToast(
              `${code} → ${destLabel} (tip: mark Processing first next time)`,
              "already",
            );
          } else {
            showToast(`${code} moved to ${destLabel} successfully`, "moved");
          }
          try { Vibration.vibrate(30); } catch {/* ignore */}
        } else if (res.outcome === "already") {
          setAlreadyCount((n) => n + 1);
          showToast(
            `${code} already ${mode === "ship" ? "Shipped" : "in Ready to Ship"}`,
            "already",
          );
        } else {
          setFailedCount((n) => n + 1);
          showToast(res.message || `Tracking ${code} not found`, "failed");
          try { Vibration.vibrate([0, 80, 60, 80]); } catch {/* ignore */}
        }
      } catch (e: any) {
        setFailedCount((n) => n + 1);
        const msg = e?.response?.data?.detail || e?.message || "Scan failed";
        showToast(msg, "failed");
      } finally {
        setBusy(false);
      }
    },
    [paused, busy],
  );

  const showToast = (msg: string, kind: Outcome) => {
    setToast({ msg, kind });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 2400);
  };

  const handleManualSubmit = () => {
    const v = manualValue.trim();
    if (!v) return;
    setManualValue("");
    void handleScan(v);
  };

  const stop = () => router.back();
  const clearLog = () => setLog([]);

  // ---------- Render ----------
  if (!permission) {
    return (
      <SafeAreaView style={styles.safe}>
        <Text style={{ color: "#fff", marginTop: 40, textAlign: "center" }}>
          Loading camera…
        </Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity
          onPress={stop}
          hitSlop={8}
          testID="scanner-back"
          style={styles.headerIconBtn}
        >
          <Ionicons name="arrow-back" size={22} color="#111827" />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>{theme.title}</Text>
        <View style={{ width: 38 }} />
      </View>

      {/* Status + Pause/Stop */}
      <View style={styles.statusBar}>
        <View
          style={[
            styles.statusPill,
            {
              backgroundColor: theme.statusActiveBg,
              borderColor: theme.statusActiveBorder,
            },
          ]}
        >
          <View
            style={[
              styles.statusDot,
              { backgroundColor: paused ? "#9CA3AF" : theme.statusActiveDot },
            ]}
          />
          <Text style={[styles.statusPillText, { color: theme.statusActiveText }]}>
            {paused ? "Paused" : "Scanner Active"}
          </Text>
        </View>
        <View style={{ flex: 1 }} />
        <TouchableOpacity
          testID="scanner-pause"
          onPress={() => setPaused((p) => !p)}
          style={[styles.ctrlBtn, styles.ctrlBtnPause]}
        >
          <Ionicons
            name={paused ? "play" : "pause"}
            size={14}
            color="#111827"
          />
          <Text style={styles.ctrlBtnText}>{paused ? "Resume" : "Pause"}</Text>
        </TouchableOpacity>
        <TouchableOpacity
          testID="scanner-stop"
          onPress={stop}
          style={[styles.ctrlBtn, styles.ctrlBtnStop]}
        >
          <Ionicons name="stop" size={14} color="#fff" />
          <Text style={[styles.ctrlBtnText, { color: "#fff" }]}>Stop</Text>
        </TouchableOpacity>
      </View>

      <ScrollView
        contentContainerStyle={{ paddingHorizontal: 14, paddingBottom: 90 }}
      >
        {/* Scanner frame */}
        <View style={styles.scannerFrame}>
          {isWeb ? (
            <View style={styles.webFallback}>
              <Ionicons name="camera-outline" size={42} color="#FF6B00" />
              <Text style={styles.webFallbackText}>
                Camera scan isn't available in web preview. Use the manual
                entry box below — works the same.
              </Text>
            </View>
          ) : !permission.granted ? (
            <View style={styles.webFallback}>
              <Ionicons name="lock-closed-outline" size={36} color="#fff" />
              <Text style={[styles.webFallbackText, { color: "#fff" }]}>
                Camera permission needed to scan barcodes.
              </Text>
              <TouchableOpacity
                onPress={requestPermission}
                style={styles.grantBtn}
              >
                <Text style={styles.grantBtnText}>Grant Permission</Text>
              </TouchableOpacity>
            </View>
          ) : (
            <>
              {!paused && (
                <CameraView
                  style={StyleSheet.absoluteFill}
                  barcodeScannerSettings={{
                    barcodeTypes: [
                      "qr",
                      "ean13",
                      "ean8",
                      "code128",
                      "code39",
                      "code93",
                      "upc_a",
                      "upc_e",
                      "itf14",
                      "pdf417",
                    ],
                  }}
                  onBarcodeScanned={(r) => handleScan(r.data || "")}
                />
              )}
              {/* Corner brackets (mode accent) */}
              <View style={[styles.corner, styles.cornerTL, { borderColor: theme.corner }]} />
              <View style={[styles.corner, styles.cornerTR, { borderColor: theme.corner }]} />
              <View style={[styles.corner, styles.cornerBL, { borderColor: theme.corner }]} />
              <View style={[styles.corner, styles.cornerBR, { borderColor: theme.corner }]} />
              {/* Red scan line */}
              {!paused && (
                <Animated.View style={[styles.scanLine, lineStyle]} />
              )}
              <Text style={styles.framePlaceholder}>
                Place barcode inside the frame
              </Text>
            </>
          )}
        </View>

        {/* Manual entry — universally available fallback */}
        <View style={[styles.manualRow, { borderColor: theme.movedBg }]}>
          <Ionicons name="keypad-outline" size={18} color={theme.textOnMoved} />
          <TextInput
            testID="scanner-manual-input"
            style={styles.manualInput}
            placeholder="or type tracking ID & press ✔"
            placeholderTextColor="#B08868"
            value={manualValue}
            onChangeText={setManualValue}
            onSubmitEditing={handleManualSubmit}
            returnKeyType="done"
            autoCapitalize="characters"
          />
          <TouchableOpacity
            onPress={handleManualSubmit}
            style={[styles.manualGo, { backgroundColor: theme.accent }]}
            testID="scanner-manual-submit"
          >
            <Ionicons name="checkmark" size={18} color="#fff" />
          </TouchableOpacity>
        </View>

        {/* Stats row */}
        <View style={styles.statsRow}>
          <View style={[styles.statCard, { backgroundColor: "#F9F1E6" }]}>
            <Text style={styles.statLabel}>Scanned</Text>
            <Text style={[styles.statNumber, { color: theme.accent }]}>
              {scannedCount}
            </Text>
          </View>
          <View style={[styles.statCard, { backgroundColor: theme.movedBg }]}>
            <Text style={[styles.statLabel, { color: theme.textOnMoved }]}>
              {theme.movedLabel}
            </Text>
            <Text style={[styles.statNumber, { color: theme.textOnMoved }]}>
              {movedCount}
            </Text>
          </View>
          <View style={[styles.statCard, { backgroundColor: theme.alreadyBg }]}>
            <Text style={[styles.statLabel, { color: theme.textOnAlready }]}>
              {theme.alreadyLabel}
            </Text>
            <Text style={[styles.statNumber, { color: theme.textOnAlready }]}>
              {alreadyCount}
            </Text>
          </View>
          <View style={[styles.statCard, { backgroundColor: theme.failedBg }]}>
            <Text style={[styles.statLabel, { color: "#991B1B" }]}>
              Failed
            </Text>
            <Text style={[styles.statNumber, { color: "#991B1B" }]}>
              {failedCount}
            </Text>
          </View>
        </View>

        {/* Live Scan Log */}
        <View style={styles.logHeader}>
          <Text style={styles.logTitle}>Live Scan Log</Text>
          {log.length > 0 && (
            <TouchableOpacity onPress={clearLog} testID="scanner-clear-log">
              <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                <Ionicons name="trash-outline" size={14} color="#8B5E34" />
                <Text style={styles.logClearText}>Clear All</Text>
              </View>
            </TouchableOpacity>
          )}
        </View>
        {log.length === 0 ? (
          <View style={styles.logEmpty}>
            <Ionicons name="barcode-outline" size={28} color="#CBB79A" />
            <Text style={styles.logEmptyText}>
              Scan a parcel to start the log.
            </Text>
          </View>
        ) : (
          <View style={{ gap: 8 }}>
            {log.map((row) => (
              <View key={row.id} style={styles.logRow}>
                <Ionicons
                  name={
                    row.outcome === "moved"
                      ? "checkmark-circle"
                      : row.outcome === "already"
                        ? "alert-circle"
                        : "close-circle"
                  }
                  size={20}
                  color={
                    row.outcome === "moved"
                      ? theme.textOnMoved
                      : row.outcome === "already"
                        ? theme.textOnAlready
                        : "#991B1B"
                  }
                />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles.logTid} numberOfLines={1}>
                    {row.tracking_id}
                    <Text style={styles.logName}>{"  " + row.customer_name}</Text>
                  </Text>
                  <Text style={styles.logTime}>{row.time_label}</Text>
                </View>
                <View
                  style={[
                    styles.logBadge,
                    row.outcome === "moved" && { backgroundColor: theme.movedBg },
                    row.outcome === "already" && { backgroundColor: theme.alreadyBg },
                    row.outcome === "failed" && { backgroundColor: theme.failedBg },
                  ]}
                >
                  <Text
                    style={[
                      styles.logBadgeText,
                      {
                        color:
                          row.outcome === "failed"
                            ? "#991B1B"
                            : row.outcome === "moved"
                              ? theme.textOnMoved
                              : theme.textOnAlready,
                      },
                    ]}
                  >
                    {row.outcome === "moved"
                      ? theme.badgeMovedLabel
                      : row.outcome === "already"
                        ? theme.badgeAlreadyLabel
                        : "Failed"}
                  </Text>
                </View>
              </View>
            ))}
          </View>
        )}
      </ScrollView>

      {/* Toast — mode-themed (cream for Dispatch mode, purple for Ship mode) */}
      {toast && (
        <View
          style={[
            styles.toast,
            toast.kind === "moved" && {
              backgroundColor: theme.toastBg,
              borderColor: theme.toastBg === "#6B5BFF" ? "#5A4BEE" : "#E6C9A8",
            },
            toast.kind === "already" && {
              backgroundColor: theme.toastAlreadyBg,
              borderColor: "#E6C9A8",
            },
            toast.kind === "failed" && {
              backgroundColor: "#FFE5E5",
              borderColor: "#F5B5B5",
            },
          ]}
          testID="scanner-toast"
        >
          <Ionicons
            name={
              toast.kind === "moved"
                ? "checkmark-circle"
                : toast.kind === "already"
                  ? "alert-circle"
                  : "close-circle"
            }
            size={18}
            color={
              toast.kind === "failed"
                ? "#991B1B"
                : toast.kind === "moved"
                  ? theme.toastText
                  : theme.toastAlreadyText
            }
          />
          <Text
            style={[
              styles.toastText,
              {
                color:
                  toast.kind === "failed"
                    ? "#991B1B"
                    : toast.kind === "moved"
                      ? theme.toastText
                      : theme.toastAlreadyText,
              },
            ]}
            numberOfLines={2}
          >
            {toast.msg}
          </Text>
          <TouchableOpacity onPress={() => setToast(null)} hitSlop={6}>
            <Ionicons
              name="close"
              size={16}
              color={
                toast.kind === "failed"
                  ? "#991B1B"
                  : toast.kind === "moved"
                    ? theme.toastText
                    : theme.toastAlreadyText
              }
            />
          </TouchableOpacity>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: "#F7F7F9",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  headerIconBtn: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
  },
  headerTitle: {
    flex: 1,
    textAlign: "left",
    fontSize: 20,
    fontWeight: "800",
    color: "#111827",
    marginLeft: 4,
  },
  statusBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingBottom: 10,
    gap: 8,
  },
  statusPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 999,
    backgroundColor: "#FFF5EC",
    borderWidth: 1,
    borderColor: "#FFD9B8",
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  statusPillText: {
    fontSize: 12,
    fontWeight: "700",
    color: "#FF6B00",
  },
  ctrlBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 10,
  },
  ctrlBtnPause: {
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  ctrlBtnStop: {
    backgroundColor: "#DC2626",
  },
  ctrlBtnText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#111827",
  },
  scannerFrame: {
    width: "100%",
    aspectRatio: 1.5,
    backgroundColor: "#0B0B0B",
    borderRadius: 14,
    overflow: "hidden",
    position: "relative",
    justifyContent: "center",
    alignItems: "center",
    marginTop: 4,
  },
  webFallback: {
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 24,
  },
  webFallbackText: {
    color: "#D4D4D8",
    fontSize: 13,
    textAlign: "center",
    lineHeight: 18,
  },
  grantBtn: {
    marginTop: 12,
    paddingHorizontal: 16,
    paddingVertical: 10,
    backgroundColor: "#FF6B00",
    borderRadius: 8,
  },
  grantBtnText: {
    color: "#fff",
    fontWeight: "800",
  },
  corner: {
    position: "absolute",
    width: 22,
    height: 22,
    borderColor: "#FF6B00",
  },
  cornerTL: { top: 16, left: 16, borderTopWidth: 3, borderLeftWidth: 3 },
  cornerTR: { top: 16, right: 16, borderTopWidth: 3, borderRightWidth: 3 },
  cornerBL: { bottom: 16, left: 16, borderBottomWidth: 3, borderLeftWidth: 3 },
  cornerBR: { bottom: 16, right: 16, borderBottomWidth: 3, borderRightWidth: 3 },
  scanLine: {
    position: "absolute",
    left: "6%",
    right: "6%",
    height: 2,
    backgroundColor: "#EF4444",
    boxShadow: "0px 0px 6px rgba(239, 68, 68, 0.9)",
    elevation: 6,
  },
  framePlaceholder: {
    position: "absolute",
    bottom: 10,
    left: 0,
    right: 0,
    textAlign: "center",
    color: "#D4D4D8",
    fontSize: 11,
    fontWeight: "600",
  },
  manualRow: {
    marginTop: 10,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#FFF",
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderWidth: 1,
    borderColor: "#E6C9A8",
    gap: 8,
  },
  manualInput: {
    flex: 1,
    fontSize: 14,
    color: "#111827",
    paddingVertical: 6,
  },
  manualGo: {
    backgroundColor: "#FF6B00",
    width: 34,
    height: 34,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  statsRow: {
    marginTop: 14,
    flexDirection: "row",
    gap: 8,
  },
  statCard: {
    flex: 1,
    paddingVertical: 12,
    paddingHorizontal: 10,
    borderRadius: 12,
    alignItems: "flex-start",
    minHeight: 80,
  },
  statLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: "#7A5A38",
    lineHeight: 14,
  },
  statNumber: {
    marginTop: 6,
    fontSize: 22,
    fontWeight: "800",
    color: "#FF6B00",
  },
  logHeader: {
    marginTop: 18,
    marginBottom: 10,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  logTitle: {
    fontSize: 15,
    fontWeight: "800",
    color: "#111827",
  },
  logClearText: {
    color: "#8B5E34",
    fontSize: 12,
    fontWeight: "700",
  },
  logEmpty: {
    alignItems: "center",
    paddingVertical: 24,
    gap: 8,
  },
  logEmptyText: {
    fontSize: 12,
    color: "#9CA3AF",
  },
  logRow: {
    flexDirection: "row",
    alignItems: "center",
    padding: 12,
    backgroundColor: "#FFF",
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#F1ECE4",
  },
  logTid: {
    fontSize: 13,
    fontWeight: "800",
    color: "#111827",
  },
  logName: {
    fontWeight: "600",
    color: "#6B7280",
  },
  logTime: {
    marginTop: 2,
    fontSize: 11,
    color: "#9CA3AF",
  },
  logBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 8,
    minWidth: 70,
    alignItems: "center",
  },
  logBadgeText: {
    fontSize: 11,
    fontWeight: "800",
    color: "#8B5E34",
  },
  toast: {
    position: "absolute",
    left: 12,
    right: 12,
    bottom: 16,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 1,
  },
  toastText: {
    flex: 1,
    color: "#5A3E2B",
    fontSize: 13,
    fontWeight: "700",
  },
});
