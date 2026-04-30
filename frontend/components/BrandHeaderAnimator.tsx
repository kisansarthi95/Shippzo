/**
 * BrandHeaderAnimator
 * -------------------
 * Rotating brand-recognition block that cycles between two "phases"
 * every ~3 seconds on the Home dashboard header:
 *
 *   Phase A — the friendly greeting block
 *     • "COURIER LABEL MANAGER" kicker
 *     • "Hello 👋"  (the hand emoji does a continuous wave)
 *     • "Ship smart. Print fast." tagline (subtle slide-up entry)
 *
 *   Phase B — the Shippzo brand block
 *     • Mini Shippzo logo (letter-group scale pulse)
 *     • "Run your shipping on autopilot." tagline
 *
 * Goal: keep users subconsciously aware of the Shippzo brand so they
 * can tell friends "this is the app called Shippzo" without confusion.
 *
 * Layout:
 *   • Absolute sizing — matches the old "Hello 👋" area height exactly
 *     so the right-side action buttons and the content below never
 *     shift when a phase swap happens.
 *   • Two children are stacked (absolute fill) inside a relative box;
 *     the inactive child fades out while the active child fades in.
 */
import React, { useEffect } from "react";
import {
  View,
  Text,
  Image,
  StyleSheet,
  Platform,
} from "react-native";
import Animated, {
  useSharedValue,
  useAnimatedStyle,
  withRepeat,
  withTiming,
  withSequence,
  withDelay,
  interpolate,
  Easing,
  cancelAnimation,
  runOnJS,
} from "react-native-reanimated";
import { colors } from "../lib/theme";

const PHASE_MS = 3000;         // each phase shows for 3s
const FADE_MS = 420;           // crossfade duration

// Fixed block size so parent layout never shifts. Derived from the
// legacy 3-line text column (kicker 14 + title 34 + tagline 18 ≈ 66px),
// with a small buffer for the logo phase.
const BLOCK_HEIGHT = 72;
// Width clamps at screen_width - right_action_buttons. On most phones
// this is ~170–200px. Using flex + minWidth avoids needing a fixed
// pixel width here.
const BLOCK_MIN_WIDTH = 170;

