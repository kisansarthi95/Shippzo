/**
 * QuickPickerSheet — small reusable bottom-sheet Modal that shows a
 * scrollable list of options with a check mark next to the current
 * value. Used by the 3 Quick Filter dropdown chips (All Print /
 * All Labels / Courier Partner) on the Shipments screen.
 */
import React from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, Modal, ScrollView, Pressable,
} from "react-native";
import PhIcon from "./PhIcon";
import { colors } from "../lib/theme";

export type QuickPickerOption = {
  id: string;
  label: string;
  icon?: string;
  color?: string;
};

type Props = {
  visible: boolean;
  title: string;
  options: QuickPickerOption[];
  value: string;
  onChange: (id: string) => void;
  onClose: () => void;
  // Optional footer button (e.g., "+ Create Label")
  footer?: React.ReactNode;
};

export default function QuickPickerSheet({
  visible, title, options, value, onChange, onClose, footer,
}: Props) {
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.root}>
        {/* Backdrop is absolutely positioned BEHIND the sheet so taps
            on options don't get swallowed by it. Only the visible
            un-covered area (above the sheet) dismisses the modal. */}
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.header}>
            <Text style={styles.title}>{title}</Text>
            <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
              <PhIcon name="close" size={22} color={colors.text} />
            </TouchableOpacity>
          </View>
          <ScrollView style={{ maxHeight: 440 }}>
            {options.map((o) => {
              const active = value === o.id;
              const tint = o.color || colors.primary;
              return (
                <TouchableOpacity
                  key={o.id}
                  onPress={() => { onChange(o.id); onClose(); }}
                  style={[styles.row, active && { backgroundColor: `${tint}12` }]}
                  activeOpacity={0.7}
                >
                  {o.icon ? (
                    <PhIcon name={o.icon as any} size={16} color={tint} />
                  ) : (
                    <View style={{ width: 16 }} />
                  )}
                  <Text style={styles.rowTxt} numberOfLines={1}>{o.label}</Text>
                  {active ? (
                    <PhIcon name="checkmark" size={18} color={tint} />
                  ) : (
                    <View style={{ width: 18 }} />
                  )}
                </TouchableOpacity>
              );
            })}
            {footer ? <View style={styles.footerWrap}>{footer}</View> : null}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: "flex-end" },
  backdrop: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.35)",
  },
  sheet: {
    backgroundColor: "#fff",
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 20,
  },
  header: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingVertical: 8, marginBottom: 6,
  },
  title: { fontSize: 16, fontWeight: "800", color: colors.text },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingVertical: 12, paddingHorizontal: 8, borderRadius: 8,
  },
  rowTxt: { flex: 1, fontSize: 14, color: colors.text, fontWeight: "600" },
  footerWrap: { borderTopWidth: 1, borderTopColor: "#F1F5F9", marginTop: 6, paddingTop: 6 },
});
