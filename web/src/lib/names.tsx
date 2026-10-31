import { createContext, useCallback, useContext, useMemo } from "react";
import type { ReactNode } from "react";

import { api } from "./api";
import { supplierLabel } from "./format";
import { useApi } from "./useApi";

const NamesContext = createContext<Record<string, string>>({});

/** Canonical supplier names (from the warehouse), so a supplier reads the same everywhere. */
export function NamesProvider({ children }: { children: ReactNode }) {
  const flags = useApi("names", () => api.flags());
  const names = useMemo(() => {
    const map: Record<string, string> = {};
    for (const flag of flags.data?.items ?? []) {
      if (flag.supplier_name) map[flag.supplier_key] = flag.supplier_name;
    }
    return map;
  }, [flags.data]);
  return <NamesContext.Provider value={names}>{children}</NamesContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSupplierName(): (key: string | null | undefined) => string {
  const names = useContext(NamesContext);
  return useCallback((key) => (key ? supplierLabel(key, names[key]) : "Unknown supplier"), [names]);
}
