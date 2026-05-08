/**
 * Settings → WhatsApp Templates (Phase-12)
 * ----------------------------------------
 * Lets every user customise the 4 outbound WhatsApp message templates
 * (shipment_sent, dispatch_confirmation, delivery_confirmation,
 * delivery_done) in 3 languages each (gu / hi / en) plus pick a
 * permanent default language.
 *
 * Hierarchy at send time:
 *   user override → admin default → bundled fallback
 *
 * All edits are stored under settings.whatsapp_templates per user.
 * Empty fields fall through to the admin/bundled chain.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import PhIcon from "../../components/PhIcon";
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Stack } from "expo-router";
import { Api } from "../../lib/api";
import AiTemplateGenerator from "../../components/AiTemplateGenerator";

const TYPE_META: Record<string, { label: string; sub: string; icon: any; tone: string }> = {
  shipment_sent: {
    label: "Shipment Booked",
    sub: "Sent when an order is created / label is printed",
    icon: "receipt-outline",
    tone: "#6B5BFF",
  },
  dispatch_confirmation: {
    label: "Ready to Ship Confirmation",
    sub: "Sent when the parcel leaves your store (Shipped status)",
    icon: "rocket-outline",
    tone: "#1F4FBF",
  },
  delivery_confirmation: {
    label: "Delivery Confirmation",
    sub: "After ETA — asks the customer if the parcel arrived",
    icon: "alert-circle-outline",
    tone: "#B45309",
  },
  delivery_done: {
    label: "Delivery Thanks",
    sub: "Sent after the customer confirms receipt",
    icon: "checkmark-circle-outline",
    tone: "#1F9D55",
  },
  feedback_request: {
    label: "Feedback Request",
    sub: "Asks the customer for a rating / review (1–5 ★)",
    icon: "star-outline",
    tone: "#1E40AF",
  },
};

const LANG_META: Record<string, { label: string; emoji: string }> = {
  gu: { label: "ગુજરાતી", emoji: "🇮🇳" },
  hi: { label: "हिन्दी", emoji: "🇮🇳" },
  en: { label: "English", emoji: "🇬🇧" },
};

const VARIABLE_HINTS = [
  "{customer_name}",
  "{order_id}",
  "{tracking_id}",
  "{courier}",
  "{eta_days}",
];

// Phase-12: business-link variables — substituted server-side from
// settings.business_links. Surfaced as a separate chip group so the
// editor signals "these need their URLs set above before they'll
// expand to anything useful".
const BUSINESS_LINK_HINTS = [
  "{google_review_url}",
  "{website_url}",
];

type ServerData = {
  admin_templates: Record<string, Record<string, string>>;
  user_templates: Record<string, Record<string, string>>;
  default_language: string;
  types: string[];
  languages: string[];
  defaults: Record<string, Record<string, string>>;
  business_links: {
    google_review_url: string;
    website_url: string;
  };
};

export default function WhatsAppTemplatesSettings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [data, setData] = useState<ServerData | null>(null);
  // The local edit buffer — keyed [type][lang]. Falls back to the
  // user's saved override at load time.
  const [edits, setEdits] = useState<Record<string, Record<string, string>>>({});
  const [activeType, setActiveType] = useState<string>("dispatch_confirmation");
  const [activeLang, setActiveLang] = useState<string>("gu");
  const [defaultLang, setDefaultLang] = useState("gu");
  // Phase-12: business URLs that the {google_review_url} /
  // {website_url} variables expand to at send time. Empty = the
  // variable substitutes to "" (template still readable).
  const [googleReviewUrl, setGoogleReviewUrl] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  // Phase-15 E: shop / helpline phone numbers — auto-injected into
  // {shop_phone} and {helpline} placeholders at send time.
  const [shopPhone, setShopPhone] = useState("");
  const [helpline, setHelpline] = useState("");

  // Phase-15: AI generator modal + saved variants per template type.
  // savedVariants[ttype] = { gu: [v1,v2,v3], hi: [...], en: [...] }
  const [aiOpen, setAiOpen] = useState(false);
  const [aiType, setAiType] = useState<string>("");
  const [savedVariants, setSavedVariants] = useState<
    Record<string, { gu: string[]; hi: string[]; en: string[] }>
  >({});

  const loadVariants = useCallback(async () => {
    try {
      const v = await Api.meGetTemplateVariants();
      setSavedVariants((v.variants || {}) as any);
    } catch {/* ignore — variants are optional */}
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await Api.meWhatsAppTemplates();
      setData(d);
      setEdits(d.user_templates || {});
      setActiveLang(d.default_language || "gu");
      setDefaultLang(d.default_language || "gu");
      setGoogleReviewUrl(d.business_links?.google_review_url || "");
      setWebsiteUrl(d.business_links?.website_url || "");
      setShopPhone(((d as any).shop_phone || "") as string);
      setHelpline(((d as any).helpline || "") as string);
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); loadVariants(); }, [load, loadVariants]);

  const setEdit = (type: string, lang: string, val: string) => {
    setEdits((prev) => {
      const next = { ...prev };
      if (!val.trim()) {
        // Clearing means "fall back to admin/default".
        if (next[type]) {
          const cleaned = { ...next[type] };
          delete cleaned[lang];
          if (Object.keys(cleaned).length === 0) delete next[type];
          else next[type] = cleaned;
        }
      } else {
        next[type] = { ...(next[type] || {}), [lang]: val };
      }
      return next;
    });
  };

  const placeholderFor = (type: string, lang: string): string => {
    if (!data) return "";
    return (
      data.admin_templates?.[type]?.[lang] ||
      data.defaults?.[type]?.[lang] ||
      ""
    );
  };

  const currentValue = (type: string, lang: string): string => {
    return edits[type]?.[lang] ?? "";
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await Api.meSaveWhatsAppTemplates(
        edits,
        defaultLang,
        {
          google_review_url: googleReviewUrl.trim(),
          website_url: websiteUrl.trim(),
        },
        {
          shop_phone: shopPhone.trim(),
          helpline:   helpline.trim(),
        },
      );
      Alert.alert("Saved", "Your WhatsApp templates are updated.");
      load();
    } catch (e: any) {
      Alert.alert("Error", e?.response?.data?.detail || e?.message || "Failed");
    } finally {
      setSaving(false);
    }
  };

  const handleResetType = (type: string) => {
    Alert.alert(
      "Reset overrides",
      `Clear your overrides for "${TYPE_META[type]?.label || type}" in all 3 languages? Admin defaults will apply.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Reset",
          style: "destructive",
          onPress: () => {
            setEdits((prev) => {
              const next = { ...prev };
              delete next[type];
              return next;
            });
          },
        },
      ],
    );
  };

  const types = useMemo(
    () => data?.types || Object.keys(TYPE_META),
    [data],
  );

  const languages = useMemo(
    () => data?.languages || ["gu", "hi", "en"],
    [data],
  );

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: "#F7F7F9" }}>
      <Stack.Screen
        options={{ title: "WhatsApp Templates", headerBackTitle: "Settings" }}
      />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={{ padding: 14, paddingBottom: 120 }}
          keyboardShouldPersistTaps="handled"
        >
          {loading || !data ? (
            <ActivityIndicator color="#6B5BFF" style={{ marginVertical: 36 }} />
          ) : (
            <>
              {/* Phase-12: Business links — referenced by the
                  {google_review_url} / {website_url} variables in the
                  Feedback Request template (and reusable in any
                  custom message). Substituted server-side at send
                  time so a single edit here updates every template
                  that uses these variables. Leave blank to remove
                  the line entirely. */}
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Business Links</Text>
                <Text style={styles.sectionHint}>
                  These two URLs auto-fill in any template that uses
                  the variables{" "}
                  <Text style={{ fontFamily: "monospace", color: "#6B5BFF" }}>
                    {"{google_review_url}"}
                  </Text>{" "}
                  or{" "}
                  <Text style={{ fontFamily: "monospace", color: "#6B5BFF" }}>
                    {"{website_url}"}
                  </Text>
                  . Leave blank to skip that line.
                </Text>
                <Text style={styles.bizLinkLabel}>⭐ Google Review URL</Text>
                <TextInput
                  style={styles.bizLinkInput}
                  placeholder="https://g.page/r/..."
                  placeholderTextColor="#9CA3AF"
                  value={googleReviewUrl}
                  onChangeText={setGoogleReviewUrl}
                  autoCapitalize="none"
                  keyboardType="url"
                />
                <Text style={[styles.bizLinkLabel, { marginTop: 10 }]}>
                  🛒 Website / Product Reviews URL
                </Text>
                <TextInput
                  style={styles.bizLinkInput}
                  placeholder="https://yourshop.com/reviews"
                  placeholderTextColor="#9CA3AF"
                  value={websiteUrl}
                  onChangeText={setWebsiteUrl}
                  autoCapitalize="none"
                  keyboardType="url"
                />
                {/* Phase-15 E: shop / helpline phone numbers — auto-fill
                    {shop_phone} and {helpline} placeholders. */}
                <Text style={[styles.bizLinkLabel, { marginTop: 14 }]}>
                  Shop Phone (used as {"{shop_phone}"})
                </Text>
                <TextInput
                  style={styles.bizLinkInput}
                  placeholder="9xxxxxxxxx"
                  placeholderTextColor="#9CA3AF"
                  value={shopPhone}
                  onChangeText={setShopPhone}
                  keyboardType="phone-pad"
                  maxLength={15}
                />
                <Text style={[styles.bizLinkLabel, { marginTop: 10 }]}>
                  Helpline (used as {"{helpline}"} — falls back to shop phone)
                </Text>
                <TextInput
                  style={styles.bizLinkInput}
                  placeholder="1800-xxx-xxxx (optional)"
                  placeholderTextColor="#9CA3AF"
                  value={helpline}
                  onChangeText={setHelpline}
                  keyboardType="phone-pad"
                  maxLength={15}
                />
              </View>

              {/* Default language picker */}
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Default Send Language</Text>
                <Text style={styles.sectionHint}>
                  Pick once — every "Send WhatsApp" button will use this
                  language unless you override per-shipment.
                </Text>
                <View style={styles.langRow}>
                  {languages.map((l) => {
                    const active = defaultLang === l;
                    return (
                      <TouchableOpacity
                        key={l}
                        style={[styles.langPill, active && styles.langPillActive]}
                        onPress={() => setDefaultLang(l)}
                      >
                        <Text
                          style={[
                            styles.langPillText,
                            active && styles.langPillTextActive,
                          ]}
                        >
                          {LANG_META[l]?.label || l}
                        </Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>
              </View>

              {/* Type selector */}
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Message Type</Text>
                {types.map((t) => {
                  const meta = TYPE_META[t];
                  const active = activeType === t;
                  const hasOverrides = !!edits[t] && Object.keys(edits[t]).length > 0;
                  const v = savedVariants[t];
                  const variantCount =
                    (v?.gu?.length || 0) + (v?.hi?.length || 0) + (v?.en?.length || 0);
                  return (
                    <View key={t}>
                      <TouchableOpacity
                        style={[styles.typeRow, active && styles.typeRowActive]}
                        onPress={() => setActiveType(t)}
                      >
                      <View
                        style={[
                          styles.typeIconBox,
                          { backgroundColor: (meta?.tone || "#6B5BFF") + "22" },
                        ]}
                      >
                        <PhIcon
                          name={meta?.icon || "chatbubble-outline"}
                          size={18}
                          color={meta?.tone || "#6B5BFF"}
                        />
                      </View>
                      <View style={{ flex: 1 }}>
                        <Text style={styles.typeLabel}>
                          {meta?.label || t}
                          {hasOverrides && (
                            <Text style={styles.overrideTag}> · custom</Text>
                          )}
                          {variantCount > 0 && (
                            <Text style={styles.variantTag}> · {variantCount} AI variants</Text>
                          )}
                        </Text>
                        <Text style={styles.typeSub} numberOfLines={2}>
                          {meta?.sub || ""}
                        </Text>
                      </View>
                      <PhIcon
                        name={active ? "chevron-up" : "chevron-down"}
                        size={18}
                        color="#9CA3AF"
                      />
                      </TouchableOpacity>
                      {/* Phase-15: AI Generate button — one per template
                          type, keeps its own DB block keyed by ttype.
                          Modal opens with prefilled saved variants
                          (if any) for fast re-edits without re-spending
                          credits. */}
                      <TouchableOpacity
                        style={styles.aiBtn}
                        onPress={() => {
                          setAiType(t);
                          setAiOpen(true);
                        }}
                      >
                        <PhIcon name="sparkles" size={13} color="#6B5BFF" />
                        <Text style={styles.aiBtnText}>
                          AI Generate {variantCount > 0 ? "/ Edit" : ""} 9 Variants
                        </Text>
                      </TouchableOpacity>
                    </View>
                  );
                })}
              </View>

              {/* Editor for activeType */}
              <View style={styles.section}>
                <View style={styles.editorHeader}>
                  <Text style={styles.sectionTitle}>
                    Edit:{" "}
                    <Text style={{ color: TYPE_META[activeType]?.tone || "#6B5BFF" }}>
                      {TYPE_META[activeType]?.label || activeType}
                    </Text>
                  </Text>
                  <TouchableOpacity
                    style={styles.resetBtn}
                    onPress={() => handleResetType(activeType)}
                  >
                    <PhIcon name="refresh" size={12} color="#374151" />
                    <Text style={styles.resetBtnText}>Reset</Text>
                  </TouchableOpacity>
                </View>

                {/* Language tabs */}
                <View style={styles.langTabs}>
                  {languages.map((l) => {
                    const active = activeLang === l;
                    const has = !!edits[activeType]?.[l];
                    return (
                      <TouchableOpacity
                        key={l}
                        style={[styles.langTab, active && styles.langTabActive]}
                        onPress={() => setActiveLang(l)}
                      >
                        <Text
                          style={[
                            styles.langTabText,
                            active && styles.langTabTextActive,
                          ]}
                        >
                          {LANG_META[l]?.label || l}
                        </Text>
                        {has && (
                          <View
                            style={[
                              styles.dot,
                              {
                                backgroundColor: active ? "#fff" : "#6B5BFF",
                              },
                            ]}
                          />
                        )}
                      </TouchableOpacity>
                    );
                  })}
                </View>

                {/* TextArea */}
                <TextInput
                  style={styles.textarea}
                  multiline
                  placeholder={placeholderFor(activeType, activeLang)}
                  placeholderTextColor="#9CA3AF"
                  value={currentValue(activeType, activeLang)}
                  onChangeText={(t) => setEdit(activeType, activeLang, t)}
                  textAlignVertical="top"
                />

                <Text style={styles.previewLabel}>
                  Live preview (placeholder substituted with example data):
                </Text>
                <View style={styles.previewBox}>
                  <Text style={styles.previewText}>
                    {(currentValue(activeType, activeLang) ||
                      placeholderFor(activeType, activeLang) ||
                      "—")
                      .replace(/\{customer_name\}/g, "Ramesh")
                      .replace(/\{order_id\}/g, "ORD-1234")
                      .replace(/\{tracking_id\}/g, "ND00056")
                      .replace(/\{courier\}/g, "Demo Courier")
                      .replace(/\{eta_days\}/g, "5")}
                  </Text>
                </View>

                {/* Variable chips */}
                <Text style={styles.varLabel}>Available variables (tap to insert):</Text>
                <View style={styles.varChipsRow}>
                  {VARIABLE_HINTS.map((v) => (
                    <TouchableOpacity
                      key={v}
                      style={styles.varChip}
                      onPress={() => {
                        const current = currentValue(activeType, activeLang) ||
                                        placeholderFor(activeType, activeLang);
                        setEdit(activeType, activeLang, current + v);
                      }}
                    >
                      <Text style={styles.varChipText}>{v}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                {/* Phase-12: Business-link chips. Highlighted gold so
                    operators notice they auto-fill from the URLs at
                    the top of the screen. */}
                <Text style={[styles.varLabel, { marginTop: 10 }]}>
                  Business links (auto-filled from URLs above):
                </Text>
                <View style={styles.varChipsRow}>
                  {BUSINESS_LINK_HINTS.map((v) => (
                    <TouchableOpacity
                      key={v}
                      style={[
                        styles.varChip,
                        {
                          backgroundColor: "#FEF3C7",
                          borderColor: "#FCD34D",
                        },
                      ]}
                      onPress={() => {
                        const current = currentValue(activeType, activeLang) ||
                                        placeholderFor(activeType, activeLang);
                        setEdit(activeType, activeLang, current + v);
                      }}
                    >
                      <Text style={[styles.varChipText, { color: "#92400E" }]}>
                        {v}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </View>
            </>
          )}
        </ScrollView>

        {/* Sticky save bar */}
        <View style={styles.stickyBar}>
          <Text style={styles.bottomHint}>
            Empty fields use admin / bundled defaults
          </Text>
          <TouchableOpacity
            style={[styles.saveBtn, saving && { opacity: 0.5 }]}
            onPress={handleSave}
            disabled={saving}
          >
            <PhIcon name="save-outline" size={16} color="#fff" />
            <Text style={styles.saveBtnText}>
              {saving ? "Saving…" : "Save Templates"}
            </Text>
          </TouchableOpacity>
        </View>
      </KeyboardAvoidingView>

      {/* Phase-15: AI Generator modal */}
      <AiTemplateGenerator
        visible={aiOpen}
        templateType={aiType}
        templateLabel={TYPE_META[aiType]?.label || aiType}
        initialVariants={
          aiType
            ? {
                gu: savedVariants[aiType]?.gu || [],
                hi: savedVariants[aiType]?.hi || [],
                en: savedVariants[aiType]?.en || [],
              }
            : null
        }
        onClose={() => setAiOpen(false)}
        onSaved={() => {
          loadVariants();
        }}
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  section: {
    backgroundColor: "#fff",
    borderRadius: 14,
    padding: 14,
    marginBottom: 12,
    borderWidth: 1,
    borderColor: "#EDEEF1",
  },
  sectionTitle: { fontSize: 14, fontWeight: "800", color: "#111827" },
  sectionHint: {
    fontSize: 12, color: "#6B7280",
    marginTop: 4, marginBottom: 8, lineHeight: 17,
  },
  langRow: { flexDirection: "row", gap: 8, marginTop: 4 },
  langPill: {
    flex: 1, paddingVertical: 10, borderRadius: 10,
    backgroundColor: "#F3F4F6", alignItems: "center",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  langPillActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  langPillText: { fontSize: 13, fontWeight: "800", color: "#374151" },
  langPillTextActive: { color: "#fff" },
  typeRow: {
    flexDirection: "row", alignItems: "center",
    gap: 12, paddingVertical: 10, paddingHorizontal: 8,
    borderRadius: 10, marginTop: 8,
    backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#EDEEF1",
  },
  typeRowActive: { backgroundColor: "#F8F7FF", borderColor: "#6B5BFF" },
  typeIconBox: {
    width: 36, height: 36, borderRadius: 10,
    alignItems: "center", justifyContent: "center",
  },
  typeLabel: { fontSize: 14, fontWeight: "800", color: "#111827" },
  typeSub: { fontSize: 11, color: "#6B7280", marginTop: 2 },
  overrideTag: { color: "#6B5BFF", fontWeight: "700", fontSize: 11 },
  variantTag: { color: "#10B981", fontWeight: "700", fontSize: 11 },
  aiBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    marginTop: 6, marginBottom: 4,
    paddingVertical: 9, paddingHorizontal: 10,
    backgroundColor: "#EEF2FF",
    borderWidth: 1, borderColor: "#C7D2FE", borderStyle: "dashed",
    borderRadius: 10,
  },
  aiBtnText: { fontSize: 12, fontWeight: "800", color: "#4338CA" },
  editorHeader: {
    flexDirection: "row", alignItems: "center",
    justifyContent: "space-between", marginBottom: 10,
  },
  resetBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6,
    backgroundColor: "#F3F4F6", borderRadius: 999,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  resetBtnText: { fontSize: 11, fontWeight: "800", color: "#374151" },
  langTabs: { flexDirection: "row", gap: 6, marginBottom: 10 },
  langTab: {
    flex: 1, flexDirection: "row",
    alignItems: "center", justifyContent: "center", gap: 6,
    paddingVertical: 9, borderRadius: 10,
    backgroundColor: "#F3F4F6",
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  langTabActive: { backgroundColor: "#6B5BFF", borderColor: "#6B5BFF" },
  langTabText: { fontSize: 12, fontWeight: "800", color: "#374151" },
  langTabTextActive: { color: "#fff" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  textarea: {
    backgroundColor: "#F8F9FB",
    borderWidth: 1, borderColor: "#E5E7EB",
    borderRadius: 10, padding: 12,
    minHeight: 130, fontSize: 13,
    color: "#111827", lineHeight: 20,
  },
  previewLabel: {
    fontSize: 11, fontWeight: "700", color: "#6B7280",
    marginTop: 12, marginBottom: 4,
  },
  previewBox: {
    backgroundColor: "#E6F7EE",
    borderWidth: 1, borderColor: "#B7E5C9",
    borderRadius: 10, padding: 10,
  },
  previewText: { fontSize: 12, color: "#0F5132", lineHeight: 18 },
  varLabel: {
    fontSize: 11, fontWeight: "700", color: "#6B7280",
    marginTop: 12, marginBottom: 6,
  },
  varChipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  varChip: {
    paddingHorizontal: 8, paddingVertical: 5,
    backgroundColor: "#EEE9FF", borderRadius: 6,
    borderWidth: 1, borderColor: "#D8D2FF",
  },
  varChipText: { fontSize: 11, fontWeight: "700", color: "#6B5BFF" },
  // Phase-12: Business links input fields.
  bizLinkLabel: {
    fontSize: 12,
    fontWeight: "700",
    color: "#374151",
    marginBottom: 4,
  },
  bizLinkInput: {
    backgroundColor: "#F8F9FB",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 13,
    color: "#111827",
  },
  stickyBar: {
    backgroundColor: "#fff",
    borderTopWidth: 1, borderColor: "#EDEEF1",
    paddingHorizontal: 12, paddingTop: 10,
    paddingBottom: Platform.OS === "ios" ? 22 : 12,
    flexDirection: "row", alignItems: "center", gap: 10,
  },
  bottomHint: { flex: 1, fontSize: 11, color: "#6B7280" },
  saveBtn: {
    flexDirection: "row", alignItems: "center",
    gap: 6, paddingHorizontal: 16, paddingVertical: 10,
    borderRadius: 10, backgroundColor: "#6B5BFF",
  },
  saveBtnText: { color: "#fff", fontWeight: "800", fontSize: 13 },
});
