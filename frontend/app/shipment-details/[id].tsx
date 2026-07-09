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

  // Phase F6.2 — Auto-refresh on screen focus so a Shipment Import
  // that finished on another tab (or was just committed) surfaces the
  // freshest imported values here without a manual reload.
  useFocusEffect(
    useCallback(() => {
      loadShipment();
    }, [loadShipment]),
  );

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

        {/* Out for Delivery — postman details + attempt history + 2h alert.
            Phase F4.4 — populated by /api/courier-sync/ingest when an
            India Post "Out for Delivery" SMS is matched. Only shown
            when we have an OFD anchor (avoids empty card noise). */}
        {!!(ship as any).out_for_delivery_at && (
          <Section title="Out for Delivery" icon="car">
            <Row
              label="Out since"
              value={fmtDate((ship as any).out_for_delivery_at)}
            />
            {(() => {
              // Compute hours elapsed for the 2h "still not delivered" hint.
              try {
                const ofdMs = Date.parse((ship as any).out_for_delivery_at);
                const now   = Date.now();
                const hours = Math.max(0, (now - ofdMs) / 3600000);
                const delivered = !!(ship as any).delivered_at || ship.status === "Delivered";
                const overdue = hours >= 2 && !delivered;
                const label = overdue
                  ? `⚠️  ${hours.toFixed(1)}h — please contact courier/customer`
                  : delivered
                    ? `Delivered after ${hours.toFixed(1)}h`
                    : `${hours.toFixed(1)}h elapsed`;
                return <Row label="Elapsed" value={label} />;
              } catch { return null; }
            })()}
            {!!(ship as any).last_delivery_person && (
              <Row
                label="Delivery Person"
                value={(ship as any).last_delivery_person}
              />
            )}
            {!!(ship as any).last_delivery_beat && (
              <Row label="Beat" value={(ship as any).last_delivery_beat} />
            )}
            <Row
              label="Attempts"
              value={String((ship as any).delivery_attempt_count || 1)}
            />
            {/* Attempt history — one line per SMS-parsed attempt. */}
            {Array.isArray((ship as any).out_for_delivery_history) &&
              (ship as any).out_for_delivery_history.length > 1 && (
                <View style={{ marginTop: 6 }}>
                  <Text style={styles.kvKey}>Attempt History</Text>
                  {(ship as any).out_for_delivery_history.map(
                    (h: any, i: number) => (
                      <Text
                        key={`ofd-h-${i}`}
                        style={[styles.kvVal, { marginTop: 4, fontSize: 12 }]}
                        selectable
                      >
                        #{i + 1} · {h.postman_name || "—"}
                        {h.beat ? ` (${h.beat})` : ""} · {h.attempted_on || fmtDate(h.received_at)}
                      </Text>
                    ),
                  )}
                </View>
              )}
          </Section>
        )}

        {/* Timestamps */}
        <Section title="Timeline" icon="time-outline">
          <Row label="Order Date / Time" value={fmtDate(ship.created_at)} />
          {!!(ship as any).imported_booking_at && (
            <Row label="Booking Date / Time" value={fmtDate((ship as any).imported_booking_at)} />
          )}
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
            <Row label="Delivered Date / Time" value={fmtDate(ship.delivered_at)} />
          )}
          {!!(ship as any).modified_at && (
            <Row label="Last Modified Date / Time" value={fmtDate((ship as any).modified_at)} />
          )}
        </Section>

        {/* ────────── Import Data (Phase F6.2) ──────────
            Shown ONLY if this shipment was ever touched by the
            Shipment Import System. Layout is grouped by import type
            so operators can quickly parse the courier-side view
            without conflicting with their original booking data. */}
        {(!!(ship as any).imported_booking_at
          || !!(ship as any).delivery_source
          || !!(ship as any).cod_payment_status
          || (((ship as any).import_validation_alerts || []).length > 0)) && (
          <Section title="Import Data" icon="cloud-download-outline">

            {/* Validation alerts — surface any weight / payment / COD
                mismatches recorded on the LAST Booking Import. */}
            {(((ship as any).import_validation_alerts || []) as any[]).length > 0 && (
              <View style={styles.alertBox}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 }}>
                  <PhIcon name="warning" size={14} color="#B45309" />
                  <Text style={styles.alertTitle}>
                    Validation Alerts ({((ship as any).import_validation_alerts || []).length})
                  </Text>
                </View>
                {(((ship as any).import_validation_alerts || []) as any[]).map((a, i) => (
                  <View key={i} style={styles.alertRow}>
                    <Text style={styles.alertField}>
                      {a.field === "weight"       && "Weight Mismatch"}
                      {a.field === "payment_mode" && "Payment Type Mismatch"}
                      {a.field === "amount"       && "COD Amount Mismatch"}
                      {!["weight", "payment_mode", "amount"].includes(a.field) && `${a.field} mismatch`}
                    </Text>
                    <Text style={styles.alertVal}>
                      Booked: <Text style={styles.alertMono}>{String(a.existing ?? "")}</Text>
                      {"  ·  "}
                      Imported: <Text style={styles.alertMono}>{String(a.imported ?? "")}</Text>
                    </Text>
                  </View>
                ))}
              </View>
            )}

            {/* Booking-import specific rows */}
            {!!(ship as any).imported_booking_at && (
              <>
                <Row label="Booking Date"  value={fmtDate((ship as any).imported_booking_at)} />
                {!!(ship as any).imported_post_office_weight && (
                  <Row label="Post Office Weight"   value={(ship as any).imported_post_office_weight} />
                )}
                {!!(ship as any).imported_courier_payment_mode && (
                  <Row label="Courier Payment Type" value={(ship as any).imported_courier_payment_mode} />
                )}
                {((ship as any).imported_booked_cod_amount ?? 0) > 0 && (
                  <Row label="Booked COD Amount"    value={fmtMoney((ship as any).imported_booked_cod_amount)} />
                )}
              </>
            )}

            {/* Delivery-import specific rows.
                Phase F6.5 — POD Reference now sits inside the Delivery
                block (it's proof-of-delivery, NOT a payment field). */}
            {(ship as any).delivery_source === "imported" && (
              <>
                {!!ship.delivered_at && (
                  <Row label="Delivery Date / Time" value={fmtDate(ship.delivered_at)} />
                )}
                <Row label="Delivery Source" value="Imported" />
                <Row label="Delivery Status" value="Delivered" />
                {!!(ship as any).pod_reference && (
                  <Row label="POD Reference" value={(ship as any).pod_reference} />
                )}
              </>
            )}

            {/* ═════════════════════════════════════════════════════════
                Phase F6.5 — Tracking History (Last Event).
                STRICTLY ONLY courier tracking events (Received /
                Dispatched / Bagged / Hold / Redirected / Returned /
                Delivered / etc.) from the India Post / Courier remit
                file. NO payment or COD data lives inside this block.
                ═════════════════════════════════════════════════════ */}
            {!!(ship as any).last_event && (
              <>
                <View style={styles.subHeaderRow}>
                  <PhIcon name="git-commit" size={13} color="#0369A1" />
                  <Text style={styles.subHeaderTxt}>Tracking History</Text>
                </View>
                <View style={styles.lastEventBox}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 4 }}>
                    <PhIcon name="pulse" size={13} color="#0369A1" />
                    <Text style={styles.lastEventLabel}>Last Event</Text>
                    {!!(ship as any).last_event_category && (
                      <View style={styles.lastEventCatPill}>
                        <Text style={styles.lastEventCatPillTxt}>
                          {(ship as any).last_event_category}
                        </Text>
                      </View>
                    )}
                  </View>
                  <Text style={styles.lastEventTxt} selectable>
                    {(ship as any).last_event}
                  </Text>
                </View>
              </>
            )}

            {/* ═════════════════════════════════════════════════════════
                Phase F6.5 — COD Payment block.
                STRICTLY payment-only fields. Separated from Tracking
                History above with its own subheader so operators can
                never confuse a courier tracking event with a payment
                settlement.
                ═════════════════════════════════════════════════════ */}
            {(ship as any).cod_payment_status === "received" && (
              <>
                <View style={styles.subHeaderRow}>
                  <PhIcon name="cash" size={13} color="#B45309" />
                  <Text style={[styles.subHeaderTxt, { color: "#B45309" }]}>COD Payment</Text>
                </View>
                {!!(ship as any).cod_payment_date && (
                  <Row label="COD Payment Received Date" value={fmtDate((ship as any).cod_payment_date)} />
                )}
                {((ship as any).cod_collected_amount ?? 0) > 0 && (
                  <Row label="COD Amount Received" value={fmtMoney((ship as any).cod_collected_amount)} />
                )}
                {!!(ship as any).cod_payer_name && (
                  <Row label="Received From" value={(ship as any).cod_payer_name} />
                )}
                <Row label="COD Payment Status" value="Received" />
              </>
            )}
          </Section>
        )}

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

  // Phase F6.2 — Import validation alerts
  alertBox: {
    backgroundColor: "#FFFBEB",
    borderLeftWidth: 3,
    borderLeftColor: "#F59E0B",
    borderRadius: 6,
    padding: 10,
    marginBottom: 10,
  },
  alertTitle: { fontSize: 12, fontWeight: "800", color: "#92400E" },
  alertRow: { marginTop: 4 },
  alertField: { fontSize: 12, fontWeight: "700", color: "#78350F" },
  alertVal: { fontSize: 11, color: "#0F172A", marginTop: 1 },
  alertMono: {
    fontFamily: "monospace",
    fontSize: 11,
    fontWeight: "700",
    color: "#B45309",
  },

  // Phase F6.4 — Last Event (verbatim India Post remit text)
  lastEventBox: {
    backgroundColor: "#ECFEFF",
    borderLeftWidth: 3,
    borderLeftColor: "#0EA5E9",
    borderRadius: 6,
    padding: 10,
    marginTop: 8,
  },
  lastEventLabel: {
    fontSize: 12, fontWeight: "800", color: "#0369A1",
    textTransform: "uppercase", letterSpacing: 0.5,
  },
  lastEventCatPill: {
    marginLeft: 4,
    backgroundColor: "#BAE6FD", paddingHorizontal: 6, paddingVertical: 1, borderRadius: 4,
  },
  lastEventCatPillTxt: { fontSize: 9, fontWeight: "800", color: "#075985", letterSpacing: 0.4 },
  lastEventTxt:  { fontSize: 13, color: "#0F172A", lineHeight: 18, marginTop: 2 },

  // Phase F6.5 — Sub-header row that visually separates Tracking
  // History from COD Payment inside the "Import Data" section.
  subHeaderRow: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 14, marginBottom: 6,
    paddingBottom: 4, borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
  },
  subHeaderTxt: {
    fontSize: 12, fontWeight: "800", color: "#0369A1",
    textTransform: "uppercase", letterSpacing: 0.6,
  },

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
