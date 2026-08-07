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
  ActivityIndicator, Alert, Linking, TextInput, Platform, Modal,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { Api, type Shipment } from "../../lib/api";
import { scannerBridge } from "../../lib/scannerBridge";
import { colors } from "../../lib/theme";
import CourierSyncListener from "../../modules/courier-sync-listener";

// India Post CBS enums — MUST match backend `_VALID_SERVICES` / `_VALID_TYPES`
// / `_VALID_STATUSES` in routers/complaints.py. Add here + backend in sync.
// Phase F7.3 — official India Post service catalogue (verbatim copy
// of the CBS complaint form dropdown). Order below is the display
// order shown to the operator; the first item is the default.
const SERVICE_OPTIONS = [
  "Speed Post Parcel (Registered/Insured/COD)",
  "Speed Post Letters (Insured/COD)",
  "India Post Parcel- Contractual (Registered/Insured/COD)",
  "India Post Parcel-retail (Registered/Insured/COD)",
  "Letter (Registered/Insured/COD)",
  "Magazine Post",
  "Post Card/Book Post/Periodical Post/Registered Newspapers/Inland Letter Card/Ordinary letter",
  "Tariff /GST Related",
] as const;
const DEFAULT_SERVICE = SERVICE_OPTIONS[0];
const COMPLAINT_TYPE_OPTIONS = [
  "Delay in delivery",
  "Non delivery of article",
  "Abstraction of Contents",
  "Loss of article",
  "Non payment of COD Amount",
  "Damage of Article",
  "Fake/Non-updation of delivery remarks/Scans",
] as const;
const COMPLAINT_STATUS_OPTIONS = [
  "Open",
  "In Progress",
  "Resolved",
  "Closed",
] as const;

// India Post's only accepted date format on their CBS bulk complaint
// uploader is DD-MM-YYYY. We normalise on both sides (backend also
// normalises) so the operator never sees a rejection due to formatting.
const toDDMMYYYY = (d: Date): string => {
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `${dd}-${mm}-${d.getFullYear()}`;
};

const parseDDMMYYYY = (s: string | undefined): Date | null => {
  if (!s) return null;
  const m = /^(\d{1,2})[\/-](\d{1,2})[\/-](\d{4})$/.exec(s.trim());
  if (!m) return null;
  const d = Number(m[1]), mo = Number(m[2]) - 1, y = Number(m[3]);
  const dt = new Date(y, mo, d);
  return isNaN(dt.getTime()) ? null : dt;
};

// ── Phase F7.4 — Description validation helpers ─────────────────
//
// "English" = only characters in the Basic Latin + Latin-1 Supplement
// blocks plus common punctuation / whitespace. Any Devanagari,
// Gujarati, Bengali, Tamil, Kannada, Malayalam, Telugu, Punjabi,
// Oriya, or CJK glyph → NOT English. This is a fast client-side
// gate; the backend AI Shorten call handles the actual translation.
const NON_ENGLISH_RE = /[^\x00-\x7F\u00A0-\u00FF]/;
const CHAR_LIMIT = 250;

function validateDescription(text: string): string {
  const t = (text || "").trim();
  const notEnglish = NON_ENGLISH_RE.test(t);
  const tooLong   = t.length > CHAR_LIMIT;
  if (notEnglish && tooLong) {
    return `Your description is not in English and exceeds ${CHAR_LIMIT} characters (currently ${t.length}).`;
  }
  if (notEnglish) {
    return "Your description is not in English.";
  }
  if (tooLong) {
    return `Your description exceeds ${CHAR_LIMIT} characters (currently ${t.length}).`;
  }
  return "";
}

const fmtDate = (iso?: string | null) => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
};

