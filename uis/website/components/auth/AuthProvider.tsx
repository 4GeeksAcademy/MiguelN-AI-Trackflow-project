"use client";

import { useRouter } from "next/navigation";
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
import { UnauthorizedError } from "@/lib/auth-api-client";
import { buildLoginUrl, LOGIN_PATH, readToken, UNAUTHORIZED_EVENT } from "@/lib/auth-storage";
import { GENERAL_ERROR_COPY, describeError } from "@/lib/friendly-error";
import { fetchCurrentUser, logout } from "@/services/auth-service";
import type { AuthenticatedUser, Profile, SessionStatus } from "@/types/auth";

type SessionContextValue = {
  status: SessionStatus;
  user: AuthenticatedUser | null;
  /** Why the session could not be checked. Set only while `status` is `error`. */
  error: string | null;
  /** Checks the session again after a failure that was not the token's fault. */
  retry: () => void;
  /** Replaces the cached profile after a successful `PUT /profiles/me`. */
  applyProfile: (profile: Profile) => void;
  signOut: () => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

/** The path the user should come back to after signing in. */
function currentPathWithQuery(): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  return `${window.location.pathname}${window.location.search}`;
}

/**
 * Owns the client-side session: it validates the stored token once on mount,
 * exposes the authenticated user, and reacts to any protected call that comes
 * back 401 by sending the user to the login page.
 *
 * Mounted only under `app/(protected)`, so public pages never read the token
 * and never redirect.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [user, setUser] = useState<AuthenticatedUser | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const hasRedirected = useRef(false);

  const redirectToLogin = useCallback(() => {
    if (hasRedirected.current) {
      return;
    }

    hasRedirected.current = true;
    setStatus("unauthenticated");
    setUser(null);
    router.replace(buildLoginUrl(currentPathWithQuery()));
  }, [router]);

  useEffect(() => {
    // A protected call rejected the token while the user was on the page.
    window.addEventListener(UNAUTHORIZED_EVENT, redirectToLogin);

    return () => window.removeEventListener(UNAUTHORIZED_EVENT, redirectToLogin);
  }, [redirectToLogin]);

  useEffect(() => {
    let isCurrent = true;

    // An absent token is settled without a request; a present one is only
    // trusted once `GET /auth/me` accepts it, which also catches expiry.
    if (!readToken()) {
      redirectToLogin();
      return;
    }

    fetchCurrentUser()
      .then((currentUser) => {
        if (!isCurrent) {
          return;
        }

        setUser(currentUser);
        setStatus("authenticated");
      })
      .catch((sessionError: unknown) => {
        if (!isCurrent) {
          return;
        }

        // The token itself was refused: `requestAuthenticatedApi` already
        // cleared it and dispatched the unauthorized event.
        if (sessionError instanceof UnauthorizedError) {
          redirectToLogin();
          return;
        }

        // The API was unreachable, failed, or answered with something
        // unreadable. None of that says the token is bad, so signing the user
        // out would turn an outage into an unexplained logout. The guard keeps
        // protected data hidden and shows the reason with a retry instead.
        setError(describeError(sessionError, "We could not confirm your session.", GENERAL_ERROR_COPY).message);
        setStatus("error");
      });

    return () => {
      isCurrent = false;
    };
  }, [redirectToLogin, attempt]);

  const retry = useCallback(() => {
    setError(null);
    setStatus("loading");
    setAttempt((current) => current + 1);
  }, []);

  const applyProfile = useCallback((profile: Profile) => {
    setUser((currentUser) => (currentUser ? { ...currentUser, profile } : currentUser));
  }, []);

  const signOut = useCallback(() => {
    logout();
    hasRedirected.current = true;
    setStatus("unauthenticated");
    setUser(null);
    router.replace(LOGIN_PATH);
  }, [router]);

  const value = useMemo<SessionContextValue>(
    () => ({ status, user, error, retry, applyProfile, signOut }),
    [status, user, error, retry, applyProfile, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);

  if (!context) {
    throw new Error("useSession must be used inside an AuthProvider");
  }

  return context;
}
