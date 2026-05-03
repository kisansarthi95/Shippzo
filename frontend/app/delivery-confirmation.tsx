/**
 * Phase F4 — Legacy /delivery-confirmation redirect stub.
 *
 * This screen has been replaced by the generic Bulk Message screen
 * (`/bulk-message/delivery_confirmation`). We keep the route as a
 * one-frame redirect so old links / bookmarks / push notifications
 * keep working. The full courier-rules editor that used to live
 * here moved to `/courier-rules`.
 */
import React, { useEffect } from "react";
import { View, ActivityIndicator } from "react-native";
import { router, Stack } from "expo-router";

export default function DeliveryConfirmationLegacyRedirect() {
  useEffect(() => {
    router.replace("/bulk-message/delivery_confirmation" as any);
  }, []);
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F7F7F9" }}>
      <Stack.Screen options={{ headerShown: false }} />
      <ActivityIndicator color="#6B5BFF" />
    </View>
  );
}
