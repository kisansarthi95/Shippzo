/**
 * Canonical Order-Amount math — single source of truth.
 *
 * Phase-31 (2026-06): The Phase-30 model treated the typed "Amount"
 * field as the post-advance COD value and ADDED the token back into
 * `amount` to fake a total. That double-counted advances in
 * downstream reports + receipts whenever upstream code (CSV export,
 * print label, shipment list, WhatsApp templates) ran its own
 * `amount - token` subtraction.
 *
 * The new contract:
 *   • `amount`       — Total Order Value (exactly what the operator
 *                       types into the form, no math applied).
 *   • `token_amount` — Advance already collected online (independent).
 *   • `cod_amount`   — What the courier will collect on delivery
 *                       = max(0, amount − token_amount) for COD,
 *                       0 for prepaid / non-COD modes.
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
  /** Total order value typed by the operator (₹). */
  amount: number | string | null | undefined;
  /** Advance already paid online (₹). */
  token: number | string | null | undefined;
  /** "COD" → cod_amount = max(0, amount − token); else cod_amount = 0. */
  paymentMode: PaymentMode;
}

export interface OrderAmountsResult {
  /** Gross order value — same as input.amount, just normalised. */
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
 * The token is NOT clamped against the total — if the operator
 * accidentally typed a token greater than the amount we still
 * floor `cod_amount` at 0 (negative COD would be nonsensical) but
 * leave `token_amount` untouched so a downstream alert can flag it.
 */
export function computeOrderAmounts(input: OrderAmountInput): OrderAmountsResult {
  const amount = toNum(input.amount);
  const tokenAmount = toNum(input.token);
  const isCod = String(input.paymentMode || "").toUpperCase() === "COD";
  const codAmount = isCod ? Math.max(0, amount - tokenAmount) : 0;
  return { amount, tokenAmount, codAmount };
}
