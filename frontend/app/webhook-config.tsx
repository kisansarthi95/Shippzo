/**
 * /app/webhook-config.tsx — Webhook configuration screen.
 *
 * Lets the user:
 *   • View / copy / share their unique webhook URL.
 *   • Rotate the secret (invalidates the old URL).
 *   • Paste a sample JSON payload to see all available keys, and map
 *     each one to a shipment schema field (or per-user custom field).
 *   • Save the mapping which then drives /api/webhook/orders/<secret>.
 *
 * The mapping picker mirrors the CSV/Excel import screen so users only
 * need to learn one mental model — same status / timestamp / custom-
 * field semantics, same auto-suggest behaviour.
 */
import * as Clipboard from "expo-clipboard";
import { Stack, useRouter } from "expo-router";
import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Modal, Platform,
  RefreshControl, ScrollView, Share, StyleSheet, Text,
  TextInput, TouchableOpacity, View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import PhIcon from "../components/PhIcon";
import { Api } from "../lib/api";


/** Mapping value → display label for the field-picker pill. Mirrors
 *  /app/file-import.tsx so the UX stays consistent. */
const FIELD_LABEL: Record<string, string> = {
  customer_name:       "Customer Name",
  customer_phone:      "Customer Phone (mobile)",
  customer_alt_phone:  "Alternate Phone",
  customer_email:      "Email Address",
  customer_gstin:      "GSTIN",
  address:             "Address (line1 + line2 auto-merge)",
  city:                "City",
  state:               "State",
  pincode:             "Pincode",
  amount:              "COD to Collect (₹) — NOT the gross total",
  token_amount:        "Order Token / Advance (₹)",
  payment_mode:        "Payment Mode (COD / PAID)",
  items:               "Items / Products",
  category:            "Item Category",
  weight:              "Parcel Weight",
  box_dimensions:      "Box Size (e.g. 10×8×4)",
  box_length:          "Box Length",
  box_width:           "Box Width",
  box_height:          "Box Height",
  courier_hint:        "Courier (hint)",
  order_id:            "Order ID",
  notes:               "Notes / Remarks",
  status:              "Status (Pending / Shipped / Delivered…)",
  created_at_override: "Order Date / Timestamp",
};


type Config = {
  secret: string;
  url: string | null;
  name: string;
  mapping: Record<string, string>;
  schema_fields: string[];
  custom_fields: { id: string; label: string }[];
  configured: boolean;
  recent_samples?: { received_at: string; payload: any }[];
};

type Preview = {
  keys: string[];
  sample_values: Record<string, string>;
  schema_fields: string[];
  custom_fields: { id: string; label: string }[];
  suggested: Record<string, string>;
};


const SAMPLE_PAYLOAD = `{
  "customer_name": "Riya Patel",
  "customer_phone": "9876543210",
  "address_line_1": "123 MG Road",
  "address_line_2": "Near City Mall",
  "city": "Surat",
  "state": "Gujarat",
  "pincode": "395003",
  "amount": 1499,
  "payment_mode": "COD",
  "items": "Cotton Saree, Bangles",
  "status": "Shipped",
  "timestamp": "2026-04-29T14:30:00"
}`;


