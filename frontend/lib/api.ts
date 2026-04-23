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
  // Platinum-only
  today_used?: number;
  today_remaining?: number;
  daily_key?: string;
};

export const Api = {
  listCouriers: () => api.get<Courier[]>("/couriers").then((r) => r.data),
  getCourier: (id: string) => api.get<Courier>(`/couriers/${id}`).then((r) => r.data),
  createCourier: (data: Partial<Courier>) =>
    api.post<Courier>("/couriers", data).then((r) => r.data),
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

  getSettings: () => api.get<Settings>("/settings").then((r) => r.data),
  updateSettings: (data: Partial<Settings>) =>
    api.put<Settings>("/settings", data).then((r) => r.data),

  sheetsPreview: (url: string) =>
    api.post<SheetPreview>("/sheets/preview", { url }).then((r) => r.data),
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
  getShipmentByTracking: (tracking_id: string) =>
    api.get<Shipment>(`/shipments/by-tracking/${encodeURIComponent(tracking_id)}`).then((r) => r.data),
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
    api.post<{ fields: any; confidence: any; warnings: string[] }>("/smart-paste/parse", { text })
      .then((r) => r.data),
  smartPasteCheckDuplicate: (text: string) =>
    api.post<{
      fields: any;
      confidence: any;
      warnings: string[];
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
  smartPasteCreate: (text: string) =>
    api.post<PendingOrder>("/smart-paste", { text }).then((r) => r.data),
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
  customer_name: string;
  customer_phone: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  pincode: string;
  items: string;
  amount: number;
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
