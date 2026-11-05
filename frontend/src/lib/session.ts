"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { Permission, Role, Session } from "@/lib/types";

export const SESSION_KEY = ["session"];

export const ROLE_LABELS: Record<Role, string> = {
  finance_executive: "Finance Executive",
  co_founder: "Co-founder",
};

export function useSession() {
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => api<Session>("/auth/me"),
    staleTime: 60_000,
  });
}

/** For showing the right controls. The API enforces the permission regardless. */
export function useCan(permission: Permission): boolean {
  const { data } = useSession();
  return data?.permissions.includes(permission) ?? false;
}
