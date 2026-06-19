/**
 * SearchBar — reusable search input with leading 🔍 icon and a
 * trailing one-tap clear (✕) button.
 *
 * Phase-32 (2026-06):
 *   • The ✕ button shows ONLY when there is text in the field.
 *   • Tapping ✕ clears the text, dismisses the keyboard, and
 *     invokes the optional `onClear` callback so the parent can
 *     reset filters / chip selection / list state.
 *   • Layout: 🔍 left · TextInput · ✕ right.
 *   • Visual: matches the existing `searchWrap` pattern used
 *     across Orders, Shipments, Customers — drop-in replacement
 *     for the inline View+PhIcon+TextInput trio.
 *
 * Why a shared component?  Every screen had a slightly different
 * style{ borderWidth, paddingVertical } combo. Pulling it into
 * one file makes the "one-tap clear" UX consistent everywhere AND
 * gives us a single place to add future affordances (voice input,
 * recent-searches dropdown, etc.).
 */
import React from "react";
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  Keyboard,
  Platform,
  ViewStyle,
  StyleProp,
  TextInputProps,
} from "react-native";
import PhIcon from "./PhIcon";
import { colors } from "../lib/theme";

export interface SearchBarProps
  extends Omit<TextInputProps, "value" | "onChangeText" | "style"> {
  /** Current search text (controlled). */
  value: string;
  /** Text-change handler. */
  onChangeText: (next: string) => void;
  /**
   * Called AFTER the ✕ button is tapped and the text is cleared.
   * Use this to reset filters / chip selection / list state.
   */
  onClear?: () => void;
  /** Placeholder text. */
  placeholder?: string;
  /** Custom outer container style. Merged on top of defaults. */
  containerStyle?: StyleProp<ViewStyle>;
  /** Test ID prefix; defaults to "searchbar". */
  testID?: string;
}

const SearchBar: React.FC<SearchBarProps> = ({
  value,
  onChangeText,
  onClear,
  placeholder = "Search…",
  containerStyle,
  testID = "searchbar",
  ...rest
}) => {
  const hasText = (value ?? "").length > 0;

  // Tapping ✕: wipe the field, dismiss the keyboard, then let the
  // parent reset whatever filter / chip / list state it owns.
  // Order matters — clear value FIRST so the controlled re-render
  // hides the button before onClear() triggers any heavy refetch.
  const handleClear = () => {
    onChangeText("");
    Keyboard.dismiss();
    onClear?.();
  };

  return (
    <View style={[styles.wrap, containerStyle]} testID={`${testID}-wrap`}>
      <PhIcon name="search" size={18} color={colors.textMuted} />
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor="#9CA3AF"
        style={styles.input}
        // Don't let iOS auto-capitalize / auto-correct mangle phone /
        // order-ID searches — that bug used to add spaces mid-query.
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="search"
        testID={`${testID}-input`}
        {...rest}
      />
      {hasText ? (
        <TouchableOpacity
          onPress={handleClear}
          // Wide 44pt tap target around the small ✕ glyph so older
          // users / Android phones with imprecise touch can still
          // hit it reliably.
          hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
          style={styles.clearBtn}
          accessibilityRole="button"
          accessibilityLabel="Clear search"
          testID={`${testID}-clear`}
        >
          <PhIcon
            name="close-circle"
            size={18}
            color={colors.textMuted}
          />
        </TouchableOpacity>
      ) : null}
    </View>
  );
};

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "ios" ? 10 : 8,
    marginHorizontal: 12,
    marginTop: 8,
    borderRadius: 12,
    backgroundColor: "#fff",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  input: {
    flex: 1,
    color: colors.text,
    fontSize: 15,
    paddingVertical: 0, // tame Android's extra TextInput line-height padding
  },
  clearBtn: {
    width: 22,
    height: 22,
    alignItems: "center",
    justifyContent: "center",
  },
});

export default SearchBar;
