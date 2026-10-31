import { CircleSlash } from "lucide-react";
import { createContext, useContext, useId, useState } from "react";
import type { ReactNode } from "react";

import { TOOL_LABELS } from "../lib/format";
import type { Span } from "../lib/types";

/**
 * Signature move: every number the agent wrote is a chip that knows which tool
 * result it came from. Hovering or focusing a chip lights that tool in the
 * Sources row; a number with no source is struck through in red.
 */
const ActiveTool = createContext<{
  active: string | null;
  setActive: (tool: string | null) => void;
}>({ active: null, setActive: () => undefined });

export function ReceiptScope({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<string | null>(null);
  return <ActiveTool.Provider value={{ active, setActive }}>{children}</ActiveTool.Provider>;
}

function NumberChip({ span }: { span: Span }) {
  const { active, setActive } = useContext(ActiveTool);
  const tipId = useId();
  const sourced = Boolean(span.tool);
  const lit = sourced && active === span.tool;
  const label = sourced ? `From ${TOOL_LABELS[span.tool!] ?? span.tool}` : "No tool returned this";
  return (
    <span className="group relative inline-block">
      <button
        type="button"
        aria-describedby={tipId}
        onMouseEnter={() => setActive(span.tool ?? null)}
        onMouseLeave={() => setActive(null)}
        onFocus={() => setActive(span.tool ?? null)}
        onBlur={() => setActive(null)}
        className={`tabular mx-[1px] rounded-md px-1.5 py-[1px] font-semibold transition-[background,box-shadow] duration-200 ${
          sourced
            ? `text-ink ${
                lit
                  ? "bg-accent/25 shadow-[inset_0_0_0_1px_var(--accent)]"
                  : "bg-[color-mix(in_srgb,var(--ink)_9%,transparent)] shadow-[inset_0_1px_0_var(--highlight)]"
              }`
            : "bg-danger/15 text-danger line-through decoration-2"
        }`}
      >
        {span.text}
      </button>
      <span
        id={tipId}
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 -translate-x-1/2 whitespace-nowrap rounded-[10px] bg-ink px-2.5 py-1 text-xs font-medium text-[var(--ground)] opacity-0 shadow-lg transition-opacity duration-150 group-focus-within:opacity-100 group-hover:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}

/** Keep a quarter label such as 2017-Q1 in one chip (grounding checks its year). */
function mergeQuarters(spans: Span[]): Span[] {
  const merged: Span[] = [];
  for (const span of spans) {
    const previous = merged.at(-1);
    const suffix = span.kind === "text" ? span.text.match(/^-Q[1-4]\b/) : null;
    if (previous?.kind === "number" && /^\d{4}$/.test(previous.text) && suffix) {
      merged[merged.length - 1] = { ...previous, text: previous.text + suffix[0] };
      const rest = span.text.slice(suffix[0].length);
      if (rest) merged.push({ kind: "text", text: rest });
    } else {
      merged.push(span);
    }
  }
  return merged;
}

export function ReceiptText({ spans, className = "" }: { spans: Span[]; className?: string }) {
  return (
    <span className={className}>
      {mergeQuarters(spans).map((span, index) =>
        span.kind === "number" ? (
          <NumberChip key={index} span={span} />
        ) : (
          <span key={index}>{span.text}</span>
        ),
      )}
    </span>
  );
}

export function SourcesRow({ tools }: { tools: { name: string; ok: boolean }[] }) {
  const { active } = useContext(ActiveTool);
  const unique = tools.filter(
    (tool, index) => tools.findIndex((other) => other.name === tool.name) === index,
  );
  return (
    <ul aria-label="Tools the agent called" className="flex flex-wrap gap-2">
      {unique.map((tool) => {
        const lit = active === tool.name;
        return (
          <li
            key={tool.name}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[13px] font-medium transition-[background,color,box-shadow] duration-200 ${
              lit
                ? "bg-accent text-accent-ink shadow-[0_0_0_4px_color-mix(in_srgb,var(--accent)_25%,transparent)]"
                : "bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] text-ink-2"
            }`}
          >
            {!tool.ok && <CircleSlash aria-label="returned an error" size={13} />}
            {TOOL_LABELS[tool.name] ?? tool.name}
          </li>
        );
      })}
    </ul>
  );
}
