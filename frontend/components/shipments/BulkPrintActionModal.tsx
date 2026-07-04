/**
 * BulkPrintActionModal — the little card that pops up when the user
 * has selected 1+ shipments and taps the "Print" pill in the
 * selection-mode toolbar. Offers two buttons:
 *   • Preview → opens the generated PDF in a system viewer
 *   • Print   → sends the job directly to the OS print dialog
 *
 * Extracted from `app/(tabs)/shipments.tsx` (Phase F4.6). Renders as
 * a plain controlled Modal — all state stays in the parent screen so
 * refresh/print flows keep working exactly as before.
 */
import React from "react";
import {
  Modal,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import PhIcon from "../PhIcon";
import { colors } from "../../lib/theme";

// Match the `bulkPerPage` union used by the parent screen.
type PerPage = 1 | 2 | 4 | "thermal" | "barcode";

type Props = {
  visible:      boolean;
  onClose:      () => void;
  selectedCount: number;
  bulkPerPage:  PerPage;
  onPreview:    () => void;
  onPrint:      () => void;
};

function perPageLabel(v: PerPage): string {
  switch (v) {
    case "thermal": return "Thermal 4×6";
    case "barcode": return "Thermal 2×1";
    case 1:         return "A4";
    case 2:         return "½A4";
    case 4:         return "A6";
    default:        return "Layout";
  }
}

export default function BulkPrintActionModal({
  visible, onClose, selectedCount, bulkPerPage, onPreview, onPrint,
}: Props) {
  return (
    <Modal
      visible={visible}
      animationType="fade"
      transparent
      onRequestClose={onClose}
    >
      <TouchableOpacity
        activeOpacity={1}
        style={styles.bulkPopupBackdrop}
        onPress={onClose}
      >
        <TouchableOpacity activeOpacity={1} style={styles.bulkPopupCard}>
          <View style={styles.bulkPopupHeaderRow}>
            <PhIcon name="print" size={18} color={colors.primary} />
            <Text style={styles.bulkPopupTitle}>
              {selectedCount} shipment{selectedCount !== 1 ? "s" : ""} •{" "}
              {perPageLabel(bulkPerPage)}
            </Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>
          <Text style={styles.bulkPopupSub}>
            Tap Preview to check the PDF before printing, or Print to send
            directly to your printer.
          </Text>
          <View style={styles.bulkPopupActions}>
            <TouchableOpacity
              testID="bulk-preview-btn"
              style={[styles.bulkPopupBtn, styles.bulkPopupBtnSecondary]}
              onPress={() => {
                onClose();
                onPreview();
              }}
            >
              <PhIcon name="eye-outline" size={20} color={colors.text} />
              <Text style={[styles.bulkPopupBtnText, { color: colors.text }]}>
                Preview
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="bulk-print-btn"
              style={[styles.bulkPopupBtn, { backgroundColor: colors.primary }]}
              onPress={() => {
                onClose();
                onPrint();
              }}
            >
              <PhIcon name="print" size={20} color="#fff" />
              <Text style={styles.bulkPopupBtnText}>Print</Text>
            </TouchableOpacity>
          </View>
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
}

const styles = StyleSheet.create({
  bulkPopupBackdrop: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: 24,
  },
  bulkPopupCard: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 18,
    elevation: 8,
  },
  bulkPopupHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 6,
  },
  bulkPopupTitle: {
    flex: 1,
    fontSize: 15,
    fontWeight: "800",
    color: colors.text,
  },
  bulkPopupSub: {
    fontSize: 12,
    color: "#6B7280",
    lineHeight: 17,
    marginBottom: 14,
  },
  bulkPopupActions: {
    flexDirection: "row",
    gap: 10,
  },
  bulkPopupBtn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    paddingVertical: 14,
    borderRadius: 12,
  },
  bulkPopupBtnSecondary: {
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  bulkPopupBtnText: {
    color: "#fff",
    fontWeight: "800",
    fontSize: 14,
    letterSpacing: 0.3,
  },
});
