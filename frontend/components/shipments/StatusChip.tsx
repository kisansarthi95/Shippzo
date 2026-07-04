/**
 * StatusChip — the little pill that shows a shipment's current stage
 * (Pending / Ready to Ship / Delivered / etc). Renders as a plain
 * badge when `onPress` is omitted; adds a caret + tap target when the
 * caller wants to open the stage-change picker.
 *
 * Extracted from `app/(tabs)/shipments.tsx` (Phase F4.5).
 */
import React from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import PhIcon from "../PhIcon";
import { colors } from "../../lib/theme";
import { STATUS_META } from "./status_meta";

type Props = {
  status: string;
  onPress?: () => void;
};

export default function StatusChip({ status, onPress }: Props) {
  // Walk STATUS_META to find a match (exact value OR one of its
  // aliases). Phase-12: prefer meta.label so legacy DB rows tagged
  // "Dispatch" still render as the user-facing "READY TO SHIP" badge.
  let bg = colors.warningBg;
  let fg = colors.warningText;
  let label = status || "Pending";
  for (const [, meta] of Object.entries(STATUS_META)) {
    if (
      meta.value === status ||
      (meta.aliases && meta.aliases.includes(status))
    ) {
      bg = meta.bg;
      fg = meta.fg;
      label = meta.label || meta.value;
      break;
    }
  }
  const content = (
    <View style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={[styles.chipText, { color: fg }]}>
        {label.toUpperCase()}
      </Text>
      {onPress && (
        <PhIcon
          name="chevron-down"
          size={11}
          color={fg}
          style={{ marginLeft: 2 }}
        />
      )}
    </View>
  );
  if (!onPress) return content;
  return (
    <TouchableOpacity
      onPress={onPress}
      activeOpacity={0.7}
      hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
    >
      {content}
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 4,
  },
  chipText: {
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 0.5,
  },
});
