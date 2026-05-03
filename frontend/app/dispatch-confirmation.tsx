/**
 * Phase F4 — Legacy /dispatch-confirmation redirect stub.
 *
 * Replaced by the generic Bulk Message screen
 * (`/bulk-message/dispatch_confirmation`). One-frame redirect kept
 * so old links / bookmarks keep working.
 */
import React, { useEffect } from "react";
import { View, ActivityIndicator } from "react-native";
import { router, Stack } from "expo-router";

export default function DispatchConfirmationLegacyRedirect() {
  useEffect(() => {
    router.replace("/bulk-message/dispatch_confirmation" as any);
  }, []);
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ headerShown: false }} />
      <ActivityIndicator color="#6B5BFF" />
    </View>
  );
}
