import React, { useCallback, useEffect, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Api, SenderAddress, Shipment } from "../../lib/api";
import { buildLabelHtml, LabelOptions } from "../../lib/label";
import { colors } from "../../lib/theme";

type PerPage = 1 | 2 | 4 | "thermal";

export default function LabelScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const [shipment, setShipment] = useState<Shipment | null>(null);
  const [sender, setSender] = useState<SenderAddress | null>(null);
  const [loading, setLoading] = useState(true);

  const [perPage, setPerPage] = useState<PerPage>(4);
  const [copies, setCopies] = useState(1);
  const [showContact, setShowContact] = useState(true);

  const [brand, setBrand] = useState<{ name: string; logo_base64: string }>({
    name: "",
    logo_base64: "",
  });

  const load = useCallback(async () => {
    try {
      const [s, settings] = await Promise.all([
        Api.getShipment(String(id)),
        Api.getSettings(),
      ]);
      setShipment(s);
      setSender(settings.sender);
      setBrand(settings.brand || { name: "", logo_base64: "" });
      setShowContact(settings.sender.show_contact);
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const getHtml = () => {
    if (!shipment || !sender) return "";
    const shipments = Array.from({ length: copies }, () => shipment);
    const opts: LabelOptions = {
      perPage,
      showSenderContact: showContact,
      brand: { name: brand.name, logo_base64: brand.logo_base64 },
    };
    return buildLabelHtml(shipments, { ...sender, show_contact: showContact }, opts);
  };

  const printNow = async () => {
    const html = getHtml();
    if (!html) return;
    try {
      await Print.printAsync({ html });
    } catch (e: any) {
      Alert.alert("Print error", e?.message || "Failed to print");
    }
  };

  const previewPdf = async () => {
    const html = getHtml();
    if (!html) return;
    try {
      const { uri } = await Print.printToFileAsync({ html });
      if (Platform.OS === "web") {
        if (typeof window !== "undefined") window.open(uri, "_blank");
        return;
      }
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: "application/pdf",
          dialogTitle: "Preview label PDF",
          UTI: "com.adobe.pdf",
        });
      } else {
        Alert.alert("Saved", `PDF saved to ${uri}`);
      }
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed to generate PDF");
    }
  };

  if (loading || !shipment || !sender) {
    return (
      <SafeAreaView style={styles.safe}>
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <TouchableOpacity
          testID="label-back"
          onPress={() => router.back()}
          style={styles.backBtn}
        >
          <Ionicons name="arrow-back" size={22} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.title} numberOfLines={1}>
          Shipping Label
        </Text>
        <View style={{ width: 40 }} />
      </View>

      <ScrollView
        testID="label-scroll"
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
      >
        {/* Preview Card */}
        <View style={styles.preview} testID="label-preview">
          <View style={styles.previewHdr}>
            <View style={{ flex: 1, flexDirection: "row", alignItems: "center", gap: 8 }}>
              {brand.logo_base64 ? (
                <View style={{ width: 32, height: 32, backgroundColor: "#F3F4F6", borderRadius: 4 }} />
              ) : null}
              <Text style={styles.previewCourier} numberOfLines={1}>
                {brand.name || sender.name || "Your Brand"}
              </Text>
            </View>
            {shipment.payment_mode === "COD" ? (
              <View style={{ alignItems: "flex-end" }}>
                <Text style={styles.codBadge}>COD ₹{shipment.amount || shipment.cod_amount}</Text>
                <Text style={styles.paySubText}>via {shipment.courier_name}</Text>
              </View>
            ) : (
              <View style={{ alignItems: "flex-end" }}>
                <Text style={styles.prepaidBadge}>PREPAID ₹{shipment.amount || 0}</Text>
                <Text style={styles.paySubText}>via {shipment.courier_name}</Text>
              </View>
            )}
          </View>
          <View style={styles.previewBody}>
            <View style={styles.blk}>
              <Text style={styles.blkTitle}>FROM</Text>
              <Text style={styles.blkName}>{sender.name || "Sender"}</Text>
              <Text style={styles.blkLine}>{sender.address_line1}</Text>
              {!!sender.address_line2 && (
                <Text style={styles.blkLine}>{sender.address_line2}</Text>
              )}
              <Text style={styles.blkLine}>
                {[sender.city, sender.state, sender.pincode]
                  .filter(Boolean)
                  .join(", ")}
              </Text>
              {showContact && !!sender.phone && (
                <Text style={styles.blkLine}>📞 {sender.phone}</Text>
              )}
            </View>
            <View style={[styles.blk, styles.blkReceiver]}>
              <Text style={styles.blkTitle}>TO</Text>
              <Text style={styles.blkName}>{shipment.customer_name}</Text>
              <Text style={styles.blkLine}>{shipment.address_line1}</Text>
              {!!shipment.address_line2 && (
                <Text style={styles.blkLine}>{shipment.address_line2}</Text>
              )}
              <Text style={styles.blkLine}>
                {[shipment.city, shipment.state, shipment.pincode]
                  .filter(Boolean)
                  .join(", ")}
              </Text>
              {!!shipment.customer_phone && (
                <Text style={styles.blkLine}>📞 {shipment.customer_phone}</Text>
              )}
            </View>
          </View>
          <View style={styles.metaRow}>
            <Text style={styles.metaText}>Weight: {shipment.weight || "—"}</Text>
            <Text style={styles.metaText}>
              {shipment.items && shipment.items.length > 0
                ? `Items: ${shipment.items.join(", ")}`
                : `Item: ${shipment.item_description || "—"}`}
            </Text>
          </View>
          {!!shipment.order_id && (
            <View style={styles.metaRow}>
              <Text style={styles.metaText}>Order #: {shipment.order_id}</Text>
            </View>
          )}
          <View style={styles.trackBlock}>
            <Text style={styles.trackLabel}>TRACKING ID</Text>
            <Text style={styles.trackId}>{shipment.tracking_id}</Text>
            <View style={styles.barcodeStub} />
          </View>
        </View>

        {/* Options */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Page Layout</Text>
          <View style={styles.toggleGrid}>
            {[
              { k: 1 as PerPage, label: "A4 · 1/page" },
              { k: 2 as PerPage, label: "A4 · 2/page" },
              { k: 4 as PerPage, label: "A4 · 4/page" },
              { k: "thermal" as PerPage, label: "Thermal 4x6" },
              { k: "barcode" as PerPage, label: "Barcode Sticker 50x25mm" },
            ].map((opt) => {
              const active = perPage === opt.k;
              return (
                <TouchableOpacity
                  key={String(opt.k)}
                  testID={`layout-${opt.k}`}
                  onPress={() => setPerPage(opt.k)}
                  style={[
                    styles.layoutBtn,
                    active && styles.layoutBtnActive,
                  ]}
                >
                  <Text
                    style={[
                      styles.layoutText,
                      active && { color: "#fff" },
                    ]}
                  >
                    {opt.label}
                  </Text>
                </TouchableOpacity>
              );
            })}
          </View>
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Copies on one sheet</Text>
          <View style={styles.copiesRow}>
            <TouchableOpacity
              testID="copies-dec"
              style={styles.copyBtn}
              onPress={() => setCopies(Math.max(1, copies - 1))}
            >
              <Ionicons name="remove" size={20} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.copiesValue}>{copies}</Text>
            <TouchableOpacity
              testID="copies-inc"
              style={styles.copyBtn}
              onPress={() => setCopies(Math.min(20, copies + 1))}
            >
              <Ionicons name="add" size={20} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.hint}>
              {perPage === "thermal"
                ? `${copies} label${copies > 1 ? "s" : ""} (one per page)`
                : `${copies} label${copies > 1 ? "s" : ""} · auto-paginated ${perPage}/page`}
            </Text>
          </View>
        </View>

        <View style={styles.section}>
          <View style={styles.rowBetween}>
            <View style={{ flex: 1 }}>
              <Text style={styles.sectionTitle}>Show Sender Contact</Text>
              <Text style={styles.hint}>
                Toggle sender phone visibility on printed labels
              </Text>
            </View>
            <TouchableOpacity
              testID="toggle-contact"
              onPress={() => setShowContact(!showContact)}
              style={[
                styles.switchBtn,
                showContact && styles.switchBtnOn,
              ]}
            >
              <View
                style={[
                  styles.switchKnob,
                  showContact && styles.switchKnobOn,
                ]}
              />
            </TouchableOpacity>
          </View>
        </View>

        <View style={styles.ctaRow}>
          <TouchableOpacity
            testID="preview-pdf-btn"
            style={styles.secondaryBtn}
            onPress={previewPdf}
          >
            <Ionicons name="eye-outline" size={18} color={colors.text} />
            <Text style={styles.secondaryBtnText}>
              {Platform.OS === "web" ? "Preview PDF" : "Preview / Share"}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="print-btn"
            style={styles.primaryBtn}
            onPress={printNow}
          >
            <Ionicons name="print" size={18} color="#fff" />
            <Text style={styles.primaryBtnText}>
              {perPage === "barcode" ? "Print Stickers" : "Print Labels"}
            </Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingTop: 6,
    paddingBottom: 8,
  },
  backBtn: {
    width: 40,
    height: 40,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    justifyContent: "center",
    alignItems: "center",
  },
  title: { fontSize: 18, fontWeight: "800", color: colors.text },

  preview: {
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.secondary,
    borderRadius: 8,
    padding: 14,
    marginBottom: 16,
  },
  previewHdr: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    borderBottomWidth: 2,
    borderBottomColor: colors.secondary,
    paddingBottom: 10,
  },
  previewCourier: {
    fontSize: 18,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: 0.5,
  },
  codBadge: {
    backgroundColor: colors.primary,
    color: "#fff",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 4,
    fontWeight: "800",
    fontSize: 12,
    overflow: "hidden",
  },
  prepaidBadge: {
    backgroundColor: colors.secondary,
    color: "#fff",
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 4,
    fontWeight: "800",
    fontSize: 12,
    overflow: "hidden",
  },
  paySubText: {
    fontSize: 9,
    color: colors.textMuted,
    marginTop: 3,
    fontWeight: "600",
  },
  previewBody: { flexDirection: "row", gap: 8, marginTop: 10 },
  blk: {
    flex: 1,
    padding: 10,
    borderWidth: 1,
    borderStyle: "dashed",
    borderColor: colors.textMuted,
    borderRadius: 4,
  },
  blkReceiver: {
    flex: 1.3,
    borderWidth: 2,
    borderColor: colors.secondary,
    borderStyle: "solid",
    backgroundColor: colors.background,
  },
  blkTitle: {
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 1,
    color: colors.textMuted,
    marginBottom: 4,
  },
  blkName: { fontSize: 14, fontWeight: "800", color: colors.text },
  blkLine: { fontSize: 11, color: colors.text, marginTop: 2, lineHeight: 14 },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 10,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  metaText: { fontSize: 11, color: colors.text, fontWeight: "600" },
  trackBlock: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 2,
    borderTopColor: colors.secondary,
    alignItems: "center",
  },
  trackLabel: {
    fontSize: 9,
    fontWeight: "800",
    color: colors.textMuted,
    letterSpacing: 1,
  },
  trackId: {
    fontFamily: "Courier",
    fontSize: 18,
    fontWeight: "800",
    letterSpacing: 2,
    color: colors.text,
    marginTop: 4,
  },
  barcodeStub: {
    height: 40,
    width: "85%",
    marginTop: 8,
    backgroundColor: colors.secondary,
    opacity: 0.1,
    borderRadius: 2,
  },
  section: {
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 14,
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 12,
    fontWeight: "800",
    color: colors.text,
    letterSpacing: 1,
    textTransform: "uppercase",
    marginBottom: 10,
  },
  toggleGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  layoutBtn: {
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    backgroundColor: "#fff",
  },
  layoutBtnActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  layoutText: { fontWeight: "700", color: colors.text, fontSize: 13 },
  copiesRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  copyBtn: {
    width: 44,
    height: 44,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    justifyContent: "center",
    alignItems: "center",
    backgroundColor: "#fff",
  },
  copiesValue: {
    fontSize: 20,
    fontWeight: "800",
    color: colors.text,
    width: 40,
    textAlign: "center",
  },
  hint: { flex: 1, color: colors.textMuted, fontSize: 12 },
  rowBetween: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  switchBtn: {
    width: 50,
    height: 30,
    borderRadius: 999,
    backgroundColor: "#D1D5DB",
    padding: 3,
    justifyContent: "center",
  },
  switchBtnOn: { backgroundColor: colors.primary },
  switchKnob: {
    width: 24,
    height: 24,
    borderRadius: 12,
    backgroundColor: "#fff",
  },
  switchKnobOn: { transform: [{ translateX: 20 }] },
  ctaRow: { flexDirection: "row", gap: 10, marginTop: 6 },
  secondaryBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: colors.secondary,
    borderRadius: 12,
  },
  secondaryBtnText: { fontWeight: "800", color: colors.text },
  primaryBtn: {
    flex: 1.2,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    height: 52,
    backgroundColor: colors.primary,
    borderRadius: 12,
  },
  primaryBtnText: { fontWeight: "800", color: "#fff" },
});
