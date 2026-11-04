import { api } from "./api";
import { useApi } from "./useApi";

export const inputClass =
  "w-full rounded-[12px] border border-line bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] px-3 py-2 text-[15px] text-ink outline-none placeholder:text-ink-3 focus-visible:border-[var(--accent)]";

export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function useDatasetDetail(id: string) {
  return useApi(`dataset:${id}`, () => api.dataset(id));
}
