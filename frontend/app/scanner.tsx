import React, { useEffect, useRef, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Alert,
  Platform,
  TextInput,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import { CameraView, useCameraPermissions } from "expo-camera";
import { colors } from "../lib/theme";

export default function ScannerModal() {
  const router = useRouter();
  const params = useLocalSearchParams<{ returnTo?: string }>();
  const [permission, requestPermission] = useCameraPermissions();
  const scannedRef = useRef(false);
  const [scannedValue, setScannedValue] = useState<string | null>(null);
  const [manualValue, setManualValue] = useState("");

  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) {
      requestPermission();
    }
  }, [permission, requestPermission]);

  const submitValue = (value: string) => {
    const v = value.trim();
    if (!v) return;
    if (params.returnTo === "add") {
      router.replace({ pathname: "/(tabs)/add", params: { scanned: v } });
    } else {
      router.back();
    }
  };

  const onBarcodeScanned = ({ data }: { data: string }) => {
    if (scannedRef.current) return;
    scannedRef.current = true;
    setScannedValue(data);
    if (Platform.OS !== "web") {
      setTimeout(() => submitValue(data), 400);
    }
  };

  const isWeb = Platform.OS === "web";
  const noPermission = !isWeb && permission && !permission.granted;

  return (
    <SafeAreaView style={styles.safe}>
      <View style={styles.header}>
        <TouchableOpacity
          testID="scanner-close"
          onPress={() => router.back()}
          style={styles.closeBtn}
        >
          <Ionicons name="close" size={22} color="#fff" />
        </TouchableOpacity>
        <Text style={styles.title}>Scan Tracking ID</Text>
        <View style={{ width: 44 }} />
      </View>

      {isWeb ? (
        <View style={styles.webBox}>
          <Ionicons name="barcode-outline" size={48} color="#fff" />
          <Text style={styles.webText}>
            Camera scanning works on mobile device. Enter tracking ID manually:
          </Text>
          <TextInput
            testID="manual-tracking-input"
            value={manualValue}
            onChangeText={setManualValue}
            placeholder="Enter tracking ID"
            placeholderTextColor="#9CA3AF"
            style={styles.manualInput}
            autoCapitalize="characters"
          />
          <TouchableOpacity
            testID="manual-submit-btn"
            style={styles.submitBtn}
            onPress={() => submitValue(manualValue)}
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
          <Text style={styles.webText}>Camera permission required.</Text>
          <TouchableOpacity
            style={styles.submitBtn}
            onPress={() => {
              requestPermission().then((r) => {
                if (!r.granted) {
                  Alert.alert(
                    "Permission",
                    "Enable camera permission in settings."
                  );
                }
              });
            }}
          >
            <Text style={styles.submitBtnText}>Grant Permission</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={styles.scannerWrap}>
          <CameraView
            style={StyleSheet.absoluteFillObject}
            facing="back"
            barcodeScannerSettings={{
              barcodeTypes: [
                "code128",
                "code39",
                "ean13",
                "ean8",
                "upc_a",
                "upc_e",
                "qr",
                "pdf417",
                "aztec",
                "datamatrix",
                "itf14",
              ],
            }}
            onBarcodeScanned={onBarcodeScanned}
          />
          <View style={styles.overlay}>
            <View style={styles.frame}>
              <View style={[styles.corner, styles.cornerTL]} />
              <View style={[styles.corner, styles.cornerTR]} />
              <View style={[styles.corner, styles.cornerBL]} />
              <View style={[styles.corner, styles.cornerBR]} />
            </View>
            <Text style={styles.overlayText}>
              Point at barcode to scan
            </Text>
            {scannedValue && (
              <Text style={styles.scannedText}>Scanned: {scannedValue}</Text>
            )}
          </View>
        </View>
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#000" },
  header: {
    height: 56,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    backgroundColor: "#000",
  },
  title: { color: "#fff", fontWeight: "800", fontSize: 16 },
  closeBtn: {
    width: 44,
    height: 44,
    borderRadius: 10,
    backgroundColor: "rgba(255,255,255,0.1)",
    justifyContent: "center",
    alignItems: "center",
  },
  center: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: 24,
    gap: 12,
  },
  scannerWrap: { flex: 1 },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: "center",
    alignItems: "center",
  },
  frame: { width: 260, height: 260, position: "relative" },
  corner: {
    position: "absolute",
    width: 36,
    height: 36,
    borderColor: colors.primary,
  },
  cornerTL: { top: 0, left: 0, borderTopWidth: 4, borderLeftWidth: 4 },
  cornerTR: { top: 0, right: 0, borderTopWidth: 4, borderRightWidth: 4 },
  cornerBL: { bottom: 0, left: 0, borderBottomWidth: 4, borderLeftWidth: 4 },
  cornerBR: { bottom: 0, right: 0, borderBottomWidth: 4, borderRightWidth: 4 },
  overlayText: {
    marginTop: 30,
    color: "#fff",
    fontWeight: "700",
    fontSize: 14,
  },
  scannedText: {
    marginTop: 12,
    color: colors.primary,
    fontWeight: "800",
    fontFamily: "Courier",
  },
  webBox: {
    flex: 1,
    padding: 24,
    justifyContent: "center",
    alignItems: "center",
    gap: 12,
  },
  webText: { color: "#fff", textAlign: "center", fontSize: 14 },
  manualInput: {
    width: "100%",
    height: 50,
    borderWidth: 2,
    borderColor: "rgba(255,255,255,0.2)",
    borderRadius: 10,
    paddingHorizontal: 14,
    color: "#fff",
    fontSize: 16,
    backgroundColor: "rgba(255,255,255,0.05)",
    marginTop: 10,
  },
  submitBtn: {
    marginTop: 4,
    height: 50,
    paddingHorizontal: 24,
    backgroundColor: colors.primary,
    borderRadius: 12,
    justifyContent: "center",
    alignItems: "center",
  },
  submitBtnText: { color: "#fff", fontWeight: "800" },
});
