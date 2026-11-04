import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, DATACO } from "./api";
import type { DatasetItem } from "./types";
import { useApi } from "./useApi";

const STORAGE_KEY = "provenance.dataset";

interface DatasetChoice {
  /** "dataco" or an onboarded dataset's id. */
  selected: string;
  setSelected: (dataset: string) => void;
  /** Datasets that can be selected: scored ones (they have flags and scores). */
  choices: DatasetItem[];
  selectedName: string;
  isDataco: boolean;
}

const DatasetContext = createContext<DatasetChoice | null>(null);

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || DATACO;
  } catch {
    return DATACO;
  }
}

/** Which data Overview and Flags show: DataCo by default, or a scored dataset. */
export function DatasetProvider({ children }: { children: ReactNode }) {
  const [stored, setStored] = useState(readStored);
  const datasets = useApi("datasets", api.datasets);
  const choices = useMemo(
    () => (datasets.data?.items ?? []).filter((item) => item.display_status === "scored"),
    [datasets.data],
  );
  // A remembered dataset that no longer exists (or is not scored) falls back to DataCo.
  const selected =
    stored === DATACO || !datasets.data || choices.some((item) => item.dataset_id === stored)
      ? stored
      : DATACO;
  const setSelected = useCallback((dataset: string) => {
    setStored(dataset);
    try {
      localStorage.setItem(STORAGE_KEY, dataset);
    } catch {
      // Storage can be unavailable (private window); the choice still applies this session.
    }
  }, []);
  const value = useMemo<DatasetChoice>(
    () => ({
      selected,
      setSelected,
      choices,
      selectedName:
        selected === DATACO
          ? "DataCo"
          : (choices.find((item) => item.dataset_id === selected)?.name ?? "Dataset"),
      isDataco: selected === DATACO,
    }),
    [selected, setSelected, choices],
  );
  return <DatasetContext.Provider value={value}>{children}</DatasetContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useDataset(): DatasetChoice {
  const value = useContext(DatasetContext);
  if (!value) throw new Error("useDataset must be used inside DatasetProvider");
  return value;
}
