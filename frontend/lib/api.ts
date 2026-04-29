import axios from "axios";
import AsyncStorage from "@react-native-async-storage/async-storage";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;

export const api = axios.create({
  baseURL: `${BASE}/api`,
  timeout: 25000,
});

/**
 * Request interceptor: if the session token exists in storage (set by
 * AuthProvider), attach it as Bearer. This is a safety net — AuthProvider
 * also sets api.defaults.headers so this rarely fires, but it guards
 * against edge cases (e.g. the provider's default was cleared).
 */
api.interceptors.request.use(async (config) => {
  try {
    if (!config.headers?.Authorization && !config.headers?.authorization) {
      const token = await AsyncStorage.getItem("@auth_token");
      if (token) {
        config.headers = config.headers ?? {};
        (config.headers as any).Authorization = `Bearer ${token}`;
      }
    }
  } catch {
    /* storage unavailable - continue without */
  }
  return config;
});

/**
 * Response interceptor: on 401 (expired/invalid token) clear credentials
 * so AuthGate can redirect to /login. We import auth dynamically to avoid
 * a circular module-load between auth.tsx and api.ts.
 */
let _onUnauthorized: (() => void) | null = null;
export function registerUnauthorizedHandler(fn: () => void) {
  _onUnauthorized = fn;
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const status = err?.response?.status;
    const url: string = err?.config?.url || "";
    // Don't auto-logout on the login/signup endpoints themselves — their
    // 401 means "wrong credentials", which the form handles locally.
    const isAuthEndpoint =
      url.includes("/auth/login") || url.includes("/auth/signup");
    if (status === 401 && !isAuthEndpoint) {
      try {
        await AsyncStorage.multiRemove(["@auth_token", "@auth_user"]);
        delete api.defaults.headers.common["Authorization"];
      } catch {
        /* ignore */
      }
      _onUnauthorized?.();
    }
    return Promise.reject(err);
  }
);

export type Courier = {
  id: string;
  name: string;
  series_prefix: string;
  next_number: number;
  number_padding: number;
  contact_phone: string;
  contact_email: string;
  website_url: string;
  tracking_url_template: string;
  customer_id: string;
  notes: string;
  // Phase-4d: per-courier tracking-ID format validation.
  tracking_id_prefix?: string;
  tracking_id_suffix?: string;
  tracking_id_length?: number;
  tracking_id_min_length?: number;
  tracking_id_max_length?: number;
  tracking_id_pattern?: string;
  created_at: string;
};

export type SenderAddress = {
  name: string;
  phone: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  pincode: string;
  show_contact: boolean;
};

export type Brand = {
  name: string;
  logo_base64: string;
};

export type SheetConfig = {
  url: string;
  sheet_id: string;
  gid: string;
  tab_name: string;
  headers: string[];
  column_mapping: Record<string, string>;
  auto_refresh_minutes?: number;
};

export type LabelFields = {
  oid: boolean;
  dispatch_date: boolean;
  weight: boolean;
  item: boolean;
  phone: boolean;
  alt_phone: boolean;
  customer_id: boolean;
  token_info: boolean;
  box_dimensions: boolean;
  shipment_notes: boolean;
};

export type CustomFieldPosition =
  | "header_top"
  | "from_block"
  | "to_block"
  | "meta_row"
  | "notes_area"
  | "footer_bottom";

export type CustomLabelField = {
  id: string;
  label: string;
  value: string;
  position: CustomFieldPosition;
  enabled: boolean;
  bold?: boolean;
  size?: "xs" | "sm" | "md";
  source?: "static" | "shipment";
  sheet_column?: string;
  placeholder?: string;
};

export type Settings = {
  id: string;
  sender: SenderAddress;
  brand: Brand;
  whatsapp_template: string;
  copy_template: string;
  default_eta_days: number;
  sheet: SheetConfig;
  prefer_logo?: boolean;
  logo_shape?: "square" | "wide";
  shipment_tagline?: string;
  label_fields?: LabelFields;
  custom_fields?: CustomLabelField[];
};

export type Shipment = {
  id: string;
  tracking_id: string;
  courier_id?: string;
  courier_name: string;
  order_id: string;
  customer_name: string;
  customer_phone: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  pincode: string;
  payment_mode: "COD" | "Prepaid";
  amount: number;
  cod_amount: number;
  items: string[];
  item_description: string;
  weight: string;
  token_amount?: number;
  box_dimensions?: string;
  shipment_notes?: string;
  custom_values?: Record<string, string>;
  status: "Pending" | "Delivered" | "Cancelled";
  created_at: string;
  delivered_at?: string | null;
  sheet_row_key?: string;
  sheet_row_num?: number | null;   // Master Sheet row this shipment was appended to (for soft-delete)
};

