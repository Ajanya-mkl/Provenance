import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import { api } from "./api";
import type { Approvals } from "./types";
import { useApi } from "./useApi";
import type { Resource } from "./useApi";

const PendingContext = createContext<Resource<Approvals> | null>(null);

/** One shared approvals load, so the rail badge and screens agree after a decision. */
export function PendingProvider({ children }: { children: ReactNode }) {
  const approvals = useApi("approvals", api.approvals);
  return <PendingContext.Provider value={approvals}>{children}</PendingContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useApprovals(): Resource<Approvals> {
  const context = useContext(PendingContext);
  if (!context) throw new Error("useApprovals must be used inside PendingProvider");
  return context;
}

// eslint-disable-next-line react-refresh/only-export-components
export function usePending(): { pending: number | undefined; loading: boolean } {
  const approvals = useApprovals();
  return { pending: approvals.data?.pending, loading: approvals.loading };
}
