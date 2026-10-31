# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Vite + React + TypeScript, Tailwind CSS (Vite plugin), Recharts, React Router, Framer Motion,
served locally next to a read-only FastAPI (`src/provenance/api/`). Chosen by the Phase 7c brief.
Everything runs locally on Windows; there is no deployment.

## Users

Primary: the project's author driving the console live in a demo or viva, on a laptop or
projector, for examiners and reviewers. It must also hold up as a credible tool for a supply-chain
risk analyst who triages risk flags and approves or rejects the agent's proposed actions.

## Product Purpose

Provenance is a local medallion pipeline (bronze → silver → gold) for supplier risk, with a
tool-using LLM agent on top. The console lets a person see which suppliers were flagged and why,
read the agent's grounded investigation, and approve or reject the one action it proposes.
Success: a reviewer can follow a flag from score to evidence to a human decision without leaving
the console, and can trust that nothing took effect without approval.

## Positioning

The rule-based score is the source of truth; the agent explains it and never overrides it. Every
number in an agent report is mechanically checked against tool results (grounding), ungrounded
reports are rejected, and actions take effect only after a named human approval recorded in a
hash-chained, append-only audit log.

## Operating Context

- Local only: DuckDB file `data/provenance.duckdb` (single writer; Dagster runs can lock it).
- API: FastAPI on port 8000; console on Vite port 5173; a mock mode serves fixtures without either.
- Workflows: review flags by tier/quarter/confidence; open a flag's score breakdown, SCD2 supplier
  history and destination market mix; read the investigation; approve or reject actions; check the
  quality gate and audit-chain integrity.
- Terms: supplier (a DataCo product category, key like `cat_golf_balls`), quarter (`2017-Q1`),
  tier (low / medium / high), confidence (full / low), top factor (late_rate, delay_stddev,
  country_risk), grounded, action (no_action / watchlist / request_review).

## Capabilities and Constraints

- Read-only console except approve/reject, which go through `provenance.agent.approval`.
- The console never triggers investigations or executes actions itself.
- No secrets in the front end; `.env` values are never returned by the API.

## Evidence on Hand

- Real pipeline output: 20 current risk flags (rules 1.2), scores, SCD2 history, quality-gate
  results, investigations, audit log.
- Agent evaluation: `src/provenance/agent/eval/results/report.json` and `docs/agent_evaluation.md`.
- Absences: no real companies, customers, testimonials or production deployment; do not invent them.

## Product Principles

1. The score is the truth; the agent is a narrator with receipts.
2. Show evidence next to every claim; an ungrounded report is visibly rejected, never softened.
3. Humans decide; the interface makes the decision explicit, named and auditable.
4. Honest about limits: state caveats quietly but persistently (dataset likely synthetic,
   suppliers are product categories, small evaluation sample).

## Accessibility & Inclusion

WCAG 2.2 AA: 4.5:1 text contrast, tier always encoded by icon and text as well as colour, full
keyboard navigation with visible focus, chart data available as tables, and motion and
transparency reduced on request (prefers-reduced-motion, prefers-reduced-transparency, Solid mode).