export type SheetPreview = {
  sheet_id: string;
  gid: string;
  headers: string[];
  sample_rows: Record<string, string>[];
  total_rows: number;
  auto_mapping: Record<string, string>;
  access_method?: "service_account" | "public_csv";
};

export type SheetServiceAccount = {
  email: string;
  instructions: string;
};

export type SheetOrder = {
  row_key: string;
  row_index: number;
  order_id: string;
  customer_name: string;
  phone: string;
  address: string;
  city: string;
  state: string;
  pincode: string;
  item: string;
  amount: string;
  timestamp: string;
  already_shipped: boolean;
  raw: Record<string, string>;
};

// Phase-3a Plans & Usage ----------------------------------------------------

export type PlanKey = "free_trial" | "silver" | "gold" | "platinum";

// 2026-04-30 — Coupon system types
export type Coupon = {
  id: string;
  code: string;                     // UPPERCASE
  discount_type: "flat" | "percent";
  discount_value: number;
  valid_from: string;               // ISO datetime
  valid_to:   string;               // ISO datetime
  max_uses:   number | null;        // null = unlimited
  used_count: number;
  applies_to_plans: ("silver" | "gold" | "platinum")[];  // empty = all paid
  billing_cycles: ("monthly" | "yearly")[];               // empty = both
  active: boolean;
  status: "active" | "paused" | "scheduled" | "expired" | "exhausted";
  restricted_to_users?: string[];    // 2026-04-30 Phase-2 — allow-list
  created_at: string;
  updated_at: string;
};

export type CouponCreatePayload = {
  code: string;
  discount_type: "flat" | "percent";
  discount_value: number;
  valid_from: string;
  valid_to: string;
  max_uses?: number | null;
  applies_to_plans?: ("silver" | "gold" | "platinum")[];
  billing_cycles?: ("monthly" | "yearly")[];
  active?: boolean;
  restricted_to_users?: string[];
};

export type PlanPricingEntry = {
  monthly_price: number;
  monthly_anchor: number;
  yearly_price: number;
  yearly_anchor: number;
  yearly_base_months: number;
  yearly_bonus_months: number;
  show_strikethrough: boolean;
};

export type CountdownConfig = {
  enabled: boolean;
  mode: "off" | "per_device" | "global";
  countdown_minutes: number;
  global_expires_at: string | null;
  headline: string;
};

export type PlanSpec = {
  key: PlanKey;
  name: string;
  feel: string;
  purpose: string;
  price_inr: number;
  label_cap: number;
  period: "trial" | "month";
  trial_days: number | null;
  bulk_max: number;
  daily_cap: number | null;
  badge: "Most Popular" | "🚀" | null;
  cta: string;
};

export type UsageSummary = {
  plan: PlanKey;
  plan_name: string;
  price_inr: number;
  bulk_max: number;
  can_bulk: boolean;
  daily_cap: number | null;
  period: "trial" | "month";
  label_cap: number;
  labels_used: number;
  labels_remaining: number;
  can_create_label: boolean;
  // trial-only
  trial_expires_at?: string | null;
  trial_days_left?: number | null;
  trial_expired?: boolean;
  // monthly-only
  period_key?: string;
  // Paid-plan validity (Phase-4d Razorpay Subscriptions)
  plan_expires_at?: string | null;
  plan_days_left?: number | null;
  plan_expired?: boolean;
  plan_billing_cycle?: "monthly" | "yearly" | null;
  // Platinum-only
  today_used?: number;
  today_remaining?: number;
  daily_key?: string;
};

export type NotificationPrefs = {
  trial_ending: boolean;
  plan_expiring: boolean;
  low_credits: boolean;
  payment_success: boolean;
  daily_summary: boolean;
  channel_push: boolean;
  channel_email: boolean;
};

// Phase-4a Credit Wallet ----------------------------------------------------

export type Wallet = {
  total_credits: number;
  used_credits: number;
  remaining_credits: number;
  updated_at?: string;
};

export type CreditHistoryEntry = {
  id: string;
  user_id: string;
  created_at: string;
  order_id: string;
  credits: number;                       // signed: negative = debit, positive = credit
  type: "ai_processing" | "shipment_charge" | "purchase" | "bonus" | "refund";
  address_type: "" | "simple" | "medium" | "complex";
  description: string;
  balance_after: number;
};

