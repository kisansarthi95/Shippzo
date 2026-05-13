import { Tabs } from "expo-router";
import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Platform, View } from "react-native";
import { usePermissions } from "../../lib/permissions";
import { NewOrderAlertProvider } from "../../lib/new_order_alert";

export default function TabsLayout() {
  // Phase B+C — hide tabs the active team-member doesn't have
  // permission for. Owners pass through hasPerm() unconditionally.
  const { hasPerm, isTeamMember } = usePermissions();
  const canAdd = hasPerm("shipments_create");
  return (
    <NewOrderAlertProvider>
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: "#9CA3AF",
        tabBarStyle: {
          backgroundColor: colors.surface,
          borderTopWidth: 2,
          borderTopColor: "#E5E7EB",
          height: Platform.OS === "ios" ? 92 : 78,
          paddingTop: 8,
          paddingBottom: Platform.OS === "ios" ? 34 : 18,
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
          tabBarIcon: ({ color, size }) => (
            <PhIcon name="home" size={26} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="orders"
        options={{
          title: "Orders",
          tabBarIcon: ({ color, size }) => (
            <PhIcon name="list" size={26} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="add"
        options={{
          title: "Ship",
          // When the team member lacks shipments_create we strip the
          // tab from the bar entirely (href:null) so they can't even
          // see the Ship button. Owners get the normal centred FAB.
          href: canAdd ? "/add" : null,
          tabBarIcon: ({ color }) => (
            <View
              style={{
                backgroundColor: colors.primary,
                width: 52,
                height: 52,
                borderRadius: 26,
                justifyContent: "center",
                alignItems: "center",
                marginBottom: 4,
                boxShadow: `0px 4px 8px ${colors.primary}59`,
                elevation: 6,
              }}
            >
              <PhIcon name="add" size={32} color="#fff" />
            </View>
          ),
          tabBarLabel: () => null,
        }}
      />
      <Tabs.Screen
        name="shipments"
        options={{
          title: "Shipments",
          tabBarIcon: ({ color, size }) => (
            <PhIcon name="cube" size={26} color={color} />
          ),
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{
          title: "Settings",
          tabBarIcon: ({ color, size }) => (
            <PhIcon name="settings-sharp" size={26} color={color} />
          ),
        }}
      />
    </Tabs>
    </NewOrderAlertProvider>
  );
}
