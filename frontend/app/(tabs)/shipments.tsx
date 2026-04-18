import React, { useCallback, useState } from "react";
import {
  View,
  Text,
  StyleSheet,
  TextInput,
  ScrollView,
  TouchableOpacity,
  FlatList,
  RefreshControl,
  Linking,
  Alert,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Ionicons } from "@expo/vector-icons";
import { useFocusEffect, useRouter } from "expo-router";
import { Api, Shipment, Settings } from "../../lib/api";
import { colors } from "../../lib/theme";

type StatusFilter = "All" | "Pending" | "Delivered" | "Cancelled";

export default function Shipments() {
  const router = useRouter();
  const [items, setItems] = useState<Shipment[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("All");
  const [refreshing, setRefreshing] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);

  const load = useCallback(async () => {
    const q: any = { search: search || undefined };
    if (status !== "All") q.status = status;
    const [list, s] = await Promise.all([Api.listShipments(q), Api.getSettings()]);
    setItems(list);
    setSettings(s);
    setRefreshing(false);
  }, [search, status]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const toggleDelivered = async (s: Shipment) => {
    const newStatus = s.status === "Delivered" ? "Pending" : "Delivered";
    await Api.updateShipment(s.id, { status: newStatus });
    load();
  };

  const remove = (s: Shipment) => {
    Alert.alert("Delete", `Delete ${s.tracking_id}?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
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
    const tpl = settings?.whatsapp_template || "";
    const msg = tpl
      .replace("{customer_name}", s.customer_name)
      .replace("{courier}", s.courier_name)
      .replace("{tracking_id}", s.tracking_id)
      .replace("{eta_days}", String(settings?.default_eta_days ?? 7));
    const phone = s.customer_phone.replace(/\D/g, "");
    const finalPhone = phone.length === 10 ? `91${phone}` : phone;
    Linking.openURL(
      `https://wa.me/${finalPhone}?text=${encodeURIComponent(msg)}`
    );
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.title}>Shipments</Text>
        <TouchableOpacity
          testID="export-csv-btn"
          style={styles.iconBtn}
          onPress={() => Linking.openURL(Api.csvUrl())}
        >
          <Ionicons name="download-outline" size={20} color={colors.text} />
        </TouchableOpacity>
      </View>

      <View style={styles.searchWrap}>
        <Ionicons name="search" size={18} color={colors.textMuted} />
        <TextInput
          testID="search-input"
          placeholder="Search tracking, name, city..."
          placeholderTextColor="#9CA3AF"
          value={search}
          onChangeText={setSearch}
          onSubmitEditing={load}
          style={styles.searchInput}
          returnKeyType="search"
        />
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.filterRow}
      >
        {(["All", "Pending", "Delivered", "Cancelled"] as StatusFilter[]).map(
          (f) => {
            const active = status === f;
            return (
              <TouchableOpacity
                key={f}
                testID={`filter-${f.toLowerCase()}`}
                style={[styles.filterPill, active && styles.filterPillActive]}
                onPress={() => setStatus(f)}
              >
                <Text
                  style={[
                    styles.filterText,
                    active && { color: "#fff" },
                  ]}
                >
                  {f}
                </Text>
              </TouchableOpacity>
            );
          }
        )}
      </ScrollView>

      <FlatList
        testID="shipments-list"
        data={items}
        keyExtractor={(i) => i.id}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => {
              setRefreshing(true);
              load();
            }}
          />
        }
        contentContainerStyle={{ padding: 16, paddingBottom: 100 }}
        ListEmptyComponent={
          <View style={styles.empty} testID="empty-shipments">
            <Ionicons name="cube-outline" size={48} color="#9CA3AF" />
            <Text style={styles.emptyText}>
              કોઈ shipment નથી મળી. નવી shipment ઉમેરો.
            </Text>
            <TouchableOpacity
              testID="empty-new-shipment"
              style={styles.primaryBtn}
              onPress={() => router.push("/(tabs)/add")}
            >
              <Text style={styles.primaryBtnText}>+ New Shipment</Text>
            </TouchableOpacity>
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.card} testID={`shipment-${item.tracking_id}`}>
            <TouchableOpacity
              style={{ flex: 1 }}
              onPress={() => router.push(`/label/${item.id}`)}
            >
              <View style={styles.row}>
                <Text style={styles.track}>{item.tracking_id}</Text>
                <StatusChip status={item.status} />
              </View>
              <Text style={styles.name}>{item.customer_name}</Text>
              <Text style={styles.sub}>
                {item.courier_name} · {item.city || "—"}{" "}
                {item.payment_mode === "COD" ? `· COD ₹${item.cod_amount}` : "· Prepaid"}
              </Text>
            </TouchableOpacity>
            <View style={styles.actions}>
              <TouchableOpacity
                testID={`toggle-delivered-${item.tracking_id}`}
                style={styles.actionBtn}
                onPress={() => toggleDelivered(item)}
              >
                <Ionicons
                  name={
                    item.status === "Delivered"
                      ? "checkmark-done-circle"
                      : "checkmark-circle-outline"
                  }
                  size={22}
                  color={
                    item.status === "Delivered"
                      ? colors.successText
                      : colors.textMuted
                  }
                />
              </TouchableOpacity>
              <TouchableOpacity
                testID={`whatsapp-${item.tracking_id}`}
                style={styles.actionBtn}
                onPress={() => sendWhatsApp(item)}
              >
                <Ionicons name="logo-whatsapp" size={20} color="#25D366" />
              </TouchableOpacity>
              <TouchableOpacity
                testID={`print-${item.tracking_id}`}
                style={styles.actionBtn}
                onPress={() => router.push(`/label/${item.id}`)}
              >
                <Ionicons name="print-outline" size={20} color={colors.text} />
              </TouchableOpacity>
              <TouchableOpacity
                testID={`delete-${item.tracking_id}`}
                style={styles.actionBtn}
                onPress={() => remove(item)}
              >
                <Ionicons name="trash-outline" size={20} color={colors.dangerText} />
              </TouchableOpacity>
            </View>
          </View>
        )}
      />
    </SafeAreaView>
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
      <Text style={[styles.chipText, { color: m.fg }]}>
        {status.toUpperCase()}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 20,
    paddingTop: 10,
    paddingBottom: 8,
  },
  title: { fontSize: 24, fontWeight: "800", color: colors.text },
  iconBtn: {
    width: 40,
    height: 40,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    backgroundColor: "#fff",
    justifyContent: "center",
    alignItems: "center",
  },
  searchWrap: {
    marginHorizontal: 16,
    marginTop: 4,
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    height: 46,
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 15 },
  filterRow: { gap: 8, paddingHorizontal: 16, paddingVertical: 12 },
  filterPill: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 999,
    backgroundColor: "#fff",
  },
  filterPillActive: {
    backgroundColor: colors.secondary,
    borderColor: colors.secondary,
  },
  filterText: { fontWeight: "700", fontSize: 13, color: colors.text },
  card: {
    backgroundColor: colors.surface,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  track: {
    fontFamily: "Courier",
    fontWeight: "800",
    fontSize: 14,
    letterSpacing: 1.5,
    color: colors.text,
  },
  name: { marginTop: 6, fontSize: 15, fontWeight: "700", color: colors.text },
  sub: { marginTop: 2, color: colors.textMuted, fontSize: 12 },
  chip: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  chipText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  actions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 6,
    marginTop: 12,
    borderTopWidth: 1,
    borderTopColor: "#F1F1F1",
    paddingTop: 10,
  },
  actionBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "#F9FAFB",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    justifyContent: "center",
    alignItems: "center",
  },
  empty: {
    alignItems: "center",
    padding: 30,
    backgroundColor: colors.surface,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderStyle: "dashed",
  },
  emptyText: { marginTop: 12, color: colors.textMuted, textAlign: "center" },
  primaryBtn: {
    marginTop: 16,
    backgroundColor: colors.primary,
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 10,
  },
  primaryBtnText: { color: "#fff", fontWeight: "800" },
});
