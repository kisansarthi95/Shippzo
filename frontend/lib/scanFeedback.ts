import { Platform } from "react-native";
import * as Haptics from "expo-haptics";
import { createAudioPlayer, setAudioModeAsync, AudioPlayer } from "expo-audio";

// A short scanner-style "beep" sound (1200Hz → 1600Hz, ~120ms). Bundled locally.
// Keep a single long-lived player instance — cheaper than re-creating each scan.
let beepPlayer: AudioPlayer | null = null;
let modeConfigured = false;

/**
 * Initialize the audio player once (idempotent). Safe to call on every mount.
 * Returns quickly if already initialised. Silent no-op on web (we rely on
 * Haptics fallback there, which itself is a no-op on web).
 */
export async function initScanFeedback(): Promise<void> {
  if (Platform.OS === "web") return;
  try {
    if (!modeConfigured) {
      // Play even if device is on silent (iOS-like UX for scanner apps).
      await setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
        shouldPlayInBackground: false,
      });
      modeConfigured = true;
    }
    if (!beepPlayer) {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const src = require("../assets/sounds/beep.wav");
      beepPlayer = createAudioPlayer(src);
      beepPlayer.volume = 1.0;
    }
  } catch {
    // Silent — we still give haptic feedback below.
  }
}

/**
 * Play a short success beep + a short impact haptic.
 * Never throws — best-effort UX feedback only.
 */
export async function playScanSuccess(): Promise<void> {
  // Haptic first (instant, works offline / on mute)
  try {
    if (Platform.OS !== "web") {
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
  } catch {
    /* ignore */
  }
  // Audio beep
  try {
    if (!beepPlayer) await initScanFeedback();
    if (beepPlayer) {
      // Rewind to start so repeated scans always beep.
      await beepPlayer.seekTo(0);
      beepPlayer.play();
    }
  } catch {
    /* ignore */
  }
}

/**
 * Play an error pattern: heavy impact haptic (no sound).
 * Used when a scan is rejected (e.g. invalid / duplicate tracking id).
 */
export async function playScanError(): Promise<void> {
  try {
    if (Platform.OS !== "web") {
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }
  } catch {
    /* ignore */
  }
}

/**
 * Light tick haptic — used when camera focuses or detects a barcode edge,
 * but before the value is confirmed. Subtle "something happened" signal.
 */
export async function playScanTick(): Promise<void> {
  try {
    if (Platform.OS !== "web") {
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
    }
  } catch {
    /* ignore */
  }
}

/**
 * Release audio resources. Call when leaving scanner screen to free memory.
 */
export function disposeScanFeedback(): void {
  try {
    beepPlayer?.remove?.();
  } catch {
    /* ignore */
  }
  beepPlayer = null;
}
