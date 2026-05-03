/**
 * AI WhatsApp Template Generator Modal (Phase-15)
 * ------------------------------------------------
 * One-shot modal that lets the user describe a tone in 1 line and
 * receives 9 ready-to-use WhatsApp message variants (3 languages ×
 * 3 variants) for ONE template type. Output is editable per-tab and
 * saved back to settings.whatsapp_template_variants.<ttype>.
 *
 * Wallet handling: per-plan rate is debited atomically by the backend
 * BEFORE the LLM call and auto-refunded on any failure. This screen
 * just shows the rate up-front so the operator can decide.
 */
import React, { useEffect, useMemo, useState } from "react";
import {
  Modal,
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  TextInput,
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { Api } from "../lib/api";

type Variants = { gu: string[]; hi: string[]; en: string[] };

type Props = {
  visible: boolean;
  templateType: string;          // e.g. "delivery_confirmation"
  templateLabel: string;         // pretty display label
  initialVariants?: Variants | null;
  onClose: () => void;
  onSaved?: () => void;
};

const QUICK_CHIPS = [
  { key: "Short",        emoji: "⚡", color: "#0EA5E9" },
  { key: "Professional", emoji: "💼", color: "#1F4FBF" },
  { key: "Friendly",     emoji: "😊", color: "#10B981" },
  { key: "Premium",      emoji: "👑", color: "#9333EA" },
  { key: "Urgent",       emoji: "⏰", color: "#DC2626" },
];

const LANG_TABS: { key: keyof Variants; label: string; emoji: string }[] = [
  { key: "gu", label: "ગુજરાતી", emoji: "🇮🇳" },
  { key: "hi", label: "हिन्दी",   emoji: "🇮🇳" },
  { key: "en", label: "English",  emoji: "🇬🇧" },
];

const EMPTY: Variants = { gu: ["", "", ""], hi: ["", "", ""], en: ["", "", ""] };

export default function AiTemplateGenerator({
  visible,
  templateType,
  templateLabel,
  initialVariants,
  onClose,
  onSaved,
}: Props) {
  const [tone, setTone] = useState("");
  const [chip, setChip] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [variants, setVariants] = useState<Variants>(EMPTY);
  const [activeLang, setActiveLang] = useState<keyof Variants>("gu");
  const [aiCost, setAiCost] = useState<number>(0);
  const [walletBalance, setWalletBalance] = useState<number | null>(null);
  const [pricingLoaded, setPricingLoaded] = useState(false);

  // Pull the user's per-plan AI generation cost the moment we open.
  useEffect(() => {
    if (!visible) return;
    (async () => {
      try {
        const p = await Api.meWhatsAppPricing();
        setAiCost(Number(p.ai_generation_credits || 0));
      } catch {/* ignore — fall back to "—" cost label */}
      try {
        const w = await Api.getWallet().catch(() => null);
        if (w && typeof (w as any).remaining_credits === "number") {
          setWalletBalance((w as any).remaining_credits);
        }
      } catch {/* ignore */}
      setPricingLoaded(true);
    })();
  }, [visible]);

  // Pre-populate from existing saved variants.
  useEffect(() => {
    if (!visible) return;
    if (initialVariants && (
      initialVariants.gu?.length || initialVariants.hi?.length || initialVariants.en?.length
    )) {
      setVariants({
        gu: padTo3(initialVariants.gu),
        hi: padTo3(initialVariants.hi),
        en: padTo3(initialVariants.en),
      });
    } else {
      setVariants(EMPTY);
    }
  }, [visible, initialVariants]);

  const hasVariants = useMemo(
    () => Object.values(variants).some((arr) => arr.some((s) => (s || "").trim().length > 0)),
    [variants],
  );

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const res = await Api.meGenerateTemplateVariants(
        templateType,
        tone.trim(),
        chip || undefined,
      );
      setVariants({
        gu: padTo3(res.variants.gu),
        hi: padTo3(res.variants.hi),
        en: padTo3(res.variants.en),
      });
      // Refresh wallet balance for instant feedback after the debit.
      try {
        const w = await Api.walletStatus();
        if (w && typeof (w as any).remaining_credits === "number") {
          setWalletBalance((w as any).remaining_credits);
        }
      } catch {/* ignore */}
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Generation failed";
      if (e?.response?.status === 402) {
        Alert.alert(
          "Wallet balance too low",
          "Top up your wallet from Settings → Wallet, then try again.",
        );
      } else {
        Alert.alert("Couldn't generate", String(msg));
      }
    } finally {
      setGenerating(false);
    }
  };

  const handleEditVariant = (lang: keyof Variants, idx: number, value: string) => {
    setVariants((prev) => {
      const arr = [...prev[lang]];
      arr[idx] = value;
      return { ...prev, [lang]: arr };
    });
  };

  const handleSave = async () => {
    // Strip empty trailing variants but keep at least 1 per language.
    const cleaned: Record<string, string[]> = {};
    (["gu", "hi", "en"] as const).forEach((L) => {
      const kept = variants[L].map((s) => (s || "").trim()).filter(Boolean);
      if (kept.length) cleaned[L] = kept;
    });
    if (Object.keys(cleaned).length === 0) {
      Alert.alert("Nothing to save", "Generate templates first or paste your own.");
      return;
    }
    setSaving(true);
    try {
      await Api.meSaveTemplateVariants(templateType, cleaned);
      Alert.alert("Saved", "Templates saved. They will rotate V1 → V2 → V3 on each send.");
      onSaved?.();
      onClose();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    Alert.alert(
      "Reset variants?",
      "This clears all 9 fields in this modal. Your saved templates won't change until you press Save.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: () => {
            setVariants(EMPTY);
            setTone("");
            setChip(null);
          },
        },
      ],
    );
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <View style={styles.backdrop}>
        <View style={styles.sheet}>
          {/* Header */}
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.headerTitle}>✨ AI Template Generator</Text>
              <Text style={styles.headerSub} numberOfLines={1}>
                For: <Text style={{ fontWeight: "800" }}>{templateLabel}</Text>
              </Text>
            </View>
            <TouchableOpacity style={styles.closeBtn} onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <Ionicons name="close" size={22} color="#374151" />
            </TouchableOpacity>
          </View>

          <KeyboardAvoidingView
            behavior={Platform.OS === "ios" ? "padding" : undefined}
            style={{ flex: 1 }}
            keyboardVerticalOffset={20}
          >
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={{ padding: 14, paddingBottom: 30 }}
          >
            {/* Tone description input */}
            <Text style={styles.label}>Describe your message</Text>
            <Text style={styles.hint}>
              e.g. "polite, short, premium, friendly". Leave blank for our smart default.
            </Text>
            <TextInput
              value={tone}
              onChangeText={setTone}
              placeholder="polite + warm tone"
              placeholderTextColor="#9CA3AF"
              style={styles.toneInput}
              multiline
            />

            {/* Quick chips */}
            <Text style={[styles.label, { marginTop: 10 }]}>Quick tone presets</Text>
            <View style={styles.chipsRow}>
              {QUICK_CHIPS.map((c) => {
                const active = chip === c.key;
                return (
                  <TouchableOpacity
                    key={c.key}
                    style={[
                      styles.chip,
                      active && { backgroundColor: c.color, borderColor: c.color },
                    ]}
                    onPress={() => setChip(active ? null : c.key)}
                  >
                    <Text style={[styles.chipText, active && { color: "#fff" }]}>
                      {c.emoji}  {c.key}
                    </Text>
                  </TouchableOpacity>
                );
              })}
            </View>

            {/* Cost preview */}
            {pricingLoaded && (
              <View style={styles.costBox}>
                <Ionicons name="wallet-outline" size={16} color="#92400E" />
                <Text style={styles.costText}>
                  Cost: {aiCost.toFixed(2)} credit{aiCost === 1 ? "" : "s"} per generation
                  {walletBalance !== null
                    ? ` · Balance: ${walletBalance.toFixed(2)}`
                    : ""}
                </Text>
              </View>
            )}

            {/* Generate button */}
            <TouchableOpacity
              style={[styles.generateBtn, generating && { opacity: 0.5 }]}
              onPress={handleGenerate}
              disabled={generating || saving}
            >
              {generating ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="sparkles" size={16} color="#fff" />
                  <Text style={styles.generateBtnText}>
                    {hasVariants ? "Regenerate Templates" : "Generate Templates"}
                  </Text>
                </>
              )}
            </TouchableOpacity>

            {/* Output: language tabs */}
            {hasVariants && (
              <>
                <View style={styles.divider} />
                <View style={styles.tabRow}>
                  {LANG_TABS.map((t) => {
                    const active = activeLang === t.key;
                    const has = variants[t.key].some((s) => (s || "").trim());
                    return (
                      <TouchableOpacity
                        key={t.key}
                        style={[styles.tab, active && styles.tabActive]}
                        onPress={() => setActiveLang(t.key)}
                      >
                        <Text style={[styles.tabText, active && styles.tabTextActive]}>
                          {t.emoji}  {t.label}
                        </Text>
                        {has && (
                          <View
                            style={[
                              styles.tabDot,
                              { backgroundColor: active ? "#fff" : "#10B981" },
                            ]}
                          />
                        )}
                      </TouchableOpacity>
                    );
                  })}
                </View>

                {/* 3 variant cards in active language */}
                {variants[activeLang].map((value, idx) => (
                  <View key={`${activeLang}-${idx}`} style={styles.varCard}>
                    <View style={styles.varCardHeader}>
                      <View style={styles.varBadge}>
                        <Text style={styles.varBadgeText}>V{idx + 1}</Text>
                      </View>
                      <Text style={styles.varHint} numberOfLines={1}>
                        Editable — rotates V1 → V2 → V3 → V1 on each send
                      </Text>
                    </View>
                    <TextInput
                      value={value}
                      onChangeText={(t) => handleEditVariant(activeLang, idx, t)}
                      placeholder={`Variant ${idx + 1} ${LANG_TABS.find((x) => x.key === activeLang)?.label}`}
                      placeholderTextColor="#9CA3AF"
                      style={styles.varInput}
                      multiline
                      textAlignVertical="top"
                    />
                  </View>
                ))}
              </>
            )}
          </ScrollView>

          {/* Sticky bottom action bar */}
          <View style={styles.bottomBar}>
            <TouchableOpacity style={styles.resetBtn} onPress={handleReset}>
              <Ionicons name="refresh" size={14} color="#374151" />
              <Text style={styles.resetBtnText}>Reset</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.saveBtn, (!hasVariants || saving) && { opacity: 0.5 }]}
              onPress={handleSave}
              disabled={!hasVariants || saving}
            >
              {saving ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <Ionicons name="save-outline" size={15} color="#fff" />
                  <Text style={styles.saveBtnText}>Save All</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
          </KeyboardAvoidingView>
        </View>
      </View>
    </Modal>
  );
}

