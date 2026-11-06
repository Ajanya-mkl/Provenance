import {
  ClipboardCheck,
  Database,
  FileSearch,
  Hourglass,
  Flag,
  LayoutDashboard,
  Layers,
  Monitor,
  Moon,
  Sun,
  Workflow,
} from "lucide-react";
import type { ReactNode } from "react";
import { useId } from "react";
import { NavLink } from "react-router";

import { api, DATACO, isMock } from "../lib/api";
import { useDataset } from "../lib/dataset";
import { useSettings } from "../lib/settings";
import type { ThemeChoice } from "../lib/settings";
import { useApi, usePipelineBusy } from "../lib/useApi";

export function Ambient() {
  return (
    <div aria-hidden className="ambient">
      <span className="a" />
      <span className="b" />
      <span className="c" />
      <span className="d" />
    </div>
  );
}

const NAV = [
  { to: "/", label: "Overview", Icon: LayoutDashboard, end: true },
  { to: "/flags", label: "Flags", Icon: Flag, end: false },
  { to: "/investigations", label: "Investigations", Icon: FileSearch, end: false },
  { to: "/approvals", label: "Approvals", Icon: ClipboardCheck, end: false },
  { to: "/pipeline", label: "Pipeline", Icon: Workflow, end: false },
  { to: "/datasets", label: "Datasets & pipelines", Icon: Database, end: false },
];

const THEMES: { value: ThemeChoice; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: "System theme", Icon: Monitor },
  { value: "light", label: "Light theme", Icon: Sun },
  { value: "dark", label: "Dark theme", Icon: Moon },
];

function BrandMark() {
  // An original mark: three stacked layers (bronze, silver, gold) as rounded bars.
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden>
      <rect x="4" y="5" width="20" height="5" rx="2.5" fill="var(--color-teal)" />
      <rect x="7" y="12" width="17" height="5" rx="2.5" fill="var(--color-mint)" />
      <rect x="10" y="19" width="14" height="5" rx="2.5" fill="var(--color-amber)" />
    </svg>
  );
}

