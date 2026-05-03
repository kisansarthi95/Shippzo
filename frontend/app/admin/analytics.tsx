/**
 * Legacy admin/analytics route — now redirects to the unified
 * /analytics screen (which itself supports admin "Platform Total"
 * scope toggle for is_admin users). Kept so that any existing
 * deep links / push-notification taps don't 404.
 */
import { useEffect } from "react";
import { router } from "expo-router";
import { View, ActivityIndicator } from "react-native";

export default function LegacyAdminAnalyticsRedirect() {
  useEffect(() => {
    // Replace so the back button doesn't bounce here.
    router.replace("/analytics" as any);
  }, []);
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
      <ActivityIndicator color="#6B5BFF" />
    </View>
  );
}