function padTo3(arr?: string[] | null): string[] {
  const out = [...(arr || [])].slice(0, 3);
  while (out.length < 3) out.push("");
  return out;
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1, backgroundColor: "rgba(15,23,42,0.55)",
    justifyContent: "flex-end",
  },
  sheet: {
    backgroundColor: "#F7F7F9",
    borderTopLeftRadius: 22, borderTopRightRadius: 22,
    height: "92%",
    overflow: "hidden",
  },
  header: {
    flexDirection: "row", alignItems: "center",
    paddingHorizontal: 16, paddingTop: 16, paddingBottom: 12,
    backgroundColor: "#fff",
    borderBottomWidth: 1, borderBottomColor: "#E5E7EB",
  },
  headerTitle: { fontSize: 16, fontWeight: "800", color: "#111827" },
  headerSub: { fontSize: 12, color: "#6B7280", marginTop: 2 },
  closeBtn: { padding: 4 },

  label: { fontSize: 13, fontWeight: "800", color: "#111827", marginTop: 4 },
  hint: { fontSize: 11, color: "#6B7280", marginTop: 2, marginBottom: 6, lineHeight: 15 },
  toneInput: {
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, padding: 12, fontSize: 14, color: "#111827",
    minHeight: 60, textAlignVertical: "top",
  },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 7,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 999,
  },
  chipText: { fontSize: 12, fontWeight: "700", color: "#374151" },

  costBox: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: "#FEF3C7", borderColor: "#FDE68A", borderWidth: 1,
    borderRadius: 10, padding: 10, marginTop: 12,
  },
  costText: { fontSize: 12, color: "#92400E", fontWeight: "700", flex: 1 },

  generateBtn: {
    backgroundColor: "#6B5BFF", borderRadius: 12, padding: 14,
    flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 6,
    marginTop: 12,
  },
  generateBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },

  divider: { height: 1, backgroundColor: "#E5E7EB", marginVertical: 14 },

  tabRow: { flexDirection: "row", gap: 6 },
  tab: {
    flex: 1, paddingVertical: 9, borderRadius: 10,
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    flexDirection: "row", justifyContent: "center", alignItems: "center", gap: 4,
  },
  tabActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  tabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  tabTextActive: { color: "#fff" },
  tabDot: { width: 6, height: 6, borderRadius: 3 },

  varCard: {
    backgroundColor: "#fff", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 12, padding: 10, marginTop: 10,
  },
  varCardHeader: {
    flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 6,
  },
  varBadge: {
    backgroundColor: "#EEF2FF", paddingHorizontal: 8, paddingVertical: 2,
    borderRadius: 6,
  },
  varBadgeText: { fontSize: 11, fontWeight: "800", color: "#4338CA" },
  varHint: { fontSize: 10, color: "#9CA3AF", flex: 1 },
  varInput: {
    backgroundColor: "#F9FAFB", borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 8, padding: 10, fontSize: 13, color: "#111827",
    minHeight: 90,
  },

  bottomBar: {
    flexDirection: "row", gap: 8,
    paddingHorizontal: 16, paddingTop: 10, paddingBottom: 16,
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
    backgroundColor: "#fff",
  },
  resetBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4,
    paddingVertical: 12, paddingHorizontal: 16,
    backgroundColor: "#F3F4F6", borderRadius: 10,
  },
  resetBtnText: { color: "#374151", fontWeight: "700", fontSize: 13 },
  saveBtn: {
    flex: 1,
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 12,
    backgroundColor: "#10B981", borderRadius: 10,
  },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 14 },
});
