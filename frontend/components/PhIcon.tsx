/**
 * <PhIcon /> — Ionicons-compatible shim built on phosphor-react-native.
 *
 * WHY THIS EXISTS
 * ────────────────
 * The Expo Go preview environment intermittently fails to load
 * @expo/vector-icons font-based glyphs on Android, leaving users
 * staring at blank squares. Phosphor renders SVG so it sidesteps the
 * font-loading pipeline entirely.
 *
 * Migrating 427 usages across 57 files to per-icon phosphor imports
 * (e.g. `<HouseIcon />`) would be a massive churn AND break our 25+
 * dynamic usages (`<Ionicons name={iconStr} />` where the name is a
 * runtime string from a config object). This shim lets us:
 *   1. Keep the existing `name="kebab-case"` API exactly as-is.
 *   2. Get SVG icons that Just Work in any Expo Go env.
 *   3. Migrate with a single global find-replace
 *      `<Ionicons` → `<PhIcon`.
 *
 * USAGE (drop-in, identical to <Ionicons /> from @expo/vector-icons):
 *   import PhIcon from '@/components/PhIcon';
 *   <PhIcon name="home-outline"      size={24} color="#000" />
 *   <PhIcon name="checkmark-circle"  size={20} color="#10B981" />
 *
 * SEMANTICS
 *   • Names ending in `-outline` map to weight="regular".
 *   • All other names map to weight="fill".
 *   • An explicit `weight` prop always wins.
 *
 * UNKNOWN NAMES
 *   Falls back to <Question /> with a console.warn the first time
 *   that name is hit. Add the missing entry to the map below.
 */
import React, { useMemo } from "react";
import * as Ph from "phosphor-react-native";
import type { IconWeight } from "phosphor-react-native";

// ─────────────────────────────────────────────────────────────────────
// Ionicons-name → Phosphor-component map.
// ONLY the BASE name (no -outline suffix) goes here; the wrapper
// strips -outline before lookup.
// Picked using https://phosphoricons.com/ — closest semantic match.
// ─────────────────────────────────────────────────────────────────────
const ICON_MAP: Record<string, React.ComponentType<any>> = {
  // Actions / CRUD
  add:                     Ph.Plus,
  "add-circle":            Ph.PlusCircle,
  remove:                  Ph.Minus,
  close:                   Ph.X,
  "close-circle":          Ph.XCircle,
  copy:                    Ph.Copy,
  "create":                Ph.PencilSimple,
  trash:                   Ph.Trash,
  save:                    Ph.FloppyDisk,
  send:                    Ph.PaperPlaneRight,
  share:                   Ph.Share,
  "share-social":          Ph.ShareNetwork,
  download:                Ph.DownloadSimple,
  print:                   Ph.Printer,
  refresh:                 Ph.ArrowsClockwise,
  "refresh-circle":        Ph.ArrowClockwise,
  sync:                    Ph.ArrowsClockwise,
  scan:                    Ph.Scan,
  scanner:                 Ph.Scan,
  search:                  Ph.MagnifyingGlass,
  options:                 Ph.SlidersHorizontal,
  settings:                Ph.Gear,
  "settings-sharp":        Ph.Gear,
  "log-out":               Ph.SignOut,
  login:                   Ph.SignIn,
  signup:                  Ph.UserPlus,

  // Status / feedback
  checkmark:               Ph.Check,
  "checkmark-circle":      Ph.CheckCircle,
  "checkmark-done":        Ph.Checks,
  "alert-circle":          Ph.WarningCircle,
  "information-circle":    Ph.Info,
  warning:                 Ph.Warning,
  "shield-checkmark":      Ph.ShieldCheck,
  flame:                   Ph.Flame,
  flash:                   Ph.Lightning,
  bulb:                    Ph.Lightbulb,
  sparkles:                Ph.Sparkle,
  star:                    Ph.Star,
  rocket:                  Ph.Rocket,

  // Navigation arrows
  "arrow-back":            Ph.ArrowLeft,
  "arrow-forward":         Ph.ArrowRight,
  "arrow-up-circle":       Ph.ArrowCircleUp,
  "chevron-back":          Ph.CaretLeft,
  "chevron-down":          Ph.CaretDown,
  "chevron-up":            Ph.CaretUp,
  "chevron-forward":       Ph.CaretRight,
  "open":                  Ph.ArrowSquareOut,

  // Layout / structure
  list:                    Ph.List,
  grid:                    Ph.GridFour,
  layers:                  Ph.Stack,
  "square":                Ph.Square,
  "ellipse":               Ph.Circle,
  cube:                    Ph.Cube,
  viewport:                Ph.SquaresFour,

  // Communication
  mail:                    Ph.Envelope,
  "chatbubble-ellipses":   Ph.ChatCircleDots,
  "paper-plane":           Ph.PaperPlaneRight,
  "logo-google":           Ph.GoogleLogo,
  "logo-whatsapp":         Ph.WhatsappLogo,
  "notifications":         Ph.Bell,

  // Identity / people
  person:                  Ph.User,
  "person-circle":         Ph.UserCircle,
  "person-add":            Ph.UserPlus,
  people:                  Ph.Users,
  "finger-print":          Ph.Fingerprint,
  "lock-closed":           Ph.Lock,
  "key":                   Ph.Key,
  "eye":                   Ph.Eye,

  // Commerce / money
  card:                    Ph.CreditCard,
  cart:                    Ph.ShoppingCart,
  cash:                    Ph.CurrencyInr,           // ₹ context
  wallet:                  Ph.Wallet,
  pricetag:                Ph.Tag,
  pricetags:               Ph.Tag,
  gift:                    Ph.Gift,
  receipt:                 Ph.Receipt,

  // Domain (shipments / business)
  storefront:              Ph.Storefront,
  shipments:               Ph.Package,
  orders:                  Ph.ListChecks,
  plans:                   Ph.Crown,
  barcode:                 Ph.Barcode,
  "document":              Ph.FileText,
  "document-text":         Ph.FileText,
  clipboard:               Ph.Clipboard,
  "stats-chart":           Ph.ChartBar,
  "bar-chart":             Ph.ChartBar,

  // Cloud / data
  "cloud-done":            Ph.CloudCheck,
  "cloud-download":        Ph.CloudArrowDown,
  "cloud-upload":          Ph.CloudArrowUp,
  "cloud-offline":         Ph.CloudSlash,

  // Time / misc
  calendar:                Ph.Calendar,
  "time":                  Ph.Clock,
  camera:                  Ph.Camera,
  image:                   Ph.Image,
  globe:                   Ph.Globe,
  language:                Ph.Translate,
  text:                    Ph.TextAa,
  home:                    Ph.House,
  keypad:                  Ph.Keypad,
  "hardware-chip":         Ph.Cpu,
  play:                    Ph.Play,
  stop:                    Ph.Stop,
  repeat:                  Ph.Repeat,
  index:                   Ph.House,                 // tab fallback

  // Phase 5e-2 — additions discovered via prop-based icon usages
  // (e.g. <StatCard icon="trending-up" />). These are passed as the
  // `name` value through the same shim, so missing entries previously
  // rendered as the Question fallback (the "?" tiles users reported).
  "trending-up":           Ph.TrendUp,
  business:                Ph.Briefcase,
  car:                     Ph.Truck,                 // courier context
  chatbubbles:             Ph.ChatsCircle,
  enter:                   Ph.SignIn,
  "help-buoy":             Ph.LifeBuoy,              // help/support
  location:                Ph.MapPin,
  "log-in":                Ph.SignIn,                // alias for login
  "phone-portrait":        Ph.DeviceMobile,

  // Phase 5e-3 — runtime-discovered (caught from the [PhIcon]
  // unmapped-icon console warnings on real device traffic).
  "checkbox":              Ph.CheckSquare,           // ticked checkbox
  "checkmark-done-circle": Ph.SealCheck,             // success badge
  happy:                   Ph.Smiley,                // emoji-style mood
};

