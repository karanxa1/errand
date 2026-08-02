// auth — the single source of truth for who is signed in.
//
// A React context holds the JWT (persisted in localStorage under `errand_token`)
// plus the hydrated user. On mount, if a token exists we call GET /api/auth/me
// to hydrate the user, and clear the token on a 401 so a stale/expired token
// never wedges the app. login/register store the returned token + user; logout
// clears both. Every authed fetch in the app pulls its Bearer token from here.

"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api } from "./config";
import { mirrorSessionToken } from "./sessionMirror";

const TOKEN_KEY = "errand_token";

export interface AuthUser {
  id: string;
  email: string;
  name?: string | null;
}

interface AuthValue {
  user: AuthUser | null;
  token: string | null;
  // True until the initial /me hydration settles, so guards can hold a beat
  // instead of flashing the login screen for an already-signed-in user.
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

// Read a stored token without throwing in SSR / privacy-mode contexts.
function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeStoredToken(token: string | null) {
  if (typeof window === "undefined") return;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — session stays in-memory only */
  }
}

// Pull a human-readable message out of an auth error response, falling back to
// a status-appropriate default so the form always has something to show.
async function errorMessage(res: Response, fallback: string): Promise<string> {
  try {
    const data = await res.json();
    const detail = (data as { detail?: unknown; message?: unknown }).detail;
    const message = (data as { message?: unknown }).message;
    if (typeof detail === "string") return detail;
    if (typeof message === "string") return message;
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  // Guards a double-hydrate under React 18/19 StrictMode's dev double-mount.
  const hydrated = useRef(false);

  // On mount: if a token is stored, hydrate the user. A 401 means the token is
  // stale — clear it. Any other failure keeps the token (offline backend) but
  // still lifts `loading` so the app isn't stuck.
  useEffect(() => {
    if (hydrated.current) return;
    hydrated.current = true;

    const stored = readStoredToken();
    if (!stored) {
      setLoading(false);
      return;
    }
    setToken(stored);
    void mirrorSessionToken(stored);

    let alive = true;
    (async () => {
      try {
        const res = await fetch(api("/api/auth/me"), {
          headers: { Authorization: `Bearer ${stored}` },
        });
        if (!alive) return;
        if (res.status === 401) {
          writeStoredToken(null);
          setToken(null);
          setUser(null);
        } else if (res.ok) {
          setUser((await res.json()) as AuthUser);
        }
      } catch {
        /* backend unreachable — keep token, let a later call retry */
      } finally {
        if (alive) setLoading(false);
      }
    })();

    return () => {
      alive = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(api("/api/auth/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      throw new Error(
        await errorMessage(res, "Invalid email or password."),
      );
    }
    const data = (await res.json()) as { token: string; user: AuthUser };
    writeStoredToken(data.token);
    void mirrorSessionToken(data.token);
    setToken(data.token);
    setUser(data.user);
  }, []);

  const register = useCallback(
    async (email: string, password: string, name?: string) => {
      const res = await fetch(api("/api/auth/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          ...(name ? { name } : {}),
        }),
      });
      if (!res.ok) {
        throw new Error(
          await errorMessage(
            res,
            res.status === 409
              ? "Email already registered."
              : "Could not create your account.",
          ),
        );
      }
      const data = (await res.json()) as { token: string; user: AuthUser };
      writeStoredToken(data.token);
      void mirrorSessionToken(data.token);
      setToken(data.token);
      setUser(data.user);
    },
    [],
  );

  const logout = useCallback(() => {
    writeStoredToken(null);
    void mirrorSessionToken(null);
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ user, token, loading, login, register, logout }),
    [user, token, loading, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return ctx;
}
