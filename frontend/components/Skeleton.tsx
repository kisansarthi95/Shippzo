/**
 * Skeleton — animated placeholder primitives for per-section loading
 * states. Replaces full-screen ActivityIndicator overlays so the user
 * sees the layout immediately while data fetches in the background.
 *
 * Usage:
 *
 *   {loading && !data ? (
 *     <SkeletonList rows={6} />
 *   ) : (
 *     <RealList data={data} />
 *   )}
 *
 * Behaviour:
 * - Uses RN-driven `Animated` opacity loop on the native thread
 *   (`useNativeDriver: true`) so it stays smooth even while the JS
 *   thread is busy parsing the freshly-fetched payload.
 * - Pre-sized blocks prevent layout shift when real content lands.
 * - One animation node shared by all skeletons in the tree — cheap.
 */
import React, { useEffect, useRef } from "react";
import { Animated, View, StyleSheet, ViewStyle } from "react-native";

const SKELETON_BG = "#E5E7EB";

/** Single rectangular pulsing block. */
export function SkeletonBlock({
  width,
  height = 14,
  radius = 6,
  style,
}: {
  width?: number | string;
  height?: number;
  radius?: number;
  style?: ViewStyle;
}) {
  const pulse = useRef(new Animated.Value(0.4)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: 700,
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0.4,
          duration: 700,
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  return (
    <Animated.View
      style={[
        {
          width: (width as any) ?? "100%",
          height,
          borderRadius: radius,
          backgroundColor: SKELETON_BG,
          opacity: pulse,
        },
        style,
      ]}
    />
  );
}

/** Single card-shaped placeholder mirroring a typical list row. */
export function SkeletonCard({ height = 84 }: { height?: number }) {
  return (
    <View style={[styles.card, { height }]}>
      <SkeletonBlock width={42} height={42} radius={21} />
      <View style={{ flex: 1, marginLeft: 12, gap: 8 }}>
        <SkeletonBlock width="60%" height={14} />
        <SkeletonBlock width="40%" height={12} />
        <SkeletonBlock width="80%" height={12} />
      </View>
    </View>
  );
}

/** Stack of N card placeholders (list view skeleton). */
export function SkeletonList({
  rows = 5,
  height,
}: {
  rows?: number;
  height?: number;
}) {
  return (
    <View style={{ paddingHorizontal: 12, paddingTop: 12 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ marginBottom: 10 }}>
          <SkeletonCard height={height} />
        </View>
      ))}
    </View>
  );
}

/** A horizontal stats-strip placeholder (3 boxes). */
export function SkeletonStatsStrip({ boxes = 3 }: { boxes?: number }) {
  return (
    <View style={styles.statsRow}>
      {Array.from({ length: boxes }).map((_, i) => (
        <View key={i} style={styles.statBox}>
          <SkeletonBlock width="60%" height={18} />
          <SkeletonBlock width="80%" height={10} style={{ marginTop: 8 }} />
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    borderWidth: 1,
    borderColor: "#F1F5F9",
  },
  statsRow: {
    flexDirection: "row",
    backgroundColor: "#fff",
    borderRadius: 12,
    padding: 14,
    marginHorizontal: 12,
    marginTop: 12,
    borderWidth: 1,
    borderColor: "#F1F5F9",
  },
  statBox: {
    flex: 1,
    alignItems: "center",
    paddingHorizontal: 8,
  },
});