export default function WebhookConfigScreen() {
  const router = useRouter();
  const [cfg, setCfg]               = useState<Config | null>(null);
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving]         = useState(false);
  const [rotating, setRotating]     = useState(false);
  const [draft, setDraft]           = useState<Record<string, string>>({});
  const [showRaw, setShowRaw]       = useState(false);

  const [pasteOpen, setPasteOpen]   = useState(false);
  const [pasteText, setPasteText]   = useState(SAMPLE_PAYLOAD);
  const [previewing, setPreviewing] = useState(false);
  const [preview, setPreview]       = useState<Preview | null>(null);

  const [pickerKey, setPickerKey]   = useState<string | null>(null);

  // Phase F2.5 — friendly source name (e.g. "Shopify"). Required on
  // first generate so the imported pending-order cards carry a clean
  // badge label. Editable later via the "Rename" tap.
  const [nameOpen, setNameOpen]     = useState(false);
  const [nameDraft, setNameDraft]   = useState("");
  const [nameMode, setNameMode]     = useState<"generate" | "rename">("generate");

  const load = async () => {
    setLoading(true);
    try {
      const data = await Api.getWebhookConfig();
      setCfg(data);
      setDraft(data.mapping || {});
    } catch (e: any) {
      Alert.alert("Couldn't load webhook config", e?.message || "Unknown error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  // ── Webhook URL actions ────────────────────────────────────────────

  const copyUrl = async () => {
    if (!cfg?.url) return;
    await Clipboard.setStringAsync(cfg.url);
    Alert.alert("Copied", "Webhook URL is on your clipboard.");
  };

  const shareUrl = async () => {
    if (!cfg?.url) return;
    try {
      await Share.share({
        message:
          `My Shippzo webhook URL\n${cfg.url}\n\n` +
          `POST your order JSON here.`,
      });
    } catch {}
  };

  const generateSecret = async (nameOverride?: string) => {
    setRotating(true);
    try {
      const data = await Api.rotateWebhookSecret(nameOverride);
      setCfg((c) => c ? {
        ...c,
        secret: data.secret,
        url: data.url,
        name: data.name || c.name,
        configured: true,
      } : c);
      Alert.alert(
        "Secret created",
        nameOverride
          ? `Webhook URL ready for "${nameOverride}". Old URLs are now disabled.`
          : "Your webhook URL is ready. Old URLs (if any) are now disabled.",
      );
    } catch (e: any) {
      Alert.alert("Couldn't rotate secret", e?.message || "Try again later.");
    } finally {
      setRotating(false);
    }
  };

  const openGenerateName = () => {
    setNameMode("generate");
    setNameDraft(cfg?.name || "");
    setNameOpen(true);
  };

  const openRenameName = () => {
    setNameMode("rename");
    setNameDraft(cfg?.name || "");
    setNameOpen(true);
  };

  const submitName = async () => {
    const clean = nameDraft.trim();
    if (!clean) {
      Alert.alert("Name required", "Please enter a name (e.g. Shopify, Dukaan).");
      return;
    }
    setNameOpen(false);
    if (nameMode === "generate") {
      await generateSecret(clean);
    } else {
      try {
        const data = await Api.putWebhookName(clean);
        setCfg((c) => c ? { ...c, name: data.name } : c);
      } catch (e: any) {
        Alert.alert("Couldn't rename", e?.message || "Try again.");
      }
    }
  };

  const confirmRotate = () => {
    Alert.alert(
      "Rotate webhook secret?",
      "Any external system using your old URL will stop working until you update it with the new secret. Continue?",
      [
        { text: "Cancel", style: "cancel" },
        { text: "Rotate", style: "destructive", onPress: generateSecret },
      ],
    );
  };

  // ── Sample-payload mapping ──────────────────────────────────────────

  const previewPayload = async () => {
    setPreviewing(true);
    try {
      let parsed: any;
      try {
        parsed = JSON.parse(pasteText);
      } catch (e: any) {
        Alert.alert("Invalid JSON", e?.message || "Could not parse the payload.");
        return;
      }
      const data = await Api.previewWebhookPayload(parsed);
      setPreview(data);
      // Seed draft mapping with suggestions for keys we don't have a mapping for yet
      setDraft((d) => {
        const out = { ...d };
        for (const k of data.keys) {
          if (!(k in out) && data.suggested[k]) out[k] = data.suggested[k];
        }
        return out;
      });
      setPasteOpen(false);
    } catch (e: any) {
      Alert.alert("Preview failed", e?.message || "Try again.");
    } finally {
      setPreviewing(false);
    }
  };

  const saveMapping = async () => {
    setSaving(true);
    try {
      await Api.putWebhookMapping(draft);
      setCfg((c) => c ? { ...c, mapping: draft } : c);
      Alert.alert("Saved", "Webhook mapping is live.");
    } catch (e: any) {
      Alert.alert("Couldn't save", e?.message || "Try again.");
    } finally {
      setSaving(false);
    }
  };

  const setKeyMapping = (key: string, val: string) => {
    setDraft((d) => ({ ...d, [key]: val }));
  };

  // The keys we offer in the UI: from preview if present, else from
  // currently saved mapping.
  const keysShown = useMemo(() => {
    if (preview?.keys?.length) return preview.keys;
    return Object.keys(cfg?.mapping || {});
  }, [preview, cfg]);

  const labelForMapping = (val: string) => {
    if (!val) return "Ignore";
    if (val.startsWith("custom:")) {
      const cf = (cfg?.custom_fields || []).find((c) => `custom:${c.id}` === val);
      return cf?.label ? `★ ${cf.label}` : "Custom Field";
    }
    return FIELD_LABEL[val] || val;
  };

  // ────────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <Stack.Screen options={{ title: "Webhook" }} />
        <ActivityIndicator size="large" color="#FF6B00" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.root}>
      <Stack.Screen options={{ title: "Webhook" }} />
      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
      >
        <ScrollView
          contentContainerStyle={{ padding: 16, paddingBottom: 120 }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        >
          {/* ── Intro ── */}
          <View style={styles.introCard}>
            <View style={styles.introIcon}>
              <PhIcon name="cloud-upload" size={20} color="#FF6B00" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.introTitle}>Real-time Order Webhooks</Text>
              <Text style={styles.introSub}>
                Send POST requests with order JSON from Shopify, Zapier, custom scripts —
                and they'll land directly in your Pending Orders.
              </Text>
            </View>
          </View>

          {/* Phase F3 — Multi-webhook entry point. The legacy single-
              webhook editor below still works (and is auto-migrated to
              the v2 store on first load), but most users want one
              webhook per storefront / event-type, so we steer them
              into the new manager up front. */}
          <TouchableOpacity
            testID="link-manage-multi-webhooks"
            style={styles.multiBanner}
            onPress={() => router.push("/webhooks" as any)}
            activeOpacity={0.85}
          >
            <View style={styles.multiBannerIcon}>
              <Text style={{ fontSize: 22 }}>🔌</Text>
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.multiBannerTitle}>
                Manage multiple webhooks →
              </Text>
              <Text style={styles.multiBannerSub}>
                Create unlimited named webhooks per event type
                (New Order, Status Update, Abandoned Cart, Custom…).
              </Text>
            </View>
            <PhIcon name="chevron-right" size={18} color="#FF6B00" />
          </TouchableOpacity>

          {/* ── Webhook URL block ── */}
          <Text style={styles.sectionTitle}>Your Webhook URL</Text>
          {cfg?.configured ? (
            <View style={styles.urlCard}>
              {/* Phase F2.5 — friendly name pill + edit. */}
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <View style={{
                  flexDirection: "row", alignItems: "center", gap: 4,
                  backgroundColor: "#FFF7EE",
                  paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12,
                  borderWidth: 1, borderColor: "#FED7AA",
                }}>
                  <PhIcon name="tag" size={11} color="#FF6B00" />
                  <Text style={{ fontSize: 11, fontWeight: "700", color: "#FF6B00" }}>
                    {cfg.name || "WEBHOOK"}
                  </Text>
                </View>
                <TouchableOpacity onPress={openRenameName} style={{ paddingHorizontal: 6 }}>
                  <Text style={{ fontSize: 11, color: "#3B82F6", fontWeight: "600" }}>
                    {cfg.name ? "Rename" : "Add name"}
                  </Text>
                </TouchableOpacity>
              </View>
              <Text
                style={styles.urlText}
                numberOfLines={showRaw ? undefined : 2}
                selectable
              >
                {cfg.url}
              </Text>
              <View style={styles.urlActions}>
                <TouchableOpacity style={styles.urlBtn} onPress={copyUrl}>
                  <PhIcon name="copy" size={14} color="#0F172A" />
                  <Text style={styles.urlBtnTxt}>Copy</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.urlBtn} onPress={shareUrl}>
                  <PhIcon name="share" size={14} color="#0F172A" />
                  <Text style={styles.urlBtnTxt}>Share</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.urlBtn, { borderColor: "#FCA5A5" }]}
                  onPress={confirmRotate}
                  disabled={rotating}
                >
                  {rotating ? <ActivityIndicator size="small" color="#DC2626" /> : (
                    <>
                      <PhIcon name="arrow-clockwise" size={14} color="#DC2626" />
                      <Text style={[styles.urlBtnTxt, { color: "#DC2626" }]}>Rotate</Text>
                    </>
                  )}
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <View style={styles.urlCard}>
              <Text style={[styles.urlText, { color: "#94A3B8" }]}>
                No webhook URL yet. Generate one to start ingesting orders.
              </Text>
              <TouchableOpacity
                style={styles.generateBtn}
                onPress={openGenerateName}
                disabled={rotating}
              >
                {rotating ? <ActivityIndicator color="#fff" /> : (
                  <>
                    <PhIcon name="plus" size={16} color="#FFFFFF" />
                    <Text style={styles.generateBtnTxt}>Generate Webhook URL</Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          )}

          {/* ── Mapping editor ── */}
          {cfg?.configured ? (
            <>
              {/* Phase F2.4 — When the sender (e.g. Dukaan) has already
                  fired a "Test webhook" probe at us BEFORE the user
                  configured their mapping, we'll have stashed the raw
                  payload. Surface the most recent one as a one-tap
                  "Use this payload" shortcut so the mapping flow takes
                  10 seconds instead of asking the user to copy-paste. */}
              {(cfg.recent_samples || []).length > 0 && (
                <View style={styles.recentCard}>
                  <View style={{ flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 6 }}>
                    <PhIcon name="check-circle" size={14} color="#16A34A" />
                    <Text style={styles.recentTitle}>
                      ✓ Webhook test received! ({cfg.recent_samples!.length} sample{cfg.recent_samples!.length > 1 ? "s" : ""})
                    </Text>
                  </View>
                  <Text style={styles.recentSub}>
                    Your sender successfully connected. Tap below to auto-fill
                    the mapping editor with the most recent payload.
                  </Text>
                  <TouchableOpacity
                    style={styles.recentBtn}
                    onPress={async () => {
                      const last = cfg.recent_samples![cfg.recent_samples!.length - 1];
                      try {
                        const data = await Api.previewWebhookPayload(last.payload);
                        setPreview(data);
                        setDraft((d) => {
                          const out = { ...d };
                          for (const k of data.keys) {
                            if (!(k in out) && data.suggested[k]) out[k] = data.suggested[k];
                          }
                          return out;
                        });
                      } catch (e: any) {
                        Alert.alert("Couldn't load sample", e?.message || "Try again.");
                      }
                    }}
                  >
                    <PhIcon name="lightning" size={14} color="#FFFFFF" />
                    <Text style={styles.recentBtnTxt}>Use last received payload</Text>
                  </TouchableOpacity>
                </View>
              )}
              <View style={styles.sectionRow}>
                <Text style={styles.sectionTitle}>Field Mapping</Text>
                <TouchableOpacity
                  style={styles.pasteBtn}
                  onPress={() => setPasteOpen(true)}
                >
                  <PhIcon name="clipboard-text" size={14} color="#FF6B00" />
                  <Text style={styles.pasteBtnTxt}>Paste sample JSON</Text>
                </TouchableOpacity>
              </View>
              {keysShown.length === 0 ? (
                <View style={styles.emptyCard}>
                  <Text style={styles.emptyTxt}>
                    Paste a sample JSON payload from your sender (e.g. Shopify)
                    to discover available keys and map them to your shipment fields.
                  </Text>
                </View>
              ) : (
                keysShown.map((k) => {
                  const val = draft[k] || "";
                  return (
                    <TouchableOpacity
                      key={k}
                      style={styles.mapRow}
                      onPress={() => setPickerKey(k)}
                    >
                      <View style={{ flex: 1 }}>
                        <Text style={styles.mapKey} numberOfLines={1}>{k}</Text>
                        {preview?.sample_values?.[k] !== undefined && (
                          <Text style={styles.mapSample} numberOfLines={1}>
                            sample: {String(preview.sample_values[k]).slice(0, 60)}
                          </Text>
                        )}
                      </View>
                      <View style={[
                        styles.fieldPill,
                        !val && { borderColor: "#E5E7EB" },
                      ]}>
                        <Text style={[
                          styles.fieldPillTxt,
                          !val && { color: "#94A3B8" },
                          val.startsWith("custom:") && { color: "#7C3AED" },
                        ]}>
                          {labelForMapping(val)}
                        </Text>
                        <PhIcon name="chevron-down" size={14} color="#64748B" />
                      </View>
                    </TouchableOpacity>
                  );
                })
              )}
            </>
          ) : null}
        </ScrollView>

        {/* ── Save bar ── */}
        {cfg?.configured && keysShown.length > 0 && (
          <View style={styles.saveBar}>
            <TouchableOpacity
              style={[styles.saveBtn, saving && { opacity: 0.5 }]}
              onPress={saveMapping}
              disabled={saving}
            >
              {saving ? <ActivityIndicator color="#fff" /> : (
                <>
                  <PhIcon name="check-circle" size={18} color="#FFFFFF" />
                  <Text style={styles.saveBtnTxt}>Save Mapping</Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        )}
      </KeyboardAvoidingView>

      {/* ── Paste-JSON modal ── */}
      <Modal
        visible={pasteOpen}
        animationType="slide"
        transparent
        onRequestClose={() => setPasteOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Paste sample JSON</Text>
              <TouchableOpacity onPress={() => setPasteOpen(false)}>
                <PhIcon name="x" size={20} color="#0F172A" />
              </TouchableOpacity>
            </View>
            <Text style={styles.modalSub}>
              Send one real order payload from your sender. We'll show every key + an
              auto-suggested mapping.
            </Text>
            <TextInput
              value={pasteText}
              onChangeText={setPasteText}
              multiline
              autoCapitalize="none"
              autoCorrect={false}
              style={styles.jsonInput}
              placeholder="{ ... }"
              placeholderTextColor="#94A3B8"
            />
            <TouchableOpacity
              style={[styles.previewBtn, previewing && { opacity: 0.5 }]}
              onPress={previewPayload}
              disabled={previewing}
            >
              {previewing ? <ActivityIndicator color="#fff" /> : (
                <Text style={styles.previewBtnTxt}>Preview keys & mapping</Text>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* ── Field picker modal ── */}
      <Modal
        visible={!!pickerKey}
        animationType="slide"
        transparent
        onRequestClose={() => setPickerKey(null)}
      >
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { maxHeight: "80%" }]}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Map "{pickerKey}"</Text>
              <TouchableOpacity onPress={() => setPickerKey(null)}>
                <PhIcon name="x" size={20} color="#0F172A" />
              </TouchableOpacity>
            </View>
            <ScrollView style={{ maxHeight: 460 }}>
              <TouchableOpacity
                style={styles.fieldOption}
                onPress={() => { setKeyMapping(pickerKey!, ""); setPickerKey(null); }}
              >
                <Text style={[styles.fieldOptionTxt, { color: "#94A3B8" }]}>
                  (Ignore this key)
                </Text>
              </TouchableOpacity>
              {(cfg?.schema_fields || []).map((f) => (
                <TouchableOpacity
                  key={f}
                  style={styles.fieldOption}
                  onPress={() => { setKeyMapping(pickerKey!, f); setPickerKey(null); }}
                >
                  <Text style={styles.fieldOptionTxt}>{FIELD_LABEL[f] || f}</Text>
                  <Text style={styles.fieldOptionSub}>{f}</Text>
                </TouchableOpacity>
              ))}
              {(cfg?.custom_fields || []).length > 0 ? (
                <>
                  <View style={styles.customGroupHeader}>
                    <PhIcon name="star" size={12} color="#7C3AED" />
                    <Text style={styles.customGroupHeaderTxt}>Your Custom Fields</Text>
                  </View>
                  {(cfg?.custom_fields || []).map((cf) => {
                    const v = `custom:${cf.id}`;
                    return (
                      <TouchableOpacity
                        key={v}
                        style={styles.fieldOption}
                        onPress={() => { setKeyMapping(pickerKey!, v); setPickerKey(null); }}
                      >
                        <Text style={[styles.fieldOptionTxt, { color: "#7C3AED" }]}>
                          ★ {cf.label || cf.id}
                        </Text>
                        <Text style={styles.fieldOptionSub}>{v}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </>
              ) : null}
            </ScrollView>
          </View>
        </View>
      </Modal>

      {/* ── Name modal — Phase F2.5 ──────────────────────────── */}
      <Modal
        visible={nameOpen}
        animationType="fade"
        transparent
        onRequestClose={() => setNameOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <View style={[styles.modalCard, { maxHeight: 280 }]}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>
                {nameMode === "generate" ? "Name your webhook" : "Rename webhook"}
              </Text>
              <TouchableOpacity onPress={() => setNameOpen(false)}>
                <PhIcon name="x" size={20} color="#0F172A" />
              </TouchableOpacity>
            </View>
            <Text style={styles.modalSub}>
              Give it a friendly label so imported orders are tagged with
              the source store (e.g. "Shopify", "Dukaan", "Meesho", "Custom Site").
            </Text>
            <TextInput
              value={nameDraft}
              onChangeText={setNameDraft}
              placeholder="Shopify"
              placeholderTextColor="#94A3B8"
              autoFocus
              maxLength={32}
              style={{
                paddingHorizontal: 12, paddingVertical: 12, borderRadius: 10,
                borderWidth: 1, borderColor: "#E5E7EB", backgroundColor: "#F8FAFC",
                fontSize: 15, color: "#0F172A", marginBottom: 12,
              }}
            />
            <TouchableOpacity
              style={styles.previewBtn}
              onPress={submitName}
            >
              <Text style={styles.previewBtnTxt}>
                {nameMode === "generate" ? "Generate URL" : "Save Name"}
              </Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root:    { flex: 1, backgroundColor: "#F8FAFC" },
  center:  { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: "#F8FAFC" },

  introCard: {
    flexDirection: "row", gap: 12,
    backgroundColor: "#FFF7EE",
    padding: 14, borderRadius: 14, marginBottom: 16,
    borderWidth: 1, borderColor: "#FED7AA",
  },
  introIcon: {
    width: 36, height: 36, borderRadius: 10,
    backgroundColor: "#FFE4CC",
    alignItems: "center", justifyContent: "center",
  },
  introTitle: { fontSize: 14, fontWeight: "700", color: "#9A3412", marginBottom: 2 },
  introSub:   { fontSize: 12, color: "#9A3412", lineHeight: 16 },

  // Phase F3 — multi-webhook entry banner
  multiBanner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: "#fff",
    borderWidth: 2,
    borderColor: "#FF6B00",
    borderRadius: 14,
    padding: 14,
    marginBottom: 16,
  },
  multiBannerIcon: {
    width: 40, height: 40, borderRadius: 10,
    backgroundColor: "#FFF7ED",
    alignItems: "center", justifyContent: "center",
  },
  multiBannerTitle: { fontSize: 14, fontWeight: "800", color: "#9A3412", marginBottom: 2 },
  multiBannerSub:   { fontSize: 12, color: "#9A3412", lineHeight: 16 },

  sectionRow: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    marginTop: 18, marginBottom: 10,
  },
  sectionTitle: { fontSize: 13, fontWeight: "700", color: "#0F172A", textTransform: "uppercase", letterSpacing: 0.5 },

  urlCard: {
    backgroundColor: "#FFFFFF", padding: 14, borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  urlText: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }), color: "#0F172A", marginBottom: 12 },
  urlActions: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  urlBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8,
    borderWidth: 1, borderColor: "#E5E7EB", backgroundColor: "#F8FAFC",
  },
  urlBtnTxt: { fontSize: 12, fontWeight: "600", color: "#0F172A" },

  generateBtn: {
    flexDirection: "row", alignItems: "center", gap: 6,
    backgroundColor: "#FF6B00", paddingVertical: 12, borderRadius: 10, justifyContent: "center",
  },
  generateBtnTxt: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },

  pasteBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
    backgroundColor: "#FFF7EE", borderWidth: 1, borderColor: "#FED7AA",
  },
  pasteBtnTxt: { fontSize: 11, fontWeight: "700", color: "#FF6B00" },

  emptyCard: {
    backgroundColor: "#FFFFFF", padding: 16, borderRadius: 12,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  emptyTxt: { fontSize: 12, color: "#64748B", lineHeight: 18, textAlign: "center" },

  mapRow: {
    flexDirection: "row", alignItems: "center", gap: 12,
    backgroundColor: "#FFFFFF", padding: 12, borderRadius: 10, marginBottom: 8,
    borderWidth: 1, borderColor: "#E5E7EB",
  },
  mapKey:    { fontSize: 13, fontWeight: "600", color: "#0F172A", fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }) },
  mapSample: { fontSize: 11, color: "#94A3B8", marginTop: 2 },

  fieldPill: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 16,
    backgroundColor: "#F1F5F9", borderWidth: 1, borderColor: "#CBD5E1",
    maxWidth: 180,
  },
  fieldPillTxt: { fontSize: 11, fontWeight: "600", color: "#0F172A" },

  saveBar: {
    position: "absolute", left: 0, right: 0, bottom: 0,
    padding: 16,
    backgroundColor: "#FFFFFF",
    borderTopWidth: 1, borderTopColor: "#E5E7EB",
  },
  saveBtn: {
    flexDirection: "row", alignItems: "center", gap: 6, justifyContent: "center",
    backgroundColor: "#FF6B00", paddingVertical: 14, borderRadius: 12,
  },
  saveBtnTxt: { color: "#FFFFFF", fontWeight: "700", fontSize: 14 },

  modalBackdrop: { flex: 1, backgroundColor: "rgba(15,23,42,0.5)", justifyContent: "flex-end" },
  modalCard:     { backgroundColor: "#FFFFFF", borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 18, maxHeight: "92%" },
  modalHeader:   { flexDirection: "row", justifyContent: "space-between", alignItems: "center", marginBottom: 8 },
  modalTitle:    { fontSize: 16, fontWeight: "700", color: "#0F172A" },
  modalSub:      { fontSize: 12, color: "#64748B", marginBottom: 12 },

  jsonInput: {
    height: 200, padding: 12, borderRadius: 10,
    borderWidth: 1, borderColor: "#E5E7EB",
    backgroundColor: "#F8FAFC",
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace" }),
    fontSize: 12, color: "#0F172A", textAlignVertical: "top",
    marginBottom: 12,
  },
  previewBtn: {
    backgroundColor: "#FF6B00", paddingVertical: 12, borderRadius: 10, alignItems: "center",
  },
  previewBtnTxt: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },

  fieldOption:    { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: "#F1F5F9" },
  fieldOptionTxt: { fontSize: 14, fontWeight: "600", color: "#0F172A" },
  fieldOptionSub: { fontSize: 11, color: "#94A3B8", marginTop: 2 },

  customGroupHeader: { flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 14, paddingBottom: 6, marginTop: 4, borderTopWidth: 1, borderTopColor: "#E5E7EB" },
  customGroupHeaderTxt: { fontSize: 11, fontWeight: "700", color: "#7C3AED", textTransform: "uppercase", letterSpacing: 0.5 },

  // Phase F2.4 — "Use last received Dukaan payload" highlight card.
  recentCard: {
    backgroundColor: "#F0FDF4", borderColor: "#BBF7D0", borderWidth: 1,
    borderRadius: 12, padding: 14, marginTop: 16,
  },
  recentTitle: { fontSize: 13, fontWeight: "700", color: "#166534" },
  recentSub:   { fontSize: 12, color: "#166534", marginBottom: 10 },
  recentBtn: {
    flexDirection: "row", alignItems: "center", gap: 6, justifyContent: "center",
    backgroundColor: "#16A34A", paddingVertical: 10, borderRadius: 8,
  },
  recentBtnTxt: { color: "#FFFFFF", fontWeight: "700", fontSize: 13 },
});
