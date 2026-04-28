/**
 * App-level error boundary.
 *
 * Catches uncaught render-tree errors so users see a friendly recovery
 * screen instead of a white blank app. Displays the error briefly,
 * lets the user reload, and (when expanded) reveals the stack so the
 * support agent can debug.
 *
 * Wired around the Expo Router root in app/_layout.tsx.
 *
 * Note: error boundaries don't catch:
 *   - errors in event handlers (use try/catch)
 *   - errors in async code (Promises) — those bubble to global handlers
 *   - server-side rendering errors
 *   - errors thrown in the boundary itself
 *
 * They DO catch render errors, lifecycle errors, and constructor
 * errors of any descendant component — which is the common case for
 * a "white screen" crash on mobile.
 */
import React from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, Platform, Alert,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

// Note: expo-updates is intentionally NOT required here.
// Metro statically resolves require() calls even inside try/catch, which
// breaks the bundle when the module isn't installed (dev / Expo Go).
// On web we hard-reload via window.location; on native we reset state and
// let the user re-navigate.

type Props = { children: React.ReactNode };
type State = {
  hasError: boolean;
  error?: Error;
  errorInfo?: { componentStack?: string };
  showDetails: boolean;
};

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = {
    hasError: false,
    showDetails: false,
  };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: { componentStack?: string }) {
    // Log to console — appears in dev and in Expo Go's logs.
    // In production, hook this up to your crash reporter.
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] Uncaught render error:", error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReload = async () => {
    // Web: hard reload via the browser.
    if (Platform.OS === "web" && typeof window !== "undefined") {
      try {
        window.location.reload();
        return;
      } catch {
        // fall through to state reset
      }
    }
    // Native (Expo Go / dev): we don't ship expo-updates, so the safest
    // path is to reset the boundary state and let the user re-navigate.
    // In a production EAS build, swap this for Updates.reloadAsync().
    this.setState({
      hasError: false,
      error: undefined,
      errorInfo: undefined,
      showDetails: false,
    });
  };

  copyError = () => {
    const { error, errorInfo } = this.state;
    const txt = `${error?.name || "Error"}: ${error?.message || ""}\n\n` +
      `Stack:\n${error?.stack || "(no stack)"}\n\n` +
      `Component stack:\n${errorInfo?.componentStack || "(none)"}`;
    if (Platform.OS === "web" && typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(txt).catch(() => {});
      Alert.alert("Copied", "Error details copied to clipboard.");
    } else {
      // Native fallback — show the text in the alert for screen-cap.
      Alert.alert("Error details", txt.slice(0, 1500));
    }
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const err = this.state.error;
    const message = err?.message || "Something went wrong.";
    const name = err?.name || "Error";

    return (
      <View style={styles.wrap}>
        <ScrollView contentContainerStyle={styles.scroll}>
          <View style={styles.iconBox}>
            <Ionicons name="alert-circle" size={56} color="#DC2626" />
          </View>
          <Text style={styles.title}>The app hit an unexpected error</Text>
          <Text style={styles.subtitle}>
            Don't worry — your data is safe on the server. Tap{" "}
            <Text style={{ fontWeight: "900" }}>Reload app</Text> to recover.
          </Text>

          <View style={styles.errBox}>
            <Text style={styles.errLabel}>{name}</Text>
            <Text style={styles.errMsg}>{message}</Text>
          </View>

          <TouchableOpacity
            style={styles.primaryBtn}
            onPress={this.handleReload}
            activeOpacity={0.85}
          >
            <Ionicons name="refresh" size={18} color="#fff" />
            <Text style={styles.primaryTxt}>Reload app</Text>
          </TouchableOpacity>

          <TouchableOpacity
            style={styles.secondaryBtn}
            onPress={() => this.setState((s) => ({ showDetails: !s.showDetails }))}
            activeOpacity={0.7}
          >
            <Text style={styles.secondaryTxt}>
              {this.state.showDetails ? "Hide details" : "Show technical details"}
            </Text>
            <Ionicons
              name={this.state.showDetails ? "chevron-up" : "chevron-down"}
              size={14}
              color="#475569"
            />
          </TouchableOpacity>

          {this.state.showDetails ? (
            <View style={styles.detailsBox}>
              <Text style={styles.detailsLabel}>Stack</Text>
              <Text style={styles.detailsTxt} selectable>
                {err?.stack || "(no stack)"}
              </Text>
              <Text style={[styles.detailsLabel, { marginTop: 12 }]}>Component tree</Text>
              <Text style={styles.detailsTxt} selectable>
                {this.state.errorInfo?.componentStack || "(none)"}
              </Text>
              <TouchableOpacity style={styles.copyBtn} onPress={this.copyError}>
                <Ionicons name="copy-outline" size={14} color="#0F172A" />
                <Text style={styles.copyTxt}>Copy error</Text>
              </TouchableOpacity>
            </View>
          ) : null}

          <Text style={styles.foot}>
            If this keeps happening, please send a screenshot of the
            error to support@your-brand.app.
          </Text>
        </ScrollView>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  wrap: { flex: 1, backgroundColor: "#F8FAFC" },
  scroll: { padding: 24, paddingTop: 80, alignItems: "center" },
  iconBox: {
    width: 96, height: 96, borderRadius: 48,
    backgroundColor: "#FEE2E2",
    alignItems: "center", justifyContent: "center",
    marginBottom: 16,
  },
  title: {
    fontSize: 20, fontWeight: "900", color: "#0F172A",
    textAlign: "center", marginBottom: 6,
  },
  subtitle: {
    fontSize: 13.5, color: "#475569",
    textAlign: "center", lineHeight: 19,
    paddingHorizontal: 12, marginBottom: 22,
  },
  errBox: {
    backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#FCA5A5",
    borderLeftWidth: 4, borderLeftColor: "#DC2626",
    borderRadius: 8,
    padding: 12, marginBottom: 18,
    width: "100%",
  },
  errLabel: { fontSize: 11, fontWeight: "900", color: "#991B1B", letterSpacing: 0.5 },
  errMsg:   { fontSize: 13, color: "#1F2937", marginTop: 4, lineHeight: 18 },
  primaryBtn: {
    backgroundColor: "#0F172A",
    flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 8, paddingVertical: 14, paddingHorizontal: 24,
    borderRadius: 10, width: "100%",
  },
  primaryTxt: { color: "#fff", fontSize: 14, fontWeight: "800", letterSpacing: 0.4 },
  secondaryBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingVertical: 14,
  },
  secondaryTxt: { color: "#475569", fontSize: 12.5, fontWeight: "700" },
  detailsBox: {
    width: "100%",
    backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 8, padding: 12,
  },
  detailsLabel: {
    fontSize: 10.5, fontWeight: "900", color: "#475569",
    letterSpacing: 0.6, textTransform: "uppercase",
    marginBottom: 4,
  },
  detailsTxt: {
    fontSize: 11, color: "#1F2937", lineHeight: 16,
    fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace",
  },
  copyBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: "#F1F5F9",
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 6, alignSelf: "flex-start",
    marginTop: 10,
  },
  copyTxt: { fontSize: 12, color: "#0F172A", fontWeight: "800" },
  foot: {
    fontSize: 11.5, color: "#94A3B8",
    textAlign: "center", marginTop: 24, paddingHorizontal: 16,
  },
});
