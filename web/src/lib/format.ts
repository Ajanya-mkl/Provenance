export const FACTOR_LABELS: Record<string, string> = {
  late_rate: "Late-delivery rate",
  delay_stddev: "Delay variability",
  country_risk: "Destination country risk",
};

export const FACTOR_PLAIN: Record<string, string> = {
  late_rate: "a larger share of shipments arriving late",
  delay_stddev: "delivery delays becoming less predictable",
  country_risk: "more shipments going to destinations with weaker logistics",
};

export const ACTION_LABELS: Record<string, string> = {
  no_action: "No action",
  watchlist: "Add to watchlist",
  request_review: "Request review",
};

export const SOURCE_LABELS: Record<string, string> = {
  dataco_shipments: "DataCo shipments",
  usaspending_contracts: "USASpending contracts",
  worldbank_lpi: "World Bank LPI",
};

export const TOOL_LABELS: Record<string, string> = {
  get_flag: "Flag",
  get_supplier_history: "Supplier history",
  get_score_breakdown: "Score breakdown",
  get_country_risk: "Country risk",
  get_market_mix: "Market mix",
  recall_past_investigations: "Memory",
};

export function factorLabel(name: string | null | undefined): string {
  return (name && FACTOR_LABELS[name]) ?? name ?? "Unknown factor";
}

export function score(value: number | null | undefined): string {
  return value == null ? "–" : value.toFixed(3);
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value == null ? "–" : `${(value * 100).toFixed(digits)}%`;
}

export function signed(value: number | null | undefined): string {
  if (value == null) return "–";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(3)}`;
}

export function supplierLabel(key: string, name?: string | null): string {
  if (name) return name;
  return key
    .replace(/^cat_/, "")
    .replace(/_s_/g, "'s ")
    .replace(/_/g, " ")
    .replace(/(^|\s)\w/g, (char) => char.toUpperCase());
}

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function date(value: string | null | undefined): string {
  if (!value) return "–";
  const parsed = new Date(value.length === 10 ? `${value}T00:00:00` : value);
  return Number.isNaN(parsed.getTime()) ? value : dateFormat.format(parsed);
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return "–";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : timeFormat.format(parsed);
}

export function quarterFromIso(value: string | null | undefined): string {
  if (!value) return "–";
  const [year, month] = value.split("-").map(Number);
  return `${year}-Q${Math.floor((month - 1) / 3) + 1}`;
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function eventLabel(event: string): string {
  return event.replace(/_/g, " ");
}
