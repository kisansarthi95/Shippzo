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
  Image,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as Print from "expo-print";
import * as Sharing from "expo-sharing";
import { Api, SenderAddress, Shipment, Courier } from "../../lib/api";
import { buildLabelHtml, LabelOptions, pageDimensionsFor } from "../../lib/label";
import { barcodeBars } from "../../lib/barcode";
import { colors } from "../../lib/theme";

type PerPage = 1 | 2 | 4 | "thermal";

// Lightweight native barcode preview using <View> bars
function BarcodePreview({ value, height = 40 }: { value: string; height?: number }) {
  const { runs, totalWidth } = barcodeBars(value || "NA");
  return (
    <View style={{ flexDirection: "row", height, width: "85%", alignSelf: "center" }}>
      {runs.map((r, i) => (
        <View
          key={i}
          style={{
            flexGrow: r.w,
            flexShrink: 0,
            flexBasis: 0,
            backgroundColor: r.on ? "#000" : "#fff",
          }}
        />
      ))}
    </View>
  );
}

/**
 * Render user-defined custom label fields that target a given slot.
 * Used in the in-app preview (this file only) — the PDF side has its own
 * HTML-based renderer in lib/label.ts. Both must stay visually in sync.
 */
function CfSlot({
  fields,
  position,
  shipment,
}: {
  fields: any[];
  position:
    | "header_top"
    | "from_block"
    | "to_block"
    | "meta_row"
    | "notes_area"
    | "footer_bottom";
  shipment?: any;
}) {
  if (!fields || fields.length === 0) return null;
  const matched = fields.filter(
    (f) =>
      f &&
      f.enabled &&
      f.position === position &&
      (f.label || f.value || (shipment?.custom_values?.[f.id]))
  );
  if (matched.length === 0) return null;

  const fontFor = (sz?: string) => (sz === "md" ? 12 : sz === "xs" ? 9 : 10.5);

  if (position === "meta_row") {
    // Same row as Wt / Box / Item — compact inline chips.
    return (
      <>
        {matched.map((f, i) => {
          const v = shipment?.custom_values?.[f.id] ?? f.value;
          return (
            <Text
              key={f.id || i}
              style={{
                fontSize: fontFor(f.size),
                color: "#1F2937",
              }}
              numberOfLines={1}
            >
              {f.label ? <Text style={{ color: "#6B7280", fontWeight: "700" }}>{f.label} </Text> : null}
              <Text style={{ fontWeight: f.bold !== false ? "800" : "400" }}>{v}</Text>
            </Text>
          );
        })}
      </>
    );
  }

  if (position === "notes_area") {
    // Notes-area style: blue left-border box similar to shipment notes.
    return (
      <View
        style={{
          marginTop: 8,
          paddingVertical: 6,
          paddingHorizontal: 8,
          backgroundColor: "#EFF6FF",
          borderLeftWidth: 4,
          borderLeftColor: "#3B82F6",
          borderRadius: 3,
        }}
      >
        {matched.map((f, i) => {
          const v = shipment?.custom_values?.[f.id] ?? f.value;
          return (
            <Text
              key={f.id || i}
              style={{
                fontSize: fontFor(f.size),
                color: "#1F2937",
                lineHeight: 16,
              }}
              numberOfLines={2}
            >
              {f.label ? (
                <Text style={{ fontWeight: "800", color: "#1E40AF" }}>{f.label} </Text>
              ) : null}
              <Text style={{ fontWeight: f.bold !== false ? "700" : "400" }}>{v}</Text>
            </Text>
          );
        })}
      </View>
    );
  }

  // Default: vertical stack of tiny lines (header_top, to_block, from_block, footer_bottom)
  return (
    <View style={{ marginTop: position === "header_top" ? 0 : 4, marginBottom: 4 }}>
      {matched.map((f, i) => {
        const v = shipment?.custom_values?.[f.id] ?? f.value;
        return (
          <Text
            key={f.id || i}
            style={{
              fontSize: fontFor(f.size),
              color: "#1F2937",
              lineHeight: 16,
            }}
            numberOfLines={2}
          >
            {f.label ? (
              <Text style={{ color: "#6B7280", fontWeight: "700" }}>{f.label} </Text>
            ) : null}
            <Text style={{ fontWeight: f.bold !== false ? "800" : "400" }}>{v}</Text>
          </Text>
        );
      })}
    </View>
  );
}


