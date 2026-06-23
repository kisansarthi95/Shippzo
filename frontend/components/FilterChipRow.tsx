/**
 * FilterChipRow — Phase F3.9 canonical horizontal filter-chip row.
 *
 * Why this exists
 * ---------------
 * 12+ screens shipped their own copies of the filter-chip pattern.
 * Eight of them missed the Android-large-font fix (Phase F3.9): chips
 * got clipped at ≥1.3× system font scale because the row lacked
 * `paddingVertical: 4`, `alignItems: 'center'`, the chip lacked
 * `minHeight: 40` / `paddingVertical: 10`, and the chip text lacked
 * `lineHeight: 18` / `includeFontPadding: false`. Consolidating the
 * pattern into one component guarantees:
 *
 *   • No screen can drift again.
 *   • Future tweaks (e.g. animated underline) ship everywhere at once.
 *   • Accessibility / 1.3× font / RTL work happens in ONE file.
 *
 * Usage
 * -----
 * Simple text + optional count badge:
 *
 *   <FilterChipRow
 *     testIDPrefix="src-filter"
 *     selected={sourceFilter}
 *     onSelect={setSourceFilter}
 *     items={[
 *       { key: "all",     label: "All",      count: 124 },
 *       { key: "paste",   label: "Paste",    count: 42  },
 *       { key: "csv",     label: "CSV",      count: 18  },
 *     ]}
 *   />
 *
 * For chips with icons or custom right-content, pass `renderChip`:
 *
 *   <FilterChipRow
 *     items={statuses}
 *     selected={status}
 *     onSelect={setStatus}
 *     renderChip={(item, sel) => (
 *       <>
 *         <PhIcon name={item.icon} size={14} color={sel ? "#fff" : "#475569"} />
 *         <Text style={[chipTxt, sel && chipTxtSel]}>{item.label}</Text>
 *       </>
 *     )}
 *   />
 */
import React, { ReactNode } from "react";
import {
  ScrollView,
  TouchableOpacity,
  Text,
  StyleSheet,
  ViewStyle,
  StyleProp,
  TextStyle,
} from "react-native";
import { colors } from "../lib/theme";

export interface FilterChipItem {
  /** Stable identifier used for selection comparison + auto testID. */
  key: string;
  /** Display label. */
  label: string;
  /** Optional count badge — rendered as ` · 12` suffix. */
  count?: number;
  /**
   * Optional per-chip selected-color override. Defaults to
   * theme.colors.primary. Useful for a "danger" filter that should
   * tint red, etc.
   */
  selectedColor?: string;
  /** Optional per-chip selected background tint. */
  selectedBg?: string;
  /** Optional override testID — defaults to `${testIDPrefix}-${key}`. */
  testID?: string;
  /** Free-form payload for renderChip implementers. */
  meta?: any;
}

export interface FilterChipRowProps<T extends string = string> {
  items: FilterChipItem[];
  selected: T;
  onSelect: (next: T) => void;
  /** Prefix for auto-generated testIDs. Default: "chip". */
  testIDPrefix?: string;
  /** Optional outer ScrollView style (e.g. marginBottom). */
  style?: StyleProp<ViewStyle>;
  /** Optional chip style overrides. */
  chipStyle?: StyleProp<ViewStyle>;
  chipTextStyle?: StyleProp<TextStyle>;
  /**
   * Optional renderer for chip children. Receives the item + selected
   * flag, returns ReactNode. When omitted we render label + count.
   */
  renderChip?: (item: FilterChipItem, selected: boolean) => ReactNode;
  /**
   * Should the chip be tappable disabled-style? Rare, but useful when
   * the row is read-only during a refresh.
   */
  disabled?: boolean;
}

function FilterChipRowInner<T extends string = string>({
  items,
  selected,
  onSelect,
  testIDPrefix = "chip",
  style,
  chipStyle,
  chipTextStyle,
  renderChip,
  disabled,
}: FilterChipRowProps<T>) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={style}
      // Phase F3.9 — paddingVertical guarantees the chip body isn't
      // clipped at >=1.3× system font scale; alignItems centres
      // narrow chips against the row baseline; gap + paddingRight
      // give breathing room without trailing whitespace.
      contentContainerStyle={{
        gap: 8,
        paddingRight: 8,
        paddingVertical: 4,
        alignItems: "center",
      }}
    >
      {items.map((item) => {
        const sel = item.key === selected;
        const selBg = sel
          ? (item.selectedBg || "#FFF7ED")
          : "#fff";
        const selBorder = sel
          ? (item.selectedColor || colors.primary)
          : "#E5E7EB";
        return (
          <TouchableOpacity
            key={item.key || "_empty"}
            testID={item.testID || `${testIDPrefix}-${item.key || "all"}`}
            style={[
              styles.chip,
              { backgroundColor: selBg, borderColor: selBorder },
              chipStyle,
            ]}
            onPress={() => !disabled && onSelect(item.key as T)}
            activeOpacity={disabled ? 1 : 0.75}
            disabled={disabled}
            accessibilityRole="button"
            accessibilityState={{ selected: sel, disabled: !!disabled }}
            accessibilityLabel={item.label}
          >
            {renderChip ? (
              renderChip(item, sel)
            ) : (
              <Text
                style={[
                  styles.chipTxt,
                  sel && {
                    color: item.selectedColor || colors.primary,
                  },
                  chipTextStyle,
                ]}
                numberOfLines={1}
              >
                {item.label}
                {typeof item.count === "number" ? ` · ${item.count}` : ""}
              </Text>
            )}
          </TouchableOpacity>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  chip: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    // Phase F3.9 — min-height keeps short labels at the same visual
    // height as long ones, AND prevents Android from clipping the
    // chip body at large system font scales.
    minHeight: 40,
    justifyContent: "center",
    flexShrink: 0,
    borderRadius: 999,
    borderWidth: 1.5,
  },
  chipTxt: {
    fontSize: 12,
    fontWeight: "700",
    color: "#475569",
    // Phase F3.9 — explicit lineHeight + includeFontPadding=false
    // matches the chip's minHeight calculation so glyphs sit flush
    // centre instead of riding into the top border.
    lineHeight: 18,
    includeFontPadding: false,
  },
});

const FilterChipRow = React.memo(FilterChipRowInner) as typeof FilterChipRowInner;
export default FilterChipRow;
