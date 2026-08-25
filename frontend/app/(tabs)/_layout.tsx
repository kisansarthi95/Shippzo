import { Tabs } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { NewOrderAlertProvider } from "../../lib/new_order_alert";
import { OfdAlertProvider } from "../../lib/ofd_alerts";

export default function TabsLayout() {
  return (
    <NewOrderAlertProvider>
    <OfdAlertProvider>
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: "#9CA3AF",
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopWidth: 2,
          borderTopColor: "#E5E7EB",
          paddingTop: 8,
        },
        tabBarIconStyle: { marginBottom: -2 },
        tabBarLabelStyle: {
          fontSize: 11,
          fontWeight: "700",
          letterSpacing: 0.3,
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          title: "Home",
          tabBarIcon: ({ color }) => (
            <PhIcon name="home" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="orders"
        options={{
          title: "Orders",
          tabBarIcon: ({ color }) => (
            <PhIcon name="list" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="audience"
        options={{
          title: "Audience",
          tabBarIcon: ({ color }) => (
            <PhIcon name="people" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="shipments"
        options={{
          title: "Shipments",
          tabBarIcon: ({ color }) => (
            <PhIcon name="cube" size={24} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: ({ color }) => (
            <PhIcon name="settings-sharp" size={24} color={color} />
          ),
        }}
      />
      {/* Phase F12 — the old central "+" (Ship) tab is hidden from the
          bar. The manual entry screen lives on at /add and is opened
          via the FAB on the Audience tab. */}
      <Tabs.Screen
        name="add"
        options={{
          href: null,
        }}
      />
    </Tabs>
    </OfdAlertProvider>
    </NewOrderAlertProvider>
  );
}