export default function LabelScreen() {
  const router = useRouter();
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
  const [logoRatio, setLogoRatio] = useState<number | null>(null); // naturalW/naturalH
  const [labelFields, setLabelFields] = useState<any>(null);
  const [shipmentTagline, setShipmentTagline] = useState<string>("");
  const [customFields, setCustomFields] = useState<any[]>([]);

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
    load();
  }, [load]);

  const getHtml = () => {
    if (!shipment || !sender) return "";
    const shipments = Array.from({ length: copies }, () => shipment);
    // Auto-switch to wide layout if image natural ratio is wide, regardless of saved setting
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
  };

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
        {/* Preview Card — NEW 3-row layout: fixed header, flexible middle, fixed footer with barcode */}
        <View style={styles.preview} testID="label-preview">
          {/* Custom field slot: header_top (above brand line) */}
          <CfSlot fields={customFields} position="header_top" shipment={shipment} />
          {/* TOP (fixed) */}
          <View style={styles.previewHdr}>
            <View style={{ flex: 1, minWidth: 0 }}>
              {brand.logo_base64 && preferLogo ? (
                (() => {
                  // Auto-detect "wide" if natural image ratio > 2:1 OR user chose wide
                  const isWide = logoShape === "wide" || (logoRatio !== null && logoRatio >= 2.2);
                  return (
                    <Image
                      source={{
                        uri: brand.logo_base64.startsWith("data:")
                          ? brand.logo_base64
                          : `data:image/png;base64,${brand.logo_base64}`,
                      }}
                      onLoad={(e: any) => {
                        const src: any = e?.nativeEvent?.source;
                        if (src?.width && src?.height) {
                          setLogoRatio(src.width / src.height);
                        } else if (typeof Image !== "undefined" && e?.target?.naturalWidth) {
                          setLogoRatio(e.target.naturalWidth / e.target.naturalHeight);
                        }
                      }}
                      style={
                        isWide
                          ? { width: "100%", height: 36, resizeMode: "contain", alignSelf: "flex-start" }
                          : { width: 44, height: 44, resizeMode: "contain", alignSelf: "flex-start" }
                      }
                    />
                  );
                })()
              ) : (
                <Text style={styles.previewCourier} numberOfLines={2}>
                  {brand.name || sender.name || "Your Brand"}
                </Text>
              )}
              {/* Under-logo row: DD · OID (tight spacing) */}
              <View style={styles.brandMeta}>
                <Text style={styles.brandMetaText}>
                  {(() => {
                    const d = new Date(shipment.created_at);
                    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
                    return !isNaN(d.getTime())
                      ? `DD: ${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`
                      : "";
                  })()}
                </Text>
                {!!shipment.order_id && (
                  <Text style={styles.brandMetaText}>OID: {shipment.order_id}</Text>
                )}
              </View>
            </View>
            {shipment.payment_mode === "COD" ? (
              <View style={{ alignItems: "flex-end" }}>
                {(() => {
                  const total = Number(shipment.amount || shipment.cod_amount || 0);
                  const tok = Number((shipment as any).token_amount || 0);
                  const collect = Math.max(0, total - tok);
                  return <Text style={styles.codBadge}>COD ₹{collect}</Text>;
                })()}
                <Text style={styles.paySubText}>via {shipment.courier_name}</Text>
                {(() => {
                  const c = couriers.find((cc) => cc.id === shipment.courier_id || cc.name === shipment.courier_name);
                  const cid = (c as any)?.customer_id?.trim();
                  return (cid && (labelFields?.customer_id !== false))
                    ? <Text style={styles.paySubText}>Cust ID: {cid}</Text> : null;
                })()}
              </View>
            ) : (
              <View style={{ alignItems: "flex-end" }}>
                <Text style={styles.prepaidBadge}>PAID ₹{shipment.amount || 0}</Text>
                <Text style={styles.paySubText}>via {shipment.courier_name}</Text>
                {(() => {
                  const c = couriers.find((cc) => cc.id === shipment.courier_id || cc.name === shipment.courier_name);
                  const cid = (c as any)?.customer_id?.trim();
                  return (cid && (labelFields?.customer_id !== false))
                    ? <Text style={styles.paySubText}>Cust ID: {cid}</Text> : null;
                })()}
              </View>
            )}
          </View>

          {/* MIDDLE (flex) — spotlight DELIVER TO */}
          <View style={[styles.blk, styles.blkReceiver, { marginTop: 10 }]}>
            <Text style={styles.blkTitle}>DELIVER TO</Text>
            <Text style={styles.blkName}>{shipment.customer_name}</Text>
            {(() => {
              const addr = [shipment.address_line1, shipment.address_line2]
                .filter(Boolean).join(", ");
              return addr ? <Text style={styles.blkLine}>{addr}</Text> : null;
            })()}
            <Text style={styles.blkLine}>
              {[shipment.city, shipment.state, shipment.pincode]
                .filter(Boolean)
                .join(", ")}
            </Text>
            {!!shipment.customer_phone && (labelFields?.phone !== false) && (
              <Text style={[styles.blkLine, { fontWeight: "800", marginTop: 4 }]}>
                📞 {shipment.customer_phone}
              </Text>
            )}
            {/* Custom: to_block (inside DELIVER TO box) */}
            <CfSlot fields={customFields} position="to_block" shipment={shipment} />
          </View>

          {/* Shipment Notes — OUTSIDE Deliver-To block, ABOVE meta-row */}
          {!!(shipment as any).shipment_notes && labelFields?.shipment_notes && (
            <View style={styles.shipmentNotesBox}>
              <Text style={styles.shipmentNotesText} numberOfLines={3}>
                <Text style={styles.shipmentNotesLabel}>Notes: </Text>
                {(shipment as any).shipment_notes}
              </Text>
            </View>
          )}
          {/* Custom: notes_area (blue-bordered box) */}
          <CfSlot fields={customFields} position="notes_area" shipment={shipment} />

          {/* META one-line */}
          <View style={styles.metaRow}>
            {!!shipment.weight && (labelFields?.weight !== false) && (
              <Text style={styles.metaText}>Wt: {shipment.weight}</Text>
            )}
            {!!(shipment as any).box_dimensions && labelFields?.box_dimensions && (
              <Text style={styles.metaText}>Box: {(shipment as any).box_dimensions}</Text>
            )}
            {(labelFields?.item !== false) && (
              <Text style={[styles.metaText, { flex: 1 }]} numberOfLines={1}>
                Item: {shipment.items && shipment.items.length > 0
                  ? shipment.items.join(", ")
                  : shipment.item_description || "—"}
              </Text>
            )}
            {/* Custom: meta_row (inline chips) */}
            <CfSlot fields={customFields} position="meta_row" shipment={shipment} />
          </View>

          {/* Token / advance box — only if toggle ON, token > 0, AND payment is COD */}
          {(() => {
            const tok = Number((shipment as any).token_amount || 0);
            const total = Number(shipment.amount || 0);
            if (!labelFields?.token_info || tok <= 0) return null;
            if (shipment.payment_mode !== "COD") return null;
            return (
              <View style={styles.tokenBox}>
                <Text style={styles.tokenLabel}>💰 Paid Advance:</Text>
                <Text style={styles.tokenVal}>₹{tok.toFixed(0)}</Text>
                <Text style={styles.tokenSep}>·</Text>
                <Text style={styles.tokenLabel}>Order Total:</Text>
                <Text style={styles.tokenVal}>₹{total.toFixed(0)}</Text>
              </View>
            );
          })()}

          {/* FOOTER (fixed) — sender brand block + tracking id + barcode, NEVER cut */}
          <View style={styles.footerBlock}>
            <View style={{ alignSelf: "stretch", marginBottom: 4 }}>
              <Text style={styles.senderNameLine} numberOfLines={1}>
                From: <Text style={styles.senderBrand}>{sender.name || "Sender"}</Text>
              </Text>
              {!!shipmentTagline && (
                <Text style={styles.senderTagline} numberOfLines={1}>
                  {shipmentTagline}
                </Text>
              )}
              <Text style={styles.senderAddr} numberOfLines={2}>
                {[
                  sender.address_line1,
                  sender.address_line2,
                  [sender.city, sender.state, sender.pincode].filter(Boolean).join(", "),
                ].filter(Boolean).join(", ")}
                {showContact && sender.phone ? ` · 📞 ${sender.phone}` : ""}
              </Text>
              {/* Custom: from_block (inside FROM sender area) */}
              <CfSlot fields={customFields} position="from_block" shipment={shipment} />
            </View>
            <Text style={styles.trackId}>{shipment.tracking_id}</Text>
            <View style={{ width: "100%", paddingHorizontal: 8, alignSelf: "center" }}>
              <BarcodePreview value={shipment.tracking_id} height={46} />
            </View>
            {/* Custom: footer_bottom (very bottom, below barcode) */}
            <CfSlot fields={customFields} position="footer_bottom" shipment={shipment} />
          </View>
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
    alignItems: "flex-start",
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
  notesLine: {
    fontSize: 11,
    color: "#475569",
    fontStyle: "italic",
    marginTop: 4,
  },
  shipmentNotesBox: {
    marginTop: 8,
    paddingVertical: 6,
    paddingHorizontal: 8,
    backgroundColor: "#FFFBEB",
    borderLeftWidth: 4,
    borderLeftColor: "#F59E0B",
    borderRadius: 3,
  },
  shipmentNotesText: {
    fontSize: 11,
    color: "#1F2937",
    lineHeight: 15,
  },
  shipmentNotesLabel: {
    fontWeight: "800",
    color: "#B45309",
    fontSize: 10.5,
  },
  tokenBox: {
    marginTop: 8,
    padding: 8,
    borderRadius: 6,
    backgroundColor: "#F8FAFC",
    borderWidth: 1,
    borderColor: "#94A3B8",
    borderStyle: "dashed",
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 6,
  },
  tokenLabel: {
    fontSize: 10,
    color: "#475569",
    fontWeight: "600",
  },
  tokenVal: {
    fontSize: 11,
    color: "#0F172A",
    fontWeight: "800",
  },
  tokenSep: {
    fontSize: 10,
    color: "#CBD5E1",
  },
  brandMeta: {
    flexDirection: "row",
    justifyContent: "flex-start",
    alignItems: "center",
    flexWrap: "wrap",
    marginTop: 6,
    gap: 12,
  },
  brandMetaText: {
    fontSize: 11,
    color: colors.text,
    fontWeight: "700",
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
    flexWrap: "wrap",
    gap: 10,
    marginTop: 10,
    paddingTop: 8,
    borderTopWidth: 1,
    borderTopColor: "#E5E7EB",
  },
  metaText: { fontSize: 11, color: colors.text, fontWeight: "600" },
  footerBlock: {
    marginTop: 12,
    paddingTop: 10,
    borderTopWidth: 2,
    borderTopColor: colors.secondary,
    alignItems: "stretch",
  },
  senderLine: {
    fontSize: 10,
    color: "#4B5563",
    lineHeight: 14,
    marginBottom: 8,
  },
  senderNameLine: {
    fontSize: 11,
    color: "#4B5563",
    lineHeight: 14,
  },
  senderBrand: {
    fontSize: 15,
    fontWeight: "900",
    color: "#0A0A0A",
    letterSpacing: 0.2,
  },
  senderTagline: {
    fontSize: 11,
    color: "#334155",
    fontStyle: "italic",
    fontWeight: "600",
    marginTop: 1,
    letterSpacing: 0.2,
  },
  senderAddr: {
    fontSize: 10,
    color: "#6B7280",
    lineHeight: 13,
    marginTop: 3,
  },
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
    fontSize: 16,
    fontWeight: "800",
    letterSpacing: 2,
    color: colors.text,
    textAlign: "center",
    marginBottom: 6,
    marginTop: 2,
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
