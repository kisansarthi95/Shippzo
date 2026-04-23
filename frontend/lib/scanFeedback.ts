import { Platform, Vibration } from "react-native";
import * as Haptics from "expo-haptics";

// -----------------------------------------------------------------------------
// Audio beep — LAZY + DEFENSIVE require.
//
// expo-audio is a native module.  If it's missing from the runtime (e.g. old
// Expo Go build, Metro bundle issue, version mismatch, web platform) we
// MUST NOT let the import crash the JS bundle — otherwise haptic feedback
// also breaks (single import = whole module fails).
//
// Strategy:
//   1. Try to require expo-audio lazily inside initScanFeedback().
//   2. If it throws at any point, set `audioAvailable = false` and fall back
//      to a 30-40ms Vibration burst so the user still feels a "scan confirmed"
//      signal.
// -----------------------------------------------------------------------------

let audioAvailable = true;
let beepPlayer: any = null;
let modeConfigured = false;
let initPromise: Promise<void> | null = null;

async function initAudioInternal(): Promise<void> {
  if (Platform.OS === "web") {
    audioAvailable = false;
    return;
  }
  try {
    // Dynamic require — never crashes bundle if native module is absent.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const ExpoAudio = require("expo-audio");
    if (!modeConfigured && ExpoAudio.setAudioModeAsync) {
      await ExpoAudio.setAudioModeAsync({
        playsInSilentMode: true,
        allowsRecording: false,
        shouldPlayInBackground: false,
      });
      modeConfigured = true;
    }
    if (!beepPlayer && ExpoAudio.createAudioPlayer) {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const src = require("../assets/sounds/beep.wav");
      beepPlayer = ExpoAudio.createAudioPlayer(src);
      try {
        beepPlayer.volume = 1.0;
      } catch {
        /* some builds treat volume as readonly before load */
      }
    }
  } catch (e) {
    // Native module not available — mark unavailable so we fall back to
    // Vibration + Haptics. Log once for debugging.
    audioAvailable = false;
    // eslint-disable-next-line no-console
    console.warn("[scanFeedback] audio unavailable:", (e as any)?.message || e);
  }
}

/**
 * Initialize the audio player once (idempotent). Safe to call on every mount.
 * Returns the same promise on parallel calls to avoid race conditions.
 */
export function initScanFeedback(): Promise<void> {
  if (!initPromise) {
    initPromise = initAudioInternal();
  }
  return initPromise;
}

/**
 * Play a short success beep + a short impact haptic.
 * Never throws — best-effort UX feedback only.
 */
export async function playScanSuccess(): Promise<void> {
  // 1) Haptic (most important — works even if audio native module missing).
  try {
    if (Platform.OS !== "web") {
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    }
  } catch {
    // Fallback to the core Vibration API — always present in RN.
    try {
      Vibration.vibrate(40);
    } catch {
      /* ignore */
    }
  }

  // 2) Audio beep (best-effort).
  if (!audioAvailable) {
    // No audio → add a second short vibration tick so the double-beat feels
    // distinctly like a "scan accepted" confirmation instead of just haptic.
    try {
      Vibration.vibrate([0, 30, 60, 30]);
    } catch {
      /* ignore */
    }
    return;
  }
  try {
    if (!beepPlayer) await initScanFeedback();
    if (beepPlayer) {
      try {
        await beepPlayer.seekTo?.(0);
      } catch {
        /* first play has nothing to seek */
      }
      beepPlayer.play?.();
    }
  } catch {
    /* ignore */
  }
}

/**
 * Play an error pattern: heavy impact haptic + short double-vibration.
 */
export async function playScanError(): Promise<void> {
  try {
    if (Platform.OS !== "web") {
      await Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    }
  } catch {
    try {
      Vibration.vibrate([0, 80, 80, 80]);
    } catch {
      /* ignore */
    }
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
  initPromise = null;
}