/** Which data Overview and Flags show: DataCo, or a scored dataset. */
export function DatasetSelect({ className = "" }: { className?: string }) {
  const id = useId();
  const { selected, setSelected, choices } = useDataset();
  return (
    <div className={className}>
      <label htmlFor={id} className="mb-1 block px-1 text-[12px] font-medium text-ink-3">
        Showing data from
      </label>
      <select
        id={id}
        value={selected}
        onChange={(event) => setSelected(event.target.value)}
        className="glass-pill w-full appearance-none px-3 py-2 text-[13px] font-medium text-ink outline-none"
      >
        <option value={DATACO}>DataCo (flagship)</option>
        {choices.map((item) => (
          <option key={item.dataset_id} value={item.dataset_id}>
            {item.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function Rail({ pending }: { pending: number | undefined }) {
  const { theme, setTheme, solid, setSolid } = useSettings();
  const health = useApi("health", api.health);
  const mock = isMock();
  return (
    <nav
      aria-label="Primary"
      className="glass-strong sticky top-4 flex h-[calc(100dvh-32px)] w-[232px] shrink-0 flex-col p-4 max-lg:w-[76px] max-lg:px-3"
    >
      <div className="mb-8 flex items-center gap-3 px-2 pt-1">
        <BrandMark />
        <div className="max-lg:hidden">
          <p className="text-[17px] leading-tight font-semibold tracking-[-0.02em]">Provenance</p>
          <p className="text-[12px] text-ink-3">Supplier risk console</p>
        </div>
      </div>
      <ul className="flex flex-col gap-1">
        {NAV.map(({ to, label, Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              title={label}
              className={({ isActive }) =>
                `relative flex items-center gap-3 rounded-[14px] px-3 py-2.5 text-[15px] font-medium transition-colors duration-200 max-lg:justify-center ${
                  isActive
                    ? "bg-[color-mix(in_srgb,var(--ink)_12%,transparent)] text-ink shadow-[inset_0_1px_0_var(--highlight)]"
                    : "text-ink-2 hover:bg-[color-mix(in_srgb,var(--ink)_6%,transparent)] hover:text-ink"
                }`
              }
            >
              <Icon aria-hidden size={19} strokeWidth={2} />
              <span className="max-lg:sr-only">{label}</span>
              {to === "/approvals" && Boolean(pending) && (
                <span className="tabular ml-auto rounded-full bg-accent px-2 text-[12px] leading-5 font-semibold text-accent-ink max-lg:absolute max-lg:top-1 max-lg:right-1 max-lg:px-1.5">
                  {pending}
                  <span className="sr-only"> pending</span>
                </span>
              )}
            </NavLink>
          </li>
        ))}
      </ul>

      <DatasetSelect className="mt-6 max-lg:hidden" />

      <div className="mt-auto flex flex-col gap-3">
        {mock && (
          <p
            title="Mock data: fixtures from a copy of the warehouse"
            className="rounded-[14px] bg-[color-mix(in_srgb,var(--color-amber)_18%,transparent)] px-3 py-2 text-[12px] font-medium text-ink max-lg:px-1 max-lg:text-center max-lg:text-[11px]"
          >
            <span className="max-lg:hidden">Mock data: fixtures from a copy of the warehouse</span>
            <span className="lg:hidden">Mock</span>
          </p>
        )}
        <div role="radiogroup" aria-label="Theme" className="glass-pill justify-between p-1 max-lg:flex-col">
          {THEMES.map(({ value, label, Icon }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={theme === value}
              aria-label={label}
              title={label}
              onClick={() => setTheme(value)}
              className={`flex flex-1 justify-center rounded-full py-1.5 transition-colors ${
                theme === value ? "bg-ink text-[var(--ground)]" : "text-ink-2 hover:text-ink"
              }`}
            >
              <Icon aria-hidden size={16} />
            </button>
          ))}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={solid}
          onClick={() => setSolid(!solid)}
          title="Solid mode replaces glass with solid surfaces"
          className="glass-pill justify-between px-3 py-2 text-[13px] font-medium max-lg:justify-center"
        >
          <span className="inline-flex items-center gap-2">
            <Layers aria-hidden size={16} />
            <span className="max-lg:sr-only">Solid mode</span>
          </span>
          <span
            aria-hidden
            className={`relative inline-block h-5 w-9 shrink-0 rounded-full transition-colors max-lg:hidden ${
              solid ? "bg-accent" : "bg-[color-mix(in_srgb,var(--ink)_22%,transparent)]"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow transition-transform duration-200 ${
                solid ? "translate-x-4" : "translate-x-0"
              }`}
            />
          </span>
        </button>
        {health.data && (
          <p
            className="flex justify-center lg:hidden"
            title={`${health.data.db_reachable ? "Warehouse reachable" : "Warehouse unreachable"} · ${health.data.model}`}
          >
            <span
              aria-label={health.data.db_reachable ? "Warehouse reachable" : "Warehouse unreachable"}
              role="img"
              className={`size-2.5 rounded-full ${health.data.db_reachable ? "bg-tier-low" : "bg-danger"}`}
            />
          </p>
        )}
        <div className="px-2 text-[12px] leading-snug text-ink-3 max-lg:hidden">
          {health.data ? (
            <>
              <p className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className={`size-2 rounded-full ${health.data.db_reachable ? "bg-tier-low" : "bg-danger"}`}
                />
                {health.data.db_reachable ? "Warehouse reachable" : "Warehouse unreachable"}
              </p>
              <p className="mt-0.5 truncate" title={health.data.model}>
                Model {health.data.model}
              </p>
              <p className="mt-0.5">
                {health.data.approval_required ? "Approval required" : "Approval flag off (still enforced)"}
              </p>
            </>
          ) : health.error ? (
            <p>API offline</p>
          ) : null}
        </div>
      </div>
    </nav>
  );
}

function BusyBanner() {
  const busy = usePipelineBusy();
  if (!busy) return null;
  return (
    <p
      role="status"
      className="glass-pill mb-6 w-fit px-4 py-2 text-[14px] font-medium text-ink"
    >
      <Hourglass aria-hidden size={16} className="text-tier-medium" />
      Pipeline running, showing last loaded data
    </p>
  );
}

function Footer() {
  const { isDataco, selectedName } = useDataset();
  return (
    <footer className="mt-16 max-w-[80ch] text-[12px] leading-relaxed text-ink-3">
      {isDataco
        ? "Data comes from the DataCo supply-chain benchmark, which is likely synthetic; suppliers are product categories, not companies."
        : `Overview and Flags show the onboarded dataset “${selectedName}”. Its scores use its own frozen bounds and are comparable within this dataset only.`}{" "}
      The rule-based score is the source of truth: the agent explains it and never overrides it, and nothing it
      proposes takes effect without a named human approval. The console has no login and serves localhost only.
    </footer>
  );
}

export function Shell({ children, pending }: { children: ReactNode; pending: number | undefined }) {
  return (
    <div className="noise">
      <Ambient />
      <a
        href="#main"
        className="glass-pill pill-accent sr-only z-50 px-4 py-2 focus:not-sr-only focus:fixed focus:top-4 focus:left-4"
      >
        Skip to content
      </a>
      <div className="mx-auto flex max-w-[1600px] gap-6 p-4 max-sm:gap-3 max-sm:p-2">
        <Rail pending={pending} />
        <main id="main" className="min-w-0 flex-1 px-2 pt-6 pb-16 sm:px-4 lg:px-6">
          <BusyBanner />
          {children}
          <Footer />
        </main>
      </div>
    </div>
  );
}
