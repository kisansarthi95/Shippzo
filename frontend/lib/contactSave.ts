/**
 * Save-Contact helpers — Phase-16.
 *
 * Two paths:
 *   1. Single save → Android native Insert Intent (pre-fills the
 *      system contact screen; user taps SAVE themselves).
 *      iOS fallback: expo-contacts direct add (no native intent).
 *   2. Bulk save → write the VCF body to a cache file and fire the
 *      platform share sheet so the user can drop it into Contacts.
 */
import { Platform, Alert } from "react-native";
import * as IntentLauncher from "expo-intent-launcher";
// expo-file-system v19 dropped `EncodingType` from the root export —
// the legacy API (which still supports writeAsStringAsync + encoding
// flags) is now under /legacy. Using it keeps this file working on
// both SDK 52 (new) and older builds without a conditional fork.
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";

export type ContactPayload = {
  name:   string;
  phone:  string;
  postal: string;
  notes:  string;
  // Optional extras — reserved for future expansion.
  email?: string;
};

/**
 * Fire Android's INSERT intent so the system Contacts app opens
 * pre-filled. Falls back gracefully when the intent is unavailable
 * (old custom ROMs without a contacts provider).
 */
export async function openSaveContactIntent(c: ContactPayload): Promise<void> {
  if (Platform.OS === "android") {
    // `android.intent.action.INSERT` with `vnd.android.cursor.dir/raw_contact`
    // lets us pre-fill every key. Fields use the classic ContactsContract
    // intent-extra keys.
    const extra: Record<string, any> = {};
    if (c.name)   extra["name"]            = c.name;
    if (c.phone)  extra["phone"]           = c.phone;
    if (c.postal) extra["postal"]          = c.postal;
    if (c.notes)  extra["notes"]           = c.notes;
    try {
      await IntentLauncher.startActivityAsync(
        "android.intent.action.INSERT",
        {
          type:  "vnd.android.cursor.dir/raw_contact",
          extra,
        },
      );
      return;
    } catch (e) {
      // Some ROMs reject raw_contact; retry with the general contact
      // mime which is more widely supported.
      try {
        await IntentLauncher.startActivityAsync(
          "android.intent.action.INSERT",
          {
            type:  "vnd.android.cursor.dir/contact",
            extra,
          },
        );
        return;
      } catch (e2: any) {
        Alert.alert(
          "Save Contact failed",
          e2?.message || "Could not open the system contacts app.",
        );
        return;
      }
    }
  }

  // iOS: `ContactsContract` doesn't exist. Easiest portable fallback —
  // generate a single-entry VCF and hand it to the share sheet.
  try {
    const vcf = toVcf(c);
    const path = `${FileSystem.cacheDirectory || ""}contact_${Date.now()}.vcf`;
    await FileSystem.writeAsStringAsync(path, vcf, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(path, {
        mimeType: "text/vcard",
        dialogTitle: "Add to Contacts",
        UTI: "public.vcard",
      });
    } else {
      Alert.alert(
        "Save Contact",
        "Sharing isn't available on this device. The contact was prepared but could not be opened.",
      );
    }
  } catch (e: any) {
    Alert.alert("Save Contact failed", e?.message || "Try again.");
  }
}

function toVcf(c: ContactPayload): string {
  const esc = (v: string) =>
    (v || "")
      .replace(/\\/g, "\\\\")
      .replace(/\n/g, "\\n")
      .replace(/,/g, "\\,")
      .replace(/;/g, "\\;");
  const lines = [
    "BEGIN:VCARD",
    "VERSION:3.0",
    `FN:${esc(c.name)}`,
    `N:${esc(c.name)};;;;`,
  ];
  if (c.phone)  lines.push(`TEL;TYPE=CELL:${esc(c.phone)}`);
  if (c.postal) lines.push(`ADR;TYPE=HOME:;;${esc(c.postal)};;;;`);
  if (c.notes)  lines.push(`NOTE:${esc(c.notes)}`);
  lines.push("END:VCARD");
  return lines.join("\r\n");
}

/**
 * Bulk flow — given a pre-built VCF body (from /contacts/build-vcf),
 * write it to cache and fire the OS share sheet.
 */
export async function saveBulkVcf(vcfBody: string, filename: string = "contacts.vcf") {
  try {
    const path = `${FileSystem.cacheDirectory || ""}${filename}`;
    await FileSystem.writeAsStringAsync(path, vcfBody, {
      encoding: FileSystem.EncodingType.UTF8,
    });
    if (await Sharing.isAvailableAsync()) {
      await Sharing.shareAsync(path, {
        mimeType: "text/vcard",
        dialogTitle: "Export Contacts",
        UTI: "public.vcard",
      });
    } else {
      Alert.alert(
        "Export ready",
        `File saved at ${path}. Opening is not supported on this device.`,
      );
    }
  } catch (e: any) {
    Alert.alert("Export failed", e?.message || "Try again.");
  }
}
