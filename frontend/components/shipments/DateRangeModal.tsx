/**
 * DateRangeModal — the "Custom Date Range" bottom-modal used by the
 * Shipments screen's date-filter chip. Two tap-to-open date fields
 * (From / To), a Clear + Apply button pair, and a native
 * DateTimePicker overlay on iOS/Android (web hides the picker since
 * @react-native-community/datetimepicker is not web-compatible).
 *
 * Extracted from `app/(tabs)/shipments.tsx` (Phase F4.6). Keeps
 * ~90 lines out of the hot shipments file. State is owned by the
 * parent — this component is a pure controlled Modal.
 */
import React from "react";
import {
  Modal,
  Platform,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import PhIcon from "../PhIcon";
import { colors } from "../../lib/theme";

type Props = {
  visible: boolean;
  onClose: () => void;
  from: Date | null;
  to:   Date | null;
  setFrom: (d: Date | null) => void;
  setTo:   (d: Date | null) => void;
  // Which field currently owns the native picker; null == closed.
  pickerField: "from" | "to" | null;
  setPickerField: (v: "from" | "to" | null) => void;
};

function fmt(d: Date | null): string {
  if (!d) return "Tap to pick date";
  try {
    return d.toLocaleDateString("en-GB", {
      day: "2-digit", month: "short", year: "numeric",
    });
  } catch {
    return String(d);
  }
}

export default function DateRangeModal({
  visible, onClose, from, to, setFrom, setTo,
  pickerField, setPickerField,
}: Props) {
  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={onClose}
    >
      <View style={styles.modalOverlay}>
        <View style={styles.dateModalCard}>
          <View style={styles.dateModalHdr}>
            <PhIcon name="calendar" size={18} color={colors.primary} />
            <Text style={styles.dateModalTitle}>Custom Date Range</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>
          <Text style={styles.dateHint}>
            Select From &amp; To dates to filter shipments.
          </Text>

          <TouchableOpacity
            testID="picker-from"
            style={styles.dateField}
            onPress={() => setPickerField("from")}
          >
            <Text style={styles.dateFieldLabel}>From</Text>
            <Text style={styles.dateFieldValue}>{fmt(from)}</Text>
          </TouchableOpacity>

          <TouchableOpacity
            testID="picker-to"
            style={styles.dateField}
            onPress={() => setPickerField("to")}
          >
            <Text style={styles.dateFieldLabel}>To</Text>
            <Text style={styles.dateFieldValue}>{fmt(to)}</Text>
          </TouchableOpacity>

          {pickerField && Platform.OS !== "web" && (
            <DateTimePicker
              value={(pickerField === "from" ? from : to) || new Date()}
              mode="date"
              display={Platform.OS === "ios" ? "inline" : "default"}
              maximumDate={new Date()}
              onChange={(event: any, selected?: Date) => {
                // On Android the native dialog dismisses itself.
                if (Platform.OS === "android") setPickerField(null);
                if (event?.type === "dismissed") return;
                if (!selected) return;
                if (pickerField === "from") setFrom(selected);
                else setTo(selected);
              }}
            />
          )}

          <View style={styles.dateModalActions}>
            <TouchableOpacity
              style={styles.dateClearBtn}
              onPress={() => {
                setFrom(null);
                setTo(null);
              }}
            >
              <Text style={styles.dateClearText}>Clear</Text>
            </TouchableOpacity>
            <TouchableOpacity
              testID="apply-date-range"
              style={styles.dateApplyBtn}
              onPress={() => {
                onClose();
                setPickerField(null);
              }}
            >
              <Text style={styles.dateApplyText}>Apply</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  modalOverlay: {
    flex: 1,
    backgroundColor: "rgba(0,0,0,0.45)",
    justifyContent: "center",
    alignItems: "center",
    padding: 16,
  },
  dateModalCard: {
    width: "100%",
    maxWidth: 400,
    backgroundColor: "#fff",
    borderRadius: 16,
    padding: 18,
  },
  dateModalHdr: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 4,
  },
  dateModalTitle: {
    flex: 1,
    fontSize: 17,
    fontWeight: "800",
    color: colors.text,
  },
  dateHint: {
    fontSize: 12,
    color: colors.textMuted,
    marginBottom: 12,
  },
  dateField: {
    borderWidth: 2,
    borderColor: "#E5E7EB",
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    marginBottom: 10,
  },
  dateFieldLabel: {
    fontSize: 11,
    fontWeight: "700",
    color: colors.textMuted,
    marginBottom: 2,
  },
  dateFieldValue: {
    fontSize: 15,
    fontWeight: "700",
    color: colors.text,
  },
  dateModalActions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 8,
  },
  dateClearBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: "#E5E7EB",
    alignItems: "center",
    backgroundColor: "#fff",
  },
  dateClearText: {
    color: colors.text,
    fontWeight: "700",
  },
  dateApplyBtn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: colors.primary,
    alignItems: "center",
  },
  dateApplyText: {
    color: "#fff",
    fontWeight: "800",
  },
});
