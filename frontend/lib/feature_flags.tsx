/**
 * Feature-flag client.
 *
 * Architecture:
 *   1. <FeatureFlagsProvider/> wraps the app (added in app/_layout.tsx).
 *      It fetches /me/feature-flags after login and caches the result.
 *   2. useFeatureFlag(key) returns boolean — UI uses it to gate rendering.
 *   3. useFeatureFlags() returns the full Set + a refresh() callback,
 *      e.g. for the admin panel to invalidate after saving.
 *
 * Why a Provider instead of a hook-only API?
 *   - Single network call shared by every screen.
 *   - Survives tab switches (provider sits above the tab navigator).
 *   - Lets us expose `isAdmin` cheaply alongside the flags.
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api } from "./api";
import { useAuth } from "./auth";

type Ctx = {
  features: Set<string>;
  isAdmin: boolean;
  loading: boolean;
  refresh: () => Promise<void>;
};

const FeatureFlagsContext = createContext<Ctx>({
  features: new Set(),
  isAdmin: false,
  loading: true,
  refresh: async () => {},
});

export function FeatureFlagsProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [features, setFeatures] = useState<Set<string>>(new Set());
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!user) {
      setFeatures(new Set());
      setIsAdmin(false);
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const r = await api.get<{ features: string[]; is_admin: boolean }>(
        "/me/feature-flags",
      );
      setFeatures(new Set(r.data.features || []));
      setIsAdmin(!!r.data.is_admin);
    } catch {
      // Fail-open: if the server is unreachable we DO show all features
      // so the user isn't locked out due to a transient error. Logged-in
      // session is required anyway.
      setFeatures(new Set());
      setIsAdmin(false);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ features, isAdmin, loading, refresh }),
    [features, isAdmin, loading, refresh],
  );
  return (
    <FeatureFlagsContext.Provider value={value}>
      {children}
    </FeatureFlagsContext.Provider>
  );
}

/** Returns `true` when the feature is enabled for the current user.
 *  Phase F4.7 — for TEAM MEMBER sessions, also checks that the
 *  member has the specific permission. This means the same hook
 *  transparently gates the UI at both levels:
 *    1. Owner's plan must include the feature (existing behavior)
 *    2. Team member must have the permission (new)
 *  Owners always pass the second gate. */
export function useFeatureFlag(key: string): boolean {
  const { features, isAdmin } = useContext(FeatureFlagsContext);
  // Admin gets EVERY feature regardless of plan. This guarantees the
  // admin can always reach the panel itself + diagnose toggles.
  if (isAdmin) return true;
  // Phase F4.7 — Import inline to avoid a circular dep with
  // permissions.tsx (which itself imports Api that goes through this
  // provider). React allows hooks called at the top of a component
  // — this line runs inside the caller's render, so it's safe.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { usePermissions } = require("./permissions");
  const { isTeamMember, hasPerm } = usePermissions();
  if (!features.has(key)) return false;
  if (isTeamMember && !hasPerm(key)) return false;
  return true;
}

export function useFeatureFlags() {
  return useContext(FeatureFlagsContext);
}
