/**
 * Auth context — persists JWT token in AsyncStorage, exposes login/
 * signup/logout, and injects the Bearer header into every axios call
 * made via `Api` (see lib/api.ts axios instance).
 *
 * Usage:
 *   wrap the root layout with <AuthProvider> once,
 *   call useAuth() anywhere to read `user`, `token`, `signIn`,
 *   `signUp`, `signOut`, `loading` (initial-restore flag).
 */
import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { api } from "./api";

const TOKEN_KEY = "@auth_token";
const USER_KEY = "@auth_user";

export type User = {
  id: string;
  email: string;
  name: string;
  shop_name: string;
  is_admin?: boolean;
  plan?: string;
  created_at: string;
};

type AuthState = {
  user: User | null;
  token: string | null;
  /** True until we've tried to restore the session from AsyncStorage on boot. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string, name: string, shop_name: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

function applyTokenToAxios(token: string | null) {
  if (token) api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  else delete api.defaults.headers.common["Authorization"];
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Hydrate from AsyncStorage on first mount.
  useEffect(() => {
    (async () => {
      try {
        const [t, u] = await Promise.all([
          AsyncStorage.getItem(TOKEN_KEY),
          AsyncStorage.getItem(USER_KEY),
        ]);
        if (t) {
          applyTokenToAxios(t);
          setToken(t);
          if (u) {
            try { setUser(JSON.parse(u)); } catch {}
          }
          // Verify token still valid by hitting /auth/me. If not, wipe.
          try {
            const me = await api.get<User>("/auth/me");
            setUser(me.data);
            await AsyncStorage.setItem(USER_KEY, JSON.stringify(me.data));
          } catch {
            applyTokenToAxios(null);
            await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
            setToken(null);
            setUser(null);
          }
        }
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const persist = useCallback(async (t: string, u: User) => {
    applyTokenToAxios(t);
    setToken(t);
    setUser(u);
    await AsyncStorage.multiSet([[TOKEN_KEY, t], [USER_KEY, JSON.stringify(u)]]);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const r = await api.post<{ token: string } & User>("/auth/login", { email, password });
    const { token: tok, ...userFields } = r.data;
    await persist(tok, userFields as User);
  }, [persist]);

  const signUp = useCallback(async (
    email: string, password: string, name: string, shop_name: string,
  ) => {
    const r = await api.post<{ token: string } & User>("/auth/signup", {
      email, password, name, shop_name,
    });
    const { token: tok, ...userFields } = r.data;
    await persist(tok, userFields as User);
  }, [persist]);

  const signOut = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch {}
    applyTokenToAxios(null);
    setToken(null);
    setUser(null);
    await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
  }, []);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const me = await api.get<User>("/auth/me");
      setUser(me.data);
      await AsyncStorage.setItem(USER_KEY, JSON.stringify(me.data));
    } catch {
      await signOut();
    }
  }, [token, signOut]);

  return (
    <AuthCtx.Provider value={{ user, token, loading, signIn, signUp, signOut, refresh }}>
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth() must be inside <AuthProvider>");
  return ctx;
}
