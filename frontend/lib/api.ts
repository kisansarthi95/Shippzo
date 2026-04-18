import axios from "axios";

const BASE = process.env.EXPO_PUBLIC_BACKEND_URL;

export const api = axios.create({
  baseURL: `${BASE}/api`,
  timeout: 20000,
});

export type Courier = {
  id: string;
  name: string;
  series_prefix: string;
  next_number: number;
  number_padding: number;
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

export type Settings = {
  id: string;
  sender: SenderAddress;
  whatsapp_template: string;
  default_eta_days: number;
};

export type Shipment = {
  id: string;
  tracking_id: string;
  courier_id?: string;
  courier_name: string;
  customer_name: string;
  customer_phone: string;
  address_line1: string;
  address_line2: string;
  city: string;
  state: string;
  pincode: string;
  payment_mode: "COD" | "Prepaid";
  cod_amount: number;
  weight: string;
  item_description: string;
  status: "Pending" | "Delivered" | "Cancelled";
  created_at: string;
  delivered_at?: string | null;
};

export const Api = {
  listCouriers: () => api.get<Courier[]>("/couriers").then((r) => r.data),
  createCourier: (data: Partial<Courier>) =>
    api.post<Courier>("/couriers", data).then((r) => r.data),
  updateCourier: (id: string, data: Partial<Courier>) =>
    api.put<Courier>(`/couriers/${id}`, data).then((r) => r.data),
  deleteCourier: (id: string) =>
    api.delete(`/couriers/${id}`).then((r) => r.data),
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

  getSettings: () => api.get<Settings>("/settings").then((r) => r.data),
  updateSettings: (data: Partial<Settings>) =>
    api.put<Settings>("/settings", data).then((r) => r.data),

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
      }>("/shipments/stats")
      .then((r) => r.data),
  getShipment: (id: string) =>
    api.get<Shipment>(`/shipments/${id}`).then((r) => r.data),
  createShipment: (data: Partial<Shipment>) =>
    api.post<Shipment>("/shipments", data).then((r) => r.data),
  updateShipment: (id: string, data: Partial<Shipment>) =>
    api.put<Shipment>(`/shipments/${id}`, data).then((r) => r.data),
  deleteShipment: (id: string) =>
    api.delete(`/shipments/${id}`).then((r) => r.data),
  csvUrl: () => `${BASE}/api/shipments/export/csv`,
};