// Phase F8.0 — SMS-extracted event stamps come in TWO shapes:
//   "2026-07-10"           (date only — most OFD SMS carry no clock)
//   "2026-07-10T20:42:19"  (full datetime — booking / delivered SMS)
// Render the time ONLY when it exists so date-only stamps don't show
// a misleading "12:00 AM".
const fmtEventStamp = (iso?: string | null) => {
  if (!iso) return "";
  try {
    const hasTime = String(iso).includes("T") || String(iso).includes(":");
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    if (!hasTime) {
      return d.toLocaleDateString(undefined, {
        day: "numeric", month: "short", year: "numeric",
      });
    }
    return d.toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return String(iso); }
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
  // Phase F10.3 — "Raise Inquiry" prefill state. Bumped from the
  // payment-discrepancy alert row; the IndiaPostComplaintSection
  // useEffect reacts to a non-empty value, expands itself and seeds
  // the description with the auto-generated mismatch message.
  const [inquiryPrefill, setInquiryPrefill] = useState<string>("");
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

  // Phase F8.0 — refresh the moment an SMS auto-sync event updates a
  // shipment (Android native listener fires "onIngestResult" after a
  // successful backend ingest). No-op on iOS / web / Expo Go.
  useEffect(() => {
    const sub = CourierSyncListener.addIngestResultListener(() => {
      loadShipment();
    });
    return () => {
      try { sub?.remove(); } catch { /* no-op */ }
    };
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
            {!!(ship as any).last_delivery_attempt_at && (
              <Row
                label="Attempt Date / Time"
                value={fmtEventStamp((ship as any).last_delivery_attempt_at)}
              />
            )}
            <Row
              label="Attempts"
              value={String((ship as any).delivery_attempt_count || 1)}
            />
            {/* Attempt history — one entry per SMS-parsed attempt.
                Phase F8.0 — shows attempt date+time AND the complete
                original OFD SMS (raw_message) per attempt. */}
            {Array.isArray((ship as any).out_for_delivery_history) &&
              (ship as any).out_for_delivery_history.length >= 1 && (
                <View style={{ marginTop: 6 }}>
                  <Text style={styles.kvKey}>Attempt History</Text>
                  {(ship as any).out_for_delivery_history.map(
                    (h: any, i: number) => (
                      <View key={`ofd-h-${i}`} style={{ marginTop: 6 }}>
                        <Text
                          style={[styles.kvVal, { fontSize: 12 }]}
                          selectable
                        >
                          #{i + 1} · {h.postman_name || "—"}
                          {h.beat ? ` (${h.beat})` : ""} · {
                            fmtEventStamp(h.attempted_on) || fmtDate(h.received_at)
                          }
                        </Text>
                        {!!h.raw_message && (
                          <Text
                            style={{
                              fontSize: 11,
                              color: "#64748B",
                              marginTop: 2,
                              lineHeight: 15,
                            }}
                            selectable
                          >
                            {h.raw_message}
                          </Text>
                        )}
                      </View>
                    ),
                  )}
                </View>
              )}
          </Section>
        )}

        {/* Timestamps */}
        <Section title="Timeline" icon="time-outline">
          <Row label="Order Date / Time" value={fmtDate(ship.created_at)} />
          {/* Phase F8.0 — booking_date is set by Booking import,
              Delivery-import mapper, or the Booking SMS (whichever
              lands first — only-if-empty rule). */}
          {!!(ship as any).booking_date && (
            <Row label="Booking Date / Time" value={fmtEventStamp((ship as any).booking_date)} />
          )}
          {!(ship as any).booking_date && !!(ship as any).imported_booking_at && (
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
                {(((ship as any).import_validation_alerts || []) as any[]).map((a, i) => {
                  const isPaymentField = a.field === "cod_amount" || a.field === "amount";
                  return (
                  <View key={i} style={styles.alertRow}>
                    <Text style={styles.alertField}>
                      {a.field === "weight"       && "Weight Mismatch"}
                      {a.field === "payment_mode" && "Payment Type Mismatch"}
                      {a.field === "amount"       && "COD Amount Mismatch"}
                      {a.field === "cod_amount"   && "Payment Discrepancy"}
                      {!["weight", "payment_mode", "amount", "cod_amount"].includes(a.field) && `${a.field} mismatch`}
                    </Text>
                    <Text style={styles.alertVal}>
                      {a.field === "cod_amount" ? "Expected" : "Booked"}:{" "}
                      <Text style={styles.alertMono}>{String(a.existing ?? "")}</Text>
                      {"  ·  "}
                      {a.field === "cod_amount" ? "Received" : "Imported"}:{" "}
                      <Text style={styles.alertMono}>{String(a.imported ?? "")}</Text>
                    </Text>
                    {isPaymentField && (
                      // Phase F10.3 — Raise Inquiry: prefills the
                      // India Post Complaint description with the
                      // discrepancy message and scrolls into view.
                      <TouchableOpacity
                        style={styles.raiseInquiryBtn}
                        testID={`raise-inquiry-btn-${i}`}
                        onPress={() => {
                          const msg =
                            `Payment discrepancy found. ` +
                            `Expected: ₹${a.existing ?? "?"}, ` +
                            `Received: ₹${a.imported ?? "?"}. ` +
                            `Please clarify the difference.`;
                          // Bump with a nonce so a second click on the
                          // same alert re-triggers the useEffect even
                          // if the string is identical.
                          setInquiryPrefill(`${msg}`);
                        }}
                      >
                        <PhIcon name="megaphone-outline" size={13} color="#B91C1C" />
                        <Text style={styles.raiseInquiryTxt}>Raise Inquiry</Text>
                      </TouchableOpacity>
                    )}
                  </View>
                  );
                })}
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

        {/* ────────── Phase F7.0 — India Post Complaint ──────────
            Compact inline form so operators can raise / update / clear
            a complaint against the shipment in-place. Serial No is
            never edited manually — it's generated per-file at export
            time. Order No + Article No / Tracking are auto-derived. */}
        <IndiaPostComplaintSection
          ship={ship}
          onSaved={loadShipment}
          prefillDescription={inquiryPrefill}
        />

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

// ─── Phase F7.0 — India Post Complaint sub-component ──────────────
//
// Kept inside the same file as the parent screen (not in /components)
// because it is 100% coupled to the shipment context here — it never
// mounts standalone. Splitting it out would just add an import layer
// for zero reuse benefit.
function IndiaPostComplaintSection({
  ship,
  onSaved,
  prefillDescription,
}: {
  ship: Shipment;
  onSaved: () => Promise<void> | void;
  // Phase F10.3 — Optional description prefill used by the
  // "Raise Inquiry" button on payment-discrepancy alerts. When this
  // prop changes to a non-empty value we expand the form and seed
  // the description with the auto-generated mismatch message so the
  // operator can review + submit in one tap.
  prefillDescription?: string;
}) {
  const existing = ship as any;
  const [expanded, setExpanded] = useState<boolean>(!!existing.complaint_created);
  const [bookingDate, setBookingDate] = useState<string>(
    existing.complaint_booking_date || "",
  );
  const [serviceName, setServiceName] = useState<string>(
    existing.complaint_service_name || DEFAULT_SERVICE,
  );
  const [serviceOther, setServiceOther] = useState<string>(
    existing.complaint_service_name_other || "",
  );
  const [complaintType, setComplaintType] = useState<string>(
    existing.complaint_type || "Delay in delivery",
  );
  const [description, setDescription] = useState<string>(
    existing.complaint_description || "",
  );
  // Phase F10.3 — React to a `prefillDescription` change from the
  // parent's "Raise Inquiry" button. Only replaces the description
  // when it's empty (or when the user hasn't touched the form yet)
  // so we never silently overwrite an operator's in-flight draft.
  React.useEffect(() => {
    const p = (prefillDescription || "").trim();
    if (!p) return;
    setExpanded(true);
    if (!description.trim()) {
      setDescription(p);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefillDescription]);
  const [complaintStatus, setComplaintStatus] = useState<string>(
    existing.complaint_status || "Open",
  );
  const [saving, setSaving] = useState(false);
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [manualDateOpen, setManualDateOpen] = useState(false);
  const [manualDate, setManualDate] = useState<string>(bookingDate);
  // Phase F7.4 — AI Shorten notification state.
  //   validationOpen : the "Description must be English + ≤250 chars"
  //                    notification modal is visible.
  //   validationIssue: precomputed short message shown at the top of
  //                    the modal.  Kept in state so re-opening the
  //                    modal after a failed AI call preserves the
  //                    reason without recomputing.
  //   aiBusy         : true while the LLM call is in flight.
  const [validationOpen, setValidationOpen] = useState(false);
  const [validationIssue, setValidationIssue] = useState<string>("");
  const [aiBusy, setAiBusy] = useState(false);

  const isCreated = !!existing.complaint_created;

  // Booking Date UX: The native date picker opens on tap. If the
  // operator cancels the picker, we fall back to a manual DD-MM-YYYY
  // TextInput popup so they can type the exact date they intend to
  // book (parcel may go out on a different date than it was created).
  const openDatePicker = () => {
    // Try native DateTimePicker; fall back to manual entry modal.
    setShowDatePicker(true);
  };
  const onNativeDateChange = (event: any, selected?: Date) => {
    // Android fires "dismissed" when the user hits back / outside tap.
    setShowDatePicker(false);
    if (Platform.OS === "android" && event?.type === "dismissed") {
      // Cancelled → open manual entry so they can type instead.
      setManualDate(bookingDate);
      setManualDateOpen(true);
      return;
    }
    if (selected) setBookingDate(toDDMMYYYY(selected));
  };
  const saveManualDate = () => {
    const t = manualDate.trim();
    if (!t) { setBookingDate(""); setManualDateOpen(false); return; }
    const parsed = parseDDMMYYYY(t);
    if (!parsed) {
      Alert.alert(
        "Invalid date",
        "Please enter the date as DD-MM-YYYY (e.g. 05-07-2026).",
      );
      return;
    }
    setBookingDate(toDDMMYYYY(parsed));
    setManualDateOpen(false);
  };

  const onSave = async () => {
    if (saving) return;
    if (!bookingDate) {
      Alert.alert("Booking Date required", "Please select the booking date first.");
      return;
    }
    if (!complaintType) {
      Alert.alert("Complaint Type required", "Please choose a complaint type.");
      return;
    }
    const trimmed = description.trim();
    if (!trimmed) {
      Alert.alert("Description required", "Please describe the complaint.");
      return;
    }
    // ── Phase F7.4 — Description validation (English + ≤250) ────
    // Both conditions must be satisfied. If EITHER fails we surface
    // the AI Shorten notification modal instead of creating the
    // complaint (per user spec).
    const issue = validateDescription(trimmed);
    if (issue) {
      setValidationIssue(issue);
      setValidationOpen(true);
      return;
    }
    setSaving(true);
    try {
      await Api.saveComplaint(ship.id, {
        booking_date: bookingDate,
        service_name: serviceName,
        // Phase F7.3 — "Other" removed; still send the field to keep
        // the API contract unchanged (backend ignores empty strings).
        service_name_other: "",
        complaint_type: complaintType,
        complaint_description: trimmed,
        complaint_status: complaintStatus,
      });
      await onSaved();
      Alert.alert(
        "Complaint saved",
        "This shipment is now flagged for the India Post bulk complaint export.",
      );
    } catch (e: any) {
      Alert.alert(
        "Couldn't save complaint",
        e?.response?.data?.detail || e?.message || "Try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!isCreated) return;
    Alert.alert(
      "Delete complaint?",
      "This will remove the complaint from this shipment. The export will no longer include it.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete", style: "destructive",
          onPress: async () => {
            try {
              await Api.deleteComplaint(ship.id);
              await onSaved();
              // Reset local form state so the section returns to
              // pristine "create new complaint" mode.
              setBookingDate("");
              setServiceName(DEFAULT_SERVICE);
              setServiceOther("");
              setComplaintType("Delay in delivery");
              setDescription("");
              setComplaintStatus("Open");
              setExpanded(false);
            } catch (e: any) {
              Alert.alert(
                "Couldn't delete complaint",
                e?.response?.data?.detail || e?.message || "Try again.",
              );
            }
          },
        },
      ],
    );
  };

  // ── Phase F7.4 — AI Shorten flow ─────────────────────────────
  //
  // Run the same LLM rewrite the user requested. On success, replace
  // the description in-place with the returned English text so the
  // operator can immediately tap Create again — this time it will
  // pass validation and save without any prompt.
  const runAiShorten = async () => {
    if (aiBusy) return;
    setAiBusy(true);
    try {
      const raw = description.trim();
      if (!raw) {
        Alert.alert(
          "Nothing to shorten",
          "Please type a complaint description first.",
        );
        return;
      }
      const resp = await Api.aiShortenComplaint(raw);
      setDescription(resp.rewritten);
      setValidationOpen(false);
      // Non-blocking confirmation of the credit spend so the user
      // knows their wallet was debited (0.5 per successful click).
      Alert.alert(
        "AI Shorten Complete",
        `${resp.chars} characters · English · 0.5 AI Credits used.\n` +
        `Balance: ${resp.balance_after.toFixed(2)} credits.\n\n` +
        `Tap "Create Complaint" to save.`,
      );
    } catch (e: any) {
      // Backend guarantees: NO credit deducted on any failure path.
      // The 402 path is a special-case (empty wallet) that we surface
      // differently — user can top up.
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.message ||
                     "AI Shorten failed. Please try again.";
      Alert.alert(
        status === 402 ? "Not enough AI credits" : "AI Shorten failed",
        `${detail}\n\nNo credits were deducted.`,
      );
    } finally {
      setAiBusy(false);
    }
  };


  // Lazy-load DateTimePicker to avoid Metro trying to resolve it on web.
  const DateTimePicker = (() => {
    if (Platform.OS === "web") return null;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      return require("@react-native-community/datetimepicker").default;
    } catch { return null; }
  })();

  return (
    <View style={cpStyles.section}>
      <TouchableOpacity
        style={cpStyles.header}
        activeOpacity={0.7}
        onPress={() => setExpanded((v) => !v)}
        testID="cp-toggle"
      >
        <PhIcon
          name={isCreated ? "warning" : "megaphone-outline"}
          size={16}
          color={isCreated ? "#DC2626" : colors.primary}
        />
        <Text
          style={[cpStyles.title, isCreated && { color: "#DC2626" }]}
        >
          India Post Complaint
        </Text>
        {isCreated ? (
          <View style={cpStyles.statusPill}>
            <Text style={cpStyles.statusPillTxt}>
              {existing.complaint_status || "Open"}
            </Text>
          </View>
        ) : null}
        <View style={{ flex: 1 }} />
        <PhIcon
          name={expanded ? "chevron-up" : "chevron-down"}
          size={18}
          color="#94A3B8"
        />
      </TouchableOpacity>

      {expanded && (
        <View style={cpStyles.body}>
          {/* Read-only auto-derived rows so operators see exactly what
              will be exported without opening the Excel. */}
          <FormRow label="Order No" value={ship.order_id || "—"} />
          <FormRow
            label="Article No (Tracking)"
            value={ship.tracking_id || "—"}
            mono
          />

          {/* Booking Date */}
          <Text style={cpStyles.fieldLabel}>Booking Date</Text>
          <TouchableOpacity
            style={cpStyles.datePickerBtn}
            onPress={openDatePicker}
            testID="cp-date-picker"
          >
            <PhIcon name="calendar" size={14} color={colors.primary} />
            <Text style={[
              cpStyles.datePickerTxt,
              !bookingDate && { color: "#94A3B8", fontStyle: "italic" },
            ]}>
              {bookingDate || "Tap to select date"}
            </Text>
            <PhIcon name="chevron-forward" size={14} color="#94A3B8" />
          </TouchableOpacity>

          {/* Service Name */}
          <Text style={cpStyles.fieldLabel}>Service Name</Text>
          <View style={cpStyles.chipWrap}>
            {SERVICE_OPTIONS.map((s) => (
              <TouchableOpacity
                key={s}
                onPress={() => setServiceName(s)}
                style={[
                  cpStyles.chip,
                  serviceName === s && cpStyles.chipActive,
                ]}
                testID={`cp-svc-${s}`}
              >
                <Text style={[
                  cpStyles.chipTxt,
                  serviceName === s && cpStyles.chipTxtActive,
                ]}>
                  {s}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Complaint Type */}
          <Text style={cpStyles.fieldLabel}>Complaint Type</Text>
          <View style={cpStyles.chipWrap}>
            {COMPLAINT_TYPE_OPTIONS.map((t) => (
              <TouchableOpacity
                key={t}
                onPress={() => setComplaintType(t)}
                style={[
                  cpStyles.chip,
                  complaintType === t && cpStyles.chipActive,
                ]}
                testID={`cp-type-${t}`}
              >
                <Text style={[
                  cpStyles.chipTxt,
                  complaintType === t && cpStyles.chipTxtActive,
                ]}>
                  {t}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Description */}
          <Text style={cpStyles.fieldLabel}>Description</Text>
          <TextInput
            style={[cpStyles.input, cpStyles.textarea]}
            value={description}
            onChangeText={setDescription}
            placeholder="Describe the complaint (what went wrong, when, and any relevant reference numbers)…"
            placeholderTextColor="#94A3B8"
            multiline
            numberOfLines={4}
            textAlignVertical="top"
            testID="cp-desc"
          />

          {/* Complaint Status (internal — NOT exported) */}
          <Text style={cpStyles.fieldLabel}>
            Complaint Status <Text style={cpStyles.helperInline}>(internal)</Text>
          </Text>
          <View style={cpStyles.chipWrap}>
            {COMPLAINT_STATUS_OPTIONS.map((s) => (
              <TouchableOpacity
                key={s}
                onPress={() => setComplaintStatus(s)}
                style={[
                  cpStyles.chip,
                  complaintStatus === s && cpStyles.chipActive,
                ]}
                testID={`cp-status-${s}`}
              >
                <Text style={[
                  cpStyles.chipTxt,
                  complaintStatus === s && cpStyles.chipTxtActive,
                ]}>
                  {s}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Actions */}
          <View style={cpStyles.actionsRow}>
            {isCreated ? (
              <TouchableOpacity
                style={cpStyles.deleteBtn}
                onPress={onDelete}
                disabled={saving}
                testID="cp-delete"
              >
                <PhIcon name="trash-outline" size={14} color="#DC2626" />
                <Text style={cpStyles.deleteBtnTxt}>Delete</Text>
              </TouchableOpacity>
            ) : null}
            <View style={{ flex: 1 }} />
            <TouchableOpacity
              style={[cpStyles.saveBtn, saving && { opacity: 0.6 }]}
              onPress={onSave}
              disabled={saving}
              testID="cp-save"
            >
              {saving ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <PhIcon
                  name={isCreated ? "save-outline" : "add-circle-outline"}
                  size={14}
                  color="#fff"
                />
              )}
              <Text style={cpStyles.saveBtnTxt}>
                {saving ? "Saving…" : (isCreated ? "Update" : "Create Complaint")}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {/* Native DateTimePicker */}
      {showDatePicker && DateTimePicker && (
        <DateTimePicker
          value={parseDDMMYYYY(bookingDate) || new Date()}
          mode="date"
          display={Platform.OS === "ios" ? "spinner" : "default"}
          maximumDate={new Date()}
          onChange={onNativeDateChange}
        />
      )}

      {/* Manual DD-MM-YYYY entry — used when the native picker isn't
          available (web) or when the user cancels the native picker. */}
      <Modal
        visible={manualDateOpen || (showDatePicker && !DateTimePicker)}
        transparent
        animationType="fade"
        onRequestClose={() => { setManualDateOpen(false); setShowDatePicker(false); }}
      >
        <View style={cpStyles.modalRoot}>
          <View style={cpStyles.modalCard}>
            <Text style={cpStyles.modalTitle}>Booking Date</Text>
            <Text style={cpStyles.modalSub}>
              Enter the date in DD-MM-YYYY format.
            </Text>
            <TextInput
              style={cpStyles.input}
              value={manualDate}
              onChangeText={setManualDate}
              placeholder="DD-MM-YYYY"
              placeholderTextColor="#94A3B8"
              keyboardType="numbers-and-punctuation"
              autoFocus
              testID="cp-manual-date-input"
            />
            <View style={{ flexDirection: "row", gap: 8, marginTop: 12 }}>
              <TouchableOpacity
                style={[cpStyles.modalBtn, cpStyles.modalBtnGhost]}
                onPress={() => { setManualDateOpen(false); setShowDatePicker(false); }}
              >
                <Text style={cpStyles.modalBtnGhostTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[cpStyles.modalBtn, cpStyles.modalBtnPrimary]}
                onPress={saveManualDate}
                testID="cp-manual-date-save"
              >
                <Text style={cpStyles.modalBtnPrimaryTxt}>OK</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* ── Phase F7.4 — AI Shorten notification modal ──────────
          Only appears when Create is attempted with a description
          that is NOT English or exceeds 250 chars (or both). Any
          other failure path uses a plain Alert. */}
      <Modal
        visible={validationOpen}
        transparent
        animationType="fade"
        onRequestClose={() => !aiBusy && setValidationOpen(false)}
      >
        <View style={cpStyles.modalRoot}>
          <View style={cpStyles.aiModalCard}>
            <View style={cpStyles.aiModalHeader}>
              <PhIcon name="alert-circle" size={20} color="#B45309" />
              <Text style={cpStyles.aiModalTitle}>
                Description needs fixing
              </Text>
            </View>
            <Text style={cpStyles.aiModalIssue}>{validationIssue}</Text>

            <View style={cpStyles.aiModalDivider} />

            <Text style={cpStyles.aiModalBody}>
              Use <Text style={cpStyles.aiSparkle}>✨ AI Shorten</Text> to
              automatically convert your complaint into professional
              English and optimize it to 250 characters or less while
              preserving all important complaint details.
            </Text>

            <View style={cpStyles.aiCreditRow}>
              <PhIcon name="cash-outline" size={13} color="#7C3AED" />
              <Text style={cpStyles.aiCreditTxt}>
                AI Shorten Charge: 0.5 AI Credits
              </Text>
            </View>

            <View style={{ flexDirection: "row", gap: 8, marginTop: 14 }}>
              <TouchableOpacity
                style={[cpStyles.modalBtn, cpStyles.modalBtnGhost]}
                onPress={() => setValidationOpen(false)}
                disabled={aiBusy}
                testID="cp-ai-modal-cancel"
              >
                <Text style={cpStyles.modalBtnGhostTxt}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[
                  cpStyles.modalBtn, cpStyles.aiShortenBtn,
                  aiBusy && { opacity: 0.6 },
                ]}
                onPress={runAiShorten}
                disabled={aiBusy}
                testID="cp-ai-shorten"
              >
                {aiBusy ? (
                  <ActivityIndicator size="small" color="#fff" />
                ) : (
                  <Text style={cpStyles.aiShortenTxt}>✨ AI Shorten</Text>
                )}
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function FormRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <View style={cpStyles.formRow}>
      <Text style={cpStyles.formLabel}>{label}</Text>
      <Text
        style={[cpStyles.formValue, mono && { fontFamily: "monospace" }]}
        selectable
      >
        {value}
      </Text>
    </View>
  );
}

const cpStyles = StyleSheet.create({
  section: {
    backgroundColor: "#fff",
    borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    marginBottom: 12,
    overflow: "hidden",
  },
  header: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 14, paddingVertical: 12,
    backgroundColor: "#FEF2F2",
    borderBottomWidth: 1, borderBottomColor: "#FEE2E2",
  },
  title: { fontSize: 13, fontWeight: "800", color: colors.text, letterSpacing: 0.3 },
  statusPill: {
    backgroundColor: "#FCA5A5",
    paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8,
  },
  statusPillTxt: { fontSize: 10, fontWeight: "800", color: "#7F1D1D", letterSpacing: 0.4 },
  body: { paddingHorizontal: 14, paddingVertical: 10 },

  formRow: {
    flexDirection: "row", paddingVertical: 6,
    borderBottomWidth: 1, borderBottomColor: "#F1F5F9", gap: 12,
  },
  formLabel: { fontSize: 12, color: "#64748B", fontWeight: "600", width: 130 },
  formValue: { flex: 1, fontSize: 13, color: colors.text, fontWeight: "600" },

  fieldLabel: {
    fontSize: 12, fontWeight: "700", color: "#334155",
    marginTop: 12, marginBottom: 6, letterSpacing: 0.2,
  },
  helperInline: { fontSize: 10, color: "#94A3B8", fontWeight: "600" },

  datePickerBtn: {
    flexDirection: "row", alignItems: "center", gap: 8,
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 10,
    backgroundColor: "#F8FAFC",
  },
  datePickerTxt: { flex: 1, fontSize: 13, color: colors.text, fontWeight: "600" },

  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  chip: {
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999,
    backgroundColor: "#fff",
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipTxt: { fontSize: 12, fontWeight: "700", color: "#334155" },
  chipTxtActive: { color: "#fff" },

  input: {
    borderWidth: 1, borderColor: "#E5E7EB", borderRadius: 8,
    paddingHorizontal: 12, paddingVertical: 8,
    fontSize: 13, color: colors.text,
    backgroundColor: "#fff",
    marginTop: 6,
  },
  textarea: { minHeight: 80, maxHeight: 180 },

  actionsRow: {
    flexDirection: "row", alignItems: "center", gap: 10,
    marginTop: 14, paddingTop: 12,
    borderTopWidth: 1, borderTopColor: "#F1F5F9",
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: colors.primary,
    paddingHorizontal: 14, paddingVertical: 10,
    borderRadius: 8,
  },
  saveBtnTxt: { color: "#fff", fontSize: 13, fontWeight: "800" },
  deleteBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 8,
    borderRadius: 8, borderWidth: 1, borderColor: "#FCA5A5",
    backgroundColor: "#FEF2F2",
  },
  deleteBtnTxt: { color: "#DC2626", fontSize: 12, fontWeight: "700" },

  modalRoot: {
    flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(0,0,0,0.4)", padding: 24,
  },
  modalCard: {
    backgroundColor: "#fff", borderRadius: 12,
    padding: 18, minWidth: 280, maxWidth: 400, width: "100%",
  },
  modalTitle: { fontSize: 16, fontWeight: "800", color: colors.text, marginBottom: 4 },
  modalSub: { fontSize: 12, color: "#64748B", marginBottom: 6 },
  modalBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 8, alignItems: "center",
  },
  modalBtnGhost: { backgroundColor: "#F1F5F9" },
  modalBtnGhostTxt: { color: colors.text, fontSize: 13, fontWeight: "800" },
  modalBtnPrimary: { backgroundColor: colors.primary },
  modalBtnPrimaryTxt: { color: "#fff", fontSize: 13, fontWeight: "800" },

  // ── Phase F7.4 — AI Shorten notification modal ─────────────
  aiModalCard: {
    backgroundColor: "#fff", borderRadius: 14,
    padding: 18, minWidth: 300, maxWidth: 440, width: "100%",
    borderWidth: 1, borderColor: "#FDE68A",
  },
  aiModalHeader: {
    flexDirection: "row", alignItems: "center", gap: 8,
    marginBottom: 8,
  },
  aiModalTitle: {
    fontSize: 15, fontWeight: "800", color: "#78350F", letterSpacing: 0.3,
  },
  aiModalIssue: {
    fontSize: 13, color: "#92400E", lineHeight: 18,
    backgroundColor: "#FEF3C7",
    paddingHorizontal: 10, paddingVertical: 8,
    borderRadius: 8, marginTop: 4, fontWeight: "600",
  },
  aiModalDivider: {
    height: 1, backgroundColor: "#F1F5F9",
    marginVertical: 12,
  },
  aiModalBody: {
    fontSize: 13, color: colors.text, lineHeight: 19,
  },
  aiSparkle: { fontWeight: "800", color: "#7C3AED" },
  aiCreditRow: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginTop: 10,
    backgroundColor: "#F5F3FF",
    paddingHorizontal: 10, paddingVertical: 6,
    borderRadius: 8, alignSelf: "flex-start",
    borderWidth: 1, borderColor: "#DDD6FE",
  },
  aiCreditTxt: {
    fontSize: 12, fontWeight: "700", color: "#6D28D9",
  },
  aiShortenBtn: {
    backgroundColor: "#7C3AED",
    flexDirection: "row", alignItems: "center",
    justifyContent: "center", gap: 6,
  },
  aiShortenTxt: {
    color: "#fff", fontSize: 13, fontWeight: "800", letterSpacing: 0.3,
  },
});

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
  // Phase F10.3 — "Raise Inquiry" button rendered inline on
  // payment-discrepancy alert rows.
  raiseInquiryBtn: {
    marginTop: 6,
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 6,
    backgroundColor: "#FEE2E2",
    borderWidth: 1,
    borderColor: "#FCA5A5",
  },
  raiseInquiryTxt: {
    fontSize: 11.5,
    fontWeight: "800",
    color: "#B91C1C",
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
