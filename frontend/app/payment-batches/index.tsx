// ==============================================================
// Phase F9 — Payment Batch History (list screen)
// --------------------------------------------------------------
// Full-screen list of every payment batch the operator has ever
// created (via COD-Payment imports or standalone). Tapping a card
// opens the drill-down at `/payment-batches/[id]`. Reused from
// the compact picker on the Shipments tab but with full space
// for search, meta chips, and the "New" affordance in the future.
// ==============================================================
import React, { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text,
  TextInput, TouchableOpacity, View, ScrollView, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack, useRouter } from "expo-router";
import { Ionicons } from "@expo/vector-icons";

import { Api } from "../../lib/api";
import { colors } from "../../lib/theme";

type Batch = {
  id: string;
  name: string;
  description?: string;
  payment_date: string;
  payment_mode: string;
  reference_number: string;
  bank_name?: string;
  total_articles: number;
  total_amount: number;
  created_at: string;
};

const MODE_LABEL: Record<string, string> = {
  cheque: "Cheque", neft: "NEFT", bank_transfer: "Bank Transfer",
  upi: "UPI", other: "Other",
};

const MODE_PILLS: { key: string; label: string }[] = [
  { key: "",        label: "All" },
  { key: "cheque",  label: "Cheque" },
  { key: "neft",    label: "NEFT" },
  { key: "upi",     label: "UPI" },
  { key: "bank_transfer", label: "Bank Transfer" },
  { key: "other",   label: "Other" },
];

export default function PaymentBatchesScreen() {
  const router = useRouter();
  const [items, setItems]       = useState<Batch[]>([]);
  const [loading, setLoading]   = useState(true);
  const [refreshing, setRefr]   = useState(false);
  const [search, setSearch]     = useState("");
  const [modeFilter, setMode]   = useState<string>("");

  const load = useCallback(async () => {
    try {
      const r = await Api.listPaymentBatches({
        search:       search.trim() || undefined,
        payment_mode: modeFilter || undefined,
        limit: 200,
      });
      setItems(r.batches || []);
    } catch (e: any) {
      Alert.alert("Load failed", e?.response?.data?.detail || e?.message || "Try again");
    } finally {
      setLoading(false);
      setRefr(false);
    }
  }, [search, modeFilter]);

  useEffect(() => { load(); }, [load]);

  const renderItem = ({ item }: { item: Batch }) => (
    <TouchableOpacity
      style={styles.card}
      onPress={() => router.push(`/payment-batches/${item.id}` as any)}
    >
      <View style={styles.cardHead}>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle} numberOfLines={1}>{item.name}</Text>
          <Text style={styles.cardSub} numberOfLines={1}>
            {MODE_LABEL[item.payment_mode] || item.payment_mode} · Ref{" "}
            {item.reference_number}
          </Text>
        </View>
        <Ionicons name="chevron-forward" size={20} color="#94A3B8" />
      </View>
      <View style={styles.metaRow}>
        <View style={styles.metaPill}>
          <Text style={styles.metaPillTxt}>
            📦 {item.total_articles} article{item.total_articles === 1 ? "" : "s"}
          </Text>
        </View>
        <View style={[styles.metaPill, { backgroundColor: "#DCFCE7" }]}>
          <Text style={[styles.metaPillTxt, { color: "#166534" }]}>
            ₹{Number(item.total_amount || 0).toLocaleString("en-IN")}
          </Text>
        </View>
        <Text style={styles.dateTxt}>{(item.payment_date || "").slice(0, 10)}</Text>
      </View>
    </TouchableOpacity>
  );

  return (
    <SafeAreaView style={styles.root} edges={["top"]}>
      <Stack.Screen options={{ title: "Payment Batches", headerShown: false }} />
      <View style={styles.header}>
        <TouchableOpacity onPress={() => router.back()} style={{ padding: 4 }}>
          <Ionicons name="chevron-back" size={26} color={colors.primary} />
        </TouchableOpacity>
        <Text style={styles.hTitle}>Payment Batches</Text>
        <View style={{ width: 26 }} />
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={16} color="#94A3B8" />
        <TextInput
          value={search}
          onChangeText={setSearch}
          placeholder="Search by name, ref#, notes…"
          placeholderTextColor="#94A3B8"
          style={styles.searchInput}
          returnKeyType="search"
          onSubmitEditing={load}
        />
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={{ paddingHorizontal: 12, gap: 6 }}
        style={{ maxHeight: 40 }}
      >
        {MODE_PILLS.map((p) => (
          <TouchableOpacity
            key={p.key || "all"}
            style={[
              styles.filterPill,
              modeFilter === p.key && styles.filterPillActive,
            ]}
            onPress={() => setMode(p.key)}
          >
            <Text style={[
              styles.filterPillTxt,
              modeFilter === p.key && styles.filterPillTxtActive,
            ]}>{p.label}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : items.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="wallet-outline" size={48} color="#CBD5E1" />
          <Text style={styles.emptyTxt}>No payment batches yet</Text>
          <Text style={styles.emptySub}>
            Batches are created automatically when you import a COD-payment
            reconciliation file with a Reference Number.
          </Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => i.id}
          renderItem={renderItem}
          contentContainerStyle={{ padding: 12, gap: 8 }}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => { setRefr(true); load(); }}
              tintColor={colors.primary}
            />
          }
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root:      { flex: 1, backgroundColor: "#F8FAFC" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 8,
    paddingVertical: 10,
    backgroundColor: "#fff",
    borderBottomWidth: 1,
    borderBottomColor: "#E2E8F0",
  },
  hTitle: { fontSize: 17, fontWeight: "800", color: "#0F172A" },
  searchWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    margin: 12,
    marginBottom: 8,
    paddingHorizontal: 12,
    height: 40,
    backgroundColor: "#fff",
    borderRadius: 10,
    borderWidth: 1,
    borderColor: "#E2E8F0",
  },
  searchInput: { flex: 1, fontSize: 14, color: "#0F172A", paddingVertical: 0 },
  filterPill: {
    paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 16, backgroundColor: "#E2E8F0",
    borderWidth: 1, borderColor: "#E2E8F0",
    alignSelf: "flex-start",
  },
  filterPillActive: { backgroundColor: "#DBEAFE", borderColor: "#93C5FD" },
  filterPillTxt:      { fontSize: 12, fontWeight: "700", color: "#475569" },
  filterPillTxtActive:{ color: "#1E40AF" },
  card: {
    backgroundColor: "#fff", borderRadius: 12, padding: 14,
    borderWidth: 1, borderColor: "#E2E8F0",
  },
  cardHead:  { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 },
  cardTitle: { fontSize: 14.5, fontWeight: "800", color: "#0F172A" },
  cardSub:   { fontSize: 12, color: "#64748B", marginTop: 2 },
  metaRow:   { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  metaPill: {
    paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 8, backgroundColor: "#EFF6FF",
  },
  metaPillTxt: { fontSize: 11.5, fontWeight: "700", color: "#1E40AF" },
  dateTxt: { fontSize: 11.5, color: "#64748B", marginLeft: "auto" },
  center:  { flex: 1, alignItems: "center", justifyContent: "center", padding: 32, gap: 8 },
  emptyTxt:{ fontSize: 14.5, fontWeight: "700", color: "#475569", marginTop: 8 },
  emptySub:{ fontSize: 12.5, color: "#94A3B8", textAlign: "center", lineHeight: 17 },
});
