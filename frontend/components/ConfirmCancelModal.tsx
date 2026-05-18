/**
 * ConfirmCancelModal — Phase-33
 * ------------------------------
 * One reusable modal that powers EVERY destructive "this turns the
 * order into a dead state" action across Shipments and Pending
 * Orders. Lives separate from generic confirm dialogs because the
 * messaging here is intentionally heavy ("cannot be processed
 * anymore" / no undo) and the button shape is locked to the spec
 * the product owner approved.
 *
 * Usage:
 *   const [open, setOpen] = useState<null | TerminalAction>(null);
 *   <ConfirmCancelModal
 *     action={open}                    // null hides the modal
 *     onClose={() => setOpen(null)}
 *     onConfirm={async () => {         // your async write
 *       await Api.updateShipment(id, { status: open!.targetStatus });
 *       setOpen(null);
 *     }}
 *   />
 *
 * Props are intentionally narrow — keep this dumb / presentation
 * only. Callers compose the API call (PUT shipments, DELETE pending,
 * etc.) outside so the modal stays reusable.
 */
import React from "react";
import {
  Modal,
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ActivityIndicator,
  Platform,
} from "react-native";
import PhIcon from "./PhIcon";

export type TerminalActionKind =
  | "cancel"          // generic cancel — sets status=Cancelled
  | "cancel_by_buyer" // marketplace-side cancel
  | "returned"        // parcel returned to origin
  | "delete";         // tick / X icon → also routes to Cancelled

export type TerminalAction = {
  kind: TerminalActionKind;
  /** Human-readable label of the order (order id / customer name) shown
   *  in the modal so the operator can sanity-check they're cancelling
   *  the RIGHT card before they confirm. */
  orderLabel?: string;
};

/** Returns the title + body copy for the given action. Kept here so
 *  every call-site stays consistent — change once, apply everywhere. */
function copyFor(kind: TerminalActionKind): { title: string; body: string; confirmLabel: string } {
  switch (kind) {
    case "cancel_by_buyer":
      return {
        title: "Mark as Cancelled by Buyer",
        body: "Once you mark this order as cancelled by the buyer, it cannot be processed or shipped anymore. This action is permanent.",
        confirmLabel: "Yes, Confirm",
      };
    case "returned":
      return {
        title: "Mark as Returned",
        body: "Once you mark this order as Returned, it becomes permanently locked. No further status changes, re-shipping, or label re-prints will be allowed.",
        confirmLabel: "Yes, Confirm",
      };
    case "delete":
      return {
        title: "Cancel Order",
        body: "Once you cancel this order, it cannot be processed anymore. The card stays for history but no further actions are allowed.",
        confirmLabel: "Yes, Cancel",
      };
    case "cancel":
    default:
      return {
        title: "Cancel Order",
        body: "Once you cancel this order, it cannot be processed anymore. This action is permanent.",
        confirmLabel: "Yes, Confirm",
      };
  }
}

type Props = {
  action: TerminalAction | null;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
  /** Optional loading flag — when true the confirm button shows a
   *  spinner and both buttons disable so the user can't double-tap. */
  loading?: boolean;
};

export default function ConfirmCancelModal({
  action,
  onClose,
  onConfirm,
  loading = false,
}: Props) {
  const visible = !!action;
  const meta = action ? copyFor(action.kind) : null;

  return (
    <Modal
      visible={visible}
      transparent
      animationType="fade"
      onRequestClose={loading ? undefined : onClose}
    >
      <View style={styles.backdrop}>
        <View style={styles.card}>
          {/* Close (X) — top-right. Disabled while loading. */}
          <TouchableOpacity
            style={styles.closeBtn}
            onPress={onClose}
            disabled={loading}
            accessibilityLabel="Close confirmation"
          >
            <PhIcon name="close" size={18} color={loading ? "#9CA3AF" : "#374151"} />
          </TouchableOpacity>

          {/* Icon + title */}
          <View style={styles.headBlock}>
            <View style={styles.iconCircle}>
              <PhIcon name="alert-circle-outline" size={26} color="#B45309" />
            </View>
            <Text style={styles.title}>{meta?.title ?? "Cancel Order"}</Text>
            {action?.orderLabel ? (
              <Text style={styles.orderLabel} numberOfLines={1}>
                {action.orderLabel}
              </Text>
            ) : null}
          </View>

          <Text style={styles.body}>{meta?.body ?? ""}</Text>

          <View style={styles.actionsRow}>
            <TouchableOpacity
              style={[styles.btn, styles.btnGhost]}
              onPress={onClose}
              disabled={loading}
            >
              <Text style={styles.btnGhostText}>Close</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.btn, styles.btnDanger, loading && { opacity: 0.7 }]}
              onPress={() => onConfirm()}
              disabled={loading}
            >
              {loading ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <>
                  <PhIcon name="checkmark" size={16} color="#fff" />
                  <Text style={styles.btnDangerText}>
                    {meta?.confirmLabel ?? "Yes, Confirm"}
                  </Text>
                </>
              )}
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: "rgba(15,23,42,0.55)",
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  card: {
    width: "100%",
    maxWidth: 380,
    backgroundColor: "#fff",
    borderRadius: 18,
    padding: 22,
    paddingTop: 26,
    ...Platform.select({
      android: { elevation: 6 },
      default: {
        shadowColor: "#0F172A",
        shadowOpacity: 0.18,
        shadowRadius: 22,
        shadowOffset: { width: 0, height: 10 },
      },
    }),
  },
  closeBtn: {
    position: "absolute",
    top: 10,
    right: 10,
    width: 32,
    height: 32,
    borderRadius: 16,
    alignItems: "center",
    justifyContent: "center",
  },
  headBlock: {
    alignItems: "center",
    marginBottom: 14,
  },
  iconCircle: {
    width: 54,
    height: 54,
    borderRadius: 27,
    backgroundColor: "#FEF3C7",
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 10,
  },
  title: {
    fontSize: 18,
    fontWeight: "800",
    color: "#111827",
    textAlign: "center",
  },
  orderLabel: {
    fontSize: 12,
    color: "#6B7280",
    marginTop: 4,
    maxWidth: 280,
    textAlign: "center",
  },
  body: {
    fontSize: 13,
    color: "#374151",
    lineHeight: 19,
    textAlign: "center",
    marginBottom: 18,
    paddingHorizontal: 4,
  },
  actionsRow: {
    flexDirection: "row",
    gap: 10,
  },
  btn: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    flexDirection: "row",
    gap: 6,
  },
  btnGhost: {
    backgroundColor: "#F3F4F6",
    borderWidth: 1,
    borderColor: "#E5E7EB",
  },
  btnGhostText: {
    fontSize: 13,
    fontWeight: "700",
    color: "#374151",
  },
  btnDanger: {
    backgroundColor: "#DC2626",
  },
  btnDangerText: {
    fontSize: 13,
    fontWeight: "800",
    color: "#fff",
  },
});

/** Public helper used by callers to decide if a status string means
 *  the order is permanently dead. Keeps the source-of-truth on the
 *  frontend identical to backend/lib/terminal_states.py. */
export const TERMINAL_SHIPMENT_STATUSES = [
  "Cancelled",
  "Cancel by buyer",
  "Returned",
] as const;

export function isTerminalShipmentStatus(status?: string | null): boolean {
  if (!status) return false;
  const s = String(status).trim().toLowerCase();
  return TERMINAL_SHIPMENT_STATUSES.some((x) => x.toLowerCase() === s);
}

export function isTerminalPendingStatus(status?: string | null): boolean {
  if (!status) return false;
  return String(status).trim().toLowerCase() === "cancelled";
}
