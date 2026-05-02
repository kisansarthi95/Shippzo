/**
 * SmartCycleButton — animated 3-state header button.
 *
 * Replaces the static purple sparkles icon in the dashboard header.
 * Auto-cycles every 2 seconds through three "Smart Paste" sub-flows so
 * a new user can immediately see what the button does without tapping:
 *
 *   State 1  ✦ Smart Fill   (purple)   — entry hint
 *   State 2  📋 Paste Text  (blue)      — clipboard branch
 *   State 3  📷 Scan Photo  (orange)    — vision branch
 *
 * The label / icon / colour all crossfade smoothly. A row of 3 dots at
 * the bottom of the pill indicates the current state. Tapping the
 * button opens the existing Smart Paste flow (handled by the parent's
 * onPress prop) — the cycling animation pauses for 1s after a press
 * so the user has time to see what they tapped.
 *
 * Per product requirement: NO occurrence of the word "AI" anywhere on
 * the button. The intent is to feel like a fast manual tool, not a
 * generative AI feature.
 */
import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  View, Text, StyleSheet, Pressable, Animated, Easing,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

type StateDef = {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  bg: string;
  border: string;
};

// Three visual states the button cycles through. Order matters — it
// drives the dot indicator and the timed transition.
const STATES: readonly StateDef[] = [
  { icon: "sparkles",          label: "Smart Fill", bg: "#7C3AED", border: "#A78BFA" },
  { icon: "clipboard-outline", label: "Paste Text", bg: "#2563EB", border: "#60A5FA" },
  { icon: "camera-outline",    label: "Scan Photo", bg: "#EA580C", border: "#FB923C" },
] as const;

const CYCLE_MS = 2000;          // 2-second per state
const TRANSITION_MS = 320;      // crossfade duration
const POST_PRESS_PAUSE_MS = 1500; // pause cycling briefly after a tap

type Props = {
  onPress: () => void;
  busy?: boolean;
  testID?: string;
};

export default function SmartCycleButton({ onPress, busy, testID }: Props) {
  const [stateIdx, setStateIdx] = useState(0);
  const fade = useRef(new Animated.Value(1)).current;
  const pressedAt = useRef<number>(0);

  // Animated background colour driven by stateIdx — RN's Animated.Value
  // can't interpolate background colours directly without input range,
  // so we rebuild a "color" Animated.Value each cycle via a number 0..N
  // and interpolate to RGB strings.
  const colorAnim = useRef(new Animated.Value(0)).current;

  const goToState = useCallback((next: number) => {
    // Fade out → swap → fade in. Colour interpolation runs in parallel
    // for a smooth blend instead of a hard cut.
    Animated.parallel([
      Animated.timing(fade, {
        toValue: 0,
        duration: TRANSITION_MS / 2,
        easing: Easing.out(Easing.cubic),
        useNativeDriver: true,
      }),
      Animated.timing(colorAnim, {
        toValue: next,
        duration: TRANSITION_MS,
        easing: Easing.inOut(Easing.cubic),
        useNativeDriver: false, // colour interpolation requires JS driver
      }),
    ]).start(() => {
      setStateIdx(next);
      Animated.timing(fade, {
        toValue: 1,
        duration: TRANSITION_MS / 2,
        easing: Easing.in(Easing.cubic),
        useNativeDriver: true,
      }).start();
    });
  }, [fade, colorAnim]);

  // Auto-cycle every 2s. Skip cycle when:
  //   - the button is "busy" (parent is processing a tap)
  //   - the user just tapped (give them ~1.5s before resuming)
  useEffect(() => {
    if (busy) return;
    const id = setInterval(() => {
      const sincePress = Date.now() - pressedAt.current;
      if (sincePress < POST_PRESS_PAUSE_MS) return;
      const next = (stateIdx + 1) % STATES.length;
      goToState(next);
    }, CYCLE_MS);
    return () => clearInterval(id);
  }, [busy, stateIdx, goToState]);

  const handlePress = () => {
    pressedAt.current = Date.now();
    onPress();
  };

  // Background colour interpolation between the 3 brand tones.
  const bgColor = useMemo(
    () => colorAnim.interpolate({
      inputRange: [0, 1, 2],
      outputRange: [STATES[0].bg, STATES[1].bg, STATES[2].bg],
    }),
    [colorAnim],
  );
  const borderColor = useMemo(
    () => colorAnim.interpolate({
      inputRange: [0, 1, 2],
      outputRange: [STATES[0].border, STATES[1].border, STATES[2].border],
    }),
    [colorAnim],
  );

  const current = STATES[stateIdx];

  return (
    <Pressable
      testID={testID}
      onPress={handlePress}
      disabled={busy}
      style={({ pressed }) => [styles.wrap, pressed && styles.pressed]}
    >
      <Animated.View
        style={[
          styles.pill,
          {
            backgroundColor: bgColor,
            borderColor: borderColor,
            opacity: busy ? 0.65 : 1,
          },
        ]}
      >
        <Animated.View style={[styles.row, { opacity: fade }]}>
          <Ionicons name={current.icon} size={16} color="#fff" />
          <Text
            style={styles.label}
            numberOfLines={1}
            allowFontScaling={false}
          >
            {current.label}
          </Text>
        </Animated.View>

        {/* 3-dot state indicator at bottom of pill. Active dot is
            opaque + slightly bigger; inactive ones are muted. */}
        <View style={styles.dotsRow}>
          {STATES.map((_, i) => {
            const active = i === stateIdx;
            return (
              <View
                key={i}
                style={[
                  styles.dot,
                  active && styles.dotActive,
                ]}
              />
            );
          })}
        </View>
      </Animated.View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: {
    // Per product direction: the button MUST match the sibling
    // refresh/scanner icon buttons exactly (44×44 square). Icon sits
    // on top, tiny label below, with a 3-dot state indicator at the
    // very bottom — all inside the square, no horizontal overflow.
    width: 44,
    height: 44,
  },
  pressed: { transform: [{ scale: 0.94 }] },
  pill: {
    flex: 1,
    borderRadius: 10,
    borderWidth: 1,
    // Tight padding so the 3 stacked rows (icon, label, dots) still
    // fit inside a 44-px square without clipping on any Android /
    // iOS system font size.
    paddingHorizontal: 2,
    paddingTop: 4,
    paddingBottom: 3,
    alignItems: "center",
    justifyContent: "space-between",
  },
  row: {
    alignItems: "center",
    justifyContent: "center",
  },
  label: {
    // 8.5px keeps the longest label ("Scan Photo") on a single line
    // inside the 44-wide square. Very dense but legible at 3× dpi.
    fontSize: 8.5,
    fontWeight: "900",
    color: "#fff",
    letterSpacing: 0.1,
    marginTop: 1,
    textAlign: "center",
  },
  dotsRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 2.5,
  },
  dot: {
    width: 3,
    height: 3,
    borderRadius: 1.5,
    backgroundColor: "rgba(255,255,255,0.45)",
  },
  dotActive: {
    width: 7,
    height: 3,
    borderRadius: 1.5,
    backgroundColor: "#fff",
  },
});
