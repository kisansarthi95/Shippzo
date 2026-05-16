/**
 * Phase-21 — Create Support Ticket screen.
 *
 * Reached from Support Center → Create Request card / Still need help?
 * CTA, OR from the My Requests empty state.
 *
 * Form fields:
 *   • Category (5 chips: General / Billing / Technical / Feature / Other)
 *   • Title (single line, max 140 chars)
 *   • Description (multi-line, max 5000 chars)
 *
 * On submit:
 *   POST /api/support/tickets → on success router.replace() to the
 *   detail screen so the operator lands on the freshly-created thread.
 */
import React, { useState } from "react";
import {
  Alert,
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

import PhIcon from "../../components/PhIcon";
import { colors } from "../../lib/theme";
import { Api } from "../../lib/api";

type Category = {
  k: "general" | "billing" | "technical" | "feature" | "other";
  label: string;
  icon: string;
  color: string;
};

const CATEGORIES: Category[] = [
  { k: "general",   label: "General",    icon: "chatbubbles-outline",       color: "#475569" },
  { k: "billing",   label: "Billing",    icon: "wallet-outline",            color: "#DC2626" },
  { k: "technical", label: "Technical",  icon: "settings-outline",          color: "#2563EB" },
  { k: "feature",   label: "Feature",    icon: "sparkles-outline",          color: "#7C3AED" },
  { k: "other",     label: "Other",      icon: "ellipsis-horizontal",       color: "#64748B" },
];

export default function CreateTicket() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [category, setCategory]   = useState<Category["k"]>("general");
  const [title, setTitle]         = useState("");
  const [description, setDesc]    = useState("");
  const [saving, setSaving]       = useState(false);

  const submit = async () => {
    const t = title.trim();
    const d = description.trim();
    if (t.length < 2)  { Alert.alert("Title required", "Please enter a short title.");      return; }
    if (d.length < 2)  { Alert.alert("Description required", "Please describe your issue."); return; }
    setSaving(true);
    try {
      const ticket = await Api.supportCreateTicket({
        title: t, description: d, category,
      });
      // Replace the create screen with the new ticket detail so back
      // navigation returns to My Requests, not the empty form.
      router.replace(`/support-center/ticket/${ticket.id}` as any);
    } catch (e: any) {
      Alert.alert(
        "Could not create ticket",
        e?.response?.data?.detail || e?.message || "Please try again.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1, backgroundColor: "#F4F5F7" }}
    >
      <Stack.Screen
        options={{
          title: "Create Request",
          headerTitleStyle: { fontWeight: "800" },
          headerShadowVisible: false,
        }}
      />
      <ScrollView
        style={{ flex: 1 }}
        contentContainerStyle={{ padding: 16, paddingBottom: insets.bottom + 96 }}
        keyboardShouldPersistTaps="handled"
      >
        {/* Category picker */}
        <Text style={styles.label}>Category</Text>
        <View style={styles.chips}>
          {CATEGORIES.map((c) => {
            const active = c.k === category;
            return (
              <TouchableOpacity
                key={c.k}
                testID={`cat-${c.k}`}
                onPress={() => setCategory(c.k)}
                style={[
                  styles.chip,
                  active && { backgroundColor: c.color, borderColor: c.color },
                ]}
                activeOpacity={0.85}
              >
                <PhIcon name={c.icon} size={14} color={active ? "#fff" : c.color} />
                <Text style={[
                  styles.chipTxt,
                  active && { color: "#fff" },
                ]}>{c.label}</Text>
              </TouchableOpacity>
            );
          })}
        </View>

        {/* Title */}
        <Text style={[styles.label, { marginTop: 18 }]}>Title</Text>
        <View style={styles.inputWrap}>
          <TextInput
            testID="ticket-title"
            value={title}
            onChangeText={setTitle}
            placeholder="e.g. Wallet recharge not reflecting"
            placeholderTextColor="#9CA3AF"
            style={styles.input}
            maxLength={140}
            returnKeyType="next"
          />
        </View>
        <Text style={styles.charCount}>{title.length} / 140</Text>

        {/* Description */}
        <Text style={[styles.label, { marginTop: 14 }]}>Description</Text>
        <View style={[styles.inputWrap, { minHeight: 160, alignItems: "stretch" }]}>
          <TextInput
            testID="ticket-desc"
            value={description}
            onChangeText={setDesc}
            placeholder="Tell us what happened in as much detail as you can…"
            placeholderTextColor="#9CA3AF"
            style={[styles.input, { minHeight: 140, textAlignVertical: "top" }]}
            multiline
            maxLength={5000}
          />
        </View>
        <Text style={styles.charCount}>{description.length} / 5000</Text>

        <View style={{ height: 8 }} />
      </ScrollView>

      {/* Sticky bottom action bar — keeps the primary CTA in thumb reach */}
      <View style={[styles.bar, { paddingBottom: insets.bottom + 10 }]}>
        <TouchableOpacity
          testID="ticket-submit"
          onPress={submit}
          activeOpacity={0.85}
          disabled={saving}
          style={[styles.submitBtn, saving && { opacity: 0.6 }]}
        >
          <PhIcon name="send" size={16} color="#fff" />
          <Text style={styles.submitTxt}>{saving ? "Submitting…" : "Submit Request"}</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 13, fontWeight: "800", color: "#0F172A", marginBottom: 8 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    flexDirection: "row", alignItems: "center", gap: 6,
    paddingHorizontal: 12, paddingVertical: 8,
    borderRadius: 999, backgroundColor: "#fff",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  chipTxt: { fontSize: 12.5, fontWeight: "700", color: "#334155" },
  inputWrap: {
    backgroundColor: "#fff", borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
    paddingHorizontal: 12, paddingVertical: Platform.OS === "ios" ? 12 : 6,
  },
  input: { fontSize: 14, color: "#0F172A", paddingVertical: 6 },
  charCount: { fontSize: 11, color: "#94A3B8", textAlign: "right", marginTop: 4 },
  bar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    backgroundColor: "#fff",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
    paddingHorizontal: 16, paddingTop: 10,
  },
  submitBtn: {
    flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 8,
    backgroundColor: colors.primary,
    borderRadius: 12, paddingVertical: 14,
  },
  submitTxt: { color: "#fff", fontSize: 15, fontWeight: "800" },
});
