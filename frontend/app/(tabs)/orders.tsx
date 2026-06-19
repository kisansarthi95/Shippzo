import React, { useCallback, useEffect, useRef, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View, Text, StyleSheet, TextInput, TouchableOpacity,
  FlatList, RefreshControl, ActivityIndicator, Alert, Modal,
  ScrollView, Linking,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { useFocusEffect, useRouter } from "expo-router";
import { Api, SheetOrder, PendingOrder, Courier, AbandonedCart } from "../../lib/api";
import { colors } from "../../lib/theme";
import { useFeatureFlag } from "../../lib/feature_flags";
import ConfirmCancelModal, {
  TerminalAction,
} from "../../components/ConfirmCancelModal";
import SearchBar from "../../components/SearchBar";

type Filter = "pending" | "shipped" | "all";   // legacy — retained for type-import compat

export default function OrdersFromSheet() {
  const router = useRouter();
  // Phase F3.8 — gate the per-card Edit icon by plan. Admin toggles
  // this in /admin/pricing → Plan Features. Hidden plans show only
  // Delete (operator can still remove rows, just not mutate them).
  const flagEditPending   = useFeatureFlag("pending_orders_edit");
  // Phase F3.9.2 — same pattern for the Delete icon. When off, the
  // trash button disappears from every paste/file/webhook row so
  // operators on the limited plan can't remove pending orders.
  const flagDeletePending = useFeatureFlag("pending_orders_delete");
  // Phase-21 — NEW (unviewed) + REPEAT (returning-customer) markers
  // on every pending-order card. Both default-ON for free trial, but
  // admin can untick per plan. UI silently hides the badges when off.
  const flagNewMarker     = useFeatureFlag("pending_orders_new_marker");
  const flagRepeatMarker  = useFeatureFlag("pending_orders_repeat_marker");
  const [loading, setLoading] = useState(true);
  const [orders, setOrders] = useState<SheetOrder[]>([]);
  const [connected, setConnected] = useState(false);
  const [lastSync, setLastSync] = useState<Date | null>(null);
  const [headersChanged, setHeadersChanged] = useState(false);
  const [search, setSearch] = useState("");
  // Phase B — `filter` state retired; pending/shipped/all chips were
  // collapsed into source-based chips (paste/file/sheet/webhook).
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Smart Paste pending orders queue
  const [pasteOrders, setPasteOrders] = useState<PendingOrder[]>([]);
  // Phase F1 — File-import pending queue (CSV/XLSX uploads)
  const [fileOrders, setFileOrders] = useState<PendingOrder[]>([]);
  // Phase F2.5 — Webhook ingest pending queue (Dukaan/Shopify/etc.)
  const [webhookOrders, setWebhookOrders] = useState<PendingOrder[]>([]);
  // Phase F3.9.3 (bug fix) — Recovered abandoned carts. When a user
  // taps "Confirm" on an abandoned cart it gets promoted into the
  // pending_orders collection with source="abandoned_cart". The old
  // loadPasteOrders() only fetched paste/file/webhook sources, so
  // these orders silently DISAPPEARED from the UI even though they
  // existed in the DB. We now load them as a 4th call and surface
  // them in `unifiedRows` under the same `webhook` filter (and under
  // "all") so the recovered-cart provenance stays visible.
  const [abandonedRecoveredOrders, setAbandonedRecoveredOrders] = useState<PendingOrder[]>([]);
  // Phase F2.5 — friendly webhook label (e.g. "Shopify"). Falls back
  // to "WEBHOOK" pill when the user hasn't named theirs yet.
  const [webhookName, setWebhookName]   = useState<string>("");
  // Phase F3.3 — Abandoned carts (webhook event_type=abandoned_order).
  // Surfaces alongside Pending orders with Call / WhatsApp / Confirm
  // actions instead of the regular "Ship this order" button.
  const [abandonedCarts, setAbandonedCarts] = useState<AbandonedCart[]>([]);
  const [confirmingCartId, setConfirmingCartId] = useState<string | null>(null);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [shipModalOrder, setShipModalOrder] = useState<PendingOrder | null>(null);
  const [shipping, setShipping] = useState(false);
  // Phase B — unified source filter. "all" shows every pending row
  // from every channel mixed together; the others narrow to one
  // channel. Cards are sorted newest-first regardless of filter.
  // Phase F3.3 — `abandoned` is its own filter (separate collection).
  // Phase-21 — `new` is the DEFAULT filter and excludes REUSED
  // (already-shipped) sheet rows. Matches the count on the Home tab's
  // "Pending Orders" pill.
  const [sourceFilter, setSourceFilter] = useState<
    "new" | "all" | "paste" | "file" | "sheet" | "webhook" | "abandoned" | "cancelled"
  >("new");

  // Phase-33 — Cancelled pending orders kept for history. Loaded on
  // demand (same Api.listPendingOrders with status="cancelled") so
  // the operator can review past cancellations but can never act on
  // them — every row is read-only and the backend rejects writes.
  const [cancelledOrders, setCancelledOrders] = useState<PendingOrder[]>([]);
  // Phase-33 — Reusable Cancel Order modal state. Triggered by the X
  // tick on any pending-order card. On confirm we hit the existing
  // DELETE endpoint which the server has re-purposed into a cancel-
  // flip (status="cancelled") — the record is never removed.
  const [pendingCancel, setPendingCancel] = useState<
    | (TerminalAction & { orderId: string })
    | null
  >(null);
  const [cancelSubmitting, setCancelSubmitting] = useState(false);

  const loadPasteOrders = useCallback(async () => {
    try {
      const [pos, fos, wos, aros, cs, wcfg, ac] = await Promise.all([
        Api.listPendingOrders({ source: "paste",          status: "pending" }),
        Api.listPendingOrders({ source: "file",           status: "pending" }),
        Api.listPendingOrders({ source: "webhook",        status: "pending" }),
        // Phase F3.9.3 — Recovered abandoned carts surface here.
        // When the user taps "Confirm" on an abandoned cart card it
        // gets promoted into pending_orders with source="abandoned_cart".
        // Without this 4th call those rows existed in the DB but never
        // rendered on the Orders tab — the "orders disappearing" bug.
        Api.listPendingOrders({ source: "abandoned_cart", status: "pending" })
          .catch(() => [] as PendingOrder[]),
        Api.listCouriers(),
        // Webhook name lookup is best-effort — a brand-new account
        // won't have one yet, in which case we fall back to "WEBHOOK".
        Api.getWebhookConfig().catch(() => null),
        // Phase F3.3 — pull active abandoned carts so the new
        // "Abandoned" filter chip can show a count + render cards.
        Api.listAbandonedCarts({ status: "abandoned", limit: 200 })
          .catch(() => ({ carts: [], count: 0, total: 0 })),
      ]);
      setPasteOrders(pos);
      setFileOrders(fos);
      setWebhookOrders(wos);
      setAbandonedRecoveredOrders(aros as PendingOrder[]);
      setCouriers(cs);
      setWebhookName((wcfg as any)?.name || "");
      setAbandonedCarts((ac as any)?.carts || []);
      // Phase-33 — Best-effort load of cancelled pending orders so
      // operators can review history. Read-only by design — the
      // backend rejects every write on these rows.
      try {
        const cans = await Api.listPendingOrders({ status: "cancelled" });
        setCancelledOrders(cans as PendingOrder[]);
      } catch {/* ignore — empty list keeps the chip visible at (0) */}
    } catch {/* ignore */}
  }, []);

  // Phase F3.3 — Abandoned cart actions.
  const callPhone = (phone: string) => {
    const cleaned = (phone || "").replace(/[^+\d]/g, "");
    if (!cleaned) {
      Alert.alert("No phone", "This cart has no customer phone number.");
      return;
    }
    Linking.openURL(`tel:${cleaned}`);
  };

  const whatsappAbandoned = async (cart: AbandonedCart) => {
    const cleaned = (cart.customer_phone || "").replace(/[^+\d]/g, "");
    if (!cleaned) {
      Alert.alert("No phone", "This cart has no customer phone number.");
      return;
    }
    // wa.me requires a phone in international format (no +). For
    // Indian numbers we prepend 91 if it's missing.
    let waPhone = cleaned.replace(/^\+/, "");
    if (waPhone.length === 10) waPhone = `91${waPhone}`;
    const name  = cart.customer_name || "ગ્રાહક";
    const value = cart.cart_value
      ? `₹${Math.round(cart.cart_value).toLocaleString("en-IN")}`
      : "";
    // Phase F3.9.6 — Pretty-print line items as bulleted lines.
    // `items_summary` is a comma-joined string like
    //   "Premium Triphala Powder 100gm x2, Notebook x1"
    // — fine for compact card display but cramped inside a WhatsApp
    // message. Split it back out and render each entry on its own
    // line with a 📦 bullet so the customer can see exactly what
    // they're being asked to come back for. Falls back gracefully
    // to the single-line format when the cart has only one item
    // (no separator to split on) or no items at all.
    let items = "";
    if (cart.items_summary) {
      const parts = cart.items_summary
        .split(/\s*[,;]\s*|\s*\n\s*/)        // ", " | "; " | newline
        .map((s) => s.trim())
        .filter(Boolean);
      if (parts.length > 1) {
        items = "\n\n📦 ઓર્ડર:\n" + parts.map((p) => `• ${p}`).join("\n");
      } else if (parts.length === 1) {
        items = `\n📦 ${parts[0]}`;
      }
    }
    // Phase F3.9.8 — Short-link service. Raw recovery URLs from
    // Dukaan/Shopify are 80-100+ characters which makes the WhatsApp
    // message look spammy and gets it auto-filtered by some
    // carriers. We compress to a tidy `<host>/api/s/<6-char>` alias
    // via the backend short-link service. The link binds to this
    // cart so when the customer clicks it we can stamp
    // `link_clicked_at` and (in Phase F3.9.9) auto-mark the cart
    // recovered when the matching order webhook arrives.
    //
    // Failure to shorten is non-fatal — fall back to the raw URL
    // so the user can still send the message.
    let recoveryLink = "";
    if (cart.recovery_url) {
      let urlToShare = cart.recovery_url;
      try {
        const s = await Api.shortenUrl(cart.recovery_url, cart.id);
        if (s?.short_url) urlToShare = s.short_url;
      } catch {
        /* keep raw URL */
      }
      recoveryLink = `\n🔗 ઓર્ડર પૂરો કરો: ${urlToShare}`;
    }
    const msg =
      `નમસ્તે ${name} 🙏\n\n` +
      `તમે અમારા સ્ટોર પર ઓર્ડર છોડી દીધો છે${value ? ` (${value})` : ""}.${items}${recoveryLink}\n\n` +
      `કૃપા કરી ઓર્ડર કન્ફર્મ કરવા આ મેસેજ માં Reply કરો અથવા call કરો. ` +
      `અમે તમારા ઓર્ડરની રાહ જોઈએ છીએ! 🛒`;
    const url = `https://wa.me/${waPhone}?text=${encodeURIComponent(msg)}`;
    Linking.openURL(url).catch(() =>
      Alert.alert("WhatsApp", "Couldn't open WhatsApp. Is it installed?"),
    );
  };

  const confirmAbandoned = (cart: AbandonedCart) => {
    const subtitle = `${cart.customer_name || "Customer"} · ${
      cart.cart_value
        ? "₹" + Math.round(cart.cart_value).toLocaleString("en-IN")
        : ""
    }`;

    // Phase F3.9.7 — Workflow selection. Two recovery paths share the
    // same backend endpoint:
    //   1. "Move to Pending"           → drops into Pending Orders
    //      queue. User ships later. (Original behaviour.)
    //   2. "Create Shipment Directly"  → recovers + immediately
    //      opens the existing ship-flow modal so the user picks a
    //      courier and converts to a real shipment in one tap.
    const runRecover = async (createShipment: boolean) => {
      setConfirmingCartId(cart.id);
      try {
        const r = await Api.recoverAbandonedCart(cart.id, {
          create_shipment: createShipment,
        });
        await loadPasteOrders();
        if (createShipment && r.pending_order) {
          // Drop straight into ship flow on the freshly created
          // pending row. The user picks courier + confirms inside
          // the existing modal.
          setShipModalOrder(r.pending_order);
        } else {
          setSourceFilter("all");
          Alert.alert(
            "Recovered ✓",
            `Order ${r.master_order_id} moved to Pending Orders.`,
          );
        }
      } catch (e: any) {
        Alert.alert(
          "Recovery failed",
          e?.response?.data?.detail || e?.message || "Try again.",
        );
      } finally {
        setConfirmingCartId(null);
      }
    };

    Alert.alert(
      "Confirm this order?",
      `${subtitle}\n\nWhere should this cart go next?`,
      [
        { text: "Cancel", style: "cancel" },
        { text: "Move to Pending",          onPress: () => runRecover(false) },
        { text: "Create Shipment Directly", onPress: () => runRecover(true)  },
      ],
    );
  };

  const shipPasteOrder = (order: PendingOrder) => {
    // Same flow as Sheet orders — navigate to Add with prefill.
    // User can edit fields, choose courier, and pick tracking ID
    // (auto/manual/scan). Add screen will finalize the pending order.
    //
    // Phase-9 unified-address (2026-04-30): pass the FULL address as
    // ONE string. We still attach the legacy split fields for any
    // unforeseen back-compat path, but Add screen's `fullAddressFrom`
    // helper consumes `address` first and never re-parses it.
    const fullAddr = [order.address_line1, order.address_line2]
      .map((s) => String(s || "").trim())
      .filter(Boolean)
      .join(", ");
    router.push({
      pathname: "/(tabs)/add",
      params: {
        prefill: JSON.stringify({
          order_id: order.order_id_hint || "",
          customer_name: order.customer_name,
          phone: order.customer_phone,
          alt_phone: (order as any).customer_alt_phone || "",
          address: fullAddr,
          city: order.city,
          state: order.state,
          pincode: order.pincode,
          item: order.items,
          amount: order.amount,
          payment_mode: order.payment_mode,
          weight: order.weight,
          token_amount: (order as any).token_amount || "",
          box_dimensions: (order as any).box_dimensions || "",
          shipment_notes: (order as any).shipment_notes || "",
          notes: (order as any).notes || "",
          pending_order_id: order.id,
          source: "paste",
        }),
      },
    });
  };

  // Phase-33 — Cancel Order replaces hard-delete. The X tick on each
  // card stages the action and opens the shared ConfirmCancelModal;
  // submitCancelPending() hits DELETE /orders/pending/{id} which the
  // backend has re-purposed into a `status="cancelled"` flip.
  const deletePasteOrder = (order: PendingOrder) => {
    setPendingCancel({
      kind: "delete",
      orderId: order.id,
      orderLabel:
        (order.customer_name || "") +
        (order.customer_phone ? ` · ${order.customer_phone}` : ""),
    });
  };

  const submitCancelPending = useCallback(async () => {
    if (!pendingCancel) return;
    setCancelSubmitting(true);
    try {
      await Api.deletePendingOrder(pendingCancel.orderId);
      setPendingCancel(null);
      await loadPasteOrders();
    } catch (e: any) {
      if (e?.response?.status === 423) {
        Alert.alert(
          "Order locked",
          e?.response?.data?.detail ||
            "This pending order has already been cancelled.",
        );
        setPendingCancel(null);
      } else {
        Alert.alert(
          "Couldn't cancel",
          e?.response?.data?.detail || e?.message || "Try again.",
        );
      }
    } finally {
      setCancelSubmitting(false);
    }
  }, [pendingCancel]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const settings = await Api.getSettings();
      const isConnected = Boolean(settings.sheet?.sheet_id);
      setConnected(isConnected);
      if (!isConnected) {
        setOrders([]);
        setLoading(false);
        setRefreshing(false);
        return;
      }
      const res = await Api.sheetsOrders();
      setOrders(res.orders);
      setHeadersChanged(!!res.headers_changed);
      setLastSync(new Date());
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to fetch sheet");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      loadPasteOrders();
      // auto-refresh every 60s while tab is focused
      intervalRef.current = setInterval(() => {
        load();
        loadPasteOrders();
      }, 60_000);
      return () => {
        if (intervalRef.current) clearInterval(intervalRef.current);
      };
    }, [load, loadPasteOrders])
  );

  // Phase B — Build the unified pending-orders list. All four sources
  // (Smart Paste, File Import, Webhook, Google Sheet) collapse into
  // a single vertical-scroll FlatList where each row carries its own
  // colour-coded badge. Newest-first ordering uses created_at when
  // available, falling back to spreadsheet row index for sheet rows.
  type UnifiedRow = {
    key: string;
    source: "paste" | "file" | "sheet" | "webhook" | "abandoned";
    badgeLabel: string;
    badgeBg: string;
    badgeFg: string;
    sortTime: number;          // higher = newer
    customer_name: string;
    customer_phone: string;
    city: string;
    state: string;
    pincode: string;
    items: string;
    amount: number;
    payment_mode: string;
    extra?: string;            // small footnote (e.g. "from invoice.csv")
    paste?: PendingOrder;      // present for paste/file/webhook
    sheet?: SheetOrder;        // present for sheet
    abandoned?: AbandonedCart; // present for abandoned carts (Phase F3.3)
    // Phase-21 — visual marker hints. `isNew` paints the green ✨ NEW
    // badge until the operator taps the card (server-tracked via
    // `viewed`). `isRepeat` paints the soft-grey REPEAT badge when
    // this customer has been shipped before. Both are independent so
    // a single card can wear BOTH badges (new order from a returning
    // customer). `reused` (legacy field) remains for sheet rows that
    // are already shipped — those use the "Reuse" CTA.
    isNew?: boolean;
    isRepeat?: boolean;
    reused?: boolean;
  };

  const unifiedRows: UnifiedRow[] = (() => {
    const out: UnifiedRow[] = [];
    const tsOf = (iso?: string) =>
      iso ? Date.parse(iso) || 0 : 0;

    // Smart Paste
    if (sourceFilter === "all" || sourceFilter === "new" || sourceFilter === "paste") {
      for (const po of pasteOrders) {
        out.push({
          key: `paste|${po.id}`,
          source: "paste",
          badgeLabel: "✨ PASTE",
          badgeBg: "#EDE9FE",
          badgeFg: "#7C3AED",
          sortTime: tsOf(po.created_at),
          customer_name: po.customer_name,
          customer_phone: po.customer_phone,
          city: po.city,
          state: po.state,
          pincode: po.pincode,
          items: po.items,
          amount: Number(po.amount || 0),
          payment_mode: po.payment_mode,
          paste: po,
          isNew:    !(po as any).viewed,
          isRepeat: !!(po as any).is_repeat_customer,
        });
      }
    }
    // File Imports
    if (sourceFilter === "all" || sourceFilter === "new" || sourceFilter === "file") {
      for (const po of fileOrders) {
        const meta: any = (po as any).source_meta || {};
        out.push({
          key: `file|${po.id}`,
          source: "file",
          badgeLabel: "📄 FILE",
          badgeBg: "#D1FAE5",
          badgeFg: "#047857",
          sortTime: tsOf(meta.imported_at) || tsOf(po.created_at),
          customer_name: po.customer_name,
          customer_phone: po.customer_phone,
          city: po.city,
          state: po.state,
          pincode: po.pincode,
          items: po.items,
          amount: Number(po.amount || 0),
          payment_mode: po.payment_mode,
          extra: meta.filename ? `from ${meta.filename}` : undefined,
          paste: po,
          isNew:    !(po as any).viewed,
          isRepeat: !!(po as any).is_repeat_customer,
        });
      }
    }
    // Webhook (Dukaan / Shopify / …) — name-tagged badge.
    if (sourceFilter === "all" || sourceFilter === "new" || sourceFilter === "webhook") {
      const wname = (webhookName || "WEBHOOK").toUpperCase().slice(0, 16);
      for (const po of webhookOrders) {
        const meta: any = (po as any).source_meta || {};
        // Phase F3.9.10 — Display the upstream platform's order id
        // (Dukaan #ORD-1234, Shopify #1001, etc.) when the payload
        // carried one, falling back to our master_order_id otherwise.
        // The backend already preserves the upstream id at ingest
        // (webhook.py line ~1335: `doc.order_id ||= master_order_id`)
        // — we just surface it on the card so operators can match
        // the Shippzo row against their storefront dashboard at a
        // glance instead of guessing from amount + phone.
        const upstreamId =
          (po as any).external_order_id ||
          (po.order_id && po.order_id !== (po as any).master_order_id
            ? po.order_id
            : "");
        const idChip = upstreamId ? ` · #${upstreamId}` : "";
        const fromTxt = meta.webhook_name
          ? `from ${meta.webhook_name}`
          : (wname !== "WEBHOOK" ? `from ${wname}` : "");
        out.push({
          key: `webhook|${po.id}`,
          source: "webhook",
          badgeLabel: `🔌 ${(meta.webhook_name || webhookName || "WEBHOOK").toUpperCase().slice(0, 16)}`,
          badgeBg: "#FFE4CC",
          badgeFg: "#C2410C",
          sortTime: tsOf(meta.received_at) || tsOf(po.created_at),
          customer_name: po.customer_name,
          customer_phone: po.customer_phone,
          city: po.city,
          state: po.state,
          pincode: po.pincode,
          items: po.items,
          amount: Number(po.amount || 0),
          payment_mode: po.payment_mode,
          extra: meta.webhook_name
            ? `from ${meta.webhook_name}${idChip}`
            : (wname !== "WEBHOOK" ? `from ${wname}${idChip}` : (idChip ? idChip.replace(" · ", "") : undefined)),
          paste: po,
          isNew:    !(po as any).viewed,
          isRepeat: !!(po as any).is_repeat_customer,
        });
      }
    }
    // Phase F3.9.3 — Recovered abandoned carts. These are pending
    // orders with source="abandoned_cart" — the result of the user
    // tapping "Confirm" on an abandoned-cart card. They share the
    // same shipping action as paste/file/webhook rows (since they
    // live in pending_orders) but carry a distinct soft-pink badge
    // so the provenance ("this came from a recovery") stays visible
    // in the unified list. Phase-21 — Surfaced under "all" AND "new"
    // ONLY. The Abandoned tab is reserved for STILL-ABANDONED carts
    // (un-recovered), so once a cart has been recovered it leaves
    // that view and only shows up under the unified buckets.
    if (sourceFilter === "all" || sourceFilter === "new") {
      for (const po of abandonedRecoveredOrders) {
        const meta: any = (po as any).source_meta || {};
        // Phase-21 — Show the ORIGINAL source (the storefront the
        // cart was recovered from) on the badge so the operator can
        // tell "this came from Shopify" vs "this came from Dukaan"
        // at a glance. Priority: source_app → webhook_name → generic
        // "Recovered" fallback. Normalised to Title Case (first letter
        // upper, rest lower) so SHOPIFY / shopify / Shopify all
        // render identically as "Shopify".
        const srcRaw = String(meta.source_app || meta.webhook_name || "").trim();
        const srcLabel = srcRaw
          ? srcRaw.charAt(0).toUpperCase() + srcRaw.slice(1).toLowerCase()
          : "Recovered";
        out.push({
          key: `abandoned-recovered|${po.id}`,
          source: "webhook",   // shares the webhook shipping action
          badgeLabel: `🛒 ${srcLabel}`,
          badgeBg: "#FCE7F3",
          badgeFg: "#9D174D",
          sortTime: tsOf(meta.recovered_at) || tsOf(po.created_at),
          customer_name: po.customer_name,
          customer_phone: po.customer_phone,
          city: po.city,
          state: po.state,
          pincode: po.pincode,
          items: po.items,
          amount: Number(po.amount || 0),
          payment_mode: po.payment_mode,
          extra: "recovered from cart",
          paste: po,
          isNew:    !(po as any).viewed,
          isRepeat: !!(po as any).is_repeat_customer,
        });
      }
    }
    // Google Sheet — Phase F3.9.11. Two visual states now:
    //   • Pending  (already_shipped=false) — original card, counts in
    //     All, ship action available.
    //   • REUSED   (already_shipped=true)  — soft-grey badge so the
    //     operator can SEE that this customer has already shipped
    //     once, with a "Reuse" CTA that opens the ship modal for a
    //     fresh shipment to the same address. Does NOT count in All.
    // Phase-21 — `new` filter is the same as "sheet" but EXPLICITLY
    // hides every already_shipped (REUSED) row so the operator only
    // sees first-time customers there. Matches the Home pill count.
    if (sourceFilter === "all" || sourceFilter === "new" || sourceFilter === "sheet") {
      const q = search.trim().toLowerCase();
      for (const o of orders) {
        if (q) {
          const hay = `${o.order_id} ${o.customer_name} ${o.phone} ${o.city}`.toLowerCase();
          if (!hay.includes(q)) continue;
        }
        const reused = !!o.already_shipped;
        // Phase-21 — Skip REUSED rows entirely in the "new" filter.
        if (sourceFilter === "new" && reused) continue;
        out.push({
          key: `sheet|${o.row_index}|${o.row_key || ""}`,
          source: "sheet",
          badgeLabel: reused ? "🔄 REPEAT" : "📊 SHEET",
          badgeBg:    reused ? "#E5E7EB" : "#DBEAFE",
          badgeFg:    reused ? "#475569" : "#1D4ED8",
          sortTime: typeof o.row_index === "number" ? o.row_index : 0,
          customer_name: o.customer_name,
          customer_phone: o.phone,
          city: o.city,
          state: o.state,
          pincode: o.pincode,
          items: o.item,
          amount: Number(o.amount || 0),
          payment_mode: "",
          extra: reused
            ? `🔁 Already shipped${o.order_id ? ` · #${o.order_id}` : ""}`
            : `Row ${o.row_index}${o.order_id ? ` · #${o.order_id}` : ""}`,
          sheet: o,
          // Hint to the renderer/ship-action that this row should
          // open the ship modal as a NEW shipment (Reuse semantics).
          reused,
          isRepeat: reused,
        });
      }
    }

    // Phase F3.3 — Abandoned carts (separate collection, but the
    // user wants them inline so they can recover/contact in one
    // place). Source-only filter — never bleed into "all".
    if (sourceFilter === "abandoned") {
      for (const ac of abandonedCarts) {
        const sourceLbl = ac.source_app
          ? ac.source_app.charAt(0).toUpperCase() + ac.source_app.slice(1)
          : "Cart";
        out.push({
          key: `abandoned|${ac.id}`,
          source: "abandoned",
          badgeLabel: `🛒 ${sourceLbl.toUpperCase()}`,
          badgeBg: "#FED7AA",
          badgeFg: "#9A3412",
          sortTime: Date.parse(ac.abandoned_at || ac.created_at) || 0,
          customer_name: ac.customer_name,
          customer_phone: ac.customer_phone,
          city: ac.city,
          state: ac.state,
          pincode: ac.pincode,
          items: ac.items_summary,
          amount: ac.cart_value,
          payment_mode: "",
          extra: ac.customer_email || "",
          abandoned: ac,
        });
      }
    }

    // Phase-33 — Cancelled pending orders (read-only history).
    // Only included when the operator explicitly picks the
    // "Cancelled" filter chip. These rows are dead-state: the X
    // tick is hidden, the Edit/Ship buttons are skipped, and the
    // body tap shows a friendly info toast instead of opening
    // the edit screen.
    if (sourceFilter === "cancelled") {
      for (const po of cancelledOrders) {
        out.push({
          key: `cancelled|${po.id}`,
          source: "paste",      // hide edit/ship/X via dedicated flag below
          badgeLabel: "❌ CANCELLED",
          badgeBg: "#FEE2E2",
          badgeFg: "#991B1B",
          sortTime: Date.parse((po as any).cancelled_at || po.created_at || "") || 0,
          customer_name: po.customer_name || "",
          customer_phone: po.customer_phone || "",
          city: po.city || "",
          state: po.state || "",
          pincode: po.pincode || "",
          items: po.items || "",
          amount: Number(po.amount || 0),
          payment_mode: po.payment_mode || "",
          extra: "Locked — view only",
          paste: po,
          // Reuse a different flag would require a renderer split.
          // We keep this row in the paste source but the renderer
          // omits Edit/Ship/X when sourceFilter === "cancelled".
        });
      }
    }

    // Newest first.
    out.sort((a, b) => b.sortTime - a.sortTime);
    return out;
  })();

  const renderUnifiedRow = (row: UnifiedRow) => (
    <View key={row.key} style={styles.unifiedCard} testID={`unified-${row.key}`}>
      <View style={styles.unifiedHeader}>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 6, flexShrink: 1 }}>
          <View style={[styles.unifiedBadge, { backgroundColor: row.badgeBg }]}>
            <Text style={[styles.unifiedBadgeTxt, { color: row.badgeFg }]}>
              {row.badgeLabel}
            </Text>
          </View>
          {/* Phase-21 — NEW pill: shows on every unviewed pending
              card. Clears the moment the operator taps the card body
              (markViewed) — both locally and server-side. Plan-gated
              via `pending_orders_new_marker`. Skipped for sheet REUSED
              rows since they already wear the REPEAT badge.  */}
          {flagNewMarker && row.isNew && !row.reused ? (
            <View style={[styles.unifiedBadge, styles.newPill]}>
              <Text style={styles.newPillTxt}>✨ NEW</Text>
            </View>
          ) : null}
          {/* Phase-21 — REPEAT pill: shows when the customer (by
              phone) has been shipped at least once before. Stays
              even after viewing — operator should always know this
              is a returning customer.  */}
          {flagRepeatMarker && row.isRepeat && !row.reused ? (
            <View style={[styles.unifiedBadge, styles.repeatPill]}>
              <Text style={styles.repeatPillTxt}>🔁 REPEAT</Text>
            </View>
          ) : null}
        </View>
        <View style={{ flexDirection: "row", gap: 6, alignItems: "center" }}>
          {/* Phase C — Edit + Delete sized identically (32×32 round
              icon buttons) so they read as a paired action group
              instead of a big edit button + tiny delete X. */}
          {/* Phase-33 — Hide Edit & Cancel-X on cancelled-history
              rows. Read-only by design. */}
          {row.paste && flagEditPending && sourceFilter !== "cancelled" ? (
            <TouchableOpacity
              onPress={() => {
                markViewed(row);
                router.push(`/edit-pending/${row.paste!.id}` as any);
              }}
              hitSlop={6}
              style={styles.cardActionBtn}
              testID={`edit-${row.key}`}
            >
              <PhIcon name="create-outline" size={16} color="#3B82F6" />
            </TouchableOpacity>
          ) : null}
          {row.paste && flagDeletePending && sourceFilter !== "cancelled" ? (
            <TouchableOpacity
              onPress={() => deletePasteOrder(row.paste!)}
              hitSlop={6}
              style={[styles.cardActionBtn, { backgroundColor: "#FEF2F2", borderColor: "#FECACA" }]}
              testID={`cancel-${row.key}`}
            >
              {/* Phase-33 — X icon (Cancel Order) replaces the
                  legacy trash icon. Same destructive colour palette
                  but the semantics now route through the shared
                  ConfirmCancelModal → backend cancel-flip. */}
              <PhIcon name="close-circle-outline" size={16} color="#DC2626" />
            </TouchableOpacity>
          ) : null}
        </View>
      </View>
      {/* Phase-21 — Tap the body of the card to "open" the order.
          For pending_orders rows this clears the NEW badge by calling
          mark-viewed (optimistic + server-sync). For sheet rows it's
          a no-op visually but keeps the touch target consistent. */}
      <TouchableOpacity
        activeOpacity={0.85}
        onPress={() => {
          markViewed(row);
          if (row.paste) {
            router.push(`/edit-pending/${row.paste.id}` as any);
          }
        }}
      ><Text style={styles.unifiedName} numberOfLines={1}>
        {row.customer_name || "(no name)"}
      </Text>
      <Text style={styles.unifiedMeta} numberOfLines={1}>
        📞 {row.customer_phone || "—"} · {row.pincode || "—"}
      </Text>
      <Text style={styles.unifiedMeta} numberOfLines={1}>
        {row.city || "—"}{row.state ? `, ${row.state}` : ""}
      </Text>
      {!!row.items && (
        <Text style={styles.unifiedItems} numberOfLines={1}>
          📦 {row.items}
        </Text>
      )}
      <Text style={styles.unifiedAmount}>
        {row.payment_mode === "COD" ? "💵 COD" : row.payment_mode === "PAID" ? "✅ PAID" : ""}
        {row.amount ? `  ₹${row.amount.toFixed(0)}` : ""}
      </Text>
      {!!row.extra && (
        <Text style={styles.unifiedExtra} numberOfLines={1}>{row.extra}</Text>
      )}
      </TouchableOpacity>
      {row.source === "abandoned" && row.abandoned ? (
        // Phase F3.3 — Abandoned cart actions: Call + WhatsApp +
        // Confirm. The Ship button is intentionally hidden because
        // abandoned carts aren't real orders yet — owner needs to
        // confirm them first.
        <View style={styles.abandonedActions}>
          <TouchableOpacity
            style={[styles.abActBtn, styles.abActCall]}
            onPress={() => callPhone(row.abandoned!.customer_phone)}
            testID={`call-${row.key}`}
            activeOpacity={0.85}
          >
            <PhIcon name="phone" size={14} color="#1E40AF" />
            <Text style={styles.abActCallTxt}>Call</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.abActBtn, styles.abActWa]}
            onPress={() => whatsappAbandoned(row.abandoned!)}
            testID={`wa-${row.key}`}
            activeOpacity={0.85}
          >
            <PhIcon name="logo-whatsapp" size={14} color="#fff" />
            <Text style={styles.abActWaTxt}>WhatsApp</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[
              styles.abActBtn, styles.abActConfirm,
              confirmingCartId === row.abandoned.id && { opacity: 0.5 },
            ]}
            onPress={() => confirmAbandoned(row.abandoned!)}
            disabled={confirmingCartId === row.abandoned.id}
            testID={`confirm-${row.key}`}
            activeOpacity={0.85}
          >
            {confirmingCartId === row.abandoned.id ? (
              <ActivityIndicator size="small" color="#fff" />
            ) : (
              <>
                <PhIcon name="checkmark" size={14} color="#fff" />
                <Text style={styles.abActConfirmTxt}>Confirm</Text>
              </>
            )}
          </TouchableOpacity>
        </View>
      ) : (
        <TouchableOpacity
          style={[
            styles.shipBtn,
            row.source === "file"    && { backgroundColor: "#10B981" },
            row.source === "sheet"   && { backgroundColor: "#1D4ED8" },
            row.source === "webhook" && { backgroundColor: "#C2410C" },
            // Phase F3.9.11 — Reuse CTA for already-shipped sheet rows.
            // Soft-grey background so it doesn't compete with the
            // bright Ship buttons on fresh pending rows.
            (row as any).reused && { backgroundColor: "#64748B" },
          ]}
          onPress={() => {
            if (row.sheet) shipNow(row.sheet);
            else if (row.paste) shipPasteOrder(row.paste);
          }}
          testID={`ship-${row.key}`}
        >
          <PhIcon
            name={(row as any).reused ? "refresh-outline" : "rocket-outline"}
            size={14} color="#fff"
          />
          <Text style={styles.shipBtnText}>
            {(row as any).reused ? "Reuse" : "Ship this order"}
          </Text>
        </TouchableOpacity>
      )}
    </View>
  );

  const shipNow = (o: SheetOrder) => {
    // Navigate to Add with prefilled fields via URL params (stringified).
    // Include the full `raw` row so Add can auto-fill any per-shipment
    // custom fields that are mapped to Google Sheet columns.
    router.push({
      pathname: "/(tabs)/add",
      params: {
        prefill: JSON.stringify({
          order_id: o.order_id,
          customer_name: o.customer_name,
          phone: o.phone,
          address: o.address,
          city: o.city,
          state: o.state,
          pincode: o.pincode,
          item: o.item,
          amount: o.amount,
          row_key: o.row_key,
          raw: o.raw || {},
        }),
      },
    });
  };

  // Phase-21 — Mark a pending-order card as viewed when the operator
  // taps it. Optimistically updates the in-memory list (NEW badge
  // disappears instantly) and fires the API call in the background.
  // No-op for sheet rows (they aren't in pending_orders) — the local
  // optimistic update is enough.
  const markViewed = useCallback((row: UnifiedRow) => {
    const po = row.paste;
    if (!po) return; // sheet/abandoned rows don't have a pending_orders id
    if ((po as any).viewed) return; // already viewed → nothing to do

    // Optimistic flip → re-render with the NEW badge gone.
    const flip = (lst: PendingOrder[]) =>
      lst.map((x) => (x.id === po.id ? ({ ...x, viewed: true } as any) : x));
    if (po.source === "paste")           setPasteOrders(flip);
    else if (po.source === "file")       setFileOrders(flip);
    else if (po.source === "webhook")    setWebhookOrders(flip);
    else                                 setAbandonedRecoveredOrders(flip);

    // Best-effort server sync; swallow errors so a flaky network
    // doesn't bring back the badge.
    Api.markPendingOrderViewed(po.id).catch(() => {});
  }, []);

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Orders</Text>
          <Text style={styles.subtitle}>
            {(() => {
              // Phase B — single subtitle that aggregates pending
              // counts from every source the user has wired up.
              const sheetPending = orders.filter(o => !o.already_shipped).length;
              const totalPending = pasteOrders.length + fileOrders.length + webhookOrders.length + sheetPending;
              return totalPending === 0
                ? "No pending orders yet"
                : `${totalPending} pending · synced ${lastSync ? timeAgo(lastSync) : "—"}`;
            })()}
          </Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center" }}>
          <TouchableOpacity
            testID="orders-upload-btn"
            style={[styles.refreshBtn, { backgroundColor: "#10B981", marginRight: 8 }]}
            onPress={() => router.push("/file-import" as any)}
          >
            <PhIcon name="cloud-upload" size={20} color="#fff" />
          </TouchableOpacity>
          <TouchableOpacity
            testID="orders-refresh-btn"
            style={styles.refreshBtn}
            onPress={() => {
              setRefreshing(true);
              load();
              loadPasteOrders();
            }}
          >
            <PhIcon name="refresh" size={20} color="#fff" />
          </TouchableOpacity>
        </View>
      </View>

      {/* Phase B — Unified Pending Orders.
          One vertical FlatList for ALL sources (Smart Paste, File,
          Webhook, Google Sheet). Each card carries a colour-coded
          source badge and the list is sorted newest-first.
          Sheet rows that are already shipped are filtered out. */}
      <SearchBar
        testID="orders-search"
        value={search}
        onChangeText={setSearch}
        onClear={() => {
          // Phase-32 one-tap clear UX: wipe search AND reset the
          // source-filter chip back to "all" so the list returns
          // to its default state in a single tap.
          setSourceFilter("all");
        }}
        placeholder="Search order, name, phone, city"
      />

      {/* Source filter chips — horizontal scroll. Phase F3.9 fix:
          removed `maxHeight: 52` because on Android with system font
          set to "Largest" (>=1.3x scale) the chip text overflows past
          52dp and gets clipped. Replaced with breathing-room
          marginTop + bigger vertical paddingVertical so the chips
          stay readable at any font scale. */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={{ flexGrow: 0, marginTop: 8 }}
        contentContainerStyle={{
          paddingHorizontal: 12,
          paddingVertical: 8,
          alignItems: "center",
          gap: 8,
        }}
      >
        {([
          // Phase-21 — "New Orders" filter. Excludes REUSED (sheet-side
          // already-shipped) rows so the operator only sees orders
          // that have never been fulfilled. This count matches the
          // Home tab's "Pending Orders" pill. Default-selected when
          // the screen first opens.
          {
            k: "new",
            label: `New Orders (${
              pasteOrders.length +
              fileOrders.length +
              webhookOrders.length +
              abandonedRecoveredOrders.length +
              orders.filter(o => !o.already_shipped).length
            })`,
          },
          // Phase F3.9.11 — Sheet rows show with a soft-grey REUSED
          // badge + "Reuse" CTA when already_shipped=true.
          // Phase-21 — All Orders now counts EVERY order (including
          // REUSED sheet rows) so the operator sees the true total of
          // what's visible on the screen; New Orders narrows down to
          // unshipped only. Includes abandoned-recovered pending
          // orders as well to stay consistent with the New filter.
          { k: "all",     label: `All (${pasteOrders.length + fileOrders.length + webhookOrders.length + abandonedRecoveredOrders.length + orders.length})` },
          { k: "paste",   label: `✨ Smart Paste${pasteOrders.length ? ` (${pasteOrders.length})` : ""}` },
          { k: "file",    label: `📄 File${fileOrders.length ? ` (${fileOrders.length})` : ""}` },
          { k: "sheet",   label: `📊 Sheet${connected ? ` (${orders.filter(o => !o.already_shipped).length})` : ""}` },
          { k: "webhook", label: `🔌 ${(webhookName || "Webhook")}${webhookOrders.length ? ` (${webhookOrders.length})` : ""}` },
          { k: "abandoned", label: `🛒 Abandoned${abandonedCarts.length ? ` (${abandonedCarts.length})` : ""}` },
          // Phase-33 — Cancelled history filter. Always visible even
          // at (0) so operators learn the section exists; clicking
          // loads cancelled rows which render in read-only mode.
          { k: "cancelled", label: `❌ Cancelled${cancelledOrders.length ? ` (${cancelledOrders.length})` : ""}` },
        ] as const).map((f) => {
          const active = sourceFilter === f.k;
          return (
            <TouchableOpacity
              key={f.k}
              testID={`unified-filter-${f.k}`}
              onPress={() => setSourceFilter(f.k as any)}
              style={[styles.filterPill, active && styles.filterPillActive]}
            >
              <Text style={[styles.filterText, active && { color: "#fff" }]}>
                {f.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {error && sourceFilter !== "all" && sourceFilter !== "sheet" ? null : null}
      {connected && headersChanged && (
        <View style={styles.warnBox}>
          <PhIcon name="warning-outline" size={16} color={colors.warningText} />
          <Text style={styles.warnText}>
            Sheet columns changed. Re-map in Settings → Google Sheet.
          </Text>
        </View>
      )}

      {loading ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      ) : unifiedRows.length === 0 ? (
        <View style={styles.empty} testID="unified-empty">
          <PhIcon name="cube-outline" size={48} color="#9CA3AF" />
          <Text style={styles.emptyTitle}>No pending orders</Text>
          <Text style={styles.emptyText}>
            New orders will appear here automatically as they arrive
            from Smart Paste, File Imports, Google Sheet, or Webhooks.
          </Text>
          {!connected && (
            <TouchableOpacity
              testID="orders-goto-settings"
              style={styles.primaryBtn}
              onPress={() => router.push("/(tabs)/settings")}
            >
              <Text style={styles.primaryBtnText}>Connect a source</Text>
            </TouchableOpacity>
          )}
        </View>
      ) : (
        <FlatList
          testID="unified-list"
          data={unifiedRows}
          keyExtractor={(r) => r.key}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                load();
                loadPasteOrders();
              }}
            />
          }
          contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
          renderItem={({ item }) => renderUnifiedRow(item)}
        />
      )}

      {/* Courier Picker Modal for Ship This Order */}
      <Modal
        visible={!!shipModalOrder}
        transparent
        animationType="slide"
        onRequestClose={() => setShipModalOrder(null)}
      >
        <View style={styles.modalOverlay}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <PhIcon name="rocket" size={18} color="#7C3AED" />
              <Text style={styles.modalTitle}>Ship Order</Text>
              <TouchableOpacity onPress={() => setShipModalOrder(null)} hitSlop={10}>
                <PhIcon name="close" size={22} color={colors.text} />
              </TouchableOpacity>
            </View>
            {shipModalOrder && (
              <>
                <View style={styles.shipSummary}>
                  <Text style={styles.shipSummaryName}>{shipModalOrder.customer_name}</Text>
                  <Text style={styles.shipSummaryLine}>
                    📞 {shipModalOrder.customer_phone} · ₹{Number(shipModalOrder.amount || 0).toFixed(0)} {shipModalOrder.payment_mode}
                  </Text>
                  <Text style={styles.shipSummaryLine}>
                    {shipModalOrder.city}, {shipModalOrder.state} - {shipModalOrder.pincode}
                  </Text>
                </View>
                <Text style={styles.modalHint}>Pick a courier to allocate tracking ID:</Text>
                <ScrollView style={{ maxHeight: 320 }}>
                  {couriers.map((c) => (
                    <TouchableOpacity
                      key={c.id}
                      style={styles.courierRow}
                      onPress={() => shipPasteOrder(shipModalOrder, c)}
                      disabled={shipping}
                    >
                      <View style={styles.courierIcon}>
                        <PhIcon name="cube-outline" size={18} color={colors.primary} />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.courierName}>{c.name}</Text>
                        <Text style={styles.courierSub}>
                          Next: {c.series_prefix}{String(c.next_number || 1).padStart(c.number_padding || 4, "0")}
                        </Text>
                      </View>
                      <PhIcon name="chevron-forward" size={18} color={colors.textMuted} />
                    </TouchableOpacity>
                  ))}
                </ScrollView>
                {shipping && (
                  <View style={{ padding: 10, alignItems: "center" }}>
                    <ActivityIndicator color={colors.primary} />
                  </View>
                )}
              </>
            )}
          </View>
        </View>
      </Modal>

      {/* Phase-33 — Shared Cancel Order modal. The X tick on each
          pending-order card stages a `kind="delete"` action; the
          modal confirms it before the API call. On success the row
          flips to status="cancelled" and shows under the new
          Cancelled history filter. */}
      <ConfirmCancelModal
        action={pendingCancel}
        loading={cancelSubmitting}
        onClose={() => !cancelSubmitting && setPendingCancel(null)}
        onConfirm={submitCancelPending}
      />
    </SafeAreaView>
  );
}

