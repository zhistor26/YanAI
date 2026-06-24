"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  getDefaultRouteForRole,
  getStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";

const POST_AUTH_REDIRECT_STORAGE_KEY = "yanai_post_auth_redirect";

type UseAuthGuardResult = {
  isCheckingAuth: boolean;
  session: StoredAuthSession | null;
};

function currentPathWithSearch() {
  if (typeof window === "undefined") {
    return "/image";
  }
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function normalizeLocalRedirect(value: string | null | undefined, fallback: string) {
  const target = String(value || "").trim();
  if (!target || !target.startsWith("/") || target.startsWith("//")) {
    return fallback;
  }
  if (target.startsWith("/login") || target.startsWith("/signup") || target.startsWith("/oauth/")) {
    return fallback;
  }
  return target;
}

function loginRouteWithRedirect(target: string) {
  const redirect = normalizeLocalRedirect(target, "/image");
  return `/login?redirect=${encodeURIComponent(redirect)}`;
}

export function rememberPostAuthRedirect(target: string) {
  if (typeof window === "undefined") {
    return;
  }
  const redirect = normalizeLocalRedirect(target, "");
  if (!redirect) {
    return;
  }
  window.sessionStorage.setItem(POST_AUTH_REDIRECT_STORAGE_KEY, redirect);
}

export function getPostAuthRedirect(role: AuthRole, options: { consume?: boolean } = {}) {
  const fallback = getDefaultRouteForRole(role);
  if (typeof window === "undefined") {
    return fallback;
  }
  const params = new URLSearchParams(window.location.search);
  const fromQuery = normalizeLocalRedirect(params.get("redirect"), "");
  const fromStorage = normalizeLocalRedirect(window.sessionStorage.getItem(POST_AUTH_REDIRECT_STORAGE_KEY), "");
  if (options.consume) {
    window.sessionStorage.removeItem(POST_AUTH_REDIRECT_STORAGE_KEY);
  }
  return fromQuery || fromStorage || fallback;
}

export function useAuthGuard(allowedRoles?: AuthRole[]): UseAuthGuardResult {
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const allowedRolesKey = (allowedRoles || []).join(",");

  useEffect(() => {
    let active = true;

    const load = async () => {
      const roleList = allowedRolesKey ? (allowedRolesKey.split(",") as AuthRole[]) : [];
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (!storedSession) {
        setSession(null);
        setIsCheckingAuth(false);
        router.replace(loginRouteWithRedirect(currentPathWithSearch()));
        return;
      }

      if (roleList.length > 0 && !roleList.includes(storedSession.role)) {
        setSession(storedSession);
        setIsCheckingAuth(false);
        router.replace(getDefaultRouteForRole(storedSession.role));
        return;
      }

      setSession(storedSession);
      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [allowedRolesKey, router]);

  return { isCheckingAuth, session };
}

export function useRedirectIfAuthenticated() {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (storedSession) {
        router.replace(getPostAuthRedirect(storedSession.role, { consume: true }));
        return;
      }

      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [router]);

  return { isCheckingAuth };
}
