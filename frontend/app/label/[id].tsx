import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
  Alert,
  Image,
  Dimensions,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView } from "react-native-webview";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Api, SenderAddress, Shipment, Courier } from "../../lib/api";
import { buildLabelHtml, LabelOptions, pageDimensionsFor } from "../../lib/label";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";

type PerPage = 1 | 2 | 4 | "thermal" | "barcode";

/**
 * Platform-aware HTML preview. Native → WebView. Web → iframe with srcdoc.
 * Both render THE EXACT SAME HTML that buildLabelHtml emits for the PDF,
 * so the on-screen preview is guaranteed to match the printed PDF.
 */
function HtmlPreview({
  html,
  height,
  onHeightChange,
}: {
  html: string;
  height: number;
  onHeightChange: (h: number) => void;
}) {
  if (!html) return null;
  if (Platform.OS === "web") {
    // Use a raw iframe (createElement works on react-native-web since the
    // runtime translates primitives to real DOM). We listen for postMessage
    // from the injected script to auto-size.
    return React.createElement("iframe" as any, {
      srcDoc: html,
      style: {
        width: "100%",
        height,
        border: 0,
        backgroundColor: "#F1F5F9",
        display: "block",
      },
      sandbox: "allow-scripts allow-same-origin",
      onLoad: () => {
        // Ask iframe to post its height via the already-injected script.
        // The injected script posts on load + resize + timers.
      },
    });
  }
  return (
    <WebView
      originWhitelist={["*"]}
      source={{ html }}
      style={{ backgroundColor: "#F1F5F9", flex: 1 }}
      scrollEnabled={false}
      javaScriptEnabled={true}
      domStorageEnabled={false}
      onMessage={(e) => {
        const h = parseInt(e?.nativeEvent?.data || "0", 10);
        if (!isNaN(h) && h > 100 && h < 3000) {
          onHeightChange(h);
        }
      }}
      androidLayerType="software"
    />
  );
}

/**
 * Wrap the print HTML with screen-only CSS that auto-scales the label
 * page to the mobile viewport width. The print CSS (`@page`, `@media print`)
 * is NOT affected — only what the WebView shows on-screen. This guarantees
 * the preview uses the exact same layout/CSS as the PDF, just scaled.
 */
function wrapHtmlForScreenPreview(html: string): string {
  // Inject right before </head>: screen styles + auto-fit JS.
  // Width of A6 sheet = 99mm ≈ 374px. We let the WebView engine render at
  // natural mm size, then CSS-scale the first .sheet to fit the viewport.
  const injection = `
<style>
  @media screen {
    html, body { margin: 0; padding: 0; background: #F1F5F9; }
    body { display: flex; justify-content: center; align-items: flex-start;
      padding: 8px 0 16px 0; }
    /* Only show the FIRST sheet in the on-screen preview (bulk PDFs have
       multiple sheets but preview only needs one). */
    .sheet ~ .sheet, .sheet-sticker ~ .sheet-sticker { display: none !important; }
    .sheet, .sheet-sticker {
      box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
      background: #fff;
      transform-origin: top center;
    }
  }
</style>
<script>
  function __fitSheet() {
    var el = document.querySelector('.sheet, .sheet-sticker');
    if (!el) return;
    var rect = el.getBoundingClientRect();
    var availW = window.innerWidth - 16; // 8px horiz padding each side
    if (rect.width <= 0 || availW <= 0) return;
    var scale = Math.min(1, availW / rect.width);
    el.style.transform = 'scale(' + scale + ')';
    // Reserve vertical space for the scaled element (avoid scroll cutoff).
    el.style.marginBottom = ((scale - 1) * rect.height) + 'px';
    // Report scaled height back to React Native so the WebView can size itself.
    var h = Math.ceil(rect.height * scale) + 24;
    if (window.ReactNativeWebView && window.ReactNativeWebView.postMessage) {
      window.ReactNativeWebView.postMessage(String(h));
    }
    // For iframe (web) preview, post up to parent.
    if (window.parent && window.parent !== window) {
      try { window.parent.postMessage({ __labelPreviewHeight: h }, '*'); } catch(e){}
    }
  }
  window.addEventListener('load', __fitSheet);
  window.addEventListener('resize', __fitSheet);
  // Retry after a tick in case images (logo) shift layout.
  setTimeout(__fitSheet, 150);
  setTimeout(__fitSheet, 400);
</script>`;
  if (html.includes("</head>")) {
    return html.replace("</head>", injection + "</head>");
  }
  return injection + html;
}

