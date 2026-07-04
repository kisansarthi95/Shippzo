/**
 * ActionBtn — the 36×36 icon pill used inside the shipment card's
 * bottom action row (Copy, WhatsApp, Edit, Delete, etc.).
 *
 * Extracted from `app/(tabs)/shipments.tsx` (Phase F4.5) so the same
 * button style/behavior can be reused by any other card layout
 * without duplicating the styling.
 */
import React from "react";
import { StyleSheet, TouchableOpacity } from "react-native";
import PhIcon from "../PhIcon";

type Props = {
  icon: string;
  color: string;
  onPress: () => void;
  testID?: string;
  // Phase F4.8 — Optional filled-background variant for state
  // indicators (e.g., Contact Saved). Falls back to the standard
  // outline pill when omitted so every existing call-site keeps
  // its exact appearance.
  bg?: string;
  iconColor?: string;
};

export default function ActionBtn({
  icon, color, onPress, testID, bg, iconColor,
}: Props) {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      style={[
        styles.actionBtn,
        bg ? { backgroundColor: bg, borderColor: bg } : null,
      ]}
    >
      <PhIcon name={icon} size={18} color={iconColor || color} />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  actionBtn: {
    width: 36,
    height: 36,
    borderRadius: 8,
    backgroundColor: "#F9FAFB",
    borderWidth: 1,
    borderColor: "#E5E7EB",
    justifyContent: "center",
    alignItems: "center",
  },
});
