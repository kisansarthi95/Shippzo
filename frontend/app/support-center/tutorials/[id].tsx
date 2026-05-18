/**
 * Phase-21 — Embedded YouTube player.
 *
 * Plays the tutorial INSIDE the app via a react-native-webview that
 * loads the YouTube iframe player URL with `rel=0&modestbranding=1`
 * so related videos and YouTube branding stay suppressed. We also
 * intercept navigation requests so a stray tap on a recommendation
 * card never bounces the operator out to the YouTube app/browser.
 */
import React, { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Stack, useLocalSearchParams } from "expo-router";
import { WebView } from "react-native-webview";
import { Api, VideoTutorial } from "../../../lib/api";
import { colors } from "../../../lib/theme";

export default function TutorialPlayer() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [t, setT] = useState<VideoTutorial | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      try { setT(await Api.getVideoTutorial(String(id))); }
      catch (e: any) { setErr(e?.response?.data?.detail || "Could not load tutorial"); }
    })();
  }, [id]);

  if (err) {
    return (
      <View style={[styles.root, styles.center]}>
        <Stack.Screen options={{ title: "Tutorial" }} />
        <Text style={styles.err}>{err}</Text>
      </View>
    );
  }
  if (!t) {
    return (
      <View style={[styles.root, styles.center]}>
        <Stack.Screen options={{ title: "Tutorial" }} />
        <ActivityIndicator color={colors.primary} />
      </View>
    );
  }

  // Phase-21 / Phase-34 — Embed URL hardening.
  //   Switched to `youtube-nocookie.com` (privacy-enhanced) since it
  //   has a more permissive origin policy for in-app WebView embeds.
  //   `enablejsapi=1` lets the IFrame API answer origin pings.
  //   `origin=` MUST match the WebView's reported origin (the
  //   `baseUrl` we set on source below) — otherwise YouTube returns
  //   Error 153 "Video player configuration error".
  // Final URL shape:
  //   https://www.youtube-nocookie.com/embed/<id>
  //     ?rel=0&modestbranding=1&playsinline=1&controls=1
  //     &showinfo=0&iv_load_policy=3&fs=1
  //     &enablejsapi=1&origin=https%3A%2F%2Fshippzo.app
  const EMBED_ORIGIN = "https://shippzo.app";
  const embedUrl =
    `https://www.youtube-nocookie.com/embed/${t.youtube_video_id}` +
    `?rel=0&modestbranding=1&playsinline=1&controls=1&showinfo=0` +
    `&iv_load_policy=3&fs=1&enablejsapi=1` +
    `&origin=${encodeURIComponent(EMBED_ORIGIN)}`;

  // HTML wrapper keeps the WebView background black while the player
  // loads and locks the iframe to fill the screen. We also block
  // window.open so a recommendation tap (when one slips through)
  // can't escape to the system browser.
  const html = `
    <!DOCTYPE html>
    <html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no" />
      <style>
        html, body { margin:0; padding:0; background:#000; height:100%; }
        .wrap { position:absolute; inset:0; }
        iframe { width:100%; height:100%; border:0; display:block; }
      </style>
    </head><body>
      <div class="wrap">
        <iframe src="${embedUrl}"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowfullscreen
                referrerpolicy="origin"></iframe>
      </div>
      <script>
        // Belt-and-braces: block any popups so a stray "watch on YouTube"
        // button can't push us out of the in-app player.
        window.open = function(){ return null; };
      </script>
    </body></html>`;

  return (
    <View style={styles.root}>
      <Stack.Screen
        options={{
          title: t.title,
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <View style={styles.playerWrap}>
        <WebView
          // Phase-34 — Pass `baseUrl` so the WebView reports a real
          // HTTPS origin to YouTube instead of `about:blank` /
          // `null`. Combined with the `origin=` query param above
          // this fixes the recurring Error 153 "Video player
          // configuration error" that some uploaders' embed policies
          // would otherwise trigger inside Android WebView.
          source={{ html, baseUrl: EMBED_ORIGIN }}
          originWhitelist={["*"]}
          style={{ backgroundColor: "#000", flex: 1 }}
          javaScriptEnabled
          domStorageEnabled
          mediaPlaybackRequiresUserAction={false}
          allowsInlineMediaPlayback
          // Phase-21 — Block any navigation other than the initial
          // iframe URL so a tap on a YouTube recommendation can't
          // take the operator out of the app.
          onShouldStartLoadWithRequest={(req) => {
            const u = req.url || "";
            return (
              u === "about:blank" ||
              u.startsWith("data:") ||
              u.startsWith(EMBED_ORIGIN) ||
              u.startsWith("https://www.youtube.com/embed/") ||
              u.startsWith("https://www.youtube-nocookie.com/") ||
              u.startsWith("https://i.ytimg.com/")
            );
          }}
          androidLayerType={Platform.OS === "android" ? "hardware" : undefined}
        />
      </View>
      <View style={styles.meta}>
        <Text style={styles.metaTitle}>{t.title}</Text>
        {t.short_description ? (
          <Text style={styles.metaSub}>{t.short_description}</Text>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },
  center: { alignItems: "center", justifyContent: "center" },
  err: { color: "#DC2626", fontSize: 14, padding: 20, textAlign: "center" },
  playerWrap: { aspectRatio: 16 / 9, backgroundColor: "#000" },
  meta: { padding: 16 },
  metaTitle: { fontSize: 16, fontWeight: "800", color: "#0F172A" },
  metaSub: { fontSize: 13, color: "#475569", marginTop: 6, lineHeight: 18 },
});