// Aliases for less obvious mappings — the kebab-case key is the
// Ionicons name we may have stripped "-outline" from.
const ALIASES: Record<string, string> = {
  "create":      "create",     // PencilSimple
  "ellipse":     "ellipse",    // Circle
  "open":        "open",       // ArrowSquareOut
};

const _missingWarned = new Set<string>();

export interface PhIconProps {
  name: string;
  size?: number;
  color?: string;
  weight?: IconWeight;
  style?: any;
}

export default function PhIcon({
  name,
  size = 24,
  color,
  weight,
  style,
}: PhIconProps) {
  const { Component, resolvedWeight } = useMemo(() => {
    const raw = String(name || "").trim();

    // -sharp / -outline suffix handling
    let base = raw;
    let inferredWeight: IconWeight = "fill";
    if (base.endsWith("-outline")) {
      base = base.slice(0, -"-outline".length);
      inferredWeight = "regular";
    } else if (base.endsWith("-sharp")) {
      base = base.slice(0, -"-sharp".length);
      inferredWeight = "fill";
    }

    const aliased = ALIASES[base] ?? base;
    const Comp = ICON_MAP[aliased];
    if (!Comp) {
      if (!_missingWarned.has(raw)) {
        _missingWarned.add(raw);
        if (__DEV__) {
          // eslint-disable-next-line no-console
          console.warn(
            `[PhIcon] unmapped icon name "${raw}" — falling back to Question.`,
          );
        }
      }
      return { Component: Ph.Question, resolvedWeight: inferredWeight };
    }
    return { Component: Comp, resolvedWeight: inferredWeight };
  }, [name]);

  return (
    <Component
      size={size}
      color={color}
      weight={weight ?? resolvedWeight}
      style={style}
    />
  );
}
