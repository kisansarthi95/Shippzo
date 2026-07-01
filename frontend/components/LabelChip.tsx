/**
 * LabelChip — pill-shaped chip that renders one ShipmentLabel with:
 *   • coloured icon on the left
 *   • label name
 *   • optional (×) button on the right for removal
 *
 * Used in the shipment card row and (without the ×) inside the
 * Label Select bottom-sheet.
 */
import React from "react";
import { View, Text, TouchableOpacity, StyleSheet } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { ShipmentLabel, LABEL_ICON_MAP } from "../lib/labels";

type Props = {
  label: ShipmentLabel;
  onRemove?: () => void;
  /** Show a smaller chip when rendered inside a dense list. */
  size?: "sm" | "md";
};

function resolveIcon(icon: string): string {
  return LABEL_ICON_MAP[icon] || icon || "pricetag";
}

/** Simple lightening — appends "1A" (≈10% alpha) to a #RRGGBB. */
function tintBg(hex: string): string {
  if (!hex || hex.length !== 7 || !hex.startsWith("#")) return "#F1F5F9";
  return `${hex}1A`;
}

const LabelChip = React.memo(function LabelChip({ label, onRemove, size = "md" }: Props) {
  const iconName = resolveIcon(label.icon);
  const bg = tintBg(label.color);
  const isSm = size === "sm";
  return (
    <View
      style={[
        styles.chip,
        {
          backgroundColor: bg,
          borderColor: label.color,
          paddingVertical: isSm ? 3 : 5,
          paddingHorizontal: isSm ? 8 : 10,
        },
      ]}
    >
      <Ionicons name={iconName as any} size={isSm ? 12 : 14} color={label.color} />
      <Text
        style={[
          styles.txt,
          {
            color: label.color,
            fontSize: isSm ? 11 : 12,
          },
        ]}
        numberOfLines={1}
      >
        {label.name}
      </Text>
      {onRemove ? (
        <TouchableOpacity
          onPress={onRemove}
          hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          accessibilityLabel={`Remove ${label.name}`}
          style={{ marginLeft: 2 }}
        >
          <Ionicons name="close" size={isSm ? 12 : 14} color={label.color} />
        </TouchableOpacity>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: 999,
    borderWidth: 1,
    marginRight: 6,
    alignSelf: "flex-start",
  },
  txt: {
    fontWeight: "700",
    letterSpacing: 0.2,
    maxWidth: 140,
  },
});

export default LabelChip;
