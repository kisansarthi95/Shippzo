import React, { useEffect, useRef, useState } from "react";
import PhIcon from "../components/PhIcon";
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator,
  Alert, Platform, TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Api, Courier } from "../lib/api";
import { scannerBridge } from "../lib/scannerBridge";
import {
  initScanFeedback,
  playScanSuccess,
  playScanError,
  disposeScanFeedback,
} from "../lib/scanFeedback";
import { colors } from "../lib/theme";
import { validateTrackingId, findMatchingCourier } from "../lib/trackingValidator";
import { useFeatureFlag } from "../lib/feature_flags";

// Storage key for the user's "double-confirm scan" preference (per device).
const DOUBLE_CONFIRM_KEY = "@scanner_double_confirm_v1";
// Window (ms) within which a second matching read must arrive to confirm.
const CONFIRM_WINDOW_MS = 2500;

export default function ScannerModal() {
  const router = useRouter();
  const params = useLocalSearchParams<{ returnTo?: string; from?: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const scannedRef = useRef(false);
  const [scannedValue, setScannedValue] = useState<string | null>(null);
  const [manualValue, setManualValue] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const [doubleConfirm, setDoubleConfirm] = useState(true);
  const [pendingValue, setPendingValue] = useState<string | null>(null);
  const pendingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [errorHint, setErrorHint] = useState<string | null>(null);

  const isWeb = Platform.OS === "web";

  // Plan-gated feature flags (admin can toggle each off per plan).
  const flagSoundFeedback   = useFeatureFlag("scanner_sound_feedback");
  const flagDoubleConfirm   = useFeatureFlag("scanner_double_confirm");
  const flagManualEntry     = useFeatureFlag("scanner_manual_entry");

  // Pre-load the beep player as soon as scanner mounts — avoids first-scan delay.
  useEffect(() => {
    initScanFeedback();
    return () => {
      disposeScanFeedback();
    };
  }, []);

  // Restore the user's "double-confirm" preference (defaults to ON).
  useEffect(() => {
    (async () => {
      try {
        const v = await AsyncStorage.getItem(DOUBLE_CONFIRM_KEY);
        if (v === "0") setDoubleConfirm(false);
      } catch {/* ignore */}
    })();
  }, []);

  // Cleanup pending-confirm timer on unmount.
  useEffect(() => () => {
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
  }, []);

  const toggleDoubleConfirm = async () => {
    const next = !doubleConfirm;
    setDoubleConfirm(next);
    try {
      await AsyncStorage.setItem(DOUBLE_CONFIRM_KEY, next ? "1" : "0");
    } catch {/* ignore */}
    // Reset any pending state when the user changes mode mid-flow.
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    setPendingValue(null);
    scannedRef.current = false;
  };

  // Load couriers once so we can validate scans against their format rules.
  useEffect(() => {
    let cancelled = false;
    Api.listCouriers()
      .then((list) => { if (!cancelled) setCouriers(list || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const askPermission = async () => {
    setRequesting(true);
    try {
      await requestPermission();
    } finally {
      setRequesting(false);
    }
  };

  useEffect(() => {
    if (!isWeb && permission && !permission.granted && permission.canAskAgain) {
      askPermission();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [permission?.canAskAgain, permission?.granted, isWeb]);

  const submitValue = async (value: string, fromManual = false) => {
    const v = value.trim();
    if (!v) {
      playScanError();
      return;
    }

    // ── Format validation ─────────────────────────────────────────
    // If ANY courier has format rules configured, find the first one
    // whose rules accept this value. If none matches, the scan is
    // almost certainly garbled — show a red error and keep scanning.
    const couriersWithRules = couriers.filter(
      (c) => c.tracking_id_prefix || c.tracking_id_suffix || c.tracking_id_length,
    );
    let matchedCourier: Courier | null = null;
    if (couriersWithRules.length > 0) {
      matchedCourier = (findMatchingCourier(v, couriersWithRules as any) as Courier) || null;
      if (!matchedCourier) {
        // Run validation against the "best-guess" courier (first one
        // whose prefix matches, else the first with rules) so we can
        // give a precise reason to the user.
        const guess =
          couriersWithRules.find((c) =>
            (c.tracking_id_prefix || "").length > 0 &&
            v.toUpperCase().startsWith((c.tracking_id_prefix || "").toUpperCase()),
          ) || couriersWithRules[0];
        const res = validateTrackingId(v, guess as any);
        playScanError();
        setErrorHint(
          `${res.reason || "This doesn't match any courier format."}\n` +
          `Scanned: ${v}`,
        );
        // Reset debounce so the next good scan can fire.
        setTimeout(() => {
          scannedRef.current = false;
          setScannedValue(null);
        }, fromManual ? 0 : 900);
        return;
      }
    }

    // Reset the error hint on a good scan.
    setErrorHint(null);

    // First check if this tracking already exists. The Api wrapper now
    // returns null on 404 (no match), so we don't need a try/catch — and
    // RN's "Uncaught (in promise) AxiosError 404" dev warning never fires.
    const existing = await Api.getShipmentByTracking(v);
    if (existing?.id) {
      router.replace(`/label/${existing.id}`);
      return;
    }
    if (params.returnTo === "add") {
      // New tracking ID + user wants to create a shipment.
      //
      // Phase-4d: when a courier's format rules matched this scan,
      // include its id so the Add screen can auto-select the courier
      // dropdown — saves the user a tap.
      scannerBridge.push({
        value: v,
        courier_id: matchedCourier?.id || null,
        courier_name: matchedCourier?.name || null,
      });
      if (params.from === "add") {
        router.back();
      } else {
        router.replace({
          pathname: "/(tabs)/add",
          params: {
            scanned: v,
            ...(matchedCourier?.id ? { courier_id: matchedCourier.id } : {}),
          },
        });
      }
    } else if (params.returnTo === "shipment-details") {
      // Phase-25 — Tracking-ID Gate. User scanned from the Shipment
      // Details screen to attach a tracking ID to an existing shipment.
      // Push to the bridge so the screen's useFocusEffect can pick it
      // up and PATCH the shipment.
      scannerBridge.push({
        value: v,
        courier_id: matchedCourier?.id || null,
        courier_name: matchedCourier?.name || null,
      });
      router.back();
    } else {
      router.back();
    }
  };

  const onBarcodeScanned = ({ data }: { data: string }) => {
    if (scannedRef.current) return;
    if (!data) return;

    // Single-read mode → behave as before.
    if (!doubleConfirm) {
      scannedRef.current = true;
      setScannedValue(data);
      if (soundOn) playScanSuccess();
      setTimeout(() => submitValue(data), 300);
      return;
    }

    // Double-confirm mode: require two consecutive matching reads
    // within CONFIRM_WINDOW_MS to commit. Reduces single-frame
    // misreads (e.g. "EG350898496IN" vs "EG358898496IN").
    if (pendingValue === null) {
      // First read — start pending state.
      setPendingValue(data);
      if (soundOn) playScanSuccess();
      // Auto-clear pending if a matching second read doesn't arrive.
      if (pendingTimerRef.current) clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = setTimeout(() => {
        setPendingValue(null);
        pendingTimerRef.current = null;
      }, CONFIRM_WINDOW_MS);
      return;
    }

    // Already pending — compare.
    if (data.trim() === pendingValue.trim()) {
      // Second matching read → commit.
      if (pendingTimerRef.current) {
        clearTimeout(pendingTimerRef.current);
        pendingTimerRef.current = null;
      }
      scannedRef.current = true;
      setScannedValue(data);
      setPendingValue(null);
      if (soundOn) playScanSuccess();
      setTimeout(() => submitValue(data), 200);
    } else {
      // Inconsistent reads → restart the confirm cycle with the new value.
      setPendingValue(data);
      if (pendingTimerRef.current) clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = setTimeout(() => {
        setPendingValue(null);
        pendingTimerRef.current = null;
      }, CONFIRM_WINDOW_MS);
      // Subtle error tone — reading was unstable, ask user to hold steady.
      playScanError();
      setErrorHint("Reading was inconsistent — hold steady, scan again.");
      setTimeout(() => setErrorHint(null), 1200);
    }
  };

  const noPermission = !isWeb && permission && !permission.granted;

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.header}>
        <TouchableOpacity testID="scanner-close" onPress={() => router.back()} style={styles.closeBtn}>
          <PhIcon name="close" size={22} color="#fff" />
        </TouchableOpacity>
        <Text style={styles.title}>Scan Tracking ID</Text>
        <View style={{ flexDirection: "row", gap: 6 }}>
          {flagDoubleConfirm && (
          <TouchableOpacity
            testID="double-confirm-toggle"
            onPress={toggleDoubleConfirm}
            style={[styles.closeBtn, doubleConfirm && styles.closeBtnActive]}
            accessibilityLabel={doubleConfirm ? "Double-confirm scan ON" : "Double-confirm scan OFF"}
          >
            <PhIcon
              name={doubleConfirm ? "shield-checkmark" : "shield-outline"}
              size={20}
              color="#fff"
            />
          </TouchableOpacity>
          )}
          {flagSoundFeedback && (
          <TouchableOpacity
            testID="sound-toggle"
            onPress={() => setSoundOn((v) => !v)}
            style={styles.closeBtn}
            accessibilityLabel={soundOn ? "Mute scan beep" : "Unmute scan beep"}
          >
            <PhIcon
              name={soundOn ? "volume-high" : "volume-mute"}
              size={22}
              color="#fff"
            />
          </TouchableOpacity>
          )}
        </View>
      </View>

      {isWeb ? (
        <View style={styles.webBox}>
          <PhIcon name="barcode-outline" size={48} color="#fff" />
          <Text style={styles.webText}>
            Camera scanning works on the Expo Go mobile app. {flagManualEntry ? "On web, enter manually below:" : ""}
          </Text>
          {flagManualEntry && (
          <>
          <TextInput
            testID="manual-tracking-input"
            value={manualValue}
            onChangeText={setManualValue}
            placeholder="Enter tracking ID"
            placeholderTextColor="#9CA3AF"
            style={styles.manualInput}
            autoCapitalize="characters"
            onSubmitEditing={() => submitValue(manualValue, true)}
          />
          <TouchableOpacity
            testID="manual-submit-btn"
            style={styles.submitBtn}
            onPress={() => submitValue(manualValue, true)}
          >
            <Text style={styles.submitBtnText}>Use Tracking ID</Text>
          </TouchableOpacity>
          </>
          )}
        </View>
      ) : !permission ? (
        <View style={styles.center}>
          <ActivityIndicator color="#fff" />
        </View>
      ) : noPermission ? (
        <View style={styles.center}>
          <PhIcon name="camera-outline" size={48} color="#fff" />
          <Text style={styles.webText}>
            Camera access needed to scan tracking barcodes.
          </Text>
          <TouchableOpacity
            testID="grant-perm-btn"
            style={styles.submitBtn}
            onPress={async () => {
              const res = await requestPermission();
              if (!res.granted) {
                Alert.alert(
                  "Permission blocked",
                  Platform.OS === "ios"
                    ? "Go to Settings → Courier Label Manager → Camera to enable."
                    : "Open app settings to enable Camera."
                );
              }
            }}
            disabled={requesting}
          >
            <Text style={styles.submitBtnText}>
              {requesting ? "Requesting..." : "Grant Camera Access"}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => router.back()} style={{ marginTop: 10 }}>
            <Text style={{ color: "#fff", opacity: 0.7 }}>Cancel</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={styles.scannerWrap}>
          <CameraView
            style={StyleSheet.absoluteFillObject}
            facing="back"
            barcodeScannerSettings={{
              barcodeTypes: [
                "code128", "code39", "ean13", "ean8",
                "upc_a", "upc_e", "qr", "pdf417",
                "aztec", "datamatrix", "itf14",
              ],
            }}
            onBarcodeScanned={onBarcodeScanned}
          />
          <View style={styles.overlay} pointerEvents="none">
            <View style={styles.frame}>
              <View style={[styles.corner, styles.cornerTL]} />
              <View style={[styles.corner, styles.cornerTR]} />
              <View style={[styles.corner, styles.cornerBL]} />
              <View style={[styles.corner, styles.cornerBR]} />
            </View>
            <Text style={styles.overlayText}>
              {doubleConfirm
                ? "Point camera at barcode — we'll read it twice"
                : "Point camera at barcode / QR"}
            </Text>
            {pendingValue && !errorHint ? (
              <View style={styles.confirmBanner}>
                <ActivityIndicator color="#fff" size="small" />
                <Text style={styles.confirmBannerTxt}>
                  Confirming {pendingValue}{"\n"}
                  <Text style={styles.confirmBannerSub}>
                    Hold steady — scanning again to confirm…
                  </Text>
                </Text>
              </View>
            ) : null}
            {scannedValue && !errorHint && !pendingValue && <Text style={styles.scannedText}>✓ {scannedValue}</Text>}
            {errorHint ? (
              <View style={styles.errorBanner}>
                <PhIcon name="close-circle" size={18} color="#fff" />
                <Text style={styles.errorBannerTxt}>{errorHint}</Text>
              </View>
            ) : null}
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#000" },
  errorBanner: {
    position: "absolute",
    top: 40, left: 16, right: 16,
    backgroundColor: "rgba(220, 38, 38, 0.95)",
    padding: 12, borderRadius: 10,
    flexDirection: "row", alignItems: "flex-start", gap: 8,
  },
  errorBannerTxt: {
    flex: 1, color: "#fff", fontSize: 12.5, fontWeight: "700", lineHeight: 17,
  },
  header: {
    height: 56, flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", paddingHorizontal: 14, backgroundColor: "#000",
  },
  title: { color: "#fff", fontWeight: "800", fontSize: 16 },
  closeBtn: {
    width: 44, height: 44, borderRadius: 10,
    backgroundColor: "rgba(255,255,255,0.1)",
    justifyContent: "center", alignItems: "center",
  },
  closeBtnActive: {
    backgroundColor: "rgba(34, 197, 94, 0.35)",
    borderWidth: 1, borderColor: "rgba(34, 197, 94, 0.7)",
  },
  confirmBanner: {
    position: "absolute",
    top: 40, left: 16, right: 16,
    backgroundColor: "rgba(30, 64, 175, 0.94)",
    padding: 12, borderRadius: 10,
    flexDirection: "row", alignItems: "center", gap: 10,
  },
  confirmBannerTxt: {
    flex: 1, color: "#fff", fontSize: 13, fontWeight: "800",
  },
  confirmBannerSub: {
    color: "rgba(255,255,255,0.85)", fontSize: 11.5, fontWeight: "600",
  },
  center: { flex: 1, justifyContent: "center", alignItems: "center", padding: 24, gap: 12 },
  scannerWrap: { flex: 1 },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: "center", alignItems: "center",
  },
  frame: { width: 260, height: 260, position: "relative" },
  corner: { position: "absolute", width: 36, height: 36, borderColor: colors.primary },
  cornerTL: { top: 0, left: 0, borderTopWidth: 4, borderLeftWidth: 4 },
  cornerTR: { top: 0, right: 0, borderTopWidth: 4, borderRightWidth: 4 },
  cornerBL: { bottom: 0, left: 0, borderBottomWidth: 4, borderLeftWidth: 4 },
  cornerBR: { bottom: 0, right: 0, borderBottomWidth: 4, borderRightWidth: 4 },
  overlayText: { marginTop: 30, color: "#fff", fontWeight: "700", fontSize: 14 },
  scannedText: { marginTop: 12, color: colors.primary, fontWeight: "800", fontFamily: "Courier" },
  webBox: { flex: 1, padding: 24, justifyContent: "center", alignItems: "center", gap: 12 },
  webText: { color: "#fff", textAlign: "center", fontSize: 14 },
  manualInput: {
    width: "100%", height: 50, borderWidth: 2, borderColor: "rgba(255,255,255,0.2)",
    borderRadius: 10, paddingHorizontal: 14, color: "#fff", fontSize: 16,
    backgroundColor: "rgba(255,255,255,0.05)", marginTop: 10,
  },
  submitBtn: {
    marginTop: 4, height: 50, paddingHorizontal: 24,
    backgroundColor: colors.primary, borderRadius: 12,
    justifyContent: "center", alignItems: "center",
  },
  submitBtnText: { color: "#fff", fontWeight: "800" },
});
