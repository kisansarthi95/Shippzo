/**
 * /app/shipment-details/[id].tsx — Phase-19
 *
 * Read-only shipment details page. Tap any shipment card in the
 * Shipments tab to navigate here. Displays every captured field
 * (tracking, addresses, payment, items, timestamps, status history)
 * in a compact mobile-friendly layout. No edit affordances live on
 * this screen — corrections still happen via the pencil icon on
 * the shipment card.
 *
 * The single CTA at the top opens the existing Print Preview flow
 * (`/label/[id]`) so operators can hop straight from "review" to
 * "print" without losing context.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, ScrollView, StyleSheet, TouchableOpacity,
  ActivityIndicator, Alert, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { Api, type Shipment } from "../../lib/api";
import { scannerBridge } from "../../lib/scannerBridge";
import { colors } from "../../lib/theme";

const fmtDate = (iso?: string | null) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
};

const fmtMoney = (n?: number | null) =>
  n == null ? "—" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

function Row({ label, value, mono = false }: { label: string; value?: string | number | null; mono?: boolean }) {
  const v = value === 0 || value === "0" ? "0" : (value || "");
  return (
    <View style={styles.kvRow}>
      <Text style={styles.kvKey}>{label}</Text>
      <Text style={[styles.kvVal, mono && styles.mono]} selectable numberOfLines={3}>
        {v || "—"}
      </Text>
    </View>
  );
}

function Section({ title, icon, children }: { title: string; icon: string; children: any }) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHeader}>
        <PhIcon name={icon as any} size={16} color={colors.primary} />
        <Text style={styles.sectionTitle}>{title}</Text>
      </View>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

export default function ShipmentDetailsScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router  = useRouter();
  const [ship, setShip] = useState<Shipment | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingTracking, setSavingTracking] = useState(false);

  const loadShipment = useCallback(async () => {
    try {
      const s = await Api.getShipment(String(id));
      setShip(s);
    } catch (e: any) {
      Alert.alert("Couldn't load shipment", e?.message || "Try again.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    loadShipment();
  }, [loadShipment]);

  // Phase-25 — Tracking-ID Gate. When the user scans a tracking ID
  // from this screen (via "Add Tracking ID first" CTA → /scanner),
  // the scanner pushes the value through scannerBridge. On focus
  // return, we consume it, PATCH the shipment, and refresh.
  useFocusEffect(
    useCallback(() => {
      const v = scannerBridge.consume();
      if (!v?.value || !ship || ship.tracking_id) return;
      (async () => {
        setSavingTracking(true);
        try {
          await Api.updateShipment(ship.id, { tracking_id: v.value });
          await loadShipment();
        } catch (e: any) {
          Alert.alert(
            "Couldn't save tracking",
            e?.response?.data?.detail || e?.message || "Try again."
          );
        } finally {
          setSavingTracking(false);
        }
      })();
    }, [ship, loadShipment])
  );

  const openScanner = useCallback(() => {
    router.push(
      `/scanner?returnTo=shipment-details&id=${encodeURIComponent(String(id))}` as any
    );
  }, [router, id]);

  const promptManualTracking = useCallback(() => {
    if (!ship) return;
    // Prompt-style native picker is not available cross-platform —
    // navigate to the existing edit screen for the typed-flow. The
    // tracking ID input there is wired to PUT /api/shipments/{id}.
    router.push({
      pathname: "/(tabs)/add",
      params: { edit_id: ship.id },
    } as any);
  }, [router, ship]);

  if (loading) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.center}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      </SafeAreaView>
    );
  }
  if (!ship) {
    return (
      <SafeAreaView style={styles.safe} edges={["top"]}>
        <View style={styles.center}>
          <Text style={styles.empty}>Shipment not found.</Text>
        </View>
      </SafeAreaView>
    );
  }

  const fullAddress = [
    ship.address_line1, ship.address_line2,
    ship.city, ship.state, ship.pincode,
  ].filter(Boolean).join(", ");
  const itemsTxt = (ship.items || []).join(", ")
    || (ship as any).item_description || "—";

  // Phase-25 — Tracking gate. When the shipment has no tracking_id
  // we hide the Print Preview link entirely and surface a prominent
  // "Add Tracking ID first" CTA in its place, so the operator can
  // either scan or type without leaving the screen.
  const hasTracking = !!(ship.tracking_id || "").trim();

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      {/* Header bar */}
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} hitSlop={8} testID="back-btn">
          <PhIcon name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <View style={{ flex: 1, marginLeft: 8 }}>
          <Text style={styles.headerTitle} numberOfLines={1}>
            Shipment Details
          </Text>
          {hasTracking ? (
            <Text style={styles.headerSub} numberOfLines={1}>
              {ship.tracking_id}
            </Text>
          ) : (
            <Text style={[styles.headerSub, styles.headerSubMissing]} numberOfLines={1}>
              Tracking ID missing
            </Text>
          )}
        </View>
        {hasTracking ? (
          <TouchableOpacity
            testID="print-preview-btn"
            style={styles.printBtn}
            onPress={() => router.push(`/label/${ship.id}` as any)}
            activeOpacity={0.85}
          >
            <PhIcon name="print-outline" size={16} color="#fff" />
            <Text style={styles.printBtnTxt}>Print Preview</Text>
          </TouchableOpacity>
        ) : (
          <TouchableOpacity
            testID="print-preview-btn-disabled"
            style={[styles.printBtn, styles.printBtnDisabled]}
            onPress={() =>
              Alert.alert(
                "Tracking ID required",
                "Please add a tracking ID before printing the label.",
                [
                  { text: "Cancel", style: "cancel" },
                  { text: "Scan now", onPress: openScanner },
                ]
              )
            }
            activeOpacity={0.85}
          >
            <PhIcon name="print-outline" size={16} color="#fff" />
            <Text style={styles.printBtnTxt}>Print</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* Tracking-gate CTA — shown only when no tracking is set */}
      {!hasTracking ? (
        <View style={styles.trackingCtaCard}>
          <View style={{ flex: 1 }}>
            <Text style={styles.trackingCtaTitle}>Add Tracking ID first</Text>
            <Text style={styles.trackingCtaSub}>
              Print labels are disabled until a tracking ID is added to this
              shipment.
            </Text>
          </View>
          <TouchableOpacity
            testID="tracking-cta-scan"
            style={styles.trackingCtaScanBtn}
            onPress={openScanner}
            activeOpacity={0.85}
            disabled={savingTracking}
          >
            {savingTracking ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <PhIcon name="scan-outline" size={18} color="#fff" />
            )}
            <Text style={styles.trackingCtaScanTxt}>
              {savingTracking ? "Saving…" : "Scan"}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity
            testID="tracking-cta-type"
            style={styles.trackingCtaTypeBtn}
            onPress={promptManualTracking}
            activeOpacity={0.85}
            disabled={savingTracking}
          >
            <PhIcon name="create-outline" size={18} color={colors.primary} />
          </TouchableOpacity>
        </View>
      ) : null}

      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 40 }}>
        {/* Status chip */}
        <View style={styles.statusRow}>
          <View style={styles.statusChip}>
            <Text style={styles.statusChipTxt}>{(ship.status || "—").toUpperCase()}</Text>
          </View>
          {(ship as any).is_modified ? (
            <View style={[styles.statusChip, { backgroundColor: "#FEF3C7", borderColor: "#FCD34D" }]}>
              <Text style={[styles.statusChipTxt, { color: "#92400E" }]}>MODIFIED</Text>
            </View>
          ) : null}
        </View>

        {/* IDs */}
        <Section title="Identifiers" icon="barcode-outline">
          {hasTracking ? (
            <Row label="Tracking Number" value={ship.tracking_id} mono />
          ) : (
            <View style={styles.kvRow}>
              <Text style={styles.kvKey}>Tracking Number</Text>
              <TouchableOpacity
                testID="tracking-row-add"
                style={styles.kvCtaRow}
                onPress={openScanner}
                activeOpacity={0.7}
              >
                <Text style={styles.kvCtaTxt}>Add Tracking ID first</Text>
                <PhIcon name="scan-outline" size={16} color={colors.primary} />
              </TouchableOpacity>
            </View>
          )}
          <Row label="Shipment ID"     value={ship.id} mono />
          <Row label="Master Order ID" value={ship.master_order_id} mono />
          <Row label="Order ID"        value={ship.order_id} mono />
        </Section>

        {/* Customer */}
        <Section title="Customer" icon="person-outline">
          <Row label="Name"           value={ship.customer_name} />
          <TouchableOpacity
            activeOpacity={0.7}
            onPress={() => ship.customer_phone && Linking.openURL(`tel:${ship.customer_phone}`)}
          >
            <Row label="Mobile Number" value={ship.customer_phone} mono />
          </TouchableOpacity>
          {!!ship.customer_alt_phone && (
            <Row label="Alt. Phone"    value={ship.customer_alt_phone} mono />
          )}
          {!!ship.customer_email && (
            <Row label="Email"         value={ship.customer_email} />
          )}
          {!!ship.customer_gstin && (
            <Row label="GSTIN"         value={ship.customer_gstin} mono />
          )}
        </Section>

        {/* Address */}
        <Section title="Address" icon="location-outline">
          <Row label="Full Address"  value={fullAddress} />
          <Row label="City"          value={ship.city} />
          <Row label="State"         value={ship.state} />
          <Row label="Pincode"       value={ship.pincode} mono />
          {!!(ship as any).taluka && (
            <Row label="Taluka"      value={(ship as any).taluka} />
          )}
          {!!(ship as any).district && (
            <Row label="District"    value={(ship as any).district} />
          )}
        </Section>

        {/* Shipment */}
        <Section title="Shipment" icon="cube-outline">
          <Row label="Courier"          value={ship.courier_name} />
          <Row label="Payment Mode"     value={ship.payment_mode} />
          {ship.payment_mode === "COD" ? (
            // Phase-31 canonical math — Total Order Value = ship.amount
            // (verbatim from form), COD to Collect = ship.cod_amount
            // (already `max(0, amount − token)` on the backend). NO
            // re-adding the token here; the DB row carries the right
            // numbers so we just render them as-is. This avoids the
            // Phase-30 double-counting where we were ADDING the token
            // back into a value that was supposed to be the gross total.
            Number(ship.token_amount || 0) > 0 ? (
              <>
                <Row
                  label="Total Order Value"
                  value={fmtMoney(ship.amount || 0)}
                />
                <Row
                  label="Token / Advance"
                  value={fmtMoney(ship.token_amount || 0)}
                />
                <Row
                  label="COD to Collect"
                  value={fmtMoney(ship.cod_amount || 0)}
                />
              </>
            ) : (
              <>
                <Row
                  label="Total Order Value"
                  value={fmtMoney(ship.amount || 0)}
                />
                <Row
                  label="COD to Collect"
                  value={fmtMoney(ship.cod_amount || ship.amount || 0)}
                />
              </>
            )
          ) : (
            <Row label="Amount"         value={fmtMoney(ship.amount)} />
          )}
          {/*
            Standalone Token / Advance row — kept for non-COD orders
            (e.g. Prepaid with a partial advance). For COD orders the
            token is shown above inside the 3-row breakdown so we omit
            the standalone copy to avoid duplication.
          */}
          {!!ship.token_amount && ship.payment_mode !== "COD" && (
            <Row label="Token / Advance" value={fmtMoney(ship.token_amount)} />
          )}
          <Row label="Items"            value={itemsTxt} />
          {!!ship.weight && (
            <Row label="Weight"         value={ship.weight} />
          )}
          {!!ship.box_dimensions && (
            <Row label="Box Dimensions" value={ship.box_dimensions} />
          )}
          {!!ship.variant_name && (
            <Row label="Package Variant" value={ship.variant_name} />
          )}
          {!!ship.category && (
            <Row label="Category"       value={ship.category} />
          )}
        </Section>

        {/* Timestamps */}
        <Section title="Timeline" icon="time-outline">
          <Row label="Order Date / Time" value={fmtDate(ship.created_at)} />
          {!!(ship as any).processing_started_at && (
            <Row label="Processing Started" value={fmtDate((ship as any).processing_started_at)} />
          )}
          {!!(ship as any).dispatched_at && (
            <Row label="Ready to Ship at"   value={fmtDate((ship as any).dispatched_at)} />
          )}
          {!!(ship as any).shipped_at && (
            <Row label="Shipped at"         value={fmtDate((ship as any).shipped_at)} />
          )}
          {!!ship.delivered_at && (
            <Row label="Delivered at"       value={fmtDate(ship.delivered_at)} />
          )}
          {!!(ship as any).modified_at && (
            <Row label="Last Modified at"   value={fmtDate((ship as any).modified_at)} />
          )}
        </Section>

        {/* Notes */}
        {(!!ship.shipment_notes || !!(ship as any).admin_notes) && (
          <Section title="Notes" icon="document-text-outline">
            {!!ship.shipment_notes && (
              <Row label="Shipment Notes" value={ship.shipment_notes} />
            )}
            {!!(ship as any).admin_notes && (
              <Row label="Admin Notes"    value={(ship as any).admin_notes} />
            )}
          </Section>
        )}

        {/* Confirmation / Feedback / Return — if any */}
        {((ship as any).confirmation_status ||
          (ship as any).feedback ||
          (ship as any).return_status ||
          (ship as any).cancel_reason) && (
          <Section title="Status Details" icon="chatbubble-ellipses-outline">
            {!!(ship as any).confirmation_status && (
              <Row label="Delivery Confirmation" value={(ship as any).confirmation_status} />
            )}
            {!!(ship as any).last_confirmation_reply && (
              <Row label="Customer Reply" value={(ship as any).last_confirmation_reply} />
            )}
            {!!(ship as any).feedback && (
              <Row label="Feedback"       value={(ship as any).feedback} />
            )}
            {!!(ship as any).return_status && (
              <Row label="Return Status"  value={(ship as any).return_status} />
            )}
            {!!(ship as any).cancel_reason && (
              <Row label="Cancel Reason"  value={(ship as any).cancel_reason} />
            )}
          </Section>
        )}

        {/* Custom values — any per-shipment custom field captures */}
        {ship.custom_values && Object.keys(ship.custom_values).length > 0 && (
          <Section title="Custom Fields" icon="layers-outline">
            {Object.entries(ship.custom_values).map(([k, v]) => (
              <Row key={k} label={k} value={String(v ?? "")} />
            ))}
          </Section>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  empty: { color: colors.textMuted, fontSize: 14 },

  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  headerTitle: { fontSize: 16, fontWeight: "800", color: colors.text },
  headerSub:   { fontSize: 11, color: colors.textMuted, marginTop: 1, fontWeight: "600" },

  printBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 8,
  },
  printBtnTxt: { color: "#fff", fontWeight: "800", fontSize: 12 },

  statusRow: { flexDirection: "row", gap: 8, marginBottom: 12 },
  statusChip: {
    paddingHorizontal: 10, paddingVertical: 5,
    borderRadius: 6, borderWidth: 1,
    backgroundColor: "#EEE9FF", borderColor: "#DAD0FF",
  },
  statusChipTxt: { fontSize: 11, fontWeight: "800", letterSpacing: 0.5, color: "#4B3FCF" },

  section: {
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginBottom: 12,
    overflow: "hidden",
  },
  sectionHeader: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 14, paddingVertical: 10,
    backgroundColor: "#F9FAFB",
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
  },
  sectionTitle: { fontSize: 13, fontWeight: "800", color: colors.text, letterSpacing: 0.3 },
  sectionBody: { paddingHorizontal: 14, paddingVertical: 6 },

  kvRow: {
    flexDirection: "row",
    paddingVertical: 8,
    borderBottomWidth: 1, borderBottomColor: "#F1F5F9",
    gap: 12,
  },
  kvKey: {
    fontSize: 12, color: colors.textMuted,
    fontWeight: "600", width: 130,
  },
  kvVal: {
    flex: 1,
    fontSize: 13, color: colors.text,
    fontWeight: "600",
  },
  mono: { fontFamily: "monospace", letterSpacing: 0.3 },

  // Phase-25 — Tracking gate styles
  headerSubMissing: { color: "#DC2626", fontWeight: "700" },
  printBtnDisabled: {
    backgroundColor: "#9CA3AF",
    opacity: 0.6,
  },
  trackingCtaCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: "#FEF3C7",
    borderBottomWidth: 1,
    borderBottomColor: "#FCD34D",
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  trackingCtaTitle: {
    fontSize: 14,
    fontWeight: "800",
    color: "#92400E",
  },
  trackingCtaSub: {
    fontSize: 11,
    color: "#92400E",
    marginTop: 2,
    lineHeight: 15,
  },
  trackingCtaScanBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 8,
  },
  trackingCtaScanTxt: {
    color: "#fff",
    fontSize: 12,
    fontWeight: "800",
  },
  trackingCtaTypeBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: colors.primary,
  },
  kvCtaRow: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  kvCtaTxt: {
    flex: 1,
    fontSize: 13,
    color: colors.primary,
    fontWeight: "700",
  },
});
