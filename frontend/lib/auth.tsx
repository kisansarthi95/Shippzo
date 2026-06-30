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
import { api, registerUnauthorizedHandler, Api } from "./api";

const TOKEN_KEY = "@auth_token";
const USER_KEY = "@auth_user";

export type User = {
  id: string;
  display_id?: string;
  email: string;
  name: string;
  shop_name: string;
  phone?: string;
  is_admin?: boolean;
  plan?: string;
  /** Phase G — slug picked during signup. Empty string for legacy /
   *  Google-OAuth accounts that pre-date the field. */
  primary_business_category?: string;
  created_at: string;
};

type AuthState = {
  user: User | null;
  token: string | null;
  /** True until we've tried to restore the session from AsyncStorage on boot. */
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signInTeam: (email: string, password: string) => Promise<void>;
  signUp: (
    email: string, password: string, name: string, shop_name: string,
    phone: string, business_category?: string,
  ) => Promise<{ trial_denied: boolean; trial_denied_reason: string }>;
  signInWithGoogleSession: (sessionId: string) => Promise<void>;
  /**
   * Phase-OTP — WhatsApp-OTP login OR signup, returned by the backend
   * as `mode: "login" | "signup"`. The caller doesn't need to know
   * which one happened; the session is persisted either way and the
   * UI just routes to the dashboard.
   *
   * `name` / `shop_name` are only used when the backend determines a
   * fresh user record needs to be created (i.e. signup-via-OTP). For
   * existing users they are ignored server-side, so it's safe to send
   * them blank when the screen is acting as a "login" rather than a
   * "signup" form.
   */
  signInWithOtp: (params: {
    phone: string;
    otp: string;
    event_type?:
      | "login"
      | "signup"
      | "phone_verification"
      | "auth"
      | "password_reset"
      | "mfa";
    name?: string;
    shop_name?: string;
  }) => Promise<{ mode: "login" | "signup" }>;
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
    // Phase G6 — fire-and-forget push token registration the first
    // time the user authenticates on this device. Idempotent.
    try {
      const { registerForPushNotificationsAsync } = await import("./pushRegistration");
      registerForPushNotificationsAsync().catch(() => {});
    } catch { /* ignore */ }
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const r = await api.post<{ token: string } & User>("/auth/login", { email, password });
    const { token: tok, ...userFields } = r.data;
    await persist(tok, userFields as User);
  }, [persist]);

  /** Phase B+C — log in as a TEAM MEMBER (a sub-account under a shop
   *  owner). The backend issues a token with kind="team" carrying the
   *  member's permission set; that token is otherwise indistinguishable
   *  from an owner token from the client's perspective except for the
   *  `is_team_member` + `permissions` fields surfaced via
   *  `/auth/context` (read by `PermissionsProvider`). */
  const signInTeam = useCallback(async (email: string, password: string) => {
    const r = await api.post<{ token: string }>("/team/login", { email, password });
    const tok = r.data.token;
    // The team member doesn't have its own User row, so we surface a
    // synthetic stub. PermissionsProvider's `/auth/context` call will
    // load the real shop-name + permissions immediately after.
    const stub: User = {
      id:    "team-session",
      email, name: email.split("@")[0],
      shop_name: "", phone: "",
    } as any;
    await persist(tok, stub);
  }, [persist]);

  const signUp = useCallback(async (
    email: string, password: string, name: string, shop_name: string,
    phone: string, business_category?: string,
  ) => {
    // Phase-2b: collect a stable per-device fingerprint so the backend
    // can deny repeated free trials from the same hardware. Best-effort
    // — never blocks signup if the helper fails.
    let device_fingerprint = "";
    try {
      const { safeGetDeviceFingerprint } = await import("./deviceFingerprint");
      device_fingerprint = await safeGetDeviceFingerprint();
    } catch { /* ignore */ }

    const r = await api.post<
      { token: string; trial_denied?: boolean; trial_denied_reason?: string } & User
    >("/auth/signup", {
      email, password, name, shop_name, phone, device_fingerprint,
      // Phase G — primary business category collected on the signup form
      // itself. Empty string when the field is left blank (the form
      // marks it required, but we tolerate older clients gracefully).
      primary_business_category: business_category || "",
    });
    const { token: tok, trial_denied, trial_denied_reason, ...userFields } = r.data;
    await persist(tok, userFields as User);
    return { trial_denied: !!trial_denied, trial_denied_reason: trial_denied_reason || "" };
  }, [persist]);

  /**
   * Exchange an Emergent Google OAuth session_id for our own JWT. The
   * session_id is the one-time token the Emergent Auth page drops into
   * `window.location.hash#session_id=...` after a successful Google login.
   */
  const signInWithGoogleSession = useCallback(async (sessionId: string) => {
    const r = await api.post<{ token: string } & User>("/auth/google/session", {
      session_id: sessionId,
    });
    const { token: tok, ...userFields } = r.data;
    await persist(tok, userFields as User);
  }, [persist]);

  /**
   * Phase-OTP — WhatsApp OTP-based sign-in.
   *
   * The backend's /auth/otp/verify endpoint covers BOTH login and
   * signup transparently: if a user already exists for the verified
   * phone we get `mode:"login"` with their existing row; otherwise the
   * server creates a fresh user on the spot and returns
   * `mode:"signup"`. Either way the response shape is identical
   * `{ token, user }` so the client just persists and routes.
   *
   * `name` / `shop_name` are best-effort — the server uses them only
   * for the signup branch and silently ignores them for existing
   * users. Sending them as empty strings on the login screen is fine.
   */
  const signInWithOtp = useCallback(async (params: {
    phone: string;
    otp: string;
    event_type?:
      | "login"
      | "signup"
      | "phone_verification"
      | "auth"
      | "password_reset"
      | "mfa";
    name?: string;
    shop_name?: string;
  }) => {
    const r = await Api.verifyPhoneOtp({
      phone: params.phone,
      otp: params.otp,
      event_type: params.event_type || "auth",
      name: params.name,
      shop_name: params.shop_name,
    });
    await persist(r.token, r.user as User);
    return { mode: r.mode };
  }, [persist]);

  const signOut = useCallback(async () => {
    // Phase G6 — drop the cached push token from the backend so this
    // device stops receiving notifications for the previous account.
    try {
      const { getCachedPushToken, clearCachedPushToken } = await import("./pushRegistration");
      const cached = getCachedPushToken();
      if (cached) {
        try { await Api.removePushToken(cached); } catch { /* ignore */ }
      }
      clearCachedPushToken();
    } catch { /* ignore */ }
    try { await api.post("/auth/logout"); } catch {}
    applyTokenToAxios(null);
    setToken(null);
    setUser(null);
    await AsyncStorage.multiRemove([TOKEN_KEY, USER_KEY]);
    // Wipe in-memory screen cache so the next user / fresh login
    // doesn't see stale data from the previous session.
    try {
      const { screenCache } = await import("./screenCache");
      screenCache.invalidate();
    } catch { /* ignore */ }
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
    <AuthCtx.Provider
      value={{
        user, token, loading,
        signIn, signInTeam, signUp, signInWithGoogleSession,
        signInWithOtp,
        signOut, refresh,
      }}
    >
      {children}
    </AuthCtx.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth() must be inside <AuthProvider>");
  return ctx;
}
