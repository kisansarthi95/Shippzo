import { Stack } from "expo-router";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StatusBar } from "expo-status-bar";

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: "#F4F5F7" },
        }}
      >
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="scanner" options={{ presentation: "modal", headerShown: false }} />
        <Stack.Screen name="label/[id]" options={{ headerShown: false }} />
        <Stack.Screen name="courier/[id]" options={{ headerShown: false }} />
      </Stack>
    </SafeAreaProvider>
  );
}
