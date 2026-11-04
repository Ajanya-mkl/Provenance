import { BadgeCheck, ChevronRight, FileWarning, Maximize2, Search, SearchX } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Sheet } from "../components/Dialogs";
import { DatasetSelect } from "../components/Shell";
import { ConfidenceBadge, EmptyState, ErrorState, LoadingBlock, PageHeader, Segmented, TierChange } from "../components/ui";
import { api } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { factorLabel, score, supplierLabel } from "../lib/format";
import type { FlagSummary } from "../lib/types";
import { useApi } from "../lib/useApi";
import { FlagDetailBody } from "./FlagDetail";

type TierFilter = "all" | "high" | "medium" | "low";
type ConfidenceFilter = "all" | "full" | "low";

function InvestigationChip({ flag }: { flag: FlagSummary }) {
  if (!flag.investigation) return <span className="text-[13px] text-ink-3">Not investigated</span>;
  if (!flag.investigation.grounded) {
    return (
      <span className="inline-flex items-center gap-1 text-[13px] font-medium text-danger">
        <FileWarning aria-hidden size={14} /> Not grounded
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[13px] font-medium text-tier-low">
      <BadgeCheck aria-hidden size={14} /> Grounded
    </span>
  );
}

function FlagRow({ flag, onOpen }: { flag: FlagSummary; onOpen: () => void }) {
  return (
    <li className="border-b border-line last:border-0">
      <button
        type="button"
        onClick={onOpen}
        className="grid w-full grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_auto] items-center gap-4 rounded-[14px] px-4 py-3.5 text-left transition-colors duration-200 hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] md:grid-cols-[minmax(0,1.6fr)_90px_minmax(0,1.2fr)_76px_minmax(0,1.3fr)_minmax(0,1fr)_20px]"
      >
        <span className="min-w-0">
          <span className="block truncate text-[15px] font-semibold text-ink">
            {supplierLabel(flag.supplier_key, flag.supplier_name)}
          </span>
          <span className="block truncate text-[12px] text-ink-3">{flag.supplier_key}</span>
        </span>
        <span className="tabular text-[14px] text-ink-2 max-md:hidden">{flag.quarter}</span>
        <span className="max-md:col-span-3 max-md:row-start-2">
          <TierChange from={flag.old_tier} to={flag.new_tier} />
        </span>
        <span className="tabular text-right text-[17px] font-semibold md:text-left">{score(flag.score)}</span>
        <span className="min-w-0 max-md:hidden">
          <span className="block truncate text-[14px] text-ink-2">{factorLabel(flag.top_factor.name)}</span>
          <ConfidenceBadge confidence={flag.confidence} />
        </span>
        <span className="max-md:hidden">
          <InvestigationChip flag={flag} />
        </span>
        <ChevronRight aria-hidden size={18} className="text-ink-3" />
      </button>
    </li>
  );
}

export function FlagsPage() {
  const [params, setParams] = useSearchParams();
  const [tier, setTier] = useState<TierFilter>("all");
  const [confidence, setConfidence] = useState<ConfidenceFilter>("all");
  const [query, setQuery] = useState("");
  const { selected, selectedName, isDataco } = useDataset();
  const flags = useApi(`flags:${selected}`, () => api.flags({ dataset: selected }));
  const open = params.get("open");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (flags.data?.items ?? []).filter(
      (flag) =>
        (tier === "all" || flag.new_tier === tier) &&
        (confidence === "all" || flag.confidence === confidence) &&
        (!needle ||
          `${flag.supplier_name ?? ""} ${flag.supplier_key} ${flag.quarter}`.toLowerCase().includes(needle)),
    );
  }, [flags.data, tier, confidence, query]);

  const openFlag = flags.data?.items.find((flag) => flag.event_id === open);
  const setOpen = (eventId: string | null) => {
    const next = new URLSearchParams(params);
    if (eventId) next.set("open", eventId);
    else next.delete("open");
    setParams(next, { replace: !eventId });
  };

  return (
    <>
      <PageHeader
        title="Flags"
        meta={
          flags.data
            ? `${isDataco ? "" : `${selectedName} · `}${flags.data.count} ${isDataco ? "supplier" : "entity"}-quarter tier changes from the rule engine. Select one to see why.`
            : "Supplier-quarter tier changes from the rule engine."
        }
        actions={<DatasetSelect className="w-[220px] lg:hidden" />}
      />
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <Segmented<TierFilter>
          label="Tier"
          value={tier}
          onChange={setTier}
          options={[
            { value: "all", label: "All tiers" },
            { value: "high", label: "High" },
            { value: "medium", label: "Medium" },
            { value: "low", label: "Low" },
          ]}
        />
        <Segmented<ConfidenceFilter>
          label="Confidence"
          value={confidence}
          onChange={setConfidence}
          options={[
            { value: "all", label: "Any confidence" },
            { value: "full", label: "Full" },
            { value: "low", label: "Low" },
          ]}
        />
        <label className="glass-pill min-w-[220px] flex-1 px-4 py-2 sm:max-w-[340px]">
          <Search aria-hidden size={16} className="shrink-0 text-ink-3" />
          <span className="sr-only">Search suppliers</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search supplier or quarter"
            className="w-full bg-transparent text-[15px] text-ink outline-none placeholder:text-ink-3"
          />
        </label>
      </div>

      <section aria-label="Flag list" className="glass p-2">
        {flags.error ? (
          <ErrorState error={flags.error} onRetry={flags.reload} />
        ) : flags.loading ? (
          <div className="p-4">
            <LoadingBlock rows={8} label="Loading flags" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState icon={<SearchX size={22} />} title="No flags match these filters">
            {flags.data?.count
              ? "Clear the search or pick another tier or confidence."
              : "The rule engine has not flagged any supplier yet. Run the pipeline to score the latest quarter."}
          </EmptyState>
        ) : (
          <ul aria-label={`${filtered.length} flags`}>
            {filtered.map((flag) => (
              <FlagRow key={flag.event_id} flag={flag} onOpen={() => setOpen(flag.event_id)} />
            ))}
          </ul>
        )}
      </section>

      <Sheet
        open={Boolean(open)}
        onClose={() => setOpen(null)}
        title={
          openFlag
            ? `${supplierLabel(openFlag.supplier_key, openFlag.supplier_name)} · ${openFlag.quarter}`
            : "Flag"
        }
      >
        {open && (
          <>
            <FlagDetailBody eventId={open} />
            <div className="mt-6 flex justify-end">
              <Link to={`/flags/${encodeURIComponent(open)}`} className="glass-pill px-4 py-2 text-sm">
                <Maximize2 aria-hidden size={15} /> Open as page
              </Link>
            </div>
          </>
        )}
      </Sheet>
    </>
  );
}
