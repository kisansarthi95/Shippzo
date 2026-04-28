import React, { useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ActivityIndicator,
  Alert, Platform, TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
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

export default function ScannerModal() {
  const router = useRouter();
  const params = useLocalSearchParams<{ returnTo?: string; from?: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const scannedRef = useRef(false);
  const [scannedValue, setScannedValue] = useState<string | null>(null);
  const [manualValue, setManualValue] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [errorHint, setErrorHint] = useState<string | null>(null);

  const isWeb = Platform.OS === "web";

  // Pre-load the beep player as soon as scanner mounts — avoids first-scan delay.
  useEffect(() => {
    initScanFeedback();
    return () => {
      disposeScanFeedback();
    };
  }, []);

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
    if (couriersWithRules.length > 0) {
      const matched = findMatchingCourier(v, couriersWithRules as any);
      if (!matched) {
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

    // First check if this tracking already exists
    try {
      const existing = await Api.getShipmentByTracking(v);
      if (existing?.id) {
        router.replace(`/label/${existing.id}`);
        return;
      }
    } catch {
      // 404 means new — proceed
    }
    if (params.returnTo === "add") {
      // New tracking ID + user wants to create a shipment.
      //
      // Two entry points to this screen:
      //   A) From (tabs)/add itself (`from=add`): Add screen is already
      //      mounted with user-typed form data. We MUST NOT unmount it,
      //      otherwise the user's typed address/name/items/amount all
      //      vanish. Use router.back() + scannerBridge.
      //   B) From Dashboard/Home (no `from` param): Add screen is not
      //      yet mounted. router.back() would return to the dashboard
      //      and silently drop the scanned value. Fall back to
      //      router.replace() which pushes Add with the param.
      scannerBridge.push(v); // always — useFocusEffect on Add picks it up
      if (params.from === "add") {
        router.back();
      } else {
        router.replace({
          pathname: "/(tabs)/add",
          params: { scanned: v },
        });
      }
    } else {
      router.back();
    }
  };

  const onBarcodeScanned = ({ data }: { data: string }) => {
    if (scannedRef.current) return;
    scannedRef.current = true;
    setScannedValue(data);
    // Instant audio + haptic feedback (fire-and-forget).
    if (soundOn) {
      playScanSuccess();
    }
    setTimeout(() => submitValue(data), 300);
  };

  const noPermission = !isWeb && permission && !permission.granted;

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.header}>
        <TouchableOpacity testID="scanner-close" onPress={() => router.back()} style={styles.closeBtn}>
          <Ionicons name="close" size={22} color="#fff" />
        </TouchableOpacity>
        <Text style={styles.title}>Scan Tracking ID</Text>
        <TouchableOpacity
          testID="sound-toggle"
          onPress={() => setSoundOn((v) => !v)}
          style={styles.closeBtn}
          accessibilityLabel={soundOn ? "Mute scan beep" : "Unmute scan beep"}
        >
          <Ionicons
            name={soundOn ? "volume-high" : "volume-mute"}
            size={22}
            color="#fff"
          />
        </TouchableOpacity>
      </View>

      {isWeb ? (
        <View style={styles.webBox}>
          <Ionicons name="barcode-outline" size={48} color="#fff" />
          <Text style={styles.webText}>
            Camera scanning works on the Expo Go mobile app. On web, enter manually below:
          </Text>
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
        </View>
      ) : !permission ? (
        <View style={styles.center}>
          <ActivityIndicator color="#fff" />
        </View>
      ) : noPermission ? (
        <View style={styles.center}>
          <Ionicons name="camera-outline" size={48} color="#fff" />
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
            <Text style={styles.overlayText}>Point camera at barcode / QR</Text>
            {scannedValue && !errorHint && <Text style={styles.scannedText}>✓ {scannedValue}</Text>}
            {errorHint ? (
              <View style={styles.errorBanner}>
                <Ionicons name="close-circle" size={18} color="#fff" />
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
