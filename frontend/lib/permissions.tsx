/**
 * Permissions context + helpers \u2014 Phase B+C
 * ----------------------------------------
 * Reads `/api/auth/context` once at app boot and exposes:
 *
 *   const { isTeamMember, permissions, hasPerm } = usePermissions();
 *   <Gated permission="reports.view">  ...children...  </Gated>
 *
 * For OWNERS (is_team_member=false) `hasPerm` always returns true so
 * gating is invisible \u2014 they see every screen as before. For
 * TEAM-MEMBER sessions the permissions array drives both UI hiding
 * and "no access" fallback messaging.
 */
import React, {
  createContext, useContext, useEffect, useState, useCallback,
} from "react";
import { View, Text, StyleSheet } from "react-native";
import { Api } from "./api";
import { useAuth } from "./auth";
import PhIcon from "../components/PhIcon";

type AuthContextPayload = {
  isTeamMember: boolean;
  permissions: string[];
  teamMember: { id: string; name: string; role: string; email: string } | null;
  parentBusiness: string | null;
  loading: boolean;
  hasPerm: (key: string) => boolean;
  refresh: () => Promise<void>;
};

const PermCtx = createContext<AuthContextPayload>({
  isTeamMember: false, permissions: [], teamMember: null,
  parentBusiness: null, loading: true,
  hasPerm: () => true, refresh: async () => {},
});

export function PermissionsProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [state, setState] = useState<{
    isTeamMember: boolean; permissions: string[];
    teamMember: any; parentBusiness: string | null; loading: boolean;
  }>({
    isTeamMember: false, permissions: [],
    teamMember: null, parentBusiness: null, loading: true,
  });

  const refresh = useCallback(async () => {
    try {
      const res = await Api.authContext();
      setState({
        isTeamMember:   !!res.is_team_member,
        permissions:    res.team_member?.permissions || [],
        teamMember:     res.team_member,
        parentBusiness: res.user?.shop_name || null,
        loading: false,
      });
    } catch {
      // Phase F4.7 — if the fetch fails (401 / offline), reset to
      // a NEUTRAL owner-like state so we don't accidentally leak
      // permission-restricted UI from a previous team session.
      setState({
        isTeamMember: false, permissions: [],
        teamMember: null, parentBusiness: null, loading: false,
      });
    }
  }, []);

  // Phase F4.7 — Refresh whenever the auth state changes (login,
  // logout, token refresh). Without this, the provider mounts BEFORE
  // login → gets 401 → stays default (isTeamMember=false) → team
  // members appear to have owner-level access even though their
  // token is a team token. Re-running when `user` changes fixes it.
  useEffect(() => { refresh(); }, [refresh, user?.id, (user as any)?.kind]);

  const hasPerm = useCallback((key: string) => {
    // Owners (and admins) bypass gating entirely.
    if (!state.isTeamMember) return true;
    return state.permissions.includes(key);
  }, [state.isTeamMember, state.permissions]);

  return (
    <PermCtx.Provider value={{ ...state, hasPerm, refresh }}>
      {children}
    </PermCtx.Provider>
  );
}

export function usePermissions() { return useContext(PermCtx); }

/** Drop-in wrapper that hides children when the active session lacks
 *  the required permission. Renders an explanatory fallback when
 *  `fallback="message"` so navigation entries don't silently no-op. */
export function Gated({
  permission, children, fallback = "hide",
}: {
  permission: string;
  children: React.ReactNode;
  fallback?: "hide" | "message";
}) {
  const { hasPerm, loading } = usePermissions();
  if (loading) return null;
  if (hasPerm(permission)) return <>{children}</>;
  if (fallback === "hide") return null;
  return (
    <View style={styles.lockCard}>
      <PhIcon name="lock-closed" size={26} color="#92400E" />
      <Text style={styles.lockTitle}>No access</Text>
      <Text style={styles.lockTxt}>
        Your role does not have the
        <Text style={{ fontWeight: "800" }}> {permission} </Text>
        permission. Ask the shop owner to grant it.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  lockCard: {
    backgroundColor: "#FEF3C7", borderWidth: 1, borderColor: "#FCD34D",
    padding: 18, borderRadius: 12, alignItems: "center", margin: 16,
  },
  lockTitle: { fontSize: 16, fontWeight: "800", color: "#92400E", marginTop: 8 },
  lockTxt:   { fontSize: 12.5, color: "#92400E", marginTop: 4, textAlign: "center", lineHeight: 18 },
});