export default function LabelScreen() {
  const router = useRouter();
  // Phase F3.6 — gate the "Preview / Share PDF" button. Free tier
  // can still print directly (Print button stays visible) but PDF
  // download is reserved for paid tiers.
  const flagPdfDownload = useFeatureFlag("pdf_download");
  const { id } = useLocalSearchParams<{ id: string }>();
  const [shipment, setShipment] = useState<Shipment | null>(null);
  const [sender, setSender] = useState<SenderAddress | null>(null);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [loading, setLoading] = useState(true);

  const [perPage, setPerPage] = useState<PerPage>(4);
  const [copies, setCopies] = useState(1);
  const [showContact, setShowContact] = useState(true);

  const [brand, setBrand] = useState<{ name: string; logo_base64: string }>({
    name: "",
    logo_base64: "",
  });
  const [preferLogo, setPreferLogo] = useState<boolean>(true);
  const [logoShape, setLogoShape] = useState<"square" | "wide">("square");
  const [logoRatio, setLogoRatio] = useState<number | null>(null);
  const [labelFields, setLabelFields] = useState<any>(null);
  const [shipmentTagline, setShipmentTagline] = useState<string>("");
  const [customFields, setCustomFields] = useState<any[]>([]);

  // WebView-reported natural height after scaling. Starts with a sensible
  // A6 default (≈540px scaled) so layout doesn't jump while the page boots.
  const [webHeight, setWebHeight] = useState<number>(540);

  // Web: listen for iframe height messages.
  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    const handler = (ev: any) => {
      const h = ev?.data?.__labelPreviewHeight;
      if (typeof h === "number" && h > 100 && h < 3000) {
        setWebHeight(h);
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  const load = useCallback(async () => {
    try {
      const [s, settings, cs] = await Promise.all([
        Api.getShipment(String(id)),
        Api.getSettings(),
        Api.listCouriers().catch(() => [] as Courier[]),
      ]);
      setShipment(s);
      setSender(settings.sender);
      setCouriers(cs);
      setBrand(settings.brand || { name: "", logo_base64: "" });
      setShowContact(settings.sender.show_contact);
      setPreferLogo((settings as any).prefer_logo !== false);
      setLogoShape((settings as any).logo_shape === "wide" ? "wide" : "square");
      setLabelFields((settings as any).label_fields || null);
      setCustomFields((settings as any).custom_fields || []);
      setShipmentTagline(String((settings as any).shipment_tagline || ""));
    } catch (e: any) {
      Alert.alert("Error", e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    if (brand.logo_base64) {
      const uri = brand.logo_base64.startsWith("data:")
        ? brand.logo_base64
        : `data:image/png;base64,${brand.logo_base64}`;
      Image.getSize(
        uri,
        (w, h) => setLogoRatio(w / Math.max(1, h)),
        () => setLogoRatio(null),
      );
    } else {
      setLogoRatio(null);
    }
  }, [brand.logo_base64]);

  useEffect(() => {
    load();
  }, [load]);

  const getHtml = useCallback((): string => {
    if (!shipment || !sender) return "";
    const shipments = Array.from({ length: copies }, () => shipment);
    const effectiveShape: "square" | "wide" =
      logoShape === "wide" || (logoRatio !== null && logoRatio >= 2.2) ? "wide" : "square";
    const opts: LabelOptions = {
      perPage,
      showSenderContact: showContact,
      brand: { name: brand.name, logo_base64: brand.logo_base64 },
      preferLogo,
      logoShape: effectiveShape,
      couriers,
      labelFields: labelFields || undefined,
      shipmentTagline: shipmentTagline || undefined,
      customFields: customFields,
    };
    return buildLabelHtml(shipments, { ...sender, show_contact: showContact }, opts);
  }, [
    shipment, sender, copies, perPage, showContact, brand, preferLogo,
    logoShape, logoRatio, couriers, labelFields, shipmentTagline, customFields,
  ]);

  const previewHtml = useMemo(() => {
    const html = getHtml();
    if (!html) return "";
    return wrapHtmlForScreenPreview(html);
  }, [getHtml]);

  const printNow = async () => {
    const html = getHtml();
    if (!html) return;
    try {
      const dims = pageDimensionsFor(perPage);
      await Print.printAsync({ html, ...(dims || {}) });
    } catch (e: any) {
      Alert.alert("Print error", e?.message || "Failed to print");
    }
  };

  const previewPdf = async () => {
    const html = getHtml();
    if (!html) return;
    try {
      const dims = pageDimensionsFor(perPage);
      const { uri } = await Print.printToFileAsync({ html, ...(dims || {}) });
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

  const goBack = () => {
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)/shipments");
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
          onPress={goBack}
          style={styles.backBtn}
        >
          <PhIcon name="arrow-back" size={22} color={colors.text} />
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
        {/* Preview WebView — renders the EXACT HTML used for the PDF.
            Preview = PDF guaranteed because they share the same template. */}
        <View style={styles.previewCard}>
          <View style={styles.previewBadge}>
            <PhIcon name="eye-outline" size={13} color="#0369A1" />
            <Text style={styles.previewBadgeText}>
              LIVE PREVIEW · {perPage === "barcode" ? "50×25mm" : perPage === "thermal" ? "100×150mm" : perPage === 4 ? "A6 · 105×148mm" : perPage === 2 ? "A4 · 2/page" : "A4"}
            </Text>
          </View>
          {previewHtml ? (
            <View style={[styles.webWrap, { height: webHeight }]} testID="label-preview">
              <HtmlPreview
                html={previewHtml}
                height={webHeight}
                onHeightChange={setWebHeight}
              />
            </View>
          ) : (
            <ActivityIndicator style={{ marginVertical: 40 }} color={colors.primary} />
          )}
        </View>

        {/* Options */}
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Page Layout</Text>
          <View style={styles.toggleGrid}>
            {[
              { k: 1 as PerPage, label: "A4 · 1/page" },
              { k: 2 as PerPage, label: "A4 · 2/page" },
              { k: 4 as PerPage, label: "A6 · 1/page" },
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
              <PhIcon name="remove" size={20} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.copiesValue}>{copies}</Text>
            <TouchableOpacity
              testID="copies-inc"
              style={styles.copyBtn}
              onPress={() => setCopies(Math.min(20, copies + 1))}
            >
              <PhIcon name="add" size={20} color={colors.text} />
            </TouchableOpacity>
            <Text style={styles.hint}>
              {perPage === "thermal"
                ? `${copies} label${copies > 1 ? "s" : ""} (one per page)`
                : perPage === 4
                ? `${copies} label${copies > 1 ? "s" : ""} · 1 per A6 page`
                : `${copies} label${copies > 1 ? "s" : ""} · auto-paginated`}
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
          {flagPdfDownload ? (
            <TouchableOpacity
              testID="preview-pdf-btn"
              style={styles.secondaryBtn}
              onPress={previewPdf}
            >
              <PhIcon name="eye-outline" size={18} color={colors.text} />
              <Text style={styles.secondaryBtnText}>
                {Platform.OS === "web" ? "Preview PDF" : "Preview / Share"}
              </Text>
            </TouchableOpacity>
          ) : null}
          <TouchableOpacity
            testID="print-btn"
            style={styles.primaryBtn}
            onPress={printNow}
          >
            <PhIcon name="print" size={18} color="#fff" />
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

  previewCard: {
    backgroundColor: "#F1F5F9",
    borderWidth: 2,
    borderColor: "#CBD5E1",
    borderRadius: 12,
    padding: 8,
    marginBottom: 16,
    overflow: "hidden",
  },
  previewBadge: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: "#E0F2FE",
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
    marginBottom: 8,
    marginLeft: 4,
  },
  previewBadgeText: {
    fontSize: 10.5,
    fontWeight: "800",
    color: "#0369A1",
    letterSpacing: 0.3,
  },
  webWrap: {
    width: "100%",
    backgroundColor: "#F1F5F9",
    borderRadius: 8,
    overflow: "hidden",
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
