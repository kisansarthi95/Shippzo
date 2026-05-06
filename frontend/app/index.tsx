import { Redirect } from "expo-router";

/**
 * Root-route landing page.
 *
 * expo-router requires an `index.tsx` at /app when the user hits "/".
 * Without it, Metro logs repeated "Route not found" bundling errors on
 * every cold launch. We simply forward to the primary app surface —
 * the (tabs) group — which itself respects AuthGate (see _layout.tsx)
 * and will redirect unauthenticated users to (auth)/login.
 */
export default function Index() {
  return <Redirect href="/(tabs)" />;
}
