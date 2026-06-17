/**
 * Canonical Order-Amount math — single source of truth.
 *
 * Phase-31 (rev-2, 2026-06): The form's "amount" field is now the
 * **COD-to-Collect** value — what the courier will physically take
 * from the customer at delivery. The Total Order Value is derived
 * upward (= COD + Token), never downward. No subtraction is ever
 * performed in app code; the values flow through verbatim.
 *
 * The new contract:
 *   • amount       — Total Order Value = codInput + tokenAmount.
 *   • token_amount — Advance already paid online (independent).
 *   • cod_amount   — What the courier will collect on delivery
 *                     = codInput verbatim for COD,
 *                     0 for prepaid / non-COD modes.
 *
 * Surfaces that must use this helper:
 *   • add.tsx           (form submit payload)
 *   • shipment-details  (display rows)
 *   • shipments list    (compact "COD ₹" subtitle)
 *   • bulk paste flows
 *
 * Keeping a single helper guarantees the same rounding & clamp
 * behaviour everywhere; if the business rule shifts again, only
 * one file changes.
 */
export type PaymentMode = "COD" | "Prepaid" | string;

export interface OrderAmountInput {
  /**
   * COD-to-Collect entered by the operator (₹).
   * For COD orders this is exactly what the courier collects at
   * delivery; for prepaid orders it represents the paid order value.
   */
  amount: number | string | null | undefined;
  /** Advance already paid online (₹). */
  token: number | string | null | undefined;
  /** "COD" → codAmount = input; total = input + token. Else codAmount = 0. */
  paymentMode: PaymentMode;
}

export interface OrderAmountsResult {
  /**
   * Gross Total Order Value (₹) = codAmount + tokenAmount for COD,
   * = the entered value for prepaid.
   */
  amount: number;
  /** Token / advance already paid online. */
  tokenAmount: number;
  /** What the courier collects at delivery (₹). 0 for non-COD. */
  codAmount: number;
}

/** Normalise loosely-typed input into a finite, non-negative number. */
function toNum(v: number | string | null | undefined): number {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return 0;
  return n < 0 ? 0 : n;
}

/**
 * Compute the canonical {amount, tokenAmount, codAmount} triple.
 *
 * The entered value IS the final COD to Collect (no math applied);
 * the Total is derived by ADDING the token back so receipts /
 * accounting reports still see the full order value.
 */
export function computeOrderAmounts(input: OrderAmountInput): OrderAmountsResult {
  const codInput = toNum(input.amount);
  const tokenAmount = toNum(input.token);
  const isCod = String(input.paymentMode || "").toUpperCase() === "COD";
  const codAmount = isCod ? codInput : 0;
  // Total Order Value = COD + Token (no subtraction ever).
  const amount = isCod ? codInput + tokenAmount : codInput;
  return { amount, tokenAmount, codAmount };
}
