/**
 * Centralised error-message normaliser for Axios + Alert.alert.
 * ──────────────────────────────────────────────────────────────
 * FastAPI returns Pydantic validation errors as `{ detail: [...] }`
 * (an ARRAY of error objects), not a string. React-Native's
 * `Alert.alert(title, message)` crashes hard when `message` is
 * anything other than a string with:
 *
 *   "Value for message cannot be cast from ReadableNativeArray
 *    to String"
 *
 * This helper safely flattens any of the shapes we see in the wild
 * (string / string[] / Pydantic-error[] / Error / unknown) into a
 * single human-readable string suitable for Alert.alert / toasts.
 */
export function errMsg(e: any, fallback: string = "Something went wrong"): string {
  if (!e) return fallback;
  // Plain string ----------------------------------------------------
  if (typeof e === "string") return e;
  // Axios error → response.data.detail is the canonical FastAPI shape
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    // Pydantic-422 — array of { msg, loc, type } objects.
    const parts = d.map((it: any) =>
      typeof it === "string"
        ? it
        : it?.msg
          ? `${(it.loc || []).join(".")}: ${it.msg}`.trim()
          : JSON.stringify(it),
    );
    return parts.join("\n");
  }
  if (d && typeof d === "object") return JSON.stringify(d);
  // Axios error message
  if (typeof e?.message === "string" && e.message) return e.message;
  // Generic stringify
  try { return JSON.stringify(e); } catch { return fallback; }
}