export type WalletQuote = {
  plan: PlanKey;
  plan_has_room: boolean;
  trial_expired: boolean;
  daily_blocked: boolean;
  ai_complexity: "simple" | "medium" | "complex";
  ai_reason?: string;
  ai_credits: number;
  ai_applies: boolean;
  shipment_credits: number;
  total: number;
  wallet_balance: number;
  can_afford: boolean;
};

export const Api = {
  listCouriers: () => api.get<Courier[]>("/couriers").then((r) => r.data),
  getCourier: (id: string) => api.get<Courier>(`/couriers/${id}`).then((r) => r.data),
  createCourier: (data: Partial<Courier>) =>
    api.post<Courier>("/couriers", data).then((r) => r.data),
  getCourierLimits: () =>
    api
      .get<{
        plan: string;
        plan_label: string;
        is_admin: boolean;
        limit: number | null;
        current_count: number;
        can_add: boolean;
        is_unlimited: boolean;
        suggested_upgrade: string | null;
      }>("/couriers/limits")
      .then((r) => r.data),
  updateCourier: (id: string, data: Partial<Courier>) =>
    api.put<Courier>(`/couriers/${id}`, data).then((r) => r.data),
  deleteCourier: (id: string) => api.delete(`/couriers/${id}`).then((r) => r.data),
  peekNextTracking: (id: string) =>
    api
      .get<{ tracking_id: string; next_number: number }>(
        `/couriers/${id}/next-tracking`
      )
      .then((r) => r.data),
  consumeTracking: (id: string) =>
    api
      .post<{ tracking_id: string }>(`/couriers/${id}/consume-tracking`)
      .then((r) => r.data),

  // --- Phase-3a Plans & Usage ---
  listPlans: () =>
    api
      .get<{ plans: PlanSpec[]; current: PlanKey }>("/plans")
      .then((r) => r.data),
  myUsage: () => api.get<UsageSummary>("/me/usage").then((r) => r.data),

  // Phase-5c Anchor pricing & countdown (admin tunable)
  getPlansPricing: () =>
    api
      .get<{
        plan_pricing: Record<PlanKey, PlanPricingEntry>;
        countdown: CountdownConfig;
      }>("/plans-pricing")
      .then((r) => r.data),
  upgradePlan: (plan: PlanKey) =>
    api
      .post<{
        ok: boolean;
        mocked: boolean;
        plan: PlanKey;
        plan_started_at: string;
        plan_expires_at: string | null;
      }>("/plans/upgrade", { plan })
      .then((r) => r.data),

  // --- Phase-4a Wallet ---
  getWallet: () => api.get<Wallet>("/wallet").then((r) => r.data),
  getWalletHistory: (limit: number = 100) =>
    api
      .get<{ entries: CreditHistoryEntry[]; count: number }>(
        "/wallet/history",
        { params: { limit } },
      )
      .then((r) => r.data),
  purchaseCredits: (amount_inr: number) =>
    api
      .post<{
        ok: boolean;
        mocked: boolean;
        amount_inr: number;
        credits_added: number;
        balance: number;
        history_id: string;
      }>("/wallet/purchase", { amount_inr })
      .then((r) => r.data),
  // Phase-4c Razorpay live payments
  rzpCreateOrder: (amount_inr: number) =>
    api.post<{
      key_id: string;
      order_id: string;
      amount_paise: number;
      amount_inr: number;
      currency: string;
      receipt: string;
      credits_to_grant: number;
      bonus_credits: number;
      user_email: string;
      user_name: string;
    }>("/wallet/razorpay/create-order", { amount_inr }).then((r) => r.data),
  rzpVerify: (
    razorpay_order_id: string,
    razorpay_payment_id: string,
    razorpay_signature: string,
  ) =>
    api.post<{
      ok: true;
      already_credited: boolean;
      amount_inr?: number;
      credits_added: number;
      bonus?: number;
      balance: number;
    }>("/wallet/razorpay/verify", {
      razorpay_order_id,
      razorpay_payment_id,
      razorpay_signature,
    }).then((r) => r.data),
  // Phase-4d Razorpay Plan Subscriptions
  rzpCreatePlanOrder: (
    plan_key: PlanKey,
    billing_cycle: "monthly" | "yearly",
    coupon_code?: string,
  ) =>
    api.post<{
      key_id: string;
      order_id: string;
      amount_paise: number;
      amount_inr: number;
      currency: string;
      receipt: string;
      purpose: "plan_subscription";
      plan_key: PlanKey;
      plan_name: string;
      billing_cycle: "monthly" | "yearly";
      months: number;
      bonus_months: number;
      user_email: string;
      user_name: string;
      base_inr?: number;
      coupon?: {
        applied: boolean;
        code?: string;
        discount?: number;
        base_inr?: number;
        final_inr?: number;
      };
    }>("/plans/razorpay/create-order", { plan_key, billing_cycle, coupon_code: coupon_code || undefined })
      .then((r) => r.data),
  rzpVerifyPlan: (
    razorpay_order_id: string,
    razorpay_payment_id: string,
    razorpay_signature: string,
  ) =>
    api.post<{
      ok: true;
      already_credited: boolean;
      plan: PlanKey;
      billing_cycle: "monthly" | "yearly";
      amount_inr?: number;
      months?: number;
      bonus_months?: number;
      plan_expires_at: string | null;
    }>("/plans/razorpay/verify", {
      razorpay_order_id,
      razorpay_payment_id,
      razorpay_signature,
    }).then((r) => r.data),

  // ── Coupons (2026-04-30) ──────────────────────────────────────
  // Admin CRUD endpoints (gated server-side by is_admin):
  adminListCoupons: () =>
    api.get<{ coupons: Coupon[] }>("/admin/coupons").then((r) => r.data.coupons),
  adminCreateCoupon: (payload: CouponCreatePayload) =>
    api.post<{ ok: true; coupon: Coupon }>("/admin/coupons", payload).then((r) => r.data.coupon),
  adminUpdateCoupon: (id: string, payload: Partial<CouponCreatePayload>) =>
    api.put<{ ok: true; coupon: Coupon }>(`/admin/coupons/${id}`, payload).then((r) => r.data.coupon),
  adminDeleteCoupon: (id: string) =>
    api.delete<{ ok: true; deleted: string }>(`/admin/coupons/${id}`).then((r) => r.data),
  adminCouponAnalytics: () =>
    api.get<{
      totals: { redemptions: number; total_discount: number; total_revenue: number };
      coupons: Array<{
        code: string;
        redemptions: number;
        total_discount: number;
        total_revenue: number;
        plans: string[];
        cycles: string[];
        last_redeemed?: string;
        status: string;
        discount_type?: string;
        discount_value?: number;
      }>;
      total_coupons: number;
      status_counts: Record<string, number>;
    }>("/admin/coupons/analytics").then((r) => r.data),
  // User-facing validate. Never writes — only the payment-verify path
  // bumps `used_count` after a successful Razorpay charge.
  validateCoupon: (
    code: string,
    plan_key: PlanKey,
    billing_cycle: "monthly" | "yearly",
  ) =>
    api.post<{
      ok: boolean;
      reason: string;
      code: string;
      base_inr: number;
      discount: number;
      final_inr: number;
      savings_pct: number;
    }>("/coupons/validate", { code, plan_key, billing_cycle }).then((r) => r.data),
  // Phase-4d notification prefs + subscription mgmt
  getNotificationPrefs: () =>
    api.get<NotificationPrefs>("/me/notification-prefs").then((r) => r.data),
  updateNotificationPrefs: (prefs: Partial<NotificationPrefs>) =>
    api.put<NotificationPrefs>("/me/notification-prefs", prefs).then((r) => r.data),
  cancelSubscription: () =>
    api.post<{
      ok: true;
      plan: PlanKey;
      plan_expires_at: string | null;
      message: string;
    }>("/me/cancel-subscription").then((r) => r.data),
  quoteLabel: (address: string = "") =>
    api
      .get<WalletQuote>("/wallet/quote", { params: { address } })
      .then((r) => r.data),

  getSettings: () => api.get<Settings>("/settings").then((r) => r.data),
  updateSettings: (data: Partial<Settings>) =>
    api.put<Settings>("/settings", data).then((r) => r.data),

  sheetsPreview: (url: string) =>
    api.post<SheetPreview>("/sheets/preview", { url }).then((r) => r.data),
  sheetsServiceAccount: () =>
    api.get<SheetServiceAccount>("/sheets/service-account").then((r) => r.data),
  sheetsOrders: () =>
    api
      .get<{
        headers: string[];
        headers_changed: boolean;
        orders: SheetOrder[];
        total: number;
      }>("/sheets/orders")
      .then((r) => r.data),

  listShipments: (params?: {
    status?: string;
    courier_id?: string;
    search?: string;
  }) => api.get<Shipment[]>("/shipments", { params }).then((r) => r.data),
  getStats: () =>
    api
      .get<{
        total: number;
        delivered: number;
        pending: number;
        cod_total: number;
        cod_count: number;
        prepaid_total: number;
        prepaid_count: number;
        revenue_total: number;
      }>("/shipments/stats")
      .then((r) => r.data),
  /**
   * Lookup an existing shipment by tracking_id. Returns null when no
   * match (HTTP 404) instead of throwing, so callers don't have to
   * try/catch — and React Native's "Uncaught (in promise) AxiosError
   * 404" dev warning never fires for the expected "tracking ID is new"
   * code path.
   */
  getShipmentByTracking: (tracking_id: string) =>
    api
      .get<Shipment>(`/shipments/by-tracking/${encodeURIComponent(tracking_id)}`)
      .then((r) => r.data)
      .catch((err) => {
        if (err?.response?.status === 404) return null as any;
        throw err;
      }),
  bulkFetch: (ids: string[]) =>
    api.post<Shipment[]>(`/shipments/bulk-fetch`, { ids }).then((r) => r.data),
  getShipment: (id: string) =>
    api.get<Shipment>(`/shipments/${id}`).then((r) => r.data),
  createShipment: (data: Partial<Shipment>) =>
    api.post<Shipment>("/shipments", data).then((r) => r.data),
  updateShipment: (id: string, data: Partial<Shipment>) =>
    api.put<Shipment>(`/shipments/${id}`, data).then((r) => r.data),
  deleteShipment: (id: string) =>
    api.delete(`/shipments/${id}`).then((r) => r.data),
  csvUrl: () => `${BASE}/api/shipments/export/csv`,

  // Smart Paste & Pending Orders
  smartPasteParse: (text: string) =>
    api.post<{
      fields: any;
      confidence: any;
      warnings: string[];
      ai?: {
        used: boolean;
        missing: string[];
        complexity: "simple" | "medium" | "complex" | "";
        reason: string;
        source: "llm" | "regex" | "fallback";
      };
    }>("/smart-paste/parse", { text })
      .then((r) => r.data),
  smartPasteCheckDuplicate: (text: string) =>
    api.post<{
      fields: any;
      confidence: any;
      warnings: string[];
      ai?: {
        used: boolean;
        missing: string[];
        complexity: "simple" | "medium" | "complex" | "";
        reason: string;
        source: "llm" | "regex" | "fallback";
      };
      duplicates: Array<{
        kind: "pending" | "shipment";
        id: string;
        tracking_id?: string;
        customer_name: string;
        customer_phone: string;
        order_id: string;
        status: string;
        created_at: string;
        match_on: string[];
      }>;
    }>("/smart-paste/check-duplicate", { text }).then((r) => r.data),
  smartPasteCreate: (text: string, skipLlm: boolean = false) =>
    api.post<PendingOrder>("/smart-paste", { text, skip_llm: skipLlm }).then((r) => r.data),

  // Phase-C: Pull every Master-Sheet row tagged with the caller's
  // user_id into the caller's own personal sheet. `overwrite=true`
  // (default) clears the user's tab data rows first to reflect any
  // admin edits. `false` appends only new rows (dedup by master_order_id
  // / composite key for legacy rows).
  syncFromMaster: (overwrite: boolean = true) =>
    api.post<{
      ok: boolean;
      rows_synced: number;
      master_total_rows: number;
      tab: string;
      sheet_id: string;
      mode: "overwrite" | "append";
    }>("/sheets/sync-from-master", { overwrite }).then((r) => r.data),

  // Phase-7e: Live preview of the next Master Order ID for the New
  // Shipment form. Does NOT consume the counter — actual ID is allocated
  // at save time. Frontend may pass the previewed value back via
  // `master_order_id` in POST /shipments to lock it in.
  peekMasterOrderId: () =>
    api.get<{
      master_order_id: string;
      auto_generate: boolean;
      autofill_in_new_shipment: boolean;
    }>("/orders/peek-master-id").then((r) => r.data),

  // Phase-7f: Read / write the global Master Order ID counter.
  // Used in Settings to migrate from a legacy numbering system —
  // eg user has shipped 2200 parcels and wants the next master ID
  // to start from `02201`. setMasterIdCounter forces seq=2200 so the
  // next allocation produces `<YYMMDD>02201`.
  getMasterIdCounter: () =>
    api.get<{
      current_seq: number;
      next_seq: number;
      next_master_order_id: string;
    }>("/orders/master-id-counter").then((r) => r.data),
  setMasterIdCounter: (seq: number, force: boolean = false) =>
    api.post<{
      current_seq: number;
      next_seq: number;
      next_master_order_id: string;
    }>("/orders/master-id-counter", { seq, force }).then((r) => r.data),

  // Conversational Smart Paste (chat UI).
  smartPasteChat: (fields: Record<string, any>, reply: string) =>
    api.post<{
      fields: Record<string, any>;
      missing: string[];
      complete: boolean;
      ai_message: string;
      complexity: "simple" | "medium" | "complex" | "";
      reason: string;
      source: "llm" | "regex" | "fallback";
    }>("/smart-paste/chat", { fields, reply }).then((r) => r.data),

  // Photo OCR — Gemini Vision. Uses an extended 90 s timeout because
  // vision calls + the address-recovery follow-up can take up to ~45 s
  // on a slow mobile connection. The default 25 s axios timeout would
  // surface as "Network Error" on the client even though the backend
  // is still happily processing.
  smartPastePhoto: (image_base64: string, mime: string = "image/jpeg") =>
    api.post<{
      fields: Record<string, any>;
      missing: string[];
      complete: boolean;
      ai_message: string;
      complexity: "simple" | "medium" | "complex" | "";
      reason: string;
      source: "llm" | "regex" | "fallback";
      credits_charged: number;
    }>(
      "/smart-paste/photo",
      { image_base64, mime },
      { timeout: 90000 },
    ).then((r) => r.data),

  // Customer memory — look up past customer by phone for auto-suggest.
  lookupCustomerByPhone: (phone: string) =>
    api.get<{
      found: boolean;
      count: number;
      customer: {
        customer_name: string;
        customer_phone: string;
        address_line1: string;
        address_line2: string;
        city: string;
        state: string;
        pincode: string;
        source: "shipment" | "pending";
        last_tracking_id?: string;
        last_date?: string;
      } | null;
    }>(`/customers/by-phone/${encodeURIComponent(phone)}`).then((r) => r.data),

  // Phase-4b+ Smart Paste AI helpers
  smartPasteDefaultPrompt: () =>
    api.get<{
      default_prompt: string;
      user_instructions: string;
      ai_enabled: boolean;
    }>("/smart-paste/default-prompt").then((r) => r.data),
  listPendingOrders: (params?: { source?: string; status?: string }) =>
    api.get<PendingOrder[]>("/orders/pending", { params }).then((r) => r.data),
  getPendingOrder: (id: string) =>
    api.get<PendingOrder>(`/orders/pending/${id}`).then((r) => r.data),
  updatePendingOrder: (id: string, data: Partial<PendingOrder>) =>
    api.put<PendingOrder>(`/orders/pending/${id}`, data).then((r) => r.data),
  deletePendingOrder: (id: string) =>
    api.delete(`/orders/pending/${id}`).then((r) => r.data),
  shipPendingOrder: (id: string, courier_id: string, overrides?: any) =>
    api.post<Shipment>(`/orders/pending/${id}/ship`, { courier_id, overrides }).then((r) => r.data),
  pendingOrdersCount: () =>
    api.get<{ count: number }>("/orders/pending-count").then((r) => r.data),
};

export type PendingOrder = {
  id: string;
  source: "paste" | "sheet" | "manual";
  status: "pending" | "shipped" | "skipped";
  master_order_id?: string;
  order_id?: string;
  customer_name: string;
  customer_phone: string;
  customer_alt_phone?: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  pincode: string;
  items: string;
  amount: number;
  token_amount?: number;
  payment_mode: "COD" | "PAID";
  courier_hint?: string;
  order_id_hint?: string;
  weight?: string;
  notes?: string;
  sheet_row_num?: number;
  raw_text?: string;
  shipment_id?: string;
  tracking_id?: string;
  confidence?: Record<string, string>;
  warnings?: string[];
  created_at: string;
  processed_at?: string;
};

export const SHEET_FIELDS: { key: string; label: string }[] = [
  { key: "order_id", label: "Order ID" },
  { key: "customer_name", label: "Customer Name" },
  { key: "phone", label: "Phone" },
  { key: "address", label: "Address" },
  { key: "city", label: "City" },
  { key: "state", label: "State" },
  { key: "pincode", label: "Pincode" },
  { key: "item", label: "Item / Product" },
  { key: "amount", label: "Amount" },
  { key: "timestamp", label: "Timestamp" },
];
