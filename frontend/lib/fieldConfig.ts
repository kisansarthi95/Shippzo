/**
 * Phase-24 — Field-config client (Field Control System).
 *
 * Talks to the backend `/api/field-configs/{module}` endpoint and
 * exposes a tiny ergonomic hook that screens can use to decide
 * whether a non-locked field should render & whether it's required.
 *
 * Design notes:
 *  • LOCKED fields are still enumerated by the server so the UI can
 *    show a 🔒 badge in the admin screen. From the consuming screen's
 *    POV they're always enabled + required; no need to special-case.
 *  • Unknown / unregistered field keys default to `enabled=true,
 *    required=false` — safe fallback so a future code change can
 *    reference a key before the registry catches up.
 *  • The hook caches by module key in a module-level Map so multiple
 *    screens sharing the same module hit the network only once.
 */
import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

export type FieldRule = {
  field_key: string;
  label: string;
  enabled: boolean;
  required: boolean;
  hint?: string;
  locked: boolean;
};

export type ModuleConfig = {
  module: string;
  locked: FieldRule[];
  configurable: FieldRule[];
  locked_keys?: string[];
  updated_at?: string;
};

// ── Tiny memo cache so re-mounts don't re-hit the network ──────────
const _cache = new Map<string, ModuleConfig>();
const _inflight = new Map<string, Promise<ModuleConfig>>();

async function fetchModuleConfig(module: string): Promise<ModuleConfig> {
  if (_cache.has(module)) return _cache.get(module)!;
  if (_inflight.has(module)) return _inflight.get(module)!;
  const p = api
    .get<ModuleConfig>(`/field-configs/${module}`)
    .then((r) => {
      _cache.set(module, r.data);
      return r.data;
    })
    .finally(() => {
      _inflight.delete(module);
    });
  _inflight.set(module, p);
  return p;
}

/** Drop the cache so the next read goes back to the server (admin
 *  screens call this after editing).                                 */
export function invalidateFieldConfig(module?: string) {
  if (module) _cache.delete(module);
  else _cache.clear();
}

/**
 * useFieldConfig — react to the centralized field rules.
 *
 * Returns helpers tuned for inline use in JSX:
 *   const fc = useFieldConfig("new_shipment");
 *   {fc.isEnabled("tracking_id") && <Input … />}
 *   {fc.isRequired("tracking_id") ? "*" : null}
 *
 * Until the network completes, helpers fall back to "enabled + not
 * required" so the UI doesn't flash empty.
 */
export function useFieldConfig(module: string) {
  const [cfg, setCfg] = useState<ModuleConfig | null>(
    _cache.get(module) ?? null
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!_cache.has(module));

  useEffect(() => {
    let alive = true;
    setLoading(!_cache.has(module));
    fetchModuleConfig(module)
      .then((data) => {
        if (alive) {
          setCfg(data);
          setError(null);
        }
      })
      .catch((e) => {
        if (alive) {
          // Network blip / 401 etc. — log and stay on safe defaults.
          // eslint-disable-next-line no-console
          console.warn("[field_configs] fetch failed", module, e?.message);
          setError(e?.message ?? "fetch failed");
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [module]);

  const rule = useCallback(
    (key: string): FieldRule | null => {
      if (!cfg) return null;
      const all = [...cfg.locked, ...cfg.configurable];
      return all.find((f) => f.field_key === key) ?? null;
    },
    [cfg]
  );

  const isEnabled = useCallback(
    (key: string): boolean => {
      const r = rule(key);
      // Safe default while loading or for unknown keys: visible.
      return r ? r.enabled : true;
    },
    [rule]
  );

  const isRequired = useCallback(
    (key: string): boolean => {
      const r = rule(key);
      // Safe default while loading or for unknown keys: optional.
      return r ? r.required : false;
    },
    [rule]
  );

  const reload = useCallback(async () => {
    invalidateFieldConfig(module);
    const data = await fetchModuleConfig(module);
    setCfg(data);
    return data;
  }, [module]);

  return { cfg, loading, error, rule, isEnabled, isRequired, reload };
}

// ── Self endpoints (any user with `field_controls` feature flag) ────
//
// Phase-27 — Migrated from /api/admin/field-configs to
// /api/me/field-configs because the feature is now per-tenant and
// gated by the plan-level feature flag `field_controls`.
export async function adminGetFieldConfig(module: string): Promise<ModuleConfig> {
  const r = await api.get<ModuleConfig>(`/me/field-configs/${module}`);
  return r.data;
}

export async function adminPatchFieldConfig(
  module: string,
  field_key: string,
  patch: { enabled?: boolean; required?: boolean }
): Promise<ModuleConfig> {
  const r = await api.patch<ModuleConfig>(
    `/me/field-configs/${module}/${field_key}`,
    patch
  );
  // Bust the cache so consuming screens see the latest immediately.
  invalidateFieldConfig(module);
  return r.data;
}
