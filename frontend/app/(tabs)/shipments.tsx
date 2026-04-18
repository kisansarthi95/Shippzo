import React, { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, TextInput, ScrollView, TouchableOpacity,
  FlatList, RefreshControl, Linking, Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { useFocusEffect, useRouter } from "expo-router";
import { Api, Shipment, Settings, Courier } from "../../lib/api";
import { buildCopyText, buildWhatsAppText, cleanPhone } from "../../lib/format";
import { colors } from "../../lib/theme";

type StatusFilter = "All" | "Pending" | "Delivered" | "Cancelled";

export default function Shipments() {
  const router = useRouter();
  const [items, setItems] = useState<Shipment[]>([]);
  const [couriers, setCouriers] = useState<Courier[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("All");
  const [refreshing, setRefreshing] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);

  const load = useCallback(async () => {
    const q: any = { search: search || undefined };
    if (status !== "All") q.status = status;
    const [list, s, cs] = await Promise.all([
      Api.listShipments(q), Api.getSettings(), Api.listCouriers(),
    ]);
    setItems(list);
    setSettings(s);
    setCouriers(cs);
    setRefreshing(false);
  }, [search, status]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const findCourier = (s: Shipment) =>
    couriers.find((c) => c.id === s.courier_id) || null;

  const toggleDelivered = async (s: Shipment) => {
    const newStatus = s.status === "Delivered" ? "Pending" : "Delivered";
    await Api.updateShipment(s.id, { status: newStatus });
    load();
  };

  const remove = (s: Shipment) => {
    Alert.alert("Delete", `Delete ${s.tracking_id}?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete", style: "destructive",
        onPress: async () => {
          await Api.deleteShipment(s.id);
          load();
        },
      },
    ]);
  };

  const sendWhatsApp = (s: Shipment) => {
    if (!s.customer_phone) {
      Alert.alert("No phone", "Customer phone not set.");
      return;
    }
    const msg = buildWhatsAppText(s, settings);
    const phone = cleanPhone(s.customer_phone);
    Linking.openURL(`https://wa.me/${phone}?text=${encodeURIComponent(msg)}`);
  };

  const copyAll = async (s: Shipment) => {
    const text = buildCopyText(s, settings, findCourier(s));
    await Clipboard.setStringAsync(text);
    Alert.alert("Copied", "Tracking details copied to clipboard.");
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Shipments</Text>
        <TouchableOpacity
          testID="export-csv-btn" style={styles.iconBtn}
          onPress={() => Linking.openURL(Api.csvUrl())}
        >
          <Ionicons name="download-outline" size={20} color={colors.text} />
        </TouchableOpacity>
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={18} color={colors.textMuted} />
        <TextInput
          testID="search-input"
          placeholder="Search tracking, name, city, order..."
          placeholderTextColor="#9CA3AF"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={load}
          style={styles.searchInput}
          returnKeyType="search"
        />
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filterRow}>
        {(["All", "Pending", "Delivered", "Cancelled"] as StatusFilter[]).map((f) => {
          const active = status === f;
          return (
            <TouchableOpacity
              key={f} testID={`filter-${f.toLowerCase()}`}
              style={[styles.filterPill, active && styles.filterPillActive]}
              onPress={() => setStatus(f)}
            >
              <Text style={[styles.filterText, active && { color: "#fff" }]}>{f}</Text>
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      <FlatList
        testID="shipments-list"
        data={items}
        keyExtractor={(i) => i.id}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => {
            setRefreshing(true); load();
          }} />
        }
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        ListEmptyComponent={
          <View style={styles.empty} testID="empty-shipments">
            <Ionicons name="cube-outline" size={48} color="#9CA3AF" />
            <Text style={styles.emptyText}>કોઈ shipment નથી મળી.</Text>
            <TouchableOpacity
              testID="empty-new-shipment" style={styles.primaryBtn}
              onPress={() => router.push("/(tabs)/add")}
            >
              <Text style={styles.primaryBtnText}>+ New Shipment</Text>
            </TouchableOpacity>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.card} testID={`shipment-${item.tracking_id}`}>
            <TouchableOpacity style={{ flex: 1 }}
              onPress={() => router.push(`/label/${item.id}`)}>
              <View style={styles.row}>
                <Text style={styles.track}>{item.tracking_id}</Text>
                <StatusChip status={item.status} />
              </View>
              <Text style={styles.name}>{item.customer_name}</Text>
              {!!item.order_id && (
                <Text style={styles.order}>Order #{item.order_id}</Text>
              )}
              <Text style={styles.sub} numberOfLines={1}>
                {item.courier_name} · {item.city || "—"} · {
                  item.payment_mode === "COD"
                    ? `COD ₹${item.amount || item.cod_amount}`
                    : `Prepaid ₹${item.amount}`
                }
              </Text>
              {item.items && item.items.length > 0 && (
                <Text style={styles.items} numberOfLines={1}>
                  📦 {item.items.join(", ")}
                </Text>
              )}
            </TouchableOpacity>
            <View style={styles.actions}>
              <ActionBtn
                icon={item.status === "Delivered" ? "checkmark-done-circle" : "checkmark-circle-outline"}
                color={item.status === "Delivered" ? colors.successText : colors.textMuted}
                onPress={() => toggleDelivered(item)}
                testID={`toggle-delivered-${item.tracking_id}`}
              />
              <ActionBtn
                icon="copy-outline" color={colors.text}
                onPress={() => copyAll(item)}
                testID={`copy-all-${item.tracking_id}`}
              />
              <ActionBtn
                icon="logo-whatsapp" color="#25D366"
                onPress={() => sendWhatsApp(item)}
                testID={`whatsapp-${item.tracking_id}`}
              />
              <ActionBtn
                icon="print-outline" color={colors.text}
                onPress={() => router.push(`/label/${item.id}`)}
                testID={`print-${item.tracking_id}`}
              />
              <ActionBtn
                icon="trash-outline" color={colors.dangerText}
                onPress={() => remove(item)}
                testID={`delete-${item.tracking_id}`}
              />
            </View>
          </View>
        )}
      />
    </SafeAreaView>
  );
}

function ActionBtn({
  icon, color, onPress, testID,
}: {
  icon: keyof typeof import("@expo/vector-icons").Ionicons.glyphMap;
  color: string;
  onPress: () => void;
  testID?: string;
}) {
  return (
    <TouchableOpacity testID={testID} onPress={onPress} style={styles.actionBtn}>
      <Ionicons name={icon} size={18} color={color} />
    </TouchableOpacity>
  );
}

function StatusChip({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    Delivered: { bg: colors.successBg, fg: colors.successText },
    Pending: { bg: colors.warningBg, fg: colors.warningText },
    Cancelled: { bg: colors.dangerBg, fg: colors.dangerText },
  };
  const m = map[status] || map.Pending;
  return (
    <View style={[styles.chip, { backgroundColor: m.bg }]}>
      <Text style={[styles.chipText, { color: m.fg }]}>{status.toUpperCase()}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    paddingHorizontal: 20, paddingTop: 10, paddingBottom: 8,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  iconBtn: {
    width: 40, height: 40, borderRadius: 10, borderWidth: 2,
    borderColor: "#E5E7EB", backgroundColor: "#fff",
    justifyContent: "center", alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16, marginTop: 4, flexDirection: "row",
    alignItems: "center", gap: 8, height: 46,
    backgroundColor: colors.surface, borderWidth: 2,
    borderColor: "#E5E7EB", borderRadius: 10, paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRow: { gap: 8, paddingHorizontal: 16, paddingVertical: 12 },
  filterPill: {
    paddingHorizontal: 14, paddingVertical: 8, borderWidth: 2,
    borderColor: "#E5E7EB", borderRadius: 999, backgroundColor: "#fff",
  },
  filterPillActive: { backgroundColor: colors.secondary, borderColor: colors.secondary },
  filterText: { fontWeight: "700", fontSize: 13, color: colors.text },
  card: {
    backgroundColor: colors.surface, borderWidth: 2, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 14, marginBottom: 10,
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  track: {
    fontFamily: "Courier", fontWeight: "800", fontSize: 14,
    letterSpacing: 1.5, color: colors.text,
  },
  name: { marginTop: 6, fontSize: 15, fontWeight: "700", color: colors.text },
  order: { fontSize: 11, color: colors.primary, fontWeight: "700", marginTop: 2 },
  sub: { marginTop: 3, color: colors.textMuted, fontSize: 12 },
  items: { marginTop: 3, color: colors.text, fontSize: 12, fontWeight: "600" },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  actions: {
    flexDirection: "row", justifyContent: "flex-end", gap: 6,
    marginTop: 12, borderTopWidth: 1, borderTopColor: "#F1F1F1", paddingTop: 10,
  },
  actionBtn: {
    width: 36, height: 36, borderRadius: 8, backgroundColor: "#F9FAFB",
    borderWidth: 1, borderColor: "#E5E7EB",
    justifyContent: "center", alignItems: "center",
  },
  empty: {
    alignItems: "center", padding: 30, backgroundColor: colors.surface,
    borderRadius: 12, borderWidth: 2, borderColor: "#E5E7EB", borderStyle: "dashed",
  },
  emptyText: { marginTop: 12, color: colors.textMuted, textAlign: "center" },
  primaryBtn: {
    marginTop: 16, backgroundColor: colors.primary,
    paddingHorizontal: 20, paddingVertical: 12, borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },
});