function timeAgo(d: Date): string {
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 10,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  subtitle: { color: colors.textMuted, fontSize: 12, marginTop: 2 },
  refreshBtn: {
    width: 44, height: 44, borderRadius: 10,
    backgroundColor: colors.primary,
    justifyContent: "center", alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16, marginTop: 4,
    flexDirection: "row", alignItems: "center", gap: 8,
    height: 46, backgroundColor: colors.surface,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 10, paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRow: {
    flexDirection: "row", gap: 8,
    paddingHorizontal: 16, paddingVertical: 12,
  },
  filterPill: {
    paddingHorizontal: 14, paddingVertical: 10,
    minHeight: 40,
    justifyContent: "center",
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 20, backgroundColor: "#fff",
    flexShrink: 0,
    marginRight: 8,
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: {
    fontWeight: "700", fontSize: 13, color: colors.text,
    lineHeight: 18,
    includeFontPadding: false,
  },
  warnBox: {
    flexDirection: "row", alignItems: "center", gap: 6,
    marginHorizontal: 16, marginTop: 8,
    padding: 10, backgroundColor: colors.warningBg,
    borderWidth: 1, borderColor: "#FDE68A", borderRadius: 8,
  },
  warnText: { color: colors.warningText, fontSize: 12, fontWeight: "600", flex: 1 },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  orderId: {
    fontFamily: "Courier", fontWeight: "800", fontSize: 13,
    color: colors.primary, letterSpacing: 1,
  },
  customerName: {
    marginTop: 6, fontSize: 16, fontWeight: "800", color: colors.text,
  },
  metaLine: { marginTop: 2, fontSize: 12, color: colors.textMuted },
  itemLine: { marginTop: 4, fontSize: 13, color: colors.text, fontWeight: "600" },
  amountLine: { marginTop: 4, fontSize: 14, color: colors.text, fontWeight: "800" },
  shipBtn: {
    marginTop: 12, flexDirection: "row", alignItems: "center",
    justifyContent: "center", gap: 6,
    height: 42, backgroundColor: colors.primary, borderRadius: 10,
  },
  shipBtnText: { color: "#fff", fontWeight: "800" },
  shippedChip: {
    backgroundColor: colors.successBg,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4,
  },
  shippedChipText: { fontSize: 10, fontWeight: "800", color: colors.successText, letterSpacing: 0.5 },
  pendingChip: {
    backgroundColor: colors.warningBg,
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4,
  },
  pendingChipText: { fontSize: 10, fontWeight: "800", color: colors.warningText, letterSpacing: 0.5 },
  empty: {
    alignItems: "center", padding: 30, marginHorizontal: 16, marginTop: 20,
    backgroundColor: colors.surface, borderRadius: 12,
    borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyTitle: { marginTop: 14, fontSize: 16, fontWeight: "800", color: colors.text },
  emptyText: {
    marginTop: 8, color: colors.textMuted, textAlign: "center", fontSize: 13, lineHeight: 18,
  },
  primaryBtn: {
    marginTop: 16, backgroundColor: colors.primary,
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },

  // ─── Phase B — Unified pending-orders card ────────────────────
  // Used for cards from every source (paste / file / sheet / webhook).
  // Source identity is conveyed via the colour-coded badge at the top.
  unifiedCard: {
    backgroundColor: colors.surface,
    borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  unifiedHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  unifiedBadge: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6,
  },
  unifiedBadgeTxt: {
    fontSize: 10, fontWeight: "900", letterSpacing: 0.5,
  },
  // Phase-21 — Visual markers on pending-order cards.
  //   newPill    → green ✨ NEW pill shown until the operator taps
  //                the card body (server-tracked via `viewed`).
  //   repeatPill → soft-slate 🔁 REPEAT pill shown when the customer's
  //                phone matches an earlier shipment of the same user.
  // Both can stack on the same card (new order from returning cust).
  newPill: {
    backgroundColor: "#DCFCE7",
    borderWidth: 1,
    borderColor: "#86EFAC",
  },
  newPillTxt: {
    fontSize: 10, fontWeight: "900", letterSpacing: 0.5, color: "#15803D",
  },
  repeatPill: {
    backgroundColor: "#E2E8F0",
    borderWidth: 1,
    borderColor: "#CBD5E1",
  },
  repeatPillTxt: {
    fontSize: 10, fontWeight: "900", letterSpacing: 0.5, color: "#475569",
  },
  unifiedName: { marginTop: 8, fontSize: 16, fontWeight: "800", color: colors.text },
  unifiedMeta: { marginTop: 2, fontSize: 12, color: colors.textMuted },
  unifiedItems: { marginTop: 4, fontSize: 13, color: colors.text, fontWeight: "600" },
  unifiedAmount: { marginTop: 6, fontSize: 14, color: colors.text, fontWeight: "800" },
  unifiedExtra: { marginTop: 4, fontSize: 11, color: "#94A3B8", fontStyle: "italic" },
  // Phase C — uniform 32×32 round icon buttons for Edit + Delete so
  // they read as a paired action group on every pending card.
  cardActionBtn: {
    width: 32, height: 32, borderRadius: 8,
    alignItems: "center", justifyContent: "center",
    backgroundColor: "#EFF6FF", borderWidth: 1, borderColor: "#BFDBFE",
  },

  // Smart Paste queue styles
  pasteQueueWrap: {
    backgroundColor: "#F5F3FF",
    borderBottomWidth: 1,
    borderBottomColor: "#DDD6FE",
    paddingTop: 10,
  },
  pasteQueueHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 14,
    marginBottom: 8,
  },
  pasteQueueTitle: {
    fontSize: 12,
    fontWeight: "900",
    color: "#5B21B6",
    letterSpacing: 0.5,
  },
  pasteCard: {
    width: 260,
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 12,
    borderWidth: 1,
    borderColor: "#DDD6FE",
    gap: 4,
  },
  pasteBadge: {
    alignSelf: "flex-start",
    paddingVertical: 2,
    paddingHorizontal: 8,
    backgroundColor: "#7C3AED",
    borderRadius: 8,
  },
  pasteBadgeText: { color: "#fff", fontSize: 10, fontWeight: "900" },
  pasteName: {
    fontSize: 14,
    fontWeight: "800",
    color: colors.text,
    marginTop: 4,
  },
  pasteMeta: { fontSize: 11, color: colors.textMuted },
  pasteAmount: { fontSize: 13, fontWeight: "800", color: colors.text, marginTop: 4 },
  shipBtn: {
    marginTop: 8,
    backgroundColor: "#7C3AED",
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    paddingVertical: 8,
    borderRadius: 8,
  },
  shipBtnText: { color: "#fff", fontSize: 12, fontWeight: "800" },

  // Phase F3.3 — Abandoned cart actions: Call + WhatsApp + Confirm.
  abandonedActions: {
    flexDirection: "row",
    gap: 6,
    marginTop: 8,
  },
  abActBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    paddingVertical: 9,
    borderRadius: 8,
  },
  abActCall: { backgroundColor: "#DBEAFE" },
  abActCallTxt: { color: "#1E40AF", fontSize: 12, fontWeight: "800" },
  abActWa:   { backgroundColor: "#22C55E" },
  abActWaTxt:   { color: "#fff", fontSize: 12, fontWeight: "800" },
  abActConfirm: { backgroundColor: "#9A3412" },
  abActConfirmTxt: { color: "#fff", fontSize: 12, fontWeight: "800" },

  // Modal
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.55)",
    justifyContent: "flex-end",
  },
  modalCard: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 18,
    paddingBottom: 30,
    maxHeight: "80%",
  },
  modalHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 8,
  },
  modalTitle: { flex: 1, fontSize: 16, fontWeight: "900", color: colors.text },
  modalHint: {
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 10,
    lineHeight: 17,
  },
  shipSummary: {
    padding: 12,
    backgroundColor: "#F5F3FF",
    borderRadius: 10,
    marginBottom: 14,
  },
  shipSummaryName: { fontSize: 14, fontWeight: "900", color: colors.text },
  shipSummaryLine: { fontSize: 12, color: colors.text, marginTop: 3 },
  courierRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 10,
    borderRadius: 10,
    marginBottom: 6,
    borderWidth: 1,
    borderColor: "#E5E7EB",
    backgroundColor: "#FAFAFA",
  },
  courierIcon: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "#FFF7ED",
    alignItems: "center",
    justifyContent: "center",
  },
  courierName: { fontSize: 14, fontWeight: "800", color: colors.text },
  courierSub: { fontSize: 11, color: colors.textMuted, marginTop: 2 },
});