export default function BrandHeaderAnimator() {
  // 0 = Phase A (Hello), 1 = Phase B (Shippzo). The opacity of the
  // two absolute children is driven straight off this value so the
  // crossfade feels continuous.
  const phase = useSharedValue(0);

  // Hand-wave rotation — runs only during Phase A.
  const handAngle = useSharedValue(0);
  // Letters pulse scale — runs only during Phase B.
  const logoScale = useSharedValue(1);
  // Tagline slide — shared between phases (resets on each swap).
  const taglineY = useSharedValue(0);

  useEffect(() => {
    // Continuous waving hand — tiny angle sweep, loops forever.
    handAngle.value = withRepeat(
      withSequence(
        withTiming(14, { duration: 180, easing: Easing.out(Easing.quad) }),
        withTiming(-12, { duration: 240, easing: Easing.inOut(Easing.quad) }),
        withTiming(10, { duration: 200 }),
        withTiming(0, { duration: 220 }),
        withTiming(0, { duration: 280 }),  // tiny pause between waves
      ),
      -1,
      false,
    );

    // Slow heartbeat scale on the Shippzo logo (only noticeable when
    // Phase B is visible — Phase B fades in over the top).
    logoScale.value = withRepeat(
      withSequence(
        withTiming(1.05, { duration: 700, easing: Easing.inOut(Easing.quad) }),
        withTiming(0.97, { duration: 700, easing: Easing.inOut(Easing.quad) }),
        withTiming(1.00, { duration: 600 }),
      ),
      -1,
      false,
    );

    // Phase cycle — toggles every PHASE_MS.
    let isA = true;
    const tick = () => {
      // Reset + replay tagline slide whenever a phase swaps in.
      taglineY.value = 14;
      taglineY.value = withDelay(
        120,
        withTiming(0, { duration: 520, easing: Easing.out(Easing.cubic) }),
      );
      phase.value = withTiming(isA ? 0 : 1, {
        duration: FADE_MS,
        easing: Easing.inOut(Easing.quad),
      });
      isA = !isA;
    };
    // Kick the first tagline animation.
    taglineY.value = 14;
    taglineY.value = withDelay(
      160,
      withTiming(0, { duration: 520, easing: Easing.out(Easing.cubic) }),
    );
    const interval: ReturnType<typeof setInterval> = setInterval(tick, PHASE_MS);

    return () => {
      clearInterval(interval);
      cancelAnimation(phase);
      cancelAnimation(handAngle);
      cancelAnimation(logoScale);
      cancelAnimation(taglineY);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const phaseAStyle = useAnimatedStyle(() => ({
    opacity: interpolate(phase.value, [0, 1], [1, 0]),
  }));
  const phaseBStyle = useAnimatedStyle(() => ({
    opacity: interpolate(phase.value, [0, 1], [0, 1]),
  }));

  const handStyle = useAnimatedStyle(() => ({
    transform: [{ rotate: `${handAngle.value}deg` }],
  }));

  const logoStyle = useAnimatedStyle(() => ({
    transform: [{ scale: logoScale.value }],
  }));

  const taglineStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: taglineY.value }],
    opacity: interpolate(taglineY.value, [14, 0], [0, 1]),
  }));

  return (
    <View style={styles.block}>
      {/* ── Phase A ─────────────────────────────────────── */}
      <Animated.View style={[styles.phaseLayer, phaseAStyle]}>
        <Text style={styles.kicker}>COURIER LABEL MANAGER</Text>
        <View style={styles.titleRow}>
          <Text style={styles.title}>Hello </Text>
          <Animated.Text style={[styles.wave, handStyle]}>👋</Animated.Text>
        </View>
        <Animated.Text style={[styles.sub, taglineStyle]}>
          Ship smart. Print fast.
        </Animated.Text>
      </Animated.View>

      {/* ── Phase B ─────────────────────────────────────── */}
      <Animated.View style={[styles.phaseLayer, phaseBStyle]}>
        <Text style={styles.kickerBrand}>POWERED BY</Text>
        <Animated.Image
          source={require("../assets/brand/shippzo_logo.png")}
          style={[styles.logo, logoStyle]}
          resizeMode="contain"
        />
        <Animated.Text style={[styles.subBrand, taglineStyle]}>
          Run your shipping on{" "}
          <Text style={styles.subBrandAccent}>autopilot.</Text>
        </Animated.Text>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  block: {
    minWidth: BLOCK_MIN_WIDTH,
    height: BLOCK_HEIGHT,
    justifyContent: "flex-start",
    position: "relative",
  },
  phaseLayer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: "flex-start",
  },
  kicker: {
    fontSize: 10,
    fontWeight: "700",
    color: colors.primary,
    letterSpacing: 1.5,
  },
  kickerBrand: {
    fontSize: 10,
    fontWeight: "800",
    color: "#9CA3AF",
    letterSpacing: 2,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 2,
  },
  title: {
    fontSize: 28,
    fontWeight: "800",
    color: colors.text,
  },
  wave: {
    fontSize: 26,
    // Apparent baseline correction for the emoji glyph on Android.
    marginTop: Platform.OS === "android" ? -2 : 0,
    // Animations rotate around the middle-left bottom of the hand —
    // React Native defaults the origin to the view's center which is
    // already correct for our use-case.
  },
  sub: {
    color: colors.textMuted,
    marginTop: 2,
    fontSize: 13,
  },
  logo: {
    marginTop: 4,
    width: 140,
    height: 24,
    // Sharper rendering in both directions.
    ...(Platform.OS === "web"
      ? { imageRendering: "high-quality" as any }
      : {}),
  },
  subBrand: {
    marginTop: 6,
    color: colors.textMuted,
    fontSize: 12,
    fontWeight: "600",
    fontStyle: "italic",
  },
  subBrandAccent: {
    color: "#FF5A00",
    fontWeight: "800",
    fontStyle: "normal",
  },
});
