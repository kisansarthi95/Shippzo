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
import { Ionicons } from "@expo/vector-icons";
import { Api } from "./api";

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
      setState((s) => ({ ...s, loading: false }));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

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
      <Ionicons name="lock-closed" size={26} color="#92400E" />
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
