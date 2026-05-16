/**
 * Phase-21 — Admin: Add Video Tutorial.
 *
 * Mirrors the approved design 1:1. Renders only for admins; non-admin
 * navigation here gets a warning card with a back nudge. On submit
 * the backend extracts the YouTube ID and derives the thumbnail URL
 * automatically, so the admin doesn't need to fiddle with manual
 * thumbnail uploads.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Image,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";
import { Stack, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import PhIcon from "../../../../components/PhIcon";
import { colors } from "../../../../lib/theme";
import { Api, VideoTutorialCategory } from "../../../../lib/api";
import { useAuth } from "../../../../lib/auth";

const YT_RX = /(?:v=|\/embed\/|\/shorts\/|youtu\.be\/)([A-Za-z0-9_-]{6,15})/;
function extractYouTubeId(s: string): string {
  const m = (s || "").match(YT_RX);
  return m ? m[1] : "";
}

export default function AddVideoTutorial() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user } = useAuth();
  const isAdmin = !!(user as any)?.is_admin;

  const [cats, setCats]                 = useState<VideoTutorialCategory[]>([]);
  const [youtubeUrl, setYoutubeUrl]     = useState("");
  const [title, setTitle]               = useState("");
  const [desc, setDesc]                 = useState("");
  const [category, setCategory]         = useState("");
  const [duration, setDuration]         = useState("");
  const [saving, setSaving]             = useState(false);

  useEffect(() => {
    Api.listVideoTutorialCategories()
      .then((r) => { setCats(r.items || []); if (r.items && r.items.length && !category) setCategory(r.items[0].name); })
      .catch(() => setCats([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ytId = useMemo(() => extractYouTubeId(youtubeUrl), [youtubeUrl]);
  const thumbUrl = ytId ? `https://img.youtube.com/vi/${ytId}/hqdefault.jpg` : "";

  if (!isAdmin) {
    return (
      <View style={[styles.root, { padding: 20 }]}>
        <Stack.Screen options={{ title: "Add Video Tutorial" }} />
        <View style={styles.warn}>
          <PhIcon name="information-circle-outline" size={20} color="#9A3412" />
          <Text style={styles.warnTxt}>This screen is only available to the app owner / admin.</Text>
        </View>
      </View>
    );
  }

  const submit = async () => {
    if (!ytId) { Alert.alert("Invalid YouTube link", "Please paste a valid YouTube URL."); return; }
    if (title.trim().length < 2) { Alert.alert("Title required"); return; }
    if (!category) { Alert.alert("Category required"); return; }
    setSaving(true);
    try {
      await Api.adminCreateVideoTutorial({
        youtube_url:       youtubeUrl.trim(),
        title:             title.trim(),
        short_description: desc.trim(),
        category,
        duration:          duration.trim(),
      });
      Alert.alert("Tutorial added", "The video is now visible in the Video Tutorials list.", [
        { text: "OK", onPress: () => router.back() },
      ]);
    } catch (e: any) {
      Alert.alert("Failed", e?.response?.data?.detail || e?.message || "Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1, backgroundColor: "#F4F5F7" }}
    >
      <Stack.Screen options={{ title: "Add Video Tutorial", headerTitleStyle: { fontWeight: "800" }, headerShadowVisible: false }} />
      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 96 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* Info card */}
        <View style={styles.info}>
          <PhIcon name="information-circle-outline" size={18} color="#9A3412" />
          <Text style={styles.infoTxt}>
            Only admin/owner can add video tutorials. Add a YouTube video link and details to help users learn easily.
          </Text>
        </View>

        <Text style={styles.fieldLbl}>YouTube Video Link</Text>
        <View style={styles.inputWrap}>
          <TextInput
            value={youtubeUrl}
            onChangeText={setYoutubeUrl}
            placeholder="Paste YouTube video link here"
            placeholderTextColor="#9CA3AF"
            style={styles.input}
            autoCapitalize="none"
            keyboardType="url"
          />
        </View>

        <Text style={styles.fieldLbl}>Title</Text>
        <View style={styles.inputWrap}>
          <TextInput
            value={title}
            onChangeText={setTitle}
            placeholder="Enter video title"
            placeholderTextColor="#9CA3AF"
            style={styles.input}
            maxLength={100}
          />
        </View>
        <Text style={styles.charCount}>{title.length} / 100</Text>

        <Text style={styles.fieldLbl}>Short Description</Text>
        <View style={[styles.inputWrap, { minHeight: 90, alignItems: "stretch" }]}>
          <TextInput
            value={desc}
            onChangeText={setDesc}
            placeholder="Enter short description about this video"
            placeholderTextColor="#9CA3AF"
            style={[styles.input, { minHeight: 72, textAlignVertical: "top" }]}
            multiline
            maxLength={200}
          />
        </View>
        <Text style={styles.charCount}>{desc.length} / 200</Text>

        <Text style={styles.fieldLbl}>Category</Text>
        <View style={styles.chips}>
          {cats.map((c) => {
            const active = c.name === category;
            return (
              <TouchableOpacity
                key={c.id}
                onPress={() => setCategory(c.name)}
                style={[styles.catChip, active && { backgroundColor: colors.primary, borderColor: colors.primary }]}
                activeOpacity={0.85}
              >
                <Text style={[styles.catChipTxt, active && { color: "#fff" }]}>{c.name}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        <Text style={styles.fieldLbl}>Duration</Text>
        <View style={styles.inputWrap}>
          <TextInput
            value={duration}
            onChangeText={setDuration}
            placeholder="e.g. 02:45"
            placeholderTextColor="#9CA3AF"
            style={styles.input}
            maxLength={20}
          />
        </View>

        <Text style={styles.fieldLbl}>Thumbnail Preview</Text>
        <View style={styles.thumbPreviewWrap}>
          {thumbUrl ? (
            <Image source={{ uri: thumbUrl }} style={styles.thumbPreview} />
          ) : (
            <View style={[styles.thumbPreview, { alignItems: "center", justifyContent: "center" }]}>
              <PhIcon name="cloud-upload-outline" size={26} color="#94A3B8" />
              <Text style={{ color: "#94A3B8", fontSize: 12, marginTop: 8 }}>
                Paste a link above to preview the thumbnail
              </Text>
            </View>
          )}
        </View>
        <Text style={styles.helper}>Thumbnail will be auto fetched from YouTube link.</Text>
      </ScrollView>

      <View style={[styles.bar, { paddingBottom: insets.bottom + 10 }]}>
        <TouchableOpacity
          style={[styles.submitBtn, saving && { opacity: 0.6 }]}
          activeOpacity={0.85}
          disabled={saving}
          onPress={submit}
        >
          <Text style={styles.submitTxt}>{saving ? "Adding…" : "Add Tutorial"}</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: "#F4F5F7" },

  info: {
    flexDirection: "row", gap: 10, alignItems: "flex-start",
    backgroundColor: "#FFEDD5", borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 12, marginBottom: 6,
  },
  infoTxt: { flex: 1, color: "#9A3412", fontSize: 12.5, lineHeight: 18, fontWeight: "600" },

  warn: {
    flexDirection: "row", gap: 10, alignItems: "center",
    backgroundColor: "#FEF3C7", borderRadius: 12,
    paddingHorizontal: 14, paddingVertical: 14,
  },
  warnTxt: { flex: 1, color: "#9A3412", fontSize: 13.5, fontWeight: "600" },

  fieldLbl: { fontSize: 12.5, fontWeight: "800", color: "#0F172A", marginTop: 14, marginBottom: 6 },
  inputWrap: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 12, paddingVertical: Platform.OS === "ios" ? 12 : 6,
  },
  input: { fontSize: 14, color: "#0F172A", paddingVertical: 6 },
  charCount: { fontSize: 11, color: "#94A3B8", textAlign: "right", marginTop: 4 },

  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  catChip: {
    paddingHorizontal: 14, paddingVertical: 8,
    borderRadius: 999, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  catChipTxt: { fontSize: 12.5, fontWeight: "700", color: "#334155" },

  thumbPreviewWrap: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB", borderStyle: "dashed",
    padding: 8,
  },
  thumbPreview: { width: "100%", aspectRatio: 16 / 9, borderRadius: 8, backgroundColor: "#F1F5F9" },
  helper: { fontSize: 11, color: "#94A3B8", marginTop: 6 },

  bar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    backgroundColor: "#fff", borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 16, paddingTop: 10,
  },
  submitBtn: {
    backgroundColor: colors.primary, borderRadius: 12,
    paddingVertical: 14, alignItems: "center",
  },
  submitTxt: { color: "#fff", fontSize: 15, fontWeight: "800" },
});
